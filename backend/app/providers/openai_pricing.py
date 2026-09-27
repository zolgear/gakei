"""OpenAI gpt-image-2.5 系の Standard 料金表(参考価格の見積もり用)。

OpenAI 料金ページ(https://developers.openai.com/api/docs/pricing、2026-09-22 確認)の
Standard 価格を USD / 1M トークンで保持する。サイズ×画質ごとの出力トークン数は公開されて
いないため、出力トークンは実行履歴(`run.usage`)から推定する(app/domain/pricing.py)。
ここで返す金額はあくまで参考値であり、実際の請求額ではない。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UnitPrices:
    """USD / 1M トークンの単価。"""

    text_input: float
    cached_input: float
    image_input: float
    image_output: float


UNIT_PRICES: dict[str, UnitPrices] = {
    "gpt-image-2.5-sunburst": UnitPrices(
        text_input=5.00, cached_input=1.25, image_input=8.00, image_output=30.00
    ),
    "gpt-image-2.5-flare": UnitPrices(
        text_input=5.00, cached_input=1.25, image_input=8.00, image_output=30.00
    ),
    "gpt-image-2": UnitPrices(
        text_input=5.00, cached_input=1.25, image_input=8.00, image_output=30.00
    ),
}

PRICING_SOURCE_URL = "https://developers.openai.com/api/docs/pricing"
PRICING_CHECKED_AT = "2026-09-22"


def get_unit_prices(model: str) -> UnitPrices | None:
    """単価を返す。未知のモデルは None(見積もり不能)。"""
    return UNIT_PRICES.get(model)
