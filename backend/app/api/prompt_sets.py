"""プロンプトセット(ADR-0009)。名前を付けたプロンプトの集まり。証跡ではないので
更新・論理削除ができる。`run` との外部キーは張らない。件数が少ない前提でページングしない。

ADR-0025: 認証モードでは本人が作ったもの(作成者が記録されていない以前のものは管理者)だけを
扱う。他人のセットは存在しないものと同じ 404。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.auth.deps import require_user
from app.auth.identity import CurrentUser
from app.deps import get_session
from app.domain.models import PromptSet, PromptSetItem
from app.domain.schemas import (
    PromptSetCreateRequest,
    PromptSetItemAppendRequest,
    PromptSetItemResponse,
    PromptSetItemUpdateRequest,
    PromptSetListResponse,
    PromptSetResponse,
    PromptSetUpdateRequest,
)
from app.domain.visibility import get_visible_prompt_set, prompt_set_visible
from app.i18n import t

router = APIRouter(prefix="/api/prompt-sets", tags=["prompt-sets"])


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _to_item_response(item: PromptSetItem) -> PromptSetItemResponse:
    return PromptSetItemResponse(
        id=item.id,
        label=item.label,
        text=item.text,
        position=item.position,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def _to_set_response(prompt_set: PromptSet, items: list[PromptSetItem]) -> PromptSetResponse:
    return PromptSetResponse(
        id=prompt_set.id,
        name=prompt_set.name,
        created_at=prompt_set.created_at,
        updated_at=prompt_set.updated_at,
        items=[_to_item_response(i) for i in items],
    )


def _get_active_set(db: Session, prompt_set_id: uuid.UUID, user: CurrentUser) -> PromptSet:
    prompt_set = get_visible_prompt_set(db, user, prompt_set_id)
    if prompt_set is None:
        raise HTTPException(
            status_code=404,
            detail=t("promptSets.notFound"),
        )
    return prompt_set


def _get_active_items(db: Session, prompt_set_id: uuid.UUID) -> list[PromptSetItem]:
    return (
        db.execute(
            select(PromptSetItem)
            .where(
                PromptSetItem.prompt_set_id == prompt_set_id,
                PromptSetItem.deleted_at.is_(None),
            )
            .order_by(PromptSetItem.position)
        )
        .scalars()
        .all()
    )


def _get_active_item(db: Session, prompt_set_id: uuid.UUID, item_id: uuid.UUID) -> PromptSetItem:
    item = db.get(PromptSetItem, item_id)
    if item is None or item.deleted_at is not None or item.prompt_set_id != prompt_set_id:
        raise HTTPException(
            status_code=404,
            detail=t("promptSets.itemNotFound"),
        )
    return item


def _renumber(items: list[PromptSetItem]) -> None:
    """`items` は望む並び順で渡す前提。position を 0 から振り直す。"""
    for index, item in enumerate(items):
        item.position = index


def _touch_set(db: Session, prompt_set_id: uuid.UUID, now: datetime) -> None:
    """項目側の変更でも updated_at を更新する(セット自体は変えていなくても「新しい順」に出す)。"""
    db.execute(update(PromptSet).where(PromptSet.id == prompt_set_id).values(updated_at=now))


@router.get("", response_model=PromptSetListResponse, operation_id="list_prompt_sets")
def list_prompt_sets(
    db: Session = Depends(get_session), user: CurrentUser = Depends(require_user)
) -> PromptSetListResponse:
    prompt_sets = (
        db.execute(
            select(PromptSet)
            .where(PromptSet.deleted_at.is_(None), prompt_set_visible(user))
            .order_by(PromptSet.updated_at.desc(), PromptSet.id.desc())
        )
        .scalars()
        .all()
    )
    items = [_to_set_response(ps, _get_active_items(db, ps.id)) for ps in prompt_sets]
    return PromptSetListResponse(items=items)


@router.post(
    "", response_model=PromptSetResponse, status_code=201, operation_id="create_prompt_set"
)
def create_prompt_set(
    body: PromptSetCreateRequest,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> PromptSetResponse:
    now = _utcnow()
    prompt_set = PromptSet(
        name=body.name, created_by_user_id=user.id, created_at=now, updated_at=now
    )
    db.add(prompt_set)
    db.flush()

    items: list[PromptSetItem] = []
    for index, item_in in enumerate(body.items):
        item = PromptSetItem(
            prompt_set_id=prompt_set.id,
            label=item_in.label,
            text=item_in.text,
            position=index,
            created_at=now,
            updated_at=now,
        )
        db.add(item)
        items.append(item)

    db.commit()
    return _to_set_response(prompt_set, items)


@router.patch(
    "/{prompt_set_id}", response_model=PromptSetResponse, operation_id="update_prompt_set"
)
def update_prompt_set(
    prompt_set_id: uuid.UUID,
    body: PromptSetUpdateRequest,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> PromptSetResponse:
    prompt_set = _get_active_set(db, prompt_set_id, user)
    prompt_set.name = body.name
    prompt_set.updated_at = _utcnow()
    db.commit()
    return _to_set_response(prompt_set, _get_active_items(db, prompt_set_id))


@router.delete("/{prompt_set_id}", status_code=204, operation_id="delete_prompt_set")
def delete_prompt_set(
    prompt_set_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> None:
    prompt_set = _get_active_set(db, prompt_set_id, user)
    prompt_set.deleted_at = _utcnow()
    db.commit()


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
    _get_active_set(db, prompt_set_id, user)
    existing = _get_active_items(db, prompt_set_id)
    now = _utcnow()
    item = PromptSetItem(
        prompt_set_id=prompt_set_id,
        label=body.label,
        text=body.text,
        position=len(existing),
        created_at=now,
        updated_at=now,
    )
    db.add(item)
    _touch_set(db, prompt_set_id, now)
    db.commit()
    return _to_item_response(item)


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
    _get_active_set(db, prompt_set_id, user)
    item = _get_active_item(db, prompt_set_id, item_id)
    now = _utcnow()
    fields_set = body.model_fields_set

    if "label" in fields_set:
        item.label = body.label
    if body.text is not None:
        item.text = body.text

    if body.position is not None:
        ordered = [i for i in _get_active_items(db, prompt_set_id) if i.id != item_id]
        target_position = max(0, min(body.position, len(ordered)))
        ordered.insert(target_position, item)
        _renumber(ordered)

    item.updated_at = now
    _touch_set(db, prompt_set_id, now)
    db.commit()
    return _to_item_response(item)


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
    _get_active_set(db, prompt_set_id, user)
    item = _get_active_item(db, prompt_set_id, item_id)
    now = _utcnow()
    item.deleted_at = now
    item.updated_at = now

    remaining = [i for i in _get_active_items(db, prompt_set_id) if i.id != item_id]
    _renumber(remaining)

    _touch_set(db, prompt_set_id, now)
    db.commit()
