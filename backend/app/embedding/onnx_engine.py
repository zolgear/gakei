"""ローカルの ONNX で動かす CLIP 系のエンジン(ADR-0033 2章・11章)。

入出力の名前(2026-10-03 に確かめた値):

| モデル | 入力 | 出力 |
|---|---|---|
| Xenova の画像側 | `pixel_values` [batch, 3, 224, 224] | `image_embeds` [batch, 512] |
| Xenova の文章側 | `input_ids` [batch, 長さ] | `text_embeds` [batch, 512] |
| LY の画像側 | `input` [batch, 3, 224, 224] | `output` [batch, 512] |
| LY の文章側 | `input0`、`input1`、`input2` | `output` [batch, 512] |

出力は射影済みだが L2 正規化されていないので、ここで正規化する。

EmbeddingGemma 2(ADR-0044 3章。2026-10-11 に確かめた値):

- 画像側 `vision_encoder_quantized.onnx`
  - 入力: `pixel_values` [1, 2520, 768]、`pixel_position_ids` [1, 2520, 2]
  - 出力: `image_features` [N, 512](N はソフトトークン数。詰めた分は返らない)
- 文章側 `model_quantized.onnx`
  - 入力: `input_ids`・`attention_mask` [1, 長さ]、`image_features`・`video_features`・
    `audio_features` [n, 512]
  - 出力: `sentence_embedding` [1, 768]

- 画像は2段で計算する: 画像側の出力(ソフトトークン N 個の特徴)を、画像のトークン列と一緒に
  文章側へ渡す。画像を計算するときは両方を読み込み、文章での検索だけなら文章側だけを読み込む。
- 使わない入力(文章のときは画像・動画・音声、画像のときは動画・音声)も必須なので、[0, 512] の
  空の配列を渡す。
- `sentence_embedding` はプーリング・射影・L2 正規化まで済んでいるので、ここでは何もしない。
- 画像も文章も1件ずつ計算する(q8 は動的量子化で、まとめる件数で値が変わる)。検索の文章には
  `SearchQuery` のプレフィックスを付け、1,024 トークンで切る。

- 画像側と文章側は別々のセッションにし、使うときに初めて読み込む。文章での検索だけなら
  文章側だけを読み込む。
- セッションの作り方は WD Tagger と同じ(`app/model_store/onnx_runtime.py`)。読み込む前に
  空きメモリをモデルの目安と比べる(もう片方を読み込み済みなら、両方の目安との差を比べる)。
- WD Tagger と同時にメモリに載せない(`app/model_store/residency.py`)。
- 量子化した画像側(`clip-vit-b32-u8`)と LY の画像側は1枚ずつ計算する(`image_batch_size`)。
- しばらく(`IDLE_RELEASE_SECONDS`)使わなければ両方のセッションを手放す。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from app.embedding.base import EmbeddingError, l2_normalize
from app.embedding.catalog import ClipModel, model_dir, onnx_model_key
from app.embedding.preprocess import preprocess_clip, preprocess_eg2, preprocess_ly
from app.embedding.tokenization import (
    ClipBpeTokenizer,
    Eg2Tokenizer,
    LyTokenizer,
    eg2_image_input_ids,
)
from app.i18n import t
from app.model_store.onnx_runtime import (
    InsufficientMemoryError,
    available_memory_bytes,
    check_memory_bytes,
    session_options,
)
from app.model_store.residency import RESIDENCY, ModelResidency

IDLE_RELEASE_SECONDS = 600.0
TEXT_BATCH_SIZE = 16

# family → (画像の入力, 画像の出力, 文章の出力)
_IO_NAMES: dict[str, tuple[str, str, str]] = {
    "openai_clip": ("pixel_values", "image_embeds", "text_embeds"),
    "ly_clip": ("input", "output", "output"),
}

# EmbeddingGemma 2 の入出力の名前。画像側の位置の入力は、transformers の processor のキー名
# (`image_position_ids`)と違う。
EG2_PIXEL_VALUES = "pixel_values"
EG2_PIXEL_POSITIONS = "pixel_position_ids"
EG2_IMAGE_FEATURES = "image_features"
EG2_VIDEO_FEATURES = "video_features"
EG2_AUDIO_FEATURES = "audio_features"
EG2_OUTPUT = "sentence_embedding"
EG2_FEATURE_DIM = 512
EG2_TOKENIZER_FILE = "tokenizer.model"


def eg2_empty_features() -> np.ndarray:
    """使わないモダリティに渡す空の特徴([0, 512])。"""
    return np.zeros((0, EG2_FEATURE_DIM), dtype=np.float32)


class OnnxClipEngine:
    RESIDENCY_OWNER = "embedding"

    def __init__(
        self,
        data_dir: Path,
        model: ClipModel,
        memory_probe: Callable[[], int | None] = available_memory_bytes,
        residency: ModelResidency | None = None,
    ) -> None:
        self.data_dir = data_dir
        self.model = model
        self._memory_probe = memory_probe
        self._residency = residency or RESIDENCY
        self._residency.register(self.RESIDENCY_OWNER, self._drop)
        self._lock = threading.Lock()
        self._vision: Any = None
        self._text: Any = None
        self._tokenizer: Any = None
        self._last_used = 0.0

    # -- EmbeddingEngine --------------------------------------------------------

    @property
    def model_key(self) -> str:
        return onnx_model_key(self.model)

    @property
    def dim(self) -> int:
        return self.model.dim

    @property
    def image_batch_size(self) -> int:
        return self.model.image_batch_size

    def embed_images(self, images: list[Image.Image], *, priority: bool = False) -> np.ndarray:
        if not images:
            return np.zeros((0, self.dim), dtype=np.float32)
        if self.model.family == "embeddinggemma2":
            return self._embed_images_eg2(images, priority=priority)
        preprocess = preprocess_ly if self.model.family == "ly_clip" else preprocess_clip
        input_name, output_name, _ = _IO_NAMES[self.model.family]
        batch = self.image_batch_size
        # 調停(WD Tagger を待つことがある)の外で鍵を取らない。鍵を持ったまま待つと、文章での
        # 検索がその間ずっと待たされる。`priority` は「画像で探す」の1枚(利用者が待っている)
        # で、文章での検索と同じ優先の区間にする(ADR-0033 2章・6章)。
        with self._residency.use(self.RESIDENCY_OWNER, priority=priority), self._lock:
            session = self._ensure_vision()
            outputs: list[np.ndarray] = []
            for start in range(0, len(images), batch):
                pixels = np.stack([preprocess(image) for image in images[start : start + batch]])
                result = session.run([output_name], {input_name: pixels})[0]
                outputs.append(np.asarray(result, dtype=np.float32))
            self._last_used = time.monotonic()
        return l2_normalize(np.concatenate(outputs, axis=0))

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        if self.model.family == "embeddinggemma2":
            return self._embed_texts_eg2(texts)
        _, _, output_name = _IO_NAMES[self.model.family]
        # 文章の推論は検索のときだけ(worker は画像だけを計算する)。利用者を待たせないよう、
        # 優先の区間にする(WD Tagger の推論1回分だけを待ち、読み込まれたままの WD はすぐ
        # 手放させる。ADR-0033 2章)。読み込むのは文章側だけ。
        with self._residency.use(self.RESIDENCY_OWNER, priority=True), self._lock:
            session = self._ensure_text()
            outputs: list[np.ndarray] = []
            for start in range(0, len(texts), TEXT_BATCH_SIZE):
                feeds = self._tokenizer(texts[start : start + TEXT_BATCH_SIZE])
                result = session.run([output_name], feeds)[0]
                outputs.append(np.asarray(result, dtype=np.float32))
            self._last_used = time.monotonic()
        return l2_normalize(np.concatenate(outputs, axis=0))

    # -- EmbeddingGemma 2 ----------------------------------------------------------

    def _embed_images_eg2(self, images: list[Image.Image], *, priority: bool) -> np.ndarray:
        with self._residency.use(self.RESIDENCY_OWNER, priority=priority), self._lock:
            # 文章側を先に読み込む(画像側の目安は、文章側との差で比べる)。
            text = self._ensure_text()
            vision = self._ensure_vision()
            rows: list[np.ndarray] = []
            for image in images:
                pixels, positions, num_soft_tokens = preprocess_eg2(image)
                features = vision.run(
                    [EG2_IMAGE_FEATURES],
                    {EG2_PIXEL_VALUES: pixels, EG2_PIXEL_POSITIONS: positions},
                )[0]
                features = np.asarray(features, dtype=np.float32)
                if features.ndim != 2 or features.shape[0] != num_soft_tokens:
                    raise EmbeddingError(t("embeddings.modelUnreadable", model=self.model.name))
                input_ids = eg2_image_input_ids(num_soft_tokens)
                result = text.run(
                    [EG2_OUTPUT],
                    {
                        "input_ids": input_ids,
                        "attention_mask": np.ones_like(input_ids),
                        EG2_IMAGE_FEATURES: features,
                        EG2_VIDEO_FEATURES: eg2_empty_features(),
                        EG2_AUDIO_FEATURES: eg2_empty_features(),
                    },
                )[0]
                rows.append(np.asarray(result, dtype=np.float32).reshape(-1))
            self._last_used = time.monotonic()
        return np.stack(rows)

    def _embed_texts_eg2(self, texts: list[str]) -> np.ndarray:
        max_length = self.model.max_text_tokens
        assert max_length is not None
        with self._residency.use(self.RESIDENCY_OWNER, priority=True), self._lock:
            session = self._ensure_text()
            rows: list[np.ndarray] = []
            for text in texts:
                feeds: dict[str, np.ndarray] = self._tokenizer.feeds(
                    self.model.query_prefix + text, max_length
                )
                feeds[EG2_IMAGE_FEATURES] = eg2_empty_features()
                feeds[EG2_VIDEO_FEATURES] = eg2_empty_features()
                feeds[EG2_AUDIO_FEATURES] = eg2_empty_features()
                result = session.run([EG2_OUTPUT], feeds)[0]
                rows.append(np.asarray(result, dtype=np.float32).reshape(-1))
            self._last_used = time.monotonic()
        return np.stack(rows)

    def release_idle(self, idle_seconds: float = IDLE_RELEASE_SECONDS) -> bool:
        with self._lock:
            if self._vision is None and self._text is None:
                return False
            if time.monotonic() - self._last_used < idle_seconds:
                return False
            self._drop()
            self._residency.release(self.RESIDENCY_OWNER)
            return True

    def close(self) -> None:
        """モデルを切り替えるときに呼ぶ(セッションを手放す)。"""
        with self._lock:
            self._drop()
            self._residency.release(self.RESIDENCY_OWNER)

    @property
    def loaded_parts(self) -> tuple[str, ...]:
        parts: list[str] = []
        if self._vision is not None:
            parts.append("vision")
        if self._text is not None:
            parts.append("text")
        return tuple(parts)

    # -- 読み込み ------------------------------------------------------------------

    def _drop(self) -> None:
        """セッションへの参照を外す(`ModelResidency` から、推論の外で呼ばれることがある)。"""
        self._vision = None
        self._text = None

    def _path(self, filename: str) -> Path:
        path = model_dir(self.data_dir, self.model.name) / filename
        if not path.is_file():
            raise EmbeddingError(t("embeddings.modelMissing", model=self.model.name))
        return path

    def _check_memory(self, part_bytes: int, other_loaded: bool, other_bytes: int) -> None:
        needed = part_bytes
        if other_loaded:
            needed = max(0, self.model.memory_bytes - other_bytes)
        try:
            check_memory_bytes(self.model.name, needed, self._memory_probe)
        except InsufficientMemoryError as e:
            raise EmbeddingError(
                t(
                    "embeddings.insufficientMemory",
                    model=e.model_name,
                    needed=f"{e.needed / 1e9:.1f}",
                    available=f"{e.available / 1e9:.1f}",
                )
            ) from e

    def _create_session(self, path: Path) -> Any:
        import onnxruntime

        return onnxruntime.InferenceSession(
            str(path),
            sess_options=session_options(onnxruntime),
            providers=["CPUExecutionProvider"],
        )

    def _ensure_vision(self) -> Any:
        if self._vision is None:
            path = self._path(self.model.vision_file)
            self._check_memory(
                self.model.memory_vision_bytes,
                self._text is not None,
                self.model.memory_text_bytes,
            )
            self._vision = self._create_session(path)
        return self._vision

    def _ensure_text(self) -> Any:
        if self._text is None:
            path = self._path(self.model.text_file)
            self._check_memory(
                self.model.memory_text_bytes,
                self._vision is not None,
                self.model.memory_vision_bytes,
            )
            if self._tokenizer is None:
                self._tokenizer = self._load_tokenizer()
            self._text = self._create_session(path)
        return self._text

    def _load_tokenizer(self) -> Any:
        try:
            if self.model.family == "ly_clip":
                return LyTokenizer(self._path("spiece.model"))
            if self.model.family == "embeddinggemma2":
                return Eg2Tokenizer(self._path(EG2_TOKENIZER_FILE))
            return ClipBpeTokenizer(self._path("vocab.json"), self._path("merges.txt"))
        except (OSError, RuntimeError, ValueError) as e:
            raise EmbeddingError(t("embeddings.modelUnreadable", model=self.model.name)) from e
