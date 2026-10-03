"""知覚ハッシュ(ADR-0033 12章)。重複の候補を、CLIP の類似度と併せて判定するのに使う。

画像(thumb)ごとに、モデルに依らない小さな値を持つ。どれも、透明な部分を白で合成してから
作る(埋め込みの前処理と同じ)。

- **差分ハッシュ(dHash、64 ビット、8 バイト):** 構図を見る。グレースケールにして 9×8 に
  縮め(LANCZOS)、各行で隣り合う画素の明るさを比べる(左が右より明るければ 1)。縮小・
  再圧縮・わずかな切り抜きではあまり変わらず、色を変えても明るさの並びが同じなら変わらない。
  距離はハミング距離(0〜64)。
- **色(64 バイト):** 色を見る。2つの部分から成る。
  - 4×4 に面積平均で縮めた CIELAB(D65)。48 バイト(マスは左上から行の順に L*, a*, b*。
    L* は 0〜100 を 0〜255 に、a*・b* は -128〜127 を 0〜255 に丸める。丸めの誤差は ΔE で
    1 未満)。距離はマスごとの ΔE76 の平均。どこが何色かを見る(領域の色の入れ替え)。
  - 彩度で重みを付けた色相のヒストグラム(8 ビン、16 バイト。各ビンを 100 倍した
    uint16 リトルエンディアン)。64×64 に縮めた各画素の a*b* から色相と彩度を求め、隣り合う
    2つのビンに線形に振り分けて、画素数で割る(彩度の平均の内訳)。距離はビンごとの差の
    絶対値の和。位置を見ないので切り抜きや縮小にほとんど動かず、小さな面積の色だけが
    変わった色違い(暗い夜景の窓の色だけが違う、など)を拾う。4×4 のマスでは、そうした
    違いが周りの色に薄まってしまい、劣化させた重複との差が付かなかった。
  - Lab にするのは、RGB の差ではなく人の目に近い差で比べるため。

計算は決定的(Pillow の縮小と numpy だけで、乱数を使わない)。アルゴリズムを変えたら
`ALGORITHM_VERSION` を上げる。版の違う行は「ハッシュが無い」と同じに扱い、worker が作り直す。

しきい値は内部の定数で、画面には出さない。値は `tests/test_perceptual_hash.py` の画像の組
(`tests/perceptual_images.py`。縮小と JPEG の劣化、わずかな切り抜き、色違い、同じテーマの
別の画像、docs/images のスクリーンショット)で測って決めた。測った値は定数の横に書く。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image

from app.embedding.base import composite_on_white

ALGORITHM_VERSION = 1

# 重複とみなす距離の上限(すべて以下なら「近い」)。測った値は tests/test_perceptual_hash.py。
# - dHash: 劣化させた重複は最大 14、同じテーマの別の画像は最小 20。
DHASH_MAX_DISTANCE = 17
# - 4×4 の Lab: 劣化させた重複は最大 6.1。色の入れ替えを外すための緩い上限。
COLOR_MAX_DISTANCE = 9.0
# - 色相のヒストグラム: 劣化させた重複は最大 3.7、色違いは最小 4.8。
HUE_MAX_DISTANCE = 4.2

DHASH_BYTES = 8
_GRID = 4
_GRID_BYTES = _GRID * _GRID * 3
_HUE_BINS = 8
_HUE_SIZE = 64
_HUE_SCALE = 100.0
COLOR_BYTES = _GRID_BYTES + _HUE_BINS * 2

# sRGB(D65)→ XYZ
_RGB_TO_XYZ = np.array(
    [
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041],
    ],
    dtype=np.float64,
)
_WHITE_D65 = np.array([0.95047, 1.0, 1.08883], dtype=np.float64)


@dataclass(frozen=True)
class PerceptualHash:
    dhash: bytes
    color: bytes
    version: int = ALGORITHM_VERSION


def dhash_bytes(image: Image.Image) -> bytes:
    """差分ハッシュ(64 ビット。上位ビットが左上)を 8 バイトのビッグエンディアンで返す。"""
    gray = composite_on_white(image).convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    pixels = np.asarray(gray, dtype=np.int16)
    bits = (pixels[:, :-1] > pixels[:, 1:]).reshape(-1)
    return np.packbits(bits.astype(np.uint8)).tobytes()


def _srgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """[..., 3] の sRGB(0〜1)を CIELAB(D65)にする。"""
    linear = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    xyz = linear @ _RGB_TO_XYZ.T / _WHITE_D65
    delta = 6 / 29
    f = np.where(xyz > delta**3, np.cbrt(xyz), xyz / (3 * delta**2) + 4 / 29)
    lightness = 116 * f[..., 1] - 16
    a = 500 * (f[..., 0] - f[..., 1])
    b = 200 * (f[..., 1] - f[..., 2])
    return np.stack([lightness, a, b], axis=-1)


def _lab_of(rgb: Image.Image, size: int) -> np.ndarray:
    small = rgb.resize((size, size), Image.Resampling.BOX)
    return _srgb_to_lab(np.asarray(small, dtype=np.float64) / 255.0).reshape(-1, 3)


def _grid_bytes(rgb: Image.Image) -> bytes:
    lab = _lab_of(rgb, _GRID)
    quantized = np.empty_like(lab)
    quantized[:, 0] = np.clip(np.rint(lab[:, 0] * 2.55), 0, 255)
    quantized[:, 1:] = np.clip(np.rint(lab[:, 1:]), -128, 127) + 128
    return quantized.astype(np.uint8).tobytes()


def _hue_histogram(rgb: Image.Image) -> np.ndarray:
    lab = _lab_of(rgb, _HUE_SIZE)
    a, b = lab[:, 1], lab[:, 2]
    chroma = np.hypot(a, b)
    position = (np.arctan2(b, a) % (2 * np.pi)) / (2 * np.pi) * _HUE_BINS - 0.5
    low = np.floor(position).astype(np.int64)
    upper = position - low
    histogram = np.zeros(_HUE_BINS, dtype=np.float64)
    np.add.at(histogram, low % _HUE_BINS, chroma * (1 - upper))
    np.add.at(histogram, (low + 1) % _HUE_BINS, chroma * upper)
    return histogram / len(lab)


def color_bytes(image: Image.Image) -> bytes:
    """色の値(4×4 の Lab 48 バイト + 色相のヒストグラム 16 バイト)。"""
    rgb = composite_on_white(image)
    histogram = np.clip(np.rint(_hue_histogram(rgb) * _HUE_SCALE), 0, 65535).astype("<u2")
    return _grid_bytes(rgb) + histogram.tobytes()


def compute(image: Image.Image) -> PerceptualHash:
    """画像(thumb)から知覚ハッシュを作る。"""
    return PerceptualHash(dhash=dhash_bytes(image), color=color_bytes(image))


def dhash_distance(a: bytes, b: bytes) -> int:
    """差分ハッシュのハミング距離(0〜64)。"""
    return (int.from_bytes(a, "big") ^ int.from_bytes(b, "big")).bit_count()


def _decode_grid(data: bytes) -> np.ndarray:
    raw = np.frombuffer(data[:_GRID_BYTES], dtype=np.uint8).astype(np.float64).reshape(-1, 3)
    lab = np.empty_like(raw)
    lab[:, 0] = raw[:, 0] / 2.55
    lab[:, 1:] = raw[:, 1:] - 128
    return lab


def _decode_hue(data: bytes) -> np.ndarray:
    return np.frombuffer(data[_GRID_BYTES:], dtype="<u2").astype(np.float64) / _HUE_SCALE


def color_distance(a: bytes, b: bytes) -> float:
    """4×4 の Lab の距離(マスごとの ΔE76 の平均)。"""
    return float(np.linalg.norm(_decode_grid(a) - _decode_grid(b), axis=1).mean())


def hue_distance(a: bytes, b: bytes) -> float:
    """色相のヒストグラムの距離(ビンごとの差の絶対値の和)。"""
    return float(np.abs(_decode_hue(a) - _decode_hue(b)).sum())


def is_close(a: PerceptualHash, b: PerceptualHash) -> bool:
    """構図も色も近い(重複の候補として残す)。"""
    return (
        dhash_distance(a.dhash, b.dhash) <= DHASH_MAX_DISTANCE
        and color_distance(a.color, b.color) <= COLOR_MAX_DISTANCE
        and hue_distance(a.color, b.color) <= HUE_MAX_DISTANCE
    )


def from_stored(
    dhash: bytes | None, color: bytes | None, version: int | None
) -> PerceptualHash | None:
    """保存された行を読む。今の版でない、形が合わないなら None(ハッシュが無いのと同じ)。"""
    if (
        version != ALGORITHM_VERSION
        or dhash is None
        or len(dhash) != DHASH_BYTES
        or color is None
        or len(color) != COLOR_BYTES
    ):
        return None
    return PerceptualHash(dhash=bytes(dhash), color=bytes(color), version=version)


# -- まとめて比べる(重複の候補) -------------------------------------------------------


@dataclass(frozen=True)
class HashTable:
    """複数の画像の知覚ハッシュを、numpy で一度に比べられる形にしたもの。"""

    present: np.ndarray  # (n,) bool。ハッシュがあるか
    dhash: np.ndarray  # (n,) uint64
    grid: np.ndarray  # (n, 16, 3) float64。Lab
    hue: np.ndarray  # (n, 8) float64


def decode_many(hashes: list[PerceptualHash | None]) -> HashTable:
    n = len(hashes)
    present = np.zeros(n, dtype=bool)
    dhash = np.zeros(n, dtype=np.uint64)
    grid = np.zeros((n, _GRID * _GRID, 3), dtype=np.float64)
    hue = np.zeros((n, _HUE_BINS), dtype=np.float64)
    for x, value in enumerate(hashes):
        if value is None:
            continue
        present[x] = True
        dhash[x] = int.from_bytes(value.dhash, "big")
        grid[x] = _decode_grid(value.color)
        hue[x] = _decode_hue(value.color)
    return HashTable(present=present, dhash=dhash, grid=grid, hue=hue)


_PAIR_CHUNK = 65536


def close_pairs(table: HashTable, i: np.ndarray, j: np.ndarray) -> np.ndarray:
    """組 (i[k], j[k]) ごとに、構図も色も近いか(`is_close` と同じ判定。両方にハッシュが
    あることが前提で、無い組の値は意味を持たない)。"""
    result = np.zeros(len(i), dtype=bool)
    for start in range(0, len(i), _PAIR_CHUNK):
        a = i[start : start + _PAIR_CHUNK]
        b = j[start : start + _PAIR_CHUNK]
        dh = np.bitwise_count(table.dhash[a] ^ table.dhash[b])
        grid = np.linalg.norm(table.grid[a] - table.grid[b], axis=2).mean(axis=1)
        hue = np.abs(table.hue[a] - table.hue[b]).sum(axis=1)
        result[start : start + _PAIR_CHUNK] = (
            (dh <= DHASH_MAX_DISTANCE) & (grid <= COLOR_MAX_DISTANCE) & (hue <= HUE_MAX_DISTANCE)
        )
    return result
