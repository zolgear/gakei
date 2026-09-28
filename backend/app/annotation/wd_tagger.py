"""WD Tagger v3(ONNX)の前処理・推論・後処理(ADR-0024 3章)。

前処理は SmilingWolf の配布元(wd-tagger の Space)と同じ:

1. RGBA にして白背景に合成し、RGB にする。
2. 長辺に合わせて白で正方形にパディングする(中央寄せ)。
3. モデルの入力サイズ(ONNX の入力の形から読む。v3 は 448)に BICUBIC で縮める。
4. float32(0〜255 のまま)、RGB → BGR、NHWC(1, H, W, 3)。

後処理は `selected_tags.csv` の category 0(一般)と 4(キャラクター)だけを対象にし、
9(rating)は付けない。しきい値以上を確信度の高い順に最大20個、`_` を空白にして返す。
モデルの出力はシグモイド適用済みの確率。

セッションは初回の推論で読み込み、`IDLE_RELEASE_SECONDS` 使わなければ解放する
(`release_if_idle` を worker が定期的に呼ぶ)。読み込み(`InferenceSession` の生成)も推論も
`tag()` の中で行い、呼び出し側が `asyncio.to_thread` で別スレッドに逃がす(イベントループを
塞がない)。推論のスレッド数は CPU コア数の半分に抑える(API の応答を妨げないため)。
"""

from __future__ import annotations

import csv
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from app.annotation.wd_models import MODEL_FILE, TAGS_FILE, model_dir

CATEGORY_GENERAL = 0
CATEGORY_CHARACTER = 4
CATEGORY_RATING = 9
TARGET_CATEGORIES = frozenset({CATEGORY_GENERAL, CATEGORY_CHARACTER})

MAX_TAGS = 20
DEFAULT_INPUT_SIZE = 448
IDLE_RELEASE_SECONDS = 600.0


@dataclass(frozen=True)
class Label:
    name: str
    category: int


def load_labels(csv_path: Path) -> list[Label]:
    """`selected_tags.csv`(tag_id,name,category,count)を行の順に読む(出力の並びと一致する)。"""
    labels: list[Label] = []
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            labels.append(Label(name=row["name"], category=int(row["category"])))
    return labels


def preprocess(image: Image.Image, size: int) -> np.ndarray:
    """PIL 画像をモデルの入力(1, size, size, 3、float32、BGR、0〜255)にする。"""
    rgba = image.convert("RGBA")
    background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    rgb = Image.alpha_composite(background, rgba).convert("RGB")

    side = max(rgb.size)
    padded = Image.new("RGB", (side, side), (255, 255, 255))
    padded.paste(rgb, ((side - rgb.width) // 2, (side - rgb.height) // 2))
    if side != size:
        padded = padded.resize((size, size), Image.Resampling.BICUBIC)

    array = np.asarray(padded, dtype=np.float32)
    array = array[:, :, ::-1]  # RGB -> BGR
    return np.ascontiguousarray(array[np.newaxis, ...])


def postprocess(
    probs: np.ndarray, labels: list[Label], threshold: float, max_tags: int = MAX_TAGS
) -> list[tuple[str, float]]:
    """確率の配列(ラベル数)から、対象カテゴリーでしきい値以上のタグを確信度順に返す。"""
    values = np.asarray(probs, dtype=np.float32).reshape(-1)
    picked: list[tuple[str, float]] = []
    for index, label in enumerate(labels):
        if index >= values.shape[0]:
            break
        if label.category not in TARGET_CATEGORIES:
            continue
        score = float(values[index])
        if score >= threshold:
            picked.append((label.name.replace("_", " "), score))
    picked.sort(key=lambda pair: -pair[1])
    return picked[:max_tags]


def _input_size(session: Any) -> int:
    """ONNX の入力の形(N, H, W, C)から H を読む。読めなければ 448。"""
    try:
        shape = session.get_inputs()[0].shape
        height = shape[1]
        if isinstance(height, int) and height > 0:
            return height
    except (AttributeError, IndexError, TypeError):
        pass
    return DEFAULT_INPUT_SIZE


def inference_threads(cpu_count: int | None = None) -> int:
    """推論に使うスレッド数。CPU コア数の半分(最低1)。"""
    count = cpu_count if cpu_count is not None else (os.cpu_count() or 2)
    return max(1, count // 2)


def session_options(onnxruntime: Any) -> Any:
    options = onnxruntime.SessionOptions()
    options.intra_op_num_threads = inference_threads()
    options.inter_op_num_threads = 1
    return options


class WdTagger:
    """モデル1つ分のセッションとラベル。スレッドから呼ばれる前提でロックを持つ。"""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self._lock = threading.Lock()
        self._model_name: str | None = None
        self._session: Any = None
        self._labels: list[Label] = []
        self._size = DEFAULT_INPUT_SIZE
        self._last_used = 0.0

    def _ensure_loaded(self, model_name: str) -> None:
        if self._session is not None and self._model_name == model_name:
            return
        import onnxruntime

        directory = model_dir(self.data_dir, model_name)
        for filename in (MODEL_FILE, TAGS_FILE):
            if not (directory / filename).is_file():
                raise FileNotFoundError(directory / filename)
        session = onnxruntime.InferenceSession(
            str(directory / MODEL_FILE),
            sess_options=session_options(onnxruntime),
            providers=["CPUExecutionProvider"],
        )
        self._labels = load_labels(directory / TAGS_FILE)
        self._session = session
        self._model_name = model_name
        self._size = _input_size(session)

    def tag(self, image: Image.Image, model_name: str, threshold: float) -> list[tuple[str, float]]:
        with self._lock:
            self._ensure_loaded(model_name)
            batch = preprocess(image, self._size)
            input_name = self._session.get_inputs()[0].name
            outputs = self._session.run(None, {input_name: batch})
            self._last_used = time.monotonic()
            return postprocess(outputs[0][0], self._labels, threshold)

    def release_if_idle(self, idle_seconds: float = IDLE_RELEASE_SECONDS) -> bool:
        with self._lock:
            if self._session is None:
                return False
            if time.monotonic() - self._last_used < idle_seconds:
                return False
            self._session = None
            self._model_name = None
            self._labels = []
            return True

    @property
    def loaded(self) -> bool:
        return self._session is not None
