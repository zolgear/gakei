"""派生画像(サムネイル・プレビュー)の生成。ADR-0004: 長辺512px / 長辺2048px の WebP。

元画像がすでに目標サイズ以下の場合は拡大しない。透過(RGBA)は保持する。
"""

from __future__ import annotations

import io

from PIL import Image

THUMB_LONG_EDGE = 512
PREVIEW_LONG_EDGE = 2048
WEBP_QUALITY = 90


def _resize_long_edge(image: Image.Image, long_edge: int) -> Image.Image:
    width, height = image.size
    current_long_edge = max(width, height)
    if current_long_edge <= long_edge:
        return image.copy()

    scale = long_edge / current_long_edge
    new_size = (max(1, round(width * scale)), max(1, round(height * scale)))
    return image.resize(new_size, Image.LANCZOS)


def _to_webp_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="WEBP", quality=WEBP_QUALITY)
    return buffer.getvalue()


def make_thumb(image: Image.Image) -> bytes:
    """長辺512pxのWebPを作る。"""
    return _to_webp_bytes(_resize_long_edge(image, THUMB_LONG_EDGE))


def make_preview(image: Image.Image) -> bytes:
    """長辺2048pxのWebPを作る。"""
    return _to_webp_bytes(_resize_long_edge(image, PREVIEW_LONG_EDGE))
