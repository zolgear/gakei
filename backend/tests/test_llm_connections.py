"""LLM の接続先(ADR-0032。中身は ADR-0024 8章「接続先(一覧)」)。

- `/api/settings/llm-connections` の一覧・追加・変更・削除とキー
- 組み込みの `openai` は API 形式のほかは変えられず、削除もできない
- 使っている機能(`used_by`)と、使用中の接続先の削除の禁止
- キーは一部も返さない、権限(参照は全ログイン者、更新系は管理者だけ)
- 保存先の名前は変えていない(`annotation.connections`、`annotation_connection_key.<id>`)
- 古い `/api/settings/annotation/connections*` は無い
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.domain import api_key as api_key_domain
from app.domain import llm_connections
from app.domain.general_settings import _get_raw_value
from tests.conftest import login_as

BASE = "/api/settings/llm-connections"
LOCAL_URL = "http://127.0.0.1:11434/v1"


def add_llm_connection(
    client: TestClient,
    name: str = "Ollama",
    *,
    base_url: str = LOCAL_URL,
    api_style: str = "chat",
    **extra: Any,
) -> str:
    """接続先を足して id を返す(ほかのテストからも使う)。"""
    before = {c["id"] for c in client.get(BASE).json()["connections"]}
    response = client.post(
        BASE, json={"name": name, "base_url": base_url, "api_style": api_style, **extra}
    )
    assert response.status_code == 201, response.text
    connections = response.json()["connections"]
    created = [c for c in connections if c["id"] not in before]
    assert len(created) == 1
    assert connections[-1]["id"] == created[0]["id"]
    return created[0]["id"]


def _connection(body: dict, connection_id: str) -> dict:
    return next(c for c in body["connections"] if c["id"] == connection_id)


def _patch_annotation(client: TestClient, **values: Any) -> dict:
    response = client.patch("/api/settings/annotation", json=values)
    assert response.status_code == 200, response.text
    return response.json()


# -- 一覧と CRUD ------------------------------------------------------------------


def test_list_has_builtin_first(client: TestClient) -> None:
    response = client.get(BASE)
    assert response.status_code == 200
    connections = response.json()["connections"]
    assert [c["id"] for c in connections] == ["openai"]
    builtin = connections[0]
    assert builtin["builtin"] is True
    assert builtin["name"] == "OpenAI の設定"
    # 既定では自動タイトル・タグの「既定」の組が組み込みの接続先を使っている。
    assert builtin["used_by"] == ["annotation"]


def test_create_update_delete_connection(client: TestClient, data_dir: Path) -> None:
    connection_id = add_llm_connection(client, " Ollama ", api_key="sk-local-wxyz")
    body = client.get(BASE).json()
    assert _connection(body, connection_id) == {
        "id": connection_id,
        "name": "Ollama",
        "builtin": False,
        "base_url": LOCAL_URL,
        "api_style": "chat",
        "api_key_set": True,
        "used_by": [],
    }
    # キーは DB ではなく secrets.json に置き、API では返さない。保存先の名前は変えていない。
    secrets = json.loads((data_dir / "secrets.json").read_text())
    assert secrets[f"annotation_connection_key.{connection_id}"] == "sk-local-wxyz"
    assert "wxyz" not in json.dumps(body)
    with client.app.state.session_factory() as db:
        saved = _get_raw_value(db, "annotation.connections")
    assert isinstance(saved, list)
    assert [c["id"] for c in saved] == [connection_id]

    response = client.patch(
        f"{BASE}/{connection_id}",
        json={
            "name": "LiteLLM",
            "base_url": "https://llm.example.com/v1/",
            "api_style": "responses",
        },
    )
    assert response.status_code == 200, response.text
    view = _connection(response.json(), connection_id)
    assert (view["name"], view["base_url"], view["api_style"]) == (
        "LiteLLM",
        "https://llm.example.com/v1",
        "responses",
    )
    assert client.patch(f"{BASE}/nope", json={"name": "x"}).status_code == 404

    response = client.delete(f"{BASE}/{connection_id}")
    assert response.status_code == 200
    assert [c["id"] for c in response.json()["connections"]] == ["openai"]
    # キーも消える。
    assert not (data_dir / "secrets.json").exists() or (
        f"annotation_connection_key.{connection_id}"
        not in json.loads((data_dir / "secrets.json").read_text())
    )
    assert client.delete(f"{BASE}/{connection_id}").status_code == 404


def test_connection_key_set_and_delete(client: TestClient) -> None:
    connection_id = add_llm_connection(client)
    assert _connection(client.get(BASE).json(), connection_id)["api_key_set"] is False
    url = f"{BASE}/{connection_id}/api-key"
    response = client.put(url, json={"api_key": "sk-abcd-qrst"})
    assert response.status_code == 200
    assert "qrst" not in response.text
    assert _connection(response.json(), connection_id)["api_key_set"] is True
    assert "qrst" not in client.get(BASE).text
    assert client.put(url, json={"api_key": "  "}).status_code == 400
    response = client.delete(url)
    assert _connection(response.json(), connection_id)["api_key_set"] is False
    assert client.put(f"{BASE}/nope/api-key", json={"api_key": "x"}).status_code == 404
    assert client.delete(f"{BASE}/nope/api-key").status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"name": "", "base_url": LOCAL_URL},
        {"name": "x" * 101, "base_url": LOCAL_URL},
        {"name": "x", "base_url": "ftp://127.0.0.1/v1"},
        {"name": "x", "base_url": ""},
        {"name": "x", "base_url": "http://user:pw@127.0.0.1/v1"},
        {"name": "x", "base_url": LOCAL_URL, "api_style": "grpc"},
    ],
)
def test_create_connection_validation(client: TestClient, body: dict) -> None:
    response = client.post(BASE, json=body)
    assert response.status_code == 422, body
    assert [c["id"] for c in client.get(BASE).json()["connections"]] == ["openai"]


def test_connections_max(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_connections, "CONNECTIONS_MAX", 1)
    add_llm_connection(client)
    response = client.post(BASE, json={"name": "x", "base_url": LOCAL_URL})
    assert response.status_code == 422


def test_builtin_connection_is_read_only_except_api_style(client: TestClient) -> None:
    base = f"{BASE}/openai"
    for body in ({"name": "x"}, {"base_url": LOCAL_URL}, {"name": "x", "api_style": "chat"}):
        assert client.patch(base, json=body).status_code == 409, body
    assert client.delete(base).status_code == 409
    assert client.put(base + "/api-key", json={"api_key": "x"}).status_code == 409
    assert client.delete(base + "/api-key").status_code == 409

    response = client.patch(base, json={"api_style": "chat"})
    assert response.status_code == 200
    assert _connection(response.json(), "openai")["api_style"] == "chat"
    # 保存先の名前は変えていない。
    with client.app.state.session_factory() as db:
        assert _get_raw_value(db, "annotation.openai_api_style") == "chat"
    assert client.patch(base, json={"api_style": "grpc"}).status_code == 422


def test_builtin_connection_shows_openai_settings(client: TestClient, data_dir: Path) -> None:
    api_key_domain.write_file_key(data_dir, "sk-openai-uvwx")
    api_key_domain.write_file_base_url(data_dir, "http://127.0.0.1:4000/v1")
    response = client.get(BASE)
    view = _connection(response.json(), "openai")
    assert view["builtin"] is True
    assert view["base_url"] == "http://127.0.0.1:4000/v1"
    assert view["api_key_set"] is True
    assert "uvwx" not in response.text


# -- 使っている機能 -----------------------------------------------------------------


def test_connection_in_use_cannot_be_deleted(client: TestClient) -> None:
    connection_id = add_llm_connection(client)
    _patch_annotation(
        client, profiles={"comfyui": {"llm": {"connection_id": connection_id, "model": "m"}}}
    )
    body = client.get(BASE).json()
    assert _connection(body, connection_id)["used_by"] == ["annotation"]
    response = client.delete(f"{BASE}/{connection_id}")
    assert response.status_code == 409
    assert "自動タイトル・タグ" in response.json()["detail"]
    assert connection_id in {c["id"] for c in client.get(BASE).json()["connections"]}

    # 「既定と同じ」に戻せば消せる。
    _patch_annotation(client, profiles={"comfyui": {"llm": None}})
    assert _connection(client.get(BASE).json(), connection_id)["used_by"] == []
    response = client.delete(f"{BASE}/{connection_id}")
    assert response.status_code == 200


def test_usage_comes_from_registered_functions(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """接続先の側は機能を知らず、登録された関数の答えだけを見る。"""
    connection_id = add_llm_connection(client)
    monkeypatch.setitem(llm_connections._usage_functions, "annotation", lambda db: {connection_id})
    body = client.get(BASE).json()
    assert _connection(body, connection_id)["used_by"] == ["annotation"]
    assert _connection(body, "openai")["used_by"] == []
    assert client.delete(f"{BASE}/{connection_id}").status_code == 409


# -- 権限とキー -------------------------------------------------------------------


_ADMIN_CASES: list[tuple[str, str, dict | None]] = [
    ("POST", BASE, {"name": "x", "base_url": LOCAL_URL}),
    ("PATCH", f"{BASE}/openai", {"api_style": "chat"}),
    ("DELETE", f"{BASE}/nope", None),
    ("PUT", f"{BASE}/nope/api-key", {"api_key": "x"}),
    ("DELETE", f"{BASE}/nope/api-key", None),
]


@pytest.mark.parametrize(("method", "path", "body"), _ADMIN_CASES)
def test_updates_are_admin_only(
    client_oidc: TestClient, method: str, path: str, body: dict | None
) -> None:
    login_as(client_oidc, "user@example.com")
    response = client_oidc.request(method, path, json=body)
    assert response.status_code == 403, response.text
    login_as(client_oidc, "admin@example.com")
    response = client_oidc.request(method, path, json=body)
    assert response.status_code != 403, response.text


def test_list_requires_login(client_oidc: TestClient) -> None:
    assert client_oidc.get(BASE).status_code == 401


def test_non_admin_can_list_but_does_not_see_key(client_oidc: TestClient) -> None:
    login_as(client_oidc, "admin@example.com")
    connection_id = add_llm_connection(client_oidc, api_key="sk-secret-mnop")
    login_as(client_oidc, "user@example.com")
    response = client_oidc.get(BASE)
    assert response.status_code == 200
    assert _connection(response.json(), connection_id)["api_key_set"] is True
    assert "mnop" not in response.text


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/settings/annotation/connections"),
        ("PATCH", "/api/settings/annotation/connections/openai"),
        ("DELETE", "/api/settings/annotation/connections/x"),
        ("PUT", "/api/settings/annotation/connections/x/api-key"),
        ("DELETE", "/api/settings/annotation/connections/x/api-key"),
    ],
)
def test_old_annotation_connection_api_is_gone(client: TestClient, method: str, path: str) -> None:
    response = client.request(method, path, json={})
    assert response.status_code in (404, 405)


def test_annotation_settings_no_longer_lists_connections(client: TestClient) -> None:
    local = add_llm_connection(client)
    body = client.get("/api/settings/annotation").json()
    assert "connections" not in body
    # 自動タイトル・タグ固有の、接続先ごとの呼び出し回数は annotation 側に残す。
    assert [c["connection_id"] for c in body["connection_calls"]] == ["openai", local]
    assert all(c["calls_last_hour"] == 0 for c in body["connection_calls"])
