"""`GET /api/capabilities` が外部のサーバー(SD WebUI・ComfyUI)を待たないこと。

手元の値をすぐ返し、古いか無ければ裏で取り直す(同時に1つだけ)。Run の作成などが使う
`capabilities()` / `availability()` は、これまでどおり古ければその場で問い合わせる。
偽の接続先だけを使う(実の WebUI・ComfyUI には触れない)。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.providers.background_cache import BackgroundRefreshCache
from app.providers.comfyui.provider import ComfyUIProvider
from app.providers.sdwebui.provider import SdWebuiProvider
from tests.sdwebui_fake import FakeSdWebui

URL = "http://127.0.0.1:7860"
# 「すぐ返る」の上限。最初の問い合わせを待つ猶予(0.2 秒)を含めても十分に短い。
FAST_SECONDS = 0.5


class _Gate:
    """開くまで要求を止めておく、偽の接続先の前段。止まっている要求の数を数える。"""

    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self._handler = handler
        self.opened = threading.Event()
        self.lock = threading.Lock()
        self.waiting = 0
        self.total = 0

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            with self.lock:
                self.total += 1
                self.waiting += 1
            try:
                self.opened.wait(10)
            finally:
                with self.lock:
                    self.waiting -= 1
            return self._handler(request)

        return httpx.MockTransport(handle)


def _refuse(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


def _provider(transport: httpx.MockTransport, session_factory: sessionmaker) -> SdWebuiProvider:
    return SdWebuiProvider(
        URL, session_factory, 30.0, credentials_loader=lambda: None, transport=transport
    )


def _wait_idle(provider: SdWebuiProvider) -> None:
    provider._availability_cache.wait_idle()
    provider._catalog_cache.wait_idle()


def _timed[T](fn: Callable[[], T]) -> tuple[T, float]:
    started = time.monotonic()
    value = fn()
    return value, time.monotonic() - started


# -- BackgroundRefreshCache ------------------------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_cache_peek_returns_stale_value_and_refreshes_in_background() -> None:
    clock = _Clock()
    values = iter(["first", "second"])
    release = threading.Event()

    def fetch() -> str:
        value = next(values)
        if value == "second":
            release.wait(5)
        return value

    cache: BackgroundRefreshCache[str] = BackgroundRefreshCache(
        fetch, lambda _v: 10.0, name="test", clock=clock
    )
    assert cache.get() == "first"
    clock.now += 11  # 古くなった
    # 取り直しを待たずに古い値を返す。取り直しは1つだけ。
    assert cache.peek() == (True, "first")
    assert cache.peek() == (True, "first")
    assert cache.refreshing
    release.set()
    cache.wait_idle()
    assert not cache.refreshing
    assert cache.peek() == (True, "second")


def test_cache_invalidate_discards_inflight_result() -> None:
    clock = _Clock()
    calls: list[int] = []
    release_old = threading.Event()
    old_started = threading.Event()

    def fetch() -> int:
        calls.append(len(calls))
        if len(calls) == 1:
            old_started.set()
            release_old.wait(5)
            return 1  # 捨てられるはずの古い結果
        return 2

    cache: BackgroundRefreshCache[int] = BackgroundRefreshCache(
        fetch, lambda _v: 10.0, name="test", clock=clock
    )
    assert cache.peek() == (False, None)
    assert old_started.wait(5)
    old_thread = cache._thread
    cache.invalidate()
    # 古い取り直しが終わっていなくても、新しい世代の取り直しを始める
    assert cache.peek() == (False, None)
    cache.wait_idle()
    assert cache.peek() == (True, 2)
    release_old.set()
    assert old_thread is not None
    old_thread.join(5)
    assert cache.peek() == (True, 2)
    assert len(calls) == 2


def test_cache_get_waits_when_stale() -> None:
    clock = _Clock()
    counter = iter(range(100))
    cache: BackgroundRefreshCache[int] = BackgroundRefreshCache(
        lambda: next(counter), lambda _v: 10.0, name="test", clock=clock
    )
    assert cache.get() == 0
    assert cache.get() == 0
    clock.now += 11
    assert cache.get() == 1
    cache.invalidate()
    assert cache.get() == 2


# -- SD WebUI -------------------------------------------------------------------------


def test_sdwebui_snapshot_does_not_wait_for_unreachable_server(
    db_session_factory: sessionmaker,
) -> None:
    gate = _Gate(_refuse)
    provider = _provider(gate.transport(), db_session_factory)
    try:
        snapshot, elapsed = _timed(provider.status_snapshot)
        assert elapsed < FAST_SECONDS
        assert snapshot.checking is True
        assert snapshot.available is False
        assert snapshot.unavailable_reason
        assert snapshot.capabilities.models == []

        # 取り直し中に何度呼んでも、問い合わせは増えない(一覧と到達性で1つずつ)
        for _ in range(5):
            _, elapsed = _timed(provider.status_snapshot)
            assert elapsed < FAST_SECONDS
        assert gate.total == 2
    finally:
        gate.opened.set()
    _wait_idle(provider)

    # 接続できないと分かった後は、確かめ中ではなく「使えない」
    snapshot, elapsed = _timed(provider.status_snapshot)
    assert elapsed < FAST_SECONDS
    assert snapshot.checking is False
    assert snapshot.available is False
    assert snapshot.unavailable_reason


def test_sdwebui_snapshot_shows_catalog_after_background_refresh(
    db_session_factory: sessionmaker,
) -> None:
    fake = FakeSdWebui()
    gate = _Gate(fake.handle)
    provider = _provider(gate.transport(), db_session_factory)
    try:
        snapshot = provider.status_snapshot()
        assert snapshot.checking is True
        assert snapshot.capabilities.models == []
    finally:
        gate.opened.set()
    _wait_idle(provider)

    snapshot = provider.status_snapshot()
    assert snapshot.checking is False
    assert snapshot.available is True
    assert [m.model for m in snapshot.capabilities.models] == ["model-a", "model-b"]


def test_sdwebui_snapshot_answers_at_once_for_fast_server(
    db_session_factory: sessionmaker,
) -> None:
    """手元の接続先なら、最初の問い合わせを短く待つ間に済み、確かめ中を挟まない。"""
    fake = FakeSdWebui()
    provider = _provider(fake.transport(), db_session_factory)
    snapshot = provider.status_snapshot()
    _wait_idle(provider)
    if snapshot.checking:  # 機械が混んでいて猶予に間に合わなかったとき
        snapshot = provider.status_snapshot()
    assert snapshot.checking is False
    assert [m.model for m in snapshot.capabilities.models] == ["model-a", "model-b"]


def test_sdwebui_invalidate_then_snapshot_refetches(db_session_factory: sessionmaker) -> None:
    fake = FakeSdWebui()
    provider = _provider(fake.transport(), db_session_factory)
    provider.status_snapshot()
    _wait_idle(provider)
    assert len(provider.status_snapshot().capabilities.models) == 2

    fake.checkpoints = fake.checkpoints[:1]
    # キャッシュがあるうちは、接続先が変わっても前の一覧のまま
    assert len(provider.status_snapshot().capabilities.models) == 2
    provider.invalidate_cache()
    provider.status_snapshot()
    _wait_idle(provider)
    snapshot = provider.status_snapshot()
    assert [m.model for m in snapshot.capabilities.models] == ["model-a"]


def test_sdwebui_sync_paths_still_wait(db_session_factory: sessionmaker) -> None:
    """Run の作成が使う `catalog()` / `availability()` は、無ければその場で問い合わせる。"""
    fake = FakeSdWebui()
    provider = _provider(fake.transport(), db_session_factory)
    assert provider.availability() == (True, None)
    catalog = provider.catalog()
    assert catalog is not None
    assert [c.model_name for c in catalog.checkpoints] == ["model-a", "model-b"]
    # 待つ経路で取った値は、待たない経路でも使う(問い合わせを増やさない)
    count = len(fake.requests)
    snapshot = provider.status_snapshot()
    assert snapshot.checking is False
    assert len(snapshot.capabilities.models) == 2
    assert len(fake.requests) == count


# -- ComfyUI --------------------------------------------------------------------------


def test_comfyui_snapshot_does_not_wait(
    db_session_factory: sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = threading.Event()
    calls: list[str] = []

    def slow_check(base_url: str, timeout: float = 1.0):  # noqa: ANN202
        calls.append(base_url)
        release.wait(10)
        return (False, "接続できません", None)

    monkeypatch.setattr("app.providers.comfyui.provider.check_available", slow_check)
    provider = ComfyUIProvider("http://127.0.0.1:8188", db_session_factory, 60.0)
    try:
        for _ in range(3):
            snapshot, elapsed = _timed(provider.status_snapshot)
            assert elapsed < FAST_SECONDS
            assert snapshot.checking is True
            assert snapshot.available is False
        assert len(calls) == 1
    finally:
        release.set()
    provider._availability_cache.wait_idle()
    snapshot = provider.status_snapshot()
    assert snapshot.checking is False
    assert snapshot.available is False
    assert snapshot.unavailable_reason == "接続できません"


# -- GET /api/capabilities -------------------------------------------------------------


def test_capabilities_endpoint_does_not_wait_for_sdwebui(
    client: TestClient, db_session_factory: sessionmaker
) -> None:
    gate = _Gate(_refuse)
    provider = _provider(gate.transport(), db_session_factory)
    client.app.state.registry.set_provider("sdwebui", provider)  # type: ignore[attr-defined]
    try:
        response, elapsed = _timed(lambda: client.get("/api/capabilities"))
        assert response.status_code == 200
        assert elapsed < FAST_SECONDS
        entry = next(p for p in response.json()["providers"] if p["provider"] == "sdwebui")
        assert entry["checking"] is True
        assert entry["available"] is False
        assert entry["models"] == []
        # 主プロバイダーは確かめ中にならない
        primary = next(p for p in response.json()["providers"] if p["provider"] != "sdwebui")
        assert primary["checking"] is False
    finally:
        gate.opened.set()
    _wait_idle(provider)

    response, elapsed = _timed(lambda: client.get("/api/capabilities"))
    assert elapsed < FAST_SECONDS
    entry = next(p for p in response.json()["providers"] if p["provider"] == "sdwebui")
    assert entry["checking"] is False
    assert entry["available"] is False
