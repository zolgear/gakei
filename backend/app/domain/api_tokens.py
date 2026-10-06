"""MCP 用のアクセストークン(ADR-0023 2章。認証モードのみ)。

値は `gakei_` + ランダム文字列で、発行時に1回だけ返す。DB には SHA-256 のハッシュだけを
保存する(`auth/sessions.py` のセッショントークンと同じ扱い)。トークンはそれを発行した
利用者として動く。失効したトークンの行は消さない(Run の `api_token_id` から参照されるため)。

有効期限と権限(ADR-0023 11章): 発行のときに期限(30日 / 90日 / 1年 / 無期限)と権限
(`full` = すべて / `read` = 読み取りのみ)を選び、後から変えない。期限を過ぎたトークンは
失効と同じく受け付けないが、応答の文言を分けるため `authenticate` は `ApiTokenExpiredError`
を投げる。読み取りのみのトークンで使えるツールは `app/mcp/server.py` の `READ_SCOPE_TOOLS`。
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.identity import CurrentUser, Role
from app.auth.sessions import hash_token, is_email_allowed
from app.domain.models import ApiToken, AppUser
from app.i18n import t

TOKEN_PREFIX = "gakei_"
NAME_MAX_LENGTH = 100

TokenScope = Literal["full", "read"]
SCOPE_FULL: TokenScope = "full"
SCOPE_READ: TokenScope = "read"
# 発行のときに選べる期限(日数)。None は無期限。既定は 90 日(ADR-0023 11章 1)。
EXPIRY_DAYS_CHOICES: tuple[int, ...] = (30, 90, 365)
DEFAULT_EXPIRY_DAYS = 90


class ApiTokenNameError(ValueError):
    """名前が空・長すぎる(API 層で 422 にする)。"""


class ApiTokenExpiredError(Exception):
    """トークン自体は正しいが、有効期限を過ぎている(401。文言を「無効」と分ける)。"""


@dataclass(frozen=True)
class TokenPrincipal:
    user: CurrentUser
    token_id: uuid.UUID
    scope: TokenScope = SCOPE_FULL


def _utcnow() -> datetime:
    return datetime.now(UTC)


def normalize_name(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise ApiTokenNameError(t("apiTokens.nameEmpty"))
    if len(normalized) > NAME_MAX_LENGTH:
        raise ApiTokenNameError(t("apiTokens.nameTooLong", max=NAME_MAX_LENGTH))
    return normalized


def is_expired(token: ApiToken, now: datetime | None = None) -> bool:
    """期限を過ぎているか(無期限なら False)。"""
    if token.expires_at is None:
        return False
    return token.expires_at <= (now or _utcnow())


def is_usable(token: ApiToken | None, now: datetime | None = None) -> bool:
    """失効も期限切れもしていないか。トークンで発行したアップロード・ダウンロード URL を
    使う時点の確認に使う(ADR-0023 11章 3)。"""
    return token is not None and token.revoked_at is None and not is_expired(token, now)


def issue_token(
    db: Session,
    user_id: uuid.UUID,
    name: str,
    *,
    expires_in_days: int | None = DEFAULT_EXPIRY_DAYS,
    scope: TokenScope = SCOPE_FULL,
) -> tuple[ApiToken, str]:
    """新しいトークンを作り、(行, 生の値) を返す。生の値はどこにも保存しない。

    `expires_in_days` は `EXPIRY_DAYS_CHOICES` のどれかか None(無期限)。期限と権限は
    ここで1回だけ書く(API 層のスキーマでも選択肢に絞っている)。
    """
    if expires_in_days is not None and expires_in_days not in EXPIRY_DAYS_CHOICES:
        raise ValueError(f"unsupported expires_in_days: {expires_in_days}")
    if scope not in (SCOPE_FULL, SCOPE_READ):
        raise ValueError(f"unsupported scope: {scope}")
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    now = _utcnow()
    row = ApiToken(
        user_id=user_id,
        name=normalize_name(name),
        token_hash=hash_token(raw),
        created_at=now,
        expires_at=None if expires_in_days is None else now + timedelta(days=expires_in_days),
        scope=scope,
    )
    db.add(row)
    db.flush()
    return row, raw


def list_active_tokens(db: Session, user_id: uuid.UUID) -> list[ApiToken]:
    """失効していない自分のトークン(新しい順)。期限切れのものも含む(画面に「期限切れ」と
    出し、利用者が失効させるまで残す。ADR-0023 11章 1)。"""
    return list(
        db.execute(
            select(ApiToken)
            .where(ApiToken.user_id == user_id, ApiToken.revoked_at.is_(None))
            .order_by(ApiToken.created_at.desc(), ApiToken.id.desc())
        )
        .scalars()
        .all()
    )


def revoke_token(db: Session, user_id: uuid.UUID, token_id: uuid.UUID) -> bool:
    """自分のトークンを失効させる。見つからない・他人のもの・失効済みなら False。"""
    row = db.get(ApiToken, token_id)
    if row is None or row.user_id != user_id or row.revoked_at is not None:
        return False
    row.revoked_at = _utcnow()
    db.flush()
    return True


def authenticate(
    db: Session,
    raw: str,
    admin_emails: set[str],
    allowed_domains: set[str],
) -> TokenPrincipal | None:
    """生のトークンから利用者を引き、`last_used_at` を更新する(コミットは呼び出し側)。

    不明・失効済み、または許可ドメインから外れた利用者なら None。期限切れなら
    `ApiTokenExpiredError`(応答の文言を分けるため)。ロールはセッションと同じく毎回
    `admin_emails` から評価し直す(`auth/sessions.py::find_user_for_token` と同じ)。
    """
    if not raw.startswith(TOKEN_PREFIX):
        return None
    row = db.execute(
        select(ApiToken, AppUser)
        .join(AppUser, AppUser.id == ApiToken.user_id)
        .where(ApiToken.token_hash == hash_token(raw))
    ).first()
    if row is None:
        return None
    token, user = row
    if token.revoked_at is not None:
        return None
    now = _utcnow()
    if is_expired(token, now):
        raise ApiTokenExpiredError
    email = user.email or ""
    if not is_email_allowed(email, allowed_domains, admin_emails):
        return None
    role: Role = "admin" if email.strip().lower() in admin_emails else "user"
    token.last_used_at = now
    db.flush()
    return TokenPrincipal(
        user=CurrentUser(
            id=user.id,
            name=user.name,
            email=user.email,
            role=role,
            avatar_sha256=user.avatar_sha256,
        ),
        token_id=token.id,
        # 想定外の値は、権限の狭いほう(読み取りのみ)に倒す。
        scope=SCOPE_FULL if token.scope == SCOPE_FULL else SCOPE_READ,
    )
