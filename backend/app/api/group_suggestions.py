"""グループの提案(ADR-0056)。実装は `app/domain/group_suggestions.py`。

- `GET /api/asset-groups/suggestions`: 提案の一覧(自動では何も保存しない)
- `POST /api/asset-groups/suggestions/accept`: 提案を受け入れる(新しいグループを作って入れる、
  または既存のグループへ移す。1回のトランザクション)

どちらも `require_user` で、見える範囲(ADR-0025)だけを扱う。埋め込みが無効、または使える
モデルが無い間、一覧は 409(`detail.code = "embeddings_unavailable"`)。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.auth.deps import require_user
from app.auth.identity import CurrentUser
from app.config import Settings
from app.deps import get_embedder, get_session, get_settings, get_vector_index
from app.domain import group_suggestions as domain
from app.domain.schemas import (
    AssetGroupRow,
    EmbeddingErrorDetail,
    GroupSuggestionAcceptRequest,
    GroupSuggestionsResponse,
)
from app.domain.semantic_search import EmbeddingsConflictError
from app.domain.vector_index import VectorIndex
from app.i18n import t
from app.worker.embedder import Embedder

router = APIRouter(prefix="/api/asset-groups/suggestions", tags=["asset-groups"])


def get_group_suggestion_cache(request: Request) -> domain.GroupSuggestionCache:
    """ADR-0056 8章: 集めた結果のキャッシュ。"""
    return request.app.state.group_suggestion_cache


@router.get(
    "",
    response_model=GroupSuggestionsResponse,
    operation_id="list_group_suggestions",
    responses={409: {"model": EmbeddingErrorDetail, "description": "埋め込みを使えない"}},
)
def list_group_suggestions(
    scope: Literal["ungrouped", "all"] = Query(
        default="ungrouped",
        description="ungrouped: グループ外の画像だけを提案に入れる / all: グループの画像も",
    ),
    granularity: Literal["normal", "fine"] = Query(
        default="normal", description="fine: 大きな集まりを細かく分ける"
    ),
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    index: VectorIndex = Depends(get_vector_index),
    embedder: Embedder = Depends(get_embedder),
    cache: domain.GroupSuggestionCache = Depends(get_group_suggestion_cache),
    user: CurrentUser = Depends(require_user),
) -> GroupSuggestionsResponse:
    """画像の埋め込みで集まりを見つけ、グループにする案を出す(大きい順)。"""
    try:
        return domain.suggest_groups(
            db,
            settings=settings,
            index=index,
            version_of=embedder.version,
            cache=cache,
            viewer=user,
            scope=scope,
            granularity=granularity,
        )
    except EmbeddingsConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail=EmbeddingErrorDetail(code=exc.code, message=exc.message).model_dump(),  # type: ignore[arg-type]
        ) from exc


@router.post(
    "/accept",
    response_model=AssetGroupRow,
    operation_id="accept_group_suggestion",
)
def accept_group_suggestion(
    body: GroupSuggestionAcceptRequest,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> AssetGroupRow:
    """提案を受け入れる。`name` なら新しいグループ(先頭に入る)を作って入れ、`target_group_id`
    ならそのグループへ移す。見えない・削除済みの画像やグループを含めば 404 で全体を拒む。"""
    try:
        row = domain.accept_suggestion(
            db,
            user,
            asset_ids=body.asset_ids,
            name=body.name,
            target_group_id=body.target_group_id,
        )
    except domain.GroupSuggestionTargetNotFoundError as e:
        db.rollback()
        raise HTTPException(status_code=404, detail=t("assetGroups.notFound")) from e
    except domain.AssetGroupAssetsMissingError as e:
        db.rollback()
        ids = ", ".join(str(i) for i in e.missing_ids)
        raise HTTPException(status_code=404, detail=t("assetGroups.assetNotFound", ids=ids)) from e
    db.commit()
    return row
