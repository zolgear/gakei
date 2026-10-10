"""画像の前処理(ADR-0033 2章)。どのモデルでも、透明な部分を先に白で合成して RGB にする。

- OpenAI CLIP(`CLIPImageProcessor` と同じ): 短辺 224 に bicubic でリサイズし(長辺は
  `int(224 * 長辺 / 短辺)`)、中央の 224×224 を切り出す。0〜1 にして CLIP の mean/std で
  正規化し、CHW にする。
- LY(CLYP の TestTransform と同じ): 長辺 224 に bicubic で縮め(新しい辺は Python の
  `round`)、黒の余白を足して中央に置く(左 `余白 // 2`、上 `余白 // 2`)。ImageNet の
  mean/std で正規化し、CHW にする。
- EmbeddingGemma 2(`preprocess_eg2`。ADR-0044 3章。transformers の Gemma4ImageProcessor を
  numpy と Pillow で書き直したもの): 縦横比を保って、パッチ(16px)の数が 2,520 以下で、辺が
  48(16 × プーリングの 3)の倍数になる大きさに bicubic でリサイズする(transformers は
  torchvision で、画素値の差は 1/255 × 2 段まで)。0〜1 にするだけで mean/std の正規化は
  しない。16×16 のパッチに分けて行優先に並べ、位置 ID は (列, 行)。2,520 まで 0 で詰め、
  詰めた分の位置 ID は -1。ソフトトークン数はパッチ数 / 9。
"""

from __future__ import annotations

import math

import numpy as np
from PIL import Image

from app.embedding.base import composite_on_white

EG2_PATCH_SIZE = 16
EG2_POOLING_KERNEL_SIZE = 3
EG2_MAX_SOFT_TOKENS = 280
EG2_MAX_PATCHES = EG2_MAX_SOFT_TOKENS * EG2_POOLING_KERNEL_SIZE**2  # 2520
EG2_PATCH_DIM = EG2_PATCH_SIZE * EG2_PATCH_SIZE * 3  # 768

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


def eg2_target_size(height: int, width: int) -> tuple[int, int]:
    """リサイズ後の (高さ, 幅)。image_processing_gemma4.py の
    `get_aspect_ratio_preserving_size` と同じ計算。"""
    patch = EG2_PATCH_SIZE
    kernel = EG2_POOLING_KERNEL_SIZE
    target_px = EG2_MAX_PATCHES * patch**2
    factor = math.sqrt(target_px / (height * width))
    ideal_height = factor * height
    ideal_width = factor * width
    side_mult = kernel * patch

    target_height = int(math.floor(ideal_height / side_mult)) * side_mult
    target_width = int(math.floor(ideal_width / side_mult)) * side_mult
    if target_height == 0 and target_width == 0:
        raise ValueError("画像を 0 x 0 に縮めることになる")

    max_side_length = (EG2_MAX_PATCHES // kernel**2) * side_mult
    if target_height == 0:
        target_height = side_mult
        target_width = min(int(math.floor(width / height)) * side_mult, max_side_length)
    elif target_width == 0:
        target_width = side_mult
        target_height = min(int(math.floor(height / width)) * side_mult, max_side_length)

    if target_height * target_width > target_px:
        raise ValueError("パッチ数が上限を超える")
    return target_height, target_width


def preprocess_eg2(image: Image.Image) -> tuple[np.ndarray, np.ndarray, int]:
    """1枚 → (pixel_values [1, 2520, 768] float32、position_ids [1, 2520, 2] int64、
    ソフトトークン数)。"""
    rgb = composite_on_white(image)
    width, height = rgb.size
    target_height, target_width = eg2_target_size(height, width)
    if (target_height, target_width) != (height, width):
        rgb = rgb.resize((target_width, target_height), Image.Resampling.BICUBIC)

    patch = EG2_PATCH_SIZE
    array = np.asarray(rgb, dtype=np.uint8)
    patch_h = target_height // patch
    patch_w = target_width // patch
    patches = (
        array.reshape(patch_h, patch, patch_w, patch, 3)
        .transpose(0, 2, 1, 3, 4)
        .reshape(patch_h * patch_w, EG2_PATCH_DIM)
        .astype(np.float32)
        / 255.0
    )
    num_patches = patches.shape[0]
    num_soft_tokens = num_patches // EG2_POOLING_KERNEL_SIZE**2

    # 位置 ID は (x, y) = (列, 行)、行優先。
    xs, ys = np.meshgrid(np.arange(patch_w), np.arange(patch_h), indexing="xy")
    positions = np.stack([xs, ys], axis=-1).reshape(num_patches, 2).astype(np.int64)

    pixel_values = np.zeros((EG2_MAX_PATCHES, EG2_PATCH_DIM), dtype=np.float32)
    pixel_values[:num_patches] = patches
    position_ids = np.full((EG2_MAX_PATCHES, 2), -1, dtype=np.int64)
    position_ids[:num_patches] = positions
    return pixel_values[np.newaxis], position_ids[np.newaxis], num_soft_tokens
