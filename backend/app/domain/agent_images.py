"""エージェントが見るための画像(MCP の `get_image` とサムネイル。ADR-0023 8章 1・2)。

MCP の応答の本文(`ImageContent`)に載せる画像を作る。

- 長辺は `LARGE_LONG_EDGE`(1568px。Claude が画像を細かく見られる上限)か
  `SMALL_LONG_EDGE`(512px)。元の画像がそれより小さければ拡大しない。
- 透過のある画像(alpha < 255 の画素がある)は PNG、それ以外は JPEG。WebP は扱えない
  クライアントがあるので使わない。
- base64 にした大きさが `MAX_BASE64_BYTES` を超えないよう、JPEG は品質を段階的に下げ、
  PNG は長辺を段階的に縮める(Claude Desktop は大きすぎるツールの結果を受け取らないため)。

元にする画像は、原本(4K まである)をそのままデコードすると遅いので、派生画像を使えるときは
使う。512px は派生のサムネイル(512px WebP、品質 90)、1568px はプレビュー(2048px WebP、
品質 90)を縮める。元の画像が目標以下のとき(縮めないとき)だけ原本を使い、WebP を経由した
劣化を避ける。派生が無ければ原本を使う。

同じ内容(sha256)・同じ大きさの結果は変わらないので、小さな LRU キャッシュに覚えておく
(サムネイルは検索結果などで何枚も作るため)。
"""

from __future__ import annotations

import base64
import io
import math
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Literal

from PIL import Image

from app.domain.derivatives import PREVIEW_LONG_EDGE, THUMB_LONG_EDGE
from app.domain.models import Asset
from app.domain.storage import AssetStore, Variant

ImageSize = Literal["large", "small"]

LARGE_LONG_EDGE = 1568
SMALL_LONG_EDGE = THUMB_LONG_EDGE  # 512

LONG_EDGES: dict[ImageSize, int] = {"large": LARGE_LONG_EDGE, "small": SMALL_LONG_EDGE}

# base64 にした画像の大きさの上限(バイト)。Claude Desktop がツールの結果を受け取れる大きさ
# (約 1MB)から、同じ応答に入るテキスト(メタデータの JSON)と JSON-RPC の包みの分を引く。
MAX_BASE64_BYTES = 900 * 1024

# JPEG の品質を下げる段階。最後の段階でも収まらなければ、長辺を縮めてもう一度試す。
JPEG_QUALITIES = (85, 75, 65, 55, 45, 35)
# 1回に縮める割合の上限(推定より大きく縮めることはあっても、これ以下には刻まない)。
_SHRINK_STEP = 0.85
# これより小さくはしない(ここまで縮めれば、どんな画像も上限に収まる)。
_MIN_LONG_EDGE = 64


@dataclass(frozen=True)
class AgentImage:
    data: bytes
    mime_type: Literal["image/jpeg", "image/png"]
    width: int
    height: int

    @property
    def base64(self) -> str:
        return base64.b64encode(self.data).decode("ascii")


def base64_length(n: int) -> int:
    return 4 * math.ceil(n / 3)


def has_transparency(image: Image.Image) -> bool:
    """alpha < 255 の画素があるか(アルファチャンネルや tRNS の透過色が付いていても、
    全画素が不透明なら False)。"""
    if image.mode not in ("RGBA", "LA", "PA", "RGBa", "La") and "transparency" not in image.info:
        return False
    alpha = image.convert("RGBA").getchannel("A")
    low, _ = alpha.getextrema()
    return low < 255


def _resize_long_edge(image: Image.Image, long_edge: int) -> Image.Image:
    width, height = image.size
    current = max(width, height)
    if current <= long_edge:
        return image
    scale = long_edge / current
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    return image.resize(size, Image.LANCZOS)


def _encode_jpeg(image: Image.Image, quality: int) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality, optimize=True)
    return buffer.getvalue()


def _encode_png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _fit_jpeg(image: Image.Image, limit: int) -> AgentImage:
    rgb = image if image.mode in ("RGB", "L") else image.convert("RGB")
    current = rgb
    while True:
        data = b""
        for quality in JPEG_QUALITIES:
            data = _encode_jpeg(current, quality)
            if base64_length(len(data)) <= limit:
                return AgentImage(data, "image/jpeg", current.width, current.height)
        long_edge = max(current.size)
        if long_edge <= _MIN_LONG_EDGE:
            return AgentImage(data, "image/jpeg", current.width, current.height)
        current = _resize_long_edge(rgb, max(_MIN_LONG_EDGE, int(long_edge * _SHRINK_STEP)))


