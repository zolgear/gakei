"""タグの API。

- GET /api/tags: タグ名と件数(ADR-0024 5章。オートコンプリートとストックの絞り込み用)。
- GET /api/tags/suggestions: プロンプトのタグ編集の候補(ADR-0039 2章)。
- GET /api/assets/{id}/prompt-tags: 画像のタグをプロンプトにするときの並び(ADR-0039 1章)。
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.annotation.tag_vocabulary import get_vocabulary
from app.auth.deps import require_user
from app.auth.identity import CurrentUser
from app.deps import get_data_dir, get_session
from app.domain import annotations as annotations_domain
from app.domain import prompt_tags as prompt_tags_domain
from app.domain.schemas import (
    PromptTagsResponse,
    TagCount,
    TagListResponse,
    TagSuggestion,
    TagSuggestionResponse,
)
from app.domain.visibility import get_visible_asset
from app.i18n import t

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


@router.get(
    "/api/tags/suggestions",
    response_model=TagSuggestionResponse,
    operation_id="suggest_prompt_tags",
)
def suggest_prompt_tags(
    q: str = Query(default="", max_length=200, description="打った文字。空なら候補なし"),
    limit: int = Query(default=prompt_tags_domain.SUGGESTION_MAX, ge=1, le=20),
    db: Session = Depends(get_session),
    data_dir: Path = Depends(get_data_dir),
    user: CurrentUser = Depends(require_user),
) -> TagSuggestionResponse:
    """WD Tagger の語彙と GAKEI のタグ(英数字のもの、本人に見える Asset のタグだけ)から、
    前方一致を先に、次に部分一致を、最大 20 件。"""
    vocabulary = get_vocabulary(data_dir)
    items = prompt_tags_domain.suggest_tags(db, user, vocabulary, q, limit)
    return TagSuggestionResponse(
        items=[TagSuggestion(name=i.name, source=i.source, count=i.count) for i in items],
        vocabulary_available=vocabulary is not None,
    )


@router.get(
    "/api/assets/{asset_id}/prompt-tags",
    response_model=PromptTagsResponse,
    operation_id="get_asset_prompt_tags",
)
def get_asset_prompt_tags(
    asset_id: uuid.UUID,
    db: Session = Depends(get_session),
    data_dir: Path = Depends(get_data_dir),
    user: CurrentUser = Depends(require_user),
) -> PromptTagsResponse:
    """removed を除き、語彙にあるタグを score の高い順、続けて人が付けた語彙にあるタグ。
    語彙が無い環境では英数字だけのタグ。名前はエスケープしない。"""
    # 他人の Asset は存在しないものと同じ 404(ADR-0025)。
    asset = get_visible_asset(db, user, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail=t("assets.notFound"))
    vocabulary = get_vocabulary(data_dir)
    return PromptTagsResponse(
        asset_id=asset.id,
        tags=prompt_tags_domain.prompt_tags_for_asset(db, asset.id, vocabulary),
        vocabulary_available=vocabulary is not None,
    )
