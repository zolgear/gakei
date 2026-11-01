"""IP 素材を使うときの申告と検査、派生物への引き継ぎ(ADR-0059 5章・6章)。

- 引き継ぎ: Asset が引き継ぐ IP は `asset_ip_lineage` に、Asset を作るときに書く(`inherit`)。
  素材そのもの、Run の出力(入力の IP の和)、スケッチ・領域の下地、配置の素材から。
- 申告と検査: IP を引き継ぐ Asset を入力にする Run は、用途の申告が要る。Run を作るときと、
  worker が実行する直前の2回、`plan_use` で確かめる。
- 外部の AI へ送る経路(自動タイトル・タグ、埋め込み、領域の検出、アシスタント)は、
  `ai_blocked_asset_ids` で契約の条件(外部の AI への送信)に合わない Asset を外す。
- 派生物を読める人: 作った本人(普段の可視性)、すべての IP の「見る」の許可を持つ人、
  いずれかの IP の担当者と管理者(`readable_derived_condition`)。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import ColumnElement, and_, exists, false, not_, or_, select
from sqlalchemy.orm import Session, aliased

from app.auth.identity import CurrentUser
from app.config import Settings
from app.domain import ip_materials
from app.domain import locality as locality_domain
from app.domain.models import (
    AssetCompositionSource,
    AssetIpLineage,
    ComfyWorkflow,
    IpGrant,
    IpMaterial,
    IpProperty,
    IpTerms,
    RunInput,
    RunIpUse,
)

PURPOSE_MAX_LENGTH = 100
WORK_NOTE_MAX_LENGTH = 1000


class IpUseError(Exception):
    """使えない(申告が無い、許可が無い、条件に合わない)。`key` は文言のキー、`ip_id` は関わる
    IP。"""

    def __init__(self, key: str, ip_id: uuid.UUID | None = None, **params: str) -> None:
        super().__init__(key)
        self.key = key
        self.ip_id = ip_id
        self.params = params


@dataclass(frozen=True)
class Declaration:
    purpose: str
    work_note: str | None


@dataclass(frozen=True)
class UsePlan:
    ip_id: uuid.UUID
    terms_id: uuid.UUID
    grant_id: uuid.UUID


def _today() -> date:
    return datetime.now(UTC).date()


def normalize_declaration(purpose: str | None, work_note: str | None) -> Declaration | None:
    if purpose is None or not purpose.strip():
        return None
    note = (work_note or "").strip()[:WORK_NOTE_MAX_LENGTH] or None
    return Declaration(purpose=purpose.strip()[:PURPOSE_MAX_LENGTH], work_note=note)


# -- 引き継ぎ -------------------------------------------------------------------------------


def ip_ids_of(db: Session, asset_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, set[uuid.UUID]]:
    """Asset ごとの引き継ぐ IP(引き継がない Asset は入らない)。"""
    ids = list(dict.fromkeys(asset_ids))
    result: dict[uuid.UUID, set[uuid.UUID]] = {}
    if not ids:
        return result
    for asset_id, ip_id in db.execute(
        select(AssetIpLineage.asset_id, AssetIpLineage.ip_id).where(
            AssetIpLineage.asset_id.in_(ids)
        )
    ).all():
        result.setdefault(asset_id, set()).add(ip_id)
    return result


def union_ip_ids(db: Session, asset_ids: Iterable[uuid.UUID]) -> set[uuid.UUID]:
    found: set[uuid.UUID] = set()
    for ips in ip_ids_of(db, asset_ids).values():
        found |= ips
    return found


def inherit(db: Session, asset_id: uuid.UUID, parent_ids: Iterable[uuid.UUID]) -> set[uuid.UUID]:
    """親(入力・下地・素材)の IP を `asset_id` に書く(既にあるものは書かない)。書いた IP を返す。"""
    ips = union_ip_ids(db, parent_ids)
    if not ips:
        return set()
    existing = ip_ids_of(db, [asset_id]).get(asset_id, set())
    for ip_id in ips - existing:
        db.add(AssetIpLineage(asset_id=asset_id, ip_id=ip_id))
    db.flush()
    return ips


def mark_material(db: Session, asset_id: uuid.UUID, ip_id: uuid.UUID) -> None:
    """素材そのものの引き継ぎ(素材の取り込みで呼ぶ)。"""
    if db.get(AssetIpLineage, (asset_id, ip_id)) is None:
        db.add(AssetIpLineage(asset_id=asset_id, ip_id=ip_id))
        db.flush()


def inherit_from_run_inputs(db: Session, asset_id: uuid.UUID, run_id: uuid.UUID) -> None:
    inputs = db.execute(select(RunInput.asset_id).where(RunInput.run_id == run_id)).scalars().all()
    inherit(db, asset_id, inputs)


def inherit_from_composition(db: Session, asset_id: uuid.UUID) -> None:
    sources = (
        db.execute(
            select(AssetCompositionSource.source_asset_id).where(
                AssetCompositionSource.asset_id == asset_id
            )
        )
        .scalars()
        .all()
    )
    inherit(db, asset_id, sources)


def ip_marked_ids(db: Session, asset_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    """IP を引き継ぐ(素材か派生物の)Asset(一覧の印)。"""
    return set(ip_ids_of(db, asset_ids))


# -- 派生物を読める人 ------------------------------------------------------------------------


def readable_derived_condition(viewer: CurrentUser, asset_id_column) -> ColumnElement[bool]:  # noqa: ANN001
    """IP を引き継ぐ Asset を `viewer` が読める条件(作った本人の分は普段の可視性で見る)。"""
    if viewer.id is None:
        return false()
    # 素材そのものは素材の規則で見る(取り下げたものは許可を受けた人に見せない。3章)。
    any_lineage = and_(
        exists().where(AssetIpLineage.asset_id == asset_id_column),
        not_(exists().where(IpMaterial.asset_id == asset_id_column)),
    )
    if viewer.is_admin:
        return any_lineage
    managed = exists().where(
        AssetIpLineage.asset_id == asset_id_column,
        AssetIpLineage.ip_id.in_(ip_materials.managed_ip_ids(viewer.id)),
    )
    other = aliased(AssetIpLineage)
    missing_grant = exists().where(
        other.asset_id == asset_id_column,
        other.ip_id.not_in(ip_materials.granted_ip_ids(viewer.id)),
    )
    return and_(any_lineage, or_(managed, not_(missing_grant)))


def can_read_derived(db: Session, viewer: CurrentUser, asset_id: uuid.UUID) -> bool:
    from app.domain.models import Asset

    return (
        db.execute(
            select(Asset.id).where(
                Asset.id == asset_id, readable_derived_condition(viewer, Asset.id)
            )
        ).first()
        is not None
    )


# -- 外部の AI へ送る経路 --------------------------------------------------------------------


def ai_blocked_asset_ids(
    db: Session, asset_ids: Iterable[uuid.UUID], locality: str
) -> set[uuid.UUID]:
    """契約の条件で、この送り先(`external` / `local`)の AI に送れない Asset。

    `deny` の IP を引き継ぐものはどこにも送らない。`local_only` の IP を引き継ぐものは、外部の
    送り先に送らない。"""
    ip_map = ip_ids_of(db, asset_ids)
    if not ip_map:
        return set()
    all_ips = set().union(*ip_map.values())
    policies = {ip_id: _external_ai(db, ip_id) for ip_id in all_ips}

    def blocked(ips: set[uuid.UUID]) -> bool:
        for ip_id in ips:
            policy = policies.get(ip_id) or "deny"
            if policy == "deny" or (policy == "local_only" and locality != "local"):
                return True
        return False

    return {asset_id for asset_id, ips in ip_map.items() if blocked(ips)}


def _external_ai(db: Session, ip_id: uuid.UUID) -> str | None:
    terms = ip_materials.current_terms(db, ip_id)
    return terms.external_ai if terms is not None else None


# -- 申告と検査 -----------------------------------------------------------------------------


def run_locality(db: Session, settings: Settings | None, provider: str, model: str) -> str:
    """画像の生成の送り先の区分(ComfyUI はワークフローの値を優先する)。`mods.events.run_target`
    と同じ決め方。"""
    if provider in locality_domain.PROVIDERS:
        connection = locality_domain.provider_locality(db, settings, provider)
    else:
        connection = (locality_domain.DEFAULT, "default")
    workflow = None
    if provider == "comfyui":
        try:
            row = db.get(ComfyWorkflow, uuid.UUID(model))
        except ValueError:
            row = None
        if row is not None:
            workflow = locality_domain.read_workflow(row.locality)
    locality, _source = locality_domain.resolve(connection, workflow)  # type: ignore[arg-type]
    return locality


def _usable_grant(
    db: Session, viewer: CurrentUser, ip_id: uuid.UUID, purpose: str
) -> IpGrant | None:
    if viewer.id is None:
        return None
    grants = (
        db.execute(
            select(IpGrant)
            .where(
                IpGrant.ip_id == ip_id,
                IpGrant.can_use.is_(True),
                ip_materials.active_grant_condition(viewer.id),
            )
            .order_by(IpGrant.granted_at)
        )
        .scalars()
        .all()
    )
    return next((g for g in grants if purpose in (g.purposes or [])), None)


def plan_use(
    db: Session,
    viewer: CurrentUser,
    input_asset_ids: list[uuid.UUID],
    declaration: Declaration | None,
    *,
    locality: str,
) -> list[UsePlan]:
    """入力の IP について、使ってよいかを確かめ、申告に使う条件の版と許可を返す。IP を引き継ぐ
    入力が無ければ空(申告は要らない)。使えなければ `IpUseError`。"""
    ip_map = ip_ids_of(db, input_asset_ids)
    if not ip_map:
        return []
    ip_ids = sorted(set().union(*ip_map.values()), key=str)
    if declaration is None:
        raise IpUseError("ip.declarationRequired", ip_ids[0])
    withdrawn = set(
        db.execute(
            select(IpMaterial.asset_id).where(
                IpMaterial.asset_id.in_(input_asset_ids), IpMaterial.withdrawn_at.is_not(None)
            )
        )
        .scalars()
        .all()
    )
    if withdrawn:
        material = db.get(IpMaterial, next(iter(withdrawn)))
        raise IpUseError("ip.materialWithdrawn", material.ip_id if material else None)
    today = _today()
    plans: list[UsePlan] = []
    for ip_id in ip_ids:
        ip = db.get(IpProperty, ip_id)
        if ip is None or ip.state != "active":
            raise IpUseError("ip.notActive", ip_id, name=ip.name if ip else "")
        terms: IpTerms | None = ip_materials.current_terms(db, ip_id)
        if terms is None or (
            (terms.valid_from is not None and terms.valid_from > today)
            or (terms.valid_until is not None and terms.valid_until < today)
        ):
            raise IpUseError("ip.outOfContract", ip_id, name=ip.name)
        if declaration.purpose not in (terms.purposes or []):
            raise IpUseError("ip.purposeNotAllowed", ip_id, name=ip.name)
        if terms.external_ai == "deny" or (
            terms.external_ai == "local_only" and locality != "local"
        ):
            raise IpUseError("ip.providerNotAllowed", ip_id, name=ip.name)
        grant = _usable_grant(db, viewer, ip_id, declaration.purpose)
        if grant is None:
            raise IpUseError("ip.noGrant", ip_id, name=ip.name)
        plans.append(UsePlan(ip_id=ip_id, terms_id=terms.id, grant_id=grant.id))
    return plans


def record_use(
    db: Session, run_id: uuid.UUID, plans: list[UsePlan], declaration: Declaration | None
) -> None:
    if not plans or declaration is None:
        return
    now = datetime.now(UTC)
    for plan in plans:
        db.add(
            RunIpUse(
                run_id=run_id,
                ip_id=plan.ip_id,
                terms_id=plan.terms_id,
                grant_id=plan.grant_id,
                purpose=declaration.purpose,
                work_note=declaration.work_note,
                created_at=now,
            )
        )


def declaration_of_run(db: Session, run_id: uuid.UUID) -> Declaration | None:
    row = db.execute(select(RunIpUse).where(RunIpUse.run_id == run_id).limit(1)).scalars().first()
    if row is None:
        return None
    return Declaration(purpose=row.purpose, work_note=row.work_note)


def uses_of_run(db: Session, run_id: uuid.UUID) -> list[RunIpUse]:
    return list(
        db.execute(select(RunIpUse).where(RunIpUse.run_id == run_id).order_by(RunIpUse.ip_id))
        .scalars()
        .all()
    )


# -- 申告の前の確かめ(フォーム) --------------------------------------------------------------


@dataclass(frozen=True)
class UsageIp:
    ip: IpProperty
    terms: IpTerms | None
    purposes: list[str]
    problem: str | None


def usage_summary(
    db: Session, viewer: CurrentUser, asset_ids: list[uuid.UUID], *, locality: str
) -> tuple[bool, list[UsageIp], list[str]]:
    """フォームで申告を出すための情報。(申告が要るか、IP ごとの情報、どの IP でも使える用途)。"""
    ip_map = ip_ids_of(db, asset_ids)
    if not ip_map:
        return False, [], []
    today = _today()
    items: list[UsageIp] = []
    common: list[str] | None = None
    for ip_id in sorted(set().union(*ip_map.values()), key=str):
        ip = db.get(IpProperty, ip_id)
        if ip is None:
            continue
        terms = ip_materials.current_terms(db, ip_id)
        problem: str | None = None
        if ip.state != "active":
            problem = "ip.notActive"
        elif terms is None or (
            (terms.valid_from is not None and terms.valid_from > today)
            or (terms.valid_until is not None and terms.valid_until < today)
        ):
            problem = "ip.outOfContract"
        elif terms.external_ai == "deny" or (
            terms.external_ai == "local_only" and locality != "local"
        ):
            problem = "ip.providerNotAllowed"
        allowed = set(terms.purposes or []) if terms is not None else set()
        purposes: list[str] = []
        if viewer.id is not None:
            for grant in db.execute(
                select(IpGrant)
                .where(
                    IpGrant.ip_id == ip_id,
                    IpGrant.can_use.is_(True),
                    ip_materials.active_grant_condition(viewer.id),
                )
                .order_by(IpGrant.granted_at)
            ).scalars():
                for purpose in grant.purposes or []:
                    if purpose in allowed and purpose not in purposes:
                        purposes.append(purpose)
        if problem is None and not purposes:
            problem = "ip.noGrant"
        items.append(UsageIp(ip=ip, terms=terms, purposes=purposes, problem=problem))
        common = purposes if common is None else [p for p in common if p in purposes]
    return True, items, common or []
