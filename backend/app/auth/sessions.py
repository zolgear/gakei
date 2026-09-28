"""サーバー側セッション(ADR-0019)。Cookie(`gakei_session`)にはランダムトークンのみを置き、
DB(`auth_session`)にはそのハッシュ(sha256)だけを保存する(トークン自体は保存しない)。
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.auth.identity import CurrentUser, Role
from app.auth.oidc import OidcIdentity
from app.domain.models import AppUser, AuthSession


def _utcnow() -> datetime:
    return datetime.now(UTC)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def is_email_allowed(email: str, allowed_domains: set[str], admin_emails: set[str]) -> bool:
    """ログインを許すか。`allowed_domains` が空なら誰でも可。管理者のメールは常に可。

    Google のように誰でもアカウントを持てる IdP を使うとき、ドメインで絞らないと組織外の人も
    ログインして(user として)画像を生成できてしまうため(ADR-0019 3章)。
    """
    normalized = email.strip().lower()
    if normalized in admin_emails:
        return True
    if not allowed_domains:
        return True
    domain = normalized.rsplit("@", 1)[-1] if "@" in normalized else ""
    return domain in allowed_domains


def upsert_user(db: Session, identity: OidcIdentity, admin_emails: set[str]) -> AppUser:
    """`(issuer, subject)` で既存ユーザーを探し、無ければ作る。ロールはログインのたびに
    `admin_emails`(`Settings.admin_email_set()`)から再計算する(IdP のクレームは使わない。
    ADR-0019 の決定)。
    """
    user = db.execute(
        select(AppUser).where(
            AppUser.issuer == identity.issuer, AppUser.subject == identity.subject
        )
    ).scalar_one_or_none()

    role = "admin" if (identity.email or "").strip().lower() in admin_emails else "user"
    now = _utcnow()

    if user is None:
        user = AppUser(
            issuer=identity.issuer,
            subject=identity.subject,
            email=identity.email,
            name=identity.name,
            role=role,
            created_at=now,
            last_login_at=now,
        )
        db.add(user)
    else:
        user.email = identity.email
        user.name = identity.name
        user.role = role
        user.last_login_at = now

    db.flush()
    return user


def create_session(db: Session, user: AppUser, hours: int) -> str:
    """新しいセッションを作り、Cookie に入れる生トークンを返す(DB に保存するのはハッシュのみ)。"""
    raw = new_token()
    now = _utcnow()
    db.add(
        AuthSession(
            user_id=user.id,
            token_hash=hash_token(raw),
            created_at=now,
            expires_at=now + timedelta(hours=hours),
        )
    )
    db.flush()
    return raw


def find_user_for_token(
    db: Session,
    raw: str,
    admin_emails: set[str],
    allowed_domains: set[str],
) -> CurrentUser | None:
    """トークンから有効なセッションを引き、`CurrentUser` を返す。存在しない・期限切れは None。

    L-3(2026-09-27 追記): ロールと許可ドメインは、DB に保存済みの `app_user.role` を
    そのまま返すのではなく、毎リクエスト `admin_emails` / `allowed_domains`(現在の
    `.env` の値)から評価し直す。`AUTH_ADMIN_EMAILS` / `AUTH_ALLOWED_EMAIL_DOMAINS` を
    変えたとき、既存のセッションを消さなくても次のリクエストから効くようにするため。
    許可ドメインから外れた場合は None(呼び出し側は 401)。`app_user.role` の更新は
    ログイン時(`upsert_user`)のままでよい(表示用に残すだけ)。
    """
    token_hash = hash_token(raw)
    row = db.execute(
        select(AuthSession, AppUser)
        .join(AppUser, AppUser.id == AuthSession.user_id)
        .where(AuthSession.token_hash == token_hash)
    ).first()
    if row is None:
        return None
    session, user = row
    if session.expires_at <= _utcnow():
        return None

    email = user.email or ""
    if not is_email_allowed(email, allowed_domains, admin_emails):
        return None
    role: Role = "admin" if email.strip().lower() in admin_emails else "user"
    return CurrentUser(
        id=user.id,
        name=user.name,
        email=user.email,
        role=role,
        avatar_sha256=user.avatar_sha256,
    )


def revoke(db: Session, raw: str) -> None:
    """トークンに対応するセッションを消す(存在しなくても何もしない)。"""
    db.execute(delete(AuthSession).where(AuthSession.token_hash == hash_token(raw)))
    db.flush()


MAX_SESSIONS_PER_USER = 10


def prune_oldest_sessions(db: Session, user_id: uuid.UUID, keep: int) -> None:
    """指定ユーザーのセッションを新しい順に `keep` 件だけ残し、それより古いものを消す。

    同じ利用者が複数の端末で同時にログインできるようにしつつ(2026-09-28 改訂。以前は
    1ユーザー1セッションだった。ADR-0019 2章)、使われなくなった端末のセッションが
    際限なく増えないよう、`create_session` の直前に `keep=MAX_SESSIONS_PER_USER - 1` で呼ぶ。
    """
    stale_ids = (
        db.execute(
            select(AuthSession.id)
            .where(AuthSession.user_id == user_id)
            .order_by(AuthSession.created_at.desc(), AuthSession.id.desc())
            .offset(keep)
        )
        .scalars()
        .all()
    )
    if stale_ids:
        db.execute(delete(AuthSession).where(AuthSession.id.in_(stale_ids)))
        db.flush()


def purge_expired(db: Session) -> None:
    """期限切れセッションを消す。定期ジョブは持たず、ログインのたびに呼ぶ。"""
    db.execute(delete(AuthSession).where(AuthSession.expires_at <= _utcnow()))
    db.flush()
