"""SD WebUI(A1111 互換の API)の設定の API(ADR-0038 6章)。

接続先の URL、Basic 認証の資格情報、接続テスト、一覧の読み直し、LoRA の一覧(8章)。実行そのもの
(`SdWebuiProvider`)はここでは扱わない。

資格情報の値は応答・ログ・エラーメッセージに一部も出さない(`credentials_set` だけ)。
接続先の `/sdapi/v1/options` の中身や、一覧の `filename`(フルパス)も返さない。LoRA は
name・alias・ベースモデル・トリガーの候補だけを返し、`path` と学習時のメタ情報は返さない。
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.auth.deps import require_admin
from app.config import Settings
from app.deps import get_registry, get_runner, get_session, get_session_factory, get_settings
from app.domain import sdwebui_connection as connection
from app.domain.models import Run, RunStatus
from app.domain.schemas import (
    SdWebuiConnectionRequest,
    SdWebuiConnectionTestRequest,
    SdWebuiConnectionTestResponse,
    SdWebuiCredentialsRequest,
    SdWebuiLora,
    SdWebuiLorasResponse,
    SdWebuiStatusResponse,
)
from app.i18n import t
from app.providers.registry import ProviderRegistry, is_loopback_url
from app.providers.sdwebui.client import Availability, Credentials, SdWebuiClient, SdWebuiError
from app.providers.sdwebui.provider import PROVIDER_NAME, SdWebuiProvider
from app.worker.runner import Runner

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sdwebui", tags=["sdwebui"])


def _make_client(url: str, credentials: Credentials | None) -> SdWebuiClient:
    """テストで偽の WebUI を注入するための差し替え口。"""
    return SdWebuiClient(url, credentials=credentials)


def _make_provider(url: str, settings: Settings, session_factory: sessionmaker) -> SdWebuiProvider:
    """テストで偽の WebUI を注入するための差し替え口。"""
    return SdWebuiProvider.from_settings(url, settings, session_factory)


def _is_locked(db: Session) -> bool:
    """SD WebUI の Run が `queued` か `running` の間は設定を変えさせない(ADR-0038 6章)。"""
    return (
        db.execute(
            select(Run.id)
            .where(
                Run.provider == PROVIDER_NAME,
                Run.status.in_((RunStatus.QUEUED, RunStatus.RUNNING)),
            )
            .limit(1)
        ).first()
        is not None
    )


def _reject_if_locked(db: Session, message: str) -> None:
    if _is_locked(db):
        raise HTTPException(status_code=409, detail=message)


def _registered(registry: ProviderRegistry) -> SdWebuiProvider | None:
    provider = registry.get(PROVIDER_NAME)
    return provider if isinstance(provider, SdWebuiProvider) else None


def _probe(
    client: SdWebuiClient, *, timeout: float, provider: SdWebuiProvider | None = None
) -> tuple[Availability, int | None]:
    """到達性と、チェックポイントの数。登録中のプロバイダーが同じ接続先なら、その一覧の
    キャッシュを使う。"""
    availability = client.check_available(timeout=timeout)
    if not availability.available:
        return availability, None
    if provider is not None and provider.base_url == client.base_url:
        catalog = provider.catalog()
    else:
        try:
            catalog = client.fetch_catalog()
        except SdWebuiError:
            catalog = None
    return availability, (len(catalog.checkpoints) if catalog is not None else None)


def _build_status(
    db: Session, settings: Settings, registry: ProviderRegistry
) -> SdWebuiStatusResponse:
    url, source = connection.resolve_effective_url(db, settings)
    locked = _is_locked(db)
    credentials = connection.read_credentials(settings.data_dir)
    if url is None:
        return SdWebuiStatusResponse(
            url=None,
            enabled=False,
            available=False,
            source=source,
            locked=locked,
            loopback=None,
            credentials_set=credentials is not None,
        )
    availability, count = _probe(
        _make_client(url, credentials), timeout=1.0, provider=_registered(registry)
    )
    return SdWebuiStatusResponse(
        url=url,
        enabled=True,
        available=availability.available,
        reason=availability.reason,  # type: ignore[arg-type]
        reason_message=availability.message,
        source=source,
        locked=locked,
        loopback=is_loopback_url(url),
        credentials_set=credentials is not None,
        flavor=availability.flavor,
        checkpoint_count=count,
    )


@router.get("/status", response_model=SdWebuiStatusResponse, operation_id="get_sdwebui_status")
def get_status(
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    registry: ProviderRegistry = Depends(get_registry),
) -> SdWebuiStatusResponse:
    return _build_status(db, settings, registry)


@router.post(
    "/connection/test",
    response_model=SdWebuiConnectionTestResponse,
    operation_id="test_sdwebui_connection",
    dependencies=[Depends(require_admin)],
)
def test_connection(
    body: SdWebuiConnectionTestRequest,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> SdWebuiConnectionTestResponse:
    """入力中の URL(省略時は有効な URL)への接続を試す。設定は変えない。

    保存時と違い、ループバック以外でも確認チェックなしで試せる(送るのは一覧の問い合わせ
    だけで、画像やプロンプトは送らないため)。
    """
    url = body.url
    if url is None:
        url, _source = connection.resolve_effective_url(db, settings)
    if url is None:
        raise HTTPException(status_code=422, detail=t("sdwebui.api.connectionUrlMissing"))
    try:
        url = connection.normalize_connection_url(url, allow_non_loopback=True)
    except connection.SdWebuiConnectionValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if body.username is not None or body.password is not None:
        if body.username is None or body.password is None:
            raise HTTPException(status_code=422, detail=t("sdwebui.credentials.bothRequired"))
        try:
            connection.validate_credentials(body.username, body.password)
        except connection.SdWebuiConnectionValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        credentials: Credentials | None = (body.username, body.password)
    else:
        credentials = connection.read_credentials(settings.data_dir)

    availability, count = _probe(_make_client(url, credentials), timeout=3.0)
    return SdWebuiConnectionTestResponse(
        url=url,
        available=availability.available,
        reason=availability.reason,  # type: ignore[arg-type]
        reason_message=availability.message,
        loopback=is_loopback_url(url),
        flavor=availability.flavor,
        checkpoint_count=count,
    )


@router.put(
    "/connection",
    response_model=SdWebuiStatusResponse,
    operation_id="set_sdwebui_connection",
    dependencies=[Depends(require_admin)],
)
async def set_connection(
    body: SdWebuiConnectionRequest,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    registry: ProviderRegistry = Depends(get_registry),
    runner: Runner = Depends(get_runner),
    session_factory: sessionmaker = Depends(get_session_factory),
) -> SdWebuiStatusResponse:
    """接続・URL の変更。再起動なしで登録簿と実行レーンに反映する。

    `runner.ensure_lane` が `asyncio.create_task` を呼ぶため、async にしている
    (`app/api/comfyui.py` の `set_connection` と同じ理由)。
    """
    _reject_if_locked(db, t("sdwebui.api.lockedChangeConnection"))
    try:
        url = connection.normalize_connection_url(
            body.url, allow_non_loopback=body.allow_non_loopback
        )
    except connection.SdWebuiConnectionValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if not is_loopback_url(url):
        logger.warning(
            "SD WebUI の接続先にループバック以外のアドレスが設定されました(%s)。"
            "入力画像とプロンプトをネットワーク越しに送信することになります。",
            url,
        )

    connection.save_connection_url(db, url)
    registry.set_provider(PROVIDER_NAME, _make_provider(url, settings, session_factory))
    runner.ensure_lane(PROVIDER_NAME)

    return await asyncio.to_thread(_build_status, db, settings, registry)


@router.delete(
    "/connection",
    response_model=SdWebuiStatusResponse,
    operation_id="detach_sdwebui",
    dependencies=[Depends(require_admin)],
)
def detach_connection(
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    registry: ProviderRegistry = Depends(get_registry),
) -> SdWebuiStatusResponse:
    """切り離す。過去の Run と資格情報は消さない。"""
    _reject_if_locked(db, t("sdwebui.api.lockedDetach"))
    connection.save_detached(db)
    registry.set_provider(PROVIDER_NAME, None)
    return _build_status(db, settings, registry)


@router.put(
    "/credentials",
    response_model=SdWebuiStatusResponse,
    operation_id="set_sdwebui_credentials",
    dependencies=[Depends(require_admin)],
)
def set_credentials(
    body: SdWebuiCredentialsRequest,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    registry: ProviderRegistry = Depends(get_registry),
) -> SdWebuiStatusResponse:
    """Basic 認証の資格情報を `secrets.json` に保存する。値は返さない。"""
    _reject_if_locked(db, t("sdwebui.api.lockedChangeCredentials"))
    try:
        connection.save_credentials(settings.data_dir, body.username, body.password)
    except connection.SdWebuiConnectionValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    provider = _registered(registry)
    if provider is not None:
        provider.invalidate_cache()
    return _build_status(db, settings, registry)


@router.delete(
    "/credentials",
    response_model=SdWebuiStatusResponse,
    operation_id="delete_sdwebui_credentials",
    dependencies=[Depends(require_admin)],
)
def delete_credentials(
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    registry: ProviderRegistry = Depends(get_registry),
) -> SdWebuiStatusResponse:
    _reject_if_locked(db, t("sdwebui.api.lockedChangeCredentials"))
    connection.delete_credentials(settings.data_dir)
    provider = _registered(registry)
    if provider is not None:
        provider.invalidate_cache()
    return _build_status(db, settings, registry)


@router.post(
    "/refresh",
    response_model=SdWebuiStatusResponse,
    operation_id="refresh_sdwebui",
    dependencies=[Depends(require_admin)],
)
def refresh(
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    registry: ProviderRegistry = Depends(get_registry),
) -> SdWebuiStatusResponse:
    """WebUI にチェックポイントと LoRA の一覧を読み直させ、GAKEI の一覧のキャッシュを捨てる。"""
    _reject_if_locked(db, t("sdwebui.api.lockedRefresh"))
    url, _source = connection.resolve_effective_url(db, settings)
    if url is None:
        raise HTTPException(status_code=409, detail=t("sdwebui.api.notConnected"))
    client = _make_client(url, connection.read_credentials(settings.data_dir))
    try:
        client.refresh_checkpoints()
        client.refresh_loras()
    except SdWebuiError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    provider = _registered(registry)
    if provider is not None:
        provider.invalidate_cache()
    return _build_status(db, settings, registry)


@router.get("/loras", response_model=SdWebuiLorasResponse, operation_id="list_sdwebui_loras")
def list_loras(registry: ProviderRegistry = Depends(get_registry)) -> SdWebuiLorasResponse:
    """接続先の LoRA の一覧(ADR-0038 8章)。プロンプトに `<lora:name:重み>` を入れる補助に使う。

    接続していない(切り離した・未設定)ときと、接続先から一覧を取れないときは 409。
    LoRA の機能が無い接続先では空の一覧。
    """
    provider = _registered(registry)
    if provider is None:
        raise HTTPException(status_code=409, detail=t("sdwebui.api.notConnected"))
    try:
        loras = provider.loras()
    except SdWebuiError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    return SdWebuiLorasResponse(
        items=[
            SdWebuiLora(
                name=lora.name,
                alias=lora.alias,
                base_model=lora.base_model,
                trigger_tags=list(lora.trigger_tags),
            )
            for lora in loras
        ]
    )
