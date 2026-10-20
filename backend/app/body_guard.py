"""リクエストの本文の上限と、本文を読む前の認証の足切り(ADR-0019 2章、2026-10-20 追記)。

FastAPI は、本文を引数に取るルート(JSON のスキーマ、`File` / `Form`)では、依存
(`require_user` を含む)を解く前に本文を全部読む(multipart は一時ファイルに書き出す)。
そのままでは、未ログインの要求の本文も、大きさの上限なしに読まれてしまう。全リクエストの入口の
純粋な ASGI ミドルウェア `RequestBodyGuardMiddleware` が、本文を読む前に次を確かめる。

1. **本文の上限(全モード):** 既定は `DEFAULT_BODY_LIMIT`(JSON 用)。大きな本文を受ける
   ルートは、エンドポイントに `body_policy(max_bytes=...)` の印を付けて上限を広げる(値は各ルートの
   既存の上限の定数から作る)。`Content-Length` が上限を超えていれば本文を読まずに 413。
   `Content-Length` が無い本文(chunked)は、読んだ量を数え、超えた時点で 413 にする。
2. **認証の足切り(oidc モードだけ):** 本文のある要求で、`body_policy(before_login=True)` の
   印の無いルートは、本文を読む前に Cookie `gakei_session` のセッションか、アクセストークンの
   Bearer(`require_user_or_api_token` が受けるもの)が有効かを確かめ、どちらも無効なら 401
   (`auth.required`)。権限の判定(`require_user` / `require_admin`)は今のままで、ここは入口の
   足切りだけ。

**ルートとの対応の付け方:** ミドルウェアはルーティングの前に走るので、入口ではどのルートかが
分からない。そこで `receive` を包み、アプリが本文を最初に読もうとした時点(ルーティングの後で、
FastAPI では依存を解く前)で、ルーターが scope に置いたエンドポイント(`scope["endpoint"]`、
`scope["route"]`)の印を引いて確かめる。このとき、まだ本文は 1 バイトも読んでいない。パスの
一覧や正規表現を別に持たないので、ルートを足したときに一覧の直し忘れが起きない(印の無いルートは
既定の上限と、足切りありの側に倒れる)。本文を読まないルート(本文の無い要求、404 など)では
何もしない。

**断り方:** 断ると決めたら、`receive` から `HTTPException` を送出する(FastAPI は本文の読み込み
中の `HTTPException` をそのまま送り直す)。以後のアプリの送信は捨て、アプリが戻った後に、まだ
応答を始めていなければこのミドルウェアが 413 / 401 を返す(アプリが例外を握りつぶして別の応答を
作っても、こちらの応答になる)。既に応答を始めていた(本文を読みながら応答を流すルート)ときは、
それ以上は流さずに戻る。応答が終わっていないので、サーバーが接続を切る。

`BaseHTTPMiddleware` は使わない(SSE のストリーミングをバッファしてしまうため。`http_guard.py`
と同じ)。`HostOriginGuardMiddleware` の内側に置く(`Host` / `Origin` で断るものは先に断る)。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.i18n import parse_accept_language, t, use_locale

logger = logging.getLogger(__name__)

# 既定の本文の上限(JSON 用)。既存の JSON のルートで、正当にこれを超えるものは無い(画像は
# multipart か Asset の id で送り、base64 を JSON に載せない。ADR-0004)。
DEFAULT_BODY_LIMIT = 16 * 1024 * 1024

# multipart の包み(境界やヘッダー、ほかのフィールド)の分の余裕。画像を multipart で受ける
# ルートは、画像の上限にこれを足した値を本文の上限にする(`app/api/uploads.py` から移した値)。
MULTIPART_OVERHEAD = 1024 * 1024

# エンドポイントに付ける印の属性名。
_POLICY_ATTR = "__gakei_body_policy__"

_F = TypeVar("_F", bound=Callable[..., Any])


@dataclass(frozen=True)
class BodyPolicy:
    """ルートの本文の扱い。`max_bytes` が None なら既定の上限。`before_login` は、ログインの前に
    本文を読んでよい(認証をルートの中で自分で行う、または認証の要らない)ルート。"""

    max_bytes: int | None = None
    before_login: bool = False

    @property
    def limit(self) -> int:
        return self.max_bytes if self.max_bytes is not None else DEFAULT_BODY_LIMIT


DEFAULT_POLICY = BodyPolicy()


def body_policy(*, max_bytes: int | None = None, before_login: bool = False) -> Callable[[_F], _F]:
    """エンドポイントに本文の扱いの印を付けるデコレーター。`@router.post(...)` の下に書く。

    `max_bytes` は、そのルートの既存の上限の定数から作る(multipart なら包みの分の余裕を足す)。
    """
    policy = BodyPolicy(max_bytes=max_bytes, before_login=before_login)

    def decorate(endpoint: _F) -> _F:
        setattr(endpoint, _POLICY_ATTR, policy)
        return endpoint

    return decorate


def policy_for_endpoint(endpoint: object) -> BodyPolicy:
    """エンドポイント(関数か ASGI アプリのオブジェクト)の印。無ければ既定。"""
    policy = getattr(endpoint, _POLICY_ATTR, None)
    return policy if isinstance(policy, BodyPolicy) else DEFAULT_POLICY


def _policy_for_scope(scope: Scope) -> BodyPolicy:
    route = scope.get("route")
    if route is not None:
        policy = getattr(getattr(route, "endpoint", None), _POLICY_ATTR, None)
        if isinstance(policy, BodyPolicy):
            return policy
    return policy_for_endpoint(scope.get("endpoint"))


def _header(scope: Scope, name: bytes) -> str | None:
    for raw_name, raw_value in scope.get("headers", ()):
        if raw_name.lower() == name:
            return raw_value.decode("latin-1")
    return None


def _content_length(scope: Scope) -> int | None:
    value = _header(scope, b"content-length")
    if value is None:
        return None
    value = value.strip()
    return int(value) if value.isdigit() else None


def _has_body(scope: Scope) -> bool:
    """本文のある要求か(`Content-Length` > 0 か、`Transfer-Encoding` あり)。`Content-Length`
    が数でなければ、本文があるものとして数える。"""
    if _header(scope, b"transfer-encoding") is not None:
        return True
    value = _header(scope, b"content-length")
    if value is None:
        return False
    length = _content_length(scope)
    return length is None or length > 0


def _is_authenticated(scope: Scope) -> bool:
    """Cookie `gakei_session` のセッションか、アクセストークンの Bearer が有効か(スレッドで呼ぶ)。

    判定は `get_current_user` と `require_user_or_api_token` と同じ関数を使う。ここでは確かめる
    だけなので、トークンの `last_used_at` の更新は確定しない(ルートの依存が改めて行う)。
    """
    from app.auth.deps import bearer_token
    from app.auth.sessions import find_user_for_token
    from app.domain import api_tokens as api_tokens_domain
    from app.domain import mcp_settings

    state = scope["app"].state
    runtime = state.auth_runtime
    request = Request(scope)
    admin_emails = runtime.admin_email_set()
    allowed_domains = runtime.allowed_email_domain_set()
    with state.session_factory() as db:
        try:
            cookie = request.cookies.get("gakei_session")
            if cookie and find_user_for_token(db, cookie, admin_emails, allowed_domains):
                return True
            raw = bearer_token(request)
            if raw is None or not mcp_settings.is_enabled(db):
                return False
            try:
                principal = api_tokens_domain.authenticate(db, raw, admin_emails, allowed_domains)
            except api_tokens_domain.ApiTokenExpiredError:
                return False
            return principal is not None
        finally:
            db.rollback()


class _Rejected(HTTPException):
    """本文を読む前・読む途中で断るときに `receive` から送出する。"""


class _Guard:
    """1 つの要求の状態。"""

    def __init__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.scope = scope
        self._receive = receive
        self._send = send
        self.checked = False
        self.limit = DEFAULT_BODY_LIMIT
        self.received = 0
        self.rejection: _Rejected | None = None
        self.response_started = False

    def _reject(self, status: int) -> _Rejected:
        with use_locale(parse_accept_language(_header(self.scope, b"accept-language"))):
            detail = t("auth.required") if status == 401 else t("httpGuard.bodyTooLarge")
        self.rejection = _Rejected(status_code=status, detail=detail)
        if status == 413:
            logger.info(
                "本文が上限を超えたので断った: path=%r limit=%d received=%d content-length=%s",
                self.scope.get("path", "")[:200],
                self.limit,
                self.received,
                _header(self.scope, b"content-length"),
            )
        return self.rejection

    async def _check_before_body(self) -> None:
        """アプリが本文を最初に読もうとしたとき(ルーティングの後、本文を読む前)に1回だけ呼ぶ。"""
        policy = _policy_for_scope(self.scope)
        self.limit = policy.limit
        length = _content_length(self.scope)
        if length is not None and length > self.limit:
            raise self._reject(413)
        if policy.before_login:
            return
        app = self.scope.get("app")
        runtime = getattr(getattr(app, "state", None), "auth_runtime", None)
        # lifespan の前(runtime がまだ無い)は none モードとして扱う(本文を受けるルートは
        # lifespan の後にしか動かない)。
        if runtime is None or not runtime.is_oidc:
            return
        if not await run_in_threadpool(_is_authenticated, self.scope):
            raise self._reject(401)

    async def receive(self) -> Message:
        if self.rejection is not None:
            raise self.rejection
        if not self.checked:
            self.checked = True
            await self._check_before_body()
        message = await self._receive()
        if message["type"] == "http.request":
            self.received += len(message.get("body", b""))
            if self.received > self.limit:
                raise self._reject(413)
        return message

    async def send(self, message: Message) -> None:
        # 断った後は、アプリの応答(例外から作った 413 / 401 も含む)を流さない。
        if self.rejection is not None:
            return
        if message["type"] == "http.response.start":
            self.response_started = True
        await self._send(message)


class RequestBodyGuardMiddleware:
    """本文の上限(全モード)と、本文を読む前の認証の足切り(oidc モード)を掛ける純粋な ASGI
    ミドルウェア。本文の無い要求(GET や SSE など)には何もしない。"""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not _has_body(scope):
            await self._app(scope, receive, send)
            return

        guard = _Guard(scope, receive, send)
        try:
            await self._app(scope, guard.receive, guard.send)
        except Exception:
            if guard.rejection is None:
                raise
        rejection = guard.rejection
        if rejection is None or guard.response_started:
            # 断る前に応答を始めていたら、それ以上は流さない(応答が終わっていないので、
            # サーバーが接続を切る)。
            return
        response = JSONResponse({"detail": rejection.detail}, status_code=rejection.status_code)
        await response(scope, receive, send)
