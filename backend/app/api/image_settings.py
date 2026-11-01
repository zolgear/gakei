"""画像から生成の設定を読み込む(ADR-0038 11章)。

`POST /api/image-settings/import` は、送られた画像に埋め込まれた情報(GAKEI の `gakei`
チャンク、ComfyUI の `prompt` チャンク、A1111 形式の `parameters`)から、生成のフォームに
入れる値を返す。9章と同じく画像は保存しない(メモリの上で読むだけで、ファイルにも DB にも
ログにも残さない。Asset にもしない)。判定の順は `app/domain/image_settings_import.py`。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.sdwebui import IMPORT_BODY_LIMIT, read_import_input
from app.body_guard import body_policy
from app.deps import get_registry, get_session
from app.domain.generation_meta import extract_generation_meta, read_comfyui_prompt_graph
from app.domain.image_settings_import import (
    GakeiRecipe,
    ImageSettingsImportError,
    ImportedSettings,
    comfy_workflow_name,
    is_registered_workflow,
    match_comfy_workflow,
    read_gakei_recipe,
    settings_from_comfy_graph,
    settings_from_gakei,
)
from app.domain.models import ComfyWorkflow
from app.domain.schemas import (
    ImageSettingsImportResponse,
    SdWebuiImportNote,
    SdWebuiImportUnapplied,
)
from app.i18n import t
from app.providers.registry import ProviderRegistry
from app.providers.sdwebui.import_params import build_form_values
from app.providers.sdwebui.provider import PROVIDER_NAME as SDWEBUI_PROVIDER
from app.providers.sdwebui.provider import SdWebuiProvider

router = APIRouter(prefix="/api/image-settings", tags=["image-settings"])

_COMFYUI_PROVIDER = "comfyui"


@dataclass
class _Embedded:
    """画像から読んだもの(どれも無ければ None)。"""

    gakei: GakeiRecipe | None
    comfy_graph: dict[str, Any] | None
    generation_meta: dict[str, Any] | None


def _read_embedded(data: bytes) -> _Embedded:
    return _Embedded(
        gakei=read_gakei_recipe(data),
        comfy_graph=read_comfyui_prompt_graph(data),
        generation_meta=extract_generation_meta(data),
    )


def _resolve_local(db: Session, embedded: _Embedded) -> ImportedSettings | None:
    """GAKEI の埋め込みと ComfyUI のグラフから読む。どちらも無ければ None(A1111 へ)。"""
    workflows: list[ComfyWorkflow] | None = None

    def load_workflows() -> list[ComfyWorkflow]:
        nonlocal workflows
        if workflows is None:
            workflows = list(
                db.execute(select(ComfyWorkflow).where(ComfyWorkflow.deleted_at.is_(None)))
                .scalars()
                .all()
            )
        return workflows

    recipe = embedded.gakei
    if recipe is not None:
        if recipe.provider != _COMFYUI_PROVIDER or is_registered_workflow(
            recipe.model, load_workflows()
        ):
            return settings_from_gakei(recipe)
        # この環境に無いワークフローの Run。画像のグラフで登録済みのものと照合する。
        candidate = (
            match_comfy_workflow(embedded.comfy_graph, load_workflows())
            if embedded.comfy_graph is not None
            else None
        )
        if candidate is None:
            raise ImageSettingsImportError(
                t("imageSettings.workflowNotFound", name=comfy_workflow_name(recipe))
            )
        return settings_from_comfy_graph(embedded.comfy_graph or {}, candidate)

    if embedded.comfy_graph is not None:
        candidate = match_comfy_workflow(embedded.comfy_graph, load_workflows())
        if candidate is None:
            raise ImageSettingsImportError(t("imageSettings.noMatchingWorkflow"))
        return settings_from_comfy_graph(embedded.comfy_graph, candidate)
    return None


def _to_response(settings: ImportedSettings) -> ImageSettingsImportResponse:
    return ImageSettingsImportResponse(
        source=settings.source,
        provider=settings.provider,
        model=settings.model,
        prompt=settings.prompt,
        params=settings.params,
        unapplied=[SdWebuiImportUnapplied(name=n, value=v) for n, v in settings.unapplied],
        notes=[SdWebuiImportNote(code=c, message=m) for c, m in settings.notes],
        software=settings.software,
    )


_IMPORT_OPENAPI: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "description": "画像のファイル(multipart の `file`。保存しない)。",
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "properties": {"file": {"type": "string", "format": "binary"}},
                    "required": ["file"],
                }
            }
        },
    }
}


@router.post(
    "/import",
    response_model=ImageSettingsImportResponse,
    operation_id="import_image_settings",
    openapi_extra=_IMPORT_OPENAPI,
    responses={
        409: {"description": "A1111 形式の画像だが、SD WebUI に接続していない・一覧を取れない"},
        413: {"description": "画像が大きすぎる"},
        422: {"description": "読み込める生成情報が無い、合うワークフローが無い、本文が不正"},
    },
)
@body_policy(max_bytes=IMPORT_BODY_LIMIT)
async def import_image_settings(
    request: Request,
    db: Session = Depends(get_session),
    registry: ProviderRegistry = Depends(get_registry),
) -> ImageSettingsImportResponse:
    """画像に埋め込まれた生成情報から、生成のフォームの値を作る(ADR-0038 11章)。"""
    data, asset_id = await read_import_input(request)
    if asset_id is not None or not data:
        raise HTTPException(status_code=422, detail=t("imageSettings.fileMissing"))
    try:
        embedded = await asyncio.to_thread(_read_embedded, data)
    finally:
        del data

    try:
        local = _resolve_local(db, embedded)
    except ImageSettingsImportError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from exc
    if local is not None:
        return _to_response(local)

    meta = embedded.generation_meta
    if meta is None or meta.get("tool") != "a1111":
        raise HTTPException(status_code=422, detail=t("imageSettings.noSettings"))
    provider = registry.get(SDWEBUI_PROVIDER)
    if not isinstance(provider, SdWebuiProvider):
        raise HTTPException(status_code=409, detail=t("imageSettings.sdwebuiNotConnected"))
    catalog = await asyncio.to_thread(provider.catalog)
    if catalog is None:
        raise HTTPException(status_code=409, detail=t("sdwebui.provider.unavailable"))
    result = build_form_values(meta, catalog)
    return ImageSettingsImportResponse(
        source="a1111",
        provider=SDWEBUI_PROVIDER,
        model=result.model,
        prompt=result.prompt,
        params=result.params,
        unapplied=[SdWebuiImportUnapplied(name=n, value=v) for n, v in result.unapplied],
        notes=[SdWebuiImportNote(code=n.code, message=n.message) for n in result.notes],
        software=result.software,
    )
