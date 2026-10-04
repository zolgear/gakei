"""`/mcp` の入口(ADR-0023 1章・2章)。SDK のセッションマネージャーに渡す前に、次を順に確かめる。

1. 管理者設定で無効なら 404。
2. `Origin` ヘッダーがあり、GAKEI 自身の origin でなければ 403(DNS リバインディング対策)。
3. 認証。個人モードは認証なしで `LOCAL_ADMIN`。認証モードは `Authorization: Bearer <token>`
   のアクセストークンだけを受け付ける(Cookie では受けない)。無い・不明・失効は 401。

SDK の transport security(Host/Origin の許可リスト)は使わない。待ち受けのホスト名を事前に
列挙できない(LAN の IP で開く使い方がある)ため、Origin の判定をここで行う。
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

import anyio.to_thread
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from app.auth.deps import bearer_token
from app.auth.identity import LOCAL_ADMIN
from app.auth.runtime import AuthRuntime
from app.domain import api_tokens as api_tokens_domain
from app.domain import mcp_settings
from app.i18n import t
from app.mcp.context import SCOPE_KEY, McpRequestContext, current_mcp_context


def _origin_of(url: str) -> str | None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}".lower()


def _is_ip_or_localhost(hostname: str | None) -> bool:
    if not hostname:
        return False
    if hostname == "localhost":
        return True
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return True


def origin_allowed(
    origin: str | None, host_header: str | None, public_base_url: str | None
) -> bool:
    """`Origin` が GAKEI 自身の origin かどうか。`Origin` が無い(ブラウザ以外の)呼び出しは通す。

    - `PUBLIC_BASE_URL` の origin と一致すれば許す。
    - `Host` ヘッダーと同じ host:port でも許すが、IP アドレスか `localhost` の場合に限る。
      DNS リバインディングでは攻撃者のドメイン名が GAKEI の IP を指すため、`Origin` と `Host`
      はどちらも攻撃者のドメイン名になり、単純な一致では防げない。IP・localhost で開いている
      場合はこの攻撃が成り立たない。ホスト名で公開するときは `PUBLIC_BASE_URL` を設定する。
    """
    if not origin:
        return True
    normalized = _origin_of(origin)
    if normalized is None:
        return False
    if public_base_url and _origin_of(public_base_url) == normalized:
        return True
    parts = urlsplit(origin)
    if host_header and parts.netloc.lower() == host_header.lower():
        return _is_ip_or_localhost(parts.hostname)
    return False


class McpEndpoint:
    """`/mcp` の ASGI アプリ。セッションマネージャーは lifespan が `app.state` に置く。"""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":  # pragma: no cover - HTTP 以外は来ない
            return
        request = Request(scope, receive)
        state = request.app.state
        runtime: AuthRuntime = state.auth_runtime
        session_factory = state.session_factory

        def _is_enabled() -> bool:
            with session_factory() as db:
                return mcp_settings.is_enabled(db)

        if not await anyio.to_thread.run_sync(_is_enabled):
            await JSONResponse({"detail": t("mcp.disabled")}, status_code=404)(scope, receive, send)
            return

        if not origin_allowed(
            request.headers.get("origin"), request.headers.get("host"), runtime.public_base_url
        ):
            await JSONResponse({"detail": t("mcp.originForbidden")}, status_code=403)(
                scope, receive, send
            )
            return

        if not runtime.is_oidc:
            user = LOCAL_ADMIN
            token_id = None
        else:
            raw = bearer_token(request)
            if raw is None:
                await self._unauthorized(scope, receive, send, t("mcp.tokenRequired"))
                return

            def _authenticate() -> api_tokens_domain.TokenPrincipal | None:
                with session_factory() as db:
                    principal = api_tokens_domain.authenticate(
                        db, raw, runtime.admin_email_set(), runtime.allowed_email_domain_set()
                    )
                    db.commit()
                    return principal

            principal = await anyio.to_thread.run_sync(_authenticate)
            if principal is None:
                await self._unauthorized(scope, receive, send, t("mcp.tokenInvalid"))
                return
            user = principal.user
            token_id = principal.token_id

        context = McpRequestContext(
            user=user,
            api_token_id=token_id,
            base_url=mcp_settings.resolve_public_base(
                runtime.public_base_url, str(request.base_url)
            ),
            state=state,
        )
        scope[SCOPE_KEY] = context
        reset = current_mcp_context.set(context)
        try:
            await state.mcp_session_manager.handle_request(scope, receive, send)
        finally:
            current_mcp_context.reset(reset)

    @staticmethod
    async def _unauthorized(scope: Scope, receive: Receive, send: Send, detail: str) -> None:
        response = JSONResponse(
            {"detail": detail}, status_code=401, headers={"WWW-Authenticate": "Bearer"}
        )
        await response(scope, receive, send)
