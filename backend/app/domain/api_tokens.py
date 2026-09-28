"""MCP 用のアクセストークン(ADR-0023 2章。認証モードのみ)。

値は `gakei_` + ランダム文字列で、発行時に1回だけ返す。DB には SHA-256 のハッシュだけを
保存する(`auth/sessions.py` のセッショントークンと同じ扱い)。トークンはそれを発行した
利用者として動く。失効したトークンの行は消さない(Run の `api_token_id` から参照されるため)。
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.identity import CurrentUser, Role
from app.auth.sessions import hash_token, is_email_allowed
from app.domain.models import ApiToken, AppUser
from app.i18n import t

TOKEN_PREFIX = "gakei_"
NAME_MAX_LENGTH = 100


class ApiTokenNameError(ValueError):
    """名前が空・長すぎる(API 層で 422 にする)。"""


@dataclass(frozen=True)
class TokenPrincipal:
    user: CurrentUser
    token_id: uuid.UUID


def _utcnow() -> datetime:
    return datetime.now(UTC)


def normalize_name(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise ApiTokenNameError(t("apiTokens.nameEmpty"))
    if len(normalized) > NAME_MAX_LENGTH:
        raise ApiTokenNameError(t("apiTokens.nameTooLong", max=NAME_MAX_LENGTH))
    return normalized


def issue_token(db: Session, user_id: uuid.UUID, name: str) -> tuple[ApiToken, str]:
    """新しいトークンを作り、(行, 生の値) を返す。生の値はどこにも保存しない。"""
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    row = ApiToken(
        user_id=user_id,
        name=normalize_name(name),
        token_hash=hash_token(raw),
        created_at=_utcnow(),
    )
    db.add(row)
    db.flush()
    return row, raw


def list_active_tokens(db: Session, user_id: uuid.UUID) -> list[ApiToken]:
    """失効していない自分のトークン(新しい順)。"""
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

    不明・失効済み、または許可ドメインから外れた利用者なら None。ロールはセッションと同じく
    毎回 `admin_emails` から評価し直す(`auth/sessions.py::find_user_for_token` と同じ)。
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
    email = user.email or ""
    if not is_email_allowed(email, allowed_domains, admin_emails):
        return None
    role: Role = "admin" if email.strip().lower() in admin_emails else "user"
    token.last_used_at = _utcnow()
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
    )
