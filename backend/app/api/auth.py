"""ログイン・ログアウト(ADR-0019)。

BFF 方式: Authorization Code + PKCE をサーバー側(Authlib)で行い、SPA には Cookie
(`gakei_session`)しか渡さない(`<img src>` と SSE(EventSource)はヘッダーを付けられない
ため)。このルーター自体には認可を付けない(`main.py` が他の9ルーターにだけ
`Depends(require_user)` を付ける)。

ADR-0034: モードと接続は `AuthRuntime`(実効の設定)から読む。管理者のテストログイン
(`/test-login`)は、通常のログインと同じ `/callback` に戻す(手続き中の `gakei_oidc` に印を
付けて区別する)。
"""

from __future__ import annotations

import html
import json
from collections.abc import Callable
from typing import Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth.deps import (
    get_current_user,
    get_oidc_client,
    get_oidc_client_factory,
    require_admin,
)
from app.auth.identity import CurrentUser
from app.auth.oidc import OidcClient, OidcIdentity
from app.auth.runtime import AuthRuntime, OidcClientFactory, get_auth_runtime
from app.auth.sessions import (
    MAX_SESSIONS_PER_USER,
    create_session,
    is_email_allowed,
    prune_oldest_sessions,
    purge_expired,
    revoke,
    upsert_user,
)
from app.config import Settings
from app.deps import get_session, get_settings
from app.domain import auth_settings
from app.domain.avatars import avatar_url
from app.domain.schemas import AuthLogoutResponse, AuthMeResponse, AuthUser
from app.i18n import t

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _sanitize_next(raw: str | None) -> str:
    """`next` を `/` 始まりの相対パスだけに絞る(オープンリダイレクト対策)。

    `//evil.example.com` はスキームを省いたプロトコル相対 URL として、`\\` は一部ブラウザが
    `/` と同一視して解釈するため、どちらも拒否する。
    """
    if not raw:
        return "/"
    if not raw.startswith("/") or raw.startswith("//") or "\\" in raw:
        return "/"
    return raw


def _to_auth_user(user: CurrentUser) -> AuthUser:
    assert user.id is not None  # none モードでは呼ばない(id=None は LOCAL_ADMIN のみ)
    return AuthUser(
        id=user.id,
        name=user.name,
        email=user.email,
        role=user.role,
        avatar_url=avatar_url(user.id, user.avatar_sha256),
    )


# 手続き中の `gakei_oidc`(署名付き)に入れるテストログインの印(ADR-0034 2章の3)。
_TEST_SESSION_KEY = "gakei_auth_test"

TestLoginError = Literal[
    "no_connection",
    "callback_failed",
    "email_missing",
    "email_unverified",
    "email_not_admin",
    "config_changed",
]


# 文言のキーはリテラルで書く(`tests/test_i18n.py` がコード中のキーを洗い出すため)。
_TEST_ERROR_MESSAGES: dict[str, Callable[[], str]] = {
    "no_connection": lambda: t("auth.testLogin.errors.no_connection"),
    "callback_failed": lambda: t("auth.testLogin.errors.callback_failed"),
    "email_missing": lambda: t("auth.testLogin.errors.email_missing"),
    "email_unverified": lambda: t("auth.testLogin.errors.email_unverified"),
    "email_not_admin": lambda: t("auth.testLogin.errors.email_not_admin"),
    "config_changed": lambda: t("auth.testLogin.errors.config_changed"),
}


def _set_session_cookie(response: Response, raw_token: str, runtime: AuthRuntime) -> None:
    response.set_cookie(
        "gakei_session",
        raw_token,
        httponly=True,
        samesite="lax",
        secure=runtime.public_base_is_https,
        path="/",
        max_age=runtime.session_hours * 3600,
    )


def _create_login_session(db: Session, identity: OidcIdentity, runtime: AuthRuntime) -> str:
    user = upsert_user(db, identity, runtime.admin_email_set())
    purge_expired(db)
    # 複数の端末で同時にログインできる(2026-09-28 改訂。ADR-0019 2章)。他の端末の
    # セッションは消さず、1ユーザーあたり MAX_SESSIONS_PER_USER 件を超える古いものだけ消す。
    prune_oldest_sessions(db, user.id, keep=MAX_SESSIONS_PER_USER - 1)
    raw_token = create_session(db, user, runtime.session_hours)
    db.commit()
    return raw_token


@router.get("/me", response_model=AuthMeResponse, operation_id="get_auth_me")
def get_me(
    runtime: AuthRuntime = Depends(get_auth_runtime),
    user: CurrentUser | None = Depends(get_current_user),
) -> AuthMeResponse:
    if not runtime.is_oidc:
        return AuthMeResponse(mode="none", user=None)
    return AuthMeResponse(mode="oidc", user=_to_auth_user(user) if user is not None else None)


