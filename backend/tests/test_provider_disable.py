"""SD WebUI と ComfyUI の「無効にする / 有効にする」(ADR-0013 7章、ADR-0038 6章)。

無効にしても URL・資格情報・待つ上限は残り、モデルの選択肢から消え、接続先に問い合わせない。
有効に戻すと同じ設定で接続する。無効は再起動しても保ち、環境変数の URL があっても有効に
ならない。Run が `queued` / `running` の間は 409。

実物の WebUI・ComfyUI には接続しない(偽の WebUI と、`check_available` の差し替えだけを使う)。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.domain import comfyui_connection, sdwebui_connection
from app.providers.comfyui.provider import ComfyUIProvider
from app.providers.sdwebui.provider import SdWebuiProvider
from tests.comfyui_graphs import T2I_GRAPH, clone
from tests.sdwebui_fake import FakeSdWebui, install_fake_factories

SD_URL = "http://127.0.0.1:7860"
COMFY_URL = "http://127.0.0.1:8189"
# 起動時に登録したプロバイダーが万一問い合わせても、どこにもつながらないアドレス
UNREACHABLE_URL = "http://127.0.0.1:9"


@contextmanager
def _app(monkeypatch: pytest.MonkeyPatch, data_dir: Path, **env: str) -> Iterator[TestClient]:
    """同じ `DATA_DIR` で作り直すと「再起動」になる(DB の設定は引き継がれる)。"""
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("FAKE_PROVIDER", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    for name in ("SDWEBUI_URL", "COMFYUI_URL"):
        monkeypatch.setenv(name, env.get(name, ""))

    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


def _providers(client: TestClient) -> set[str]:
    return {p["provider"] for p in client.get("/api/capabilities").json()["providers"]}


# -- SD WebUI ---------------------------------------------------------------------


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeSdWebui:
    fake = FakeSdWebui()
    install_fake_factories(monkeypatch, fake)
    return fake


def test_sdwebui_disable_keeps_settings_and_stops_queries(
    client: TestClient, fake: FakeSdWebui
) -> None:
    assert client.put("/api/sdwebui/connection", json={"url": SD_URL}).status_code == 200
    assert (
        client.put(
            "/api/sdwebui/credentials", json={"username": "user-x", "password": "pw-123"}
        ).status_code
        == 200
    )
    assert (
        client.patch("/api/settings/general", json={"sdwebui_timeout_seconds": 900}).status_code
        == 200
    )
    assert "sdwebui" in _providers(client)

    response = client.post("/api/sdwebui/connection/disable")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["disabled"] is True
    assert body["enabled"] is False
    assert body["available"] is False
    assert body["url"] == SD_URL
    assert body["source"] == "setting"
    assert body["loopback"] is True
    assert body["credentials_set"] is True
    assert client.app.state.registry.get("sdwebui") is None

    # 無効の間は接続先に問い合わせない(状態・capabilities・LoRA・一覧の読み直し)
    fake.requests.clear()
    assert client.get("/api/sdwebui/status").json()["disabled"] is True
    assert "sdwebui" not in _providers(client)
    assert client.get("/api/sdwebui/loras").status_code == 409
    assert client.post("/api/sdwebui/refresh").status_code == 409
    assert fake.requests == []

    # 待つ上限も残っている
    timeout = client.get("/api/settings/general").json()["sdwebui_timeout_seconds"]
    assert timeout["value"] == 900

    # 生成の依頼も、切り離したときと同じく受け付けない
    run = client.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": "model-a",
            "prompt": "a cat",
            "provider": "sdwebui",
            "params": {},
        },
    )
    assert run.status_code >= 400
    assert fake.requests == []

    response = client.post("/api/sdwebui/connection/enable")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["disabled"] is False
    assert body["enabled"] is True
    assert body["available"] is True
    assert body["url"] == SD_URL
    assert body["credentials_set"] is True
    assert "sdwebui" in _providers(client)


def test_sdwebui_url_change_while_disabled_stays_disabled(
    client: TestClient, fake: FakeSdWebui
) -> None:
    client.put("/api/sdwebui/connection", json={"url": SD_URL})
    client.post("/api/sdwebui/connection/disable")
    fake.requests.clear()

    response = client.put("/api/sdwebui/connection", json={"url": "http://127.0.0.1:7861"})
    assert response.status_code == 200, response.text
    assert response.json()["disabled"] is True
    assert response.json()["url"] == "http://127.0.0.1:7861"
    assert client.app.state.registry.get("sdwebui") is None
    assert fake.requests == []

    # 接続テストは無効の間も押せる(明示の操作)。無効のまま
    assert client.post("/api/sdwebui/connection/test", json={}).json()["available"] is True
    assert client.get("/api/sdwebui/status").json()["disabled"] is True

    enabled = client.post("/api/sdwebui/connection/enable").json()
    assert enabled["url"] == "http://127.0.0.1:7861"
    assert enabled["enabled"] is True


def test_sdwebui_disable_requires_url(client: TestClient) -> None:
    assert client.post("/api/sdwebui/connection/disable").status_code == 409


def test_sdwebui_detach_clears_disabled(client: TestClient, fake: FakeSdWebui) -> None:
    client.put("/api/sdwebui/connection", json={"url": SD_URL})
    client.post("/api/sdwebui/connection/disable")
    body = client.delete("/api/sdwebui/connection").json()
    assert body["url"] is None
    assert body["disabled"] is False
    # つなぎ直せば、無効ではなく有効になる
    body = client.put("/api/sdwebui/connection", json={"url": SD_URL}).json()
    assert body["enabled"] is True


def test_sdwebui_disabled_survives_restart_even_with_env(
    monkeypatch: pytest.MonkeyPatch, data_dir: Path
) -> None:
    with _app(monkeypatch, data_dir, SDWEBUI_URL=UNREACHABLE_URL) as client:
        assert isinstance(client.app.state.registry.get("sdwebui"), SdWebuiProvider)
        body = client.post("/api/sdwebui/connection/disable").json()
        assert body["disabled"] is True
        # 環境変数から来た URL は、出所も含めてそのまま
        assert body["source"] == "env"
        assert body["url"] == UNREACHABLE_URL

    with _app(monkeypatch, data_dir, SDWEBUI_URL=UNREACHABLE_URL) as client:
        assert client.app.state.registry.get("sdwebui") is None
        status = client.get("/api/sdwebui/status").json()
        assert status["disabled"] is True
        assert status["enabled"] is False
        assert status["url"] == UNREACHABLE_URL
        assert "sdwebui" not in _providers(client)
        client.post("/api/sdwebui/connection/enable")

    with _app(monkeypatch, data_dir, SDWEBUI_URL=UNREACHABLE_URL) as client:
        assert isinstance(client.app.state.registry.get("sdwebui"), SdWebuiProvider)


def test_sdwebui_disable_enable_locked_while_run_is_queued(
    client_no_runner: TestClient, fake: FakeSdWebui
) -> None:
    app = client_no_runner.app
    from app.api.sdwebui import _make_provider

    # PUT だと実行レーンが起動して Run が進むので、設定と登録簿に直接入れる
    with app.state.session_factory() as db:
        sdwebui_connection.save_connection_url(db, SD_URL)
    app.state.registry.providers["sdwebui"] = _make_provider(
        SD_URL, app.state.settings, app.state.session_factory
    )
    run = client_no_runner.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": "model-a",
            "prompt": "a cat",
            "provider": "sdwebui",
            "params": {},
        },
    )
    assert run.status_code == 202, run.text

    assert client_no_runner.post("/api/sdwebui/connection/disable").status_code == 409
    assert client_no_runner.post("/api/sdwebui/connection/enable").status_code == 409
    assert client_no_runner.get("/api/sdwebui/status").json()["disabled"] is False


# -- ComfyUI ----------------------------------------------------------------------


@pytest.fixture
def comfy_checks(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """ComfyUI への到達確認を差し替え、問い合わせた URL を記録する。"""
    calls: list[str] = []

    def check(base_url: str, timeout: float = 1.0, **_kw: object) -> tuple[bool, str | None, dict]:
        calls.append(base_url)
        return True, None, {"system": {"comfyui_version": "1.0.0-test"}, "devices": []}

    monkeypatch.setattr("app.api.comfyui.check_available", check)
    monkeypatch.setattr("app.providers.comfyui.provider.check_available", check)
    monkeypatch.setattr("app.api.comfyui._fetch_object_info", lambda url: calls.append(url) or {})
    return calls


def _create_workflow(client: TestClient) -> dict:
    response = client.post(
        "/api/comfyui/workflows",
        json={
            "name": "t2i",
            "operation": "generate",
            "template": clone(T2I_GRAPH),
            "bindings": {
                "prompt": {"node": "6", "input": "text"},
                "negative_prompt": {"node": "7", "input": "text"},
                "seed": [{"node": "3", "input": "seed"}],
                "width": {"node": "5", "input": "width"},
                "height": {"node": "5", "input": "height"},
                "batch_size": {"node": "5", "input": "batch_size"},
                "outputs": ["9"],
            },
            "exposed_params": [],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_comfyui_disable_keeps_url_and_workflows(
    client: TestClient, comfy_checks: list[str]
) -> None:
    assert client.put("/api/comfyui/connection", json={"url": COMFY_URL}).status_code == 200
    workflow = _create_workflow(client)
    assert "comfyui" in _providers(client)

    response = client.post("/api/comfyui/connection/disable")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["disabled"] is True
    assert body["enabled"] is False
    assert body["available"] is False
    assert body["url"] == COMFY_URL
    assert body["loopback"] is True
    assert client.app.state.registry.get("comfyui") is None

    comfy_checks.clear()
    assert client.get("/api/comfyui/status").json()["disabled"] is True
    assert "comfyui" not in _providers(client)
    # 登録画面の補完も問い合わせない
    analyze = client.post("/api/comfyui/workflows/analyze", json={"template": clone(T2I_GRAPH)})
    assert analyze.status_code == 200, analyze.text
    assert comfy_checks == []

    # ワークフローは消えない
    listed = client.get("/api/comfyui/workflows").json()["items"]
    assert workflow["id"] in [w["id"] for w in listed]

    body = client.post("/api/comfyui/connection/enable").json()
    assert body["disabled"] is False
    assert body["enabled"] is True
    assert body["url"] == COMFY_URL
    assert "comfyui" in _providers(client)


def test_comfyui_url_change_while_disabled_stays_disabled(
    client: TestClient, comfy_checks: list[str]
) -> None:
    client.put("/api/comfyui/connection", json={"url": COMFY_URL})
    client.post("/api/comfyui/connection/disable")
    body = client.put("/api/comfyui/connection", json={"url": "http://127.0.0.1:8190"}).json()
    assert body["disabled"] is True
    assert body["url"] == "http://127.0.0.1:8190"
    assert client.app.state.registry.get("comfyui") is None
    # 接続テストは無効の間も使える。無効のまま
    assert client.post("/api/comfyui/connection/test", json={}).status_code == 200
    assert client.get("/api/comfyui/status").json()["disabled"] is True


def test_comfyui_disable_requires_url_and_detach_clears(
    client: TestClient, comfy_checks: list[str]
) -> None:
    assert client.post("/api/comfyui/connection/disable").status_code == 409
    client.put("/api/comfyui/connection", json={"url": COMFY_URL})
    client.post("/api/comfyui/connection/disable")
    body = client.delete("/api/comfyui/connection").json()
    assert body["url"] is None
    assert body["disabled"] is False


def test_comfyui_disabled_survives_restart_even_with_env(
    monkeypatch: pytest.MonkeyPatch, data_dir: Path
) -> None:
    with _app(monkeypatch, data_dir, COMFYUI_URL=UNREACHABLE_URL) as client:
        assert isinstance(client.app.state.registry.get("comfyui"), ComfyUIProvider)
        body = client.post("/api/comfyui/connection/disable").json()
        assert body["disabled"] is True
        assert body["source"] == "env"

    with _app(monkeypatch, data_dir, COMFYUI_URL=UNREACHABLE_URL) as client:
        assert client.app.state.registry.get("comfyui") is None
        status = client.get("/api/comfyui/status").json()
        assert status["disabled"] is True
        assert status["url"] == UNREACHABLE_URL
        assert "comfyui" not in _providers(client)
        client.post("/api/comfyui/connection/enable")

    with _app(monkeypatch, data_dir, COMFYUI_URL=UNREACHABLE_URL) as client:
        assert isinstance(client.app.state.registry.get("comfyui"), ComfyUIProvider)


def test_comfyui_disable_enable_locked_while_run_is_queued(
    client_no_runner: TestClient, comfy_checks: list[str]
) -> None:
    app = client_no_runner.app
    # PUT だと実行レーンが起動して Run が進むので、設定と登録簿に直接入れる
    with app.state.session_factory() as db:
        comfyui_connection.save_connection_url(db, COMFY_URL)
    app.state.registry.providers["comfyui"] = ComfyUIProvider(
        COMFY_URL, app.state.session_factory, app.state.settings.comfyui_timeout_seconds
    )
    workflow = _create_workflow(client_no_runner)
    run = client_no_runner.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": workflow["id"],
            "prompt": "a cat",
            "provider": "comfyui",
            "params": {},
        },
    )
    assert run.status_code == 202, run.text

    assert client_no_runner.post("/api/comfyui/connection/disable").status_code == 409
    assert client_no_runner.post("/api/comfyui/connection/enable").status_code == 409
    assert client_no_runner.get("/api/comfyui/status").json()["disabled"] is False
