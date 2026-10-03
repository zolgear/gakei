"""onnxruntime のセッションの設定と、読み込む前の空きメモリの確認(ADR-0024 6章・7章、
ADR-0033 2章)。WD Tagger と埋め込みのモデルで共通に使う。

- 推論のスレッド数は CPU コア数の半分(API の応答を妨げないため)。
- 重みの prepacking(`session.disable_prepacking`)とメモリパターン(`enable_mem_pattern`)を
  切る。読み込み時と推論時のメモリの山が下がる(Raspberry Pi 5 の実測は
  `app/annotation/wd_tagger.py` の docstring)。
- 読み込む前に空きメモリ(Linux の `/proc/meminfo` の MemAvailable と、cgroup の上限の残り)を
  モデルの目安と比べ、足りなければ読み込まずに `InsufficientMemoryError` にする。空きメモリを
  読めない環境(Windows、macOS)では確かめない。
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any


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


def check_memory_bytes(label: str, needed: int, probe: Callable[[], int | None]) -> None:
    """`needed` バイトより空きメモリが少なければ `InsufficientMemoryError`(`label` は文言用)。"""
    available = probe()
    if available is not None and available < needed:
        raise InsufficientMemoryError(label, needed, available)
