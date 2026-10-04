"""グループ(ストックの手動整理。ADR-0022)。証跡ではないので更新・論理削除ができる。

1 つの Asset が属するグループは 1 つだけ(2026-09-28 に多対多から変更)。グループに
入れる操作(`add_members`)は常に「移す」で、元のグループからは外れる。
メンバーの追加・削除・名前変更のたびに `updated_at` を進める。一覧(`list_groups`)は
利用者が決めた並び順(`position ASC, created_at DESC`)で返し、`updated_at` は並びに
影響しない(2026-09-28 に変更)。`member_count` / `cover_asset_id` は削除済みでない Asset だけを数える
(cover は `added_at` が最新のメンバー)。グループ数・メンバー数がどちらも少数な前提
(ADR-0022 のトレードオフ分析)で、都度 Python 側で集計する。

ADR-0025: グループと、その中の Asset は見る人(`viewer`)に見えるものだけを扱う
(`app/domain/visibility.py`)。`viewer=None` は worker(runner)の内部処理だけが使う
(Run の作成時に、実行者に見えるグループであることを確かめ済み)。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import ColumnElement, delete, func, select, true
from sqlalchemy.orm import Session

from app.auth.identity import CurrentUser
from app.domain.models import Asset, AssetGroup, AssetGroupMember
from app.domain.schemas import AssetGroupRef, AssetGroupRow
from app.domain.visibility import asset_visible, group_visible


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AssetGroupAssetsMissingError(ValueError):
    """追加しようとした asset_ids の中に、存在しない・削除済みの Asset があった。
    ルーター側で 404 に変換する(ADR-0022 3章)。
    """

    def __init__(self, missing_ids: list[uuid.UUID]) -> None:
        self.missing_ids = missing_ids
        super().__init__(f"asset ids not found or deleted: {missing_ids}")


class AssetGroupOrderMismatchError(ValueError):
    """並べ替えの id の一覧が、削除済みでない全グループの id と過不足なく一致しない
    (重複を含む)。ルーター側で 422 に変換する(ADR-0022 3章)。
    """


def _asset_cond(viewer: CurrentUser | None) -> ColumnElement[bool]:
    return true() if viewer is None else asset_visible(viewer)


def get_active_group_or_none(db: Session, group_id: uuid.UUID) -> AssetGroup | None:
    """可視性を見ずに引く(worker 専用)。API・MCP は `visibility.get_visible_group` を使う。"""
    group = db.get(AssetGroup, group_id)
    if group is None or group.deleted_at is not None:
        return None
    return group


def _member_stats(
    db: Session,
    group_ids: list[uuid.UUID],
    viewer: CurrentUser | None,
    kinds: list[str] | None = None,
) -> dict[uuid.UUID, tuple[int, uuid.UUID | None]]:
    """各グループの (member_count, cover_asset_id) をまとめて求める
    (削除済みでなく、`viewer` に見える Asset だけを対象にする)。
    `kinds` を渡したときは、その種類の Asset だけで数える(ADR-0035)。"""
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
        .where(
            AssetGroupMember.asset_group_id.in_(group_ids),
            Asset.deleted_at.is_(None),
            _asset_cond(viewer),
            Asset.kind.in_(kinds) if kinds else true(),
        )
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
        position=group.position,
        member_count=member_count,
        cover_asset_id=cover_asset_id,
        created_at=group.created_at,
        updated_at=group.updated_at,
    )


def _row_for_group(db: Session, group: AssetGroup, viewer: CurrentUser | None) -> AssetGroupRow:
    count, cover = _member_stats(db, [group.id], viewer).get(group.id, (0, None))
    return _to_row(group, count, cover)


def _active_groups_in_order(db: Session, viewer: CurrentUser) -> list[AssetGroup]:
    return list(
        db.execute(
            select(AssetGroup)
            .where(AssetGroup.deleted_at.is_(None), group_visible(viewer))
            .order_by(AssetGroup.position.asc(), AssetGroup.created_at.desc(), AssetGroup.id.desc())
        )
        .scalars()
        .all()
    )


def _rows_for_groups(
    db: Session,
    groups: list[AssetGroup],
    viewer: CurrentUser,
    kinds: list[str] | None = None,
) -> list[AssetGroupRow]:
    stats = _member_stats(db, [g.id for g in groups], viewer, kinds)
    return [_to_row(g, *stats.get(g.id, (0, None))) for g in groups]


def list_groups(
    db: Session, viewer: CurrentUser, kinds: list[str] | None = None
) -> list[AssetGroupRow]:
    """`viewer` に見える、削除されていないグループ(ADR-0025)。
    `kinds` を渡すと、`member_count` / `cover_asset_id` をその種類だけで数える(ADR-0035)。"""
    return _rows_for_groups(db, _active_groups_in_order(db, viewer), viewer, kinds)


def create_group(db: Session, name: str, viewer: CurrentUser) -> AssetGroupRow:
    """新しいグループは先頭に入れる(`viewer` に見える、削除済みでないグループの最小の
    `position` − 1。1件も無ければ 0)。作成者は `viewer`(個人モードは null)。"""
    now = _utcnow()
    min_position = db.execute(
        select(func.min(AssetGroup.position)).where(
            AssetGroup.deleted_at.is_(None), group_visible(viewer)
        )
    ).scalar_one_or_none()
    position = 0 if min_position is None else min_position - 1
    group = AssetGroup(
        name=name,
        position=position,
        created_by_user_id=viewer.id,
        created_at=now,
        updated_at=now,
    )
    db.add(group)
    db.flush()
    return _to_row(group, 0, None)


def rename_group(db: Session, group: AssetGroup, name: str, viewer: CurrentUser) -> AssetGroupRow:
    group.name = name
    group.updated_at = _utcnow()
    db.flush()
    return _row_for_group(db, group, viewer)


def reorder_groups(
    db: Session, group_ids: list[uuid.UUID], viewer: CurrentUser
) -> list[AssetGroupRow]:
    """`viewer` に見える、削除済みでない全グループを `group_ids` の順に並べ、`position` を
    0 から振り直す(他人のグループは対象にも照合にも含めない。ADR-0025)。
    `group_ids` がそれらの id と過不足なく一致しない(重複を含む)ときは
    `AssetGroupOrderMismatchError` を送出し、何も変えない。`updated_at` は進めない
    (並び順はグループの中身の変更ではない)。
    """
    groups = _active_groups_in_order(db, viewer)
    by_id = {g.id: g for g in groups}
    if len(group_ids) != len(set(group_ids)) or set(group_ids) != set(by_id):
        raise AssetGroupOrderMismatchError(
            f"group ids do not match active groups: {[str(i) for i in group_ids]}"
        )
    ordered = [by_id[group_id] for group_id in group_ids]
    for position, group in enumerate(ordered):
        group.position = position
    db.flush()
    return _rows_for_groups(db, ordered, viewer)


def delete_group(db: Session, group: AssetGroup) -> None:
    """論理削除のみ。メンバー行は残す(復元 API は今は無い。ADR-0022 2章)。"""
    group.deleted_at = _utcnow()


def add_members(
    db: Session,
    group: AssetGroup,
    asset_ids: list[uuid.UUID],
    *,
    viewer: CurrentUser | None,
) -> AssetGroupRow:
    """Asset をこのグループへ移す(ADR-0022 3章)。

    `viewer` に見えない Asset は、存在しない Asset と同じに扱う(ADR-0025)。`viewer=None` は
    worker が生成物を入れるときだけ。

    別のグループ(削除済みグループを含む)に入っている Asset は、その行を消してから入れる
    (所属は常に 1 行)。既にこのグループに入っているものはそのまま(`added_at` も変えない)。
    存在しない・削除済みの Asset が1件でもあれば、全体を拒み `AssetGroupAssetsMissingError`
    を送出する(何も変えない)。このグループと、Asset が外れた元のグループの `updated_at` を進める。
    """
    unique_ids = list(dict.fromkeys(asset_ids))
    found_ids = set(
        db.execute(
            select(Asset.id).where(
                Asset.id.in_(unique_ids), Asset.deleted_at.is_(None), _asset_cond(viewer)
            )
        )
        .scalars()
        .all()
    )
    missing = [asset_id for asset_id in unique_ids if asset_id not in found_ids]
    if missing:
        raise AssetGroupAssetsMissingError(missing)

    current = db.execute(
        select(AssetGroupMember.asset_id, AssetGroupMember.asset_group_id).where(
            AssetGroupMember.asset_id.in_(unique_ids)
        )
    ).all()
    already_here = {asset_id for asset_id, group_id in current if group_id == group.id}
    moving = [(asset_id, group_id) for asset_id, group_id in current if group_id != group.id]
    now = _utcnow()

    if moving:
        db.execute(
            delete(AssetGroupMember).where(
                AssetGroupMember.asset_id.in_([asset_id for asset_id, _ in moving]),
                AssetGroupMember.asset_group_id != group.id,
            )
        )
        # 一意索引に当たらないよう、元の行の削除を挿入より先に DB へ送る。
        db.flush()
        source_group_ids = {group_id for _, group_id in moving}
        for source in (
            db.execute(select(AssetGroup).where(AssetGroup.id.in_(source_group_ids)))
            .scalars()
            .all()
        ):
            source.updated_at = now

    for asset_id in unique_ids:
        if asset_id in already_here:
            continue
        db.add(AssetGroupMember(asset_group_id=group.id, asset_id=asset_id, added_at=now))

    group.updated_at = now
    db.flush()
    return _row_for_group(db, group, viewer)


def remove_members(
    db: Session, group: AssetGroup, asset_ids: list[uuid.UUID], viewer: CurrentUser
) -> AssetGroupRow:
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
    return _row_for_group(db, group, viewer)


def group_for_asset(db: Session, asset_id: uuid.UUID, viewer: CurrentUser) -> AssetGroupRef | None:
    """その Asset が属しているグループ(`AssetDetail.group` に使う)。所属は 1 つだけ。
    どこにも入っていない、削除済みグループにだけ残っている、または `viewer` に見えない
    グループ(ADR-0025)のときは None。"""
    group = db.execute(
        select(AssetGroup)
        .join(AssetGroupMember, AssetGroupMember.asset_group_id == AssetGroup.id)
        .where(
            AssetGroupMember.asset_id == asset_id,
            AssetGroup.deleted_at.is_(None),
            group_visible(viewer),
        )
    ).scalar_one_or_none()
    if group is None:
        return None
    return AssetGroupRef(id=group.id, name=group.name)
