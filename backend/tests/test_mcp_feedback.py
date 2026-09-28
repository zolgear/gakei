"""MCP の使ってみたフィードバックによる改訂(ADR-0023 7章)。

すぐ返る生成と `get_run` の待ち、`list_runs`、1回限りのアップロード URL、画像本体の
アクセストークン認証、透過の情報、料金の目安と上限の残り。
"""

from __future__ import annotations

import io
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select, update

import app.api.uploads as uploads_api
import app.mcp.server as mcp_server
from app.domain.models import ApiToken, Asset, Run, UploadTicket
from app.domain.pricing import estimate_cost as estimate_cost_domain
from tests.conftest import login_as, make_png_bytes
from tests.test_mcp import _call, _enable, _error_text, _generate, _ok, _run_count


def _rgba_png(width: int = 40, height: int = 20, transparent_columns: int = 20) -> bytes:
    """左 `transparent_columns` 列が完全に透明、残りが不透明の RGBA PNG。"""
    image = Image.new("RGBA", (width, height), (255, 0, 0, 255))
    for x in range(transparent_columns):
        for y in range(height):
            image.putpixel((x, y), (0, 0, 0, 0))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _jpeg() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), (10, 20, 30)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _upload_url(client: TestClient, headers: dict[str, str] | None = None) -> dict[str, Any]:
    return _ok(_call(client, "create_upload_url", {}, headers))


def _path_of(url: str) -> str:
    assert url.startswith("http://testserver/")
    return url.removeprefix("http://testserver")


# -- すぐ返る生成と get_run の待ち(7章 1) -------------------------------------------


def test_generate_image_returns_immediately_with_quota_and_estimate(
    client_no_runner: TestClient,
) -> None:
    client = client_no_runner
    _enable(client, hourly_run_limit=5)
    payload = _ok(
        _call(
            client,
            "generate_image",
            {"prompt": "fast", "params": {"size": "1024x1024", "quality": "low"}},
        )
    )
    # runner が止まっていても、待たずに run_id と状態が返る。
    assert payload["status"] == "queued"
    assert uuid.UUID(payload["run_id"])
    assert "get_run" in payload["note"]
    assert payload["quota"] == {"hourly_run_limit": 5, "runs_last_hour": 1, "remaining": 4}
    # 実行前はパラメーターからの見積もり(画面の見積もりと同じ計算)。
    assert payload["cost"]["basis"] == "estimate"
    expected = estimate_cost_domain(
        model=payload["model"],
        quality="low",
        size="1024x1024",
        n=1,
        prompt_length=len("fast"),
    ).total_usd
    assert payload["cost"]["usd"] == pytest.approx(expected)


