"""MCP 用のアクセストークン(ADR-0023 2章)。認証モード(oidc)だけの機能で、`none` モードでは
全エンドポイントを 404(`t("auth.disabled")`)にする(`api/users.py` と同じ扱い)。

自分のトークンだけを発行・一覧・失効できる。値は発行時の応答でだけ返し、DB には SHA-256 の
ハッシュだけを保存する。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.deps import require_user
from app.auth.identity import CurrentUser
from app.config import Settings
from app.deps import get_session, get_settings
from app.domain import api_tokens as api_tokens_domain
from app.domain.models import ApiToken
from app.domain.schemas import (
    ApiTokenCreateRequest,
    ApiTokenCreateResponse,
    ApiTokenListResponse,
    ApiTokenRow,
)
from app.i18n import t

router = APIRouter(prefix="/api/users/me/api-tokens", tags=["api-tokens"])


def _require_oidc_user(settings: Settings, user: CurrentUser) -> uuid.UUID:
    if settings.auth_mode != "oidc" or user.id is None:
        raise HTTPException(status_code=404, detail=t("auth.disabled"))
    return user.id


def _to_row(token: ApiToken) -> ApiTokenRow:
    return ApiTokenRow(
        id=token.id,
        name=token.name,
        created_at=token.created_at,
        last_used_at=token.last_used_at,
    )


@router.get("", response_model=ApiTokenListResponse, operation_id="list_api_tokens")
def list_api_tokens(
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> ApiTokenListResponse:
    user_id = _require_oidc_user(settings, user)
    tokens = api_tokens_domain.list_active_tokens(db, user_id)
    return ApiTokenListResponse(items=[_to_row(tok) for tok in tokens])


@router.post(
    "", response_model=ApiTokenCreateResponse, status_code=201, operation_id="create_api_token"
)
def create_api_token(
    body: ApiTokenCreateRequest,
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> ApiTokenCreateResponse:
    user_id = _require_oidc_user(settings, user)
    try:
        token, raw = api_tokens_domain.issue_token(db, user_id, body.name)
    except api_tokens_domain.ApiTokenNameError as e:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(e)) from e
    db.commit()
    return ApiTokenCreateResponse(**_to_row(token).model_dump(), token=raw)


@router.delete("/{token_id}", status_code=204, operation_id="revoke_api_token")
def revoke_api_token(
    token_id: uuid.UUID,
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> None:
    """失効させる(行は消さない。Run の `api_token_id` から参照されるため)。"""
    user_id = _require_oidc_user(settings, user)
    if not api_tokens_domain.revoke_token(db, user_id, token_id):
        raise HTTPException(status_code=404, detail=t("apiTokens.notFound"))
    db.commit()