@router.get("/login", operation_id="login")
async def login(
    request: Request,
    next: str | None = None,
    runtime: AuthRuntime = Depends(get_auth_runtime),
    oidc_client: OidcClient | None = Depends(get_oidc_client),
) -> Response:
    if not runtime.is_oidc or oidc_client is None:
        raise HTTPException(status_code=404, detail=t("auth.disabled"))

    # 前のテストログインの印が残っていれば消す(通常のログインとして戻ってくるように)。
    request.session.pop(_TEST_SESSION_KEY, None)
    request.session["gakei_next"] = _sanitize_next(next)
    redirect_uri = auth_settings.redirect_uri_for(runtime.public_base_url or "")
    return await oidc_client.authorize_redirect(request, redirect_uri)


def _test_target(
    db: Session, settings: Settings, runtime: AuthRuntime
) -> tuple[Literal["pending", "main"], auth_settings.OidcConnection] | None:
    """テストログインに使う接続(仮登録があればそれ、無ければ本登録)。"""
    pending = auth_settings.get_pending(db)
    if pending is not None:
        return "pending", pending.to_connection(settings.data_dir)
    connection = runtime.config.connection
    if connection is not None:
        return "main", connection
    return None


@router.get("/test-login", operation_id="auth_test_login")
async def test_login(
    request: Request,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    runtime: AuthRuntime = Depends(get_auth_runtime),
    client_factory: OidcClientFactory = Depends(get_oidc_client_factory),
    _user: CurrentUser = Depends(require_admin),
) -> Response:
    """管理者のテストログイン(ADR-0034 2章の3)。設定画面がポップアップで開く。

    仮登録の接続(無ければ本登録)で IdP へリダイレクトする。結果は `/callback` が
    `window.opener.postMessage` で設定画面に返す。
    """
    target = _test_target(db, settings, runtime)
    if target is None:
        return _test_result_page(ok=False, error="no_connection")
    kind, connection = target
    request.session.pop("gakei_next", None)
    request.session[_TEST_SESSION_KEY] = {"target": kind, "fingerprint": connection.fingerprint()}
    client = client_factory(connection)
    return await client.authorize_redirect(request, connection.redirect_uri)


@router.get("/callback", operation_id="auth_callback")
async def callback(
    request: Request,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    runtime: AuthRuntime = Depends(get_auth_runtime),
    oidc_client: OidcClient | None = Depends(get_oidc_client),
    client_factory: OidcClientFactory = Depends(get_oidc_client_factory),
) -> Response:
    test_mark = request.session.get(_TEST_SESSION_KEY)
    if isinstance(test_mark, dict):
        return await _complete_test_login(request, db, settings, runtime, client_factory, test_mark)
    if not runtime.is_oidc or oidc_client is None:
        raise HTTPException(status_code=404, detail=t("auth.disabled"))

    # callback はブラウザの最上位ナビゲーションなので、失敗を JSON で返すと生の JSON が画面に
    # 出る。ログイン画面(SPA)へ `login_error` 付きで戻し、そちらで文言を出す。
    try:
        identity = await oidc_client.complete_login(request)
    except HTTPException:
        request.session.clear()
        return RedirectResponse(url="/?login_error=callback_failed", status_code=302)
    if not identity.email:
        request.session.clear()
        # M-1(2026-09-27 追記): email_verified が明示的に False だったため oidc.py が
        # email を落としたケースは、「メールアドレスが設定されていない」(email_missing)
        # ではなく専用の文言(email_unverified)を出す。
        error_code = "email_unverified" if identity.email_verified is False else "email_missing"
        return RedirectResponse(url=f"/?login_error={error_code}", status_code=302)
    if not is_email_allowed(
        identity.email, runtime.allowed_email_domain_set(), runtime.admin_email_set()
    ):
        request.session.clear()
        return RedirectResponse(url="/?login_error=email_not_allowed", status_code=302)

    raw_token = _create_login_session(db, identity, runtime)

    next_path = _sanitize_next(request.session.pop("gakei_next", None))
    # state/nonce/PKCE の一時保存(`gakei_oidc` Cookie)は用済みなので捨てる。
    request.session.clear()

    response = RedirectResponse(url=next_path, status_code=302)
    _set_session_cookie(response, raw_token, runtime)
    return response


