"""ComfyUI の接続設定(ADR-0013 7章)。既定は無効、設定画面での接続・切り離し、
再起動なしの反映、DB 設定と環境変数の優先順位を確かめる。

実物の ComfyUI には一切接続しない。`app.providers.comfyui.provider.check_available` を
monkeypatch し、実行は `tests/comfyui_fake.py` の偽サーバーを使う。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.providers.comfyui.client import ComfyUIClient
from app.providers.comfyui.provider import ComfyUIProvider
from tests.comfyui_fake import FakeComfyUI, unavailable_ws_connect
from tests.comfyui_graphs import T2I_GRAPH, clone
from tests.conftest import make_png_bytes, wait_for_run_terminal


@contextmanager
def _app(
    monkeypatch: pytest.MonkeyPatch, data_dir: Path, *, comfyui_url: str | None = None
) -> Iterator[TestClient]:
    """`DATA_DIR` を固定して(必要なら)アプリを起動・終了する。

    複数回呼ぶことで「再起動」を模す(同じ DATA_DIR = 同じ SQLite なので、DB に保存した
    接続設定は引き継がれる)。
    """
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("FAKE_PROVIDER", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    if comfyui_url is None:
        monkeypatch.setenv("COMFYUI_URL", "")
    else:
        monkeypatch.setenv("COMFYUI_URL", comfyui_url)

    from app.main import create_app

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


def _mark_available(monkeypatch: pytest.MonkeyPatch, *, available: bool = True) -> None:
    """`ComfyUIProvider.availability()`(Run 作成・capabilities が使う)を差し替える。"""
    monkeypatch.setattr(
        "app.providers.comfyui.provider.check_available",
        lambda base_url, timeout=1.0: (available, None if available else "使用できません", {}),
    )


def _mark_status_check_available(
    monkeypatch: pytest.MonkeyPatch, *, available: bool = True
) -> None:
    """`/api/comfyui/status` と `/api/comfyui/connection/test` が使う `check_available` を
    差し替える。`app/api/comfyui.py` はこの名前を直接 import しているため、`_mark_available`
    (`app.providers.comfyui.provider` 側)とは別の束縛先を差し替える必要がある。
    """
    monkeypatch.setattr(
        "app.api.comfyui.check_available",
        lambda base_url, timeout=1.0: (
            available,
            None if available else "使用できません",
            {"system": {"comfyui_version": "1.0.0-test"}, "devices": []},
        ),
    )


def _has_comfyui_capability(client: TestClient) -> bool:
    body = client.get("/api/capabilities").json()
    return any(p["provider"] == "comfyui" for p in body["providers"])


def _t2i_bindings_body() -> dict:
    return {
        "prompt": {"node": "6", "input": "text"},
        "negative_prompt": {"node": "7", "input": "text"},
        "seed": [{"node": "3", "input": "seed"}],
        "width": {"node": "5", "input": "width"},
        "height": {"node": "5", "input": "height"},
        "batch_size": {"node": "5", "input": "batch_size"},
        "outputs": ["9"],
    }


def _create_t2i_workflow(client: TestClient, name: str = "t2i サンプル") -> dict:
    response = client.post(
        "/api/comfyui/workflows",
        json={
            "name": name,
            "operation": "generate",
            "template": clone(T2I_GRAPH),
            "bindings": _t2i_bindings_body(),
            "exposed_params": [],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _install_fake_comfyui(client: TestClient, fake: FakeComfyUI, url: str) -> None:
    """レジストリの `comfyui` プロバイダーを、偽サーバーに接続するものへ差し替える。"""
    session_factory = client.app.state.session_factory

    def client_factory() -> ComfyUIClient:
        return ComfyUIClient(
            url, http=fake.make_async_client(), ws_connect=unavailable_ws_connect()
        )

    settings = client.app.state.settings
    provider = ComfyUIProvider(
        url, session_factory, settings.comfyui_timeout_seconds, client_factory=client_factory
    )
    client.app.state.registry.providers["comfyui"] = provider


def _configure_success(fake: FakeComfyUI) -> None:
    fake.set_history(
        "prompt-1",
        {
            "outputs": {
                "9": {"images": [{"filename": "out.png", "subfolder": "", "type": "output"}]}
            },
            "status": {"status_str": "success", "completed": True},
        },
    )
    fake.add_output_file("out.png", "", "output", make_png_bytes())


# -- 既定は無効 -----------------------------------------------------------------


def test_disabled_by_default_no_comfyui_in_capabilities(
    monkeypatch: pytest.MonkeyPatch, data_dir: Path
) -> None:
    with _app(monkeypatch, data_dir) as client:
        assert _has_comfyui_capability(client) is False
        status = client.get("/api/comfyui/status").json()
        assert status == {
            "url": None,
            "enabled": False,
            "available": False,
            "reason": None,
            "version": None,
            "device": None,
            "source": "none",
            "locked": False,
            "loopback": None,
        }


def test_env_default_enables_when_no_db_setting(
    monkeypatch: pytest.MonkeyPatch, data_dir: Path
) -> None:
    with _app(monkeypatch, data_dir, comfyui_url="http://127.0.0.1:8188") as client:
        assert _has_comfyui_capability(client) is True
        status = client.get("/api/comfyui/status").json()
        assert status["source"] == "env"
        assert status["url"] == "http://127.0.0.1:8188"
        assert status["loopback"] is True


# -- 接続 / 変更 -----------------------------------------------------------------


def test_put_connects_without_restart_and_run_completes_on_new_lane(
    monkeypatch: pytest.MonkeyPatch, data_dir: Path
) -> None:
    with _app(monkeypatch, data_dir) as client:
        assert _has_comfyui_capability(client) is False

        response = client.put("/api/comfyui/connection", json={"url": "http://127.0.0.1:8189"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["url"] == "http://127.0.0.1:8189"
        assert body["enabled"] is True
        assert body["source"] == "setting"

        assert _has_comfyui_capability(client) is True

        _mark_available(monkeypatch)
        workflow = _create_t2i_workflow(client)
        fake = FakeComfyUI()
        _configure_success(fake)
        _install_fake_comfyui(client, fake, "http://127.0.0.1:8189")

        run_response = client.post(
            "/api/runs",
            json={
                "operation": "generate",
                "model": workflow["id"],
                "prompt": "a cat",
                "provider": "comfyui",
                "params": {},
            },
        )
        assert run_response.status_code == 202, run_response.text
        run_id = run_response.json()["id"]

        detail = wait_for_run_terminal(client, run_id)
        assert detail["status"] == "succeeded", detail


# -- 設定は環境変数より常に優先する(再起動をまたいでも) ----------------------------


def test_db_setting_beats_env_across_restart(
    monkeypatch: pytest.MonkeyPatch, data_dir: Path
) -> None:
    with _app(monkeypatch, data_dir, comfyui_url="http://127.0.0.1:8188") as client:
        response = client.put("/api/comfyui/connection", json={"url": "http://127.0.0.1:8189"})
        assert response.status_code == 200, response.text

    # 「再起動」: 同じ DATA_DIR で、env は別の値にしてアプリを作り直す。
    with _app(monkeypatch, data_dir, comfyui_url="http://127.0.0.1:9999") as client:
        status = client.get("/api/comfyui/status").json()
        assert status["source"] == "setting"
        assert status["url"] == "http://127.0.0.1:8189"
        assert _has_comfyui_capability(client) is True


def test_detached_state_beats_env_across_restart(
    monkeypatch: pytest.MonkeyPatch, data_dir: Path
) -> None:
    with _app(monkeypatch, data_dir, comfyui_url="http://127.0.0.1:8188") as client:
        connect_response = client.put(
            "/api/comfyui/connection", json={"url": "http://127.0.0.1:8188"}
        )
        assert connect_response.status_code == 200, connect_response.text

        detach_response = client.delete("/api/comfyui/connection")
        assert detach_response.status_code == 200, detach_response.text
        assert detach_response.json()["enabled"] is False

    # 「再起動」: env に値があっても、切り離した設定が優先されて無効のまま。
    with _app(monkeypatch, data_dir, comfyui_url="http://127.0.0.1:8188") as client:
        status = client.get("/api/comfyui/status").json()
        assert status["source"] == "setting"
        assert status["enabled"] is False
        assert status["url"] is None
        assert _has_comfyui_capability(client) is False


# -- 切り離し ---------------------------------------------------------------------


def test_delete_detaches_capabilities_drops_but_workflows_remain(
    monkeypatch: pytest.MonkeyPatch, data_dir: Path
) -> None:
    with _app(monkeypatch, data_dir) as client:
        connect_response = client.put(
            "/api/comfyui/connection", json={"url": "http://127.0.0.1:8189"}
        )
        assert connect_response.status_code == 200, connect_response.text
        workflow = _create_t2i_workflow(client)

        detach_response = client.delete("/api/comfyui/connection")
        assert detach_response.status_code == 200, detach_response.text
        assert detach_response.json()["enabled"] is False

        assert _has_comfyui_capability(client) is False

        listed = client.get("/api/comfyui/workflows").json()["items"]
        assert workflow["id"] in [w["id"] for w in listed]


# -- 実行中は 409 -----------------------------------------------------------------


def test_connection_locked_returns_409_while_comfyui_run_is_queued(
    client_no_runner: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # runner のループを止めているので、直接レジストリへ差し込んで queued のまま固定する
    # (PUT 経由だとレーンが起動し、テストの前提(実行させない)が崩れるため)。
    _mark_available(monkeypatch)
    settings = client_no_runner.app.state.settings
    session_factory = client_no_runner.app.state.session_factory
    client_no_runner.app.state.registry.providers["comfyui"] = ComfyUIProvider(
        "http://127.0.0.1:8189", session_factory, settings.comfyui_timeout_seconds
    )

    workflow = _create_t2i_workflow(client_no_runner)
    run_response = client_no_runner.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": workflow["id"],
            "prompt": "a cat",
            "provider": "comfyui",
            "params": {},
        },
    )
    assert run_response.status_code == 202, run_response.text
    assert client_no_runner.get(f"/api/runs/{run_response.json()['id']}").json()["status"] == (
        "queued"
    )

    put_response = client_no_runner.put(
        "/api/comfyui/connection", json={"url": "http://127.0.0.1:8199"}
    )
    assert put_response.status_code == 409

    delete_response = client_no_runner.delete("/api/comfyui/connection")
    assert delete_response.status_code == 409

    # 接続テストはロック中でも常に許可する。
    test_response = client_no_runner.post(
        "/api/comfyui/connection/test", json={"url": "http://127.0.0.1:8189"}
    )
    assert test_response.status_code == 200, test_response.text

    status = client_no_runner.get("/api/comfyui/status").json()
    assert status["locked"] is True


# -- URL の検証 -------------------------------------------------------------------


def test_put_rejects_bad_scheme(monkeypatch: pytest.MonkeyPatch, data_dir: Path) -> None:
    with _app(monkeypatch, data_dir) as client:
        response = client.put("/api/comfyui/connection", json={"url": "ftp://example.com"})
        assert response.status_code == 422


def test_put_rejects_url_without_host(monkeypatch: pytest.MonkeyPatch, data_dir: Path) -> None:
    with _app(monkeypatch, data_dir) as client:
        response = client.put("/api/comfyui/connection", json={"url": "http://"})
        assert response.status_code == 422


def test_put_rejects_non_loopback_without_allow_flag(
    monkeypatch: pytest.MonkeyPatch, data_dir: Path
) -> None:
    with _app(monkeypatch, data_dir) as client:
        response = client.put("/api/comfyui/connection", json={"url": "http://192.168.1.50:8188"})
        assert response.status_code == 422
        assert _has_comfyui_capability(client) is False


def test_put_accepts_non_loopback_with_allow_flag(
    monkeypatch: pytest.MonkeyPatch, data_dir: Path
) -> None:
    with _app(monkeypatch, data_dir) as client:
        response = client.put(
            "/api/comfyui/connection",
            json={"url": "http://192.168.1.50:8188", "allow_non_loopback": True},
        )
        assert response.status_code == 200, response.text
        assert response.json()["loopback"] is False
        assert _has_comfyui_capability(client) is True


# -- 接続テスト -------------------------------------------------------------------


def test_connection_test_does_not_save_and_reports_unavailable_for_unreachable_url(
    monkeypatch: pytest.MonkeyPatch, data_dir: Path
) -> None:
    with _app(monkeypatch, data_dir) as client:
        response = client.post(
            "/api/comfyui/connection/test", json={"url": "http://127.0.0.1:8189"}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["available"] is False
        assert body["loopback"] is True

        # 設定は変わっていない(既定のまま無効)。
        assert _has_comfyui_capability(client) is False
        status = client.get("/api/comfyui/status").json()
        assert status["source"] == "none"


def test_connection_test_without_url_uses_current_effective_url(
    monkeypatch: pytest.MonkeyPatch, data_dir: Path
) -> None:
    with _app(monkeypatch, data_dir, comfyui_url="http://127.0.0.1:8188") as client:
        _mark_status_check_available(monkeypatch)
        response = client.post("/api/comfyui/connection/test", json={})
        assert response.status_code == 200, response.text
        assert response.json()["url"] == "http://127.0.0.1:8188"
        assert response.json()["available"] is True


def test_connection_test_returns_422_when_no_url_available(
    monkeypatch: pytest.MonkeyPatch, data_dir: Path
) -> None:
    with _app(monkeypatch, data_dir) as client:
        response = client.post("/api/comfyui/connection/test", json={})
        assert response.status_code == 422


def test_connection_test_allows_non_loopback_without_flag(
    monkeypatch: pytest.MonkeyPatch, data_dir: Path
) -> None:
    with _app(monkeypatch, data_dir) as client:
        response = client.post(
            "/api/comfyui/connection/test", json={"url": "http://192.168.1.50:8188"}
        )
        assert response.status_code == 200, response.text
        assert response.json()["loopback"] is False
