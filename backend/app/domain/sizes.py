"""画像サイズの検証。

制約(公式ドキュメントとローカルMVP計画で再確認したもの):
- 幅・高さは16の倍数
- 縦横比は 1:3 〜 3:1
- 長辺は 3840px 以下
- 総画素数は 655,360 〜 8,294,400
"""

from __future__ import annotations

from typing import Protocol

from app.i18n import t

MULTIPLE_OF = 16
MAX_LONG_EDGE = 3840
MIN_TOTAL_PIXELS = 655_360
MAX_TOTAL_PIXELS = 8_294_400
MIN_ASPECT_RATIO = 1 / 3
MAX_ASPECT_RATIO = 3 / 1

# プロンプトの最大長。capabilities とプロンプトセットの両方でこの値を共有する。
PROMPT_MAX_LENGTH = 32_000


class InvalidSizeError(ValueError):
    """サイズが制約を満たさない場合に送出する。"""


class SizeLimits(Protocol):
    """サイズの制約(`app.providers.base.SizeConstraints` と同じ形)。プロバイダーごとに違う
    (ADR-0038 2章: SD WebUI は 8 の倍数、長辺 2048px)。"""

    multiple_of: int
    max_long_edge: int
    min_total_pixels: int
    max_total_pixels: int
    min_aspect_ratio: float
    max_aspect_ratio: float


def validate_size(width: int, height: int, limits: SizeLimits | None = None) -> None:
    """制約を満たさない場合に InvalidSizeError を送出する。`limits` を省くと OpenAI の制約。"""
    multiple_of = limits.multiple_of if limits else MULTIPLE_OF
    max_long_edge = limits.max_long_edge if limits else MAX_LONG_EDGE
    min_total = limits.min_total_pixels if limits else MIN_TOTAL_PIXELS
    max_total = limits.max_total_pixels if limits else MAX_TOTAL_PIXELS
    min_ratio = limits.min_aspect_ratio if limits else MIN_ASPECT_RATIO
    max_ratio = limits.max_aspect_ratio if limits else MAX_ASPECT_RATIO

    if width <= 0 or height <= 0:
        raise InvalidSizeError(t("sizes.positiveIntegersRequired"))
    if width % multiple_of != 0 or height % multiple_of != 0:
        raise InvalidSizeError(t("sizes.multipleOfRequired", multipleOf=multiple_of))
    if max(width, height) > max_long_edge:
        raise InvalidSizeError(t("sizes.longEdgeMax", maxLongEdge=max_long_edge))

    total_pixels = width * height
    if not (min_total <= total_pixels <= max_total):
        raise InvalidSizeError(t("sizes.totalPixelsRange", min=min_total, max=max_total))

    aspect_ratio = width / height
    if not (min_ratio <= aspect_ratio <= max_ratio):
        raise InvalidSizeError(t("sizes.aspectRatioRange"))


def parse_size(size: str, limits: SizeLimits | None = None) -> tuple[int, int] | None:
    """`"auto"` または `"WIDTHxHEIGHT"` を解釈する。

    `"auto"` の場合は None を返す(プロバイダー側に解決を任せる)。
    `"WIDTHxHEIGHT"` の場合は検証した上で (width, height) を返す。`limits` を省くと
    OpenAI の制約で検証する。
    """
    size = size.strip()
    if size == "auto":
        return None

    size_format_error = t("sizes.sizeFormat")
    parts = size.lower().split("x")
    if len(parts) != 2:
        raise InvalidSizeError(size_format_error)
    try:
        width, height = int(parts[0]), int(parts[1])
    except ValueError as e:
        raise InvalidSizeError(size_format_error) from e

    validate_size(width, height, limits)
    return width, height