def _fit_png(image: Image.Image, limit: int) -> AgentImage:
    rgba = image if image.mode == "RGBA" else image.convert("RGBA")
    current = rgba
    while True:
        data = _encode_png(current)
        size = base64_length(len(data))
        long_edge = max(current.size)
        if size <= limit or long_edge <= _MIN_LONG_EDGE:
            return AgentImage(data, "image/png", current.width, current.height)
        # 大きさは画素数にほぼ比例するので、面積の比から次の長辺を見積もる(少し余裕を見る)。
        estimated = long_edge * math.sqrt(limit / size) * 0.95
        next_edge = int(min(estimated, long_edge * _SHRINK_STEP))
        current = _resize_long_edge(rgba, max(_MIN_LONG_EDGE, next_edge))


def encode_for_agent(
    image: Image.Image,
    long_edge: int,
    *,
    transparent: bool | None = None,
    limit: int | None = None,
) -> AgentImage:
    """画像を長辺 `long_edge` 以下に縮め(拡大はしない)、base64 にした大きさが `limit`
    (省くと `MAX_BASE64_BYTES`)以下の JPEG / PNG にする。

    `transparent` を省くと、この画像自身の画素から透過の有無を判定する。
    """
    if limit is None:
        limit = MAX_BASE64_BYTES
    image.load()
    if transparent is None:
        transparent = has_transparency(image)
    resized = _resize_long_edge(image, long_edge)
    if transparent:
        return _fit_png(resized, limit)
    return _fit_jpeg(resized, limit)


def _source_variant(store: AssetStore, asset: Asset, long_edge: int) -> Variant | None:
    """元にする画像(ADR-0004 の派生画像を使えるときは使う)。原本も無ければ None。"""
    original_long = max(asset.width or 0, asset.height or 0)
    # 縮めないときは、劣化のない原本を使う。
    if not (original_long and original_long <= long_edge):
        if long_edge <= THUMB_LONG_EDGE and store.content_exists(
            asset.blob_key, asset.sha256, "thumb"
        ):
            return "thumb"
        if long_edge <= PREVIEW_LONG_EDGE and store.content_exists(
            asset.blob_key, asset.sha256, "preview"
        ):
            return "preview"
    if store.content_exists(asset.blob_key, asset.sha256, "original"):
        return "original"
    return None


def _render(
    store: AssetStore, asset: Asset, variant: Variant, long_edge: int, transparent: bool | None
) -> AgentImage:
    content = store.open_content(asset.blob_key, asset.sha256, variant)
    if content is None:
        raise FileNotFoundError(f"{asset.blob_key} ({variant})")
    with Image.open(io.BytesIO(content.read_all())) as image:
        if image.format == "JPEG":
            # JPEG はデコードの段階で縮められる(4K の原本でも速い)。
            image.draft("RGB", (long_edge, long_edge))
        return encode_for_agent(image, long_edge, transparent=transparent)


# キャッシュのキーは (sha256, variant, 長辺, 透過)。同じ内容・同じ元画像なら結果は変わらない。
# `store` と `asset` はキーに含めず、見つからなかったとき(読めなかったとき)だけ読みに行く。
_small_cache: OrderedDict[tuple, AgentImage] = OrderedDict()
_large_cache: OrderedDict[tuple, AgentImage] = OrderedDict()
_SMALL_CACHE_SIZE = 256
_LARGE_CACHE_SIZE = 8
_cache_lock = threading.Lock()


def _cached_render(
    cache: OrderedDict[tuple, AgentImage],
    max_size: int,
    store: AssetStore,
    asset: Asset,
    variant: Variant,
    long_edge: int,
    transparent: bool | None,
) -> AgentImage:
    key = (asset.sha256, variant, long_edge, transparent)
    with _cache_lock:
        if key in cache:
            cache.move_to_end(key)
            return cache[key]
    image = _render(store, asset, variant, long_edge, transparent)
    with _cache_lock:
        cache[key] = image
        cache.move_to_end(key)
        while len(cache) > max_size:
            cache.popitem(last=False)
    return image


def clear_cache() -> None:
    with _cache_lock:
        _small_cache.clear()
        _large_cache.clear()


def render_asset(
    store: AssetStore,
    asset: Asset,
    size: ImageSize,
    *,
    transparent: bool | None = None,
) -> AgentImage | None:
    """Asset をエージェントに見せる画像にする。ファイルが無ければ None。

    `transparent` は原本から求めた透過の有無(分かっていれば)。省くと元にした画像の画素から
    判定する。
    """
    long_edge = LONG_EDGES[size]
    variant = _source_variant(store, asset, long_edge)
    if variant is None:
        return None
    if long_edge <= SMALL_LONG_EDGE:
        return _cached_render(
            _small_cache, _SMALL_CACHE_SIZE, store, asset, variant, long_edge, transparent
        )
    return _cached_render(
        _large_cache, _LARGE_CACHE_SIZE, store, asset, variant, long_edge, transparent
    )
