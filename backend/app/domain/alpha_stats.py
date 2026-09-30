"""画像の透過の情報(ADR-0023 7章 4。MCP の `get_asset` が返す)。

サムネイル(WebP)では透過かどうかが分かりにくいので、原本から数える。

- `has_alpha`: アルファチャンネル(または PNG の tRNS による透過色)を持つか。
- `transparent_ratio`: alpha < 255 のピクセルの割合(0〜1)。アルファが無ければ 0。

縮小せずに全ピクセルを数える(アルファだけを取り出してヒストグラムを取るので C の中で済み、
4K でも数える部分は数十ミリ秒。時間の大半は原本のデコード)。同じ内容の画像は結果が
変わらないので、sha256 をキーに覚えておく。
"""

from __future__ import annotations

import io
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

from PIL import Image


@dataclass(frozen=True)
class AlphaStats:
    has_alpha: bool
    transparent_ratio: float


def _compute(data: bytes) -> AlphaStats:
    with Image.open(io.BytesIO(data)) as image:
        has_alpha = image.mode in ("RGBA", "LA", "PA", "RGBa", "La") or (
            "transparency" in image.info
        )
        if not has_alpha:
            return AlphaStats(has_alpha=False, transparent_ratio=0.0)
        alpha = image.convert("RGBA").getchannel("A")
        histogram = alpha.histogram()
        total = alpha.width * alpha.height
        transparent = total - histogram[255]
        ratio = transparent / total if total else 0.0
        return AlphaStats(has_alpha=True, transparent_ratio=round(ratio, 6))


_CACHE_SIZE = 256
_cache: OrderedDict[str, AlphaStats] = OrderedDict()
_lock = threading.Lock()


def alpha_stats(sha256: str, read: Callable[[], bytes]) -> AlphaStats:
    """原本から透過の情報を求める(内容のハッシュごとに覚えておく)。

    `read` は原本のバイト列を返す関数(`AssetStore.read` など)。覚えていないときだけ呼ぶ。
    """
    with _lock:
        if sha256 in _cache:
            _cache.move_to_end(sha256)
            return _cache[sha256]
    stats = _compute(read())
    with _lock:
        _cache[sha256] = stats
        while len(_cache) > _CACHE_SIZE:
            _cache.popitem(last=False)
    return stats
