"""サムネイルの焦点の読み書き(ADR-0043)。

焦点は証跡ではない派生データで、`asset_focal_point` に Asset ごとに1行持つ(来歴の列には
書かない。ADR-0003)。顔が見つからなかったときも `method='none'` の行を作り、同じ版では
探し直さない。応答に載せるのは `x`・`y` がある行だけ(版が古くても、作り直すまでは使う)。

計算は取り込みのあと焦点の worker(`app/worker/focal.py`)が行い、既存の Asset は埋め戻しの
ツール(`python -m app.tools.backfill_focal_points`)が行う。どちらも原本を `open_content` で
読む(オブジェクトストレージでも同じ。ADR-0028)。
"""

from __future__ import annotations

import io
import logging
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime

from PIL import Image
from PIL import UnidentifiedImageError as PillowUnidentifiedImageError
from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from app.domain.models import Asset, AssetFocalPoint, AssetKind
from app.domain.schemas import FocalPoint
from app.domain.storage import AssetStore
from app.focal import detect

logger = logging.getLogger(__name__)

# 一覧で `IN` に並べる id の上限(SQLite の変数の上限より十分小さく)。
_IN_CHUNK = 500


def _utcnow() -> datetime:
    return datetime.now(UTC)


def detect_from_bytes(data: bytes) -> detect.FocalPoint | None:
    """画像のバイト列から焦点を求める。画像として読めなければ None。"""
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            return detect.detect_focal_point(image)
    except (PillowUnidentifiedImageError, OSError, Image.DecompressionBombError, ValueError):
        logger.warning("焦点を求める画像を読めません", exc_info=True)
        return None


def detect_for_asset(store: AssetStore, asset: Asset) -> detect.FocalPoint | None:
    """Asset の原本から焦点を求める。原本が無い・読めないときは None。

    保存先との通信の失敗(`StorageIOError`)はそのまま上げる(焦点なしとして記録せず、
    あとで作り直せるように)。
    """
    content = store.open_content(asset.blob_key, asset.sha256, "original")
    if content is None:
        logger.warning("焦点を求める原本がありません: %s", asset.blob_key)
        return None
    return detect_from_bytes(content.read_all())


def save(session: Session, asset_id: uuid.UUID, point: detect.FocalPoint | None) -> None:
    """焦点を記録する(あれば置き換える)。commit はしない。"""
    row = session.get(AssetFocalPoint, asset_id)
    if row is None:
        row = AssetFocalPoint(asset_id=asset_id)
        session.add(row)
    row.x = point.x if point is not None else None
    row.y = point.y if point is not None else None
    row.method = detect.METHOD_ANIMEFACE_LBP if point is not None else detect.METHOD_NONE
    row.version = detect.ALGORITHM_VERSION
    row.computed_at = _utcnow()
    session.flush()


def needs_compute(session: Session, asset_id: uuid.UUID) -> bool:
    """まだ今の版の焦点が無いか。"""
    row = session.get(AssetFocalPoint, asset_id)
    return row is None or row.version < detect.ALGORITHM_VERSION


def pending_query(*, recompute: bool = False) -> Select:
    """焦点を求める Asset の id(マスク以外。論理削除済みも、復元に備えて含める)。
    `recompute` なら今の版の行がある Asset も含める。古い順(作成日時と id)に並べる。"""
    query = select(Asset.id).where(Asset.kind != AssetKind.MASK)
    if not recompute:
        query = query.outerjoin(AssetFocalPoint, AssetFocalPoint.asset_id == Asset.id).where(
            or_(
                AssetFocalPoint.asset_id.is_(None),
                AssetFocalPoint.version < detect.ALGORITHM_VERSION,
            )
        )
    return query.order_by(Asset.created_at, Asset.id)


def to_schema(x: float | None, y: float | None) -> FocalPoint | None:
    if x is None or y is None:
        return None
    return FocalPoint(x=x, y=y)


def bulk_get(session: Session, asset_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, FocalPoint]:
    """Asset の id ごとの焦点(焦点がある Asset だけ)。一覧の応答でまとめて引くのに使う。"""
    ids = list(dict.fromkeys(asset_ids))
    found: dict[uuid.UUID, FocalPoint] = {}
    for start in range(0, len(ids), _IN_CHUNK):
        chunk = ids[start : start + _IN_CHUNK]
        rows = session.execute(
            select(AssetFocalPoint.asset_id, AssetFocalPoint.x, AssetFocalPoint.y).where(
                AssetFocalPoint.asset_id.in_(chunk),
                AssetFocalPoint.x.is_not(None),
                AssetFocalPoint.y.is_not(None),
            )
        ).all()
        for asset_id, x, y in rows:
            value = to_schema(x, y)
            if value is not None:
                found[asset_id] = value
    return found


def get(session: Session, asset_id: uuid.UUID) -> FocalPoint | None:
    """1つの Asset の焦点。"""
    return bulk_get(session, (asset_id,)).get(asset_id)
