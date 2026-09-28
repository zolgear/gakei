"""MCP サーバー(`/mcp`。ADR-0023)。FAKE プロバイダーで、JSON-RPC を直接投げて確かめる。

SDK は stateless + JSON 応答で動かしているので、`initialize` を省いて `tools/list` や
`tools/call` をそのまま POST できる(1リクエストで1応答)。
"""

from __future__ import annotations

import base64
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.domain.models import ApiToken, Run
from app.mcp.endpoint import origin_allowed
from tests.conftest import login_as, make_png_bytes

PROTOCOL_VERSION = "2025-11-25"
_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "MCP-Protocol-Version": PROTOCOL_VERSION,
}


def _rpc(
    client: TestClient,
    method: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
):  # noqa: ANN202
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    return client.post("/mcp", json=body, headers={**_HEADERS, **(headers or {})})


def _call(
    client: TestClient,
    name: str,
    arguments: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    response = _rpc(
        client, "tools/call", {"name": name, "arguments": arguments or {}}, headers=headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "result" in body, body
    return body["result"]


def _ok(result: dict[str, Any]) -> dict[str, Any]:
    assert not result.get("isError"), result
    return result["structuredContent"]


def _error_text(result: dict[str, Any]) -> str:
    assert result.get("isError") is True, result
    return result["content"][0]["text"]


def _enable(client: TestClient, **extra: Any) -> None:
    response = client.patch("/api/settings/mcp", json={"enabled": True, **extra})
    assert response.status_code == 200, response.text


def _run_count(client: TestClient) -> int:
    with client.app.state.session_factory() as db:
        return db.execute(select(func.count()).select_from(Run)).scalar_one()


def _upload_b64(color: tuple[int, int, int] = (10, 120, 200)) -> str:
    return base64.b64encode(make_png_bytes(64, 64, color)).decode("ascii")


# -- 有効/無効・ツール一覧 ----------------------------------------------------------


def test_mcp_is_404_when_disabled(client: TestClient) -> None:
    response = _rpc(client, "tools/list")
    assert response.status_code == 404
    assert client.get("/api/settings/mcp").json()["enabled"] is False


def test_tools_list_when_enabled(client: TestClient) -> None:
    _enable(client)
    response = _rpc(client, "tools/list")
    assert response.status_code == 200, response.text
    tools = {t["name"]: t for t in response.json()["result"]["tools"]}
    assert set(tools) == {
        "get_capabilities",
        "generate_image",
        "get_run",
        "cancel_run",
        "search_assets",
        "get_asset",
        "upload_image",
        "list_prompt_sets",
        "list_groups",
        "create_group",
        "move_to_group",
    }
    # 削除・設定変更のツールは無い(ADR-0023 3章)。
    assert not any("delete" in name for name in tools)
    generate = tools["generate_image"]
    assert "BILLED" in generate["description"]
    assert generate["annotations"]["readOnlyHint"] is False
    assert generate["annotations"]["destructiveHint"] is False
    assert tools["get_capabilities"]["annotations"]["readOnlyHint"] is True
    assert tools["search_assets"]["annotations"]["readOnlyHint"] is True


def test_modern_protocol_version_header_is_served(client: TestClient) -> None:
    """SDK が別経路で処理する新しいプロトコル版でも、認証の文脈がツールに届くこと。"""
    _enable(client)
    response = _rpc(
        client,
        "tools/call",
        {
            "name": "list_groups",
            "arguments": {},
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientCapabilities": {},
            },
        },
        headers={
            "MCP-Protocol-Version": "2026-07-28",
            "Mcp-Method": "tools/call",
            "Mcp-Name": "list_groups",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "result" in body, body
    assert not body["result"].get("isError"), body


def test_get_capabilities_tool(client: TestClient) -> None:
    _enable(client)
    caps = _ok(_call(client, "get_capabilities"))
    assert caps["default_provider"] == "fake"
    assert caps["providers"][0]["default_model"]


# -- generate_image -------------------------------------------------------------


def test_generate_image_returns_outputs_and_thumbnail(client: TestClient) -> None:
    _enable(client)
    result = _call(client, "generate_image", {"prompt": "a lighthouse at dusk"})
    payload = _ok(result)
    assert payload["status"] == "succeeded"
    assert payload["origin"] == "mcp"
    assert len(payload["outputs"]) == 1
    output = payload["outputs"][0]
    asset_id = output["asset_id"]
    assert output["url"] == f"http://testserver/api/assets/{asset_id}/content?variant=original"
    assert output["viewer_url"] == f"http://testserver/assets/{asset_id}"

    images = [c for c in result["content"] if c["type"] == "image"]
    assert len(images) == 1
    assert images[0]["mimeType"] == "image/webp"
    # サムネイル(512px WebP の派生画像)そのものであること。原本は載せない。
    thumb = client.get(f"/api/assets/{asset_id}/content?variant=thumb").content
    assert base64.b64decode(images[0]["data"]) == thumb

    # 画面の Run 詳細でも実行元が分かる。none モードなので実行者・トークンは null。
    detail = client.get(f"/api/runs/{payload['run_id']}").json()
    assert detail["origin"] == "mcp"
    assert detail["created_by"] is None
    with client.app.state.session_factory() as db:
        run = db.get(Run, uuid.UUID(payload["run_id"]))
        assert run.api_token_id is None


def test_generate_image_without_thumbnails(client: TestClient) -> None:
    _enable(client)
    result = _call(client, "generate_image", {"prompt": "no thumbs", "include_thumbnails": False})
    _ok(result)
    assert [c["type"] for c in result["content"]] == ["text"]


def test_rest_runs_have_null_origin(client: TestClient) -> None:
    response = client.post(
        "/api/runs",
        json={"operation": "generate", "model": "gpt-image-2.5-sunburst", "prompt": "ui"},
    )
    assert response.status_code == 202
    assert client.get(f"/api/runs/{response.json()['id']}").json()["origin"] is None


def test_generate_image_validation_error_creates_no_run(client: TestClient) -> None:
    _enable(client)
    before = _run_count(client)
    text = _error_text(_call(client, "generate_image", {"prompt": "x", "model": "no-such-model"}))
    assert text
    assert _run_count(client) == before


def test_hourly_limit_blocks_new_runs(client: TestClient) -> None:
    _enable(client, hourly_run_limit=1)
    _ok(_call(client, "generate_image", {"prompt": "first"}))
    before = _run_count(client)
    text = _error_text(_call(client, "generate_image", {"prompt": "second"}))
    assert "limit" in text
    assert _run_count(client) == before

    # 画面(REST)からの Run は上限に数えない・止めない。
    response = client.post(
        "/api/runs",
        json={"operation": "generate", "model": "gpt-image-2.5-sunburst", "prompt": "ui"},
    )
    assert response.status_code == 202
    assert client.get("/api/settings/mcp").json()["runs_last_hour"] == 1


def test_hourly_limit_zero_stops_generation_but_not_read_tools(client: TestClient) -> None:
    _enable(client, hourly_run_limit=0)
    text = _error_text(_call(client, "generate_image", {"prompt": "blocked"}))
    assert "turned off" in text
    assert _run_count(client) == 0
    _ok(_call(client, "list_groups"))


def test_no_wait_then_get_run_and_cancel(client_no_runner: TestClient) -> None:
    client = client_no_runner
    _enable(client)
    payload = _ok(_call(client, "generate_image", {"prompt": "queued", "wait": False}))
    assert payload["status"] == "queued"
    assert "note" in payload
    run_id = payload["run_id"]

    waited = _ok(_call(client, "get_run", {"run_id": run_id, "wait_seconds": 0.3}))
    assert waited["status"] == "queued"

    canceled = _ok(_call(client, "cancel_run", {"run_id": run_id}))
    assert canceled["status"] == "canceled"
    assert _ok(_call(client, "get_run", {"run_id": run_id}))["status"] == "canceled"
    # 2回目は取り消せない。
    assert "cannot be canceled" in _error_text(_call(client, "cancel_run", {"run_id": run_id}))


def test_get_run_unknown(client: TestClient) -> None:
    _enable(client)
    assert "not found" in _error_text(_call(client, "get_run", {"run_id": str(uuid.uuid4())}))


# -- upload_image → edit、ストック、グループ ------------------------------------------


def test_upload_image_then_edit(client: TestClient) -> None:
    _enable(client)
    uploaded = _ok(_call(client, "upload_image", {"data_base64": _upload_b64()}))
    assert uploaded["kind"] == "upload"
    assert uploaded["ingest_outcome"] == "created"
    asset_id = uploaded["asset_id"]

    payload = _ok(
        _call(
            client,
            "generate_image",
            {
                "prompt": "make it sunset",
                "operation": "edit",
                "model": "gpt-image-2.5-sunburst",
                "input_asset_ids": [asset_id],
            },
        )
    )
    assert payload["status"] == "succeeded"
    assert payload["inputs"] == [{"asset_id": asset_id, "role": "image", "position": 0}]

    output_id = payload["outputs"][0]["asset_id"]
    asset = _ok(_call(client, "get_asset", {"asset_id": output_id}))
    assert asset["primary_parent_asset_id"] == asset_id
    assert asset["produced_by_run"]["run_id"] == payload["run_id"]
    assert asset["produced_by_run"]["origin"] == "mcp"


def test_upload_image_accepts_data_url_and_rejects_bad_base64(client: TestClient) -> None:
    _enable(client)
    data_url = "data:image/png;base64," + _upload_b64((1, 2, 3))
    assert _ok(_call(client, "upload_image", {"data_base64": data_url}))["kind"] == "upload"
    assert "base64" in _error_text(_call(client, "upload_image", {"data_base64": "!!!"}))
    not_image = base64.b64encode(b"hello").decode("ascii")
    _error_text(_call(client, "upload_image", {"data_base64": not_image}))


def test_groups_search_and_prompt_sets(client: TestClient) -> None:
    _enable(client)
    generated = _ok(_call(client, "generate_image", {"prompt": "zebra crossing at noon"}))
    asset_id = generated["outputs"][0]["asset_id"]
    uploaded = _ok(_call(client, "upload_image", {"data_base64": _upload_b64()}))

    group = _ok(_call(client, "create_group", {"name": "  From agent  "}))
    assert group["name"] == "From agent"
    assert _error_text(_call(client, "create_group", {"name": "   "}))

    moved = _ok(_call(client, "move_to_group", {"group_id": group["id"], "asset_ids": [asset_id]}))
    assert moved["member_count"] == 1
    missing = _call(
        client, "move_to_group", {"group_id": group["id"], "asset_ids": [str(uuid.uuid4())]}
    )
    assert "not found" in _error_text(missing)

    groups = _ok(_call(client, "list_groups"))["items"]
    assert [g["name"] for g in groups] == ["From agent"]

    in_group = _ok(_call(client, "search_assets", {"group_id": group["id"]}))["items"]
    assert [a["asset_id"] for a in in_group] == [asset_id]
    assert in_group[0]["group"]["name"] == "From agent"

    by_query = _ok(_call(client, "search_assets", {"query": "zebra", "include_thumbnails": True}))
    assert [a["asset_id"] for a in by_query["items"]] == [asset_id]
    assert "zebra" in by_query["items"][0]["prompt_snippet"]

    uploads = _ok(_call(client, "search_assets", {"kind": "upload"}))["items"]
    assert [a["asset_id"] for a in uploads] == [uploaded["asset_id"]]

    detail = _ok(_call(client, "get_asset", {"asset_id": asset_id}))
    assert detail["group"]["name"] == "From agent"

    response = client.post("/api/prompt-sets", json={"name": "Agent prompts"})
    assert response.status_code == 201, response.text
    sets = _ok(_call(client, "list_prompt_sets"))["items"]
    assert [s["name"] for s in sets] == ["Agent prompts"]


# -- Origin --------------------------------------------------------------------


def test_origin_mismatch_is_403(client: TestClient) -> None:
    _enable(client)
    response = _rpc(client, "tools/list", headers={"Origin": "http://evil.example"})
    assert response.status_code == 403


def test_origin_rebinding_hostname_is_403(client: TestClient) -> None:
    """DNS リバインディングでは Origin と Host がどちらも攻撃者のドメイン名になる。"""
    _enable(client)
    response = _rpc(
        client,
        "tools/list",
        headers={"Origin": "http://evil.example:8000", "Host": "evil.example:8000"},
    )
    assert response.status_code == 403


def test_origin_same_ip_host_is_allowed(client: TestClient) -> None:
    _enable(client)
    response = _rpc(
        client,
        "tools/list",
        headers={"Origin": "http://127.0.0.1:8000", "Host": "127.0.0.1:8000"},
    )
    assert response.status_code == 200, response.text


@pytest.mark.parametrize(
    ("origin", "host", "public", "expected"),
    [
        (None, "anything", None, True),
        ("http://localhost:5173", "localhost:5173", None, True),
        ("http://192.168.1.5:8000", "192.168.1.5:8000", None, True),
        ("http://192.168.1.5:8000", "192.168.1.5:9000", None, False),
        ("https://gakei.example.com", "internal:8000", "https://gakei.example.com", True),
        ("https://gakei.example.com", "gakei.example.com", None, False),
        ("null", "localhost:8000", None, False),
    ],
)
def test_origin_allowed_rules(
    origin: str | None, host: str, public: str | None, expected: bool
) -> None:
    assert origin_allowed(origin, host, public) is expected


# -- 認証モード(アクセストークン) -------------------------------------------------


def _setup_oidc(client: TestClient) -> str:
    """管理者で MCP を有効にし、一般利用者でログインし直してトークンを発行する。"""
    login_as(client, "admin@example.com", "管理者")
    _enable(client)
    login_as(client, "agent-user@example.com", "利用者")
    response = client.post("/api/users/me/api-tokens", json={"name": "Claude Code"})
    assert response.status_code == 201, response.text
    return response.json()["token"]


def test_oidc_mcp_requires_bearer_token(client_oidc: TestClient) -> None:
    _setup_oidc(client_oidc)
    # ログイン済みの Cookie があっても、トークンが無ければ 401。
    response = _rpc(client_oidc, "tools/list")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"

    response = _rpc(
        client_oidc, "tools/list", headers={"Authorization": "Bearer gakei_not-a-real-token"}
    )
    assert response.status_code == 401


def test_oidc_mcp_with_token_records_user_and_token(client_oidc: TestClient) -> None:
    token = _setup_oidc(client_oidc)
    auth = {"Authorization": f"Bearer {token}"}
    me = client_oidc.get("/api/auth/me").json()

    payload = _ok(_call(client_oidc, "generate_image", {"prompt": "with token"}, headers=auth))
    uploaded = _ok(_call(client_oidc, "upload_image", {"data_base64": _upload_b64()}, headers=auth))

    detail = client_oidc.get(f"/api/runs/{payload['run_id']}").json()
    assert detail["origin"] == "mcp"
    assert detail["created_by"]["email"] == "agent-user@example.com"
    asset = client_oidc.get(f"/api/assets/{uploaded['asset_id']}").json()
    assert asset["created_by"]["email"] == "agent-user@example.com"

    with client_oidc.app.state.session_factory() as db:
        run = db.get(Run, uuid.UUID(payload["run_id"]))
        token_row = db.execute(select(ApiToken)).scalar_one()
        assert run.api_token_id == token_row.id
        assert str(run.created_by_user_id) == me["user"]["id"]
        assert token_row.last_used_at is not None
        # 値そのものは保存しない。
        assert token_row.token_hash != token
        assert len(token_row.token_hash) == 64

    listed = client_oidc.get("/api/users/me/api-tokens").json()["items"]
    assert listed[0]["last_used_at"] is not None


def test_oidc_revoked_token_is_401(client_oidc: TestClient) -> None:
    token = _setup_oidc(client_oidc)
    auth = {"Authorization": f"Bearer {token}"}
    assert _rpc(client_oidc, "tools/list", headers=auth).status_code == 200

    token_id = client_oidc.get("/api/users/me/api-tokens").json()["items"][0]["id"]
    assert client_oidc.delete(f"/api/users/me/api-tokens/{token_id}").status_code == 204
    assert _rpc(client_oidc, "tools/list", headers=auth).status_code == 401
    assert client_oidc.get("/api/users/me/api-tokens").json()["items"] == []


def test_oidc_public_base_url_origin_is_allowed(client_oidc: TestClient) -> None:
    token = _setup_oidc(client_oidc)
    headers = {"Authorization": f"Bearer {token}", "Origin": "http://testserver"}
    assert _rpc(client_oidc, "tools/list", headers=headers).status_code == 200
    headers["Origin"] = "http://other.example"
    assert _rpc(client_oidc, "tools/list", headers=headers).status_code == 403


# -- アクセストークン API ----------------------------------------------------------


def test_api_tokens_are_404_in_none_mode(client: TestClient) -> None:
    assert client.get("/api/users/me/api-tokens").status_code == 404
    assert client.post("/api/users/me/api-tokens", json={"name": "x"}).status_code == 404
    assert client.delete(f"/api/users/me/api-tokens/{uuid.uuid4()}").status_code == 404


def test_api_token_crud(client_oidc: TestClient) -> None:
    login_as(client_oidc, "owner@example.com")
    created = client_oidc.post("/api/users/me/api-tokens", json={"name": "  laptop  "})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["name"] == "laptop"
    assert body["token"].startswith("gakei_")
    assert body["last_used_at"] is None

    listed = client_oidc.get("/api/users/me/api-tokens").json()["items"]
    assert [t["id"] for t in listed] == [body["id"]]
    assert "token" not in listed[0]

    assert client_oidc.post("/api/users/me/api-tokens", json={"name": "  "}).status_code == 422
    too_long = client_oidc.post("/api/users/me/api-tokens", json={"name": "x" * 101})
    assert too_long.status_code == 422

    # 他人のトークンは失効できない(存在しない扱い)。
    login_as(client_oidc, "someone-else@example.com")
    assert client_oidc.get("/api/users/me/api-tokens").json()["items"] == []
    assert client_oidc.delete(f"/api/users/me/api-tokens/{body['id']}").status_code == 404

    login_as(client_oidc, "owner@example.com")
    assert client_oidc.delete(f"/api/users/me/api-tokens/{body['id']}").status_code == 204
    assert client_oidc.delete(f"/api/users/me/api-tokens/{body['id']}").status_code == 404


def test_api_tokens_require_login(client_oidc: TestClient) -> None:
    assert client_oidc.get("/api/users/me/api-tokens").status_code == 401


# -- 管理者設定 -------------------------------------------------------------------


def test_mcp_settings_defaults_and_validation(client: TestClient) -> None:
    body = client.get("/api/settings/mcp").json()
    assert body == {
        "enabled": False,
        "hourly_run_limit": 30,
        "hourly_run_limit_default": 30,
        "hourly_run_limit_max": 1000,
        "runs_last_hour": 0,
        "endpoint_url": "http://testserver/mcp",
    }
    assert client.patch("/api/settings/mcp", json={"hourly_run_limit": -1}).status_code == 422
    assert client.patch("/api/settings/mcp", json={"hourly_run_limit": 1001}).status_code == 422
    # 不正な項目があれば、他の項目も保存しない。
    response = client.patch("/api/settings/mcp", json={"enabled": True, "hourly_run_limit": -1})
    assert response.status_code == 422
    assert client.get("/api/settings/mcp").json()["enabled"] is False

    updated = client.patch("/api/settings/mcp", json={"enabled": True, "hourly_run_limit": 0})
    assert updated.status_code == 200
    assert updated.json()["enabled"] is True
    assert updated.json()["hourly_run_limit"] == 0


def test_mcp_settings_update_requires_admin(client_oidc: TestClient) -> None:
    login_as(client_oidc, "plain@example.com")
    response = client_oidc.get("/api/settings/mcp")
    assert response.status_code == 200
    # PUBLIC_BASE_URL があればそれを基点にする。
    assert response.json()["endpoint_url"] == "http://testserver/mcp"
    assert client_oidc.patch("/api/settings/mcp", json={"enabled": True}).status_code == 403

    login_as(client_oidc, "admin@example.com")
    assert client_oidc.patch("/api/settings/mcp", json={"enabled": True}).status_code == 200


def test_mcp_settings_require_login(client_oidc: TestClient) -> None:
    assert client_oidc.get("/api/settings/mcp").status_code == 401


def test_upload_image_larger_than_sdk_default_body_limit(client: TestClient) -> None:
    """SDK の既定の本文上限(4 MiB)より大きい画像も、REST と同じ上限まで受け取れること。"""
    import io
    import os

    from PIL import Image

    image = Image.frombytes("RGB", (1400, 1400), os.urandom(1400 * 1400 * 3))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    data = buffer.getvalue()
    assert len(data) > 4 * 1024 * 1024
    _enable(client)
    encoded = base64.b64encode(data).decode("ascii")
    uploaded = _ok(_call(client, "upload_image", {"data_base64": encoded}))
    assert uploaded["bytes"] == len(data)


# -- タイトルとタグ(ADR-0024) ---------------------------------------------------------


def _upload_rest(client: TestClient, color: tuple[int, int, int]) -> str:
    response = client.post(
        "/api/assets",
        files={"file": ("a.png", make_png_bytes(32, 32, color), "image/png")},
        data={"kind": "upload"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_search_assets_filters_by_tag_and_returns_title_and_tags(client: TestClient) -> None:
    _enable(client)
    a = _upload_rest(client, (1, 2, 3))
    b = _upload_rest(client, (4, 5, 6))
    client.patch(f"/api/assets/{a}/title", json={"title": "夜の港"})
    client.post(f"/api/assets/{a}/tags", json={"name": "harbor"})
    client.post(f"/api/assets/{b}/tags", json={"name": "forest"})

    payload = _ok(_call(client, "search_assets", {"tag": "Harbor"}))
    assert [item["asset_id"] for item in payload["items"]] == [a]
    item = payload["items"][0]
    assert item["title"] == "夜の港"
    assert item["tags"] == [{"name": "harbor", "source": "user"}]

    # キーワードはタイトルにも当たる。tag と組み合わせられる。
    payload = _ok(_call(client, "search_assets", {"query": "夜の港"}))
    assert [item["asset_id"] for item in payload["items"]] == [a]
    payload = _ok(_call(client, "search_assets", {"query": "夜の港", "tag": "forest"}))
    assert payload["items"] == []


def test_get_asset_returns_title_and_tags_with_source(client: TestClient) -> None:
    from app.domain import annotations as ann

    _enable(client)
    asset_id = _upload_rest(client, (7, 8, 9))
    client.post(f"/api/assets/{asset_id}/tags", json={"name": "cat"})
    with client.app.state.session_factory() as db:
        ann.apply_auto_result(db, uuid.UUID(asset_id), title="自動の題", tags=[("sky", 0.8)])
        db.commit()

    payload = _ok(_call(client, "get_asset", {"asset_id": asset_id, "include_thumbnail": False}))
    assert payload["title"] == "自動の題"
    assert payload["title_source"] == "auto"
    assert payload["tags"] == [
        {"name": "cat", "source": "user"},
        {"name": "sky", "source": "auto"},
    ]
