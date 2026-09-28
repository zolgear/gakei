"""GET /api/search。テキストの部分一致によるグローバル検索(ADR-0009 6章)。

実装は app/domain/search.py に閉じる(将来 embedding 検索を足すときはそこだけ差し替える)。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth.deps import require_user
from app.auth.identity import CurrentUser
from app.deps import get_session
from app.domain.schemas import SearchResponse
from app.domain.search import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    InvalidSearchQueryError,
    parse_types,
    search,
)

router = APIRouter(tags=["search"])


@router.get("/api/search", response_model=SearchResponse, operation_id="search")
def search_endpoint(
    q: str = Query(..., description="検索語。複数語は空白区切りでAND"),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT, description="種別ごとの上限"),
    types: str | None = Query(
        default=None, description="カンマ区切り。省略時は run,asset,prompt_set 全部"
    ),
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> SearchResponse:
    try:
        selected_types = parse_types(types)
        return search(db, q, viewer=user, limit=limit, types=selected_types)
    except InvalidSearchQueryError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
