"""グループ(ストックの手動整理。ADR-0022)。証跡ではないので更新・論理削除ができる。
件数が少ない前提でページングしない(prompt-sets と同じ)。
1 つの Asset が属するグループは 1 つだけで、`POST /{id}/assets` は「移す」
(2026-09-28 に多対多から変更)。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.deps import require_user
from app.auth.identity import CurrentUser
from app.deps import get_session
from app.domain.asset_groups import (
    AssetGroupAssetsMissingError,
    AssetGroupOrderMismatchError,
    add_members,
    create_group,
    delete_group,
    list_groups,
    remove_members,
    rename_group,
    reorder_groups,
)
from app.domain.models import AssetGroup
from app.domain.schemas import (
    AssetGroupCreate,
    AssetGroupListResponse,
    AssetGroupMembersRequest,
    AssetGroupOrderRequest,
    AssetGroupRow,
    AssetGroupUpdate,
)
from app.domain.visibility import get_visible_group
from app.i18n import t

router = APIRouter(prefix="/api/asset-groups", tags=["asset-groups"])


def _get_active_group(db: Session, group_id: uuid.UUID, user: CurrentUser) -> AssetGroup:
    """見える、削除されていないグループ。他人のグループは存在しないものと同じ 404(ADR-0025)。"""
    group = get_visible_group(db, user, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail=t("assetGroups.notFound"))
    return group


@router.get("", response_model=AssetGroupListResponse, operation_id="list_asset_groups")
def list_asset_groups(
    db: Session = Depends(get_session), user: CurrentUser = Depends(require_user)
) -> AssetGroupListResponse:
    return AssetGroupListResponse(items=list_groups(db, user))


@router.post("", response_model=AssetGroupRow, status_code=201, operation_id="create_asset_group")
def create_asset_group(
    body: AssetGroupCreate,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> AssetGroupRow:
    row = create_group(db, body.name, user)
    db.commit()
    return row


# `/{group_id}` より先に宣言する(`order` が group_id のパスに吸われないように)。
@router.put("/order", response_model=AssetGroupListResponse, operation_id="reorder_asset_groups")
def reorder_asset_groups(
    body: AssetGroupOrderRequest,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> AssetGroupListResponse:
    try:
        items = reorder_groups(db, body.group_ids, user)
    except AssetGroupOrderMismatchError as e:
        db.rollback()
        raise HTTPException(status_code=422, detail=t("assetGroups.orderMismatch")) from e
    db.commit()
    return AssetGroupListResponse(items=items)


@router.patch("/{group_id}", response_model=AssetGroupRow, operation_id="update_asset_group")
def update_asset_group(
    group_id: uuid.UUID,
    body: AssetGroupUpdate,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> AssetGroupRow:
    group = _get_active_group(db, group_id, user)
    row = rename_group(db, group, body.name, user)
    db.commit()
    return row


@router.delete("/{group_id}", status_code=204, operation_id="delete_asset_group")
def delete_asset_group(
    group_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> None:
    group = _get_active_group(db, group_id, user)
    delete_group(db, group)
    db.commit()


@router.post(
    "/{group_id}/assets",
    response_model=AssetGroupRow,
    operation_id="add_asset_group_assets",
)
def add_asset_group_assets(
    group_id: uuid.UUID,
    body: AssetGroupMembersRequest,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> AssetGroupRow:
    """Asset をこのグループへ移す。別のグループに入っていれば外してから入れ、既に入っている
    ものは無視する。削除済みや存在しない(他人のものを含む)Asset が1件でもあれば 404 で
    全体を拒む。"""
    group = _get_active_group(db, group_id, user)
    try:
        row = add_members(db, group, body.asset_ids, viewer=user)
    except AssetGroupAssetsMissingError as e:
        db.rollback()
        ids = ", ".join(str(i) for i in e.missing_ids)
        raise HTTPException(status_code=404, detail=t("assetGroups.assetNotFound", ids=ids)) from e
    db.commit()
    return row


@router.post(
    "/{group_id}/assets/remove",
    response_model=AssetGroupRow,
    operation_id="remove_asset_group_assets",
)
def remove_asset_group_assets(
    group_id: uuid.UUID,
    body: AssetGroupMembersRequest,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> AssetGroupRow:
    group = _get_active_group(db, group_id, user)
    row = remove_members(db, group, body.asset_ids, user)
    db.commit()
    return row
