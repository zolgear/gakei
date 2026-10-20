"""本文の上限と、oidc モードで本文を読む前の認証の足切り(ADR-0019 2章、2026-10-20 追記。
`app/body_guard.py`)。

「本文を読まない」ことは、ASGI を直接呼び、`receive` が呼ばれた回数で確かめる。
"""

from __future__ import annotations

import inspect
import json
import sys
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import Depends
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.api import mod_verdicts as mod_verdicts_api
from app.api.sdwebui import _BODY_LIMIT as SDWEBUI_BODY_LIMIT
from app.api.tag_dictionaries import _BODY_LIMIT as TAG_DICTIONARY_BODY_LIMIT
from app.body_guard import (
    DEFAULT_BODY_LIMIT,
    MULTIPART_OVERHEAD,
    policy_for_endpoint,
)
from app.domain.assets import MAX_UPLOAD_BYTES
from app.domain.avatars import MAX_AVATAR_BYTES
from app.domain.lineage_import import MAX_ZIP_BYTES
from app.mcp.server import MAX_REQUEST_BODY_BYTES
from tests.conftest import login_as

_UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
_MIB = 1024 * 1024


# -- ASGI を直接呼ぶ ---------------------------------------------------------------------


class _Result:
    def __init__(self) -> None:
        self.receive_calls = 0
        self.status: int | None = None
        self.body = b""

    def json(self) -> Any:
        return json.loads(self.body)


def _asgi_post(
    client: TestClient,
    path: str,
    *,
    headers: dict[str, str],
    chunks: list[bytes],
    method: str = "POST",
) -> _Result:
    """`client.app` を ASGI で直接呼ぶ(TestClient のイベントループの上で)。`chunks` は
    `receive` が順に返す本文の断片。"""
    result = _Result()
    pending = list(chunks)

    async def receive() -> dict[str, Any]:
        result.receive_calls += 1
        if pending:
            body = pending.pop(0)
            return {"type": "http.request", "body": body, "more_body": bool(pending)}
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            result.status = message["status"]
        elif message["type"] == "http.response.body":
            result.body += message.get("body", b"")

    all_headers = {"host": "testserver", **{k.lower(): v for k, v in headers.items()}}
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(k.encode("latin-1"), v.encode("latin-1")) for k, v in all_headers.items()],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
        "state": {},
    }
    assert client.portal is not None
    client.portal.call(client.app, scope, receive, send)
    return result


def _json_headers(length: int, **extra: str) -> dict[str, str]:
    return {"content-type": "application/json", "content-length": str(length), **extra}


# -- 既定の上限(JSON) -------------------------------------------------------------------


@pytest.fixture
def probe(client: TestClient) -> Iterator[dict[str, int]]:
    """本文を受ける、認証の無いテスト用のルート(依存とハンドラーが呼ばれた回数を数える)。"""
    calls = {"dependency": 0, "handler": 0}

    def counted() -> None:
        calls["dependency"] += 1

    @client.app.post("/api/test-body-probe")
    def body_probe(body: dict[str, Any], _: None = Depends(counted)) -> dict[str, int]:
        calls["handler"] += 1
        return {"keys": len(body)}

    yield calls


def test_content_length_over_default_is_413_without_reading(
    client: TestClient, probe: dict[str, int]
) -> None:
    # ヘッダーだけ大きい値を付け、本文は送らない。それでも即 413 で、本文は読まない。
    result = _asgi_post(
        client,
        "/api/test-body-probe",
        headers=_json_headers(DEFAULT_BODY_LIMIT + 1),
        chunks=[b"{}"],
    )
    assert result.status == 413
    assert result.json()["detail"] == "リクエストの本文が大きすぎます。"
    assert result.receive_calls == 0
    assert probe == {"dependency": 0, "handler": 0}

    # 本物の JSON のルート(Run の作成)でも同じ。
    result = _asgi_post(
        client, "/api/runs", headers=_json_headers(DEFAULT_BODY_LIMIT + 1), chunks=[b"{}"]
    )
    assert result.status == 413
    assert result.receive_calls == 0


def test_content_length_at_default_limit_is_read(client: TestClient, probe: dict[str, int]) -> None:
    body = b'{"a": 1}'
    response = client.post(
        "/api/test-body-probe", content=body, headers={"content-type": "application/json"}
    )
    assert response.status_code == 200
    assert response.json() == {"keys": 1}
    assert probe == {"dependency": 1, "handler": 1}


