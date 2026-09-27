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
from fastapi import APIRouter, Depends, HTTPException
from openai import AsyncOpenAI
from sqlalchemy.orm import Session

from app.auth.deps import require_admin, require_user
from app.auth.identity import CurrentUser
from app.config import Settings
from app.deps import get_provider, get_session, get_settings
from app.domain import api_key as api_key_domain
from app.domain import general_settings
from app.domain.schemas import (
    ComfyUITimeoutSetting,
    GeneralSettingsResponse,
    GeneralSettingsUpdateRequest,
    ModerationSetting,
    OpenAIBaseUrlStatusResponse,
    OpenAIBaseUrlUpdateRequest,
    OpenAIKeyStatusResponse,
    OpenAIKeyUpdateRequest,
)
from app.i18n import t
from app.providers.base import ImageProvider

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


def _status_response(
    settings: Settings, provider: ImageProvider, user: CurrentUser
) -> OpenAIKeyStatusResponse:
    api_key, source = api_key_domain.resolve_key(settings)
    return OpenAIKeyStatusResponse(
        required=getattr(provider, "requires_api_key", False),
        configured=api_key is not None,
        source=source,
        # 管理者以外には末尾4文字も見せない(ADR-0019。GET 自体は非管理者の画面のバナー等
        # からも呼ぶため許可し、この項目だけ絞る)。
        hint=api_key_domain.hint(api_key) if api_key and user.is_admin else None,
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
    user: CurrentUser = Depends(require_user),
) -> OpenAIKeyStatusResponse:
    return _status_response(settings, provider, user)


@router.put("/openai-key", response_model=OpenAIKeyStatusResponse, operation_id="set_openai_key")
async def set_openai_key(
    body: OpenAIKeyUpdateRequest,
    settings: Settings = Depends(get_settings),
    provider: ImageProvider = Depends(get_provider),
    validate_key: KeyValidator = Depends(get_key_validator),
    user: CurrentUser = Depends(require_admin),
) -> OpenAIKeyStatusResponse:
    api_key = body.api_key.strip()
    if not api_key:
        raise HTTPException(status_code=400, detail=t("openai.keyEmpty"))

    _reject_if_env_locked(settings)

    # キーの確認は、その時点で有効な Base URL に対して行う(ADR-0017 2章)。
    base_url, _base_url_source = api_key_domain.resolve_base_url(settings)
    await validate_key(api_key, base_url)

    api_key_domain.write_file_key(settings.data_dir, api_key)
    return _status_response(settings, provider, user)


@router.delete(
    "/openai-key", response_model=OpenAIKeyStatusResponse, operation_id="delete_openai_key"
)
def delete_openai_key(
    settings: Settings = Depends(get_settings),
    provider: ImageProvider = Depends(get_provider),
    user: CurrentUser = Depends(require_admin),
) -> OpenAIKeyStatusResponse:
    _reject_if_env_locked(settings)
    api_key_domain.delete_file_key(settings.data_dir)
    return _status_response(settings, provider, user)


# -- OpenAI の接続先(Base URL。ADR-0017) --------------------------------------
# キーと同じ扱い: 環境変数 / `.env` が画面で保存した値より優先し、環境変数が有効な間は
# 画面から変更・削除できない。値は秘密ではないので全文を返す(キーの `hint` に相当する
# 省略はしない)。


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
