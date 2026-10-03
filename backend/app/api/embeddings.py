"""埋め込みを使った検索の API(ADR-0033 7章)。実装は `app/domain/semantic_search.py`。

- `GET /api/search/semantic`: 文章での検索
- `GET /api/assets/{id}/similar`: 似た画像(その Asset が見えること)
- `GET /api/embeddings/duplicates`: 重複の候補
- `GET /api/embeddings/graph`: マップの元データ

どれも利用者(`require_user`)向けで、見える範囲(ADR-0025)だけを扱う。埋め込みが無効、または
使えるモデルが無い間は 409(`detail.code = "embeddings_unavailable"`)。エンドポイントは同期関数に
して、検索の文章の推論をイベントループの外(スレッド)で行う。
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth.deps import require_user
from app.auth.identity import CurrentUser
from app.config import Settings
from app.deps import (
    get_embedder,
    get_query_vector_cache,
    get_session,
    get_settings,
    get_vector_index,
)
from app.domain import embedding_settings
from app.domain import semantic_search as domain
from app.domain.schemas import (
    DuplicatesResponse,
    EmbeddingErrorDetail,
    EmbeddingGraphResponse,
    SemanticSearchResponse,
    SimilarAssetsResponse,
)
from app.domain.vector_index import VectorIndex
from app.i18n import t
from app.worker.embedder import Embedder

router = APIRouter(tags=["embeddings"])

_CONFLICT = {409: {"model": EmbeddingErrorDetail, "description": "埋め込みを使えない"}}


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, domain.EmbeddingsConflictError):
        return HTTPException(
            status_code=409,
            detail=EmbeddingErrorDetail(code=exc.code, message=exc.message).model_dump(),  # type: ignore[arg-type]
        )
    if isinstance(exc, domain.EmbeddingsNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, domain.EmbeddingsInvalidQueryError):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, domain.EmbeddingsEngineError):
        return HTTPException(status_code=503, detail=t("embeddings.queryFailed", error=str(exc)))
    raise exc


_ERRORS = (
    domain.EmbeddingsConflictError,
    domain.EmbeddingsNotFoundError,
    domain.EmbeddingsInvalidQueryError,
    domain.EmbeddingsEngineError,
)


@router.get(
    "/api/search/semantic",
    response_model=SemanticSearchResponse,
    operation_id="semantic_search",
    responses=_CONFLICT,
)
def semantic_search_endpoint(
    q: str = Query(..., description="検索の文章(自然文)"),
    limit: int = Query(default=domain.SEARCH_DEFAULT_LIMIT, ge=1, le=domain.SEARCH_MAX_LIMIT),
    group_id: uuid.UUID | None = Query(default=None, description="このグループの画像に絞る"),
    tag: str | None = Query(default=None, description="このタグが付いた画像に絞る"),
    kind: Literal["upload", "generated", "sketch"] | None = Query(
        default=None, description="この種類の画像に絞る"
    ),
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    index: VectorIndex = Depends(get_vector_index),
    embedder: Embedder = Depends(get_embedder),
    cache: domain.QueryVectorCache = Depends(get_query_vector_cache),
    user: CurrentUser = Depends(require_user),
) -> SemanticSearchResponse:
    """文章で画像を探す(類似度の高い順)。キーワード検索(`GET /api/search`)とは別。"""
    try:
        return domain.semantic_search(
            db,
            settings=settings,
            index=index,
            engines=embedder.engines,
            cache=cache,
            viewer=user,
            q=q,
            limit=limit,
            group_id=group_id,
            tag=tag,
            kind=kind,
        )
    except _ERRORS as exc:
        raise _http_error(exc) from exc


@router.get(
    "/api/assets/{asset_id}/similar",
    response_model=SimilarAssetsResponse,
    operation_id="similar_assets",
    responses=_CONFLICT,
)
def similar_assets_endpoint(
    asset_id: uuid.UUID,
    limit: int = Query(default=domain.SEARCH_DEFAULT_LIMIT, ge=1, le=domain.SEARCH_MAX_LIMIT),
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    index: VectorIndex = Depends(get_vector_index),
    user: CurrentUser = Depends(require_user),
) -> SimilarAssetsResponse:
    """似た画像(起点の画像自身は含めない)。起点のベクトルがまだ無ければ 409
    (`detail.code` が `embedding_pending` / `embedding_failed` / `embedding_missing` /
    `embedding_not_supported`)。"""
    try:
        return domain.similar_assets(
            db, settings=settings, index=index, viewer=user, asset_id=asset_id, limit=limit
        )
    except _ERRORS as exc:
        raise _http_error(exc) from exc


@router.get(
    "/api/embeddings/duplicates",
    response_model=DuplicatesResponse,
    operation_id="embedding_duplicates",
    responses=_CONFLICT,
)
def duplicates_endpoint(
    threshold: float | None = Query(
        default=None,
        ge=embedding_settings.DUPLICATE_THRESHOLD_MIN,
        le=embedding_settings.DUPLICATE_THRESHOLD_MAX,
        description="類似度のしきい値。省略時は管理者設定の値",
    ),
    limit: int = Query(
        default=domain.DUPLICATES_DEFAULT_GROUPS,
        ge=1,
        le=domain.DUPLICATES_MAX_GROUPS,
        description="返すグループの数の上限",
    ),
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    index: VectorIndex = Depends(get_vector_index),
    user: CurrentUser = Depends(require_user),
) -> DuplicatesResponse:
    """重複の候補(類似度がしきい値以上で知覚ハッシュも近い組を、代表との類似度でまとめた
    グループ、大きい順)。"""
    try:
        return domain.find_duplicates(
            db, settings=settings, index=index, viewer=user, threshold=threshold, limit=limit
        )
    except _ERRORS as exc:
        raise _http_error(exc) from exc


@router.get(
    "/api/embeddings/graph",
    response_model=EmbeddingGraphResponse,
    operation_id="embedding_graph",
    responses=_CONFLICT,
)
def graph_endpoint(
    k: int = Query(default=domain.GRAPH_DEFAULT_K, ge=1, le=domain.GRAPH_MAX_K),
    limit: int = Query(default=domain.GRAPH_DEFAULT_LIMIT, ge=1, le=domain.GRAPH_MAX_LIMIT),
    group_id: uuid.UUID | None = Query(default=None),
    tag: str | None = Query(default=None),
    include_lineage: bool = Query(default=False, description="系列の主たる親の辺も返す"),
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    index: VectorIndex = Depends(get_vector_index),
    user: CurrentUser = Depends(require_user),
) -> EmbeddingGraphResponse:
    """マップの元データ。ノード(新しい順に `limit` 件まで)と、各ノードの k 近傍
    (先頭は自分自身)。"""
    try:
        return domain.build_graph(
            db,
            settings=settings,
            index=index,
            viewer=user,
            k=k,
            limit=limit,
            group_id=group_id,
            tag=tag,
            include_lineage=include_lineage,
        )
    except _ERRORS as exc:
        raise _http_error(exc) from exc
