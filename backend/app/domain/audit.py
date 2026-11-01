"""証跡(ADR-0006、ADR-0059 7章)。Run に現れない操作を `audit_event` に追記する。

- 追記のみ。行を書き換えたり消したりする関数は作らない。
- 呼ぶ側の変更と同じトランザクションに入れる(`record` は add するだけ。commit は呼ぶ側)。
  変更の後に commit 済みの場合は `record_now` を使う。
- 送り元(IP アドレス、User-Agent)は、要求があれば添える。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from app.auth.identity import CurrentUser
from app.domain.models import AuditEvent

_USER_AGENT_MAX = 500


def record(
    db: Session,
    user: CurrentUser | None,
    action: str,
    *,
    ip_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: uuid.UUID | str | None = None,
    detail: dict[str, Any] | None = None,
    request: Request | None = None,
) -> AuditEvent:
    event = AuditEvent(
        occurred_at=datetime.now(UTC),
        user_id=user.id if user is not None else None,
        action=action,
        ip_id=ip_id,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        detail=detail,
        client_ip=request.client.host if request is not None and request.client else None,
        user_agent=(request.headers.get("user-agent") or "")[:_USER_AGENT_MAX] or None
        if request is not None
        else None,
    )
    db.add(event)
    return event


def record_now(db: Session, user: CurrentUser | None, action: str, **kwargs: Any) -> None:
    """`record` して commit する(呼ぶ側の変更が commit 済みのとき)。"""
    record(db, user, action, **kwargs)
    db.commit()
