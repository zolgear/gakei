"""知覚ハッシュのしきい値を決めるための画像の組(ADR-0033 12章)。

Pillow で、構造のある画像(グラデーション、図形、文字、ノイズ)を作る。乱数は種を固定するので、
毎回同じ画像になる。どれも実際の流れと同じく、派生画像の thumb(長辺 512px の WebP)にしてから
ハッシュを作る。

- `degraded_variants`: 劣化させた重複(縮小 50%/25% と JPEG q60/q25、3% までの切り抜き)
- `color_variants`: 同じ構図の色違い(色相の回転、チャンネルの入れ替え)
- `scene(name, seed)` の種違い: 同じテーマの別の画像
"""

from __future__ import annotations

import io
import math
from collections.abc import Callable

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.domain.derivatives import make_thumb

SIZE = (1024, 768)


def _rng(seed: int) -> np.random.Generator:
    return np.random.default_rng(seed)


def _vertical_gradient(top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    w, h = SIZE
    t = np.linspace(0.0, 1.0, h)[:, None, None]
    row = np.array(top, dtype=np.float64) * (1 - t) + np.array(bottom, dtype=np.float64) * t
    return Image.fromarray(np.broadcast_to(row, (h, w, 3)).astype(np.uint8), "RGB")


def _radial_gradient(
    center: tuple[float, float], inner: tuple[int, int, int], outer: tuple[int, int, int]
) -> Image.Image:
    w, h = SIZE
    yy, xx = np.mgrid[0:h, 0:w]
    d = np.hypot(xx - center[0] * w, yy - center[1] * h) / math.hypot(w, h)
    t = np.clip(d * 1.6, 0, 1)[..., None]
    pixels = np.array(inner, dtype=np.float64) * (1 - t) + np.array(outer, dtype=np.float64) * t
    return Image.fromarray(pixels.astype(np.uint8), "RGB")


def _add_noise(image: Image.Image, rng: np.random.Generator, amount: float = 8.0) -> Image.Image:
    pixels = np.asarray(image, dtype=np.float64)
    pixels = pixels + rng.normal(0.0, amount, pixels.shape)
    return Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8), "RGB")


def _color(rng: np.random.Generator, low: int = 30, high: int = 230) -> tuple[int, int, int]:
    r, g, b = rng.integers(low, high, 3)
    return int(r), int(g), int(b)


def _landscape(rng: np.random.Generator) -> Image.Image:
    """夕焼けの空、山の稜線、太陽、湖。"""
    image = _vertical_gradient((40, 90, 190), (250, 150, 70))
    draw = ImageDraw.Draw(image)
    w, h = SIZE
    sun_x, sun_y = rng.uniform(0.15, 0.85) * w, rng.uniform(0.15, 0.45) * h
    r = rng.uniform(40, 90)
    draw.ellipse((sun_x - r, sun_y - r, sun_x + r, sun_y + r), fill=(255, 220, 120))
    for layer, shade in enumerate(((60, 80, 110), (30, 70, 50))):
        base = h * (0.5 + 0.12 * layer)
        xs = np.linspace(0, w, 9)
        ys = base + rng.uniform(-0.18, 0.08, xs.size) * h
        draw.polygon([(0, h), *zip(xs.tolist(), ys.tolist(), strict=True), (w, h)], fill=shade)
    lake_top = h * rng.uniform(0.75, 0.85)
    draw.rectangle((0, lake_top, w, h), fill=(70, 120, 170))
    return _add_noise(image.filter(ImageFilter.GaussianBlur(1.5)), rng)


def _abstract(rng: np.random.Generator) -> Image.Image:
    """放射状のグラデーションに、色のついた楕円と四角を重ねる。"""
    image = _radial_gradient(
        (rng.uniform(0.2, 0.8), rng.uniform(0.2, 0.8)), (240, 200, 90), (120, 30, 110)
    )
    draw = ImageDraw.Draw(image)
    w, h = SIZE
    for _ in range(9):
        x0, y0 = rng.uniform(0, w * 0.8), rng.uniform(0, h * 0.8)
        x1, y1 = x0 + rng.uniform(80, 320), y0 + rng.uniform(80, 320)
        if rng.random() < 0.5:
            draw.ellipse((x0, y0, x1, y1), fill=_color(rng))
        else:
            draw.rectangle((x0, y0, x1, y1), fill=_color(rng))
    return _add_noise(image, rng, 5.0)