def test_chunked_over_default_is_413(client: TestClient, probe: dict[str, int]) -> None:
    # `Content-Length` なし。1MiB ずつ送り、上限を超えた時点で打ち切る(残りは読まない)。
    chunk = b" " * _MIB
    count = DEFAULT_BODY_LIMIT // _MIB + 4
    result = _asgi_post(
        client,
        "/api/test-body-probe",
        headers={"content-type": "application/json", "transfer-encoding": "chunked"},
        chunks=[chunk] * count,
    )
    assert result.status == 413
    assert result.receive_calls == DEFAULT_BODY_LIMIT // _MIB + 1
    assert probe == {"dependency": 0, "handler": 0}

    # TestClient の chunked(ジェネレーターの本文)でも 413。
    def generate() -> Iterator[bytes]:
        for _ in range(count):
            yield chunk

    response = client.post(
        "/api/test-body-probe", content=generate(), headers={"content-type": "application/json"}
    )
    assert response.status_code == 413
    assert probe == {"dependency": 0, "handler": 0}


def test_413_detail_follows_accept_language(client: TestClient) -> None:
    result = _asgi_post(
        client,
        "/api/runs",
        headers=_json_headers(DEFAULT_BODY_LIMIT + 1, **{"accept-language": "en"}),
        chunks=[b"{}"],
    )
    assert result.status == 413
    assert result.json()["detail"] == "The request body is too large."


# -- 広げたルート ---------------------------------------------------------------------------


def test_widened_upload_accepts_body_over_default(client: TestClient) -> None:
    # 既定の上限より大きく、画像の取り込みの上限より小さい。画像ではないので 422 になる。
    data = b"\0" * (DEFAULT_BODY_LIMIT + _MIB)
    response = client.post(
        "/api/assets", files={"file": ("big.png", data, "image/png")}, data={"kind": "upload"}
    )
    assert response.status_code == 422, response.text


def test_widened_upload_still_has_its_limit(client: TestClient) -> None:
    limit = MAX_UPLOAD_BYTES + MULTIPART_OVERHEAD
    result = _asgi_post(
        client,
        "/api/assets",
        headers={
            "content-type": "multipart/form-data; boundary=x",
            "content-length": str(limit + 1),
        },
        chunks=[b"--x--\r\n"],
    )
    assert result.status == 413
    assert result.receive_calls == 0


# -- oidc モードの足切り ------------------------------------------------------------------


def _enable_mcp_and_issue_token(client: TestClient) -> str:
    login_as(client, "admin@example.com", "管理者")
    assert client.patch("/api/settings/mcp", json={"enabled": True}).status_code == 200
    login_as(client, "body-user@example.com", "利用者")
    response = client.post("/api/users/me/api-tokens", json={"name": "agent"})
    assert response.status_code == 201, response.text
    return response.json()["token"]


def test_oidc_rejects_unauthenticated_body_before_reading(client_oidc: TestClient) -> None:
    client = client_oidc
    client.cookies.clear()
    body = json.dumps({"name": "x"}).encode()
    result = _asgi_post(
        client, "/api/asset-groups", headers=_json_headers(len(body)), chunks=[body]
    )
    assert result.status == 401
    assert result.json()["detail"] == "ログインが必要です。"
    assert result.receive_calls == 0

    # multipart(画像の取り込み)も、本文を読まずに 401。
    result = _asgi_post(
        client,
        "/api/assets",
        headers={"content-type": "multipart/form-data; boundary=x", "content-length": "1000"},
        chunks=[b"\0" * 1000],
    )
    assert result.status == 401
    assert result.receive_calls == 0

    # chunked の本文も同じ。
    result = _asgi_post(
        client,
        "/api/asset-groups",
        headers={"content-type": "application/json", "transfer-encoding": "chunked"},
        chunks=[body],
    )
    assert result.status == 401
    assert result.receive_calls == 0

    # 不明なセッションの Cookie でも同じ。
    result = _asgi_post(
        client,
        "/api/asset-groups",
        headers=_json_headers(len(body), cookie="gakei_session=not-a-session"),
        chunks=[body],
    )
    assert result.status == 401
    assert result.receive_calls == 0


def test_oidc_valid_session_passes(client_oidc: TestClient) -> None:
    client = client_oidc
    login_as(client, "body-user@example.com", "利用者")
    response = client.post("/api/asset-groups", json={"name": "通る"})
    assert response.status_code == 201, response.text
    response = client.post(
        "/api/assets",
        files={"file": ("x.png", b"not an image", "image/png")},
        data={"kind": "upload"},
    )
    assert response.status_code == 422


