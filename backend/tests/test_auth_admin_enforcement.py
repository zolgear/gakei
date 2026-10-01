"""ADR-0019: プロバイダー関連の更新系エンドポイントは管理者限定、GET は全ログイン者に許す。"""

from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from app.api import settings as settings_api
from tests.comfyui_graphs import T2I_GRAPH
from tests.conftest import login_as


async def _validator_ok(api_key: str, base_url: str | None) -> None:
    return None


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


def _workflow_create_body(name: str = "t2i") -> dict:
    return {
        "name": name,
        "operation": "generate",
        "template": copy.deepcopy(T2I_GRAPH),
        "bindings": _t2i_bindings_body(),
        "exposed_params": [],
    }


def _login_user(client: TestClient) -> None:
    login_as(client, "user@example.com", "一般ユーザー")


def _login_admin(client: TestClient) -> None:
    login_as(client, "admin@example.com", "管理者")


def _admin_workflow_id(client: TestClient) -> str:
    """admin としてワークフローを1件作り、その id を返す(update/delete の前提)。
    呼び出し後はログアウトしておく(テスト側で改めて必要なロールでログインし直す)。
    """
    _login_admin(client)
    response = client.post("/api/comfyui/workflows", json=_workflow_create_body())
    assert response.status_code == 201, response.text
    client.post("/api/auth/logout")
    return response.json()["id"]


# (method, path を組み立てる関数, body) のリスト。path は workflow_id が要る場合だけ関数にする。
_SIMPLE_CASES: list[tuple[str, str, dict | None]] = [
    ("PUT", "/api/settings/openai-key", {"api_key": "sk-test-1234"}),
    ("DELETE", "/api/settings/openai-key", None),
    ("PUT", "/api/settings/openai-base-url", {"base_url": "http://127.0.0.1:9"}),
    ("DELETE", "/api/settings/openai-base-url", None),
    ("PATCH", "/api/settings/general", {}),
    ("POST", "/api/settings/llm-connections", {"name": "x", "base_url": "http://127.0.0.1:9"}),
    ("PATCH", "/api/settings/llm-connections/openai", {"api_style": "chat"}),
    ("PUT", "/api/comfyui/connection", {"url": "http://127.0.0.1:8188"}),
    ("DELETE", "/api/comfyui/connection", None),
    ("POST", "/api/comfyui/connection/test", {}),
    (
        "POST",
        "/api/comfyui/workflows/analyze",
        {"template": {"1": {"class_type": "Foo", "inputs": {}}}},
    ),
    ("POST", "/api/comfyui/workflows", _workflow_create_body("t2i-for-user-403")),
]


@pytest.mark.parametrize(("method", "path", "body"), _SIMPLE_CASES)
def test_admin_only_endpoint_forbidden_for_user(
    client_oidc: TestClient, method: str, path: str, body: dict | None
) -> None:
    _login_user(client_oidc)
    response = client_oidc.request(method, path, json=body)
    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "この操作には管理者権限が必要です。"


@pytest.mark.parametrize(("method", "path", "body"), _SIMPLE_CASES)
def test_admin_only_endpoint_not_forbidden_for_admin(
    client_oidc: TestClient, method: str, path: str, body: dict | None
) -> None:
    client_oidc.app.dependency_overrides[settings_api.get_key_validator] = lambda: _validator_ok
    try:
        _login_admin(client_oidc)
        response = client_oidc.request(method, path, json=body)
    finally:
        client_oidc.app.dependency_overrides.pop(settings_api.get_key_validator, None)
    assert response.status_code != 403, response.text


def test_update_workflow_forbidden_for_user_allowed_for_admin(client_oidc: TestClient) -> None:
    workflow_id = _admin_workflow_id(client_oidc)

    _login_user(client_oidc)
    response = client_oidc.patch(
        f"/api/comfyui/workflows/{workflow_id}", json={"name": "renamed-by-user"}
    )
    assert response.status_code == 403
    client_oidc.post("/api/auth/logout")

    _login_admin(client_oidc)
    response = client_oidc.patch(
        f"/api/comfyui/workflows/{workflow_id}", json={"name": "renamed-by-admin"}
    )
    assert response.status_code == 200, response.text


def test_delete_workflow_forbidden_for_user_allowed_for_admin(client_oidc: TestClient) -> None:
    workflow_id = _admin_workflow_id(client_oidc)

    _login_user(client_oidc)
    response = client_oidc.delete(f"/api/comfyui/workflows/{workflow_id}")
    assert response.status_code == 403
    client_oidc.post("/api/auth/logout")

    _login_admin(client_oidc)
    response = client_oidc.delete(f"/api/comfyui/workflows/{workflow_id}")
    assert response.status_code == 204, response.text


# -- GET は全ログイン者に許す ---------------------------------------------------


def test_get_endpoints_are_allowed_for_regular_user(client_oidc: TestClient) -> None:
    _login_user(client_oidc)
    for path in (
        "/api/settings/openai-key",
        "/api/settings/openai-base-url",
        "/api/settings/general",
        "/api/settings/llm-connections",
        "/api/comfyui/status",
        "/api/comfyui/workflows",
    ):
        response = client_oidc.get(path)
        assert response.status_code == 200, (path, response.text)


def test_openai_key_not_exposed_even_to_admin(client_oidc: TestClient) -> None:
    # キーは管理者にも一部(末尾など)を返さない。設定済みかどうかと出どころだけ。
    from app.domain.api_key import write_file_key

    write_file_key(client_oidc.app.state.settings.data_dir, "sk-visible-only-to-admin")

    _login_admin(client_oidc)
    admin_response = client_oidc.get("/api/settings/openai-key")
    assert admin_response.json()["configured"] is True
    assert "dmin" not in admin_response.text
    client_oidc.post("/api/auth/logout")

    _login_user(client_oidc)
    user_response = client_oidc.get("/api/settings/openai-key")
    assert user_response.json()["configured"] is True
    assert "dmin" not in user_response.text
