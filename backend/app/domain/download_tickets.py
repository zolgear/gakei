"""原本の1回限りのダウンロード URL(ADR-0023 8章 3)。

MCP の `create_download_url` が発行し、REST の `GET /api/downloads/{token}` が受け取る。作りは
アップロード URL(`upload_tickets`)と同じで、URL に含むトークンは推測できない乱数、DB には
SHA-256 のハッシュだけを保存する。有効期限は発行から10分、1回限り。

アップロード URL と違い、失敗の理由(不明・使用済み・期限切れ)を区別せず、どれも 404 にする
(ADR-0023 8章 3)。取得の時点でも、発行した利用者にその Asset が見えるかを確かめる
(ADR-0025。発行後に見えなくなった場合に備える)。同時に2回取得された場合は、条件付き
UPDATE で片方だけが通る。
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.auth.sessions import hash_token
from app.domain.models import ApiToken, DownloadTicket

TICKET_TTL = timedelta(minutes=10)
# 期限切れの行は、発行のついでに消す(証跡ではない。原本の取得の記録は残さない)。
_PURGE_AFTER = timedelta(days=1)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def issue_ticket(
    db: Session,
    *,
    asset_id: uuid.UUID,
    user_id: uuid.UUID | None,
    api_token_id: uuid.UUID | None,
    now: datetime | None = None,
) -> tuple[DownloadTicket, str]:
    """新しいチケットを作り、(行, 生のトークン) を返す(コミットは呼び出し側)。

    Asset が発行者に見えるかは、呼び出し側が先に確かめる。
    """
    now = now or _utcnow()
    db.execute(delete(DownloadTicket).where(DownloadTicket.expires_at < now - _PURGE_AFTER))
    raw = secrets.token_urlsafe(32)
    row = DownloadTicket(
        token_hash=hash_token(raw),
        asset_id=asset_id,
        user_id=user_id,
        api_token_id=api_token_id,
        created_at=now,
        expires_at=now + TICKET_TTL,
    )
    db.add(row)
    db.flush()
    return row, raw


def find_usable_ticket(db: Session, raw: str, now: datetime | None = None) -> DownloadTicket | None:
    """期限内・未使用で、発行に使ったアクセストークンが失効していないチケット。無ければ None。"""
    now = now or _utcnow()
    ticket = db.execute(
        select(DownloadTicket).where(DownloadTicket.token_hash == hash_token(raw))
    ).scalar_one_or_none()
    if ticket is None or ticket.used_at is not None or ticket.expires_at <= now:
        return None
    if ticket.api_token_id is not None:
        token = db.get(ApiToken, ticket.api_token_id)
        if token is None or token.revoked_at is not None:
            return None
    return ticket


def mark_used(db: Session, ticket: DownloadTicket, now: datetime | None = None) -> bool:
    """チケットを使用済みにする(コミットは呼び出し側)。他の取得が先に使っていたら False。"""
    now = now or _utcnow()
    result = db.execute(
        update(DownloadTicket)
        .where(DownloadTicket.id == ticket.id, DownloadTicket.used_at.is_(None))
        .values(used_at=now)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1
