"""外部のサーバーへの問い合わせの結果を覚えておき、古くなったら裏で取り直すキャッシュ。

`GET /api/capabilities` は接続先(SD WebUI・ComfyUI)が落ちていても待たせない
(stale-while-revalidate)。画面の表示に使う `peek` は、手元にある値をすぐ返し、古いか
無ければ裏のスレッドで取り直す(同時に1つだけ)。Run の作成や実行など正しい値が要る経路は
`get` で、これまでどおり古ければその場で取り直して待つ。

`invalidate` は値を捨てる(接続先や資格情報の変更、`POST /api/sdwebui/refresh`)。捨てる前に
始まっていた取り直しの結果は使わない(世代で見分ける)。
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)

Clock = Callable[[], float]

# 一度も値が無いとき(起動直後など)に、裏の最初の問い合わせを待つ上限。手元の接続先なら
# この間に済むので、確かめ中の表示を挟まずに済む。落ちている接続先でもこれ以上は待たせない。
FIRST_CHECK_GRACE_SECONDS = 0.2


class BackgroundRefreshCache[T]:
    """値1つ分のキャッシュ。`ttl(value)` 秒が過ぎたら古いとみなす。

    `fetch` は例外を投げない(失敗も値として表す)前提。投げたときは取り直しを諦め、手元の
    値はそのまま残す。
    """

    def __init__(
        self,
        fetch: Callable[[], T],
        ttl: Callable[[T], float],
        *,
        name: str,
        clock: Clock | None = None,
    ) -> None:
        self._fetch = fetch
        self._ttl = ttl
        self._name = name
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._entry: tuple[float, T] | None = None
        # invalidate のたびに進める。古い世代の取り直しの結果は捨てる。
        self._generation = 0
        # 裏で取り直し中の世代(無ければ None)と、そのスレッド(テストで待つため)
        self._refreshing_generation: int | None = None
        self._thread: threading.Thread | None = None

    def _is_fresh(self, entry: tuple[float, T] | None, now: float) -> bool:
        if entry is None:
            return False
        fetched_at, value = entry
        return now - fetched_at < self._ttl(value)

    def _store(self, generation: int, fetched_at: float, value: T) -> None:
        with self._lock:
            if generation == self._generation:
                self._entry = (fetched_at, value)

    # -- 待つ経路 ---------------------------------------------------------------------

    def get(self) -> T:
        """新しい値を返す。古いか無ければ、その場で取り直して待つ。"""
        now = self._clock()
        with self._lock:
            entry = self._entry
            generation = self._generation
        if entry is not None and self._is_fresh(entry, now):
            return entry[1]
        value = self._fetch()
        self._store(generation, now, value)
        return value

    # -- 待たない経路 ------------------------------------------------------------------

    def peek(self) -> tuple[bool, T | None]:
        """手元の値を待たずに返す(`(値があるか, 値)`)。古いか無ければ裏で取り直しを始める。"""
        now = self._clock()
        with self._lock:
            entry = self._entry
            if not self._is_fresh(entry, now) and self._refreshing_generation != self._generation:
                self._start_refresh_locked()
        if entry is None:
            return False, None
        return True, entry[1]

    def wait_first(self, deadline: float) -> None:
        """一度も値が無く、裏で取り直し中なら、`deadline`(`time.monotonic` の時刻)まで
        終わるのを待つ。"""
        with self._lock:
            if self._entry is not None:
                return
            thread = self._thread if self._refreshing_generation is not None else None
        if thread is not None:
            remaining = deadline - time.monotonic()
            if remaining > 0:
                thread.join(remaining)

    def put(self, value: T) -> None:
        """ほかの経路で確かめたばかりの値を覚える(設定画面の状態の確認など)。"""
        now = self._clock()
        with self._lock:
            self._entry = (now, value)

    def _start_refresh_locked(self) -> None:
        generation = self._generation
        self._refreshing_generation = generation
        thread = threading.Thread(
            target=self._refresh, args=(generation,), name=f"gakei-{self._name}", daemon=True
        )
        self._thread = thread
        thread.start()

    def _refresh(self, generation: int) -> None:
        try:
            started_at = self._clock()
            value = self._fetch()
            self._store(generation, started_at, value)
        except Exception:  # noqa: BLE001 - 裏の取り直しの失敗で落とさない(次の peek でまた試す)
            logger.warning("%s の取り直しに失敗しました", self._name, exc_info=True)
        finally:
            with self._lock:
                if self._refreshing_generation == generation:
                    self._refreshing_generation = None

    # -- 状態 --------------------------------------------------------------------------

    def invalidate(self) -> None:
        """値を捨てる。次の `get` はその場で、次の `peek` は裏で取り直す。"""
        with self._lock:
            self._entry = None
            self._generation += 1

    @property
    def refreshing(self) -> bool:
        with self._lock:
            return self._refreshing_generation is not None

    def wait_idle(self, timeout: float = 5.0) -> None:
        """裏の取り直しが終わるまで待つ(テスト用)。"""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
