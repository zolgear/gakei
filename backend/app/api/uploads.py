"""`PUT /api/uploads/{token}`: 1回限りのアップロード URL の受け口(ADR-0023 7章 2)。

URL は MCP の `create_upload_url` が発行する。Cookie やアクセストークンによる認証は行わず、
URL に含むトークン自体を認可とする(そのため `main.py` の `require_user` の括りに入れない)。
MCP が無効のときは 404。

本文は画像ファイルのバイト列そのもの(`curl --data-binary @image.png`)。`multipart/form-data`
(`curl -F file=@image.png`)も受ける。取り込みは `POST /api/assets` の `kind=upload` と同じ
`ingest_upload` で、大きさの上限と画像の検証も同じ。作成者は URL を発行した利用者。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from app.auth.identity import LOCAL_ADMIN, CurrentUser
from app.config import Settings
from app.deps import get_session, get_settings, get_store
from app.domain import mcp_settings
from app.domain import upload_tickets as upload_tickets_domain
from app.domain.assets import MAX_UPLOAD_BYTES, IngestError, ingest_upload
from app.domain.models import AppUser, AssetKind
from app.domain.storage import AssetStore
from app.i18n import t

router = APIRouter(tags=["uploads"])

# multipart の包み(境界やヘッダー)の分の余裕。
_MULTIPART_OVERHEAD = 1024 * 1024


class UploadByUrlResponse(BaseModel):
    asset_id: str
    kind: str
    mime: str
    width: int
    height: int
    bytes: int
    sha256: str
    ingest_outcome: str
    # 原本(`GET /api/assets/{id}/content?variant=original`)と、画面のビューアの URL。
    url: str
    viewer_url: str


def _ticket_viewer(
    db: Session, user_id: uuid.UUID | None, settings: Settings
) -> CurrentUser | None:
    """URL を発行した利用者として取り込むための `CurrentUser`(ADR-0025 の重複判定に使う)。

    個人モード(発行者が null)は `LOCAL_ADMIN`。認証モードは `app_user` から作り、ロールは
    ログイン時と同じく現在の `AUTH_ADMIN_EMAILS` で決める。発行者が見つからなければ None。
    """
    if user_id is None:
        return LOCAL_ADMIN if settings.auth_mode == "none" else None
    app_user = db.get(AppUser, user_id)
    if app_user is None:
        return None
    email = (app_user.email or "").strip().lower()
    return CurrentUser(
        id=app_user.id,
        name=app_user.name,
        email=app_user.email,
        role="admin" if email in settings.admin_email_set() else "user",
        avatar_sha256=app_user.avatar_sha256,
    )


def _too_large() -> HTTPException:
    return HTTPException(
        status_code=413, detail=t("uploads.tooLarge", mb=MAX_UPLOAD_BYTES // (1024 * 1024))
    )


async def _read_body(request: Request) -> bytes:
    """本文を読む。`MAX_UPLOAD_BYTES` 以上になった時点で読むのをやめて 413 にする。"""
    content_type = request.headers.get("content-type", "")
    length = request.headers.get("content-length")
    if content_type.startswith("multipart/form-data"):
        if length and length.isdigit() and int(length) >= MAX_UPLOAD_BYTES + _MULTIPART_OVERHEAD:
            raise _too_large()
        form = await request.form()
        for value in form.values():
            if isinstance(value, UploadFile):
                return await value.read()
        raise HTTPException(status_code=422, detail=t("uploads.noFile"))

    if length and length.isdigit() and int(length) >= MAX_UPLOAD_BYTES:
        raise _too_large()
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total >= MAX_UPLOAD_BYTES:
            raise _too_large()
        chunks.append(chunk)
    return b"".join(chunks)


_OPENAPI_BODY = {
    "requestBody": {
        "required": True,
        "description": (
            "画像ファイル(PNG / JPEG / WebP)のバイト列そのもの。multipart/form-data も可。"
        ),
        "content": {
            "application/octet-stream": {"schema": {"type": "string", "format": "binary"}},
            "image/png": {"schema": {"type": "string", "format": "binary"}},
        },
    }
}


async def _upload(
    token: str,
    request: Request,
    db: Session,
    store: AssetStore,
    settings: Settings,
) -> UploadByUrlResponse:
    def _is_enabled() -> bool:
        try:
            return mcp_settings.is_enabled(db)
        finally:
            # 本文を読む間、読み取りのトランザクションを開いたままにしない。
            db.rollback()

    if not await run_in_threadpool(_is_enabled):
        raise HTTPException(status_code=404, detail=t("mcp.disabled"))

    data = await _read_body(request)
    if not data:
        raise HTTPException(status_code=422, detail=t("uploads.empty"))

    def _ingest() -> UploadByUrlResponse:
        claim = upload_tickets_domain.claim_ticket(db, token)
        if claim.ticket is None:
            db.rollback()
            if claim.failure == "gone":
                raise HTTPException(status_code=410, detail=t("uploads.gone"))
            raise HTTPException(status_code=404, detail=t("uploads.notFound"))
        ticket = claim.ticket
        viewer = _ticket_viewer(db, ticket.user_id, settings)
        if viewer is None:
            db.rollback()
            raise HTTPException(status_code=404, detail=t("uploads.notFound"))
        try:
            result = ingest_upload(db, store, data, AssetKind.UPLOAD, viewer=viewer)
        except IngestError as e:
            # 取り込めなかったときはチケットを使用済みにしない(期限内なら送り直せる)。
            db.rollback()
            raise HTTPException(status_code=422, detail=str(e)) from e
        ticket.asset_id = result.asset.id
        db.commit()
        asset = result.asset
        base = mcp_settings.resolve_public_base(settings.public_base_url, str(request.base_url))
        return UploadByUrlResponse(
            asset_id=str(asset.id),
            kind=str(asset.kind),
            mime=asset.mime,
            width=asset.width,
            height=asset.height,
            bytes=asset.bytes,
            sha256=asset.sha256,
            ingest_outcome=result.outcome,
            url=f"{base}/api/assets/{asset.id}/content?variant=original",
            viewer_url=f"{base}/assets/{asset.id}",
        )

    return await run_in_threadpool(_ingest)


@router.put(
    "/api/uploads/{token}",
    response_model=UploadByUrlResponse,
    status_code=201,
    operation_id="upload_by_url",
    openapi_extra=_OPENAPI_BODY,
)
async def upload_by_url(
    token: str,
    request: Request,
    db: Session = Depends(get_session),
    store: AssetStore = Depends(get_store),
    settings: Settings = Depends(get_settings),
) -> UploadByUrlResponse:
    """MCP の `create_upload_url` で発行した URL に画像を送り、ストックに取り込む。
    URL は10分間・1回限り有効。不明な URL は 404、使用済み・期限切れは 410。"""
    return await _upload(token, request, db, store, settings)


@router.post(
    "/api/uploads/{token}",
    response_model=UploadByUrlResponse,
    status_code=201,
    operation_id="upload_by_url_post",
    openapi_extra=_OPENAPI_BODY,
)
async def upload_by_url_post(
    token: str,
    request: Request,
    db: Session = Depends(get_session),
    store: AssetStore = Depends(get_store),
    settings: Settings = Depends(get_settings),
) -> UploadByUrlResponse:
    """`PUT` と同じ(PUT を送りにくいクライアント向け。`curl -F file=@image.png` など)。"""
    return await _upload(token, request, db, store, settings)
