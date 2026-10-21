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
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.domain import api_key as api_key_domain
from app.domain import llm_connections
from app.domain.general_settings import _get_raw_value
from app.i18n import t
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
        # 接続先(ADR-0051 3章)。決めていなければ既定の external。
        "locality": "external",
        "locality_source": "default",
        # 構造化出力の対応。既定は自動で、公式でない接続先は対応とみなさない。
        "structured_outputs": "auto",
        "structured_outputs_supported": False,
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
            # オリジンが変わるので、キーを入れ直す
            "api_key": "sk-remote-abcd",
        },
    )
    assert response.status_code == 200, response.text
    secrets = json.loads((data_dir / "secrets.json").read_text())
    assert secrets[f"annotation_connection_key.{connection_id}"] == "sk-remote-abcd"
    assert "abcd" not in response.text
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


def test_base_url_origin_change_requires_key_reentry(client: TestClient, data_dir: Path) -> None:
    """保存済みのキーを、保存したときと別のオリジンへ送らない(入れ直させる)。"""
    connection_id = add_llm_connection(client, api_key="sk-local-wxyz")
    secret_field = f"annotation_connection_key.{connection_id}"

    # 別のオリジン(ホスト・ポート・スキームのどれかが違う)で、キーを入れ直さなければ 422
    for url in (
        "https://llm.example.com/v1",
        "http://127.0.0.1:11435/v1",
        "https://127.0.0.1:11434/v1",
    ):
        response = client.patch(f"{BASE}/{connection_id}", json={"base_url": url})
        assert response.status_code == 422, url
        assert response.json()["detail"] == t("settings.llmConnections.reenterKey")
    # 断ったときは何も変えない
    view = _connection(client.get(BASE).json(), connection_id)
    assert view["base_url"] == LOCAL_URL
    secrets = json.loads((data_dir / "secrets.json").read_text())
    assert secrets[secret_field] == "sk-local-wxyz"

    # 同じオリジンでパスだけ変えるなら、キーはそのまま
    response = client.patch(
        f"{BASE}/{connection_id}", json={"base_url": "http://127.0.0.1:11434/api/v1"}
    )
    assert response.status_code == 200, response.text
    assert _connection(response.json(), connection_id)["api_key_set"] is True
    secrets = json.loads((data_dir / "secrets.json").read_text())
    assert secrets[secret_field] == "sk-local-wxyz"

    # キーを消せば、キーなしでオリジンを変えられる
    assert client.delete(f"{BASE}/{connection_id}/api-key").status_code == 200
    response = client.patch(
        f"{BASE}/{connection_id}", json={"base_url": "https://llm.example.com/v1"}
    )
    assert response.status_code == 200, response.text
    assert _connection(response.json(), connection_id)["api_key_set"] is False


def test_builtin_connection_rejects_api_key_in_update(client: TestClient) -> None:
    response = client.patch(f"{BASE}/openai", json={"api_key": "sk-x"})
    assert response.status_code == 409


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


# -- 構造化出力の対応(2026-10-21) -------------------------------------------------------


def test_structured_outputs_on_user_connection(client: TestClient) -> None:
    connection_id = add_llm_connection(client)
    view = _connection(client.get(BASE).json(), connection_id)
    # 既定は自動。公式でない接続先は対応とみなさない。
    assert view["structured_outputs"] == "auto"
    assert view["structured_outputs_supported"] is False
    # 自動のときは保存先に書かない。
    with client.app.state.session_factory() as db:
        assert "structured_outputs" not in _get_raw_value(db, "annotation.connections")[0]

    response = client.patch(f"{BASE}/{connection_id}", json={"structured_outputs": "supported"})
    assert response.status_code == 200, response.text
    view = _connection(response.json(), connection_id)
    assert view["structured_outputs"] == "supported"
    assert view["structured_outputs_supported"] is True
    with client.app.state.session_factory() as db:
        saved = _get_raw_value(db, "annotation.connections")[0]
        assert saved["structured_outputs"] == "supported"
        assert llm_connections.connection_supports_structured_outputs(
            db, connection_id, client.app.state.settings
        )

    for bad in ("yes", None, True):
        response = client.patch(f"{BASE}/{connection_id}", json={"structured_outputs": bad})
        assert response.status_code == 422, bad
    # 追加のときにも指定できる。
    other = add_llm_connection(client, "proxy", structured_outputs="supported")
    assert _connection(client.get(BASE).json(), other)["structured_outputs_supported"] is True
    bad_create = {"name": "x", "base_url": LOCAL_URL, "structured_outputs": "maybe"}
    assert client.post(BASE, json=bad_create).status_code == 422


def test_structured_outputs_on_builtin_connection(client: TestClient, data_dir: Path) -> None:
    base = f"{BASE}/openai"
    # Base URL を変えていなければ(OpenAI 本体)、自動で対応とみなす。
    view = _connection(client.get(BASE).json(), "openai")
    assert (view["structured_outputs"], view["structured_outputs_supported"]) == ("auto", True)

    # OpenAI 互換のプロキシに向けると、自動では対応とみなさない。
    api_key_domain.write_file_base_url(data_dir, "http://127.0.0.1:4000/v1")
    view = _connection(client.get(BASE).json(), "openai")
    assert view["structured_outputs_supported"] is False

    response = client.patch(base, json={"structured_outputs": "supported"})
    assert response.status_code == 200, response.text
    assert _connection(response.json(), "openai")["structured_outputs_supported"] is True
    with client.app.state.session_factory() as db:
        assert _get_raw_value(db, llm_connections.OPENAI_STRUCTURED_OUTPUTS_KEY) == "supported"

    # 「対応しない」は公式の接続先でも対応とみなさない。
    api_key_domain.delete_file_base_url(data_dir)
    response = client.patch(base, json={"structured_outputs": "unsupported"})
    assert _connection(response.json(), "openai")["structured_outputs_supported"] is False
    assert client.patch(base, json={"structured_outputs": None}).status_code == 422
    # 不正な値があれば、ほかの項目も保存しない。
    response = client.patch(base, json={"api_style": "chat", "structured_outputs": "x"})
    assert response.status_code == 422
    assert _connection(client.get(BASE).json(), "openai")["api_style"] == "responses"


def test_supports_structured_outputs_hosts(client: TestClient) -> None:
    settings = client.app.state.settings
    official = llm_connections.ConnectionConfig(
        id="a", name="a", base_url="https://api.openai.com/v1"
    )
    local = llm_connections.ConnectionConfig(id="b", name="b", base_url=LOCAL_URL)
    assert llm_connections.supports_structured_outputs(official, settings) is True
    assert llm_connections.supports_structured_outputs(local, settings) is False
    assert (
        llm_connections.supports_structured_outputs(
            replace(official, structured_outputs="unsupported"), settings
        )
        is False
    )
    assert (
        llm_connections.supports_structured_outputs(
            replace(local, structured_outputs="supported"), settings
        )
        is True
    )


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
