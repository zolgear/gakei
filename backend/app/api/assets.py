"""Asset の登録・一覧・詳細・配信。配信のパス解決は `AssetStore.content_path` の1関数に閉じる。"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import ValidationError
from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session

from app.api.pagination import InvalidCursorError, decode_cursor, encode_cursor
from app.auth.deps import require_user, require_user_or_api_token
from app.auth.identity import CurrentUser
from app.deps import get_session, get_store
from app.domain.asset_groups import get_active_group_or_none, group_for_asset
from app.domain.assets import IngestError, asset_is_used_as_input, is_restorable
from app.domain.assets import ingest_upload as ingest_asset
from app.domain.avatars import avatar_url
from app.domain.embedded_meta import build_lineage_meta, embed_gakei_chunk, get_instance_id
from app.domain.lineage import DEFAULT_UP, MAX_DEPTH, LineageNotFoundError, build_asset_lineage
from app.domain.models import AppUser, Asset, AssetGroup, AssetGroupMember, AssetKind, Run
from app.domain.schemas import (
    AssetDetail,
    AssetLineageResponse,
    AssetListResponse,
    AssetOrigin,
    AssetSummary,
    AssetUploadResponse,
    CreatedBy,
    EmbeddedGenerationMeta,
    ProducedByRunSummary,
)
from app.domain.storage import AssetStore
from app.i18n import t

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/assets", tags=["assets"])
# 画像の本体の配信だけは、Cookie に加えて MCP のアクセストークンも受ける(ADR-0023 7章 3)。
# `main.py` で `require_user` の括りに入れず、このルーター自身に認可を掛ける。
content_router = APIRouter(
    prefix="/api/assets", tags=["assets"], dependencies=[Depends(require_user_or_api_token)]
)

_DERIVED_MEDIA_TYPE = "image/webp"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _to_summary(asset: Asset) -> AssetSummary:
    return AssetSummary(
        id=asset.id,
        kind=asset.kind,
        mime=asset.mime,
        width=asset.width,
        height=asset.height,
        bytes=asset.bytes,
        created_at=asset.created_at,
    )


def _to_origin(db: Session, asset: Asset) -> AssetOrigin | None:
    """`origin_meta` があれば AssetOrigin を組み立てる(ADR-0014、2026-09-24 追記)。"""
    if asset.origin_meta is None:
        return None
    meta = asset.origin_meta if isinstance(asset.origin_meta, dict) else {}
    return AssetOrigin(
        asset_id=asset.origin_asset_id,
        same_instance=meta.get("instance") == get_instance_id(db),
        meta=meta,
    )


def _to_embedded_meta(asset: Asset) -> EmbeddedGenerationMeta | None:
    """`embedded_meta` があれば EmbeddedGenerationMeta を組み立てる(ADR-0018、2026-09-26 追記)。
    `schema` キーは型に無いので自然に落ちる。壊れた行(想定外の形)は 500 にせず null を返す。
    """
    if not isinstance(asset.embedded_meta, dict):
        return None
    try:
        return EmbeddedGenerationMeta.model_validate(asset.embedded_meta)
    except ValidationError:
        logger.warning("asset %s の embedded_meta が読めない形式のため無視した", asset.id)
        return None


def _to_created_by(db: Session, user_id: uuid.UUID | None) -> CreatedBy | None:
    if user_id is None:
        return None
    user = db.get(AppUser, user_id)
    if user is None:
        return None
    return CreatedBy(
        id=user.id,
        name=user.name,
        email=user.email,
        avatar_url=avatar_url(user.id, user.avatar_sha256),
    )


def _to_detail(db: Session, asset: Asset, produced_by_run: Run | None) -> AssetDetail:
    produced_by_summary = None
    if produced_by_run is not None:
        produced_by_summary = ProducedByRunSummary(
            id=produced_by_run.id,
            operation=produced_by_run.operation,
            model=produced_by_run.model,
            status=produced_by_run.status,
            prompt=produced_by_run.prompt,
        )
    return AssetDetail(
        id=asset.id,
        kind=asset.kind,
        mime=asset.mime,
        width=asset.width,
        height=asset.height,
        bytes=asset.bytes,
        created_at=asset.created_at,
        sha256=asset.sha256,
        output_index=asset.output_index,
        produced_by_run=produced_by_summary,
        deleted_at=asset.deleted_at,
        restorable=is_restorable(asset, produced_by_run),
        source_asset_id=asset.source_asset_id,
        origin=_to_origin(db, asset),
        embedded_meta=_to_embedded_meta(asset),
        used_as_input=asset_is_used_as_input(db, asset.id),
        created_by=_to_created_by(db, asset.created_by_user_id),
        group=group_for_asset(db, asset.id),
    )


@router.post("", response_model=AssetUploadResponse, status_code=201, operation_id="create_asset")
def create_asset(
    file: UploadFile = File(...),
    kind: Literal["upload", "mask", "sketch"] = Form(...),
    source_asset_id: uuid.UUID | None = Form(
        default=None,
        description="上描きスケッチの下地 Asset の id(ADR-0010)。kind=sketch のときだけ指定可。",
    ),
    replaces_asset_id: uuid.UUID | None = Form(
        default=None,
        description=(
            "未使用スケッチ・未使用マスクの再編集で置き換える元の Asset の id"
            "(ADR-0010、2026-09-25 追記)。kind=sketch か kind=mask のときだけ指定可"
            "(置き換え元と同じ kind である必要がある)。source_asset_id とは同時指定不可。"
        ),
    ),
    db: Session = Depends(get_session),
    store: AssetStore = Depends(get_store),
    user: CurrentUser = Depends(require_user),
) -> AssetUploadResponse:
    data = file.file.read()
    try:
        result = ingest_asset(
            db,
            store,
            data,
            AssetKind(kind),
            source_asset_id=source_asset_id,
            replaces_asset_id=replaces_asset_id,
            created_by_user_id=user.id,
        )
    except IngestError as e:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(e)) from e
    db.commit()
    detail = _to_detail(db, result.asset, produced_by_run=None)
    return AssetUploadResponse(**detail.model_dump(), ingest_outcome=result.outcome)


@router.get("", response_model=AssetListResponse, operation_id="list_assets")
def list_assets(
    kind: Literal["upload", "generated", "mask", "sketch"] | None = Query(default=None),
    group_id: uuid.UUID | None = Query(
        default=None, description="指定すると、そのグループのメンバーだけに絞る(ADR-0022)。"
    ),
    ungrouped: bool = Query(
        default=False,
        description="true なら、削除済みでないどのグループにも入っていない Asset だけに絞る"
        "(ストックの「グループなし」の節。ADR-0022)。`group_id` と同時には指定できない。",
    ),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    db: Session = Depends(get_session),
) -> AssetListResponse:
    if group_id is not None and ungrouped:
        raise HTTPException(status_code=422, detail=t("assetGroups.groupIdAndUngrouped"))
    if group_id is not None and get_active_group_or_none(db, group_id) is None:
        raise HTTPException(status_code=404, detail=t("assetGroups.notFound"))

    query = select(Asset).where(Asset.deleted_at.is_(None))
    if kind is not None:
        query = query.where(Asset.kind == kind)
    if group_id is not None:
        query = query.join(
            AssetGroupMember,
            and_(
                AssetGroupMember.asset_id == Asset.id,
                AssetGroupMember.asset_group_id == group_id,
            ),
        )
    if ungrouped:
        # 削除済みのグループにだけ入っている Asset は「グループなし」に含める。
        active_group_ids = select(AssetGroup.id).where(AssetGroup.deleted_at.is_(None))
        in_active_group = exists().where(
            AssetGroupMember.asset_id == Asset.id,
            AssetGroupMember.asset_group_id.in_(active_group_ids),
        )
        query = query.where(~in_active_group)
    if cursor is not None:
        try:
            moment, cursor_id = decode_cursor(cursor)
        except InvalidCursorError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        query = query.where(
            or_(
                Asset.created_at < moment,
                and_(Asset.created_at == moment, Asset.id < cursor_id),
            )
        )
    query = query.order_by(Asset.created_at.desc(), Asset.id.desc()).limit(limit + 1)

    rows = list(db.execute(query).scalars().all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = encode_cursor(rows[-1].created_at, rows[-1].id) if has_more and rows else None
    return AssetListResponse(items=[_to_summary(a) for a in rows], next_cursor=next_cursor)


@router.get("/{asset_id}", response_model=AssetDetail, operation_id="get_asset")
def get_asset(asset_id: uuid.UUID, db: Session = Depends(get_session)) -> AssetDetail:
    # 論理削除済みでも200で返す(Run詳細・系列グラフから引き続き参照できるようにするため)。
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail=t("assets.notFound"))
    produced_by_run = None
    if asset.produced_by_run_id is not None:
        produced_by_run = db.get(Run, asset.produced_by_run_id)
    return _to_detail(db, asset, produced_by_run)


@router.delete("/{asset_id}", status_code=204, operation_id="delete_asset")
def delete_asset(asset_id: uuid.UUID, db: Session = Depends(get_session)) -> None:
    """論理削除のみ。原本・派生ファイルは消さない(ADR-0008「削除」追加分)。"""
    asset = db.get(Asset, asset_id)
    if asset is None or asset.deleted_at is not None:
        raise HTTPException(status_code=404, detail=t("assets.notFound"))

    asset.deleted_at = _utcnow()
    db.commit()


@router.post("/{asset_id}/restore", response_model=AssetDetail, operation_id="restore_asset")
def restore_asset(asset_id: uuid.UUID, db: Session = Depends(get_session)) -> AssetDetail:
    """論理削除した Asset を復元する。変更するのは `asset.deleted_at` だけ
    (ADR-0008「Assetの復元」)。生んだ Run が削除済みの場合は復元できない。
    """
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail=t("assets.notFound"))
    if asset.deleted_at is None:
        raise HTTPException(status_code=409, detail=t("assets.notDeleted"))

    produced_by_run = None
    if asset.produced_by_run_id is not None:
        produced_by_run = db.get(Run, asset.produced_by_run_id)
    if produced_by_run is not None and produced_by_run.deleted_at is not None:
        raise HTTPException(
            status_code=409,
            detail=t("assets.cannotRestoreDeletedRunOutput"),
        )

    asset.deleted_at = None
    db.commit()
    return _to_detail(db, asset, produced_by_run)


@content_router.get("/{asset_id}/content", operation_id="get_asset_content")
def get_asset_content(
    asset_id: uuid.UUID,
    request: Request,
    variant: Literal["thumb", "preview", "original"] = Query(default="preview"),
    download: int = Query(default=0),
    db: Session = Depends(get_session),
    store: AssetStore = Depends(get_store),
) -> Response:
    # 論理削除済みでも配信する(系列グラフ・Run詳細のサムネイル表示用。原本ファイルは消さない)。
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail=t("assets.notFound"))

    path = store.content_path(asset.blob_key, asset.sha256, variant)
    if not path.exists():
        raise HTTPException(status_code=404, detail=t("assets.contentNotFound"))

    # ADR-0014(2026-09-24 追記): original をダウンロードする PNG にだけ、系列情報
    # (gakei チャンク)を埋め込んで返す。保存している原本・画面表示用の
    # original/thumb/preview(download=0)は変えない(ETag も区別する)。
    embed_meta = variant == "original" and bool(download) and asset.mime == "image/png"
    etag = f'"{asset.sha256}-original-gakei1"' if embed_meta else f'"{asset.sha256}-{variant}"'
    headers = {"ETag": etag, "Cache-Control": "private, immutable, max-age=31536000"}

    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)

    media_type = asset.mime if variant == "original" else _DERIVED_MEDIA_TYPE
    if download:
        ext = media_type.split("/")[-1]
        headers["Content-Disposition"] = f'attachment; filename="{asset.id}-{variant}.{ext}"'

    if embed_meta:
        data = store.read(asset.blob_key)
        meta = build_lineage_meta(db, asset)
        embedded = embed_gakei_chunk(data, meta)
        return Response(content=embedded, media_type=media_type, headers=headers)

    return FileResponse(path, media_type=media_type, headers=headers)


@router.get(
    "/{asset_id}/lineage", response_model=AssetLineageResponse, operation_id="get_asset_lineage"
)
def get_asset_lineage(
    asset_id: uuid.UUID,
    up: int = Query(default=DEFAULT_UP, ge=0, le=MAX_DEPTH, description="祖先方向の深さ上限"),
    down: int = Query(default=3, ge=0, le=MAX_DEPTH, description="子孫方向の深さ上限"),
    db: Session = Depends(get_session),
) -> AssetLineageResponse:
    try:
        return build_asset_lineage(db, asset_id, up=up, down=down)
    except LineageNotFoundError as e:
        raise HTTPException(status_code=404, detail=t("assets.notFound")) from e
