"""タグ辞書の管理の API(ADR-0041 1章)。管理者だけ。

- `GET /api/settings/tag-dictionaries`: 一覧と状態
- `POST /api/settings/tag-dictionaries`: CSV か zip を登録する(multipart の `file`、任意で
  `category_scheme`)。種類を判定して行を作り、取り込みはプロセス内のスレッドで行う(202)。
  zip に CSV が複数あれば、CSV ごとに辞書を作る。
- `PATCH /api/settings/tag-dictionaries/{id}`: 有効/無効、カテゴリーの体系
- `DELETE /api/settings/tag-dictionaries/{id}`: 物理削除(取り込み中は 409)

アップロードしたファイルは**保存しない**。本文はメモリの上だけで読み(multipart も一時ファイルに
書き出さない。`app/api/sdwebui.py` の import-params と同じ工夫)、zip もメモリの上で展開する。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session, sessionmaker
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from app.auth.deps import require_admin
from app.auth.identity import CurrentUser
from app.deps import get_session, get_session_factory
from app.domain import tag_dictionaries as dictionaries
from app.domain.models import TagDictionary
from app.domain.schemas import (
    TagDictionaryItem,
    TagDictionaryListResponse,
    TagDictionaryUpdateRequest,
)
from app.i18n import t
from app.worker.tag_dictionary_importer import TagDictionaryImporter

router = APIRouter(prefix="/api/settings/tag-dictionaries", tags=["settings"])

# multipart の境界やフィールドの分の余裕。
_MULTIPART_OVERHEAD = 64 * 1024
_BODY_LIMIT = dictionaries.UPLOAD_MAX_BYTES + _MULTIPART_OVERHEAD


class _InMemoryMultiPartParser(MultiPartParser):
    """ファイルの部分を一時ファイルに書き出さない multipart の解析(本文全体の上限より大きく
    しておけば、`SpooledTemporaryFile` がディスクに移ることはない)。"""

    spool_max_size = _BODY_LIMIT + 1


def get_importer(request: Request) -> TagDictionaryImporter:
    return request.app.state.tag_dictionary_importer


def _too_large() -> HTTPException:
    return HTTPException(
        status_code=413,
        detail=t("tagDictionaries.tooLarge", mb=dictionaries.UPLOAD_MAX_BYTES // (1024 * 1024)),
    )


async def _read_body_in_memory(request: Request, limit: int) -> bytes:
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


async def _read_upload(request: Request) -> tuple[str, bytes, str | None]:
    """multipart の本文から (ファイル名, 中身, カテゴリーの体系) を取り出す。"""
    content_type = request.headers.get("content-type", "")
    if not content_type.startswith("multipart/form-data"):
        raise HTTPException(status_code=422, detail=t("tagDictionaries.noFile"))
    body = await _read_body_in_memory(request, _BODY_LIMIT)
    parser = _InMemoryMultiPartParser(
        request.headers, _single_chunk(body), max_files=1, max_fields=10
    )
    try:
        form = await parser.parse()
    except MultiPartException as exc:
        raise HTTPException(status_code=422, detail=t("tagDictionaries.noFile")) from exc
    finally:
        del body
    try:
        scheme = form.get("category_scheme")
        if scheme is not None and (
            not isinstance(scheme, str) or scheme not in dictionaries.CATEGORY_SCHEMES
        ):
            raise HTTPException(status_code=422, detail=t("tagDictionaries.invalidScheme"))
        file = form.get("file")
        if not isinstance(file, UploadFile):
            raise HTTPException(status_code=422, detail=t("tagDictionaries.noFile"))
        data = await file.read(dictionaries.UPLOAD_MAX_BYTES + 1)
        if len(data) > dictionaries.UPLOAD_MAX_BYTES:
            raise _too_large()
        return file.filename or "dictionary.csv", data, scheme
    finally:
        await form.close()


def _to_item(row: TagDictionary) -> TagDictionaryItem:
    message = None
    if row.error:
        message = t(f"tagDictionaries.errors.{row.error}")
    return TagDictionaryItem(
        id=row.id,
        filename=row.filename,
        kind=row.kind,  # type: ignore[arg-type]
        category_scheme=row.category_scheme,
        row_count=row.row_count,
        enabled=row.enabled,
        status=row.status,  # type: ignore[arg-type]
        error_code=row.error,
        error_message=message,
        created_at=row.created_at,
        finished_at=row.finished_at,
    )


_UPLOAD_OPENAPI: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "description": "辞書の CSV か、CSV を含む zip(`file`。保存しない)と、カテゴリーの体系。",
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "file": {"type": "string", "format": "binary"},
                        "category_scheme": {
                            "type": "string",
                            "enum": list(dictionaries.CATEGORY_SCHEMES),
                        },
                    },
                    "required": ["file"],
                }
            }
        },
    }
}


@router.get("", response_model=TagDictionaryListResponse, operation_id="list_tag_dictionaries")
def list_tag_dictionaries(
    db: Session = Depends(get_session),
    _admin: CurrentUser = Depends(require_admin),
) -> TagDictionaryListResponse:
    return TagDictionaryListResponse(
        items=[_to_item(row) for row in dictionaries.list_dictionaries(db)],
        category_schemes=list(dictionaries.CATEGORY_SCHEMES),
    )


def _create_rows(
    session_factory: sessionmaker[Session],
    sources: list[dictionaries.DictionarySource],
    scheme: str,
    user_id: uuid.UUID | None,
) -> list[TagDictionary]:
    with session_factory() as session:
        rows = [
            dictionaries.create_dictionary(
                session,
                filename=source.filename,
                kind=source.kind,
                category_scheme=scheme,
                user_id=user_id,
            )
            for source in sources
        ]
        session.commit()
        return rows


@router.post(
    "",
    response_model=TagDictionaryListResponse,
    status_code=202,
    operation_id="upload_tag_dictionary",
    openapi_extra=_UPLOAD_OPENAPI,
    responses={
        413: {"description": "ファイル、または zip を展開した合計が大きすぎる"},
        422: {"description": "ファイルが無い、種類を判定できない、文字コードや zip が不正"},
    },
)
async def upload_tag_dictionary(
    request: Request,
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
    importer: TagDictionaryImporter = Depends(get_importer),
    admin: CurrentUser = Depends(require_admin),
) -> TagDictionaryListResponse:
    """種類を判定して辞書(取り込み中)を作り、取り込みを始める。応答は作った辞書。"""
    filename, data, scheme = await _read_upload(request)
    try:
        sources = await asyncio.to_thread(dictionaries.read_upload, filename, data)
    except dictionaries.DictionaryFileError as exc:
        status = 413 if exc.code in ("tooLarge", "extractedTooLarge") else 422
        raise HTTPException(
            status_code=status, detail=t(f"tagDictionaries.{exc.code}", **exc.params)
        ) from exc
    finally:
        del data
    rows = await asyncio.to_thread(
        _create_rows,
        session_factory,
        sources,
        scheme or dictionaries.DEFAULT_CATEGORY_SCHEME,
        admin.id,
    )
    for row, source in zip(rows, sources, strict=True):
        importer.submit(row.id, source)
    return TagDictionaryListResponse(
        items=[_to_item(row) for row in rows],
        category_schemes=list(dictionaries.CATEGORY_SCHEMES),
    )


def _get_or_404(db: Session, dictionary_id: uuid.UUID) -> TagDictionary:
    row = db.get(TagDictionary, dictionary_id)
    if row is None:
        raise HTTPException(status_code=404, detail=t("tagDictionaries.notFound"))
    return row


@router.patch(
    "/{dictionary_id}",
    response_model=TagDictionaryItem,
    operation_id="update_tag_dictionary",
    responses={404: {"description": "辞書が無い"}},
)
def update_tag_dictionary(
    dictionary_id: uuid.UUID,
    body: TagDictionaryUpdateRequest,
    db: Session = Depends(get_session),
    _admin: CurrentUser = Depends(require_admin),
) -> TagDictionaryItem:
    row = _get_or_404(db, dictionary_id)
    if body.enabled is not None:
        row.enabled = body.enabled
    if body.category_scheme is not None and row.kind == dictionaries.KIND_TAGS:
        row.category_scheme = body.category_scheme
    db.commit()
    return _to_item(row)


@router.delete(
    "/{dictionary_id}",
    status_code=204,
    operation_id="delete_tag_dictionary",
    responses={404: {"description": "辞書が無い"}, 409: {"description": "取り込み中"}},
)
def delete_tag_dictionary(
    dictionary_id: uuid.UUID,
    db: Session = Depends(get_session),
    importer: TagDictionaryImporter = Depends(get_importer),
    _admin: CurrentUser = Depends(require_admin),
) -> Response:
    row = _get_or_404(db, dictionary_id)
    if row.status == dictionaries.STATUS_IMPORTING or importer.is_importing(row.id):
        raise HTTPException(status_code=409, detail=t("tagDictionaries.importing"))
    dictionaries.delete_dictionary(db, row)
    db.commit()
    return Response(status_code=204)
