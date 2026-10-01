"""画面から OpenAI の API キーと接続先(Base URL)を設定する(ADR-0012 Decision 4、ADR-0017)。

優先順位はキー・Base URL とも `app.domain.api_key` の `resolve_key` / `resolve_base_url` に
従う: 環境変数 / `.env` が、画面で保存したファイルの値より優先する。環境変数が有効な間は
画面から変更・削除できない。

保存前に OpenAI の無料 API(モデル一覧)でキーが有効かどうかを確かめる。キーを保存するときは
その時点で有効な Base URL に対して、Base URL を保存するときは有効なキーがあればそのキーで
確認する(キーが無ければ形式の確認だけで保存する。ADR-0017 2章)。実 API を呼ぶ処理は
`get_key_validator` の Depends 経由にしてあるので、テストでは差し替えて実 API を呼ばずに済む。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import openai
from fastapi import APIRouter, Depends, HTTPException, Request
from openai import AsyncOpenAI
from sqlalchemy.orm import Session

from app.annotation.wd_models import WD_MODELS, WdModelDownloader, is_downloaded
from app.auth.deps import require_admin, require_user
from app.auth.identity import CurrentUser
from app.config import Settings
from app.deps import (
    get_annotator,
    get_provider,
    get_session,
    get_settings,
    get_wd_downloader,
)
from app.domain import (
    annotation_settings,
    general_settings,
    mcp_settings,
    share_settings,
)
from app.domain import annotations as annotations_domain
from app.domain import api_key as api_key_domain
from app.domain.schemas import (
    AnnotationBackfillResponse,
    AnnotationComfyuiProfile,
    AnnotationConnectionCalls,
    AnnotationDefaultProfile,
    AnnotationProfiles,
    AnnotationSettingsResponse,
    AnnotationSettingsUpdateRequest,
    AnnotationTarget,
    ComfyUITimeoutSetting,
    GeneralSettingsResponse,
    GeneralSettingsUpdateRequest,
    McpSettingsResponse,
    McpSettingsUpdateRequest,
    ModerationSetting,
    OnnxDownloadRequest,
    OnnxModelStatus,
    OpenAIBaseUrlStatusResponse,
    OpenAIBaseUrlUpdateRequest,
    OpenAIKeyStatusResponse,
    OpenAIKeyUpdateRequest,
    ShareSettingsResponse,
    ShareSettingsUpdateRequest,
)
from app.i18n import t
from app.providers.base import ImageProvider
from app.worker.annotator import Annotator

router = APIRouter(prefix="/api/settings", tags=["settings"])

# 有効性確認の1回のタイムアウト(秒)。画面の操作を長く止めないよう、実行本体の
# タイムアウト(Settings.openai_timeout_seconds、既定600秒)より短くする。
_VALIDATE_TIMEOUT_SECONDS = 15.0

KeyValidator = Callable[[str, str | None], Awaitable[None]]


async def _validate_key_live(api_key: str, base_url: str | None) -> None:
    """OpenAI 互換の無料 API(モデル一覧)を1回呼び、キーが有効かどうかを確かめる。

    `base_url` はキーの確認先(ADR-0017: キー保存時はその時点の有効な Base URL、Base URL
    保存時はその時点の有効なキー)。無効なキー(401) は 400、それ以外の API エラー
    (ネットワーク・レート制限等)は 502 として呼び出し側に伝える。いずれの場合もキー・
    Base URL は保存しない(呼び出し元で保存前にこの関数を呼ぶ)。
    """
    client = AsyncOpenAI(
        api_key=api_key, base_url=base_url, max_retries=0, timeout=_VALIDATE_TIMEOUT_SECONDS
    )
    try:
        await client.models.list()
    except openai.AuthenticationError as e:
        raise HTTPException(status_code=400, detail=t("openai.keyInvalid")) from e
    except openai.PermissionDeniedError:
        # 403 は「認証は通ったが、この操作の権限がない」。画像の権限だけを付けた制限付きキー
        # (Missing scopes: api.model.read)がこれに当たる。キー自体は有効なので保存する。
        # 画像の権限があるかは、課金なしでは確かめられないので実行時に分かる。
        return
    except openai.APIError as e:
        raise HTTPException(
            status_code=502,
            detail=t("openai.keyVerifyFailed", detail=_safe_message(e)),
        ) from e


def _safe_message(e: openai.APIError) -> str:
    label = e.code or e.type or e.__class__.__name__
    detail = getattr(e, "message", None) or str(e)
    return f"{label}: {detail}"


def get_key_validator() -> KeyValidator:
    """テストではこの Depends を差し替えて、実 API を呼ばないようにする。"""
    return _validate_key_live


def _status_response(settings: Settings, provider: ImageProvider) -> OpenAIKeyStatusResponse:
    # キーは一部も返さない。設定済みかどうかと出どころだけ。
    api_key, source = api_key_domain.resolve_key(settings)
    return OpenAIKeyStatusResponse(
        required=getattr(provider, "requires_api_key", False),
        configured=api_key is not None,
        source=source,
    )


def _reject_if_env_locked(settings: Settings) -> None:
    _, source = api_key_domain.resolve_key(settings)
    if source == "env":
        raise HTTPException(
            status_code=409,
            detail=t("openai.envKeyLocked"),
        )


@router.get(
    "/openai-key", response_model=OpenAIKeyStatusResponse, operation_id="get_openai_key_status"
)
def get_openai_key(
    settings: Settings = Depends(get_settings),
    provider: ImageProvider = Depends(get_provider),
    _user: CurrentUser = Depends(require_user),
) -> OpenAIKeyStatusResponse:
    return _status_response(settings, provider)


@router.put("/openai-key", response_model=OpenAIKeyStatusResponse, operation_id="set_openai_key")
async def set_openai_key(
    body: OpenAIKeyUpdateRequest,
    settings: Settings = Depends(get_settings),
    provider: ImageProvider = Depends(get_provider),
    validate_key: KeyValidator = Depends(get_key_validator),
    _user: CurrentUser = Depends(require_admin),
) -> OpenAIKeyStatusResponse:
    api_key = body.api_key.strip()
    if not api_key:
        raise HTTPException(status_code=400, detail=t("openai.keyEmpty"))

    _reject_if_env_locked(settings)

    # キーの確認は、その時点で有効な Base URL に対して行う(ADR-0017 2章)。
    base_url, _base_url_source = api_key_domain.resolve_base_url(settings)
    await validate_key(api_key, base_url)

    api_key_domain.write_file_key(settings.data_dir, api_key)
    return _status_response(settings, provider)


@router.delete(
    "/openai-key", response_model=OpenAIKeyStatusResponse, operation_id="delete_openai_key"
)
def delete_openai_key(
    settings: Settings = Depends(get_settings),
    provider: ImageProvider = Depends(get_provider),
    _user: CurrentUser = Depends(require_admin),
) -> OpenAIKeyStatusResponse:
    _reject_if_env_locked(settings)
    api_key_domain.delete_file_key(settings.data_dir)
    return _status_response(settings, provider)


# -- OpenAI の接続先(Base URL。ADR-0017) --------------------------------------
# キーと同じ扱い: 環境変数 / `.env` が画面で保存した値より優先し、環境変数が有効な間は
# 画面から変更・削除できない。値は秘密ではないので全文を返す(キーは一部も返さない)。


def _base_url_status_response(settings: Settings) -> OpenAIBaseUrlStatusResponse:
    base_url, source = api_key_domain.resolve_base_url(settings)
    return OpenAIBaseUrlStatusResponse(value=base_url, source=source)


def _reject_if_base_url_env_locked(settings: Settings) -> None:
    _, source = api_key_domain.resolve_base_url(settings)
    if source == "env":
        raise HTTPException(status_code=409, detail=t("openai.baseUrl.envLocked"))


@router.get(
    "/openai-base-url",
    response_model=OpenAIBaseUrlStatusResponse,
    operation_id="get_openai_base_url",
)
def get_openai_base_url(
    settings: Settings = Depends(get_settings),
) -> OpenAIBaseUrlStatusResponse:
    return _base_url_status_response(settings)


@router.put(
    "/openai-base-url",
    response_model=OpenAIBaseUrlStatusResponse,
    operation_id="set_openai_base_url",
)
async def set_openai_base_url(
    body: OpenAIBaseUrlUpdateRequest,
    settings: Settings = Depends(get_settings),
    validate_key: KeyValidator = Depends(get_key_validator),
    _user: CurrentUser = Depends(require_admin),
) -> OpenAIBaseUrlStatusResponse:
    raw = body.base_url.strip()
    if not raw:
        raise HTTPException(status_code=400, detail=t("openai.baseUrl.empty"))

    _reject_if_base_url_env_locked(settings)

    try:
        normalized = api_key_domain.normalize_base_url(raw)
    except api_key_domain.BaseUrlValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    api_key_domain.warn_if_insecure_base_url(normalized)

    # 有効なキーがあれば、その URL に対してキーの確認を行う。キーが無ければ形式の確認だけで
    # 保存する(ADR-0017 2章)。
    api_key, _key_source = api_key_domain.resolve_key(settings)
    if api_key:
        await validate_key(api_key, normalized)

    api_key_domain.write_file_base_url(settings.data_dir, normalized)
    return _base_url_status_response(settings)


@router.delete(
    "/openai-base-url",
    response_model=OpenAIBaseUrlStatusResponse,
    operation_id="delete_openai_base_url",
)
def delete_openai_base_url(
    settings: Settings = Depends(get_settings),
    _user: CurrentUser = Depends(require_admin),
) -> OpenAIBaseUrlStatusResponse:
    _reject_if_base_url_env_locked(settings)
    api_key_domain.delete_file_base_url(settings.data_dir)
    return _base_url_status_response(settings)


# -- 全般設定(ADR-0009、ADR-0013 7章) ------------------------------------------
# moderation(Generate 専用)と ComfyUI のタイムアウトを画面から変える。


def _general_settings_response(db: Session, settings: Settings) -> GeneralSettingsResponse:
    moderation_value, moderation_source = general_settings.resolve_moderation(db, settings)
    timeout_value, timeout_source = general_settings.resolve_timeout_seconds(db, settings)
    return GeneralSettingsResponse(
        moderation=ModerationSetting(
            value=moderation_value,
            source=moderation_source,
            default=general_settings.default_moderation(settings),
        ),
        comfyui_timeout_seconds=ComfyUITimeoutSetting(
            value=timeout_value,
            source=timeout_source,
            default=general_settings.default_timeout_seconds(settings),
        ),
    )


@router.get("/general", response_model=GeneralSettingsResponse, operation_id="get_general_settings")
def get_general_settings(
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> GeneralSettingsResponse:
    return _general_settings_response(db, settings)


@router.patch(
    "/general", response_model=GeneralSettingsResponse, operation_id="update_general_settings"
)
def update_general_settings(
    body: GeneralSettingsUpdateRequest,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    _user: CurrentUser = Depends(require_admin),
) -> GeneralSettingsResponse:
    fields_set = body.model_fields_set
    try:
        # 先に全項目を検証してから保存する。片方だけ通って保存される事態を避けるため。
        if "moderation" in fields_set:
            general_settings.validate_moderation(body.moderation)
        if "comfyui_timeout_seconds" in fields_set:
            general_settings.validate_timeout_seconds(body.comfyui_timeout_seconds)
    except general_settings.GeneralSettingsValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if "moderation" in fields_set:
        general_settings.save_moderation(db, body.moderation)
    if "comfyui_timeout_seconds" in fields_set:
        general_settings.save_timeout_seconds(db, body.comfyui_timeout_seconds)
    return _general_settings_response(db, settings)


# -- MCP サーバー(ADR-0023) ----------------------------------------------------
# 有効/無効(既定は無効)と、MCP 経由で作る Run の上限(1時間あたり)。GET は全ログイン者
# (画面の表示用)、更新は管理者だけ。


def _mcp_settings_response(
    db: Session, settings: Settings, request: Request
) -> McpSettingsResponse:
    base = mcp_settings.resolve_public_base(settings.public_base_url, str(request.base_url))
    return McpSettingsResponse(
        enabled=mcp_settings.is_enabled(db),
        hourly_run_limit=mcp_settings.hourly_run_limit(db),
        hourly_run_limit_default=mcp_settings.DEFAULT_HOURLY_RUN_LIMIT,
        hourly_run_limit_max=mcp_settings.HOURLY_RUN_LIMIT_MAX,
        runs_last_hour=mcp_settings.count_recent_mcp_runs(db),
        endpoint_url=base + mcp_settings.MCP_PATH,
    )


@router.get("/mcp", response_model=McpSettingsResponse, operation_id="get_mcp_settings")
def get_mcp_settings(
    request: Request,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> McpSettingsResponse:
    return _mcp_settings_response(db, settings, request)


@router.patch("/mcp", response_model=McpSettingsResponse, operation_id="update_mcp_settings")
def update_mcp_settings(
    body: McpSettingsUpdateRequest,
    request: Request,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    _user: CurrentUser = Depends(require_admin),
) -> McpSettingsResponse:
    fields_set = body.model_fields_set
    try:
        # 先に全項目を検証してから保存する(片方だけ保存される事態を避けるため)。
        if "enabled" in fields_set:
            mcp_settings.validate_enabled(body.enabled)
        if "hourly_run_limit" in fields_set:
            mcp_settings.validate_hourly_run_limit(body.hourly_run_limit)
    except mcp_settings.McpSettingsValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if "enabled" in fields_set:
        assert body.enabled is not None
        mcp_settings.save_enabled(db, body.enabled)
    if "hourly_run_limit" in fields_set:
        assert body.hourly_run_limit is not None
        mcp_settings.save_hourly_run_limit(db, body.hourly_run_limit)
    return _mcp_settings_response(db, settings, request)


# -- 共有リンク(ADR-0029) ----------------------------------------------------------
# 有効/無効(既定は無効)。GET は全ログイン者(共有の操作を画面に出すかの判断)、更新は管理者だけ。


@router.get("/share", response_model=ShareSettingsResponse, operation_id="get_share_settings")
def get_share_settings(db: Session = Depends(get_session)) -> ShareSettingsResponse:
    return ShareSettingsResponse(enabled=share_settings.is_enabled(db))


@router.patch("/share", response_model=ShareSettingsResponse, operation_id="update_share_settings")
def update_share_settings(
    body: ShareSettingsUpdateRequest,
    db: Session = Depends(get_session),
    _user: CurrentUser = Depends(require_admin),
) -> ShareSettingsResponse:
    if "enabled" in body.model_fields_set:
        try:
            share_settings.validate_enabled(body.enabled)
        except share_settings.ShareSettingsValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        assert body.enabled is not None
        share_settings.save_enabled(db, body.enabled)
    return ShareSettingsResponse(enabled=share_settings.is_enabled(db))


# -- 自動タイトル・タグ(ADR-0024) ------------------------------------------------
# GET は全ログイン者(画面の表示用)、更新・モデルのダウンロード・一括実行は管理者だけ。
# 接続先は LLM の接続先(`app/api/llm_connections.py`。ADR-0032)にある。


def _onnx_model_statuses(
    settings: Settings, downloader: WdModelDownloader
) -> list[OnnxModelStatus]:
    statuses: list[OnnxModelStatus] = []
    for name, model in WD_MODELS.items():
        state = downloader.state(name)
        statuses.append(
            OnnxModelStatus(
                name=name,  # type: ignore[arg-type]
                size_bytes=model.size_bytes,
                memory_bytes=model.memory_bytes,
                downloaded=is_downloaded(settings.data_dir, name),
                download_status=state.status,  # type: ignore[arg-type]
                download_progress=state.progress,
                download_error=state.error if state.status == "failed" else None,
            )
        )
    return statuses


def _connection_calls(
    config: annotation_settings.AnnotationConfig, annotator: Annotator
) -> list[AnnotationConnectionCalls]:
    return [
        AnnotationConnectionCalls(
            connection_id=connection.id,
            calls_last_hour=annotator.calls_last_hour(connection.id),
        )
        for connection in config.all_connections()
    ]


def _target_view(value: annotation_settings.TargetChoice | None) -> AnnotationTarget | None:
    if value is None:
        return None
    return AnnotationTarget(connection_id=value.connection_id, model=value.model)


def _profiles_view(profiles: annotation_settings.Profiles) -> AnnotationProfiles:
    return AnnotationProfiles(
        default=AnnotationDefaultProfile(
            llm=_target_view(profiles.default_llm),  # type: ignore[arg-type]
            vlm=_target_view(profiles.default_vlm),  # type: ignore[arg-type]
        ),
        comfyui=AnnotationComfyuiProfile(
            llm=_target_view(profiles.comfyui_llm),
            vlm=_target_view(profiles.comfyui_vlm),
        ),
    )


def _annotation_settings_response(
    db: Session,
    settings: Settings,
    annotator: Annotator,
    downloader: WdModelDownloader,
) -> AnnotationSettingsResponse:
    config = annotation_settings.load(db)
    return AnnotationSettingsResponse(
        **{name: getattr(config, name) for name in annotation_settings.SCALAR_FIELDS},
        profiles=_profiles_view(config.profiles),
        onnx_models=_onnx_model_statuses(settings, downloader),
        pending_count=annotations_domain.pending_count(db),
        queued_count=annotations_domain.queued_count(db),
        calls_last_hour=annotator.calls_last_hour(),
        connection_calls=_connection_calls(config, annotator),
        usable_engines=annotations_domain.usable_engines(config, settings),  # type: ignore[arg-type]
    )


@router.get(
    "/annotation",
    response_model=AnnotationSettingsResponse,
    operation_id="get_annotation_settings",
)
def get_annotation_settings(
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    annotator: Annotator = Depends(get_annotator),
    downloader: WdModelDownloader = Depends(get_wd_downloader),
    _user: CurrentUser = Depends(require_user),
) -> AnnotationSettingsResponse:
    return _annotation_settings_response(db, settings, annotator, downloader)


@router.patch(
    "/annotation",
    response_model=AnnotationSettingsResponse,
    operation_id="update_annotation_settings",
)
def update_annotation_settings(
    body: AnnotationSettingsUpdateRequest,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    annotator: Annotator = Depends(get_annotator),
    downloader: WdModelDownloader = Depends(get_wd_downloader),
    _user: CurrentUser = Depends(require_admin),
) -> AnnotationSettingsResponse:
    updates = {name: getattr(body, name) for name in body.model_fields_set if name != "profiles"}
    # null を「変更しない」ではなく不正な値として扱う(検証で 422)。組(`profiles`)は書いた
    # マスだけ変える。先に全部を検証してから保存する(一部だけ保存される事態を避ける)。
    try:
        for name, value in updates.items():
            annotation_settings.normalize_value(name, value)
        profiles = None
        if "profiles" in body.model_fields_set:
            if body.profiles is None:
                raise annotation_settings.AnnotationSettingsValidationError(
                    t("settings.annotation.invalidTarget")
                )
            profiles = annotation_settings.validate_profile_updates(
                annotation_settings.load(db),
                body.profiles.model_dump(exclude_unset=True),
            )
        annotation_settings.save(db, updates)
        if profiles is not None:
            annotation_settings.save_profiles(db, profiles)
    except annotation_settings.AnnotationSettingsValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    annotator.notify()
    return _annotation_settings_response(db, settings, annotator, downloader)


def _known_onnx_model(name: str) -> str:
    if name not in WD_MODELS:
        raise HTTPException(status_code=404, detail=t("settings.annotation.unknownOnnxModel"))
    return name


@router.post(
    "/annotation/onnx/download",
    response_model=AnnotationSettingsResponse,
    status_code=202,
    operation_id="download_onnx_model",
)
async def download_onnx_model(
    body: OnnxDownloadRequest,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    annotator: Annotator = Depends(get_annotator),
    downloader: WdModelDownloader = Depends(get_wd_downloader),
    _user: CurrentUser = Depends(require_admin),
) -> AnnotationSettingsResponse:
    """モデルを Hugging Face からバックグラウンドで取得する。進み具合は GET の `onnx_models`
    で見る。既にダウンロード中なら何もしない。"""
    name = _known_onnx_model(body.model)
    downloader.start(name)
    return _annotation_settings_response(db, settings, annotator, downloader)


@router.delete(
    "/annotation/onnx/{model}",
    response_model=AnnotationSettingsResponse,
    operation_id="delete_onnx_model",
)
def delete_onnx_model(
    model: str,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    annotator: Annotator = Depends(get_annotator),
    downloader: WdModelDownloader = Depends(get_wd_downloader),
    _user: CurrentUser = Depends(require_admin),
) -> AnnotationSettingsResponse:
    name = _known_onnx_model(model)
    if downloader.is_downloading(name):
        raise HTTPException(status_code=409, detail=t("settings.annotation.onnxDownloading"))
    downloader.delete(name)
    return _annotation_settings_response(db, settings, annotator, downloader)


@router.post(
    "/annotation/backfill",
    response_model=AnnotationBackfillResponse,
    operation_id="backfill_annotations",
)
def backfill_annotations(
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    annotator: Annotator = Depends(get_annotator),
    _user: CurrentUser = Depends(require_admin),
) -> AnnotationBackfillResponse:
    """一度も推定していない Asset(削除済み・マスクを除く)をまとめて待ち行列に入れる。
    使えるエンジンが無ければ 409。"""
    config = annotation_settings.load(db)
    if not annotations_domain.usable_engines(config, settings):
        raise HTTPException(status_code=409, detail=t("annotations.noEngine"))
    count = annotations_domain.backfill(db)
    db.commit()
    annotator.notify()
    return AnnotationBackfillResponse(queued=count)
