"""GET /api/pricing/estimate。参考価格の見積もり(ADR-0009「参考価格」節)。

実装は app/domain/pricing.py に閉じる。見積もりであって請求額ではない。
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.deps import get_session
from app.domain.models import Asset
from app.domain.pricing import estimate_cost
from app.providers.openai_pricing import PRICING_CHECKED_AT, PRICING_SOURCE_URL

router = APIRouter(tags=["pricing"])

MAX_INPUT_IMAGES = 16


class UnitPricesPer1M(BaseModel):
    text_input: float
    image_input: float
    image_output: float


class InputImageEstimateResponse(BaseModel):
    asset_id: uuid.UUID
    width: int
    height: int
    tokens: int


class PriceEstimate(BaseModel):
    currency: Literal["USD"] = "USD"
    total_usd: float | None
    unavailable_reason: Literal["quality_auto", "size_auto", "size_invalid", "unknown_model"] | None
    output_tokens_per_image: int | None
    n: int
    input_image_tokens: int
    input_images: list[InputImageEstimateResponse]
    text_tokens: int
    unit_prices_per_1m: UnitPricesPer1M | None
    pricing_source: str = PRICING_SOURCE_URL
    pricing_checked_at: str = PRICING_CHECKED_AT


def _parse_input_asset_ids(raw: str | None) -> list[uuid.UUID]:
    """カンマ区切りの id 文字列を UUID のリストにする(形式が不正な項目は無視する)。

    最大 `MAX_INPUT_IMAGES` 件まで(Edit の入力上限と同じ)。超過分は切り捨てる。
    """
    if not raw:
        return []
    ids: list[uuid.UUID] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(uuid.UUID(part))
        except ValueError:
            continue
    return ids[:MAX_INPUT_IMAGES]


@router.get(
    "/api/pricing/estimate",
    response_model=PriceEstimate,
    operation_id="estimate_price",
)
def estimate_price(
    model: str = Query(...),
    operation: Literal["generate", "edit"] = Query(...),
    size: str = Query(default="auto"),
    quality: str = Query(default="auto"),
    n: int = Query(default=1, ge=1, le=10),
    prompt_length: int = Query(default=0, ge=0),
    input_asset_ids: str | None = Query(default=None),
    db: Session = Depends(get_session),
) -> PriceEstimate:
    # `operation` は API 契約上の必須クエリ(将来 generate/edit で推定の仕方を分ける余地の
    # ため)だが、現在の計算式は operation を見ない。
    del operation

    asset_ids = _parse_input_asset_ids(input_asset_ids)
    input_images: list[tuple[uuid.UUID, int, int]] = []
    if asset_ids:
        rows = (
            db.execute(select(Asset).where(Asset.id.in_(asset_ids), Asset.deleted_at.is_(None)))
            .scalars()
            .all()
        )
        by_id = {asset.id: asset for asset in rows}
        for asset_id in asset_ids:
            asset = by_id.get(asset_id)
            if asset is None or asset.width is None or asset.height is None:
                continue
            input_images.append((asset.id, asset.width, asset.height))

    result = estimate_cost(
        model=model,
        quality=quality,
        size=size,
        n=n,
        prompt_length=prompt_length,
        input_images=input_images,
    )

    unit_prices_per_1m = (
        UnitPricesPer1M(
            text_input=result.unit_prices.text_input,
            image_input=result.unit_prices.image_input,
            image_output=result.unit_prices.image_output,
        )
        if result.unit_prices is not None
        else None
    )

    return PriceEstimate(
        total_usd=result.total_usd,
        unavailable_reason=result.unavailable_reason,
        output_tokens_per_image=result.output_tokens_per_image,
        n=result.n,
        input_image_tokens=result.input_image_tokens,
        input_images=[
            InputImageEstimateResponse(
                asset_id=item.asset_id, width=item.width, height=item.height, tokens=item.tokens
            )
            for item in result.input_images
        ],
        text_tokens=result.text_tokens,
        unit_prices_per_1m=unit_prices_per_1m,
    )
