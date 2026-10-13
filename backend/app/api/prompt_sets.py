"""プロンプトセット(ADR-0009)。名前を付けたプロンプトの集まり。証跡ではないので
更新・論理削除ができる。`run` との外部キーは張らない。件数が少ない前提でページングしない。

ロジックは `app/domain/prompt_sets.py` にある(生成のアシスタントの道具と共用する。ADR-0048
4章)。ここは HTTP の形への変換だけを行う。

ADR-0025: 認証モードでは本人が作ったもの(作成者が記録されていない以前のものは管理者)だけを
扱う。他人のセットは存在しないものと同じ 404。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.deps import require_user
from app.auth.identity import CurrentUser
from app.deps import get_session
from app.domain import prompt_sets as domain
from app.domain.schemas import (
    PromptSetCreateRequest,
    PromptSetItemAppendRequest,
    PromptSetItemResponse,
    PromptSetItemUpdateRequest,
    PromptSetListResponse,
    PromptSetResponse,
    PromptSetUpdateRequest,
)

router = APIRouter(prefix="/api/prompt-sets", tags=["prompt-sets"])

_NOT_FOUND = (domain.PromptSetNotFoundError, domain.PromptSetItemNotFoundError)


def _not_found(e: Exception) -> HTTPException:
    return HTTPException(status_code=404, detail=str(e))


@router.get("", response_model=PromptSetListResponse, operation_id="list_prompt_sets")
def list_prompt_sets(
    db: Session = Depends(get_session), user: CurrentUser = Depends(require_user)
) -> PromptSetListResponse:
    return PromptSetListResponse(items=domain.list_sets(db, user))


@router.post(
    "", response_model=PromptSetResponse, status_code=201, operation_id="create_prompt_set"
)
def create_prompt_set(
    body: PromptSetCreateRequest,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> PromptSetResponse:
    items = [domain.ItemInput(label=i.label, text=i.text) for i in body.items]
    return domain.create_set(db, user, body.name, items)


@router.patch(
    "/{prompt_set_id}", response_model=PromptSetResponse, operation_id="update_prompt_set"
)
def update_prompt_set(
    prompt_set_id: uuid.UUID,
    body: PromptSetUpdateRequest,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> PromptSetResponse:
    try:
        return domain.rename_set(db, user, prompt_set_id, body.name)
    except _NOT_FOUND as e:
        raise _not_found(e) from e


@router.delete("/{prompt_set_id}", status_code=204, operation_id="delete_prompt_set")
def delete_prompt_set(
    prompt_set_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> None:
    try:
        domain.delete_set(db, user, prompt_set_id)
    except _NOT_FOUND as e:
        raise _not_found(e) from e


@router.post(
    "/{prompt_set_id}/items",
    response_model=PromptSetItemResponse,
    status_code=201,
    operation_id="add_prompt_set_item",
)
def add_prompt_set_item(
    prompt_set_id: uuid.UUID,
    body: PromptSetItemAppendRequest,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> PromptSetItemResponse:
    try:
        items = domain.add_items(
            db, user, prompt_set_id, [domain.ItemInput(label=body.label, text=body.text)]
        )
    except _NOT_FOUND as e:
        raise _not_found(e) from e
    return items[0]


@router.patch(
    "/{prompt_set_id}/items/{item_id}",
    response_model=PromptSetItemResponse,
    operation_id="update_prompt_set_item",
)
def update_prompt_set_item(
    prompt_set_id: uuid.UUID,
    item_id: uuid.UUID,
    body: PromptSetItemUpdateRequest,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> PromptSetItemResponse:
    kwargs: dict = {"text": body.text, "position": body.position}
    if "label" in body.model_fields_set:
        kwargs["label"] = body.label
    try:
        return domain.update_item(db, user, prompt_set_id, item_id, **kwargs)
    except _NOT_FOUND as e:
        raise _not_found(e) from e


@router.delete(
    "/{prompt_set_id}/items/{item_id}",
    status_code=204,
    operation_id="delete_prompt_set_item",
)
def delete_prompt_set_item(
    prompt_set_id: uuid.UUID,
    item_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> None:
    try:
        domain.delete_item(db, user, prompt_set_id, item_id)
    except _NOT_FOUND as e:
        raise _not_found(e) from e
