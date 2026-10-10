"""SD WebUI(A1111 互換の API)の設定の API(ADR-0038 6章)。

接続先の URL、Basic 認証の資格情報、接続テスト、一覧の読み直し、LoRA の一覧(8章)。実行そのもの
(`SdWebuiProvider`)はここでは扱わない。

資格情報の値は応答・ログ・エラーメッセージに一部も出さない(`credentials_set` だけ)。
接続先の `/sdapi/v1/options` の中身や、一覧の `filename`(フルパス)も返さない。LoRA は
name・alias・ベースモデル・トリガーの候補だけを返し、`path` と学習時のメタ情報は返さない。

画像の生成情報をフォームに読み込む(`POST /api/sdwebui/import-params`。9章)では、送られた画像を
**保存しない**。本文はメモリの上だけで読み(multipart も一時ファイルに書き出さない)、生成情報を
取り出したら捨てる。ファイルにも DB にもログにも残さず、Asset にもしない。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from app.auth.deps import require_admin, require_user
from app.auth.identity import CurrentUser
from app.config import Settings
from app.deps import (
    get_registry,
    get_runner,
    get_session,
    get_session_factory,
    get_settings,
    get_store,
)
from app.domain import sdwebui_connection as connection
from app.domain.assets import MAX_UPLOAD_BYTES
from app.domain.generation_meta import extract_generation_meta
from app.domain.models import Run, RunStatus
from app.domain.schemas import (
    SdWebuiConnectionRequest,
    SdWebuiConnectionTestRequest,
    SdWebuiConnectionTestResponse,
    SdWebuiCredentialsRequest,
    SdWebuiImportNote,
    SdWebuiImportParamsResponse,
    SdWebuiImportSource,
    SdWebuiImportUnapplied,
    SdWebuiLora,
    SdWebuiLorasResponse,
    SdWebuiStatusResponse,
)
from app.domain.storage import AssetStore
from app.domain.visibility import get_visible_asset
from app.i18n import t
from app.providers.registry import ProviderRegistry, is_loopback_url
from app.providers.sdwebui.client import Availability, Credentials, SdWebuiClient, SdWebuiError
from app.providers.sdwebui.import_params import build_form_values
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


# -- 画像の生成情報をフォームに読み込む(ADR-0038 9章) ---------------------------------------

# multipart の境界やヘッダーの分の余裕(`app/api/uploads.py` と同じ)
_MULTIPART_OVERHEAD = 1024 * 1024
_BODY_LIMIT = MAX_UPLOAD_BYTES + _MULTIPART_OVERHEAD


class _InMemoryMultiPartParser(MultiPartParser):
    """ファイルの部分を一時ファイルに書き出さない multipart の解析。

    Starlette の既定は 1MB を超えたファイルをディスクの一時ファイルに移す(`SpooledTemporaryFile`)。
    本文全体の上限より大きくしておけば、移ることはなくメモリの上だけに置かれる。
    """

    spool_max_size = _BODY_LIMIT + 1


def _too_large() -> HTTPException:
    return HTTPException(
        status_code=413, detail=t("uploads.tooLarge", mb=MAX_UPLOAD_BYTES // (1024 * 1024))
    )


async def _read_body_in_memory(request: Request, limit: int) -> bytes:
    """本文をメモリに読む。`limit` 以上になった時点で読むのをやめて 413 にする。"""
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) >= limit:
        raise _too_large()
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total >= limit:
            raise _too_large()
        chunks.append(chunk)
    return b"".join(chunks)


async def _single_chunk(body: bytes) -> AsyncGenerator[bytes, None]:
    yield body


def _parse_asset_id(value: Any) -> uuid.UUID:
    if not isinstance(value, str):
        raise HTTPException(status_code=422, detail=t("sdwebui.importParams.inputMissing"))
    try:
        return uuid.UUID(value.strip())
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=t("sdwebui.importParams.invalidAssetId")
        ) from exc


async def _read_import_input(request: Request) -> tuple[bytes | None, uuid.UUID | None]:
    """本文から画像のバイト列か `asset_id` を取り出す(どちらか一方)。"""
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        body = await _read_body_in_memory(request, _BODY_LIMIT)
        parser = _InMemoryMultiPartParser(
            request.headers, _single_chunk(body), max_files=1, max_fields=10
        )
        try:
            form = await parser.parse()
        except MultiPartException as exc:
            raise HTTPException(
                status_code=422, detail=t("sdwebui.importParams.inputMissing")
            ) from exc
        finally:
            del body
        try:
            file = form.get("file")
            if isinstance(file, UploadFile):
                data = await file.read(MAX_UPLOAD_BYTES + 1)
                if len(data) >= MAX_UPLOAD_BYTES:
                    raise _too_large()
                return data, None
            asset_id = form.get("asset_id")
            if asset_id is not None:
                return None, _parse_asset_id(asset_id)
        finally:
            await form.close()
        raise HTTPException(status_code=422, detail=t("sdwebui.importParams.inputMissing"))

    body = await _read_body_in_memory(request, 64 * 1024)
    if content_type.startswith("application/x-www-form-urlencoded"):
        from urllib.parse import parse_qs

        values = parse_qs(body.decode("utf-8", errors="replace")).get("asset_id")
        return None, _parse_asset_id(values[0] if values else None)
    try:
        payload = json.loads(body) if body else None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=t("sdwebui.importParams.inputMissing")) from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail=t("sdwebui.importParams.inputMissing"))
    return None, _parse_asset_id(payload.get("asset_id"))


def _meta_from_asset(
    db: Session, store: AssetStore, user: CurrentUser, asset_id: uuid.UUID
) -> dict[str, Any] | None:
    """本人に見える Asset の生成情報。取り込み時に読んだもの(`embedded_meta`)があればそれを、
    無ければ原本から読む(GAKEI が SD WebUI で作った画像なども、WebUI の埋め込みを持つため)。"""
    asset = get_visible_asset(db, user, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail=t("assets.notFound"))
    if isinstance(asset.embedded_meta, dict) and asset.embedded_meta.get("tool"):
        return asset.embedded_meta
    content = store.open_content(asset.blob_key, asset.sha256, "original")
    if content is None:
        raise HTTPException(status_code=404, detail=t("assets.contentNotFound"))
    data = content.read_all()
    try:
        return extract_generation_meta(data)
    finally:
        del data


_IMPORT_OPENAPI: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "description": (
            "画像のファイル(multipart の `file`。保存しない)か、ストックの画像の `asset_id`"
            "(JSON か multipart のフィールド)。"
        ),
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "file": {"type": "string", "format": "binary"},
                        "asset_id": {"type": "string", "format": "uuid"},
                    },
                }
            },
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {"asset_id": {"type": "string", "format": "uuid"}},
                    "required": ["asset_id"],
                }
            },
        },
    }
}


@router.post(
    "/import-params",
    response_model=SdWebuiImportParamsResponse,
    operation_id="import_sdwebui_params",
    openapi_extra=_IMPORT_OPENAPI,
    responses={
        404: {"description": "Asset が無い、または本人に見えない"},
        409: {"description": "SD WebUI に接続していない、または一覧を取れない"},
        413: {"description": "画像が大きすぎる"},
        422: {"description": "A1111 形式の生成情報が無い、または本文が不正"},
    },
)
async def import_params(
    request: Request,
    db: Session = Depends(get_session),
    store: AssetStore = Depends(get_store),
    registry: ProviderRegistry = Depends(get_registry),
    user: CurrentUser = Depends(require_user),
) -> SdWebuiImportParamsResponse:
    """画像に埋め込まれた A1111 形式の生成情報から、SD WebUI の生成フォームの値を作る
    (ADR-0038 9章)。画像は保存しない(メモリの上で生成情報だけを読む)。"""
    provider = _registered(registry)
    if provider is None:
        raise HTTPException(status_code=409, detail=t("sdwebui.api.notConnected"))

    data, asset_id = await _read_import_input(request)
    if asset_id is not None:
        meta = await asyncio.to_thread(_meta_from_asset, db, store, user, asset_id)
    else:
        assert data is not None
        if not data:
            raise HTTPException(status_code=422, detail=t("sdwebui.importParams.inputMissing"))
        try:
            meta = await asyncio.to_thread(extract_generation_meta, data)
        finally:
            del data

    if meta is None:
        raise HTTPException(status_code=422, detail=t("sdwebui.importParams.noMetadata"))
    if meta.get("tool") != "a1111":
        raise HTTPException(status_code=422, detail=t("sdwebui.importParams.notA1111"))

    catalog = await asyncio.to_thread(provider.catalog)
    if catalog is None:
        raise HTTPException(status_code=409, detail=t("sdwebui.provider.unavailable"))

    result = build_form_values(meta, catalog)
    return SdWebuiImportParamsResponse(
        model=result.model,
        prompt=result.prompt,
        params=result.params,
        unapplied=[SdWebuiImportUnapplied(name=n, value=v) for n, v in result.unapplied],
        notes=[SdWebuiImportNote(code=n.code, message=n.message) for n in result.notes],
        source=SdWebuiImportSource(software=result.software),
    )
