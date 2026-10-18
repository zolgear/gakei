"""入力画像の配置(`asset.kind = 'composite'`)のレシピと素材(ADR-0050)。

- レシピ(`asset_composition.recipe`)は「何をどう置いたか」の記録。描くのはブラウザだけで、
  サーバーは描き直さない(画素の正本は保存した PNG)。形は `schemas.CompositionRecipe`。
- 素材(`asset_composition_source`)は系列をたどるための索引。`position` は素材を足した順で、
  0 が主たる素材。同じ素材を複数のレイヤーに使っても行は1つ。
- どちらも追記のみ(ADR-0003 1項)。保存は `assets.ingest_upload` から(PNG と同じトランザクション)。
- 系列(`lineage.py` など)は、素材 → 配置の結果の `composite_source` の辺を描く(Run を挟まない)。
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.identity import CurrentUser
from app.domain.models import Asset, AssetComposition, AssetCompositionSource, AssetKind
from app.domain.schemas import (
    AssetComposition as AssetCompositionView,
)
from app.domain.schemas import (
    AssetCompositionSourceView,
    CompositionRecipe,
)
from app.domain.storage import AssetStore
from app.domain.visibility import VisibilityChecker, get_visible_asset
from app.i18n import t

logger = logging.getLogger(__name__)


class CompositionError(ValueError):
    """レシピが不正、または素材が使えない(利用者に見せる文言)。"""


def parse_recipe(raw: str) -> CompositionRecipe:
    """フォームの `composition`(JSON の文字列)を読んで検証する。"""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise CompositionError(t("assets.compositionInvalid", reason="not JSON")) from e
    try:
        return CompositionRecipe.model_validate(data)
    except ValidationError as e:
        first = e.errors()[0] if e.errors() else {}
        location = ".".join(str(part) for part in first.get("loc", ()))
        message = str(first.get("msg", "invalid"))
        reason = f"{location}: {message}" if location else message
        raise CompositionError(t("assets.compositionInvalid", reason=reason)) from e


def check_sources(db: Session, viewer: CurrentUser, recipe: CompositionRecipe) -> None:
    """素材が `viewer` に見え(ADR-0025)、論理削除されておらず、マスクでないこと。"""
    for source_id in recipe.source_order():
        source = get_visible_asset(db, viewer, source_id)
        if source is None or source.deleted_at is not None:
            raise CompositionError(t("assets.compositionSourceNotFound"))
        if source.kind == AssetKind.MASK:
            raise CompositionError(t("assets.compositionSourceIsMask"))


def save_composition(
    db: Session,
    asset: Asset,
    recipe: CompositionRecipe,
    *,
    source_ids: list[uuid.UUID] | None = None,
    positions: list[int] | None = None,
    created_at: datetime | None = None,
) -> None:
    """配置の結果の Asset にレシピと素材の行を足す(flush まで)。

    素材の並びは既定ではレシピから決める(`CompositionRecipe.source_order`)。取り込み
    (ADR-0037)では、書き出し元の `position` をそのまま使うために `source_ids` と `positions`
    を渡す。
    """
    ids = source_ids if source_ids is not None else recipe.source_order()
    pos = positions if positions is not None else list(range(len(ids)))
    db.add(
        AssetComposition(
            asset_id=asset.id,
            recipe=recipe.model_dump(mode="json"),
            created_at=created_at or asset.created_at,
        )
    )
    for position, source_id in zip(pos, ids, strict=True):
        db.add(
            AssetCompositionSource(asset_id=asset.id, position=position, source_asset_id=source_id)
        )
    db.flush()


def composition_sources(db: Session, asset_id: uuid.UUID) -> list[AssetCompositionSource]:
    """配置の結果の素材(`position` の順)。配置でなければ空。"""
    return list(
        db.execute(
            select(AssetCompositionSource)
            .where(AssetCompositionSource.asset_id == asset_id)
            .order_by(AssetCompositionSource.position)
        )
        .scalars()
        .all()
    )


def composition_sources_bulk(
    db: Session, asset_ids: set[uuid.UUID] | list[uuid.UUID]
) -> dict[uuid.UUID, list[AssetCompositionSource]]:
    """複数の配置の結果の素材(配置の結果の id → `position` の順の行)。"""
    ids = list(asset_ids)
    result: dict[uuid.UUID, list[AssetCompositionSource]] = {}
    if not ids:
        return result
    rows = (
        db.execute(
            select(AssetCompositionSource)
            .where(AssetCompositionSource.asset_id.in_(ids))
            .order_by(AssetCompositionSource.asset_id, AssetCompositionSource.position)
        )
        .scalars()
        .all()
    )
    for row in rows:
        result.setdefault(row.asset_id, []).append(row)
    return result


def composites_using(db: Session, source_id: uuid.UUID) -> list[tuple[Asset, int]]:
    """この Asset を素材にした配置の結果と、その中での `position`(子孫の方向の探索)。"""
    rows = db.execute(
        select(Asset, AssetCompositionSource.position)
        .join(AssetCompositionSource, AssetCompositionSource.asset_id == Asset.id)
        .where(AssetCompositionSource.source_asset_id == source_id)
        .order_by(Asset.created_at, Asset.id, AssetCompositionSource.position)
    ).all()
    return [(asset, position) for asset, position in rows]


def load_recipe(db: Session, asset_id: uuid.UUID) -> dict | None:
    row = db.get(AssetComposition, asset_id)
    return row.recipe if row is not None and isinstance(row.recipe, dict) else None


def composition_view(
    db: Session,
    asset: Asset,
    checker: VisibilityChecker,
    store: AssetStore | None = None,
) -> AssetCompositionView | None:
    """`AssetDetail.composition`。配置でない、またはレシピが無い・読めないときは None。

    素材は `position` の順にすべて並べる。論理削除済み・見えない・原本が無い(`store` を渡した
    ときだけ確かめる)素材は `available = false`。
    """
    if asset.kind != AssetKind.COMPOSITE:
        return None
    raw = load_recipe(db, asset.id)
    if raw is None:
        return None
    try:
        recipe = CompositionRecipe.model_validate(raw)
    except ValidationError:
        logger.warning("asset %s のレシピが読めない形式のため無視した", asset.id)
        return None
    sources: list[AssetCompositionSourceView] = []
    for row in composition_sources(db, asset.id):
        source = db.get(Asset, row.source_asset_id)
        available = (
            source is not None
            and source.deleted_at is None
            # 画像を残さなかった素材(ADR-0051 4章)は読めない。
            and source.content_withheld_at is None
            and checker.asset(source)
        )
        if available and store is not None and source is not None:
            try:
                available = store.exists(source.blob_key)
            except Exception:  # noqa: BLE001 保存先に届かないときは「読めない」とする
                available = False
        sources.append(
            AssetCompositionSourceView(asset_id=row.source_asset_id, available=available)
        )
    return AssetCompositionView(recipe=recipe, sources=sources)
