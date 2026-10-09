"""ADR-0019: 認可の網羅を `app.openapi()` から機械的に検証する(セキュリティ監査 テスト項目1)。

個々のエンドポイントの挙動は他のテストファイル(`test_auth_api.py`、
`test_auth_admin_enforcement.py` など)が担うが、こちらは「新しいエンドポイントを足したのに
認可を付け忘れる」事故を漏れなく検知するための網羅テスト。
"""

from __future__ import annotations

import re
import uuid

import pytest
from fastapi.testclient import TestClient

from tests.conftest import login_as

_DUMMY_UUID = "00000000-0000-4000-8000-000000000000"
_PATH_PARAM_RE = re.compile(r"\{[^}]+\}")

_HTTP_METHODS = ("get", "post", "put", "patch", "delete")


def _fill_path_params(path: str) -> str:
    return _PATH_PARAM_RE.sub(_DUMMY_UUID, path)


def _iter_paths_and_methods(app) -> list[tuple[str, str]]:  # noqa: ANN001
    paths = app.openapi()["paths"]
    result: list[tuple[str, str]] = []
    for path, operations in paths.items():
        for method in _HTTP_METHODS:
            if method in operations:
                result.append((path, method))
    return result


def test_all_non_auth_endpoints_require_login(client_oidc: TestClient) -> None:
    """`/api/auth/*` 以外の全 (path, method) は、未ログインなら 401 になる。"""
    failures: list[str] = []
    for path, method in _iter_paths_and_methods(client_oidc.app):
        if path.startswith("/api/auth/"):
            continue
        # 1回限りのアップロード・ダウンロード URL の受け口は、URL のトークン自体が認可(ログインは
        # 要らない。ADR-0023 7章 2・8章 3)。不正・期限切れのトークンの扱いは
        # tests/test_mcp_feedback.py と tests/test_mcp_image_access.py で確かめる。
        if path.startswith(("/api/uploads/", "/api/downloads/")):
            continue
        # ログイン不要の共有リンク(ADR-0029 6章)。見せる範囲は tests/test_shares.py で確かめる。
        if path.startswith("/api/public/"):
            continue
        # 生存確認(Issue #43)。ログイン不要で、応答は tests/test_health_api.py で確かめる。
        if path == "/api/health":
            continue
        filled = _fill_path_params(path)
        response = client_oidc.request(method, filled)
        if response.status_code != 401:
            failures.append(f"{method.upper()} {path} -> {response.status_code}")
    assert not failures, "401 にならないエンドポイントがあります:\n" + "\n".join(failures)


def test_all_settings_and_comfyui_mutations_require_admin(client_oidc: TestClient) -> None:
    """`/api/settings/*`、`/api/comfyui/*`、`/api/sdwebui/*`(ADR-0038)の非 GET は、一般ユーザー
    なら全て 403 になる。"""
    login_as(client_oidc, "matrix-user@example.com", "Matrix User")

    failures: list[str] = []
    for path, method in _iter_paths_and_methods(client_oidc.app):
        if method == "get":
            continue
        if not path.startswith(("/api/settings/", "/api/comfyui/", "/api/sdwebui/")):
            continue
        filled = _fill_path_params(path)
        response = client_oidc.request(method, filled, json={})
        if response.status_code != 403:
            failures.append(f"{method.upper()} {path} -> {response.status_code}: {response.text}")
    assert not failures, "403 にならない管理者限定エンドポイントがあります:\n" + "\n".join(failures)


def test_dummy_uuid_is_a_valid_uuid() -> None:
    uuid.UUID(_DUMMY_UUID)


@pytest.mark.parametrize("path", ["/api/auth/me"])
def test_auth_router_itself_is_excluded_from_the_login_sweep(
    client_oidc: TestClient, path: str
) -> None:
    """`/api/auth/me` は未ログインでも 200(user: null)を返す仕様で、401 ではない
    (上のスイープが `/api/auth/*` を除外していることの裏付け)。
    """
    response = client_oidc.get(path)
    assert response.status_code == 200
