"""グループ(ストックの手動整理。ADR-0022)。証跡ではないので更新・論理削除ができる。

メンバーの追加・削除・名前変更のたびに `updated_at` を進め、一覧(`list_groups`)は
それの降順で返す。`member_count` / `cover_asset_id` は削除済みでない Asset だけを数える
(cover は `added_at` が最新のメンバー)。グループ数・メンバー数がどちらも少数な前提
(ADR-0022 のトレードオフ分析)で、都度 Python 側で集計する。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.domain.models import Asset, AssetGroup, AssetGroupMember
from app.domain.schemas import AssetGroupRef, AssetGroupRow


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AssetGroupAssetsMissingError(ValueError):
    """追加しようとした asset_ids の中に、存在しない・削除済みの Asset があった。
    ルーター側で 404 に変換する(ADR-0022 3章)。
    """

    def __init__(self, missing_ids: list[uuid.UUID]) -> None:
        self.missing_ids = missing_ids
        super().__init__(f"asset ids not found or deleted: {missing_ids}")


def get_active_group_or_none(db: Session, group_id: uuid.UUID) -> AssetGroup | None:
    group = db.get(AssetGroup, group_id)
    if group is None or group.deleted_at is not None:
        return None
    return group


def _member_stats(
    db: Session, group_ids: list[uuid.UUID]
) -> dict[uuid.UUID, tuple[int, uuid.UUID | None]]:
    """各グループの (member_count, cover_asset_id) をまとめて求める
    (削除済みでない Asset だけを対象にする)。"""
    counts: dict[uuid.UUID, int] = dict.fromkeys(group_ids, 0)
    cover: dict[uuid.UUID, uuid.UUID | None] = dict.fromkeys(group_ids, None)
    if not group_ids:
        return {}

    latest_added_at: dict[uuid.UUID, datetime] = {}
    rows = db.execute(
        select(
            AssetGroupMember.asset_group_id, AssetGroupMember.asset_id, AssetGroupMember.added_at
        )
        .join(Asset, Asset.id == AssetGroupMember.asset_id)
        .where(AssetGroupMember.asset_group_id.in_(group_ids), Asset.deleted_at.is_(None))
    ).all()
    for group_id, asset_id, added_at in rows:
        counts[group_id] += 1
        if group_id not in latest_added_at or added_at > latest_added_at[group_id]:
            latest_added_at[group_id] = added_at
            cover[group_id] = asset_id
    return {group_id: (counts[group_id], cover[group_id]) for group_id in group_ids}


def _to_row(
    group: AssetGroup, member_count: int, cover_asset_id: uuid.UUID | None
) -> AssetGroupRow:
    return AssetGroupRow(
        id=group.id,
        name=group.name,
        member_count=member_count,
        cover_asset_id=cover_asset_id,
        created_at=group.created_at,
        updated_at=group.updated_at,
    )


def _row_for_group(db: Session, group: AssetGroup) -> AssetGroupRow:
    count, cover = _member_stats(db, [group.id]).get(group.id, (0, None))
    return _to_row(group, count, cover)


def list_groups(db: Session) -> list[AssetGroupRow]:
    groups = (
        db.execute(
            select(AssetGroup)
            .where(AssetGroup.deleted_at.is_(None))
            .order_by(AssetGroup.updated_at.desc(), AssetGroup.id.desc())
        )
        .scalars()
        .all()
    )
    stats = _member_stats(db, [g.id for g in groups])
    return [_to_row(g, *stats.get(g.id, (0, None))) for g in groups]


def create_group(db: Session, name: str, created_by_user_id: uuid.UUID | None) -> AssetGroupRow:
    now = _utcnow()
    group = AssetGroup(
        name=name, created_by_user_id=created_by_user_id, created_at=now, updated_at=now
    )
    db.add(group)
    db.flush()
    return _to_row(group, 0, None)


def rename_group(db: Session, group: AssetGroup, name: str) -> AssetGroupRow:
    group.name = name
    group.updated_at = _utcnow()
    db.flush()
    return _row_for_group(db, group)


def delete_group(db: Session, group: AssetGroup) -> None:
    """論理削除のみ。メンバー行は残す(復元 API は今は無い。ADR-0022 2章)。"""
    group.deleted_at = _utcnow()


def add_members(db: Session, group: AssetGroup, asset_ids: list[uuid.UUID]) -> AssetGroupRow:
    """既に入っているものは無視する。存在しない・削除済みの Asset が1件でもあれば、
    全体を拒み `AssetGroupAssetsMissingError` を送出する(何も追加しない)。
    """
    unique_ids = list(dict.fromkeys(asset_ids))
    found_ids = set(
        db.execute(select(Asset.id).where(Asset.id.in_(unique_ids), Asset.deleted_at.is_(None)))
        .scalars()
        .all()
    )
    missing = [asset_id for asset_id in unique_ids if asset_id not in found_ids]
    if missing:
        raise AssetGroupAssetsMissingError(missing)

    existing_member_ids = set(
        db.execute(
            select(AssetGroupMember.asset_id).where(
                AssetGroupMember.asset_group_id == group.id,
                AssetGroupMember.asset_id.in_(unique_ids),
            )
        )
        .scalars()
        .all()
    )
    now = _utcnow()
    for asset_id in unique_ids:
        if asset_id in existing_member_ids:
            continue
        db.add(AssetGroupMember(asset_group_id=group.id, asset_id=asset_id, added_at=now))

    group.updated_at = now
    db.flush()
    return _row_for_group(db, group)


def remove_members(db: Session, group: AssetGroup, asset_ids: list[uuid.UUID]) -> AssetGroupRow:
    """入っていないものは無視する(エラーにしない)。"""
    unique_ids = list(dict.fromkeys(asset_ids))
    db.execute(
        delete(AssetGroupMember).where(
            AssetGroupMember.asset_group_id == group.id,
            AssetGroupMember.asset_id.in_(unique_ids),
        )
    )
    group.updated_at = _utcnow()
    db.flush()
    return _row_for_group(db, group)


def groups_for_asset(db: Session, asset_id: uuid.UUID) -> list[AssetGroupRef]:
    """その Asset が属している、削除済みでないグループを名前の昇順で返す
    (`AssetDetail.groups` に使う)。"""
    groups = (
        db.execute(
            select(AssetGroup)
            .join(AssetGroupMember, AssetGroupMember.asset_group_id == AssetGroup.id)
            .where(AssetGroupMember.asset_id == asset_id, AssetGroup.deleted_at.is_(None))
            .order_by(AssetGroup.name, AssetGroup.id)
        )
        .scalars()
        .all()
    )
    return [AssetGroupRef(id=g.id, name=g.name) for g in groups]
