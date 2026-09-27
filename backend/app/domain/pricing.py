"""参考価格の見積もり(ADR-0009「参考価格」節)。

出力トークン数は、OpenAI の画像生成ガイド「Calculating costs」の公式計算機
(https://developers.openai.com/api/docs/guides/image-generation#calculating-costs、
2026-09-22 取得。UI は `GptImageTokenCalculator` という React コンポーネントで、その
JS バンドルから式を読み取った)と同じ式で、サイズと画質から決定的に計算する。
自分の実行実績(`run.usage.output_tokens`)と突き合わせて完全一致を確認済み:
- gpt-image-2.5, 1024x1024, low  → 196
- gpt-image-2.5, 1536x1024, low  → 158
- gpt-image-2.5, 2560x1440, low  → 205

入力画像のトークン数は OpenAI の vision ガイドにある画像入力の patch 式(32px パッチ、
予算1536パッチ)を流用した推定(GPT Image の入力トークンは公式には未公開)。Edit の実績
(`usage.input_tokens_details.image_tokens`)と突き合わせて完全一致を確認済み:
- 1024x1024        → 1024
- 1536x1024        → 1536
- 1024x1536        → 1536
- 341x512          → 704
- 1179x2556        → 1482
マスク(role=mask)は別フィールドで送るため、ここではトークンに数えない。

テキストトークンは実績(文字数→text_tokens)から「16 + 文字数」を上限寄りの目安とした
(単価が $5/1M と低く、精度は重要でないため)。

この式が使えない場合(quality=auto、size=auto、サイズが `app/domain/sizes.py` の制約を
満たさない、未知のモデル)は `None` を返し、見積もり不能であることを呼び出し側に伝える。
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from app.domain.sizes import InvalidSizeError, parse_size, validate_size
from app.providers.openai_pricing import UnitPrices, get_unit_prices

UnavailableReason = Literal["quality_auto", "size_auto", "size_invalid", "unknown_model"]

# サイズ×画質ごとの出力グリッドの長辺(公式計算機の JS から)。sunburst / flare は
# どちらも "gpt-image-2.5" に正規化されるため、モデル名スナップショットごとの表は不要。
OUTPUT_GRID: dict[str, dict[str, int]] = {
    "gpt-image-2": {"low": 16, "medium": 48, "high": 96},
    "gpt-image-2.5": {"low": 16, "medium": 24, "high": 48, "xhigh": 64, "max": 96},
}

# vision ガイドの patch 式のパラメーター(パッチサイズ32px、パッチ予算1536、倍率1)。
_PATCH_SIZE = 32
_PATCH_BUDGET = 1536
_MIN_LONG_EDGE_FOR_UPSCALE = 1024


def normalize_model(model: str) -> str | None:
    """モデル名(sunburst / flare、日付スナップショット付きを含む)を `OUTPUT_GRID` の
    キーに正規化する。該当しないモデルは `None`。

    `"gpt-image-2.5"` の判定を先に行う("gpt-image-2" は "gpt-image-2.5" の前方一致に
    なるため、順序を間違えると 2.5 系が 2 系に誤判定される)。
    """
    if model.startswith("gpt-image-2.5"):
        return "gpt-image-2.5"
    if model.startswith("gpt-image-2"):
        return "gpt-image-2"
    return None


def output_image_tokens(model: str, width: int, height: int, quality: str) -> int | None:
    """出力1枚あたりのトークン数(公式計算機と同じ式)。

    モデルが未知、quality がそのモデルの表に無い(auto 等)、サイズが
    `app/domain/sizes.py` の制約を満たさない場合は `None`。
    """
    normalized = normalize_model(model)
    if normalized is None:
        return None
    grid = OUTPUT_GRID[normalized]
    if quality not in grid:
        return None
    try:
        validate_size(width, height)
    except InvalidSizeError:
        return None

    long_edge, short_edge = max(width, height), min(width, height)
    grid_long = grid[quality]

    m = grid_long / (long_edge / short_edge)
    f = math.floor(m)
    # JS の Math.round は .5 を常に切り上げるが、ここでは .5 だけ偶数丸めに例外処理して
    # いる(原文どおりの分岐)。Python の round() は元々偶数丸めなので、この分岐が無くても
    # 同じ結果になるが、原文に忠実にするため残す。
    if m - f == 0.5:
        d = f + f % 2
    else:
        d = round(m)

    grid_w, grid_h = (grid_long, d) if width >= height else (d, grid_long)
    return math.ceil(grid_w * grid_h * (2_000_000 + width * height) / 4_000_000)


def input_image_tokens(width: int, height: int) -> int:
    """入力画像1枚あたりのトークン数(vision ガイドの32pxパッチ式、予算1536パッチ)。"""
    w, h = float(width), float(height)
    if max(w, h) < _MIN_LONG_EDGE_FOR_UPSCALE:
        scale = _MIN_LONG_EDGE_FOR_UPSCALE / max(w, h)
        w, h = round(w * scale), round(h * scale)

    patches = math.ceil(w / _PATCH_SIZE) * math.ceil(h / _PATCH_SIZE)
    if patches > _PATCH_BUDGET:
        s = math.sqrt(_PATCH_SIZE**2 * _PATCH_BUDGET / (w * h))
        s *= min(
            math.floor(w * s / _PATCH_SIZE) / (w * s / _PATCH_SIZE),
            math.floor(h * s / _PATCH_SIZE) / (h * s / _PATCH_SIZE),
        )
        w, h = math.floor(w * s), math.floor(h * s)
        patches = math.ceil(w / _PATCH_SIZE) * math.ceil(h / _PATCH_SIZE)

    return patches


def text_tokens(prompt_length: int) -> int:
    """テキストトークン数の目安(実績から「16 + 文字数」を上限寄りの目安とした)。"""
    return 16 + prompt_length


@dataclass(frozen=True)
class InputImageEstimate:
    asset_id: uuid.UUID
    width: int
    height: int
    tokens: int


@dataclass(frozen=True)
class EstimateResult:
    total_usd: float | None
    unavailable_reason: UnavailableReason | None
    output_tokens_per_image: int | None
    n: int
    input_image_tokens: int
    input_images: list[InputImageEstimate] = field(default_factory=list)
    text_tokens: int = 0
    unit_prices: UnitPrices | None = None


def estimate_cost(
    *,
    model: str,
    quality: str,
    size: str,
    n: int,
    prompt_length: int,
    input_images: Sequence[tuple[uuid.UUID, int, int]] = (),
) -> EstimateResult:
    """参考価格を合算する。見積もりであって請求額ではない。

    出力トークンが計算できない場合(`unavailable_reason` が非 None)は `total_usd=None`
    になるが、入力画像・テキストのトークン数は分かる範囲で計算して返す。
    """
    prices = get_unit_prices(model)
    normalized = normalize_model(model)

    input_estimates = [
        InputImageEstimate(asset_id=asset_id, width=w, height=h, tokens=input_image_tokens(w, h))
        for asset_id, w, h in input_images
    ]
    input_tokens_total = sum(item.tokens for item in input_estimates)
    prompt_tokens = text_tokens(prompt_length)

    reason: UnavailableReason | None = None
    output_tokens_per_image: int | None = None

    if normalized is None or prices is None:
        reason = "unknown_model"
    elif quality not in OUTPUT_GRID[normalized]:
        reason = "quality_auto"
    else:
        try:
            parsed = parse_size(size)
        except InvalidSizeError:
            reason = "size_invalid"
        else:
            if parsed is None:  # size == "auto"
                reason = "size_auto"
            else:
                width, height = parsed
                output_tokens_per_image = output_image_tokens(model, width, height, quality)
                if output_tokens_per_image is None:
                    # sizes.py の検証は通ったのに計算できないケース(通常は起きない)。
                    reason = "size_invalid"

    total_usd: float | None = None
    if reason is None and prices is not None and output_tokens_per_image is not None:
        total_usd = (
            prompt_tokens * prices.text_input
            + input_tokens_total * prices.image_input
            + output_tokens_per_image * n * prices.image_output
        ) / 1_000_000

    return EstimateResult(
        total_usd=total_usd,
        unavailable_reason=reason,
        output_tokens_per_image=output_tokens_per_image,
        n=n,
        input_image_tokens=input_tokens_total,
        input_images=input_estimates,
        text_tokens=prompt_tokens,
        unit_prices=prices,
    )


def _as_number(value: object) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


def cost_from_usage(model: str, usage: dict[str, Any] | None) -> float | None:
    """成功した Run の実コスト(参考)。`usage` × 単価。

    `usage["input_tokens_details"]` があればテキスト/画像の内訳をそのまま使う。無ければ
    `input_tokens` 全体をテキスト扱いする(Generate で入力画像が無い場合など)。
    モデルが未知、または `usage` が無い(未実行・失敗)場合は `None`。
    """
    if usage is None:
        return None
    prices = get_unit_prices(model)
    if prices is None:
        return None

    details = usage.get("input_tokens_details")
    if isinstance(details, dict):
        text_in = _as_number(details.get("text_tokens"))
        image_in = _as_number(details.get("image_tokens"))
    else:
        text_in = _as_number(usage.get("input_tokens"))
        image_in = 0.0
    output = _as_number(usage.get("output_tokens"))

    total = (
        text_in * prices.text_input + image_in * prices.image_input + output * prices.image_output
    ) / 1_000_000
    return round(total, 6)