def test_oidc_api_token_bearer_passes_the_gate(client_oidc: TestClient) -> None:
    """アクセストークンの Bearer は足切りを通る(その先の権限はルートの依存が決める)。"""
    client = client_oidc
    token = _enable_mcp_and_issue_token(client)
    calls = {"handler": 0}

    @client.app.post("/api/test-body-probe")
    def body_probe(body: dict[str, Any]) -> dict[str, int]:
        calls["handler"] += 1
        return {"keys": len(body)}

    client.cookies.clear()
    body = b'{"a": 1}'
    ok = _asgi_post(
        client,
        "/api/test-body-probe",
        headers=_json_headers(len(body), authorization=f"Bearer {token}"),
        chunks=[body],
    )
    assert ok.status == 200
    assert calls["handler"] == 1

    bad = _asgi_post(
        client,
        "/api/test-body-probe",
        headers=_json_headers(len(body), authorization="Bearer gakei_not-a-real-token"),
        chunks=[body],
    )
    assert bad.status == 401
    assert bad.receive_calls == 0
    none = _asgi_post(
        client, "/api/test-body-probe", headers=_json_headers(len(body)), chunks=[body]
    )
    assert none.status == 401
    assert calls["handler"] == 1

    # REST の他のルートは、Bearer では今までどおり依存が 401 にする(足切りは通るが権限は無い)。
    response = client.post(
        "/api/asset-groups", json={"name": "x"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 401


def test_oidc_gate_skips_routes_without_login(client_oidc: TestClient) -> None:
    client = client_oidc
    client.cookies.clear()
    payload = json.dumps({"verdicts": []}).encode()
    # 判定の受付: 署名の確認まで進む(無い Webhook は 404)。
    result = _asgi_post(
        client, "/api/mods/nope/verdicts", headers=_json_headers(len(payload)), chunks=[payload]
    )
    assert result.status == 404
    # ログアウト(本文があっても足切りしない)。
    response = client.post("/api/auth/logout", json={})
    assert response.status_code == 200
    # トークン付きのアップロード URL(MCP が無効なので 404)。
    response = client.put(f"/api/uploads/{uuid.uuid4()}", content=b"\x89PNG")
    assert response.status_code == 404
    # `/mcp` は自分で Bearer を確かめる(MCP が無効なので 404)。
    response = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert response.status_code == 404


def test_oidc_requests_without_body_are_unchanged(client_oidc: TestClient) -> None:
    client = client_oidc
    client.cookies.clear()
    response = client.post(f"/api/runs/{uuid.uuid4()}/cancel")
    assert response.status_code == 401
    assert response.json()["detail"] == "ログインが必要です。"
    assert client.get("/api/runs").status_code == 401
    assert client.get("/api/health").status_code == 200


def test_none_mode_does_not_gate(client: TestClient) -> None:
    body = json.dumps({"name": "個人"}).encode()
    result = _asgi_post(
        client, "/api/asset-groups", headers=_json_headers(len(body)), chunks=[body]
    )
    assert result.status == 201


# -- 判定の受付 -----------------------------------------------------------------------------


def _verdict_headers(length: int | None = None) -> dict[str, str]:
    """署名は付けない(上限の確認は署名の確認より先に行う)。"""
    headers = {"content-type": "application/json"}
    if length is not None:
        headers["content-length"] = str(length)
    else:
        headers["transfer-encoding"] = "chunked"
    return headers


def test_verdicts_content_length_over_limit_is_413(client: TestClient) -> None:
    body = b"[]"
    result = _asgi_post(
        client,
        "/api/mods/nope/verdicts",
        headers=_verdict_headers(mod_verdicts_api._BODY_MAX_BYTES + 1),
        chunks=[body],
    )
    assert result.status == 413
    assert result.receive_calls == 0


def test_verdicts_chunked_over_limit_is_413(client: TestClient) -> None:
    chunk = b" " * (256 * 1024)
    result = _asgi_post(
        client,
        "/api/mods/nope/verdicts",
        headers=_verdict_headers(),
        chunks=[chunk] * 8,
    )
    assert result.status == 413
    # 1MiB を超えた5つ目で打ち切る。
    assert result.receive_calls == 5


def test_verdicts_handler_stops_reading_at_its_limit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ハンドラーも読みながら数えて打ち切る(ミドルウェアの上限より小さく差し替えて確かめる)。"""
    monkeypatch.setattr(mod_verdicts_api, "_BODY_MAX_BYTES", 10)
    result = _asgi_post(
        client,
        "/api/mods/nope/verdicts",
        headers=_verdict_headers(),
        chunks=[b"[    ", b"      ", b"   ]", b"x"],
    )
    assert result.status == 413
    assert result.json()["detail"] == "本文が大きすぎます"
    assert result.receive_calls == 2

    result = _asgi_post(
        client, "/api/mods/nope/verdicts", headers=_verdict_headers(11), chunks=[b"[]"]
    )
    assert result.status == 413
    assert result.receive_calls == 0


# -- ルートとの対応(漏れ防止) -------------------------------------------------------------

# 既定の上限(`DEFAULT_BODY_LIMIT`)から広げた・狭めたルートと、その上限。
_EXPECTED_LIMITS: dict[tuple[str, str], int] = {
    ("POST", "/api/assets"): MAX_UPLOAD_BYTES + MULTIPART_OVERHEAD,
    ("POST", "/api/users/me/avatar"): MAX_AVATAR_BYTES + MULTIPART_OVERHEAD,
    ("POST", "/api/search/similar-image"): MAX_UPLOAD_BYTES + MULTIPART_OVERHEAD,
    ("POST", "/api/imports/lineage"): MAX_ZIP_BYTES + MULTIPART_OVERHEAD,
    ("POST", "/api/settings/tag-dictionaries"): TAG_DICTIONARY_BODY_LIMIT,
    ("POST", "/api/sdwebui/import-params"): SDWEBUI_BODY_LIMIT,
    ("PUT", "/api/uploads/{token}"): MAX_UPLOAD_BYTES + MULTIPART_OVERHEAD,
    ("POST", "/api/uploads/{token}"): MAX_UPLOAD_BYTES + MULTIPART_OVERHEAD,
    ("POST", "/api/mods/{mod_id}/verdicts"): mod_verdicts_api._BODY_MAX_BYTES,
    ("POST", "/mcp"): MAX_REQUEST_BODY_BYTES,
    ("DELETE", "/mcp"): MAX_REQUEST_BODY_BYTES,
}

# ログインの前に本文を読んでよい(oidc モードの足切りの対象外の)ルート。`tests/test_shares.py`
# の「認証なしで応答するルート」のうち、本文を受けうるもの。
_EXPECTED_BEFORE_LOGIN = {
    ("POST", "/api/auth/logout"),
    ("PUT", "/api/uploads/{token}"),
    ("POST", "/api/uploads/{token}"),
    ("POST", "/api/mods/{mod_id}/verdicts"),
    ("POST", "/mcp"),
    ("DELETE", "/mcp"),
}

# ハンドラーの中で本文を自分で読む印。
_SELF_READ_MARKERS = ("request.stream()", "request.body()", "request.form()")


def _routes(client: TestClient) -> Iterator[tuple[str, str, Any, Any]]:
    """(メソッド, パス, エンドポイント, body_field)。更新系のメソッドだけ。"""
    for context in iter_route_contexts(client.app.routes):
        methods = context.methods or set()
        endpoint = context.endpoint
        body_field = context.body_field if isinstance(context.original_route, APIRoute) else None
        for method in sorted(methods & _UNSAFE):
            yield method, context.path, endpoint, body_field


def test_every_body_route_has_a_decided_limit(client: TestClient) -> None:
    limits: dict[tuple[str, str], int] = {}
    before_login: set[tuple[str, str]] = set()
    undecided: list[str] = []
    for method, path, endpoint, body_field in _routes(client):
        key = (method, path)
        policy = policy_for_endpoint(endpoint)
        if policy.max_bytes is not None:
            limits[key] = policy.max_bytes
        if policy.before_login:
            before_login.add(key)

        is_multipart = body_field is not None and type(body_field.field_info).__name__ in (
            "File",
            "Form",
        )
        reads_itself = False
        if body_field is None and inspect.isfunction(endpoint):
            takes_request = any(
                p.annotation in ("Request", Request)
                for p in inspect.signature(endpoint).parameters.values()
            )
            module_source = inspect.getsource(sys.modules[endpoint.__module__])
            reads_itself = takes_request and any(m in module_source for m in _SELF_READ_MARKERS)
        # multipart と、本文を自分で読むルートは、上限を明示する(既定の 16MiB に黙って落ちない)。
        if (is_multipart or reads_itself) and policy.max_bytes is None:
            undecided.append(f"{method} {path}")

    assert not undecided, "本文の上限を決めていないルート:\n" + "\n".join(undecided)
    assert limits == _EXPECTED_LIMITS
    assert before_login == _EXPECTED_BEFORE_LOGIN
