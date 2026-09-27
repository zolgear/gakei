"""ログイン・ログアウト(ADR-0019)。

BFF 方式: Authorization Code + PKCE をサーバー側(Authlib)で行い、SPA には Cookie
(`gakei_session`)しか渡さない(`<img src>` と SSE(EventSource)はヘッダーを付けられない
ため)。このルーター自体には認可を付けない(`main.py` が他の9ルーターにだけ
`Depends(require_user)` を付ける)。
"""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user, get_oidc_client
from app.auth.identity import CurrentUser
from app.auth.oidc import OidcClient
from app.auth.sessions import (
    create_session,
    is_email_allowed,
    purge_expired,
    revoke,
    revoke_all_for_user,
    upsert_user,
)
from app.config import Settings
from app.deps import get_session, get_settings
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


@router.get("/me", response_model=AuthMeResponse, operation_id="get_auth_me")
def get_me(
    settings: Settings = Depends(get_settings),
    user: CurrentUser | None = Depends(get_current_user),
) -> AuthMeResponse:
    if settings.auth_mode == "none":
        return AuthMeResponse(mode="none", user=None)
    return AuthMeResponse(mode="oidc", user=_to_auth_user(user) if user is not None else None)


@router.get("/login", operation_id="login")
async def login(
    request: Request,
    next: str | None = None,
    settings: Settings = Depends(get_settings),
    oidc_client: OidcClient | None = Depends(get_oidc_client),
) -> Response:
    if settings.auth_mode == "none":
        raise HTTPException(status_code=404, detail=t("auth.disabled"))
    # oidc モードでは lifespan の check_auth_env が起動時に保証している。
    assert oidc_client is not None

    request.session["gakei_next"] = _sanitize_next(next)
    redirect_uri = f"{(settings.public_base_url or '').rstrip('/')}/api/auth/callback"
    return await oidc_client.authorize_redirect(request, redirect_uri)


@router.get("/callback", operation_id="auth_callback")
async def callback(
    request: Request,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    oidc_client: OidcClient | None = Depends(get_oidc_client),
) -> Response:
    if settings.auth_mode == "none":
        raise HTTPException(status_code=404, detail=t("auth.disabled"))
    assert oidc_client is not None

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
        identity.email, settings.allowed_email_domain_set(), settings.admin_email_set()
    ):
        request.session.clear()
        return RedirectResponse(url="/?login_error=email_not_allowed", status_code=302)

    user = upsert_user(db, identity, settings.admin_email_set())
    purge_expired(db)
    # I-3(2026-09-27 追記): 1ユーザー1セッションにする。再ログインで古いセッション
    # (別のブラウザ・端末で残っているものを含む)を無効化してから新しいセッションを作る。
    revoke_all_for_user(db, user.id)
    raw_token = create_session(db, user, settings.auth_session_hours)
    db.commit()

    next_path = _sanitize_next(request.session.pop("gakei_next", None))
    # state/nonce/PKCE の一時保存(`gakei_oidc` Cookie)は用済みなので捨てる。
    request.session.clear()

    response = RedirectResponse(url=next_path, status_code=302)
    response.set_cookie(
        "gakei_session",
        raw_token,
        httponly=True,
        samesite="lax",
        secure=settings.public_base_is_https,
        path="/",
        max_age=settings.auth_session_hours * 3600,
    )
    return response


@router.post("/logout", response_model=AuthLogoutResponse, operation_id="logout")
async def logout(
    response: Response,
    settings: Settings = Depends(get_settings),
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
        secure=settings.public_base_is_https,
    )
    # I-7(2026-09-27 追記): ログアウト後もブラウザのキャッシュに4K画像等が残らないよう、
    # キャッシュを消させる(Cookie は上の delete_cookie で個別に消しているので対象外)。
    response.headers["Clear-Site-Data"] = '"cache"'

    redirect_url = "/"
    if oidc_client is not None and settings.oidc_client_id:
        end_session = await oidc_client.end_session_endpoint()
        if end_session:
            params = {
                "post_logout_redirect_uri": f"{(settings.public_base_url or '').rstrip('/')}/",
                "client_id": settings.oidc_client_id,
            }
            redirect_url = f"{end_session}?{urlencode(params)}"

    return AuthLogoutResponse(redirect_url=redirect_url)
