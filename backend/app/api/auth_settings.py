"""認証の設定を管理者設定の画面から行う(ADR-0034)。

- `GET /api/settings/auth`: 実効の設定と出どころ、仮登録、テストの記録、有効にできない理由。
- `PUT /api/settings/auth/connection`: 接続の仮登録(形式の検査と Discovery 文書の取得まで)。
- `DELETE /api/settings/auth/connection/pending`: 仮登録の取り消し。
- `PATCH /api/settings/auth`: モード・管理者のメール・許可ドメイン・セッションの長さ。

すべて管理者だけ(`main.py` の `require_user` に加えて、各ルートで `require_admin`)。
保存に成功したら `AuthRuntime` を読み直し、再起動なしで効かせる(ADR-0034 4章)。
テストログイン(`/api/auth/test-login`)と、その戻りでの本登録は `app/api/auth.py`。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.deps import require_admin
from app.auth.identity import CurrentUser
from app.auth.runtime import AuthRuntime, get_auth_runtime
from app.config import Settings
from app.deps import get_session, get_settings
from app.domain import auth_settings
from app.domain.auth_settings import EffectiveAuthConfig, OidcConnection, Valued
from app.domain.schemas import (
    AuthClientSecretStatus,
    AuthConnectionRequest,
    AuthConnectionView,
    AuthListSetting,
    AuthModeSetting,
    AuthPendingConnectionView,
    AuthSessionHoursSetting,
    AuthSettingsErrorDetail,
    AuthSettingsResponse,
    AuthSettingsUpdateRequest,
    AuthTextSetting,
    AuthVerifiedView,
)
from app.i18n import t
from app.providers.registry import _is_loopback_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings/auth", tags=["settings"])

# Discovery 文書の取得のタイムアウト(秒)。画面の操作を長く止めない。
_DISCOVERY_TIMEOUT_SECONDS = 10.0

DiscoveryFetcher = Callable[[str], Awaitable[dict]]


class DiscoveryError(Exception):
    """Discovery 文書を取得できない(`kind="failed"`)か、中身が OIDC の形でない(`"invalid"`)。"""

    def __init__(self, kind: str, reason: str) -> None:
        super().__init__(reason)
        self.kind = kind
        self.reason = reason


def discovery_url(issuer: str) -> str:
    return f"{issuer.rstrip('/')}/.well-known/openid-configuration"


async def _fetch_discovery_live(issuer: str) -> dict:
    url = discovery_url(issuer)
    try:
        async with httpx.AsyncClient(
            timeout=_DISCOVERY_TIMEOUT_SECONDS, follow_redirects=False
        ) as client:
            response = await client.get(url, headers={"Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise DiscoveryError("failed", type(exc).__name__) from exc
    if response.status_code != 200:
        raise DiscoveryError("failed", f"HTTP {response.status_code}")
    try:
        document = response.json()
    except ValueError as exc:
        raise DiscoveryError("invalid", "not JSON") from exc
    if not isinstance(document, dict):
        raise DiscoveryError("invalid", "not a JSON object")
    return document


def get_discovery_fetcher() -> DiscoveryFetcher:
    """`{issuer}/.well-known/openid-configuration` を取る関数。テストでは差し替える
    (実ネットワークに出ない)。"""
    return _fetch_discovery_live


def _error(status: int, code: str, message: str, field: str | None = None) -> HTTPException:
    return HTTPException(
        status_code=status,
        detail=AuthSettingsErrorDetail(code=code, message=message, field=field).model_dump(),
    )


# 文言のキーはリテラルで書く(`tests/test_i18n.py` がコード中のキーを洗い出すため)。
_CONFLICT_MESSAGES: dict[str, Callable[[], str]] = {
    "env_locked": lambda: t("settings.auth.conflict.env_locked"),
    "no_connection": lambda: t("settings.auth.conflict.no_connection"),
    "not_verified": lambda: t("settings.auth.conflict.not_verified"),
    "admin_emails_empty": lambda: t("settings.auth.conflict.admin_emails_empty"),
    "verified_email_not_admin": lambda: t("settings.auth.conflict.verified_email_not_admin"),
    "self_not_admin": lambda: t("settings.auth.conflict.self_not_admin"),
}

_INVALID_MESSAGES: dict[str, Callable[[], str]] = {
    "issuer": lambda: t("settings.auth.invalid.issuer"),
    "public_base_url": lambda: t("settings.auth.invalid.public_base_url"),
    "client_id": lambda: t("settings.auth.invalid.client_id"),
    "scopes": lambda: t("settings.auth.invalid.scopes"),
    "client_secret": lambda: t("settings.auth.invalid.client_secret"),
}


def _conflict(code: str) -> HTTPException:
    return _error(409, code, _CONFLICT_MESSAGES[code]())


def _invalid(field: str) -> HTTPException:
    return _error(422, "invalid_value", _INVALID_MESSAGES[field](), field)


# -- 応答 -----------------------------------------------------------------------------


def _text(valued: Valued) -> AuthTextSetting:
    return AuthTextSetting(value=valued.value, source=valued.source)


def _list(valued: Valued) -> AuthListSetting:
    return AuthListSetting(value=list(valued.value), source=valued.source)


def _response(config: EffectiveAuthConfig) -> AuthSettingsResponse:
    connection = config.connection
    public_base = config.public_base_url.value
    pending = config.pending
    verified = config.verified
    return AuthSettingsResponse(
        mode=AuthModeSetting(
            value=config.mode, source=config.mode_source, locked=config.mode_locked
        ),
        connection=AuthConnectionView(
            configured=connection is not None,
            issuer=_text(config.issuer),
            client_id=_text(config.client_id),
            scopes=_text(config.scopes),
            public_base_url=_text(config.public_base_url),
            client_secret=AuthClientSecretStatus(
                configured=bool(config.client_secret), source=config.client_secret_source
            ),
            redirect_uri=auth_settings.redirect_uri_for(public_base) if public_base else None,
        ),
        pending=(
            AuthPendingConnectionView(
                issuer=pending.issuer,
                client_id=pending.client_id,
                scopes=pending.scopes,
                public_base_url=pending.public_base_url,
                client_secret=AuthClientSecretStatus(
                    configured=pending.has_secret, source="file" if pending.has_secret else None
                ),
                redirect_uri=auth_settings.redirect_uri_for(pending.public_base_url),
                created_at=pending.created_at,
            )
            if pending is not None
            else None
        ),
        verified=(
            AuthVerifiedView(
                email=verified.email,
                verified_at=verified.verified_at,
                matches_current=config.verified_matches_current(),
            )
            if verified is not None
            else None
        ),
        admin_emails=_list(config.admin_emails),
        allowed_email_domains=_list(config.allowed_email_domains),
        session_hours=AuthSessionHoursSetting(
            value=config.session_hours.value,
            source=config.session_hours.source,
            min=auth_settings.SESSION_HOURS_MIN,
            max=auth_settings.SESSION_HOURS_MAX,
        ),
        enable_blockers=auth_settings.enable_blockers(config),
    )


@router.get("", response_model=AuthSettingsResponse, operation_id="get_auth_settings")
def get_auth_settings(
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    _user: CurrentUser = Depends(require_admin),
) -> AuthSettingsResponse:
    return _response(auth_settings.resolve_auth_config(db, settings))


# -- 接続の仮登録 -----------------------------------------------------------------------


def _required_text(value: str, field: str, max_length: int = auth_settings.MAX_TEXT_LENGTH) -> str:
    text = value.strip()
    if not text or len(text) > max_length:
        raise _invalid(field)
    return text


def _validated_url(value: str, field: str) -> str:
    text = _required_text(value, field)
    if not auth_settings.is_valid_url(text):
        raise _invalid(field)
    if text.startswith("http://") and not _is_loopback_url(text):
        logger.warning(
            "認証の %s がループバック以外への http を指しています(%s)。"
            "認証のやり取りが平文で送信されます。",
            field,
            text,
        )
    return text


@router.put("/connection", response_model=AuthSettingsResponse, operation_id="set_auth_connection")
async def set_auth_connection(
    body: AuthConnectionRequest,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    runtime: AuthRuntime = Depends(get_auth_runtime),
    fetch_discovery: DiscoveryFetcher = Depends(get_discovery_fetcher),
    _user: CurrentUser = Depends(require_admin),
) -> AuthSettingsResponse:
    """仮登録(ADR-0034 2章の1)。テストログインに成功するまで、実効の設定は変わらない。"""
    issuer = _validated_url(body.issuer, "issuer")
    public_base_url = _validated_url(body.public_base_url, "public_base_url").rstrip("/")
    client_id = _required_text(body.client_id, "client_id")
    scopes = " ".join(_required_text(body.scopes, "scopes").split())
    if "openid" not in scopes.split():
        raise _invalid("scopes")

    if "client_secret" not in body.model_fields_set or body.client_secret is None:
        # 省略: 今のもの(仮登録があればそのもの、無ければ本登録のもの)を引き継ぐ。
        pending = auth_settings.get_pending(db)
        if pending is not None:
            secret = pending.to_connection(settings.data_dir).client_secret
        else:
            secret = runtime.config.client_secret
    elif body.client_secret == "":
        secret = None
    else:
        secret = body.client_secret
        if len(secret) > auth_settings.MAX_TEXT_LENGTH:
            raise _invalid("client_secret")

    try:
        document = await fetch_discovery(issuer)
    except DiscoveryError as exc:
        if exc.kind == "invalid":
            raise _error(
                422,
                "discovery_invalid",
                t("settings.auth.discoveryInvalid", url=discovery_url(issuer), reason=exc.reason),
                "issuer",
            ) from exc
        raise _error(
            502,
            "discovery_failed",
            t("settings.auth.discoveryFailed", url=discovery_url(issuer), reason=exc.reason),
            "issuer",
        ) from exc
    missing = [
        name
        for name in ("authorization_endpoint", "token_endpoint", "jwks_uri")
        if not isinstance(document.get(name), str) or not document.get(name)
    ]
    if missing:
        raise _error(
            422,
            "discovery_invalid",
            t(
                "settings.auth.discoveryInvalid",
                url=discovery_url(issuer),
                reason=", ".join(missing),
            ),
            "issuer",
        )

    connection = OidcConnection(
        issuer=issuer,
        client_id=client_id,
        client_secret=secret,
        scopes=scopes,
        public_base_url=public_base_url,
    )
    auth_settings.save_pending(db, settings.data_dir, connection)
    db.commit()
    runtime.reload(db)
    return _response(runtime.config)


@router.delete(
    "/connection/pending",
    response_model=AuthSettingsResponse,
    operation_id="discard_auth_pending_connection",
)
def discard_auth_pending_connection(
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    runtime: AuthRuntime = Depends(get_auth_runtime),
    _user: CurrentUser = Depends(require_admin),
) -> AuthSettingsResponse:
    auth_settings.discard_pending(db, settings.data_dir)
    db.commit()
    runtime.reload(db)
    return _response(runtime.config)


# -- 保存で反映する項目 ------------------------------------------------------------------


@router.patch("", response_model=AuthSettingsResponse, operation_id="update_auth_settings")
def update_auth_settings(
    body: AuthSettingsUpdateRequest,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    runtime: AuthRuntime = Depends(get_auth_runtime),
    user: CurrentUser = Depends(require_admin),
) -> AuthSettingsResponse:
    """ADR-0031 3章の規則どおり、全項目を検査してから保存する(途中まで保存しない)。"""
    fields_set = body.model_fields_set
    current = auth_settings.resolve_auth_config(db, settings)

    # 1. 値の形を検査し、保存後の実効の設定を見積もる。
    changes: dict[str, object] = {}
    env_only = auth_settings.resolve_auth_config(None, settings)
    try:
        if "admin_emails" in fields_set:
            if body.admin_emails is None:
                changes["admin_emails"] = env_only.admin_emails
            else:
                changes["admin_emails"] = Valued(
                    auth_settings.normalize_email_list(
                        body.admin_emails, t("settings.auth.invalid.admin_emails")
                    ),
                    "setting",
                )
        if "allowed_email_domains" in fields_set:
            if body.allowed_email_domains is None:
                changes["allowed_email_domains"] = env_only.allowed_email_domains
            else:
                changes["allowed_email_domains"] = Valued(
                    auth_settings.normalize_domain_list(
                        body.allowed_email_domains,
                        t("settings.auth.invalid.allowed_email_domains"),
                    ),
                    "setting",
                )
    except auth_settings.AuthSettingsValidationError as exc:
        raise _error(422, "invalid_value", str(exc), exc.field) from exc
    if "session_hours" in fields_set:
        if body.session_hours is None:
            changes["session_hours"] = env_only.session_hours
        elif not auth_settings.is_valid_session_hours(body.session_hours):
            raise _error(
                422,
                "invalid_value",
                t(
                    "settings.auth.invalid.session_hours",
                    min=auth_settings.SESSION_HOURS_MIN,
                    max=auth_settings.SESSION_HOURS_MAX,
                ),
                "session_hours",
            )
        else:
            changes["session_hours"] = Valued(body.session_hours, "setting")

    save_mode = False
    if "mode" in fields_set:
        if current.mode_locked:
            # `.env` の AUTH_MODE が優先する間は変えられない(同じ値なら何もしない)。
            if body.mode is not None and body.mode != current.mode:
                raise _conflict("env_locked")
        else:
            save_mode = True
            if body.mode is None:
                changes["mode"] = "none"
                changes["mode_source"] = "default"
            else:
                changes["mode"] = body.mode
                changes["mode_source"] = "setting"
    after = auth_settings.with_overrides(current, **changes)

    # 2. 締め出しを防ぐ規則(ADR-0034 2章の4・3章)。
    enabling = after.mode == "oidc" and current.mode != "oidc"
    if enabling:
        if after.connection is None:
            raise _conflict("no_connection")
        if not after.verified_matches_current():
            raise _conflict("not_verified")
    if after.mode == "oidc":
        if not after.admin_emails.value:
            raise _conflict("admin_emails_empty")
        if enabling:
            assert after.verified is not None
            if after.verified.email not in after.admin_email_set():
                raise _conflict("verified_email_not_admin")
        elif (user.email or "").strip().lower() not in after.admin_email_set():
            # oidc の間は、保存後の管理者のメールに操作している本人が含まれていなければならない。
            raise _conflict("self_not_admin")

    # 3. 保存する(DB は1回のコミット)。
    if enabling:
        # `.env` から来ていた値を DB / secrets.json に書き写す(以後は `.env` から消しても動く)。
        auth_settings.copy_env_values(db, settings.data_dir, current)
    for name, key in (
        ("admin_emails", auth_settings.ADMIN_EMAILS_KEY),
        ("allowed_email_domains", auth_settings.ALLOWED_DOMAINS_KEY),
        ("session_hours", auth_settings.SESSION_HOURS_KEY),
    ):
        if name not in fields_set:
            continue
        if getattr(body, name) is None:
            # 有効にするときは書き写した値を残す(`.env` の値に戻すのと同じ値になる)。
            if not (enabling and getattr(current, name).source == "env"):
                auth_settings.unset_value(db, key)
        else:
            auth_settings.set_value(db, key, changes[name].value)  # type: ignore[union-attr]
    if save_mode:
        if body.mode is None:
            auth_settings.unset_value(db, auth_settings.MODE_KEY)
        else:
            auth_settings.set_value(db, auth_settings.MODE_KEY, body.mode)
    db.commit()
    runtime.reload(db)
    return _response(runtime.config)