async def _complete_test_login(
    request: Request,
    db: Session,
    settings: Settings,
    runtime: AuthRuntime,
    client_factory: OidcClientFactory,
    test_mark: dict,
) -> Response:
    """テストログインの戻り(ADR-0034 2章の3)。成功したら仮登録を本登録にし、指紋とメールを
    記録する。個人モードの間はセッションを作って Cookie を渡す(oidc に切り替えた瞬間から
    ログイン済みとして残るように)。oidc の間は今のセッションをそのまま使う。
    """

    # 印は1回限り。どの結果でも手続き中の一時保存ごと捨てる。
    def fail(error: TestLoginError) -> Response:
        request.session.clear()
        return _test_result_page(ok=False, error=error)

    target = _test_target(db, settings, runtime)
    if (
        target is None
        or target[0] != test_mark.get("target")
        or target[1].fingerprint() != test_mark.get("fingerprint")
    ):
        # テストの途中で接続の設定が変わった(別のタブで差し替えた・取り消したなど)。
        return fail("config_changed")
    kind, connection = target

    client = client_factory(connection)
    try:
        identity = await client.complete_login(request)
    except HTTPException:
        return fail("callback_failed")
    if not identity.email:
        return fail("email_unverified" if identity.email_verified is False else "email_missing")
    email = identity.email.strip().lower()
    if email not in runtime.admin_email_set():
        return fail("email_not_admin")

    if kind == "pending":
        auth_settings.save_connection(db, settings.data_dir, connection)
        auth_settings.discard_pending(db, settings.data_dir)
    auth_settings.record_verified(db, connection.fingerprint(), email)
    db.commit()
    runtime.reload(db)

    request.session.clear()
    response = _test_result_page(ok=True, error=None)
    if not runtime.is_oidc:
        raw_token = _create_login_session(db, identity, runtime)
        _set_session_cookie(response, raw_token, runtime)
    return response


def _test_result_page(*, ok: bool, error: TestLoginError | None) -> HTMLResponse:
    """結果を `window.opener.postMessage` で設定画面に返して閉じる最小の HTML。

    設定画面が同じオリジンのときだけ受け取れる(`targetOrigin` に `location.origin`)。
    ポップアップがブロックされず開いたが opener が無い(直接開いた)ときは、文言だけを出す。
    """
    payload: dict[str, object] = {"type": "gakei-auth-test", "ok": ok}
    if error is not None:
        payload["error"] = error
    message = t("auth.testLogin.success") if error is None else _TEST_ERROR_MESSAGES[error]()
    # `</script>` などで抜け出せないよう、`<` `>` `&` をエスケープして埋め込む。
    script_payload = (
        json.dumps(payload).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    )
    body = (
        '<!doctype html><html><head><meta charset="utf-8">'
        f"<title>{html.escape(t('auth.testLogin.title'))}</title></head><body>"
        f"<p>{html.escape(message)}</p>"
        "<script>(function(){var r="
        + script_payload
        + ";if(window.opener){window.opener.postMessage(r,location.origin);window.close();}})();"
        "</script></body></html>"
    )
    return HTMLResponse(body, headers={"Cache-Control": "no-store"})


@router.post("/logout", response_model=AuthLogoutResponse, operation_id="logout")
async def logout(
    response: Response,
    runtime: AuthRuntime = Depends(get_auth_runtime),
    db: Session = Depends(get_session),
    gakei_session: str | None = Cookie(default=None),
    oidc_client: OidcClient | None = Depends(get_oidc_client),
) -> AuthLogoutResponse:
    if gakei_session:
        revoke(db, gakei_session)
        db.commit()
    # I-9(2026-09-27 追記): 削除する Cookie にも設定時と同じ属性を付ける(ブラウザによっては
    # 属性が食い違う delete_cookie を無視することがあるため)。
    response.delete_cookie(
        "gakei_session",
        path="/",
        httponly=True,
        samesite="lax",
        secure=runtime.public_base_is_https,
    )
    # I-7(2026-09-27 追記): ログアウト後もブラウザのキャッシュに4K画像等が残らないよう、
    # キャッシュを消させる(Cookie は上の delete_cookie で個別に消しているので対象外)。
    response.headers["Clear-Site-Data"] = '"cache"'

    redirect_url = "/"
    if runtime.is_oidc and oidc_client is not None and runtime.client_id:
        end_session = await oidc_client.end_session_endpoint()
        if end_session:
            params = {
                "post_logout_redirect_uri": f"{(runtime.public_base_url or '').rstrip('/')}/",
                "client_id": runtime.client_id,
            }
            redirect_url = f"{end_session}?{urlencode(params)}"

    return AuthLogoutResponse(redirect_url=redirect_url)
