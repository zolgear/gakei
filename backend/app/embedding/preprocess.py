"""画像の前処理(ADR-0033 2章)。どのモデルでも、透明な部分を先に白で合成して RGB にする。

- OpenAI CLIP(`CLIPImageProcessor` と同じ): 短辺 224 に bicubic でリサイズし(長辺は
  `int(224 * 長辺 / 短辺)`)、中央の 224×224 を切り出す。0〜1 にして CLIP の mean/std で
  正規化し、CHW にする。
- LY(CLYP の TestTransform と同じ): 長辺 224 に bicubic で縮め(新しい辺は Python の
  `round`)、黒の余白を足して中央に置く(左 `余白 // 2`、上 `余白 // 2`)。ImageNet の
  mean/std で正規化し、CHW にする。
"""

from __future__ import annotations

import numpy as np
from PIL import Image

from app.embedding.base import composite_on_white

SIZE = 224
CLIP_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
CLIP_STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)
IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def _to_chw(array_u8: np.ndarray, mean: np.ndarray, std: np.ndarray) -> np.ndarray:
    x = array_u8.astype(np.float32) / 255.0
    x = (x - mean) / std
    return np.ascontiguousarray(x.transpose(2, 0, 1), dtype=np.float32)


def preprocess_clip(image: Image.Image, size: int = SIZE) -> np.ndarray:
    rgb = composite_on_white(image)
    width, height = rgb.size
    if width <= height:
        new_width, new_height = size, int(size * height / width)
    else:
        new_width, new_height = int(size * width / height), size
    rgb = rgb.resize((new_width, new_height), Image.Resampling.BICUBIC)
    left = (new_width - size) // 2
    top = (new_height - size) // 2
    rgb = rgb.crop((left, top, left + size, top + size))
    return _to_chw(np.asarray(rgb), CLIP_MEAN, CLIP_STD)


def preprocess_ly(image: Image.Image, size: int = SIZE) -> np.ndarray:
    rgb = composite_on_white(image)
    width, height = rgb.size
    scale = size / float(max(width, height))
    if scale != 1.0:
        new_width, new_height = round(width * scale), round(height * scale)
        rgb = rgb.resize((new_width, new_height), Image.Resampling.BICUBIC)
    else:
        new_width, new_height = width, height
    canvas = Image.new("RGB", (size, size), (0, 0, 0))
    canvas.paste(rgb, ((size - new_width) // 2, (size - new_height) // 2))
    return _to_chw(np.asarray(canvas), IMAGENET_MEAN, IMAGENET_STD)
