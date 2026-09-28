"""FastAPI の Depends 用アクセサ(認証。ADR-0019)。`app.state` から取り出すだけの
`app/deps.py` と同じ流儀。
"""

from __future__ import annotations

from fastapi import Cookie, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.auth.identity import LOCAL_ADMIN, CurrentUser
from app.auth.oidc import OidcClient
from app.auth.sessions import find_user_for_token
from app.config import Settings
from app.deps import get_session, get_settings
from app.domain import api_tokens as api_tokens_domain
from app.domain import mcp_settings
from app.i18n import t


def get_oidc_client(request: Request) -> OidcClient | None:
    """`app.state.oidc_client`(oidc モードのみ lifespan が設定する)を返す。

    テストではこの Depends を `tests/oidc_fake.py::FakeOidcClient` に差し替える
    (`api/settings.py::get_key_validator` と同じ流儀)。`none` モードでは未設定のため
    None(呼び出し側の `/api/auth/login`・`/callback` は先に `auth_mode` を見て 404 にする)。
    """
    return getattr(request.app.state, "oidc_client", None)


def get_current_user(
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_session),
    gakei_session: str | None = Cookie(default=None),
) -> CurrentUser | None:
    """`none` モードは常に `LOCAL_ADMIN`。`oidc` モードは Cookie `gakei_session` のセッション
    から解決する(Cookie が無い・期限切れ・不明なトークンは None = 未ログイン)。
    """
    if settings.auth_mode == "none":
        return LOCAL_ADMIN
    if gakei_session is None:
        return None
    # L-3: 管理者一覧・許可ドメインは DB のセッションではなく、現在の設定から毎回評価する。
    return find_user_for_token(
        db, gakei_session, settings.admin_email_set(), settings.allowed_email_domain_set()
    )


def require_user(user: CurrentUser | None = Depends(get_current_user)) -> CurrentUser:
    if user is None:
        raise HTTPException(status_code=401, detail=t("auth.required"))
    return user


def require_admin(user: CurrentUser = Depends(require_user)) -> CurrentUser:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail=t("auth.adminRequired"))
    return user


def bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization")
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


def require_user_or_api_token(
    request: Request,
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_session),
    user: CurrentUser | None = Depends(get_current_user),
) -> CurrentUser:
    """Cookie のログインに加えて、MCP 用のアクセストークン(`Authorization: Bearer`)も受ける。

    ADR-0023 7章 3: 使うのは画像の本体を取る `GET /api/assets/{id}/content` だけ。他の REST
    API には広げない(そちらは `require_user` のまま)。トークンは MCP のためのものなので、
    MCP が無効のときは受け付けない。失効済み・不明なトークンは 401。
    """
    if user is not None:
        return user
    raw = bearer_token(request)
    if raw is None or not mcp_settings.is_enabled(db):
        raise HTTPException(status_code=401, detail=t("auth.required"))
    principal = api_tokens_domain.authenticate(
        db, raw, settings.admin_email_set(), settings.allowed_email_domain_set()
    )
    if principal is None:
        db.rollback()
        raise HTTPException(
            status_code=401,
            detail=t("mcp.tokenInvalid"),
            headers={"WWW-Authenticate": "Bearer"},
        )
    # `last_used_at` の更新を確定する。
    db.commit()
    return principal.user
