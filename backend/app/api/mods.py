"""管理者設定の「Mod」(ADR-0051 7章)。すべて管理者だけ(`require_admin`)。

- Webhook の作成・更新・削除(署名の鍵は一部も返さず、設定済みかどうかだけ)。
- `MODS_DIR` に置いた Python の Mod の一覧(読み込みエラーを含む)・有効化・設定の値。
- 呼ぶ順番、テスト送信(ダミーのイベント。記録は残さない)。
- 直近の呼び出し(`mod_invocation`)と、配達(`mod_delivery`)の一覧。

設定を変えたら、`ModRuntime.reload` で有効な購読を作り直す(再起動は要らない)。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Literal, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.auth.deps import require_admin
from app.auth.identity import CurrentUser
from app.config import Settings
from app.deps import get_session, get_settings
from app.domain.models import ModDelivery, ModInvocation
from app.domain.schemas import (
    ModDeliveryListResponse,
    ModDeliveryView,
    ModFilters,
    ModInvocationListResponse,
    ModInvocationView,
    ModOrderRequest,
    ModSettingFieldView,
    ModsResponse,
    ModsUpdateRequest,
    ModTestRequest,
    ModTestResponse,
    ModWebhookCreateRequest,
    ModWebhookUpdateRequest,
    ModWebhookView,
    PythonModUpdateRequest,
    PythonModView,
)
from app.i18n import current_locale, t
from app.mods import config as mods_config
from app.mods import loader
from app.mods.runtime import ModNotEnabledError, ModRuntime, PythonModStatus

router = APIRouter(prefix="/api/settings/mods", tags=["settings"])

_LIST_DEFAULT = 50
_LIST_MAX = 200


def _runtime(request: Request) -> ModRuntime:
    return request.app.state.mods


def _filters_view(filters: mods_config.Filters) -> ModFilters:
    return ModFilters(
        purposes=list(filters.purposes),  # type: ignore[arg-type]
        sources=list(filters.sources),  # type: ignore[arg-type]
        providers=list(filters.providers),
    )


def _label(label: str | dict[str, str]) -> str:
    if isinstance(label, str):
        return label
    return label.get(current_locale()) or label.get("ja") or next(iter(label.values()))


def _python_view(status: PythonModStatus, settings: Settings) -> PythonModView:
    folder = status.folder
    manifest = folder.manifest
    common = status.config.common
    returns_verdicts = manifest.returns_verdicts if manifest is not None else True
    fields: list[ModSettingFieldView] = []
    if manifest is not None:
        for spec in manifest.settings:
            if spec.secret:
                fields.append(
                    ModSettingFieldView(
                        key=spec.key,
                        type=spec.type,  # type: ignore[arg-type]
                        label=_label(spec.label),
                        secret_set=mods_config.read_python_secret(
                            settings.data_dir, folder.id, spec.key
                        )
                        is not None,
                    )
                )
            else:
                fields.append(
                    ModSettingFieldView(
                        key=spec.key,
                        type=spec.type,  # type: ignore[arg-type]
                        label=_label(spec.label),
                        default=spec.default,
                        value=status.config.values.get(spec.key, spec.default),
                    )
                )
    return PythonModView(
        id=folder.id,
        name=manifest.display_name(current_locale()) if manifest else None,
        version=manifest.version if manifest else None,
        api_version=manifest.api_version if manifest else None,
        available_events=list(manifest.events) if manifest else [],  # type: ignore[arg-type]
        error=status.error,
        loaded=status.loaded,
        enabled=common.enabled,
        events=list(common.events) if common.events is not None else None,  # type: ignore[arg-type]
        filters=_filters_view(common.filters),
        timeout_seconds=common.timeout_seconds,
        on_failure=common.on_failure,
        # 判定を返さない Mod では、保存した値が true でも待たない(実行時と同じに見せる)。
        wait_for_verdict=common.wait_for_verdict and returns_verdicts,
        returns_verdicts=returns_verdicts,
        order=common.order,
        settings=fields,
    )


def _webhook_view(w: mods_config.WebhookConfig, settings: Settings) -> ModWebhookView:
    secret = mods_config.read_webhook_secret(settings.data_dir, w.id)
    return ModWebhookView(
        id=w.id,
        name=w.name,
        url=w.url,
        enabled=w.common.enabled,
        events=list(w.common.events or ()),  # type: ignore[arg-type]
        filters=_filters_view(w.common.filters),
        send_images=w.send_images,
        timeout_seconds=w.common.timeout_seconds,
        on_failure=w.common.on_failure,
        wait_for_verdict=w.common.wait_for_verdict,
        order=w.common.order,
        secret_set=secret is not None,
        secret_too_short=mods_config.is_short_webhook_secret(secret),
    )


def _response(db: Session, settings: Settings, runtime: ModRuntime) -> ModsResponse:
    webhooks = [_webhook_view(w, settings) for w in mods_config.load_webhooks(db)]
    return ModsResponse(
        instance_name=mods_config.load_instance_name(db),
        webhooks=webhooks,
        python_mods=[_python_view(s, settings) for s in runtime.python_mods(db)],
    )


def _raise(exc: Exception) -> NoReturn:
    if isinstance(exc, mods_config.ModValidationError):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if isinstance(exc, mods_config.ModNotFoundError):
        raise HTTPException(status_code=404, detail=t("mods.notFound")) from exc
    raise exc


_ERRORS = (mods_config.ModValidationError, mods_config.ModNotFoundError)


def _fields(body: Any) -> dict[str, Any]:
    """送られた項目(`filters` は dict に)。"""
    result: dict[str, Any] = {}
    for name in body.model_fields_set:
        value = getattr(body, name)
        if isinstance(value, ModFilters):
            value = value.model_dump()
        result[name] = value
    return result


@router.get("", response_model=ModsResponse, operation_id="get_mods")
def get_mods(
    request: Request,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    _user: CurrentUser = Depends(require_admin),
) -> ModsResponse:
    return _response(db, settings, _runtime(request))


@router.patch("", response_model=ModsResponse, operation_id="update_mods")
def update_mods(
    body: ModsUpdateRequest,
    request: Request,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    _user: CurrentUser = Depends(require_admin),
) -> ModsResponse:
    runtime = _runtime(request)
    if "instance_name" in body.model_fields_set:
        try:
            mods_config.save_instance_name(db, body.instance_name)
        except _ERRORS as exc:
            _raise(exc)
        runtime.reload(db)
    return _response(db, settings, runtime)


# -- Webhook ----------------------------------------------------------------------


@router.post(
    "/webhooks", response_model=ModsResponse, status_code=201, operation_id="create_mod_webhook"
)
def create_webhook(
    body: ModWebhookCreateRequest,
    request: Request,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    _user: CurrentUser = Depends(require_admin),
) -> ModsResponse:
    runtime = _runtime(request)
    values = body.model_dump()
    try:
        mods_config.add_webhook(db, settings.data_dir, values)
    except _ERRORS as exc:
        _raise(exc)
    runtime.reload(db)
    return _response(db, settings, runtime)


@router.patch(
    "/webhooks/{webhook_id}", response_model=ModsResponse, operation_id="update_mod_webhook"
)
def update_webhook(
    webhook_id: str,
    body: ModWebhookUpdateRequest,
    request: Request,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    _user: CurrentUser = Depends(require_admin),
) -> ModsResponse:
    runtime = _runtime(request)
    try:
        mods_config.update_webhook(db, settings.data_dir, webhook_id, _fields(body))
    except _ERRORS as exc:
        _raise(exc)
    runtime.reload(db)
    return _response(db, settings, runtime)


@router.delete(
    "/webhooks/{webhook_id}", response_model=ModsResponse, operation_id="delete_mod_webhook"
)
def delete_webhook(
    webhook_id: str,
    request: Request,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    _user: CurrentUser = Depends(require_admin),
) -> ModsResponse:
    runtime = _runtime(request)
    try:
        mods_config.delete_webhook(db, settings.data_dir, webhook_id)
    except _ERRORS as exc:
        _raise(exc)
    runtime.reload(db)
    return _response(db, settings, runtime)


async def _test_send(
    request: Request, kind: Literal["webhook", "python"], mod_id: str, body: ModTestRequest
) -> ModTestResponse:
    runtime = _runtime(request)

    def build():  # noqa: ANN202
        with request.app.state.session_factory() as db:
            return runtime.subscriber_for_test(db, kind, mod_id)

    try:
        sub = await run_in_threadpool(build)
    except mods_config.ModNotFoundError as exc:
        raise HTTPException(status_code=404, detail=t("mods.notFound")) from exc
    except ModNotEnabledError as exc:
        raise HTTPException(status_code=409, detail=t("mods.enableFirst")) from exc
    except loader.ModLoadError as exc:
        raise HTTPException(status_code=409, detail=t("mods.loadFailed", error=str(exc))) from exc
    result = await runtime.test_send(sub, body.event)
    response = result.response
    if result.decision is not None:
        response = result.decision.to_json()
    elif result.outcome == "ok":
        response = [v.to_json() for v in result.verdicts]
    return ModTestResponse(
        outcome=result.outcome,  # type: ignore[arg-type]
        duration_ms=result.duration_ms,
        status_code=result.status_code,
        response=response,
        error=result.error,
    )


@router.post(
    "/webhooks/{webhook_id}/test",
    response_model=ModTestResponse,
    operation_id="test_mod_webhook",
)
async def test_webhook(
    webhook_id: str,
    request: Request,
    body: ModTestRequest | None = None,
    _user: CurrentUser = Depends(require_admin),
) -> ModTestResponse:
    """ダミーのイベントを送り、応答と所要時間を返す(有効でなくても送れる)。"""
    return await _test_send(request, "webhook", webhook_id, body or ModTestRequest())


# -- Python の Mod -------------------------------------------------------------------


def _python_updates(
    folder: loader.ModFolder, current: mods_config.PythonModConfig, body: PythonModUpdateRequest
) -> tuple[mods_config.PythonModConfig, dict[str, str | None]]:
    """更新後の設定と、書き換える秘密の値(None は消す)。検証してから返す。"""
    fields = _fields(body)
    manifest = folder.manifest
    allowed_events = manifest.events if manifest is not None else ()
    if (
        fields.get("wait_for_verdict") is True
        and manifest is not None
        and not manifest.returns_verdicts
    ):
        # 判定を返さない Mod を待っても、失敗が `deny` になるだけ(ADR-0051 2026-10-16 追記)。
        raise mods_config.ModValidationError(t("mods.waitForVerdictUnsupported"))
    common = mods_config.apply_common_updates(
        current.common,
        {k: v for k, v in fields.items() if k in mods_config.COMMON_FIELDS},
        allowed_events=allowed_events,
    )
    values = dict(current.values)
    secrets: dict[str, str | None] = {}
    if body.values is not None or body.secrets is not None:
        if manifest is None:
            raise mods_config.ModValidationError(t("mods.manifestInvalid"))
        specs = {spec.key: spec for spec in manifest.settings}
        for key, value in (body.values or {}).items():
            spec = specs.get(key)
            if spec is None or spec.secret:
                raise mods_config.ModValidationError(t("mods.unknownSetting", key=key))
            if value is None:
                values.pop(key, None)
            else:
                values[key] = _check_value(spec, value)
        for key, value in (body.secrets or {}).items():
            spec = specs.get(key)
            if spec is None or not spec.secret:
                raise mods_config.ModValidationError(t("mods.unknownSetting", key=key))
            secrets[key] = mods_config.normalize_secret(value) if value is not None else None
    return replace(current, common=common, values=values), secrets


def _check_value(spec: loader.SettingField, value: Any) -> Any:
    ok = False
    if spec.type in ("string", "path"):
        ok = isinstance(value, str) and len(value) <= mods_config.SETTING_VALUE_MAX
    elif spec.type == "number":
        ok = isinstance(value, int | float) and not isinstance(value, bool)
    elif spec.type == "bool":
        ok = isinstance(value, bool)
    if not ok:
        raise mods_config.ModValidationError(t("mods.invalidSettingValue", key=spec.key))
    return value


@router.patch("/python/{mod_id}", response_model=ModsResponse, operation_id="update_python_mod")
def update_python_mod(
    mod_id: str,
    body: PythonModUpdateRequest,
    request: Request,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    _user: CurrentUser = Depends(require_admin),
) -> ModsResponse:
    runtime = _runtime(request)
    folder = runtime.folder(mod_id)
    if folder is None:
        raise HTTPException(status_code=404, detail=t("mods.notFound"))
    current = mods_config.python_config_or_default(mods_config.load_python_configs(db), mod_id)
    try:
        updated, secrets = _python_updates(folder, current, body)
    except _ERRORS as exc:
        _raise(exc)
    if updated.common.order is None and updated.common.enabled:
        # 初めて有効にしたときは、最後に呼ぶ。
        updated = replace(updated, common=replace(updated.common, order=mods_config.next_order(db)))
    mods_config.save_python_config(db, updated)
    for key, value in secrets.items():
        if value is None:
            mods_config.delete_python_secret(settings.data_dir, mod_id, key)
        else:
            mods_config.write_python_secret(settings.data_dir, mod_id, key, value)
    runtime.reload(db)
    return _response(db, settings, runtime)


@router.post(
    "/python/{mod_id}/test", response_model=ModTestResponse, operation_id="test_python_mod"
)
async def test_python_mod(
    mod_id: str,
    request: Request,
    body: ModTestRequest | None = None,
    _user: CurrentUser = Depends(require_admin),
) -> ModTestResponse:
    """ダミーのイベントで Mod の関数を呼び、応答と所要時間を返す(有効にしたものだけ)。"""
    return await _test_send(request, "python", mod_id, body or ModTestRequest())


# -- 順番 ----------------------------------------------------------------------------


@router.put("/order", response_model=ModsResponse, operation_id="update_mod_order")
def update_order(
    body: ModOrderRequest,
    request: Request,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    _user: CurrentUser = Depends(require_admin),
) -> ModsResponse:
    runtime = _runtime(request)
    for item in body.items:
        if item.kind == "python" and runtime.folder(item.id) is None:
            raise HTTPException(status_code=404, detail=t("mods.notFound"))
    try:
        mods_config.save_order(db, [(item.kind, item.id) for item in body.items])
    except _ERRORS as exc:
        _raise(exc)
    runtime.reload(db)
    return _response(db, settings, runtime)


# -- 記録 ----------------------------------------------------------------------------


@router.get(
    "/invocations", response_model=ModInvocationListResponse, operation_id="list_mod_invocations"
)
def list_invocations(
    limit: int = Query(default=_LIST_DEFAULT, ge=1, le=_LIST_MAX),
    failed_only: bool = Query(
        default=False, description="true なら失敗(error・timeout)と拒否(deny)だけ。"
    ),
    db: Session = Depends(get_session),
    _user: CurrentUser = Depends(require_admin),
) -> ModInvocationListResponse:
    """直近の呼び出し(新しい順)。"""
    query = select(ModInvocation)
    if failed_only:
        query = query.where(ModInvocation.outcome.in_(("error", "timeout", "deny")))
    rows = (
        db.execute(
            query.order_by(ModInvocation.created_at.desc(), ModInvocation.id.desc()).limit(limit)
        )
        .scalars()
        .all()
    )
    return ModInvocationListResponse(
        items=[ModInvocationView.model_validate(row, from_attributes=True) for row in rows]
    )


@router.get(
    "/deliveries", response_model=ModDeliveryListResponse, operation_id="list_mod_deliveries"
)
def list_deliveries(
    status: Literal["queued", "delivering", "done", "failed"] | None = Query(
        default="failed", description="配達の状態で絞る。省くと失敗(failed)だけ。"
    ),
    limit: int = Query(default=_LIST_DEFAULT, ge=1, le=_LIST_MAX),
    db: Session = Depends(get_session),
    _user: CurrentUser = Depends(require_admin),
) -> ModDeliveryListResponse:
    """配達の一覧(新しい順)。既定は送れなかったもの。"""
    query = select(ModDelivery)
    if status is not None:
        query = query.where(ModDelivery.status == status)
    rows = (
        db.execute(
            query.order_by(ModDelivery.created_at.desc(), ModDelivery.id.desc()).limit(limit)
        )
        .scalars()
        .all()
    )
    return ModDeliveryListResponse(
        items=[ModDeliveryView.model_validate(row, from_attributes=True) for row in rows]
    )
