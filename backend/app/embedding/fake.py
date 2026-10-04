"""`FAKE_PROVIDER=1` とテスト用のダミーのエンジン(ADR-0033 2章)。ダウンロードも推論も
しない。利用者向けの一覧には出さない。

- 画像: 白で合成して 8×8 に縮めた画素(0〜1 から 0.5 を引いたもの)を、固定の乱数の行列で
  512 次元に写す。似た画像は似たベクトルになる(重複の検出や似た画像の確認に使える)。
- 文章: 文字列の sha256 を種にした乱数のベクトル(同じ文字列なら同じ)。
"""

from __future__ import annotations

import hashlib

import numpy as np
from PIL import Image

from app.embedding.base import composite_on_white, l2_normalize

FAKE_DIM = 512
_GRID = 8
_PROJECTION = (
    np.random.default_rng(20261003)
    .standard_normal((_GRID * _GRID * 3, FAKE_DIM))
    .astype(np.float32)
)


class FakeEmbeddingEngine:
    def __init__(self, model_key: str, image_batch_size: int = 8) -> None:
        self._model_key = model_key
        self._image_batch_size = image_batch_size
        self.image_calls: list[int] = []
        self.text_calls: list[int] = []
        self.priority_image_calls = 0

    @property
    def model_key(self) -> str:
        return self._model_key

    @property
    def dim(self) -> int:
        return FAKE_DIM

    @property
    def image_batch_size(self) -> int:
        return self._image_batch_size

    def embed_images(self, images: list[Image.Image], *, priority: bool = False) -> np.ndarray:
        self.image_calls.append(len(images))
        if priority:
            self.priority_image_calls += 1
        if not images:
            return np.zeros((0, FAKE_DIM), dtype=np.float32)
        rows = []
        for image in images:
            small = composite_on_white(image).resize((_GRID, _GRID), Image.Resampling.BILINEAR)
            pixels = np.asarray(small, dtype=np.float32).reshape(-1) / 255.0 - 0.5
            rows.append(pixels @ _PROJECTION)
        return l2_normalize(np.stack(rows))

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        self.text_calls.append(len(texts))
        if not texts:
            return np.zeros((0, FAKE_DIM), dtype=np.float32)
        rows = []
        for text in texts:
            seed = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "little")
            rows.append(np.random.default_rng(seed).standard_normal(FAKE_DIM))
        return l2_normalize(np.stack(rows))

    def release_idle(self) -> None:
        return None
