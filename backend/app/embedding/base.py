"""埋め込みのエンジンの共通の口(ADR-0033 2章)。

エンジンは `model_key`、`dim`、`embed_images`、`embed_texts` だけを持つ。返すベクトルは
L2 正規化した float32(行がそれぞれ1つの入力)。モデルごとの違い(前処理、トークナイザー、
API の形)はエンジンの中に閉じ込める。

呼び出しは同期で、CPU を使うもの(ローカルの ONNX)もネットワークを待つもの(リモート)も、
呼び出し側(worker)が `asyncio.to_thread` で別スレッドに逃がす。
"""

from __future__ import annotations

from typing import Protocol

import numpy as np
from PIL import Image


class EmbeddingError(Exception):
    """推論に失敗した(画面とログに出す文言を持つ)。worker はその行を `failed` にする。"""


class EmbeddingEngine(Protocol):
    @property
    def model_key(self) -> str: ...

    @property
    def dim(self) -> int | None:
        """次元。リモートのように最初の結果が出るまで分からなければ None。"""
        ...

    @property
    def image_batch_size(self) -> int:
        """画像を1回にまとめて計算する上限の枚数(1なら1枚ずつ)。"""
        ...

    def embed_images(self, images: list[Image.Image]) -> np.ndarray: ...

    def embed_texts(self, texts: list[str]) -> np.ndarray: ...

    def release_idle(self) -> None:
        """しばらく使っていなければモデルを手放す(worker が定期的に呼ぶ)。"""
        ...


def l2_normalize(vectors: np.ndarray) -> np.ndarray:
    """行ごとに L2 正規化した float32 を返す(長さ 0 の行は 0 のまま)。"""
    array = np.asarray(vectors, dtype=np.float32)
    if array.ndim == 1:
        array = array[np.newaxis, :]
    norms = np.linalg.norm(array, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (array / norms).astype(np.float32)


def composite_on_white(image: Image.Image) -> Image.Image:
    """透明な部分を白で合成して RGB にする(WD Tagger、推定に送る画像と揃える。ADR-0033 2章)。"""
    if image.mode == "RGB":
        return image
    rgba = image.convert("RGBA")
    background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    return Image.alpha_composite(background, rgba).convert("RGB")


def vector_to_blob(vector: np.ndarray) -> bytes:
    """保存する形(float32 リトルエンディアン。ADR-0033 4章)。"""
    return np.asarray(vector, dtype="<f4").reshape(-1).tobytes()


def blob_to_vector(data: bytes) -> np.ndarray:
    return np.frombuffer(data, dtype="<f4").astype(np.float32)
