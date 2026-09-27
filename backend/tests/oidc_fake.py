"""OidcClient のテスト用スタブ(ADR-0019)。Authlib・実 IdP を通さず、`app/auth/oidc.py::OidcClient`
Protocol を満たすだけのダミー。`conftest.py::login_as()` が `queue_identity()` で次のログインの
本人情報を積み、`/api/auth/login` → `/api/auth/callback` の往復をテストに任せる。
"""

from __future__ import annotations

from fastapi import Request
from starlette.responses import RedirectResponse, Response

from app.auth.oidc import OidcIdentity

DEFAULT_END_SESSION_ENDPOINT = "https://idp.test/realms/x/protocol/openid-connect/logout"


class FakeOidcClient:
    def __init__(self, end_session_endpoint: str | None = DEFAULT_END_SESSION_ENDPOINT) -> None:
        self._queued: OidcIdentity | None = None
        self._end_session_endpoint = end_session_endpoint

    def queue_identity(self, identity: OidcIdentity) -> None:
        self._queued = identity

    async def authorize_redirect(self, request: Request, redirect_uri: str) -> Response:
        # 実際の IdP には飛ばず、そのままコールバック URL に戻す(テストは
        # `client.get("/api/auth/callback")` を直接呼ぶだけでよい)。
        return RedirectResponse(url=redirect_uri, status_code=302)

    async def complete_login(self, request: Request) -> OidcIdentity:
        if self._queued is None:
            raise AssertionError(
                "FakeOidcClient.complete_login: queue_identity() を呼ばずに呼び出された"
            )
        identity = self._queued
        self._queued = None
        return identity

    async def end_session_endpoint(self) -> str | None:
        return self._end_session_endpoint