def test_generate_image_wait_is_capped_and_still_returns_run_id(
    client_no_runner: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = client_no_runner
    _enable(client)
    monkeypatch.setattr(mcp_server, "WAIT_MAX_SECONDS", 0.3)
    payload = _ok(_call(client, "generate_image", {"prompt": "slow", "wait": True}))
    # 打ち切っても、エラーにせず run_id と現在の状態を返す。
    assert payload["status"] == "queued"
    assert payload["run_id"]

    # get_run の wait_seconds は上限を超えた値も受け、上限で打ち切る。
    waited = _ok(_call(client, "get_run", {"run_id": payload["run_id"], "wait_seconds": 600}))
    assert waited["status"] == "queued"
    assert waited["quota"]["runs_last_hour"] == 1


def test_wait_limit_is_short_enough_for_mcp_clients() -> None:
    assert mcp_server.WAIT_MAX_SECONDS <= 25


def test_generate_image_never_loses_run_id_after_creation(
    client_no_runner: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = client_no_runner
    _enable(client)

    async def _broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(mcp_server, "_run_result", _broken)
    payload = _ok(_call(client, "generate_image", {"prompt": "resilient"}))
    assert payload["status"] == "unknown"
    with client.app.state.session_factory() as db:
        run = db.get(Run, uuid.UUID(payload["run_id"]))
        assert run is not None


def test_get_run_after_success_has_cost_from_usage(client: TestClient) -> None:
    _enable(client)
    payload = _generate(client, {"prompt": "done", "params": {"quality": "low"}})
    assert payload["cost"]["basis"] == "usage"
    assert payload["cost"]["usd"] > 0
    assert payload["quota"]["runs_last_hour"] == 1
    assert "note" not in payload


# -- list_runs(7章 1) ---------------------------------------------------------------


def test_list_runs_filters(client_no_runner: TestClient) -> None:
    client = client_no_runner
    _enable(client)
    first = _ok(_call(client, "generate_image", {"prompt": "mcp one"}))
    second = _ok(_call(client, "generate_image", {"prompt": "mcp two"}))
    rest = client.post(
        "/api/runs",
        json={"operation": "generate", "model": "gpt-image-2.5-sunburst", "prompt": "web"},
    )
    assert rest.status_code == 202
    _ok(_call(client, "cancel_run", {"run_id": first["run_id"]}))

    everything = _ok(_call(client, "list_runs", {}))
    assert [r["run_id"] for r in everything["items"]] == [
        rest.json()["id"],
        second["run_id"],
        first["run_id"],
    ]
    assert everything["truncated"] is False
    assert everything["quota"]["runs_last_hour"] == 2

    mcp_only = _ok(_call(client, "list_runs", {"origin": "mcp"}))["items"]
    assert [r["run_id"] for r in mcp_only] == [second["run_id"], first["run_id"]]
    web_only = _ok(_call(client, "list_runs", {"origin": "web"}))["items"]
    assert [r["run_id"] for r in web_only] == [rest.json()["id"]]

    canceled = _ok(_call(client, "list_runs", {"status": ["canceled"]}))["items"]
    assert [r["run_id"] for r in canceled] == [first["run_id"]]

    limited = _ok(_call(client, "list_runs", {"limit": 1}))
    assert len(limited["items"]) == 1
    assert limited["truncated"] is True

    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    assert _ok(_call(client, "list_runs", {"since": future}))["items"] == []
    past = (datetime.now(UTC) - timedelta(hours=1)).replace(tzinfo=None).isoformat()
    assert len(_ok(_call(client, "list_runs", {"since": past}))["items"]) == 3

    # 削除した Run は出さない。
    assert client.delete(f"/api/runs/{first['run_id']}").status_code in (200, 204)
    ids = [r["run_id"] for r in _ok(_call(client, "list_runs", {}))["items"]]
    assert first["run_id"] not in ids


def _issue_token(client: TestClient, email: str) -> str:
    login_as(client, email)
    response = client.post("/api/users/me/api-tokens", json={"name": "agent"})
    assert response.status_code == 201, response.text
    return response.json()["token"]


def test_list_runs_created_by_me_in_oidc(client_oidc: TestClient) -> None:
    login_as(client_oidc, "admin@example.com")
    _enable(client_oidc)
    alice = {"Authorization": f"Bearer {_issue_token(client_oidc, 'alice@example.com')}"}
    bob = {"Authorization": f"Bearer {_issue_token(client_oidc, 'bob@example.com')}"}
    mine = _ok(_call(client_oidc, "generate_image", {"prompt": "alice"}, alice))
    theirs = _ok(_call(client_oidc, "generate_image", {"prompt": "bob"}, bob))

    own = _ok(_call(client_oidc, "list_runs", {}, alice))["items"]
    assert [r["run_id"] for r in own] == [mine["run_id"]]
    # 閲覧範囲は全員全件(ADR-0019)なので、明示すれば他人の Run も見られる。
    anyone = _ok(_call(client_oidc, "list_runs", {"created_by": "anyone"}, alice))["items"]
    assert {r["run_id"] for r in anyone} == {mine["run_id"], theirs["run_id"]}


# -- アップロード URL(7章 2) --------------------------------------------------------


def test_upload_url_put_then_reuse_fails(client: TestClient) -> None:
    _enable(client)
    issued = _upload_url(client)
    assert issued["method"] == "PUT"
    assert issued["expires_in_seconds"] == 600
    assert issued["upload_url"] in issued["curl_example"]
    assert issued["upload_url"].startswith("http://testserver/api/uploads/")

    path = _path_of(issued["upload_url"])
    data = make_png_bytes(48, 32, (1, 2, 3))
    response = client.put(path, content=data)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["kind"] == "upload"
    assert body["width"] == 48
    assert body["bytes"] == len(data)
    assert (
        body["url"] == f"http://testserver/api/assets/{body['asset_id']}/content?variant=original"
    )

    # 取り込んだ画像は edit の入力に使える。
    asset = _ok(_call(client, "get_asset", {"asset_id": body["asset_id"]}))
    assert asset["kind"] == "upload"

    # 2回目は使えない。
    assert client.put(path, content=data).status_code == 410
    with client.app.state.session_factory() as db:
        ticket = db.execute(select(UploadTicket)).scalar_one()
        assert ticket.used_at is not None
        assert str(ticket.asset_id) == body["asset_id"]
        # トークンそのものは保存しない。
        assert ticket.token_hash not in issued["upload_url"]


def test_upload_url_multipart_post(client: TestClient) -> None:
    _enable(client)
    path = _path_of(_upload_url(client)["upload_url"])
    response = client.post(
        path, files={"file": ("x.png", make_png_bytes(20, 20, (9, 9, 9)), "image/png")}
    )
    assert response.status_code == 201, response.text
    assert response.json()["width"] == 20


def test_upload_url_expired_invalid_and_disabled(client: TestClient) -> None:
    _enable(client)
    data = make_png_bytes()
    assert client.put(f"/api/uploads/{'x' * 43}", content=data).status_code == 404

    expired = _path_of(_upload_url(client)["upload_url"])
    with client.app.state.session_factory() as db:
        db.execute(update(UploadTicket).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
        db.commit()
    assert client.put(expired, content=data).status_code == 410

    fresh = _path_of(_upload_url(client)["upload_url"])
    assert client.patch("/api/settings/mcp", json={"enabled": False}).status_code == 200
    assert client.put(fresh, content=data).status_code == 404
    # 有効に戻せば、期限内の URL は使える。
    _enable(client)
    assert client.put(fresh, content=data).status_code == 201


def test_upload_url_rejects_bad_body_without_consuming(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(client)
    path = _path_of(_upload_url(client)["upload_url"])
    assert client.put(path, content=b"").status_code == 422
    assert client.put(path, content=b"not an image").status_code == 422

    # 大きさの上限(REST のアップロードと同じ値。ここでは小さく差し替えて確かめる)。
    monkeypatch.setattr(uploads_api, "MAX_UPLOAD_BYTES", 100)
    assert client.put(path, content=make_png_bytes(64, 64)).status_code == 413
    monkeypatch.undo()

    # 取り込めなかった送信では使用済みにならない。
    assert client.put(path, content=make_png_bytes()).status_code == 201
    assert client.get("/api/assets?kind=upload").json()["items"][0]["kind"] == "upload"


def test_upload_url_rejects_image_over_rest_limit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """取り込み(`ingest_upload`)側の上限も REST と同じく効く。"""
    import app.domain.assets as assets_domain

    _enable(client)
    path = _path_of(_upload_url(client)["upload_url"])
    monkeypatch.setattr(assets_domain, "MAX_UPLOAD_BYTES", 50)
    assert client.put(path, content=make_png_bytes()).status_code == 422


def test_upload_url_records_issuer_in_oidc(client_oidc: TestClient) -> None:
    login_as(client_oidc, "admin@example.com")
    _enable(client_oidc)
    token = _issue_token(client_oidc, "uploader@example.com")
    auth = {"Authorization": f"Bearer {token}"}
    path = _path_of(_upload_url(client_oidc, auth)["upload_url"])

    # URL 自体が認可なので、Cookie もトークンも要らない。
    client_oidc.cookies.clear()
    response = client_oidc.put(path, content=make_png_bytes())
    assert response.status_code == 201, response.text
    asset_id = response.json()["asset_id"]

    login_as(client_oidc, "uploader@example.com")
    detail = client_oidc.get(f"/api/assets/{asset_id}").json()
    assert detail["created_by"]["email"] == "uploader@example.com"


def test_upload_url_invalid_after_token_revoked(client_oidc: TestClient) -> None:
    login_as(client_oidc, "admin@example.com")
    _enable(client_oidc)
    token = _issue_token(client_oidc, "revoker@example.com")
    path = _path_of(_upload_url(client_oidc, {"Authorization": f"Bearer {token}"})["upload_url"])
    token_id = client_oidc.get("/api/users/me/api-tokens").json()["items"][0]["id"]
    assert client_oidc.delete(f"/api/users/me/api-tokens/{token_id}").status_code == 204
    assert client_oidc.put(path, content=make_png_bytes()).status_code == 410


# -- 画像本体のトークン認証(7章 3) ----------------------------------------------------


def test_content_accepts_bearer_token_only_there(client_oidc: TestClient) -> None:
    login_as(client_oidc, "admin@example.com")
    _enable(client_oidc)
    token = _issue_token(client_oidc, "viewer@example.com")
    auth = {"Authorization": f"Bearer {token}"}
    payload = _generate(client_oidc, {"prompt": "fetch me"}, headers=auth)
    asset_id = payload["outputs"][0]["asset_id"]
    content_path = _path_of(payload["outputs"][0]["url"])

    with client_oidc.app.state.session_factory() as db:
        db.execute(update(ApiToken).values(last_used_at=None))
        db.commit()

    client_oidc.cookies.clear()
    assert client_oidc.get(content_path).status_code == 401
    response = client_oidc.get(content_path, headers=auth)
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert (
        client_oidc.get(f"/api/assets/{asset_id}/content?variant=thumb", headers=auth).status_code
        == 200
    )
    with client_oidc.app.state.session_factory() as db:
        assert db.execute(select(ApiToken)).scalar_one().last_used_at is not None

    # 他の REST API ではトークンを受けない。
    assert client_oidc.get("/api/runs", headers=auth).status_code == 401
    assert client_oidc.get(f"/api/assets/{asset_id}", headers=auth).status_code == 401
    assert client_oidc.get("/api/auth/me", headers=auth).json()["user"] is None

    # 不明なトークンは 401。
    bad = {"Authorization": "Bearer gakei_not-a-real-token"}
    assert client_oidc.get(content_path, headers=bad).status_code == 401

    # MCP を無効にすると、トークンでは取れない。
    login_as(client_oidc, "admin@example.com")
    assert client_oidc.patch("/api/settings/mcp", json={"enabled": False}).status_code == 200
    client_oidc.cookies.clear()
    assert client_oidc.get(content_path, headers=auth).status_code == 401
    login_as(client_oidc, "admin@example.com")
    _enable(client_oidc)

    # 失効させると 401。
    login_as(client_oidc, "viewer@example.com")
    token_id = client_oidc.get("/api/users/me/api-tokens").json()["items"][0]["id"]
    assert client_oidc.delete(f"/api/users/me/api-tokens/{token_id}").status_code == 204
    client_oidc.cookies.clear()
    assert client_oidc.get(content_path, headers=auth).status_code == 401


def test_content_with_cookie_still_works_in_oidc(client_oidc: TestClient) -> None:
    login_as(client_oidc, "admin@example.com")
    _enable(client_oidc)
    response = client_oidc.post(
        "/api/assets",
        files={"file": ("a.png", make_png_bytes(), "image/png")},
        data={"kind": "upload"},
    )
    assert response.status_code == 201, response.text
    asset_id = response.json()["id"]
    assert client_oidc.get(f"/api/assets/{asset_id}/content").status_code == 200


def test_content_in_none_mode_needs_nothing(client: TestClient) -> None:
    _enable(client)
    asset_id = _upload_via_url(client, make_png_bytes())
    path = f"/api/assets/{asset_id}/content?variant=original"
    assert client.get(path).status_code == 200
    assert client.get(path, headers={"Authorization": "Bearer whatever"}).status_code == 200


# -- 透過の情報(7章 4) ---------------------------------------------------------------


def _upload_via_url(client: TestClient, data: bytes) -> str:
    path = _path_of(_upload_url(client)["upload_url"])
    response = client.put(path, content=data)
    assert response.status_code == 201, response.text
    return response.json()["asset_id"]


def test_get_asset_transparency(client: TestClient) -> None:
    _enable(client)
    half = _upload_via_url(client, _rgba_png(40, 20, transparent_columns=10))
    detail = _ok(_call(client, "get_asset", {"asset_id": half}))
    assert detail["has_alpha"] is True
    assert detail["transparent_ratio"] == pytest.approx(0.25)

    opaque_rgba = _upload_via_url(client, _rgba_png(10, 10, transparent_columns=0))
    detail = _ok(_call(client, "get_asset", {"asset_id": opaque_rgba}))
    assert detail["has_alpha"] is True
    assert detail["transparent_ratio"] == 0

    rgb = _upload_via_url(client, make_png_bytes(16, 16))
    detail = _ok(_call(client, "get_asset", {"asset_id": rgb}))
    assert detail["has_alpha"] is False
    assert detail["transparent_ratio"] == 0

    jpeg = _upload_via_url(client, _jpeg())
    detail = _ok(_call(client, "get_asset", {"asset_id": jpeg}))
    assert detail["has_alpha"] is False
    assert detail["transparent_ratio"] == 0


def test_generated_transparent_background_is_reported(client: TestClient) -> None:
    _enable(client)
    payload = _generate(
        client,
        {"prompt": "sticker", "params": {"background": "transparent", "output_format": "png"}},
    )
    detail = _ok(_call(client, "get_asset", {"asset_id": payload["outputs"][0]["asset_id"]}))
    assert detail["has_alpha"] is True
    assert 0 <= detail["transparent_ratio"] <= 1


# -- size の説明(7章 5) -------------------------------------------------------------


def test_get_capabilities_explains_params_size(client: TestClient) -> None:
    _enable(client)
    caps = _ok(_call(client, "get_capabilities"))
    assert any("params.size" in note for note in caps["usage_notes"])
    example = caps["example_generate_image"]
    assert "size" in example["params"]
    # 例のとおりに呼べば Run ができる。
    created = _ok(_call(client, "generate_image", example))
    assert created["params"]["size"] == example["params"]["size"]


# -- 料金の目安と上限の残り(7章 6) ----------------------------------------------------


def test_estimate_cost_matches_rest_estimate(client: TestClient) -> None:
    _enable(client)
    before = _run_count(client)
    result = _ok(
        _call(
            client,
            "estimate_cost",
            {
                "model": "gpt-image-2.5-sunburst",
                "params": {"size": "1536x1024", "quality": "medium"},
                "n": 2,
                "prompt": "abc",
            },
        )
    )
    rest = client.get(
        "/api/pricing/estimate",
        params={
            "model": "gpt-image-2.5-sunburst",
            "operation": "generate",
            "size": "1536x1024",
            "quality": "medium",
            "n": 2,
            "prompt_length": 3,
        },
    ).json()
    assert result["total_usd"] == pytest.approx(rest["total_usd"])
    assert result["unavailable_reason"] is None
    assert result["n"] == 2
    assert result["output_tokens_per_image"] == rest["output_tokens_per_image"]
    # 見積もりは Run を作らない。
    assert _run_count(client) == before


def test_estimate_cost_with_input_images(client: TestClient) -> None:
    _enable(client)
    asset_id = _upload_via_url(client, make_png_bytes(1024, 1024))
    result = _ok(
        _call(
            client,
            "estimate_cost",
            {"params": {"size": "1024x1024", "quality": "low"}, "input_asset_ids": [asset_id]},
        )
    )
    assert result["input_image_tokens"] == 1024
    assert result["total_usd"] > 0


def test_estimate_cost_unavailable(client: TestClient) -> None:
    _enable(client)
    auto = _ok(_call(client, "estimate_cost", {"params": {"size": "1024x1024"}}))
    assert auto["total_usd"] is None
    assert auto["unavailable_reason"] == "quality_auto"
    assert "params.quality" in auto["unavailable_message"]

    no_size = _ok(_call(client, "estimate_cost", {"params": {"quality": "low"}}))
    assert no_size["unavailable_reason"] == "size_auto"

    unknown = _ok(_call(client, "estimate_cost", {"model": "other-model", "params": {}}))
    assert unknown["unavailable_reason"] == "unknown_model"

    assert "Unknown provider" in _error_text(
        _call(client, "estimate_cost", {"provider": "no-such-provider"})
    )


def test_quota_remaining_counts_down(client_no_runner: TestClient) -> None:
    client = client_no_runner
    _enable(client, hourly_run_limit=2)
    first = _ok(_call(client, "generate_image", {"prompt": "a"}))
    assert first["quota"]["remaining"] == 1
    second = _ok(_call(client, "generate_image", {"prompt": "b"}))
    assert second["quota"] == {"hourly_run_limit": 2, "runs_last_hour": 2, "remaining": 0}
    assert "limit" in _error_text(_call(client, "generate_image", {"prompt": "c"}))
    assert _ok(_call(client, "list_runs", {}))["quota"]["remaining"] == 0


def test_run_without_pricing_support_has_null_cost(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(client)
    fake = client.app.state.registry.get(client.app.state.registry.primary)
    monkeypatch.setattr(type(fake), "supports_pricing", False)
    payload = _generate(client, {"prompt": "no price", "params": {"quality": "low"}})
    assert payload["cost"] is None
    result = _ok(
        _call(client, "estimate_cost", {"params": {"quality": "low", "size": "1024x1024"}})
    )
    assert result["unavailable_reason"] == "provider_not_supported"


def test_asset_row_kept_for_upload(client: TestClient) -> None:
    """アップロード URL で取り込んだ Asset は通常のアップロードと同じ種類・記録になる。"""
    _enable(client)
    asset_id = _upload_via_url(client, make_png_bytes(12, 12))
    with client.app.state.session_factory() as db:
        asset = db.get(Asset, uuid.UUID(asset_id))
        assert asset.kind == "upload"
        assert asset.created_by_user_id is None
