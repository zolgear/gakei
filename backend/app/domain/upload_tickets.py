"""1回限りのアップロード URL(ADR-0023 7章 2)。

MCP の `create_upload_url` が発行し、REST の `PUT /api/uploads/{token}` が受け取る。URL に含む
トークンは推測できない乱数で、DB には SHA-256 のハッシュだけを保存する(アクセストークンや
ログインセッションと同じ扱い)。有効期限は発行から10分。

取り込み(`ingest_upload`)と「使用済み」の記録は同じトランザクションで行う。取り込みに
失敗した(画像でない・大きすぎる)ときはロールバックするので、URL は期限内ならもう一度使える。
同時に2回送られた場合は、条件付き UPDATE で片方だけが通る。
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.auth.sessions import hash_token
from app.domain.models import ApiToken, UploadTicket

TICKET_TTL = timedelta(minutes=10)
# 期限切れで未使用の行は、発行のついでに消す(使用済みの行は取り込んだ Asset との対応を残す)。
_PURGE_AFTER = timedelta(days=1)

ClaimFailure = Literal["not_found", "gone"]


def _utcnow() -> datetime:
    return datetime.now(UTC)


def issue_ticket(
    db: Session,
    *,
    user_id: uuid.UUID | None,
    api_token_id: uuid.UUID | None,
    now: datetime | None = None,
) -> tuple[UploadTicket, str]:
    """新しいチケットを作り、(行, 生のトークン) を返す(コミットは呼び出し側)。"""
    now = now or _utcnow()
    db.execute(
        delete(UploadTicket).where(
            UploadTicket.used_at.is_(None), UploadTicket.expires_at < now - _PURGE_AFTER
        )
    )
    raw = secrets.token_urlsafe(32)
    row = UploadTicket(
        token_hash=hash_token(raw),
        user_id=user_id,
        api_token_id=api_token_id,
        created_at=now,
        expires_at=now + TICKET_TTL,
    )
    db.add(row)
    db.flush()
    return row, raw


@dataclass(frozen=True)
class ClaimResult:
    ticket: UploadTicket | None
    failure: ClaimFailure | None = None


def claim_ticket(db: Session, raw: str, now: datetime | None = None) -> ClaimResult:
    """トークンを「使用済み」にして行を返す(コミットは呼び出し側。失敗時はロールバックすれば
    未使用に戻る)。

    - 不明なトークン: `not_found`
    - 使用済み・期限切れ・発行に使ったアクセストークンが失効済み: `gone`
    """
    now = now or _utcnow()
    ticket = db.execute(
        select(UploadTicket).where(UploadTicket.token_hash == hash_token(raw))
    ).scalar_one_or_none()
    if ticket is None:
        return ClaimResult(None, "not_found")
    if ticket.used_at is not None or ticket.expires_at <= now:
        return ClaimResult(None, "gone")
    if ticket.api_token_id is not None:
        token = db.get(ApiToken, ticket.api_token_id)
        if token is None or token.revoked_at is not None:
            return ClaimResult(None, "gone")
    # 同時に送られても片方だけが通るよう、未使用であることを条件に書き換える。
    result = db.execute(
        update(UploadTicket)
        .where(UploadTicket.id == ticket.id, UploadTicket.used_at.is_(None))
        .values(used_at=now)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:
        return ClaimResult(None, "gone")
    db.refresh(ticket)
    return ClaimResult(ticket)
