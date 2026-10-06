"""アクセストークンの有効期限と、読み取り専用のトークン(ADR-0023 11章、Issue #82)。

期限切れは DB の `expires_at` を過去に書き換えて作る(他のチケットのテストと同じやり方)。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from app.domain.models import ApiToken, AssetGroup
from app.i18n import t
from app.mcp.server import READ_SCOPE_TOOLS
from tests.conftest import login_as, make_png_bytes
from tests.test_mcp import _call, _enable, _error_text, _generate, _ok, _rpc, _run_count

API = "/api/users/me/api-tokens"

# ADR-0023 11章 2 の一覧(読み取りのみで使える / 使えない)。
_READ_TOOLS = {
    "get_capabilities",
    "estimate_cost",
    "get_run",
    "list_runs",
    "search_assets",
    "find_similar_assets",
    "get_asset",
    "get_image",
    "list_prompt_sets",
    "list_groups",
    "create_download_url",
}
_FULL_ONLY_TOOLS = {
    "generate_image",
    "cancel_run",
    "upload_image",
    "create_upload_url",
    "create_group",
    "move_to_group",
}


def _path_of(url: str) -> str:
    assert url.startswith("http://testserver/")
    return url.removeprefix("http://testserver")


def _issue(client: TestClient, email: str, **body: Any) -> dict[str, Any]:
    login_as(client, email)
    response = client.post(API, json={"name": "agent", **body})
    assert response.status_code == 201, response.text
    return response.json()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _expire(client: TestClient, token_id: str) -> None:
    with client.app.state.session_factory() as db:
        db.execute(
            update(ApiToken)
            .where(ApiToken.id == uuid.UUID(token_id))
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        db.commit()


def _setup(client: TestClient) -> None:
    login_as(client, "admin@example.com", "管理者")
    _enable(client)


def _tool_names(client: TestClient, headers: dict[str, str]) -> set[str]:
    response = _rpc(client, "tools/list", headers=headers)
    assert response.status_code == 200, response.text
    return {tool["name"] for tool in response.json()["result"]["tools"]}


# -- 発行と一覧 ------------------------------------------------------------------


def test_read_scope_tools_match_adr() -> None:
    assert set(READ_SCOPE_TOOLS) == _READ_TOOLS


def test_issue_defaults_to_90_days_and_full(client_oidc: TestClient) -> None:
    before = datetime.now(UTC)
    body = _issue(client_oidc, "owner@example.com")
    assert body["scope"] == "full"
    assert body["expired"] is False
    expires_at = datetime.fromisoformat(body["expires_at"])
    assert before + timedelta(days=90) <= expires_at <= datetime.now(UTC) + timedelta(days=90)


@pytest.mark.parametrize("days", [30, 90, 365])
def test_issue_with_each_expiry(client_oidc: TestClient, days: int) -> None:
    before = datetime.now(UTC)
    body = _issue(client_oidc, "owner@example.com", expires_in_days=days, scope="read")
    assert body["scope"] == "read"
    expires_at = datetime.fromisoformat(body["expires_at"])
    assert before + timedelta(days=days) <= expires_at <= datetime.now(UTC) + timedelta(days=days)
    listed = client_oidc.get(API).json()["items"][0]
    assert listed["scope"] == "read"
    assert datetime.fromisoformat(listed["expires_at"]) == expires_at


def test_issue_without_expiry(client_oidc: TestClient) -> None:
    body = _issue(client_oidc, "owner@example.com", expires_in_days=None)
    assert body["expires_at"] is None
    assert body["expired"] is False
    with client_oidc.app.state.session_factory() as db:
        assert db.execute(select(ApiToken.expires_at)).scalar_one() is None


def test_issue_rejects_unknown_expiry_and_scope(client_oidc: TestClient) -> None:
    login_as(client_oidc, "owner@example.com")
    assert client_oidc.post(API, json={"name": "x", "expires_in_days": 7}).status_code == 422
    assert client_oidc.post(API, json={"name": "x", "scope": "admin"}).status_code == 422
    assert client_oidc.get(API).json()["items"] == []


def test_expired_token_stays_listed_until_revoked(client_oidc: TestClient) -> None:
    body = _issue(client_oidc, "owner@example.com", expires_in_days=30)
    _expire(client_oidc, body["id"])
    listed = client_oidc.get(API).json()["items"]
    assert [item["id"] for item in listed] == [body["id"]]
    assert listed[0]["expired"] is True
    assert client_oidc.delete(f"{API}/{body['id']}").status_code == 204
    assert client_oidc.get(API).json()["items"] == []


# -- 期限切れ ----------------------------------------------------------------------


def test_expired_token_is_401_on_mcp_with_expiry_message(client_oidc: TestClient) -> None:
    _setup(client_oidc)
    body = _issue(client_oidc, "agent@example.com", expires_in_days=30)
    auth = _bearer(body["token"])
    assert _rpc(client_oidc, "tools/list", headers=auth).status_code == 200

    _expire(client_oidc, body["id"])
    response = _rpc(client_oidc, "tools/list", headers=auth)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["detail"] == t("mcp.tokenExpired")
    assert t("mcp.tokenExpired") != t("mcp.tokenInvalid")


def test_expired_token_is_401_on_content_get(client_oidc: TestClient) -> None:
    _setup(client_oidc)
    body = _issue(client_oidc, "agent@example.com")
    auth = _bearer(body["token"])
    payload = _generate(client_oidc, {"prompt": "expire me"}, headers=auth)
    content_path = _path_of(payload["outputs"][0]["url"])
    client_oidc.cookies.clear()
    assert client_oidc.get(content_path, headers=auth).status_code == 200

    _expire(client_oidc, body["id"])
    response = client_oidc.get(content_path, headers=auth)
    assert response.status_code == 401
    assert response.json()["detail"] == t("mcp.tokenExpired")


def test_tickets_from_expired_token_are_rejected(client_oidc: TestClient) -> None:
    _setup(client_oidc)
    body = _issue(client_oidc, "agent@example.com")
    auth = _bearer(body["token"])
    first = _ok(_call(client_oidc, "create_upload_url", {}, auth))
    client_oidc.cookies.clear()
    asset_id = client_oidc.put(_path_of(first["upload_url"]), content=make_png_bytes()).json()[
        "asset_id"
    ]
    upload_path = _path_of(_ok(_call(client_oidc, "create_upload_url", {}, auth))["upload_url"])
    download_path = _path_of(
        _ok(_call(client_oidc, "create_download_url", {"asset_id": asset_id}, auth))["download_url"]
    )

    _expire(client_oidc, body["id"])
    assert client_oidc.put(upload_path, content=make_png_bytes()).status_code == 410
    assert client_oidc.get(download_path).status_code == 404


# -- 読み取りのみ --------------------------------------------------------------------


def test_read_token_lists_only_read_tools(client_oidc: TestClient) -> None:
    _setup(client_oidc)
    read = _issue(client_oidc, "agent@example.com", scope="read")
    full = _issue(client_oidc, "agent@example.com")
    assert _tool_names(client_oidc, _bearer(read["token"])) == _READ_TOOLS
    assert _tool_names(client_oidc, _bearer(full["token"])) == _READ_TOOLS | _FULL_ONLY_TOOLS


def test_read_token_cannot_call_write_tools(client_oidc: TestClient) -> None:
    _setup(client_oidc)
    full = _bearer(_issue(client_oidc, "agent@example.com")["token"])
    read = _bearer(_issue(client_oidc, "agent@example.com", scope="read")["token"])
    payload = _generate(client_oidc, {"prompt": "made with full"}, headers=full)
    asset_id = payload["outputs"][0]["asset_id"]
    group_id = _ok(_call(client_oidc, "create_group", {"name": "g"}, full))["id"]
    runs_before = _run_count(client_oidc)

    calls: dict[str, dict[str, Any]] = {
        "generate_image": {"prompt": "should not run"},
        "cancel_run": {"run_id": payload["run_id"]},
        "upload_image": {"data_base64": "aGVsbG8="},
        "create_upload_url": {},
        "create_group": {"name": "blocked"},
        "move_to_group": {"group_id": group_id, "asset_ids": [asset_id]},
    }
    assert set(calls) == _FULL_ONLY_TOOLS
    for name, arguments in calls.items():
        text = _error_text(_call(client_oidc, name, arguments, read))
        assert "read-only access token" in text, (name, text)

    assert _run_count(client_oidc) == runs_before
    with client_oidc.app.state.session_factory() as db:
        assert db.execute(select(func.count()).select_from(AssetGroup)).scalar_one() == 1
    # move_to_group も効いていない。
    assert _ok(_call(client_oidc, "get_asset", {"asset_id": asset_id}, read))["group"] is None

    # 存在しないツールは、権限ではなく「不明なツール」のエラーのまま。
    unknown = _error_text(_call(client_oidc, "no_such_tool", {}, read))
    assert "read-only" not in unknown


def test_read_token_can_use_read_tools_and_content(client_oidc: TestClient) -> None:
    _setup(client_oidc)
    full = _bearer(_issue(client_oidc, "agent@example.com")["token"])
    read = _bearer(_issue(client_oidc, "agent@example.com", scope="read")["token"])
    payload = _generate(client_oidc, {"prompt": "read me"}, headers=full)
    asset_id = payload["outputs"][0]["asset_id"]

    assert _ok(_call(client_oidc, "get_capabilities", {}, read))
    assert _ok(_call(client_oidc, "get_run", {"run_id": payload["run_id"]}, read))
    runs = _ok(_call(client_oidc, "list_runs", {}, read))["items"]
    assert payload["run_id"] in [r["run_id"] for r in runs]
    found = _ok(_call(client_oidc, "search_assets", {"query": "read"}, read))["items"]
    assert [a["asset_id"] for a in found] == [asset_id]
    assert _ok(_call(client_oidc, "get_asset", {"asset_id": asset_id}, read))
    assert not _call(client_oidc, "get_image", {"asset_id": asset_id}, read).get("isError")
    assert _ok(_call(client_oidc, "list_groups", {}, read))["items"] == []
    assert _ok(_call(client_oidc, "list_prompt_sets", {}, read))["items"] == []

    download = _ok(_call(client_oidc, "create_download_url", {"asset_id": asset_id}, read))
    client_oidc.cookies.clear()
    assert client_oidc.get(_path_of(download["download_url"])).status_code == 200
    # 画像の本体の GET も読み取りのみのトークンで使える(11章 2)。
    content = client_oidc.get(_path_of(payload["outputs"][0]["url"]), headers=read)
    assert content.status_code == 200
