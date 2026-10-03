"""WD Tagger と埋め込みのモデルを同時にメモリに載せないための調停(ADR-0033 2章)。

メモリの小さいマシンで両方を抱えないよう、プロセスの中でモデルを持てるのは1つの持ち主
(`owner`。今は `wd` と `embedding`)だけにする。

- 持ち主は推論(読み込みを含む)を `use(owner)` の中で行う。別の持ち主がモデルを持っている間は
  待つ。
- 持っている側が推論の最中でなければ、次のどちらかで手放させる(登録した `drop` を呼ぶ)。
  - しばらく(`grace` 秒)使われていない。
  - 待っている側がいて、持っている側が `max_hold` 秒より長く持ち続けている(両方の一括実行が
    同時に走っても、片方が待ち続けないように。1件ごとに入れ替えると読み込みが毎回になるので、
    ある程度まとめて持たせる)。
- `drop` はセッションへの参照を外すだけにする(鍵を取らない)。持ち主は `use` の中でしか
  セッションに触れないので、`use` の外で外されても困らない(次の `use` で読み込み直す)。
- 持ち主自身の「しばらく使わなければ解放」は `release_if_idle` で行う。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

DEFAULT_GRACE_SECONDS = 2.0
DEFAULT_MAX_HOLD_SECONDS = 30.0
_WAIT_STEP_SECONDS = 0.25


class ModelResidency:
    def __init__(
        self,
        grace_seconds: float = DEFAULT_GRACE_SECONDS,
        max_hold_seconds: float = DEFAULT_MAX_HOLD_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.grace_seconds = grace_seconds
        self.max_hold_seconds = max_hold_seconds
        self._clock = clock
        self._cond = threading.Condition()
        self._drops: dict[str, Callable[[], None]] = {}
        self._holder: str | None = None
        self._busy = False
        self._hold_started = 0.0
        self._last_used = 0.0

    def register(self, owner: str, drop: Callable[[], None]) -> None:
        """持ち主と、そのモデルを手放す関数を登録する(登録し直したら置き換える)。"""
        with self._cond:
            self._drops[owner] = drop

    @property
    def holder(self) -> str | None:
        with self._cond:
            return self._holder

    def _evict_locked(self) -> None:
        holder = self._holder
        if holder is not None:
            drop = self._drops.get(holder)
            if drop is not None:
                drop()
        self._holder = None
        self._busy = False

    def _can_evict_locked(self, now: float) -> bool:
        if self._busy:
            return False
        if now - self._last_used >= self.grace_seconds:
            return True
        # ここに来るのは待っている側だけなので、「待っている側がいる」は満たしている。
        return now - self._hold_started >= self.max_hold_seconds

    @contextmanager
    def use(self, owner: str) -> Iterator[None]:
        """`owner` がモデルを持つ間の区間。別の持ち主がいれば、手放させるか終わるまで待つ。"""
        with self._cond:
            while True:
                now = self._clock()
                if self._holder is None or self._holder == owner:
                    break
                if self._can_evict_locked(now):
                    self._evict_locked()
                    break
                self._cond.wait(timeout=_WAIT_STEP_SECONDS)
            if self._holder != owner:
                self._holder = owner
                self._hold_started = self._clock()
            self._busy = True
        try:
            yield
        finally:
            with self._cond:
                self._busy = False
                self._last_used = self._clock()
                self._cond.notify_all()

    def release(self, owner: str) -> None:
        """`owner` が自分のモデルを手放したことを知らせる(持っていなければ何もしない)。"""
        with self._cond:
            if self._holder == owner and not self._busy:
                self._holder = None
                self._cond.notify_all()


# プロセスで1つ。テストでは別のインスタンスを渡してよい。
RESIDENCY = ModelResidency()
