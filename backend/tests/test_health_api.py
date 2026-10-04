"""`GET /api/health`(Issue #43)。ログインなしで `{"status": "ok"}` だけを返す。"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_ok_in_none_mode(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_ok_without_login_in_oidc_mode(client_oidc: TestClient) -> None:
    """oidc モードの未ログインでも 401 にならない(コンテナの HEALTHCHECK が通る)。"""
    response = client_oidc.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
