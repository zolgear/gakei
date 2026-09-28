"""画像の透過の情報(ADR-0023 7章 4。MCP の `get_asset` が返す)。

サムネイル(WebP)では透過かどうかが分かりにくいので、原本から数える。

- `has_alpha`: アルファチャンネル(または PNG の tRNS による透過色)を持つか。
- `transparent_ratio`: alpha < 255 のピクセルの割合(0〜1)。アルファが無ければ 0。

縮小せずに全ピクセルを数える(アルファだけを取り出してヒストグラムを取るので C の中で済み、
4K でも数える部分は数十ミリ秒。時間の大半は原本のデコード)。同じ内容の画像は結果が
変わらないので、sha256 をキーに覚えておく。
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image


@dataclass(frozen=True)
class AlphaStats:
    has_alpha: bool
    transparent_ratio: float


def _compute(path: Path) -> AlphaStats:
    with Image.open(path) as image:
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


@lru_cache(maxsize=256)
def _cached(sha256: str, path_str: str) -> AlphaStats:
    return _compute(Path(path_str))


def alpha_stats(sha256: str, path: Path) -> AlphaStats:
    """原本のファイルから透過の情報を求める(内容のハッシュごとに覚えておく)。"""
    return _cached(sha256, str(path))
