"""`Host` と `Origin` の確認(DNS リバインディングと CSRF の対策。ADR-0019 2章、2026-10-19 追記)。

全リクエストの入口の純粋な ASGI ミドルウェア `HostOriginGuardMiddleware` が、次を順に確かめる。

1. 実効の認証モードが none(個人モード)のときだけ、`Host` のホスト部が次のどれかでなければ
   421 Misdirected Request。IP アドレス(`[::1]` のような IPv6 も)、`localhost`、
   `PUBLIC_BASE_URL` のホスト、`ALLOWED_HOSTS` に載せたホスト名。`Host` が無い要求も断る。
   oidc モードでは確かめない(リバースプロキシが `Host` を書き換えることがあり、ログインの
   Cookie がホスト名に縛られているので、リバインディングでは Cookie が送られない)。
2. 全モードで、更新系(POST / PUT / PATCH / DELETE)に `Origin` ヘッダーがあり、
   `origin_allowed` に当たらなければ 403。`Origin` が無ければ通す(curl、Mod の判定受付など
   ブラウザ以外)。`Origin: null` は断る。

実効のモードと `PUBLIC_BASE_URL` は `AuthRuntime` から毎回読む(画面で再起動なしに切り替わる。
ADR-0034)。`origin_allowed` は `/mcp`(ADR-0023)も使う。

`BaseHTTPMiddleware` は使わない(SSE のストリーミングをバッファしてしまうため。`main.py` の
`LocaleMiddleware` と同じ)。
"""

from __future__ import annotations

import ipaddress
import logging
from collections.abc import Iterable
from urllib.parse import urlsplit

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.i18n import parse_accept_language, t, use_locale

logger = logging.getLogger(__name__)

# 更新系のメソッド。GET / HEAD / OPTIONS は `Origin` を見ない(読み取りは同一オリジン
# ポリシーで応答を読めないため。`Host` の確認は none モードでは全メソッドに掛かる)。
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# ログに出す `Host` / `Origin` の長さの上限。
_LOG_VALUE_MAX = 200


def _origin_of(url: str) -> str | None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}".lower()


def _normalize_hostname(hostname: str | None) -> str | None:
    if not hostname:
        return None
    value = hostname.strip().lower().rstrip(".")
    return value or None


def host_of(host_header: str | None) -> str | None:
    """`Host` ヘッダー(`host`、`host:port`、`[::1]:8000`)のホスト部を小文字で返す。"""
    if not host_header:
        return None
    value = host_header.strip()
    # authority 以外の文字(userinfo の `@`、パスなど)を含むものは `Host` として扱わない
    # (`evil@127.0.0.1` を IP と読み違えないように)。
    if not value or any(c in value for c in "@/?#\\ \t"):
        return None
    try:
        hostname = urlsplit(f"//{value}").hostname
    except ValueError:
        return None
    return _normalize_hostname(hostname)


def is_ip_or_localhost(hostname: str | None) -> bool:
    """IP アドレスか `localhost`。DNS リバインディングでは攻撃者のドメイン名が `Host` に
    入るので、この2つで開いている限り攻撃は成り立たない。"""
    hostname = _normalize_hostname(hostname)
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
    origin: str | None,
    host_header: str | None,
    public_base_url: str | None,
    allowed_hosts: Iterable[str] = (),
) -> bool:
    """`Origin` が GAKEI 自身の origin かどうか。`Origin` が無い(ブラウザ以外の)呼び出しは通す。

    - `PUBLIC_BASE_URL` の origin と一致すれば許す。
    - `Host` ヘッダーと同じ host:port でも許すが、ホストが IP アドレスか `localhost`、または
      `ALLOWED_HOSTS` に載せたホスト名の場合に限る。DNS リバインディングでは攻撃者のドメイン名が
      GAKEI の IP を指すため、`Origin` と `Host` はどちらも攻撃者のドメイン名になり、単純な
      一致では防げない。ホスト名で公開するときは `PUBLIC_BASE_URL` か `ALLOWED_HOSTS` を設定する。
    """
    if not origin:
        return True
    normalized = _origin_of(origin)
    if normalized is None:
        return False
    if public_base_url and _origin_of(public_base_url) == normalized:
        return True
    parts = urlsplit(origin)
    if host_header and parts.netloc.lower() == host_header.strip().lower():
        hostname = _normalize_hostname(parts.hostname)
        return is_ip_or_localhost(hostname) or (
            hostname is not None and hostname in set(allowed_hosts)
        )
    return False


def host_allowed(
    host_header: str | None, public_base_url: str | None, allowed_hosts: Iterable[str]
) -> bool:
    """個人モードで受け付ける `Host` か。`Host` が無ければ断る(HTTP/1.1 では必須)。"""
    hostname = host_of(host_header)
    if hostname is None:
        return False
    if is_ip_or_localhost(hostname):
        return True
    if public_base_url and hostname == _normalize_hostname(urlsplit(public_base_url).hostname):
        return True
    return hostname in set(allowed_hosts)


def _header(scope: Scope, name: bytes) -> str | None:
    for raw_name, raw_value in scope.get("headers", ()):
        if raw_name.lower() == name:
            return raw_value.decode("latin-1")
    return None


def _for_log(value: str | None) -> str:
    if value is None:
        return "<none>"
    return repr(value[:_LOG_VALUE_MAX])


class HostOriginGuardMiddleware:
    """`Host`(none モードだけ)と更新系の `Origin`(全モード)を確かめる純粋な ASGI
    ミドルウェア。`allowed_hosts` は `Settings.allowed_host_list()`。"""

    def __init__(self, app: ASGIApp, allowed_hosts: Iterable[str] = ()) -> None:
        self._app = app
        self._allowed_hosts = frozenset(allowed_hosts)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        app = scope.get("app")
        runtime = getattr(getattr(app, "state", None), "auth_runtime", None)
        # lifespan の前(runtime がまだ無い)は none モードとして厳しい側で扱う。
        is_oidc = bool(runtime is not None and runtime.is_oidc)
        public_base_url = runtime.public_base_url if runtime is not None else None
        host_header = _header(scope, b"host")
        path = scope.get("path", "")

        if not is_oidc and not host_allowed(host_header, public_base_url, self._allowed_hosts):
            logger.warning(
                "Host を受け付けなかった(個人モード): host=%s path=%s",
                _for_log(host_header),
                _for_log(path),
            )
            await self._reject(scope, receive, send, 421)
            return

        if scope.get("method", "").upper() in _UNSAFE_METHODS:
            origin = _header(scope, b"origin")
            if not origin_allowed(origin, host_header, public_base_url, self._allowed_hosts):
                logger.warning(
                    "別の Origin からの更新を断った: origin=%s host=%s method=%s path=%s",
                    _for_log(origin),
                    _for_log(host_header),
                    scope.get("method"),
                    _for_log(path),
                )
                await self._reject(scope, receive, send, 403)
                return

        await self._app(scope, receive, send)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send, status: int) -> None:
        """421(`Host`)か 403(`Origin`)で断る。"""
        # `LocaleMiddleware` より外側で断るので、言語はここで選ぶ。
        with use_locale(parse_accept_language(_header(scope, b"accept-language"))):
            if status == 421:
                detail = t("httpGuard.hostNotAllowed")
            else:
                detail = t("httpGuard.originNotAllowed")
        await JSONResponse({"detail": detail}, status_code=status)(scope, receive, send)
