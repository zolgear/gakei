"""`app/api/sdwebui.py` の HTTP テスト(ADR-0038 6章)。偽の WebUI だけを使う。

権限(一般ユーザーの 403、未ログインの 401)は `tests/test_auth_authz_matrix.py` が
`/api/sdwebui/*` も含めて洗い出す。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.sdwebui_fake import FAKE_PATH_ROOT, FakeSdWebui, install_fake_factories

LOCAL_URL = "http://127.0.0.1:7860"
SECRET = "secret-pass-123"


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeSdWebui:
    """API が作るクライアントとプロバイダーを、偽の WebUI につなぐ。"""
    fake = FakeSdWebui()
    install_fake_factories(monkeypatch, fake)
    return fake


def test_status_disabled_by_default(client: TestClient) -> None:
    response = client.get("/api/sdwebui/status")
    assert response.status_code == 200
    assert response.json() == {
        "url": None,
        "enabled": False,
        "available": False,
        "reason": None,
        "reason_message": None,
        "source": "none",
        "locked": False,
        "loopback": None,
        "credentials_set": False,
        "flavor": None,
        "checkpoint_count": None,
    }


def test_connect_and_detach(client: TestClient, fake: FakeSdWebui) -> None:
    response = client.put("/api/sdwebui/connection", json={"url": LOCAL_URL + "/"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["url"] == LOCAL_URL
    assert body["enabled"] is True
    assert body["available"] is True
    assert body["source"] == "setting"
    assert body["loopback"] is True
    assert body["flavor"] == "forge"
    assert body["checkpoint_count"] == 2
    # パスや options の中身は返さない
    assert FAKE_PATH_ROOT not in response.text

    caps = client.get("/api/capabilities").json()
    entry = next(p for p in caps["providers"] if p["provider"] == "sdwebui")
    assert entry["available"] is True
    assert [m["model"] for m in entry["models"]] == ["model-a", "model-b"]
    assert FAKE_PATH_ROOT not in json.dumps(caps)

    response = client.delete("/api/sdwebui/connection")
    assert response.status_code == 200
    assert response.json()["enabled"] is False
    caps = client.get("/api/capabilities").json()
    assert all(p["provider"] != "sdwebui" for p in caps["providers"])


def test_status_reports_api_not_enabled(client: TestClient, fake: FakeSdWebui) -> None:
    fake.api_enabled = False
    response = client.put("/api/sdwebui/connection", json={"url": LOCAL_URL})
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is False
    assert body["reason"] == "apiNotEnabled"
    assert "--api" in body["reason_message"]


def test_non_loopback_requires_confirmation(client: TestClient, fake: FakeSdWebui) -> None:
    url = "http://192.0.2.10:7860"
    assert client.put("/api/sdwebui/connection", json={"url": url}).status_code == 422
    response = client.put("/api/sdwebui/connection", json={"url": url, "allow_non_loopback": True})
    assert response.status_code == 200
    assert response.json()["loopback"] is False


@pytest.mark.parametrize(
    "url", ["http://user:pw@127.0.0.1:7860", "http://127.0.0.1:7860?x=1", "file:///tmp"]
)
def test_connection_rejects_bad_urls(client: TestClient, fake: FakeSdWebui, url: str) -> None:
    response = client.put("/api/sdwebui/connection", json={"url": url})
    assert response.status_code == 422
    assert "pw" not in response.json()["detail"]


def test_credentials_are_never_returned(
    client: TestClient, fake: FakeSdWebui, data_dir: Path
) -> None:
    fake.credentials = ("user-x", SECRET)
    client.put("/api/sdwebui/connection", json={"url": LOCAL_URL})
    assert client.get("/api/sdwebui/status").json()["reason"] == "unauthorized"

    response = client.put(
        "/api/sdwebui/credentials", json={"username": "user-x", "password": SECRET}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["credentials_set"] is True
    assert body["available"] is True
    assert SECRET not in response.text
    assert "user-x" not in response.text

    stored = json.loads((data_dir / "secrets.json").read_text(encoding="utf-8"))
    assert stored["sdwebui_auth_password"] == SECRET

    for path in ("/api/sdwebui/status", "/api/capabilities", "/api/settings/general"):
        text = client.get(path).text
        assert SECRET not in text

    response = client.delete("/api/sdwebui/credentials")
    assert response.status_code == 200
    assert response.json()["credentials_set"] is False


def test_credentials_validation(client: TestClient) -> None:
    response = client.put("/api/sdwebui/credentials", json={"username": "a:b", "password": "x"})
    assert response.status_code == 422


def test_connection_test_with_explicit_credentials(client: TestClient, fake: FakeSdWebui) -> None:
    fake.credentials = ("user-x", SECRET)
    response = client.post(
        "/api/sdwebui/connection/test",
        json={"url": LOCAL_URL, "username": "user-x", "password": SECRET},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["available"] is True
    assert body["checkpoint_count"] == 2
    assert SECRET not in response.text

    response = client.post("/api/sdwebui/connection/test", json={"url": LOCAL_URL})
    assert response.json()["reason"] == "unauthorized"

    # 片方だけは 422。設定は変わらない。
    response = client.post(
        "/api/sdwebui/connection/test", json={"url": LOCAL_URL, "username": "user-x"}
    )
    assert response.status_code == 422
    assert client.get("/api/sdwebui/status").json()["enabled"] is False


def test_connection_test_without_url(client: TestClient) -> None:
    response = client.post("/api/sdwebui/connection/test", json={})
    assert response.status_code == 422


def test_refresh(client: TestClient, fake: FakeSdWebui) -> None:
    assert client.post("/api/sdwebui/refresh").status_code == 409  # 未接続
    client.put("/api/sdwebui/connection", json={"url": LOCAL_URL})
    fake.checkpoints = fake.checkpoints[:1]
    response = client.post("/api/sdwebui/refresh")
    assert response.status_code == 200
    assert fake.refresh_count == 1
    assert response.json()["checkpoint_count"] == 1
    caps = client.get("/api/capabilities").json()
    entry = next(p for p in caps["providers"] if p["provider"] == "sdwebui")
    assert [m["model"] for m in entry["models"]] == ["model-a"]


def test_general_settings_sdwebui_timeout(client: TestClient) -> None:
    body = client.get("/api/settings/general").json()
    assert body["sdwebui_timeout_seconds"] == {"value": 600, "source": "default", "default": 600}
    response = client.patch("/api/settings/general", json={"sdwebui_timeout_seconds": 120})
    assert response.status_code == 200
    assert response.json()["sdwebui_timeout_seconds"]["value"] == 120
    assert response.json()["comfyui_timeout_seconds"]["value"] == 1800
    assert (
        client.patch("/api/settings/general", json={"sdwebui_timeout_seconds": 30}).status_code
        == 422
    )
    response = client.patch("/api/settings/general", json={"sdwebui_timeout_seconds": None})
    assert response.json()["sdwebui_timeout_seconds"]["source"] == "default"


def test_changes_locked_while_run_is_queued(
    client_no_runner: TestClient, fake: FakeSdWebui
) -> None:
    app = client_no_runner.app
    from app.api.sdwebui import _make_provider

    app.state.registry.providers["sdwebui"] = _make_provider(
        LOCAL_URL, app.state.settings, app.state.session_factory
    )
    run_response = client_no_runner.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": "model-a",
            "prompt": "a cat",
            "provider": "sdwebui",
            "params": {},
        },
    )
    assert run_response.status_code == 202, run_response.text

    assert client_no_runner.get("/api/sdwebui/status").json()["locked"] is True
    assert (
        client_no_runner.put("/api/sdwebui/connection", json={"url": LOCAL_URL}).status_code == 409
    )
    assert client_no_runner.delete("/api/sdwebui/connection").status_code == 409
    assert (
        client_no_runner.put(
            "/api/sdwebui/credentials", json={"username": "u", "password": "p"}
        ).status_code
        == 409
    )
    assert client_no_runner.delete("/api/sdwebui/credentials").status_code == 409
    assert client_no_runner.post("/api/sdwebui/refresh").status_code == 409
    # 接続テストはロック中でも使える
    assert (
        client_no_runner.post("/api/sdwebui/connection/test", json={"url": LOCAL_URL}).status_code
        == 200
    )
