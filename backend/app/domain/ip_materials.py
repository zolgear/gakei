"""IP 素材(ADR-0059)。IP と契約の条件、担当者、素材、利用の許可。

- IP を作る・担当者を決める・状態を変えるのは管理者。素材の取り込み・取り下げ、条件の版、許可は、
  その IP の担当者と管理者。
- 許可はチーム(ADR-0058)か個人に出す。有効な許可は「取り消していない、期間の中、IP が有効」。
  利用者が受けている許可は `active_grant_condition` の1か所で決める。
- 素材が見える人: 管理者、その IP の担当者(取り下げたものも)、「見る」の有効な許可を持つ人
  (取り下げていないものだけ)。`visible_material_asset_ids` が `visibility.py` から使われる。
- 証跡(`audit.record`)は API の側で残す(要求の送り元を添えるため)。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import ColumnElement, Select, and_, delete, false, func, or_, select, true
from sqlalchemy.orm import Session

from app.auth.identity import CurrentUser
from app.domain import teams
from app.domain.models import (
    IP_EXTERNAL_AI,
    IP_OWNER_KINDS,
    IP_STATES,
    AppUser,
    Asset,
    IpGrant,
    IpManager,
    IpMaterial,
    IpProperty,
    IpTerms,
    Team,
)

NAME_MAX_LENGTH = 200
PURPOSE_MAX_LENGTH = 100
PURPOSES_MAX = 50
TEXT_MAX_LENGTH = 4000


class IpValidationError(ValueError):
    """入力が不正(API で 422)。`args[0]` は文言のキー。"""


class IpPermissionError(Exception):
    """その操作をする権限が無い(API で 403)。"""


class IpNotFoundError(Exception):
    """見えない、または無い(API で 404)。"""


class IpStateError(Exception):
    """今の状態ではできない(終了した IP を戻すなど。API で 409)。"""


@dataclass(frozen=True)
class TermsInput:
    contract_number: str | None
    valid_from: date | None
    valid_until: date | None
    purposes: list[str]
    external_ai: str
    notes: str | None


def _utcnow() -> datetime:
    return datetime.now(UTC)


def today() -> date:
    return _utcnow().date()


def _clean(value: str | None, limit: int = TEXT_MAX_LENGTH) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value[:limit] or None


def _clean_purposes(purposes: Iterable[str]) -> list[str]:
    result: list[str] = []
    for purpose in purposes:
        purpose = purpose.strip()
        if not purpose:
            continue
        if len(purpose) > PURPOSE_MAX_LENGTH:
            raise IpValidationError("ip.invalidPurpose")
        if purpose not in result:
            result.append(purpose)
    if len(result) > PURPOSES_MAX:
        raise IpValidationError("ip.tooManyPurposes")
    return result


def _check_period(valid_from: date | None, valid_until: date | None) -> None:
    if valid_from is not None and valid_until is not None and valid_from > valid_until:
        raise IpValidationError("ip.invalidPeriod")


# -- 判定 ---------------------------------------------------------------------------------


def managed_ip_ids(user_id: uuid.UUID) -> Select[tuple]:
    return select(IpManager.ip_id).where(IpManager.user_id == user_id)


def is_manager(db: Session, viewer: CurrentUser, ip_id: uuid.UUID) -> bool:
    if viewer.id is None:
        return False
    return (
        db.execute(
            select(IpManager.ip_id).where(IpManager.ip_id == ip_id, IpManager.user_id == viewer.id)
        ).first()
        is not None
    )


def can_manage(db: Session, viewer: CurrentUser, ip: IpProperty) -> bool:
    """素材・条件・許可を扱えるか(管理者と担当者)。"""
    return viewer.is_admin or is_manager(db, viewer, ip.id)


def _in_period(valid_from: ColumnElement, valid_until: ColumnElement, on: date) -> ColumnElement:
    return and_(
        or_(valid_from.is_(None), valid_from <= on),
        or_(valid_until.is_(None), valid_until >= on),
    )


def active_grant_condition(user_id: uuid.UUID, on: date | None = None) -> ColumnElement[bool]:
    """`IpGrant` の行が、利用者 `user_id` が今受けている有効な許可である条件。IP の状態も見る。"""
    on = on or today()
    active_ips = select(IpProperty.id).where(IpProperty.state == "active")
    return and_(
        IpGrant.revoked_at.is_(None),
        _in_period(IpGrant.valid_from, IpGrant.valid_until, on),
        IpGrant.ip_id.in_(active_ips),
        or_(IpGrant.user_id == user_id, IpGrant.team_id.in_(teams.user_team_ids(user_id))),
    )


def granted_ip_ids(user_id: uuid.UUID, *, can_use: bool = False) -> Select[tuple]:
    """有効な許可を受けている IP の id。`can_use` なら「入力に使う」の許可だけ。"""
    query = select(IpGrant.ip_id).where(active_grant_condition(user_id))
    if can_use:
        query = query.where(IpGrant.can_use.is_(True))
    return query


def visible_ip_condition(viewer: CurrentUser) -> ColumnElement[bool]:
    """`IpProperty` の行が `viewer` に見える条件。"""
    if viewer.is_admin:
        return true()
    if viewer.id is None:
        return false()
    return or_(
        IpProperty.id.in_(managed_ip_ids(viewer.id)),
        IpProperty.id.in_(granted_ip_ids(viewer.id)),
    )


def visible_material_asset_ids(viewer: CurrentUser) -> Select[tuple]:
    """`viewer` に見える IP 素材の Asset の id(管理者・担当者は取り下げたものも)。"""
    if viewer.is_admin:
        return select(IpMaterial.asset_id)
    if viewer.id is None:
        return select(IpMaterial.asset_id).where(false())
    return select(IpMaterial.asset_id).where(
        or_(
            IpMaterial.ip_id.in_(managed_ip_ids(viewer.id)),
            and_(
                IpMaterial.withdrawn_at.is_(None),
                IpMaterial.ip_id.in_(granted_ip_ids(viewer.id)),
            ),
        )
    )


def managed_material_asset_ids(viewer: CurrentUser) -> Select[tuple]:
    """`viewer` が扱える(管理者と担当者)IP 素材の Asset の id。普段の可視性(変更も含む)に使う。"""
    if viewer.is_admin:
        return select(IpMaterial.asset_id)
    if viewer.id is None:
        return select(IpMaterial.asset_id).where(false())
    return select(IpMaterial.asset_id).where(IpMaterial.ip_id.in_(managed_ip_ids(viewer.id)))


def can_read_material(db: Session, viewer: CurrentUser, asset_id: uuid.UUID) -> bool:
    """IP 素材を読めるか(詳細と画像の配信。許可を受けた人を含む)。"""
    return (
        db.execute(
            visible_material_asset_ids(viewer).where(IpMaterial.asset_id == asset_id)
        ).first()
        is not None
    )


def material_asset_ids() -> Select[tuple]:
    """すべての IP 素材の Asset の id(普段のストックから除くのに使う)。"""
    return select(IpMaterial.asset_id)


def get_visible_ip(db: Session, viewer: CurrentUser, ip_id: uuid.UUID) -> IpProperty:
    ip = db.execute(
        select(IpProperty).where(IpProperty.id == ip_id, visible_ip_condition(viewer))
    ).scalar_one_or_none()
    if ip is None:
        raise IpNotFoundError
    return ip


def require_manage(db: Session, viewer: CurrentUser, ip: IpProperty) -> None:
    if not can_manage(db, viewer, ip):
        raise IpPermissionError


def material_ip(db: Session, asset_id: uuid.UUID) -> IpMaterial | None:
    return db.get(IpMaterial, asset_id)


# -- IP と条件 ------------------------------------------------------------------------------


def current_terms(db: Session, ip_id: uuid.UUID) -> IpTerms | None:
    return (
        db.execute(
            select(IpTerms).where(IpTerms.ip_id == ip_id).order_by(IpTerms.version.desc()).limit(1)
        )
        .scalars()
        .first()
    )


def all_terms(db: Session, ip_id: uuid.UUID) -> list[IpTerms]:
    return list(
        db.execute(select(IpTerms).where(IpTerms.ip_id == ip_id).order_by(IpTerms.version.desc()))
        .scalars()
        .all()
    )


def list_ips(db: Session, viewer: CurrentUser) -> list[IpProperty]:
    return list(
        db.execute(
            select(IpProperty)
            .where(visible_ip_condition(viewer))
            .order_by(IpProperty.name, IpProperty.id)
        )
        .scalars()
        .all()
    )


def material_counts(db: Session, ip_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not ip_ids:
        return {}
    rows = db.execute(
        select(IpMaterial.ip_id, func.count())
        .where(IpMaterial.ip_id.in_(ip_ids), IpMaterial.withdrawn_at.is_(None))
        .group_by(IpMaterial.ip_id)
    ).all()
    return {ip_id: count for ip_id, count in rows}


def _validate_terms(terms: TermsInput) -> TermsInput:
    if terms.external_ai not in IP_EXTERNAL_AI:
        raise IpValidationError("ip.invalidExternalAi")
    _check_period(terms.valid_from, terms.valid_until)
    return TermsInput(
        contract_number=_clean(terms.contract_number, NAME_MAX_LENGTH),
        valid_from=terms.valid_from,
        valid_until=terms.valid_until,
        purposes=_clean_purposes(terms.purposes),
        external_ai=terms.external_ai,
        notes=_clean(terms.notes),
    )


def create_ip(
    db: Session,
    viewer: CurrentUser,
    *,
    name: str,
    owner_kind: str,
    rights_holder: str | None,
    description: str | None,
    terms: TermsInput,
) -> IpProperty:
    """IP と最初の条件(版 1)を作る(管理者だけ)。"""
    if not viewer.is_admin:
        raise IpPermissionError
    name_clean = _clean(name, NAME_MAX_LENGTH)
    if name_clean is None:
        raise IpValidationError("ip.invalidName")
    if owner_kind not in IP_OWNER_KINDS:
        raise IpValidationError("ip.invalidOwnerKind")
    terms = _validate_terms(terms)
    now = _utcnow()
    ip = IpProperty(
        name=name_clean,
        owner_kind=owner_kind,
        rights_holder=_clean(rights_holder, NAME_MAX_LENGTH),
        description=_clean(description),
        state="active",
        created_by_user_id=viewer.id,
        created_at=now,
        updated_at=now,
    )
    db.add(ip)
    db.flush()
    _add_terms_row(db, viewer, ip, terms, version=1)
    return ip


def update_ip(
    db: Session,
    viewer: CurrentUser,
    ip: IpProperty,
    *,
    name: str | None = None,
    rights_holder: str | None = None,
    description: str | None = None,
) -> None:
    """名前・権利者・説明を変える(管理者だけ)。"""
    if not viewer.is_admin:
        raise IpPermissionError
    if name is not None:
        name_clean = _clean(name, NAME_MAX_LENGTH)
        if name_clean is None:
            raise IpValidationError("ip.invalidName")
        ip.name = name_clean
    if rights_holder is not None:
        ip.rights_holder = _clean(rights_holder, NAME_MAX_LENGTH)
    if description is not None:
        ip.description = _clean(description)
    ip.updated_at = _utcnow()


def set_state(db: Session, viewer: CurrentUser, ip: IpProperty, state: str) -> str:
    """状態を変える(管理者だけ)。終了は戻せない。前の状態を返す。"""
    if not viewer.is_admin:
        raise IpPermissionError
    if state not in IP_STATES:
        raise IpValidationError("ip.invalidState")
    if ip.state == "terminated" and state != "terminated":
        raise IpStateError
    before = ip.state
    ip.state = state
    ip.updated_at = _utcnow()
    return before


def _add_terms_row(
    db: Session, viewer: CurrentUser, ip: IpProperty, terms: TermsInput, *, version: int
) -> IpTerms:
    row = IpTerms(
        ip_id=ip.id,
        version=version,
        contract_number=terms.contract_number,
        valid_from=terms.valid_from,
        valid_until=terms.valid_until,
        purposes=terms.purposes,
        external_ai=terms.external_ai,
        notes=terms.notes,
        created_by_user_id=viewer.id,
        created_at=_utcnow(),
    )
    db.add(row)
    db.flush()
    return row


def add_terms(db: Session, viewer: CurrentUser, ip: IpProperty, terms: TermsInput) -> IpTerms:
    """条件の新しい版を足す(担当者と管理者)。古い版は書き換えない。"""
    require_manage(db, viewer, ip)
    if ip.state == "terminated":
        raise IpStateError
    terms = _validate_terms(terms)
    latest = db.execute(
        select(func.max(IpTerms.version)).where(IpTerms.ip_id == ip.id)
    ).scalar_one_or_none()
    return _add_terms_row(db, viewer, ip, terms, version=(latest or 0) + 1)


# -- 担当者 --------------------------------------------------------------------------------


def managers(db: Session, ip_id: uuid.UUID) -> list[AppUser]:
    return list(
        db.execute(
            select(AppUser)
            .join(IpManager, IpManager.user_id == AppUser.id)
            .where(IpManager.ip_id == ip_id)
            .order_by(AppUser.name, AppUser.email, AppUser.id)
        )
        .scalars()
        .all()
    )


def add_manager(db: Session, viewer: CurrentUser, ip: IpProperty, email: str) -> AppUser:
    if not viewer.is_admin:
        raise IpPermissionError
    user = teams.find_user_by_email(db, email)
    if user is None:
        raise IpValidationError("ip.userNotFound")
    if db.get(IpManager, (ip.id, user.id)) is None:
        db.add(
            IpManager(ip_id=ip.id, user_id=user.id, added_by_user_id=viewer.id, added_at=_utcnow())
        )
        db.flush()
    return user


def remove_manager(db: Session, viewer: CurrentUser, ip: IpProperty, user_id: uuid.UUID) -> None:
    if not viewer.is_admin:
        raise IpPermissionError
    result = db.execute(
        delete(IpManager).where(IpManager.ip_id == ip.id, IpManager.user_id == user_id)
    )
    if result.rowcount == 0:
        raise IpNotFoundError


# -- 素材 ----------------------------------------------------------------------------------


def list_materials(
    db: Session, viewer: CurrentUser, ip: IpProperty
) -> list[tuple[Asset, IpMaterial]]:
    """IP の素材(新しい順)。取り下げたものは、担当者と管理者にだけ返す。"""
    query = (
        select(Asset, IpMaterial)
        .join(IpMaterial, IpMaterial.asset_id == Asset.id)
        .where(IpMaterial.ip_id == ip.id)
        .order_by(IpMaterial.added_at.desc(), Asset.id.desc())
    )
    if not can_manage(db, viewer, ip):
        query = query.where(IpMaterial.withdrawn_at.is_(None))
    return [(asset, material) for asset, material in db.execute(query).all()]


def add_material(db: Session, viewer: CurrentUser, ip: IpProperty, asset: Asset) -> IpMaterial:
    """取り込んだ Asset を IP の素材にする(取り込みは呼ぶ側。担当者と管理者)。"""
    require_manage(db, viewer, ip)
    material = IpMaterial(
        asset_id=asset.id, ip_id=ip.id, added_by_user_id=viewer.id, added_at=_utcnow()
    )
    db.add(material)
    db.flush()
    from app.domain import ip_usage

    ip_usage.mark_material(db, asset.id, ip.id)
    return material


def check_can_add_material(db: Session, viewer: CurrentUser, ip: IpProperty) -> None:
    require_manage(db, viewer, ip)
    if ip.state != "active":
        raise IpStateError


def withdraw_material(
    db: Session, viewer: CurrentUser, ip: IpProperty, asset_id: uuid.UUID
) -> IpMaterial:
    require_manage(db, viewer, ip)
    material = db.get(IpMaterial, asset_id)
    if material is None or material.ip_id != ip.id:
        raise IpNotFoundError
    if material.withdrawn_at is None:
        material.withdrawn_at = _utcnow()
        material.withdrawn_by_user_id = viewer.id
    return material


# -- 許可 ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class GrantRow:
    grant: IpGrant
    team: Team | None
    user: AppUser | None


def list_grants(db: Session, ip_id: uuid.UUID) -> list[GrantRow]:
    rows = db.execute(
        select(IpGrant, Team, AppUser)
        .outerjoin(Team, Team.id == IpGrant.team_id)
        .outerjoin(AppUser, AppUser.id == IpGrant.user_id)
        .where(IpGrant.ip_id == ip_id)
        .order_by(IpGrant.revoked_at.is_not(None), IpGrant.granted_at.desc())
    ).all()
    return [GrantRow(grant=g, team=team, user=user) for g, team, user in rows]


def is_grant_active(grant: IpGrant, ip: IpProperty, on: date | None = None) -> bool:
    on = on or today()
    return (
        grant.revoked_at is None
        and ip.state == "active"
        and (grant.valid_from is None or grant.valid_from <= on)
        and (grant.valid_until is None or grant.valid_until >= on)
    )


def create_grant(
    db: Session,
    viewer: CurrentUser,
    ip: IpProperty,
    *,
    team_id: uuid.UUID | None,
    email: str | None,
    purposes: list[str],
    can_use: bool,
    valid_from: date | None,
    valid_until: date | None,
) -> IpGrant:
    """許可を出す(担当者と管理者)。用途は今の条件の用途の中から。"""
    require_manage(db, viewer, ip)
    if ip.state == "terminated":
        raise IpStateError
    if (team_id is None) == (email is None):
        raise IpValidationError("ip.grantTargetRequired")
    user_id: uuid.UUID | None = None
    if team_id is not None:
        if teams.get_team(db, team_id) is None:
            raise IpValidationError("ip.teamNotFound")
    else:
        assert email is not None
        user = teams.find_user_by_email(db, email)
        if user is None:
            raise IpValidationError("ip.userNotFound")
        user_id = user.id
    _check_period(valid_from, valid_until)
    cleaned = _clean_purposes(purposes)
    terms = current_terms(db, ip.id)
    allowed = set(terms.purposes if terms is not None else [])
    if any(p not in allowed for p in cleaned):
        raise IpValidationError("ip.purposeNotInTerms")
    grant = IpGrant(
        ip_id=ip.id,
        team_id=team_id,
        user_id=user_id,
        purposes=cleaned,
        can_use=can_use,
        valid_from=valid_from,
        valid_until=valid_until,
        granted_by_user_id=viewer.id,
        granted_at=_utcnow(),
    )
    db.add(grant)
    db.flush()
    return grant


def revoke_grant(db: Session, viewer: CurrentUser, ip: IpProperty, grant_id: uuid.UUID) -> IpGrant:
    require_manage(db, viewer, ip)
    grant = db.get(IpGrant, grant_id)
    if grant is None or grant.ip_id != ip.id:
        raise IpNotFoundError
    if grant.revoked_at is None:
        grant.revoked_at = _utcnow()
        grant.revoked_by_user_id = viewer.id
    return grant


def my_grants(db: Session, viewer: CurrentUser, ip_id: uuid.UUID) -> list[IpGrant]:
    """`viewer` が今受けている有効な許可(チーム経由を含む)。"""
    if viewer.id is None:
        return []
    return list(
        db.execute(select(IpGrant).where(IpGrant.ip_id == ip_id, active_grant_condition(viewer.id)))
        .scalars()
        .all()
    )
