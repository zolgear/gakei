"""認証の設定(ADR-0034)。値の出どころと優先順位を決めるのはここ1か所。

- 認証モード: `.env` に `AUTH_MODE` が明示されていれば `.env`(ロック)→ DB → 既定 none
- 発行者・クライアント ID・スコープ・`PUBLIC_BASE_URL`・管理者のメール・許可ドメイン・
  セッションの長さ: DB → `.env` → 既定
- クライアントシークレット: `secrets.json` の `oidc_client_secret` → `.env` の
  `OIDC_CLIENT_SECRET`

DB は `app_setting` の `auth.*` キー(値の形は `general_settings` と同じ `{"value": ...}`)。
シークレットは DB に置かない(`DATA_DIR/secrets.json`。ADR-0012)。

- 発行者が DB にある(画面で登録した接続を本登録した、または有効にしたときに `.env` から
  書き写した)ときは、シークレットも `secrets.json` だけから読む。本登録のときに必ず
  `secrets.json` に書く(public client なら消す)ので、`.env` の `OIDC_CLIENT_SECRET` が
  画面で選んだ public client を上書きしないようにするため。
- 仮登録(`auth.oidc.pending`)は、テストログインに成功するまで実効の設定に影響しない。
  シークレットは `secrets.json` の `oidc_client_secret_pending`。
- テストに成功した接続の指紋とメールを `auth.oidc.verified` に残す。oidc にするときは、
  これが今の接続と一致していることを確かめる(締め出しの防止。ADR-0034 2章)。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.config import Settings
from app.domain.api_key import delete_secret_field, read_secret_field, write_secret_field
from app.domain.general_settings import _get_raw_value
from app.domain.models import AppSetting

AuthMode = Literal["none", "oidc"]
Source = Literal["setting", "env", "default"]
SecretSource = Literal["file", "env"]

MODE_KEY = "auth.mode"
ISSUER_KEY = "auth.oidc.issuer"
CLIENT_ID_KEY = "auth.oidc.client_id"
SCOPES_KEY = "auth.oidc.scopes"
PUBLIC_BASE_URL_KEY = "auth.oidc.public_base_url"
ADMIN_EMAILS_KEY = "auth.oidc.admin_emails"
ALLOWED_DOMAINS_KEY = "auth.oidc.allowed_email_domains"
SESSION_HOURS_KEY = "auth.oidc.session_hours"
PENDING_KEY = "auth.oidc.pending"
VERIFIED_KEY = "auth.oidc.verified"

SECRET_FIELD = "oidc_client_secret"
PENDING_SECRET_FIELD = "oidc_client_secret_pending"

DEFAULT_SCOPES = "openid profile email"
DEFAULT_SESSION_HOURS = 24 * 30
SESSION_HOURS_MIN = 1
SESSION_HOURS_MAX = 24 * 30

CALLBACK_PATH = "/api/auth/callback"

# 管理者のメール・許可ドメインの件数の上限(画面の入力の誤りで巨大な値を保存しないため)。
MAX_LIST_ITEMS = 200
MAX_TEXT_LENGTH = 2048


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class OidcConnection:
    """IdP への接続1組(本登録か仮登録)。シークレットを含むので、画面や API にそのまま出さない。"""

    issuer: str
    client_id: str
    client_secret: str | None
    scopes: str
    public_base_url: str

    @property
    def redirect_uri(self) -> str:
        return redirect_uri_for(self.public_base_url)

    def fingerprint(self) -> str:
        """接続設定の指紋(ADR-0034 2章)。シークレットはハッシュにしてから混ぜる。"""
        secret_hash = _sha256(self.client_secret) if self.client_secret else ""
        payload = json.dumps(
            [self.issuer, self.client_id, secret_hash, self.scopes, self.public_base_url],
            ensure_ascii=False,
        )
        return _sha256(payload)


def redirect_uri_for(public_base_url: str) -> str:
    return f"{public_base_url.rstrip('/')}{CALLBACK_PATH}"


@dataclass(frozen=True)
class PendingConnection:
    """仮登録の接続(テストログインに成功するまで実効の設定に影響しない)。"""

    issuer: str
    client_id: str
    scopes: str
    public_base_url: str
    has_secret: bool
    created_at: datetime | None

    def to_connection(self, data_dir: Path) -> OidcConnection:
        secret = read_secret_field(data_dir, PENDING_SECRET_FIELD) if self.has_secret else None
        return OidcConnection(
            issuer=self.issuer,
            client_id=self.client_id,
            client_secret=secret,
            scopes=self.scopes,
            public_base_url=self.public_base_url,
        )


@dataclass(frozen=True)
class VerifiedLogin:
    """テストログインに成功した記録。"""

    fingerprint: str
    email: str
    verified_at: datetime


@dataclass(frozen=True)
class Valued[T]:
    value: T
    source: Source


@dataclass(frozen=True)
class EffectiveAuthConfig:
    """実効の認証の設定。各値の出どころも持つ。"""

    mode: AuthMode
    mode_source: Source
    mode_locked: bool
    issuer: Valued[str | None]
    client_id: Valued[str | None]
    scopes: Valued[str]
    public_base_url: Valued[str | None]
    client_secret: str | None = field(repr=False, default=None)
    client_secret_source: SecretSource | None = None
    admin_emails: Valued[list[str]] = field(default_factory=lambda: Valued([], "default"))
    allowed_email_domains: Valued[list[str]] = field(default_factory=lambda: Valued([], "default"))
    session_hours: Valued[int] = field(
        default_factory=lambda: Valued(DEFAULT_SESSION_HOURS, "default")
    )
    pending: PendingConnection | None = None
    verified: VerifiedLogin | None = None

    @property
    def connection(self) -> OidcConnection | None:
        """本登録の接続。発行者・クライアント ID・`PUBLIC_BASE_URL` のどれかが欠けていれば None。"""
        if not (self.issuer.value and self.client_id.value and self.public_base_url.value):
            return None
        return OidcConnection(
            issuer=self.issuer.value,
            client_id=self.client_id.value,
            client_secret=self.client_secret,
            scopes=self.scopes.value,
            public_base_url=self.public_base_url.value,
        )

    def admin_email_set(self) -> set[str]:
        return set(self.admin_emails.value)

    def allowed_email_domain_set(self) -> set[str]:
        return set(self.allowed_email_domains.value)

    @property
    def public_base_is_https(self) -> bool:
        value = self.public_base_url.value
        return bool(value) and urlparse(value).scheme == "https"

    def verified_matches_current(self) -> bool:
        connection = self.connection
        return (
            self.verified is not None
            and connection is not None
            and self.verified.fingerprint == connection.fingerprint()
        )


# -- 読み取り ---------------------------------------------------------------------


def _saved_text(db: Session | None, key: str) -> str | None:
    if db is None:
        return None
    value = _get_raw_value(db, key)
    return value if isinstance(value, str) and value else None


def _saved_list(db: Session | None, key: str) -> list[str] | None:
    if db is None:
        return None
    value = _get_raw_value(db, key)
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        return None
    return list(value)


def _saved_session_hours(db: Session | None) -> int | None:
    if db is None:
        return None
    value = _get_raw_value(db, SESSION_HOURS_KEY)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if not (SESSION_HOURS_MIN <= value <= SESSION_HOURS_MAX):
        return None
    return value


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def get_pending(db: Session) -> PendingConnection | None:
    value = _get_raw_value(db, PENDING_KEY)
    if not isinstance(value, dict):
        return None
    texts = [value.get(k) for k in ("issuer", "client_id", "scopes", "public_base_url")]
    if not all(isinstance(v, str) and v for v in texts):
        return None
    issuer, client_id, scopes, public_base_url = texts
    return PendingConnection(
        issuer=issuer,  # type: ignore[arg-type]
        client_id=client_id,  # type: ignore[arg-type]
        scopes=scopes,  # type: ignore[arg-type]
        public_base_url=public_base_url,  # type: ignore[arg-type]
        has_secret=value.get("has_secret") is True,
        created_at=_parse_datetime(value.get("created_at")),
    )


def get_verified(db: Session) -> VerifiedLogin | None:
    value = _get_raw_value(db, VERIFIED_KEY)
    if not isinstance(value, dict):
        return None
    fingerprint = value.get("fingerprint")
    email = value.get("email")
    verified_at = _parse_datetime(value.get("verified_at"))
    if not isinstance(fingerprint, str) or not isinstance(email, str) or verified_at is None:
        return None
    return VerifiedLogin(fingerprint=fingerprint, email=email, verified_at=verified_at)


def _text(saved: str | None, env_value: str | None, default: str | None = None) -> Valued:
    if saved:
        return Valued(saved, "setting")
    if env_value:
        return Valued(env_value, "env")
    return Valued(default, "default")


def resolve_auth_config(db: Session | None, settings: Settings) -> EffectiveAuthConfig:
    """実効の認証の設定。`db=None` なら `.env` と既定だけから決める(起動時の検査のテスト用)。"""
    if settings.auth_mode is not None:
        mode: AuthMode = settings.auth_mode
        mode_source: Source = "env"
    else:
        saved_mode = _saved_text(db, MODE_KEY)
        if saved_mode in ("none", "oidc"):
            mode, mode_source = saved_mode, "setting"  # type: ignore[assignment]
        else:
            mode, mode_source = "none", "default"

    saved_issuer = _saved_text(db, ISSUER_KEY)
    issuer = _text(saved_issuer, settings.oidc_issuer)
    client_id = _text(_saved_text(db, CLIENT_ID_KEY), settings.oidc_client_id)
    env_scopes = settings.oidc_scopes if "oidc_scopes" in settings.model_fields_set else None
    scopes = _text(_saved_text(db, SCOPES_KEY), env_scopes, DEFAULT_SCOPES)
    public_base_url = _text(_saved_text(db, PUBLIC_BASE_URL_KEY), settings.public_base_url)

    client_secret: str | None = None
    client_secret_source: SecretSource | None = None
    file_secret = read_secret_field(settings.data_dir, SECRET_FIELD)
    if file_secret:
        client_secret, client_secret_source = file_secret, "file"
    elif saved_issuer is None and settings.oidc_client_secret:
        client_secret, client_secret_source = settings.oidc_client_secret, "env"

    saved_admins = _saved_list(db, ADMIN_EMAILS_KEY)
    if saved_admins is not None:
        admin_emails: Valued[list[str]] = Valued(saved_admins, "setting")
    elif settings.admin_email_list():
        admin_emails = Valued(settings.admin_email_list(), "env")
    else:
        admin_emails = Valued([], "default")

    saved_domains = _saved_list(db, ALLOWED_DOMAINS_KEY)
    if saved_domains is not None:
        domains: Valued[list[str]] = Valued(saved_domains, "setting")
    elif settings.allowed_email_domain_list():
        domains = Valued(settings.allowed_email_domain_list(), "env")
    else:
        domains = Valued([], "default")

    saved_hours = _saved_session_hours(db)
    if saved_hours is not None:
        session_hours = Valued(saved_hours, "setting")
    elif "auth_session_hours" in settings.model_fields_set:
        session_hours = Valued(settings.auth_session_hours, "env")
    else:
        session_hours = Valued(DEFAULT_SESSION_HOURS, "default")

    return EffectiveAuthConfig(
        mode=mode,
        mode_source=mode_source,
        mode_locked=settings.auth_mode is not None,
        issuer=issuer,
        client_id=client_id,
        scopes=scopes,
        public_base_url=public_base_url,
        client_secret=client_secret,
        client_secret_source=client_secret_source,
        admin_emails=admin_emails,
        allowed_email_domains=domains,
        session_hours=session_hours,
        pending=get_pending(db) if db is not None else None,
        verified=get_verified(db) if db is not None else None,
    )


# -- 有効にできない理由 -------------------------------------------------------------

EnableBlocker = Literal[
    "env_locked",
    "no_connection",
    "not_verified",
    "admin_emails_empty",
    "verified_email_not_admin",
]


def enable_blockers(config: EffectiveAuthConfig) -> list[EnableBlocker]:
    """今の設定のまま oidc にできない理由(無ければ空)。並びは画面で案内する順。"""
    blockers: list[EnableBlocker] = []
    if config.mode_locked:
        blockers.append("env_locked")
    if config.connection is None:
        blockers.append("no_connection")
    elif not config.verified_matches_current():
        blockers.append("not_verified")
    if not config.admin_emails.value:
        blockers.append("admin_emails_empty")
    elif config.verified is not None and config.verified.email not in config.admin_email_set():
        blockers.append("verified_email_not_admin")
    return blockers


# -- 値の検査 ---------------------------------------------------------------------


class AuthSettingsValidationError(Exception):
    """値の形が不正(API 層で 422 にする)。`field` は入力欄の識別に使う。"""

    def __init__(self, field_name: str, message: str) -> None:
        super().__init__(message)
        self.field = field_name


def is_valid_url(value: str) -> bool:
    """`check_auth_env` と同じ規則: http/https で、ホストを含む。"""
    parsed = urlparse(value)
    return parsed.scheme in ("http", "https") and bool(parsed.hostname)


def normalize_email(value: str) -> str | None:
    """小文字化・trim。形が明らかに違う(`@` の前後が空、空白を含む)なら None。"""
    email = value.strip().lower()
    local, at, domain = email.rpartition("@")
    if not at or not local or not domain or any(c.isspace() for c in email):
        return None
    if len(email) > 320:
        return None
    return email


def normalize_domain(value: str) -> str | None:
    domain = value.strip().lower().lstrip("@")
    if not domain or "@" in domain or "/" in domain or any(c.isspace() for c in domain):
        return None
    if len(domain) > 253:
        return None
    return domain


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def normalize_email_list(values: list[str], invalid: str) -> list[str]:
    """不正な値があれば `AuthSettingsValidationError`(`invalid` はメッセージ)。"""
    if len(values) > MAX_LIST_ITEMS:
        raise AuthSettingsValidationError("admin_emails", invalid)
    result: list[str] = []
    for value in values:
        normalized = normalize_email(value) if isinstance(value, str) else None
        if normalized is None:
            raise AuthSettingsValidationError("admin_emails", invalid)
        result.append(normalized)
    return _dedupe(result)


def normalize_domain_list(values: list[str], invalid: str) -> list[str]:
    if len(values) > MAX_LIST_ITEMS:
        raise AuthSettingsValidationError("allowed_email_domains", invalid)
    result: list[str] = []
    for value in values:
        normalized = normalize_domain(value) if isinstance(value, str) else None
        if normalized is None:
            raise AuthSettingsValidationError("allowed_email_domains", invalid)
        result.append(normalized)
    return _dedupe(result)


def is_valid_session_hours(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, int)
        and SESSION_HOURS_MIN <= value <= SESSION_HOURS_MAX
    )


# -- 書き込み(コミットは呼び出し側) -------------------------------------------------


def set_value(db: Session, key: str, value: object) -> None:
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value={"value": value}, updated_at=_utcnow()))
        # 同じトランザクションで同じキーをもう一度書いても `db.get` で見つかるように。
        db.flush()
    else:
        row.value = {"value": value}
        row.updated_at = _utcnow()


def unset_value(db: Session, key: str) -> None:
    row = db.get(AppSetting, key)
    if row is not None:
        db.delete(row)
        db.flush()


def save_pending(db: Session, data_dir: Path, connection: OidcConnection) -> None:
    """仮登録を保存する(コミットは呼び出し側。シークレットは先に `secrets.json` に書く)。"""
    if connection.client_secret:
        write_secret_field(data_dir, PENDING_SECRET_FIELD, connection.client_secret)
    else:
        delete_secret_field(data_dir, PENDING_SECRET_FIELD)
    set_value(
        db,
        PENDING_KEY,
        {
            "issuer": connection.issuer,
            "client_id": connection.client_id,
            "scopes": connection.scopes,
            "public_base_url": connection.public_base_url,
            "has_secret": bool(connection.client_secret),
            "created_at": _utcnow().isoformat(),
        },
    )


def discard_pending(db: Session, data_dir: Path) -> None:
    unset_value(db, PENDING_KEY)
    delete_secret_field(data_dir, PENDING_SECRET_FIELD)


def save_connection(db: Session, data_dir: Path, connection: OidcConnection) -> None:
    """本登録にする(DB の4項目とシークレット)。public client ならシークレットを消す。"""
    set_value(db, ISSUER_KEY, connection.issuer)
    set_value(db, CLIENT_ID_KEY, connection.client_id)
    set_value(db, SCOPES_KEY, connection.scopes)
    set_value(db, PUBLIC_BASE_URL_KEY, connection.public_base_url)
    if connection.client_secret:
        write_secret_field(data_dir, SECRET_FIELD, connection.client_secret)
    else:
        delete_secret_field(data_dir, SECRET_FIELD)


def record_verified(db: Session, fingerprint: str, email: str) -> None:
    set_value(
        db,
        VERIFIED_KEY,
        {"fingerprint": fingerprint, "email": email, "verified_at": _utcnow().isoformat()},
    )


def copy_env_values(db: Session, data_dir: Path, config: EffectiveAuthConfig) -> None:
    """none → oidc のとき、`.env` から来ている値を DB / `secrets.json` に書き写す
    (ADR-0034 2章。以後は `.env` から消しても動く)。"""
    for key, valued in (
        (ISSUER_KEY, config.issuer),
        (CLIENT_ID_KEY, config.client_id),
        (SCOPES_KEY, config.scopes),
        (PUBLIC_BASE_URL_KEY, config.public_base_url),
        (ADMIN_EMAILS_KEY, config.admin_emails),
        (ALLOWED_DOMAINS_KEY, config.allowed_email_domains),
        (SESSION_HOURS_KEY, config.session_hours),
    ):
        if valued.source == "env":
            set_value(db, key, valued.value)
    if config.client_secret_source == "env" and config.client_secret:
        write_secret_field(data_dir, SECRET_FIELD, config.client_secret)


def with_overrides(config: EffectiveAuthConfig, **changes: object) -> EffectiveAuthConfig:
    """保存後の実効の設定を見積もる(保存前の検査に使う)。"""
    return replace(config, **changes)  # type: ignore[arg-type]
