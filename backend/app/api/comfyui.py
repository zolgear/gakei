"""ComfyUI 関連の API(ADR-0013)。

`/status` は ComfyUI の到達性を返す。`/workflows*` は登録済みワークフローの CRUD と、
登録画面向けの提案(`analyze`、保存はしない)。`ComfyUIProvider` 自体(実行)はここでは
扱わない。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.auth.deps import require_admin
from app.config import Settings
from app.deps import get_registry, get_runner, get_session, get_session_factory, get_settings
from app.domain.comfy_workflow import (
    SEED_MAX,
    Bindings,
    ExposedParam,
    WorkflowValidationError,
    analyze_workflow,
    compute_template_sha256,
    is_api_format_template,
    is_ui_format_template,
    not_api_format_message,
    ui_format_message,
    validate_workflow,
)
from app.domain.comfyui_connection import (
    ComfyUIConnectionValidationError,
    resolve_effective_url,
    save_connection_url,
    save_detached,
    validate_connection_url,
)
from app.domain.models import ComfyWorkflow, Run, RunStatus
from app.domain.schemas import (
    ComfyAnalyzeRequest,
    ComfyAnalyzeResponse,
    ComfyUIConnectionRequest,
    ComfyUIConnectionTestRequest,
    ComfyUIConnectionTestResponse,
    ComfyUIStatusResponse,
    ComfyWorkflowCreateRequest,
    ComfyWorkflowDetail,
    ComfyWorkflowListResponse,
    ComfyWorkflowSummary,
    ComfyWorkflowUpdateRequest,
)
from app.i18n import t
from app.providers.comfyui.client import check_available
from app.providers.registry import ProviderRegistry, _is_loopback_url
from app.worker.runner import Runner

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/comfyui", tags=["comfyui"])


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _check_template_format(template: dict) -> None:
    if is_ui_format_template(template):
        raise HTTPException(status_code=422, detail=ui_format_message())
    if not is_api_format_template(template):
        raise HTTPException(status_code=422, detail=not_api_format_message())


def _validate_or_422(
    template: dict, operation: str, bindings: Bindings, exposed_params: list[ExposedParam]
) -> None:
    try:
        validate_workflow(template, operation, bindings, exposed_params)  # type: ignore[arg-type]
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _to_summary(wf: ComfyWorkflow) -> ComfyWorkflowSummary:
    return ComfyWorkflowSummary(
        id=wf.id,
        name=wf.name,
        operation=wf.operation,  # type: ignore[arg-type]
        template_sha256=wf.template_sha256,
        created_at=wf.created_at,
        updated_at=wf.updated_at,
    )


def _to_detail(wf: ComfyWorkflow) -> ComfyWorkflowDetail:
    summary = _to_summary(wf)
    return ComfyWorkflowDetail(
        **summary.model_dump(),
        template=wf.template,
        bindings=Bindings.model_validate(wf.bindings),
        exposed_params=[ExposedParam.model_validate(p) for p in wf.exposed_params],
    )


def _get_active(db: Session, workflow_id: uuid.UUID) -> ComfyWorkflow:
    wf = db.get(ComfyWorkflow, workflow_id)
    if wf is None or wf.deleted_at is not None:
        raise HTTPException(status_code=404, detail=t("comfyui.api.workflowNotFound"))
    return wf


def _fetch_object_info(base_url: str, timeout: float = 2.0) -> dict[str, Any] | None:
    """`/object_info` を取得する。失敗したら None(呼び出し側は「今のまま」にフォールバックする)。"""
    try:
        response = httpx.get(f"{base_url.rstrip('/')}/object_info", timeout=timeout)
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _lookup_input_spec(
    object_info: dict[str, Any], class_type: str | None, input_name: str
) -> tuple[Any, Any] | None:
    if class_type is None:
        return None
    node_info = object_info.get(class_type)
    if not isinstance(node_info, dict):
        return None
    input_groups = node_info.get("input")
    if not isinstance(input_groups, dict):
        return None
    for group_name in ("required", "optional"):
        group = input_groups.get(group_name)
        if not isinstance(group, dict):
            continue
        entry = group.get(input_name)
        if isinstance(entry, list) and entry:
            return entry[0], (entry[1] if len(entry) > 1 else None)
    return None


def _parse_object_info_spec(type_spec: Any, config: Any) -> dict[str, Any]:
    """`/object_info` の1入力ぶん(`[type_spec, config]`)を `ExposedParam` の更新値にする。

    新形式(V3。`COMBO` は `{"options": [...]}` 、`COMFY_DYNAMICCOMBO_V3` は
    `{"options": [{"key": ...}, ...]}`)と、旧形式(選択肢そのものが型の位置に来る
    `[[...選択肢...], {...}]`)の両方に対応する。
    """
    updates: dict[str, Any] = {}
    config = config if isinstance(config, dict) else {}

    if isinstance(type_spec, list):
        updates["type"] = "enum"
        updates["choices"] = [str(choice) for choice in type_spec]
    elif type_spec == "COMBO":
        options = config.get("options")
        if isinstance(options, list):
            updates["type"] = "enum"
            updates["choices"] = [str(opt) for opt in options]
    elif type_spec == "COMFY_DYNAMICCOMBO_V3":
        options = config.get("options")
        if isinstance(options, list):
            updates["type"] = "enum"
            updates["choices"] = [
                str(opt["key"]) for opt in options if isinstance(opt, dict) and "key" in opt
            ]
    elif type_spec in ("INT", "FLOAT"):
        updates["type"] = "int" if type_spec == "INT" else "float"
        if "min" in config:
            updates["minimum"] = config["min"]
        if "max" in config:
            max_value = config["max"]
            # JavaScript が正確に扱える整数(2^53 - 1)を超える範囲(例: KSampler.seed の
            # 2^64 - 1)は、UI・型を壊さないようそこで丸める。
            if type_spec == "INT" and isinstance(max_value, int) and max_value > SEED_MAX:
                max_value = SEED_MAX
            updates["maximum"] = max_value
        if "step" in config:
            updates["step"] = config["step"]
    elif type_spec == "STRING":
        updates["type"] = "text"

    default = config.get("default")
    if default is not None:
        updates["default"] = default

    return updates


def _enrich_candidate_params(
    candidates: list[ExposedParam], template: dict[str, Any], object_info: dict[str, Any]
) -> list[ExposedParam]:
    """`/object_info` から分かる範囲で、候補パラメータの型・choices・min/max/step を補う。

    テンプレートの値だけから推定した型(int/float/bool/text)より、ComfyUI が実際に
    公開している入力の仕様(コンボの choices、数値の min/max/step)の方が正確なため。
    """
    enriched: list[ExposedParam] = []
    for candidate in candidates:
        node = template.get(candidate.node)
        class_type = node.get("class_type") if isinstance(node, dict) else None
        spec = _lookup_input_spec(object_info, class_type, candidate.input)
        if spec is None:
            enriched.append(candidate)
            continue

        type_spec, config = spec
        updates = _parse_object_info_spec(type_spec, config)
        # 既定値はワークフローに設定されている値を優先する。ノード定義の default は、
        # テンプレートに値が無いときの補いにだけ使う。
        if candidate.default is not None:
            updates.pop("default", None)
        if not updates:
            enriched.append(candidate)
            continue
        try:
            enriched.append(candidate.model_copy(update=updates))
        except ValueError:
            # 補った値で ExposedParam の検証に通らない場合は、元の候補のまま出す。
            enriched.append(candidate)
    return enriched


def _extract_version_device(body: dict[str, Any] | None) -> tuple[str | None, str | None]:
    """`/system_stats` の応答からバージョンとデバイス名を取り出す。"""
    version: str | None = None
    device: str | None = None
    if body:
        system = body.get("system")
        if isinstance(system, dict):
            version = system.get("comfyui_version")
        devices = body.get("devices")
        if isinstance(devices, list) and devices and isinstance(devices[0], dict):
            device = devices[0].get("name")
    return version, device


def _is_comfyui_locked(db: Session) -> bool:
    """ComfyUI の Run が `queued` か `running` の間は接続設定を変えさせない(ADR-0013 7章)。"""
    return (
        db.execute(
            select(Run.id)
            .where(
                Run.provider == "comfyui",
                Run.status.in_((RunStatus.QUEUED, RunStatus.RUNNING)),
            )
            .limit(1)
        ).first()
        is not None
    )


def _build_status(db: Session, settings: Settings) -> ComfyUIStatusResponse:
    url, source = resolve_effective_url(db, settings)
    locked = _is_comfyui_locked(db)
    if url is None:
        return ComfyUIStatusResponse(
            url=None,
            enabled=False,
            available=False,
            reason=None,
            version=None,
            device=None,
            source=source,
            locked=locked,
            loopback=None,
        )

    available, reason, body = check_available(url, timeout=1.0)
    version, device = _extract_version_device(body)
    return ComfyUIStatusResponse(
        url=url,
        enabled=True,
        available=available,
        reason=reason,
        version=version,
        device=device,
        source=source,
        locked=locked,
        loopback=_is_loopback_url(url),
    )


@router.get("/status", response_model=ComfyUIStatusResponse, operation_id="get_comfyui_status")
def get_status(
    db: Session = Depends(get_session), settings: Settings = Depends(get_settings)
) -> ComfyUIStatusResponse:
    return _build_status(db, settings)


@router.post(
    "/connection/test",
    response_model=ComfyUIConnectionTestResponse,
    operation_id="test_comfyui_connection",
    dependencies=[Depends(require_admin)],
)
def test_connection(
    body: ComfyUIConnectionTestRequest,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ComfyUIConnectionTestResponse:
    """入力中の URL、または省略時は現在の有効な URL への接続を試す。設定は変えない。

    保存時と違い、ループバック以外でも確認チェックなしで試せる(送信するのは
    `/system_stats` への問い合わせだけで、画像やプロンプトは送らないため)。
    """
    url = body.url
    if url is None:
        url, _source = resolve_effective_url(db, settings)
    if url is None:
        raise HTTPException(
            status_code=422,
            detail=t("comfyui.api.connectionUrlMissing"),
        )

    try:
        validate_connection_url(url, allow_non_loopback=True)
    except ComfyUIConnectionValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    available, reason, body_ = check_available(url, timeout=2.0)
    version, device = _extract_version_device(body_)
    return ComfyUIConnectionTestResponse(
        url=url,
        available=available,
        reason=reason,
        version=version,
        device=device,
        loopback=_is_loopback_url(url),
    )


@router.put(
    "/connection",
    response_model=ComfyUIStatusResponse,
    operation_id="set_comfyui_connection",
    dependencies=[Depends(require_admin)],
)
async def set_connection(
    body: ComfyUIConnectionRequest,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    registry: ProviderRegistry = Depends(get_registry),
    runner: Runner = Depends(get_runner),
    session_factory: sessionmaker = Depends(get_session_factory),
) -> ComfyUIStatusResponse:
    """接続・URL の変更。再起動なしでレジストリと実行レーンに反映する(ADR-0013 7章)。

    `runner.ensure_lane` がレーンの無いプロバイダーには `asyncio.create_task` を呼ぶため、
    実行中のイベントループ上で動く必要がある(同期のエンドポイントはスレッドプールで
    実行され、その場に実行中ループが無いため)。async にしているのはそのため。
    """
    if _is_comfyui_locked(db):
        raise HTTPException(
            status_code=409,
            detail=t("comfyui.api.lockedChangeConnection"),
        )

    try:
        validate_connection_url(body.url, allow_non_loopback=body.allow_non_loopback)
    except ComfyUIConnectionValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if not _is_loopback_url(body.url):
        logger.warning(
            "ComfyUI の接続先にループバック以外のアドレスが設定されました(%s)。"
            "認証のない ComfyUI に入力画像とプロンプトを送信することになります。",
            body.url,
        )

    save_connection_url(db, body.url)

    from app.providers.comfyui.provider import ComfyUIProvider

    registry.set_comfyui(
        ComfyUIProvider(body.url, session_factory, settings.comfyui_timeout_seconds)
    )
    runner.ensure_lane("comfyui")

    # 接続の確認(同期の HTTP)でイベントループを止めないよう、スレッドで行う。
    return await asyncio.to_thread(_build_status, db, settings)


@router.delete(
    "/connection",
    response_model=ComfyUIStatusResponse,
    operation_id="detach_comfyui",
    dependencies=[Depends(require_admin)],
)
def detach_connection(
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    registry: ProviderRegistry = Depends(get_registry),
) -> ComfyUIStatusResponse:
    """切り離す。登録済みワークフローと過去の Run は消さない(ADR-0013 7章)。"""
    if _is_comfyui_locked(db):
        raise HTTPException(
            status_code=409,
            detail=t("comfyui.api.lockedDetach"),
        )

    save_detached(db)
    registry.set_comfyui(None)

    return _build_status(db, settings)


@router.post(
    "/workflows/analyze",
    response_model=ComfyAnalyzeResponse,
    operation_id="analyze_comfy_workflow",
    dependencies=[Depends(require_admin)],
)
def analyze(
    body: ComfyAnalyzeRequest,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ComfyAnalyzeResponse:
    _check_template_format(body.template)

    # 接続できれば /object_info を先に取っておき、analyze_workflow 自体に渡す(seed・出力
    # ノードの推定にも使うため)。接続できない・失敗した場合は None のまま
    # (analyze_workflow はテンプレートの値だけからの推定にフォールバックする)。
    # URL は画面の接続設定(無ければ環境変数)から解決する(ADR-0013 7章)。
    object_info: dict[str, Any] | None = None
    url, _source = resolve_effective_url(db, settings)
    if url:
        available, _reason, _info = check_available(url, timeout=1.0)
        if available:
            object_info = _fetch_object_info(url)

    result = analyze_workflow(body.template, object_info)

    candidate_params = result.candidate_params
    if object_info is not None:
        # 型・choices・min/max/step も同じ /object_info から補う。
        candidate_params = _enrich_candidate_params(candidate_params, body.template, object_info)

    return ComfyAnalyzeResponse(
        nodes=result.nodes,
        suggested_bindings=result.suggested_bindings,
        suggested_operation=result.suggested_operation,
        candidate_params=candidate_params,
        warnings=result.warnings,
    )


@router.post(
    "/workflows",
    response_model=ComfyWorkflowDetail,
    status_code=201,
    operation_id="create_comfy_workflow",
    dependencies=[Depends(require_admin)],
)
def create_workflow(
    body: ComfyWorkflowCreateRequest, db: Session = Depends(get_session)
) -> ComfyWorkflowDetail:
    _check_template_format(body.template)
    _validate_or_422(body.template, body.operation, body.bindings, body.exposed_params)

    now = _utcnow()
    wf = ComfyWorkflow(
        name=body.name,
        operation=body.operation,
        template=body.template,
        bindings=body.bindings.model_dump(mode="json"),
        exposed_params=[p.model_dump(mode="json") for p in body.exposed_params],
        template_sha256=compute_template_sha256(body.template),
        created_at=now,
        updated_at=now,
    )
    db.add(wf)
    db.commit()
    return _to_detail(wf)


@router.get(
    "/workflows", response_model=ComfyWorkflowListResponse, operation_id="list_comfy_workflows"
)
def list_workflows(db: Session = Depends(get_session)) -> ComfyWorkflowListResponse:
    workflows = (
        db.execute(
            select(ComfyWorkflow)
            .where(ComfyWorkflow.deleted_at.is_(None))
            .order_by(ComfyWorkflow.updated_at.desc(), ComfyWorkflow.id.desc())
        )
        .scalars()
        .all()
    )
    return ComfyWorkflowListResponse(items=[_to_summary(w) for w in workflows])


@router.get(
    "/workflows/{workflow_id}",
    response_model=ComfyWorkflowDetail,
    operation_id="get_comfy_workflow",
)
def get_workflow(workflow_id: uuid.UUID, db: Session = Depends(get_session)) -> ComfyWorkflowDetail:
    wf = _get_active(db, workflow_id)
    return _to_detail(wf)


@router.patch(
    "/workflows/{workflow_id}",
    response_model=ComfyWorkflowDetail,
    operation_id="update_comfy_workflow",
    dependencies=[Depends(require_admin)],
)
def update_workflow(
    workflow_id: uuid.UUID, body: ComfyWorkflowUpdateRequest, db: Session = Depends(get_session)
) -> ComfyWorkflowDetail:
    wf = _get_active(db, workflow_id)
    fields_set = body.model_fields_set

    name = body.name if "name" in fields_set else wf.name
    operation = body.operation if "operation" in fields_set else wf.operation
    template = body.template if "template" in fields_set else wf.template
    bindings = body.bindings if "bindings" in fields_set else Bindings.model_validate(wf.bindings)
    exposed_params = (
        body.exposed_params
        if "exposed_params" in fields_set
        else [ExposedParam.model_validate(p) for p in wf.exposed_params]
    )

    _check_template_format(template)
    _validate_or_422(template, operation, bindings, exposed_params)

    wf.name = name
    wf.operation = operation
    if "template" in fields_set:
        wf.template = template
        wf.template_sha256 = compute_template_sha256(template)
    wf.bindings = bindings.model_dump(mode="json")
    wf.exposed_params = [p.model_dump(mode="json") for p in exposed_params]
    wf.updated_at = _utcnow()
    db.commit()
    return _to_detail(wf)


@router.delete(
    "/workflows/{workflow_id}",
    status_code=204,
    operation_id="delete_comfy_workflow",
    dependencies=[Depends(require_admin)],
)
def delete_workflow(workflow_id: uuid.UUID, db: Session = Depends(get_session)) -> None:
    wf = _get_active(db, workflow_id)
    wf.deleted_at = _utcnow()
    db.commit()
