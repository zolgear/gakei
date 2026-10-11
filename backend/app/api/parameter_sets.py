"""パラメーターセット(ADR-0040)。生成のフォームの設定一式に名前を付けたもの。

証跡ではないので名前と中身を更新でき、削除は論理削除。`run` との外部キーは張らない。
件数が少ない前提でページングしない(プロンプトセットと同じ)。

- `params` にサーバーだけが書く項目(`comfyui_*`、`sdwebui_*`)が来たら 422。
- `provider` は登録簿にあるかを保存時には問わない(後で無効になることもあるため。
  読み込む側が判定する)。
- ADR-0025: 認証モードでは本人が作ったものだけを扱う。他人のセットは存在しないものと同じ 404。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import require_user
from app.auth.identity import CurrentUser
from app.deps import get_session
from app.domain.models import ParameterSet
from app.domain.schemas import (
    ParameterSetCreateRequest,
    ParameterSetListResponse,
    ParameterSetResponse,
    ParameterSetUpdateRequest,
)
from app.domain.visibility import get_visible_parameter_set, parameter_set_visible
from app.i18n import t

router = APIRouter(prefix="/api/parameter-sets", tags=["parameter-sets"])

# サーバーだけが書く `run.params` のキーの接頭辞(ComfyUI は ADR-0013、SD WebUI は ADR-0038 3章)。
SERVER_ONLY_PARAM_PREFIXES = ("comfyui_", "sdwebui_")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _reject_server_only_params(params: dict | None) -> None:
    if not params:
        return
    for key in params:
        if key.startswith(SERVER_ONLY_PARAM_PREFIXES):
            raise HTTPException(
                status_code=422, detail=t("parameterSets.serverOnlyParam", name=key)
            )


def _to_response(parameter_set: ParameterSet) -> ParameterSetResponse:
    return ParameterSetResponse(
        id=parameter_set.id,
        name=parameter_set.name,
        provider=parameter_set.provider,
        model=parameter_set.model,
        prompt=parameter_set.prompt,
        params=dict(parameter_set.params or {}),
        created_at=parameter_set.created_at,
        updated_at=parameter_set.updated_at,
    )


def _get_active(db: Session, parameter_set_id: uuid.UUID, user: CurrentUser) -> ParameterSet:
    parameter_set = get_visible_parameter_set(db, user, parameter_set_id)
    if parameter_set is None:
        raise HTTPException(status_code=404, detail=t("parameterSets.notFound"))
    return parameter_set


@router.get("", response_model=ParameterSetListResponse, operation_id="list_parameter_sets")
def list_parameter_sets(
    db: Session = Depends(get_session), user: CurrentUser = Depends(require_user)
) -> ParameterSetListResponse:
    rows = (
        db.execute(
            select(ParameterSet)
            .where(ParameterSet.deleted_at.is_(None), parameter_set_visible(user))
            .order_by(ParameterSet.updated_at.desc(), ParameterSet.id.desc())
        )
        .scalars()
        .all()
    )
    return ParameterSetListResponse(items=[_to_response(row) for row in rows])


@router.get(
    "/{parameter_set_id}",
    response_model=ParameterSetResponse,
    operation_id="get_parameter_set",
)
def get_parameter_set(
    parameter_set_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> ParameterSetResponse:
    return _to_response(_get_active(db, parameter_set_id, user))


@router.post(
    "",
    response_model=ParameterSetResponse,
    status_code=201,
    operation_id="create_parameter_set",
)
def create_parameter_set(
    body: ParameterSetCreateRequest,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> ParameterSetResponse:
    _reject_server_only_params(body.params)
    now = _utcnow()
    parameter_set = ParameterSet(
        name=body.name,
        provider=body.provider,
        model=body.model,
        prompt=body.prompt,
        params=dict(body.params),
        created_by_user_id=user.id,
        created_at=now,
        updated_at=now,
    )
    db.add(parameter_set)
    db.commit()
    return _to_response(parameter_set)


@router.patch(
    "/{parameter_set_id}",
    response_model=ParameterSetResponse,
    operation_id="update_parameter_set",
)
def update_parameter_set(
    parameter_set_id: uuid.UUID,
    body: ParameterSetUpdateRequest,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> ParameterSetResponse:
    parameter_set = _get_active(db, parameter_set_id, user)
    _reject_server_only_params(body.params)
    fields_set = body.model_fields_set
    if body.name is not None:
        parameter_set.name = body.name
    if body.provider is not None:
        parameter_set.provider = body.provider
    # model と prompt は null を送ると「保存しない」に戻す(送らなければ変えない)。
    if "model" in fields_set:
        parameter_set.model = body.model
    if "prompt" in fields_set:
        parameter_set.prompt = body.prompt
    if body.params is not None:
        parameter_set.params = dict(body.params)
    parameter_set.updated_at = _utcnow()
    db.commit()
    return _to_response(parameter_set)


@router.delete("/{parameter_set_id}", status_code=204, operation_id="delete_parameter_set")
def delete_parameter_set(
    parameter_set_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> None:
    parameter_set = _get_active(db, parameter_set_id, user)
    parameter_set.deleted_at = _utcnow()
    db.commit()
