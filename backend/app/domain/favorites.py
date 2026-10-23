"""お気に入り(ADR-0055)。`asset_mark` の `flag = 'pick'` を、画像ごと・利用者ごとに持つ。

- 持ち主は見る人。認証モードは `app_user.id`、個人モード(id を持たない `LOCAL_ADMIN`)は NULL。
  他人の印は見えない(管理者も同じ)。
- 付け外しは冪等。外したら行を消す(証跡ではない)。
- 一覧は `favorite_join_condition` で出力の問い合わせに結合し、絞り込みは `favorite_asset_ids` の
  副問い合わせを使う(どちらも件数によらず問い合わせを増やさない)。
- 削除済みの Asset の行は残す(復元で戻る)。一覧・絞り込みでは呼ぶ側が `asset.deleted_at` で除く。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import ColumnElement, Select, and_, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.identity import CurrentUser
from app.domain.models import MARK_FLAG_PICK, Asset, AssetMark


def _utcnow() -> datetime:
    return datetime.now(UTC)


def mark_owner_condition(viewer: CurrentUser) -> ColumnElement[bool]:
    """`asset_mark` の行が `viewer` のものである条件。"""
    if viewer.id is None:
        return AssetMark.user_id.is_(None)
    return AssetMark.user_id == viewer.id


def favorite_join_condition(viewer: CurrentUser) -> ColumnElement[bool]:
    """`Asset` に `AssetMark` を外部結合するときの条件(1つの Asset に1人の行は1つだけ)。"""
    return and_(
        AssetMark.asset_id == Asset.id,
        mark_owner_condition(viewer),
        AssetMark.flag == MARK_FLAG_PICK,
    )


def favorite_asset_ids(viewer: CurrentUser) -> Select[tuple]:
    """`viewer` がお気に入りにした Asset の id の副問い合わせ(削除済みの Asset も含む)。"""
    return select(AssetMark.asset_id).where(
        mark_owner_condition(viewer), AssetMark.flag == MARK_FLAG_PICK
    )


def is_favorite(db: Session, viewer: CurrentUser, asset_id: uuid.UUID) -> bool:
    return (
        db.execute(
            select(AssetMark.id).where(
                AssetMark.asset_id == asset_id,
                mark_owner_condition(viewer),
                AssetMark.flag == MARK_FLAG_PICK,
            )
        ).first()
        is not None
    )


def set_favorite(db: Session, viewer: CurrentUser, asset: Asset, favorite: bool) -> None:
    """お気に入りにする・外す(冪等)。見えるか・削除済みでないかは呼ぶ側が確かめる。"""
    existing = db.execute(
        select(AssetMark).where(AssetMark.asset_id == asset.id, mark_owner_condition(viewer))
    ).scalar_one_or_none()
    if not favorite:
        if existing is not None:
            # 今は flag のほかに中身が無いので、行ごと消す(rating / note を足したら flag だけ外す)。
            db.execute(delete(AssetMark).where(AssetMark.id == existing.id))
            db.commit()
        return
    now = _utcnow()
    if existing is not None:
        if existing.flag != MARK_FLAG_PICK:
            existing.flag = MARK_FLAG_PICK
            existing.updated_at = now
            db.commit()
        return
    db.add(
        AssetMark(
            asset_id=asset.id,
            user_id=viewer.id,
            flag=MARK_FLAG_PICK,
            created_at=now,
            updated_at=now,
        )
    )
    try:
        db.commit()
    except IntegrityError:
        # 同時に付けた別の要求が先に入れた(一意索引)。結果は同じなので成功として扱う。
        db.rollback()
