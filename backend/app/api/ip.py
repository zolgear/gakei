"""IP 素材(ADR-0059)。IP と契約の条件、担当者、素材、利用の許可、証跡。

- 使えるのは認証モードで、管理者設定で有効にしたときだけ(`GET /api/ip/settings` 以外は、
  それ以外では 404)。
- IP を作る・名前と状態を変える・担当者を決めるのは管理者。条件の版、素材、許可、証跡は、
  その IP の担当者と管理者。見られるのは、管理者、担当者、有効な許可を受けている人。
- 見えない IP は 404、見えるが扱えない操作は 403。
- 変更はすべて `audit_event` に残す(ADR-0059 7章)。
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.api.assets import _to_summary
from app.api.pagination import InvalidCursorError, decode_cursor, encode_cursor
from app.auth.deps import require_user
from app.auth.identity import CurrentUser
from app.auth.runtime import AuthRuntime, get_auth_runtime
from app.body_guard import MULTIPART_OVERHEAD, body_policy
from app.config import Settings
from app.deps import (
    get_annotator,
    get_embedder,
    get_focal_worker,
    get_mods,
    get_session,
    get_settings,
    get_store,
)
from app.domain import audit, ingest_hooks, ip_reports, ip_settings, ip_usage
from app.domain import ip_materials as domain
from app.domain.assets import MAX_UPLOAD_BYTES, IngestError, ingest
from app.domain.content_withholding import purge
from app.domain.models import (
    AppUser,
    Asset,
    AssetIpLineage,
    AssetKind,
    AuditEvent,
    IpProperty,
    IpTerms,
    Team,
)
from app.domain.schemas import (
    AuditEventListResponse,
    AuditEventView,
    IpCreateRequest,
    IpDerivativeItem,
    IpDerivativeListResponse,
    IpDestroyResponse,
    IpDetail,
    IpGrantCreateRequest,
    IpGrantListResponse,
    IpGrantView,
    IpListResponse,
    IpManagerAddRequest,
    IpMaterialItem,
    IpMaterialListResponse,
    IpMyAccess,
    IpReportCount,
    IpReportMaterial,
    IpReportView,
    IpSettingsUpdateRequest,
    IpSettingsView,
    IpStateRequest,
    IpSummary,
    IpTeamRef,
    IpTermsInput,
    IpTermsView,
    IpUpdateRequest,
    IpUsageCheckRequest,
    IpUsageCheckResponse,
    IpUsageIp,
    IpUserRef,
)
from app.domain.storage import AssetStore
from app.domain.visibility import asset_visible, get_readable_asset
from app.http_body_limit import read_file_limited
from app.i18n import t
from app.mods.gate import ingest_gate

router = APIRouter(prefix="/api/ip", tags=["ip"])

AUDIT_PAGE_MAX = 200


def _available(db: Session, runtime: AuthRuntime) -> bool:
    return runtime.is_oidc and ip_settings.is_enabled(db)


def _require_available(
    db: Session = Depends(get_session), runtime: AuthRuntime = Depends(get_auth_runtime)
) -> None:
    if not _available(db, runtime):
        raise HTTPException(status_code=404, detail=t("ip.disabled"))


_available_dep = [Depends(_require_available)]


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, domain.IpNotFoundError):
        return HTTPException(status_code=404, detail=t("ip.notFound"))
    if isinstance(exc, domain.IpPermissionError):
        return HTTPException(status_code=403, detail=t("ip.forbidden"))
    if isinstance(exc, domain.IpStateError):
        return HTTPException(status_code=409, detail=t("ip.invalidStateChange"))
    if isinstance(exc, domain.IpValidationError):
        return HTTPException(status_code=422, detail=t(str(exc.args[0])))
    raise exc


_DOMAIN_ERRORS = (
    domain.IpNotFoundError,
    domain.IpPermissionError,
    domain.IpStateError,
    domain.IpValidationError,
)


def _visible(db: Session, user: CurrentUser, ip_id: uuid.UUID) -> IpProperty:
    try:
        return domain.get_visible_ip(db, user, ip_id)
    except domain.IpNotFoundError as exc:
        raise _http(exc) from exc


def _user_ref(user: AppUser | None) -> IpUserRef | None:
    if user is None:
        return None
    return IpUserRef(user_id=user.id, name=user.name, email=user.email)


def _terms_view(db: Session, terms: IpTerms | None) -> IpTermsView | None:
    if terms is None:
        return None
    creator = db.get(AppUser, terms.created_by_user_id) if terms.created_by_user_id else None
    return IpTermsView(
        version=terms.version,
        contract_number=terms.contract_number,
        valid_from=terms.valid_from,
        valid_until=terms.valid_until,
        purposes=list(terms.purposes or []),
        external_ai=terms.external_ai,  # type: ignore[arg-type]
        notes=terms.notes,
        created_at=terms.created_at,
        created_by_name=(creator.name or creator.email) if creator else None,
    )


def _access(db: Session, user: CurrentUser, ip: IpProperty) -> IpMyAccess | None:
    grants = domain.my_grants(db, user, ip.id)
    if not grants:
        return None
    purposes: list[str] = []
    for grant in grants:
        for purpose in grant.purposes or []:
            if purpose not in purposes:
                purposes.append(purpose)
    return IpMyAccess(purposes=purposes, can_use=any(g.can_use for g in grants))


def _summary(db: Session, user: CurrentUser, ip: IpProperty, count: int) -> IpSummary:
    return IpSummary(
        id=ip.id,
        name=ip.name,
        owner_kind=ip.owner_kind,  # type: ignore[arg-type]
        rights_holder=ip.rights_holder,
        state=ip.state,  # type: ignore[arg-type]
        material_count=count,
        can_manage=domain.can_manage(db, user, ip),
        access=_access(db, user, ip),
    )


def _detail(db: Session, user: CurrentUser, ip: IpProperty) -> IpDetail:
    count = domain.material_counts(db, [ip.id]).get(ip.id, 0)
    return IpDetail(
        **_summary(db, user, ip, count).model_dump(),
        description=ip.description,
        terms=_terms_view(db, domain.current_terms(db, ip.id)),
        managers=[
            ref for ref in (_user_ref(u) for u in domain.managers(db, ip.id)) if ref is not None
        ],
        can_administer=user.is_admin,
    )


def _terms_input(body: IpTermsInput) -> domain.TermsInput:
    return domain.TermsInput(
        contract_number=body.contract_number,
        valid_from=body.valid_from,
        valid_until=body.valid_until,
        purposes=list(body.purposes),
        external_ai=body.external_ai,
        notes=body.notes,
    )


def _terms_detail(terms: IpTerms) -> dict:
    return {
        "version": terms.version,
        "purposes": list(terms.purposes or []),
        "external_ai": terms.external_ai,
        "valid_from": terms.valid_from.isoformat() if terms.valid_from else None,
        "valid_until": terms.valid_until.isoformat() if terms.valid_until else None,
    }


# -- 設定 -----------------------------------------------------------------------------------


@router.get("/settings", response_model=IpSettingsView, operation_id="get_ip_settings")
def get_ip_settings(
    db: Session = Depends(get_session),
    runtime: AuthRuntime = Depends(get_auth_runtime),
    user: CurrentUser = Depends(require_user),
) -> IpSettingsView:
    return IpSettingsView(enabled=ip_settings.is_enabled(db), available=_available(db, runtime))


@router.put("/settings", response_model=IpSettingsView, operation_id="update_ip_settings")
def update_ip_settings(
    body: IpSettingsUpdateRequest,
    request: Request,
    db: Session = Depends(get_session),
    runtime: AuthRuntime = Depends(get_auth_runtime),
    user: CurrentUser = Depends(require_user),
) -> IpSettingsView:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail=t("ip.forbidden"))
    ip_settings.save_enabled(db, body.enabled)
    audit.record_now(db, user, "ip.settings", detail={"enabled": body.enabled}, request=request)
    return IpSettingsView(enabled=body.enabled, available=_available(db, runtime))


# -- IP --------------------------------------------------------------------------------------


@router.get("", response_model=IpListResponse, operation_id="list_ip", dependencies=_available_dep)
def list_ip(
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> IpListResponse:
    ips = domain.list_ips(db, user)
    counts = domain.material_counts(db, [ip.id for ip in ips])
    return IpListResponse(items=[_summary(db, user, ip, counts.get(ip.id, 0)) for ip in ips])


@router.post(
    "",
    response_model=IpDetail,
    status_code=201,
    operation_id="create_ip",
    dependencies=_available_dep,
)
def create_ip(
    body: IpCreateRequest,
    request: Request,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> IpDetail:
    try:
        ip = domain.create_ip(
            db,
            user,
            name=body.name,
            owner_kind=body.owner_kind,
            rights_holder=body.rights_holder,
            description=body.description,
            terms=_terms_input(body.terms),
        )
    except _DOMAIN_ERRORS as exc:
        db.rollback()
        raise _http(exc) from exc
    terms = domain.current_terms(db, ip.id)
    audit.record(
        db,
        user,
        "ip.create",
        ip_id=ip.id,
        target_type="ip",
        target_id=ip.id,
        detail={"name": ip.name, "owner_kind": ip.owner_kind, "rights_holder": ip.rights_holder},
        request=request,
    )
    if terms is not None:
        audit.record(
            db,
            user,
            "ip.terms_add",
            ip_id=ip.id,
            target_type="ip_terms",
            target_id=terms.id,
            detail=_terms_detail(terms),
            request=request,
        )
    db.commit()
    return _detail(db, user, ip)


@router.get("/{ip_id}", response_model=IpDetail, operation_id="get_ip", dependencies=_available_dep)
def get_ip(
    ip_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> IpDetail:
    return _detail(db, user, _visible(db, user, ip_id))


@router.patch(
    "/{ip_id}", response_model=IpDetail, operation_id="update_ip", dependencies=_available_dep
)
def update_ip(
    ip_id: uuid.UUID,
    body: IpUpdateRequest,
    request: Request,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> IpDetail:
    ip = _visible(db, user, ip_id)
    try:
        domain.update_ip(
            db,
            user,
            ip,
            name=body.name,
            rights_holder=body.rights_holder,
            description=body.description,
        )
    except _DOMAIN_ERRORS as exc:
        db.rollback()
        raise _http(exc) from exc
    audit.record(
        db,
        user,
        "ip.update",
        ip_id=ip.id,
        target_type="ip",
        target_id=ip.id,
        detail=body.model_dump(exclude_none=True),
        request=request,
    )
    db.commit()
    return _detail(db, user, ip)


@router.put(
    "/{ip_id}/state",
    response_model=IpDetail,
    operation_id="set_ip_state",
    dependencies=_available_dep,
)
def set_ip_state(
    ip_id: uuid.UUID,
    body: IpStateRequest,
    request: Request,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> IpDetail:
    """有効・停止・終了(終了は戻せない)。管理者だけ。"""
    ip = _visible(db, user, ip_id)
    try:
        before = domain.set_state(db, user, ip, body.state)
    except _DOMAIN_ERRORS as exc:
        db.rollback()
        raise _http(exc) from exc
    audit.record(
        db,
        user,
        "ip.state_change",
        ip_id=ip.id,
        target_type="ip",
        target_id=ip.id,
        detail={"from": before, "to": body.state},
        request=request,
    )
    db.commit()
    return _detail(db, user, ip)


# -- 条件 -----------------------------------------------------------------------------------


@router.get(
    "/{ip_id}/terms",
    response_model=list[IpTermsView],
    operation_id="list_ip_terms",
    dependencies=_available_dep,
)
def list_ip_terms(
    ip_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> list[IpTermsView]:
    ip = _visible(db, user, ip_id)
    return [view for view in (_terms_view(db, row) for row in domain.all_terms(db, ip.id)) if view]


@router.post(
    "/{ip_id}/terms",
    response_model=IpDetail,
    operation_id="add_ip_terms",
    dependencies=_available_dep,
)
def add_ip_terms(
    ip_id: uuid.UUID,
    body: IpTermsInput,
    request: Request,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> IpDetail:
    """条件の新しい版を足す(担当者と管理者)。"""
    ip = _visible(db, user, ip_id)
    try:
        terms = domain.add_terms(db, user, ip, _terms_input(body))
    except _DOMAIN_ERRORS as exc:
        db.rollback()
        raise _http(exc) from exc
    audit.record(
        db,
        user,
        "ip.terms_add",
        ip_id=ip.id,
        target_type="ip_terms",
        target_id=terms.id,
        detail=_terms_detail(terms),
        request=request,
    )
    db.commit()
    return _detail(db, user, ip)


# -- 担当者 ---------------------------------------------------------------------------------


@router.post(
    "/{ip_id}/managers",
    response_model=IpDetail,
    operation_id="add_ip_manager",
    dependencies=_available_dep,
)
def add_ip_manager(
    ip_id: uuid.UUID,
    body: IpManagerAddRequest,
    request: Request,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> IpDetail:
    ip = _visible(db, user, ip_id)
    try:
        manager = domain.add_manager(db, user, ip, body.email)
    except _DOMAIN_ERRORS as exc:
        db.rollback()
        raise _http(exc) from exc
    audit.record(
        db,
        user,
        "ip.manager_change",
        ip_id=ip.id,
        target_type="user",
        target_id=manager.id,
        detail={"added": manager.email},
        request=request,
    )
    db.commit()
    return _detail(db, user, ip)


@router.delete(
    "/{ip_id}/managers/{user_id}",
    status_code=204,
    operation_id="remove_ip_manager",
    dependencies=_available_dep,
)
def remove_ip_manager(
    ip_id: uuid.UUID,
    user_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> None:
    ip = _visible(db, user, ip_id)
    try:
        domain.remove_manager(db, user, ip, user_id)
    except _DOMAIN_ERRORS as exc:
        db.rollback()
        raise _http(exc) from exc
    removed = db.get(AppUser, user_id)
    audit.record(
        db,
        user,
        "ip.manager_change",
        ip_id=ip.id,
        target_type="user",
        target_id=user_id,
        detail={"removed": removed.email if removed else None},
        request=request,
    )
    db.commit()


# -- 素材 -----------------------------------------------------------------------------------


@router.get(
    "/{ip_id}/materials",
    response_model=IpMaterialListResponse,
    operation_id="list_ip_materials",
    dependencies=_available_dep,
)
def list_ip_materials(
    ip_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> IpMaterialListResponse:
    ip = _visible(db, user, ip_id)
    rows = domain.list_materials(db, user, ip)
    return IpMaterialListResponse(
        items=[
            IpMaterialItem(
                asset=_to_summary(asset),
                added_at=material.added_at,
                withdrawn_at=material.withdrawn_at,
            )
            for asset, material in rows
        ]
    )


@router.post(
    "/{ip_id}/materials",
    response_model=IpMaterialItem,
    status_code=201,
    operation_id="add_ip_material",
    dependencies=_available_dep,
)
@body_policy(max_bytes=MAX_UPLOAD_BYTES + MULTIPART_OVERHEAD)
def add_ip_material(
    ip_id: uuid.UUID,
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_session),
    store: AssetStore = Depends(get_store),
    user: CurrentUser = Depends(require_user),
    settings: Settings = Depends(get_settings),
    annotator=Depends(get_annotator),
    embedder=Depends(get_embedder),
    focal_worker=Depends(get_focal_worker),
    mods=Depends(get_mods),
) -> IpMaterialItem:
    """素材を取り込む(担当者と管理者。ADR-0059 3章)。普段のアップロードとは別の入口で、同じ内容の
    画像があっても新しい Asset にする。"""
    ip = _visible(db, user, ip_id)
    try:
        domain.check_can_add_material(db, user, ip)
    except _DOMAIN_ERRORS as exc:
        raise _http(exc) from exc
    # 取り込みの後の自動処理と Mod は、契約の条件に従う(ADR-0059 3章)。外部の AI への送信が
    # 許可でなければ、自動タイトル・タグ・埋め込み・Mod(判定を待つ購読を含む)に渡さない。
    # 焦点は手元で求める。Mod の経路の名前は契約の値(`upload`)を使う。
    terms = domain.current_terms(db, ip.id)
    allow_external = terms is not None and terms.external_ai == "allow"
    data = read_file_limited(
        file.file,
        MAX_UPLOAD_BYTES - 1,
        lambda: HTTPException(status_code=422, detail=t("assets.uploadTooLarge")),
    )
    try:
        asset = ingest(
            db,
            store,
            data,
            AssetKind.UPLOAD,
            created_by_user_id=user.id,
            gate=ingest_gate(mods, "upload") if allow_external else None,
        )
        material = domain.add_material(db, user, ip, asset)
    except IngestError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if allow_external:
        queued = ingest_hooks.enqueue_after_ingest(db, asset, settings, source="upload", mods=mods)
    else:
        queued = ingest_hooks.focal_targets(asset)
    audit.record(
        db,
        user,
        "ip.material_add",
        ip_id=ip.id,
        target_type="asset",
        target_id=asset.id,
        detail={"sha256": asset.sha256, "file_name": file.filename},
        request=request,
    )
    db.commit()
    item = IpMaterialItem(asset=_to_summary(asset), added_at=material.added_at, withdrawn_at=None)
    ingest_hooks.notify_workers(
        queued, annotator=annotator, embedder=embedder, focal=focal_worker, mods=mods
    )
    return item


@router.post(
    "/{ip_id}/materials/{asset_id}/withdraw",
    response_model=IpMaterialItem,
    operation_id="withdraw_ip_material",
    dependencies=_available_dep,
)
def withdraw_ip_material(
    ip_id: uuid.UUID,
    asset_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> IpMaterialItem:
    """素材を取り下げる(新しく使えなくなる。記録と派生物は残る)。"""
    ip = _visible(db, user, ip_id)
    try:
        material = domain.withdraw_material(db, user, ip, asset_id)
    except _DOMAIN_ERRORS as exc:
        db.rollback()
        raise _http(exc) from exc
    audit.record(
        db,
        user,
        "ip.material_withdraw",
        ip_id=ip.id,
        target_type="asset",
        target_id=asset_id,
        request=request,
    )
    db.commit()
    asset = db.get(domain.Asset, asset_id)
    assert asset is not None
    return IpMaterialItem(
        asset=_to_summary(asset), added_at=material.added_at, withdrawn_at=material.withdrawn_at
    )


# -- 許可 -----------------------------------------------------------------------------------


def _grant_view(row: domain.GrantRow, ip: IpProperty, on: date) -> IpGrantView:
    grant = row.grant
    return IpGrantView(
        id=grant.id,
        team_id=grant.team_id,
        team_name=row.team.name if row.team is not None else None,
        user=_user_ref(row.user),
        purposes=list(grant.purposes or []),
        can_use=grant.can_use,
        valid_from=grant.valid_from,
        valid_until=grant.valid_until,
        granted_at=grant.granted_at,
        revoked_at=grant.revoked_at,
        active=domain.is_grant_active(grant, ip, on)
        and (row.team is None or row.team.deleted_at is None),
    )


def _require_manage(db: Session, user: CurrentUser, ip: IpProperty) -> None:
    try:
        domain.require_manage(db, user, ip)
    except domain.IpPermissionError as exc:
        raise _http(exc) from exc


@router.get(
    "/{ip_id}/grants",
    response_model=IpGrantListResponse,
    operation_id="list_ip_grants",
    dependencies=_available_dep,
)
def list_ip_grants(
    ip_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> IpGrantListResponse:
    ip = _visible(db, user, ip_id)
    _require_manage(db, user, ip)
    on = domain.today()
    return IpGrantListResponse(
        items=[_grant_view(row, ip, on) for row in domain.list_grants(db, ip.id)]
    )


@router.post(
    "/{ip_id}/grants",
    response_model=IpGrantListResponse,
    status_code=201,
    operation_id="create_ip_grant",
    dependencies=_available_dep,
)
def create_ip_grant(
    ip_id: uuid.UUID,
    body: IpGrantCreateRequest,
    request: Request,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> IpGrantListResponse:
    """許可を出す(チームか、メールアドレスで個人に)。用途は今の条件の用途の中から。"""
    ip = _visible(db, user, ip_id)
    try:
        grant = domain.create_grant(
            db,
            user,
            ip,
            team_id=body.team_id,
            email=body.email,
            purposes=list(body.purposes),
            can_use=body.can_use,
            valid_from=body.valid_from,
            valid_until=body.valid_until,
        )
    except _DOMAIN_ERRORS as exc:
        db.rollback()
        raise _http(exc) from exc
    audit.record(
        db,
        user,
        "ip.grant_add",
        ip_id=ip.id,
        target_type="ip_grant",
        target_id=grant.id,
        detail={
            "team_id": str(grant.team_id) if grant.team_id else None,
            "user_id": str(grant.user_id) if grant.user_id else None,
            "purposes": list(grant.purposes or []),
            "can_use": grant.can_use,
            "valid_from": grant.valid_from.isoformat() if grant.valid_from else None,
            "valid_until": grant.valid_until.isoformat() if grant.valid_until else None,
        },
        request=request,
    )
    db.commit()
    on = domain.today()
    return IpGrantListResponse(
        items=[_grant_view(row, ip, on) for row in domain.list_grants(db, ip.id)]
    )


@router.post(
    "/{ip_id}/grants/{grant_id}/revoke",
    response_model=IpGrantListResponse,
    operation_id="revoke_ip_grant",
    dependencies=_available_dep,
)
def revoke_ip_grant(
    ip_id: uuid.UUID,
    grant_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> IpGrantListResponse:
    ip = _visible(db, user, ip_id)
    try:
        domain.revoke_grant(db, user, ip, grant_id)
    except _DOMAIN_ERRORS as exc:
        db.rollback()
        raise _http(exc) from exc
    audit.record(
        db,
        user,
        "ip.grant_revoke",
        ip_id=ip.id,
        target_type="ip_grant",
        target_id=grant_id,
        request=request,
    )
    db.commit()
    on = domain.today()
    return IpGrantListResponse(
        items=[_grant_view(row, ip, on) for row in domain.list_grants(db, ip.id)]
    )


@router.get(
    "/{ip_id}/grant-targets",
    response_model=list[IpTeamRef],
    operation_id="list_ip_grant_targets",
    dependencies=_available_dep,
)
def list_ip_grant_targets(
    ip_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> list[IpTeamRef]:
    """許可を出せるチーム(名前だけ)。担当者は管理者でなくても、チームを選んで許可を出せる。"""
    ip = _visible(db, user, ip_id)
    _require_manage(db, user, ip)
    rows = db.execute(
        select(Team.id, Team.name).where(Team.deleted_at.is_(None)).order_by(Team.name, Team.id)
    ).all()
    return [IpTeamRef(id=team_id, name=name) for team_id, name in rows]


# -- 使うとき(ADR-0059 5章・6章) ----------------------------------------------------------


@router.post(
    "/usage-check",
    response_model=IpUsageCheckResponse,
    operation_id="check_ip_usage",
    dependencies=_available_dep,
)
def check_ip_usage(
    body: IpUsageCheckRequest,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> IpUsageCheckResponse:
    """入力にする画像が IP を引き継ぐか、申告で選べる用途、使えない理由(生成のフォーム用)。"""
    locality = (
        ip_usage.run_locality(db, None, body.provider, body.model or "")
        if body.provider
        else "external"
    )
    # 読めない画像は数えない(見えない画像の IP の名前を明かさない)。
    readable = [a for a in body.asset_ids if get_readable_asset(db, user, a) is not None]
    required, items, purposes = ip_usage.usage_summary(db, user, readable, locality=locality)
    return IpUsageCheckResponse(
        required=required,
        ips=[
            IpUsageIp(
                id=item.ip.id,
                name=item.ip.name,
                notes=item.terms.notes if item.terms else None,
                external_ai=item.terms.external_ai if item.terms else None,  # type: ignore[arg-type]
                purposes=item.purposes,
                problem=t(item.problem, name=item.ip.name) if item.problem else None,
            )
            for item in items
        ],
        purposes=purposes,
    )


@router.get(
    "/{ip_id}/derivatives",
    response_model=IpDerivativeListResponse,
    operation_id="list_ip_derivatives",
    dependencies=_available_dep,
)
def list_ip_derivatives(
    ip_id: uuid.UUID,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> IpDerivativeListResponse:
    """この IP の派生物(新しい順)。担当者と管理者にはすべて、ほかの人には読めるものと自分のもの。
    ほかの IP も引き継ぐものには、その数を付ける(混入・掛け合わせの印)。"""
    ip = _visible(db, user, ip_id)
    query = (
        select(Asset)
        .join(AssetIpLineage, AssetIpLineage.asset_id == Asset.id)
        .where(
            AssetIpLineage.ip_id == ip.id,
            Asset.deleted_at.is_(None),
            Asset.id.not_in(domain.material_asset_ids()),
        )
    )
    if not domain.can_manage(db, user, ip):
        query = query.where(
            or_(asset_visible(user), ip_usage.readable_derived_condition(user, Asset.id))
        )
    rows = list(
        db.execute(query.order_by(Asset.created_at.desc(), Asset.id.desc()).limit(limit))
        .scalars()
        .all()
    )
    ip_map = ip_usage.ip_ids_of(db, [a.id for a in rows])
    creators = {
        u.id: (u.name or u.email)
        for u in db.execute(
            select(AppUser).where(
                AppUser.id.in_({a.created_by_user_id for a in rows if a.created_by_user_id})
            )
        ).scalars()
    }
    return IpDerivativeListResponse(
        items=[
            IpDerivativeItem(
                asset=_to_summary(asset, ip_marked=True),
                created_at=asset.created_at,
                created_by_name=creators.get(asset.created_by_user_id)
                if asset.created_by_user_id
                else None,
                other_ip_count=len(ip_map.get(asset.id, set()) - {ip.id}),
            )
            for asset in rows
        ]
    )


# -- レポートと破棄(ADR-0059 8章・9章) ----------------------------------------------------


def _report(
    db: Session,
    user: CurrentUser,
    ip_id: uuid.UUID,
    start: date | None,
    end: date | None,
    include_names: bool,
) -> tuple[IpProperty, ip_reports.IpReport]:
    ip = _visible(db, user, ip_id)
    _require_manage(db, user, ip)
    default_start, default_end = ip_reports.default_period(ip)
    try:
        report = ip_reports.build_report(
            db, ip, start or default_start, end or default_end, include_names=include_names
        )
    except ip_reports.IpReportPeriodError as exc:
        raise HTTPException(status_code=422, detail=t("ip.invalidPeriod")) from exc
    return ip, report


def _counts(counter, empty: str = "") -> list[IpReportCount]:  # noqa: ANN001
    return [IpReportCount(key=key or empty, count=count) for key, count in counter.most_common()]


@router.get(
    "/{ip_id}/report",
    response_model=IpReportView,
    operation_id="get_ip_report",
    dependencies=_available_dep,
)
def get_ip_report(
    ip_id: uuid.UUID,
    start: date | None = Query(default=None, description="期間の開始(既定は IP を作った日)"),
    end: date | None = Query(default=None, description="期間の終了(既定は今日)"),
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> IpReportView:
    """期間の集計(担当者と管理者)。画面では実行者の名前を出す(組織の中の画面のため)。"""
    _ip, report = _report(db, user, ip_id, start, end, include_names=True)
    return IpReportView(
        start=report.start,
        end=report.end,
        uses=len(report.uses),
        uses_succeeded=sum(1 for u in report.uses if u.status == "succeeded"),
        outputs=sum(u.output_count for u in report.uses),
        derived=report.derived_count,
        original_downloads=report.actions.get("asset.download_original", 0),
        detail_views=report.actions.get("asset.view_detail", 0),
        lineage_exports=report.actions.get("export.lineage", 0),
        denied=report.actions.get("ip.denied", 0),
        by_purpose=_counts(report.counter("purpose")),
        by_work_note=_counts(report.counter("work_note")),
        by_grantee=_counts(report.counter("grantee")),
        by_user=_counts(report.counter("user")),
        by_provider=_counts(report.counter("provider")),
        denied_reasons=[
            IpReportCount(key=t(key, name=_ip.name) if key.startswith("ip.") else key, count=count)
            for key, count in report.denied_reasons.most_common()
        ],
        materials=[
            IpReportMaterial(
                asset_id=m.asset_id,
                added_at=m.added_at,
                withdrawn_at=m.withdrawn_at,
                destroyed=m.destroyed,
                use_count=m.use_count,
            )
            for m in report.materials
        ],
    )


@router.get(
    "/{ip_id}/report/download",
    operation_id="download_ip_report",
    dependencies=_available_dep,
    response_class=Response,
    responses={200: {"content": {"text/html": {}, "text/csv": {}}}},
)
def download_ip_report(
    ip_id: uuid.UUID,
    request: Request,
    format: Literal["html", "csv"] = Query(default="html"),
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    include_names: bool = Query(default=False, description="実行者の名前を入れる(既定は入れない)"),
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> Response:
    """権利者に渡す報告書(HTML)と、利用の1件ずつ(CSV)。書き出したことを証跡に残す。"""
    ip, report = _report(db, user, ip_id, start, end, include_names=include_names)
    audit.record_now(
        db,
        user,
        "export.ip_report",
        ip_id=ip.id,
        target_type="ip",
        target_id=ip.id,
        detail={
            "format": format,
            "start": report.start.isoformat(),
            "end": report.end.isoformat(),
            "include_names": include_names,
        },
        request=request,
    )
    stem = f"gakei-ip-report-{report.start:%Y%m%d}-{report.end:%Y%m%d}"
    if format == "csv":
        body = "\ufeff" + ip_reports.report_csv(report)
        media = "text/csv; charset=utf-8"
        name = f"{stem}.csv"
    else:
        body = ip_reports.report_html(report)
        media = "text/html; charset=utf-8"
        name = f"{stem}.html"
    return Response(
        content=body.encode("utf-8"),
        media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "Cache-Control": "no-store",
        },
    )


@router.post(
    "/{ip_id}/materials/destroy",
    response_model=IpDestroyResponse,
    operation_id="destroy_ip_materials",
    dependencies=_available_dep,
)
def destroy_ip_materials(
    ip_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_session),
    store: AssetStore = Depends(get_store),
    user: CurrentUser = Depends(require_user),
) -> IpDestroyResponse:
    """終了した IP の素材の原本と派生画像を消す(担当者と管理者。ADR-0059 9章)。記録は残す。"""
    ip = _visible(db, user, ip_id)
    _require_manage(db, user, ip)
    if ip.state != "terminated":
        raise HTTPException(status_code=409, detail=t("ip.destroyRequiresTermination"))
    result = ip_reports.destroy_materials(db, ip)
    audit.record(
        db,
        user,
        "ip.material_destroy",
        ip_id=ip.id,
        target_type="ip",
        target_id=ip.id,
        detail={
            "asset_ids": [str(a) for a in result.asset_ids],
            "kept_shared": [str(a) for a in result.kept_shared],
        },
        request=request,
    )
    db.commit()
    purge(store, result.targets)
    return IpDestroyResponse(destroyed=len(result.asset_ids), kept_shared=len(result.kept_shared))


# -- 証跡 -----------------------------------------------------------------------------------


@router.get(
    "/{ip_id}/audit",
    response_model=AuditEventListResponse,
    operation_id="list_ip_audit",
    dependencies=_available_dep,
)
def list_ip_audit(
    ip_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=AUDIT_PAGE_MAX),
    cursor: str | None = Query(default=None),
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> AuditEventListResponse:
    """その IP の証跡(新しい順)。担当者と管理者だけ。"""
    ip = _visible(db, user, ip_id)
    _require_manage(db, user, ip)
    query = select(AuditEvent, AppUser).outerjoin(AppUser, AppUser.id == AuditEvent.user_id)
    query = query.where(AuditEvent.ip_id == ip.id)
    if cursor:
        try:
            moment, cursor_id = decode_cursor(cursor)
        except InvalidCursorError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        query = query.where(
            or_(
                AuditEvent.occurred_at < moment,
                and_(AuditEvent.occurred_at == moment, AuditEvent.id < cursor_id),
            )
        )
    rows = db.execute(
        query.order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc()).limit(limit + 1)
    ).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    last = rows[-1][0] if rows else None
    return AuditEventListResponse(
        items=[
            AuditEventView(
                id=event.id,
                occurred_at=event.occurred_at,
                user=_user_ref(actor),
                action=event.action,
                target_type=event.target_type,
                target_id=event.target_id,
                detail=event.detail,
            )
            for event, actor in rows
        ],
        next_cursor=encode_cursor(last.occurred_at, last.id) if has_more and last else None,
    )
