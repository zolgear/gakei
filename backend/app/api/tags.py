"""GET /api/tags。タグ名と件数(ADR-0024 5章。オートコンプリートとストックの絞り込み用)。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.auth.deps import require_user
from app.auth.identity import CurrentUser
from app.deps import get_session
from app.domain import annotations as annotations_domain
from app.domain.schemas import TagCount, TagListResponse

router = APIRouter(tags=["tags"])


@router.get("/api/tags", response_model=TagListResponse, operation_id="list_tags")
def list_tags(
    q: str | None = Query(
        default=None, description="部分一致で絞る(正規化してから比べる)。省略時は全件"
    ),
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> TagListResponse:
    """人が消したものと削除済みの Asset を除き、本人に見える Asset だけで数える(ADR-0025)。
    件数の多い順、同数は名前順。"""
    rows = annotations_domain.list_tags(db, user, q, limit)
    return TagListResponse(items=[TagCount(name=name, count=count) for name, count in rows])
