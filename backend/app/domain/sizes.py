"""画像サイズの検証。

制約(公式ドキュメントとローカルMVP計画で再確認したもの):
- 幅・高さは16の倍数
- 縦横比は 1:3 〜 3:1
- 長辺は 3840px 以下
- 総画素数は 655,360 〜 8,294,400
"""

from __future__ import annotations

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


def validate_size(width: int, height: int) -> None:
    """制約を満たさない場合に InvalidSizeError を送出する。"""
    if width <= 0 or height <= 0:
        raise InvalidSizeError(t("sizes.positiveIntegersRequired"))
    if width % MULTIPLE_OF != 0 or height % MULTIPLE_OF != 0:
        raise InvalidSizeError(t("sizes.multipleOfRequired", multipleOf=MULTIPLE_OF))
    if max(width, height) > MAX_LONG_EDGE:
        raise InvalidSizeError(t("sizes.longEdgeMax", maxLongEdge=MAX_LONG_EDGE))

    total_pixels = width * height
    if not (MIN_TOTAL_PIXELS <= total_pixels <= MAX_TOTAL_PIXELS):
        raise InvalidSizeError(
            t("sizes.totalPixelsRange", min=MIN_TOTAL_PIXELS, max=MAX_TOTAL_PIXELS)
        )

    aspect_ratio = width / height
    if not (MIN_ASPECT_RATIO <= aspect_ratio <= MAX_ASPECT_RATIO):
        raise InvalidSizeError(t("sizes.aspectRatioRange"))


def parse_size(size: str) -> tuple[int, int] | None:
    """`"auto"` または `"WIDTHxHEIGHT"` を解釈する。

    `"auto"` の場合は None を返す(プロバイダー側に解決を任せる)。
    `"WIDTHxHEIGHT"` の場合は検証した上で (width, height) を返す。
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

    validate_size(width, height)
    return width, height
