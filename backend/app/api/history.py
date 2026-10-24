"""履歴の画像単位の並び(ADR-0054)。日付の並びは `GET /api/runs` のまま。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.auth.deps import require_user
from app.auth.identity import CurrentUser
from app.config import Settings
from app.deps import get_embedder, get_session, get_settings, get_vector_index
from app.domain import history_sort as domain
from app.domain.history_filters import HistoryLabelFilter, HistoryShape
from app.domain.schemas import EmbeddingErrorDetail, HistoryAssetListResponse, HistorySort
from app.domain.semantic_search import EmbeddingsConflictError
from app.domain.vector_index import VectorIndex
from app.worker.embedder import Embedder

router = APIRouter(prefix="/api/history", tags=["history"])


def get_history_order_cache(request: Request) -> domain.EmbeddingOrderCache:
    """ADR-0054 3章: 似た画像の順の結果のキャッシュ。"""
    return request.app.state.history_order_cache


@router.get(
    "/assets",
    response_model=HistoryAssetListResponse,
    operation_id="list_history_assets",
    responses={409: {"model": EmbeddingErrorDetail, "description": "埋め込みを使えない"}},
)
def list_history_assets(
    sort: HistorySort = Query(..., description="group: グループごと / embedding: 似た画像の順"),
    limit: int = Query(default=30, ge=1, le=200),
    cursor: str | None = Query(default=None),
    favorite: bool = Query(default=False, description="true: お気に入りの画像だけ(ADR-0055)"),
    shape: HistoryShape | None = Query(
        default=None, description="この形(正方形・縦長・横長)の画像だけ(ADR-0055 4章)"
    ),
    transparent: bool = Query(default=False, description="true: 背景を透過で頼んだ Run の画像だけ"),
    regions: bool = Query(
        default=False, description="true: 領域の四角を付けた Run の画像だけ(ADR-0053)"
    ),
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    index: VectorIndex = Depends(get_vector_index),
    embedder: Embedder = Depends(get_embedder),
    cache: domain.EmbeddingOrderCache = Depends(get_history_order_cache),
    user: CurrentUser = Depends(require_user),
) -> HistoryAssetListResponse:
    """画像(生成された Asset)単位の履歴。画像の無い Run は出さない。"""
    labels = HistoryLabelFilter(shape=shape, transparent=transparent, regions=regions)
    try:
        if sort == "group":
            return domain.list_by_group(
                db, user, limit=limit, cursor=cursor, favorite=favorite, labels=labels
            )
        return domain.list_by_embedding(
            db,
            user,
            settings=settings,
            index=index,
            version_of=embedder.version,
            cache=cache,
            limit=limit,
            cursor=cursor,
            favorite=favorite,
            labels=labels,
        )
    except domain.InvalidHistoryCursorError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except EmbeddingsConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail=EmbeddingErrorDetail(code=exc.code, message=exc.message).model_dump(),  # type: ignore[arg-type]
        ) from exc
