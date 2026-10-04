"""Asset の埋め込みベクトルの待ち行列と保存(ADR-0033 4章・5章)。

埋め込みは証跡ではない。来歴の列(`asset`、`run`、`run_input`)には書き込まず、Run でもない。
行は(Asset、`model_key`)ごとで、`status` が待ち行列を兼ねる。

- 取り込み時の自動実行(`enqueue_on_ingest`)、一括実行(`backfill`)、1枚の再計算
  (`request_embedding`)は、いずれも使うモデルの行を `queued` にするだけ。worker
  (`app/worker/embedder.py`)が計算する。
- マスクは対象外。削除済みの Asset は待ち行列に入れない(ベクトルは消さない。検索のときに
  除く)。
- 呼び出し側がトランザクション(commit)を制御できるよう、ここでは `flush` までに留める。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session

from app.domain import embedding_settings
from app.domain.models import Asset, AssetEmbedding, AssetKind

if TYPE_CHECKING:
    from app.config import Settings

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def is_target(asset: Asset) -> bool:
    """計算の対象か(マスクと削除済みは対象外)。"""
    return asset.kind != AssetKind.MASK and asset.deleted_at is None


def _queue(row: AssetEmbedding, now: datetime) -> None:
    row.status = STATUS_QUEUED
    row.error = None
    row.requested_at = now
    row.finished_at = None
    row.updated_at = now


def request_embedding(db: Session, asset: Asset, model_key: str) -> bool:
    """1枚の(再)計算を待ち行列に入れる。既に queued / running なら何もしない(False)。
    計算済みのベクトルは、計算し直すまで残す。"""
    row = db.get(AssetEmbedding, (asset.id, model_key))
    now = _utcnow()
    if row is None:
        row = AssetEmbedding(asset_id=asset.id, model_key=model_key, status=STATUS_QUEUED)
        db.add(row)
    elif row.status in (STATUS_QUEUED, STATUS_RUNNING):
        return False
    _queue(row, now)
    db.flush()
    return True


def enqueue_on_ingest(db: Session, asset: Asset, settings: Settings) -> bool:
    """取り込み時の自動実行(ADR-0033 5章)。無効、自動実行がオフ、使えるモデルが無い、
    マスクなら何もしない。待ち行列に入れたら True(呼び出し側は commit 後に worker を起こす)。"""
    if not is_target(asset):
        return False
    config = embedding_settings.load(db)
    if not config.auto_on_ingest or not embedding_settings.usable(config, settings):
        return False
    model_key = embedding_settings.active_model_key(config, settings)
    assert model_key is not None
    return request_embedding(db, asset, model_key)


def _pending_condition(model_key: str) -> Any:
    """使うモデルのベクトルが無い(行が無い、または失敗した)・削除済みでない・マスク以外。
    待ち行列にあるもの(queued / running)は含めない。"""
    return and_(
        Asset.deleted_at.is_(None),
        Asset.kind != AssetKind.MASK,
        or_(AssetEmbedding.asset_id.is_(None), AssetEmbedding.status == STATUS_FAILED),
    )


def _outer_join_for(model_key: str) -> Any:
    return and_(AssetEmbedding.asset_id == Asset.id, AssetEmbedding.model_key == model_key)


def pending_count(db: Session, model_key: str) -> int:
    """一括実行の対象の件数(使うモデルのベクトルが無い画像。失敗したものを含む)。"""
    return db.execute(
        select(func.count())
        .select_from(Asset)
        .outerjoin(AssetEmbedding, _outer_join_for(model_key))
        .where(_pending_condition(model_key))
    ).scalar_one()


def status_count(db: Session, model_key: str, statuses: tuple[str, ...]) -> int:
    return db.execute(
        select(func.count())
        .select_from(AssetEmbedding)
        .where(AssetEmbedding.model_key == model_key, AssetEmbedding.status.in_(statuses))
    ).scalar_one()


def stored_counts(db: Session) -> list[tuple[str, int, int | None]]:
    """モデルごとの保存済みのベクトルの件数と次元(`model_key` の順)。"""
    rows = db.execute(
        select(AssetEmbedding.model_key, func.count(), func.max(AssetEmbedding.dim))
        .where(AssetEmbedding.status == STATUS_SUCCEEDED, AssetEmbedding.vector.is_not(None))
        .group_by(AssetEmbedding.model_key)
        .order_by(AssetEmbedding.model_key)
    ).all()
    return [(key, int(count), dim) for key, count, dim in rows]


def known_dim(db: Session, model_key: str) -> int | None:
    """そのモデルで計算済みのベクトルの次元(無ければ None)。"""
    return db.execute(
        select(AssetEmbedding.dim)
        .where(
            AssetEmbedding.model_key == model_key,
            AssetEmbedding.status == STATUS_SUCCEEDED,
            AssetEmbedding.dim.is_not(None),
        )
        .limit(1)
    ).scalar_one_or_none()


def backfill(db: Session, model_key: str) -> int:
    """使うモデルのベクトルが無い画像(削除済みとマスクを除く。失敗したものを含む)を
    まとめて待ち行列に入れ、件数を返す(ADR-0033 5章「一括実行」)。"""
    rows = db.execute(
        select(Asset.id, AssetEmbedding)
        .outerjoin(AssetEmbedding, _outer_join_for(model_key))
        .where(_pending_condition(model_key))
        .order_by(Asset.created_at.asc(), Asset.id.asc())
    ).all()
    now = _utcnow()
    for asset_id, row in rows:
        if row is None:
            row = AssetEmbedding(asset_id=asset_id, model_key=model_key, status=STATUS_QUEUED)
            db.add(row)
        _queue(row, now)
    db.flush()
    return len(rows)


def delete_vectors(db: Session, model_key: str) -> int:
    """そのモデルの行(ベクトルと状態)をすべて消し、件数を返す。"""
    result = db.execute(delete(AssetEmbedding).where(AssetEmbedding.model_key == model_key))
    db.flush()
    return result.rowcount or 0


def embedding_status(db: Session, asset_id: uuid.UUID, model_key: str) -> AssetEmbedding | None:
    return db.get(AssetEmbedding, (asset_id, model_key))
