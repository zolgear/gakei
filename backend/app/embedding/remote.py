"""リモートの推論サーバー(Infinity 形式)のエンジン(ADR-0033 2章)。

- 画像は `POST {Base URL}/embeddings` に `{"model": ..., "modality": "image", "input": [data URI]}`
  で送る。テキストは `modality` を付けずに送る(OpenAI の embeddings と同じ形)。
- 送る画像は thumb(512px)を JPEG にしたもの(透明な部分は白で合成する)。原本は送らない。
- キーがあれば `Authorization: Bearer` で送る。キーの値はエラーの文言にも出さない。
- 1回に送る件数の上限と時間切れだけを持つ(回数の上限は持たない。ADR-0033 5章)。
- 次元は最初の結果が出るまで分からない(`dim` は None)。
"""

from __future__ import annotations

import base64
import io
from collections.abc import Callable
from typing import Any

import httpx
import numpy as np
from PIL import Image

from app.embedding.base import EmbeddingError, composite_on_white, l2_normalize
from app.i18n import t

DEFAULT_TIMEOUT_SECONDS = 60.0
IMAGE_BATCH_SIZE = 8
TEXT_BATCH_SIZE = 32
_JPEG_MAX_SIDE = 512
_ERROR_BODY_MAX = 200


def remote_model_key(connection_id: str, model: str) -> str:
    """ベクトル空間の識別子(ADR-0033 3章)。接続先の Base URL を変えても同じ id なら同じ空間。"""
    return f"remote:{connection_id}:{model}"


def image_to_data_uri(image: Image.Image) -> str:
    copy = composite_on_white(image)
    if max(copy.size) > _JPEG_MAX_SIDE:
        copy = copy.copy()
        copy.thumbnail((_JPEG_MAX_SIDE, _JPEG_MAX_SIDE), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    copy.save(buffer, format="JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


class InfinityEngine:
    def __init__(
        self,
        *,
        connection_id: str,
        base_url: str,
        model: str,
        api_key: str | None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        client_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        self.connection_id = connection_id
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._api_key = api_key
        self.timeout_seconds = timeout_seconds
        # テストでは `httpx.MockTransport` の Client を渡す。
        self._client_factory = client_factory or (
            lambda: httpx.Client(timeout=httpx.Timeout(timeout_seconds, connect=15.0))
        )
        self._dim: int | None = None

    @property
    def model_key(self) -> str:
        return remote_model_key(self.connection_id, self.model)

    @property
    def dim(self) -> int | None:
        return self._dim

    @property
    def image_batch_size(self) -> int:
        return IMAGE_BATCH_SIZE

    def embed_images(self, images: list[Image.Image], *, priority: bool = False) -> np.ndarray:
        # 推論サーバーは別のマシンなので、ModelResidency の調停は要らない(`priority` は使わない)。
        inputs = [image_to_data_uri(image) for image in images]
        return self._embed(inputs, IMAGE_BATCH_SIZE, modality="image")

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        return self._embed(list(texts), TEXT_BATCH_SIZE, modality=None)

    def release_idle(self) -> None:
        return None

    def _embed(self, inputs: list[str], batch: int, modality: str | None) -> np.ndarray:
        if not inputs:
            return np.zeros((0, self._dim or 0), dtype=np.float32)
        rows: list[np.ndarray] = []
        with self._client_factory() as client:
            for start in range(0, len(inputs), batch):
                chunk = inputs[start : start + batch]
                rows.append(self._post(client, chunk, modality))
        vectors = l2_normalize(np.concatenate(rows, axis=0))
        self._dim = int(vectors.shape[1])
        return vectors

    def _post(self, client: httpx.Client, chunk: list[str], modality: str | None) -> np.ndarray:
        payload: dict[str, Any] = {"model": self.model, "input": chunk}
        if modality is not None:
            payload["modality"] = modality
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        try:
            response = client.post(f"{self.base_url}/embeddings", json=payload, headers=headers)
        except httpx.HTTPError as e:
            raise EmbeddingError(t("embeddings.remoteUnavailable", error=type(e).__name__)) from e
        if response.status_code != 200:
            body = response.text.strip().replace("\n", " ")[:_ERROR_BODY_MAX]
            raise EmbeddingError(
                t("embeddings.remoteHttpError", status=response.status_code, body=body)
            )
        try:
            data = response.json()["data"]
            ordered = sorted(data, key=lambda item: int(item.get("index", 0)))
            matrix = np.array([item["embedding"] for item in ordered], dtype=np.float32)
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            raise EmbeddingError(t("embeddings.remoteInvalidResponse")) from e
        if matrix.ndim != 2 or matrix.shape[0] != len(chunk) or matrix.shape[1] == 0:
            raise EmbeddingError(t("embeddings.remoteInvalidResponse"))
        if self._dim is not None and matrix.shape[1] != self._dim:
            raise EmbeddingError(t("embeddings.remoteInvalidResponse"))
        return matrix