def _poster(rng: np.random.Generator) -> Image.Image:
    """色の背景にパネルと見出しの文字(イベントのポスターのような画像)。"""
    image = _vertical_gradient((200, 40, 60), (90, 20, 120))
    draw = ImageDraw.Draw(image)
    w, h = SIZE
    px, py = rng.uniform(0.05, 0.3) * w, rng.uniform(0.05, 0.3) * h
    draw.rectangle((px, py, px + w * 0.55, py + h * 0.5), fill=(250, 240, 210))
    font = ImageFont.load_default(size=int(rng.integers(56, 80)))
    words = ["SUMMER", "FESTIVAL", "LIVE", "MUSIC", "NIGHT", "2026", "OPEN", "ART"]
    for line in range(3):
        word = " ".join(rng.choice(words, 2).tolist())
        draw.text((px + 30, py + 30 + line * 95), word, fill=(30, 30, 90), font=font)
    cx, cy = rng.uniform(0.6, 0.9) * w, rng.uniform(0.55, 0.85) * h
    draw.ellipse((cx - 110, cy - 110, cx + 110, cy + 110), fill=(250, 200, 40))
    return _add_noise(image, rng, 4.0)


def _portrait(rng: np.random.Generator) -> Image.Image:
    """背景と、顔のような図形(肌色の楕円、髪、目)。"""
    image = _vertical_gradient((120, 180, 160), (30, 60, 80))
    draw = ImageDraw.Draw(image)
    w, h = SIZE
    cx, cy = rng.uniform(0.3, 0.7) * w, rng.uniform(0.4, 0.6) * h
    rx, ry = rng.uniform(150, 210), rng.uniform(190, 260)
    draw.ellipse((cx - rx - 30, cy - ry - 40, cx + rx + 30, cy + ry * 0.4), fill=(70, 40, 30))
    draw.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=(235, 190, 160))
    for side in (-1, 1):
        ex = cx + side * rx * 0.4
        draw.ellipse((ex - 22, cy - 40, ex + 22, cy), fill=(40, 60, 120))
    draw.arc((cx - 60, cy + 40, cx + 60, cy + 120), 20, 160, fill=(170, 50, 60), width=8)
    draw.rectangle((cx - rx * 1.4, cy + ry * 0.9, cx + rx * 1.4, h), fill=(200, 60, 70))
    return _add_noise(image.filter(ImageFilter.GaussianBlur(1.0)), rng, 6.0)


def _city(rng: np.random.Generator) -> Image.Image:
    """夜の街並み(暗い空、ビル、明かりのついた窓)。"""
    image = _vertical_gradient((10, 15, 50), (60, 40, 90))
    draw = ImageDraw.Draw(image)
    w, h = SIZE
    x = 0.0
    while x < w:
        bw = rng.uniform(70, 160)
        top = h * rng.uniform(0.2, 0.6)
        draw.rectangle((x, top, x + bw, h), fill=(25, 25, 35))
        for wy in np.arange(top + 15, h - 20, 28):
            for wx in np.arange(x + 10, x + bw - 15, 22):
                if rng.random() < 0.45:
                    draw.rectangle((wx, wy, wx + 10, wy + 14), fill=(250, 210, 110))
        x += bw + rng.uniform(4, 20)
    return _add_noise(image, rng, 3.0)


def _pattern(rng: np.random.Generator) -> Image.Image:
    """斜めの縞と、色の違う丸の並び(テキスタイルの柄のような画像)。"""
    w, h = SIZE
    yy, xx = np.mgrid[0:h, 0:w]
    period = rng.uniform(60, 140)
    angle = rng.uniform(0, math.pi)
    t = (np.sin((xx * math.cos(angle) + yy * math.sin(angle)) / period * 2 * math.pi) + 1) / 2
    a, b = np.array((30, 110, 200), dtype=np.float64), np.array((240, 230, 200), dtype=np.float64)
    pixels = a * t[..., None] + b * (1 - t[..., None])
    image = Image.fromarray(pixels.astype(np.uint8), "RGB")
    draw = ImageDraw.Draw(image)
    for _ in range(14):
        cx, cy, r = rng.uniform(0, w), rng.uniform(0, h), rng.uniform(25, 80)
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(220, 70, 60))
    return _add_noise(image, rng, 5.0)


