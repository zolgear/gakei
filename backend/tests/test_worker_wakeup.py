"""プロセス内の worker が、合図(notify・stop)を取りこぼさずにすぐ起きること。

待ちの上限(`_POLL_INTERVAL_SECONDS`)を長くしておき、上限まで寝てしまえば落ちるようにする。
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from app.worker import annotator as annotator_module
from app.worker import embedder as embedder_module
from app.worker import mod_delivery as mod_delivery_module
from app.worker.annotator import Annotator
from app.worker.embedder import Embedder
from app.worker.mod_delivery import ModDeliveryWorker
from app.worker.runner import Runner

_LONG = 30.0
_QUICK = 2.0


@pytest.mark.parametrize(
    ("module", "cls"),
    [
        (annotator_module, Annotator),
        (embedder_module, Embedder),
        (mod_delivery_module, ModDeliveryWorker),
    ],
)
def test_sleep_returns_at_once_after_stop(
    monkeypatch: pytest.MonkeyPatch, module: object, cls: type
) -> None:
    """待ち行列を見ている最中に stop が来ても、次の `_sleep` で上限まで寝ない。"""
    monkeypatch.setattr(module, "_POLL_INTERVAL_SECONDS", _LONG)

    async def scenario() -> None:
        worker = cls.__new__(cls)
        worker._wake = asyncio.Event()
        worker._stop = asyncio.Event()
        # stop() と同じ合図(取り出しの最中に届いた)。
        worker._stop.set()
        worker._wake.set()
        await asyncio.wait_for(worker._sleep(), timeout=_QUICK)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("module", "cls"),
    [
        (annotator_module, Annotator),
        (embedder_module, Embedder),
        (mod_delivery_module, ModDeliveryWorker),
    ],
)
def test_sleep_keeps_notify_that_came_while_picking(
    monkeypatch: pytest.MonkeyPatch, module: object, cls: type
) -> None:
    """待ち行列を見ている最中に来た notify を、`_sleep` が消さずに受け取る。"""
    monkeypatch.setattr(module, "_POLL_INTERVAL_SECONDS", _LONG)

    async def scenario() -> None:
        worker = cls.__new__(cls)
        worker._wake = asyncio.Event()
        worker._stop = asyncio.Event()
        worker._wake.set()
        await asyncio.wait_for(worker._sleep(), timeout=_QUICK)
        # 受け取った合図は消える(次の `_sleep` は待つ)。
        assert not worker._wake.is_set()

    asyncio.run(scenario())


def test_runner_notify_from_another_thread_wakes_the_loop() -> None:
    """同期エンドポイント(別のスレッド)からの notify で、レーンがすぐ起きる。"""

    async def scenario() -> float:
        runner = Runner.__new__(Runner)
        event = asyncio.Event()
        runner._wake_events = {"fake": event}
        runner._event_loop = asyncio.get_running_loop()
        timer = threading.Timer(0.05, runner.notify)
        started = time.monotonic()
        timer.start()
        try:
            await asyncio.wait_for(event.wait(), timeout=_LONG)
        finally:
            timer.join()
        return time.monotonic() - started

    elapsed = asyncio.run(scenario())
    assert elapsed < _QUICK
