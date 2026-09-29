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

メモリ対策(2026-09-29。メモリ 4GB の VM で eva02-large を読み込んで OOM で落ちた報告を受けて):

- セッションの設定で重みの prepacking(`session.disable_prepacking`)とメモリパターン
  (`enable_mem_pattern`)を切る。Raspberry Pi 5 の実測で最大 RSS が vit 約 0.69→0.53GiB、
  swinv2 約 0.93→0.70GiB、eva02-large 約 2.1→1.42GiB に下がり、推論時間の増加は 5〜10%、
  上位タグは変わらなかった。`enable_cpu_mem_arena=False` はかえって山が高くなったので使わない。
- 別のモデルに切り替えるときは、今のセッションを手放してから読み込む(2つを同時に載せない)。
- 読み込む前に空きメモリ(Linux の `/proc/meminfo` の MemAvailable と、cgroup の上限の残り)を
  モデルの目安(`WdModel.memory_bytes`)と比べ、足りなければ読み込まずに
  `InsufficientMemoryError` にする。推定は `failed` になり、自動では再試行しない(ADR-0024
  4章)。プロセスが落ちると起動時に `running` の行が待ち行列に戻り、同じモデルを読んで
  また落ちるので、その繰り返しをここで止める。空きメモリを読めない環境(Windows、macOS)
  では確かめない。
"""

from __future__ import annotations

import csv
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from app.annotation.wd_models import MODEL_FILE, TAGS_FILE, WD_MODELS, model_dir

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
    # 読み込み時と推論時のメモリの山を下げる(モジュールの docstring を参照)。
    options.enable_mem_pattern = False
    options.add_session_config_entry("session.disable_prepacking", "1")
    return options


# -- 空きメモリの確認 -------------------------------------------------------------

_PROC_MEMINFO = Path("/proc/meminfo")
_CGROUP_ROOT = Path("/sys/fs/cgroup")
# cgroup v1 で上限が無いときの値(ページ境界に丸めた int64 の最大値)。これ以上は上限なしとみなす。
_CGROUP_UNLIMITED = 1 << 60


class InsufficientMemoryError(Exception):
    """モデルを読み込むだけの空きメモリが無い。"""

    def __init__(self, model_name: str, needed: int, available: int) -> None:
        super().__init__(f"{model_name}: needed={needed} available={available}")
        self.model_name = model_name
        self.needed = needed
        self.available = available


def _read_int(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _read_stat(path: Path, key: str) -> int:
    try:
        for line in path.read_text(encoding="ascii").splitlines():
            name, _, value = line.partition(" ")
            if name == key:
                return int(value.strip())
    except (OSError, UnicodeDecodeError, ValueError):
        pass
    return 0


def meminfo_available(path: Path = _PROC_MEMINFO) -> int | None:
    """`/proc/meminfo` の MemAvailable(バイト)。読めなければ None。"""
    try:
        for line in path.read_text(encoding="ascii").splitlines():
            if line.startswith("MemAvailable:"):
                parts = line.split()
                return int(parts[1]) * 1024
    except (OSError, UnicodeDecodeError, ValueError, IndexError):
        return None
    return None


def cgroup_available(root: Path = _CGROUP_ROOT) -> int | None:
    """cgroup のメモリ上限の残り(バイト)。上限が無い・読めなければ None。

    コンテナの中では `/proc/meminfo` がホストの値を返すので、上限はこちらで見る。使用量から
    捨てられるページキャッシュ(inactive_file)を除く。v2(`memory.max`)と v1
    (`memory/memory.limit_in_bytes`)の両方を見る。
    """
    max_path = root / "memory.max"
    if max_path.is_file():
        limit = _read_int(max_path)  # 上限なしは "max" で、int にならず None
        usage = _read_int(root / "memory.current")
        inactive = _read_stat(root / "memory.stat", "inactive_file")
    else:
        v1 = root / "memory"
        limit = _read_int(v1 / "memory.limit_in_bytes")
        usage = _read_int(v1 / "memory.usage_in_bytes")
        inactive = _read_stat(v1 / "memory.stat", "total_inactive_file")
    if limit is None or usage is None or limit >= _CGROUP_UNLIMITED:
        return None
    return max(0, limit - max(0, usage - inactive))


def available_memory_bytes() -> int | None:
    """今使える空きメモリ(バイト)。MemAvailable と cgroup の残りの小さい方。読めなければ
    None(Windows、macOS など)。"""
    values = [v for v in (meminfo_available(), cgroup_available()) if v is not None]
    return min(values) if values else None


def check_memory(model_name: str, probe: Callable[[], int | None]) -> None:
    """モデルの目安より空きメモリが少なければ `InsufficientMemoryError`。"""
    needed = WD_MODELS[model_name].memory_bytes
    available = probe()
    if available is not None and available < needed:
        raise InsufficientMemoryError(model_name, needed, available)


class WdTagger:
    """モデル1つ分のセッションとラベル。スレッドから呼ばれる前提でロックを持つ。"""

    def __init__(
        self,
        data_dir: Path,
        memory_probe: Callable[[], int | None] = available_memory_bytes,
    ) -> None:
        self.data_dir = data_dir
        # テストでは差し替える。
        self._memory_probe = memory_probe
        self._lock = threading.Lock()
        self._model_name: str | None = None
        self._session: Any = None
        self._labels: list[Label] = []
        self._size = DEFAULT_INPUT_SIZE
        self._last_used = 0.0

    def _ensure_loaded(self, model_name: str) -> None:
        if self._session is not None and self._model_name == model_name:
            return
        # 別のモデルを読む前に今のセッションを手放す(2つを同時にメモリに載せない)。
        self._session = None
        self._model_name = None
        self._labels = []

        directory = model_dir(self.data_dir, model_name)
        for filename in (MODEL_FILE, TAGS_FILE):
            if not (directory / filename).is_file():
                raise FileNotFoundError(directory / filename)
        check_memory(model_name, self._memory_probe)

        import onnxruntime

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