SCENES: dict[str, Callable[[np.random.Generator], Image.Image]] = {
    "landscape": _landscape,
    "abstract": _abstract,
    "poster": _poster,
    "portrait": _portrait,
    "city": _city,
    "pattern": _pattern,
}


def scene(name: str, seed: int) -> Image.Image:
    return SCENES[name](_rng(seed))


# -- 劣化 ---------------------------------------------------------------------------


def _jpeg(image: Image.Image, quality: int) -> Image.Image:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    buffer.seek(0)
    out = Image.open(buffer)
    out.load()
    return out.convert("RGB")


def _scale(image: Image.Image, factor: float) -> Image.Image:
    w, h = image.size
    return image.resize((max(1, round(w * factor)), max(1, round(h * factor))), Image.LANCZOS)


def _crop(image: Image.Image, fraction: float) -> Image.Image:
    """上下左右から `fraction` ずつ切り落とす(中央の切り抜き)。"""
    w, h = image.size
    dx, dy = round(w * fraction), round(h * fraction)
    return image.crop((dx, dy, w - dx, h - dy))


def _crop_corner(image: Image.Image, fraction: float) -> Image.Image:
    """右と下だけを `fraction` 切り落とす(片寄った切り抜き)。"""
    w, h = image.size
    return image.crop((0, 0, w - round(w * fraction), h - round(h * fraction)))


DEGRADATIONS: dict[str, Callable[[Image.Image], Image.Image]] = {
    "s50": lambda im: _scale(im, 0.5),
    "s25": lambda im: _scale(im, 0.25),
    "q60": lambda im: _jpeg(im, 60),
    "q25": lambda im: _jpeg(im, 25),
    "s50_q60": lambda im: _jpeg(_scale(im, 0.5), 60),
    "s50_q25": lambda im: _jpeg(_scale(im, 0.5), 25),
    "s25_q60": lambda im: _jpeg(_scale(im, 0.25), 60),
    "s25_q25": lambda im: _jpeg(_scale(im, 0.25), 25),
    "crop1": lambda im: _crop(im, 0.01),
    "crop3": lambda im: _crop(im, 0.03),
    "crop3_corner": lambda im: _crop_corner(im, 0.03),
    "crop3_s25_q25": lambda im: _jpeg(_scale(_crop(im, 0.03), 0.25), 25),
    "crop3c_s50_q60": lambda im: _jpeg(_scale(_crop_corner(im, 0.03), 0.5), 60),
}


# -- 色違い -------------------------------------------------------------------------


def _hue_rotate(image: Image.Image, degrees: float) -> Image.Image:
    hsv = np.asarray(image.convert("HSV"), dtype=np.int32).copy()
    hsv[..., 0] = (hsv[..., 0] + round(degrees / 360 * 256)) % 256
    return Image.fromarray(hsv.astype(np.uint8), "HSV").convert("RGB")


def _swap(image: Image.Image, order: tuple[int, int, int]) -> Image.Image:
    pixels = np.asarray(image)
    return Image.fromarray(np.ascontiguousarray(pixels[..., list(order)]), "RGB")


COLOR_VARIANTS: dict[str, Callable[[Image.Image], Image.Image]] = {
    "hue60": lambda im: _hue_rotate(im, 60),
    "hue120": lambda im: _hue_rotate(im, 120),
    "hue180": lambda im: _hue_rotate(im, 180),
    "hue240": lambda im: _hue_rotate(im, 240),
    "bgr": lambda im: _swap(im, (2, 1, 0)),
    "grb": lambda im: _swap(im, (1, 0, 2)),
    "brg": lambda im: _swap(im, (2, 0, 1)),
}


def thumb(image: Image.Image) -> Image.Image:
    """実際の流れと同じ thumb(長辺 512px の WebP)にして読み直す。"""
    out = Image.open(io.BytesIO(make_thumb(image)))
    out.load()
    return out
