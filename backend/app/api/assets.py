"""Asset の登録・一覧・詳細・配信。配信は `AssetStore.open_content` を経由する(ADR-0028 4章)。"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import ValidationError
from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session

from app.api.pagination import InvalidCursorError, decode_cursor, encode_cursor
from app.auth.deps import require_user, require_user_or_api_token
from app.auth.identity import CurrentUser
from app.config import Settings
from app.deps import get_annotator, get_embedder, get_session, get_settings, get_store
from app.domain import annotation_settings, derivatives, embedding_settings, ingest_hooks
from app.domain import annotations as annotations_domain
from app.domain import embeddings as embeddings_domain
from app.domain.asset_groups import group_for_asset
from app.domain.assets import IngestError, asset_is_used_as_input, is_restorable
from app.domain.assets import ingest_upload as ingest_asset
from app.domain.avatars import avatar_url
from app.domain.embedded_meta import build_lineage_meta, embed_gakei_chunk, get_instance_id
from app.domain.lineage import (
    DEFAULT_DOWN,
    DEFAULT_UP,
    MAX_DEPTH,
    LineageNotFoundError,
    build_asset_lineage,
)
from app.domain.models import AppUser, Asset, AssetGroup, AssetGroupMember, AssetKind, Run
from app.domain.run_views import run_text_outputs
from app.domain.schemas import (
    AssetAnnotationResponse,
    AssetDetail,
    AssetEmbeddingStatus,
    AssetLineageResponse,
    AssetListResponse,
    AssetOrigin,
    AssetSummary,
    AssetTagAddRequest,
    AssetTitleUpdateRequest,
    AssetUploadResponse,
    CreatedBy,
    EmbeddedGenerationMeta,
    ProducedByRunSummary,
)
from app.domain.storage import AssetStore
from app.domain.visibility import (
    VisibilityChecker,
    asset_visible,
    get_visible_asset,
    get_visible_group,
)
from app.i18n import t
from app.worker.annotator import Annotator
from app.worker.embedder import Embedder

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


def _to_summary(asset: Asset, title: str | None = None) -> AssetSummary:
    return AssetSummary(
        id=asset.id,
        kind=asset.kind,
        mime=asset.mime,
        width=asset.width,
        height=asset.height,
        bytes=asset.bytes,
        created_at=asset.created_at,
        title=title,
    )


def _to_origin(db: Session, asset: Asset, checker: VisibilityChecker) -> AssetOrigin | None:
    """`origin_meta` があれば AssetOrigin を組み立てる(ADR-0014、2026-09-24 追記)。

    由来の Asset が見る人に見えなければ、その id は返さず `asset_hidden` を立てる
    (ADR-0025 4章)。埋め込まれていた内容(`meta`)はファイルに書かれていたものなので返す。
    """
    if asset.origin_meta is None:
        return None
    meta = asset.origin_meta if isinstance(asset.origin_meta, dict) else {}
    origin_asset_id = asset.origin_asset_id
    hidden = origin_asset_id is not None and not checker.asset_id(origin_asset_id)
    return AssetOrigin(
        asset_id=None if hidden else origin_asset_id,
        asset_hidden=hidden,
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


def _to_detail(
    db: Session, asset: Asset, produced_by_run: Run | None, user: CurrentUser
) -> AssetDetail:
    """`asset` は `user` に見えることを呼び出し側で確かめ済み。その Asset が指す他の Asset
    (上描きの下地、由来)やグループが見えなければ、id を返さない(ADR-0025)。
    """
    checker = VisibilityChecker(db, user)
    source_asset_id = asset.source_asset_id
    if source_asset_id is not None and not checker.asset_id(source_asset_id):
        source_asset_id = None
    produced_by_summary = None
    if produced_by_run is not None:
        produced_by_summary = ProducedByRunSummary(
            id=produced_by_run.id,
            operation=produced_by_run.operation,
            model=produced_by_run.model,
            status=produced_by_run.status,
            prompt=produced_by_run.prompt,
            text_outputs=run_text_outputs(produced_by_run),
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
        source_asset_id=source_asset_id,
        origin=_to_origin(db, asset, checker),
        embedded_meta=_to_embedded_meta(asset),
        used_as_input=asset_is_used_as_input(db, asset.id),
        created_by=_to_created_by(db, asset.created_by_user_id),
        group=group_for_asset(db, asset.id, user),
        **annotations_domain.annotation_fields(db, asset.id),
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
    settings: Settings = Depends(get_settings),
    annotator: Annotator = Depends(get_annotator),
    embedder: Embedder = Depends(get_embedder),
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
            viewer=user,
        )
    except IngestError as e:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(e)) from e
    # ADR-0024 4章・ADR-0033 5章: 取り込み時の自動推定と埋め込み(設定がオンのときだけ)。
    # 既存の Asset を返す場合(`matched_existing`)は重ねて待ち行列に入れない。
    queued = ingest_hooks.IngestQueued()
    if result.outcome == "created":
        queued = ingest_hooks.enqueue_after_ingest(db, result.asset, settings)
    db.commit()
    ingest_hooks.notify_workers(queued, annotator=annotator, embedder=embedder)
    detail = _to_detail(db, result.asset, produced_by_run=None, user=user)
    return AssetUploadResponse(**detail.model_dump(), ingest_outcome=result.outcome)


@router.get("", response_model=AssetListResponse, operation_id="list_assets")
def list_assets(
    kind: list[Literal["upload", "generated", "mask", "sketch"]] | None = Query(
        default=None,
        description="繰り返して指定できる(`?kind=generated&kind=upload`)。指定した種類の"
        "どれかに当たる Asset に絞る。省くと全種類(ADR-0035)。",
    ),
    group_id: uuid.UUID | None = Query(
        default=None, description="指定すると、そのグループのメンバーだけに絞る(ADR-0022)。"
    ),
    ungrouped: bool = Query(
        default=False,
        description="true なら、削除済みでないどのグループにも入っていない Asset だけに絞る"
        "(ストックの「グループなし」の節。ADR-0022)。`group_id` と同時には指定できない。",
    ),
    tag: str | None = Query(
        default=None,
        description="指定すると、そのタグ(人が消したものを除く)が付いた Asset だけに絞る"
        "(ADR-0024)。名前は保存時と同じく正規化して比べる。",
    ),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> AssetListResponse:
    if group_id is not None and ungrouped:
        raise HTTPException(status_code=422, detail=t("assetGroups.groupIdAndUngrouped"))
    if group_id is not None and get_visible_group(db, user, group_id) is None:
        raise HTTPException(status_code=404, detail=t("assetGroups.notFound"))

    # ADR-0025: 見える Asset だけ(ページングの件数・カーソルも同じ条件の上で数える)。
    query = select(Asset).where(Asset.deleted_at.is_(None), asset_visible(user))
    if kind:
        query = query.where(Asset.kind.in_(kind))
    if tag is not None and tag.strip():
        try:
            query = query.where(annotations_domain.tag_filter(tag))
        except annotations_domain.TagNameError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
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
    titles = annotations_domain.bulk_titles(db, [a.id for a in rows])
    return AssetListResponse(
        items=[_to_summary(a, titles.get(a.id)) for a in rows], next_cursor=next_cursor
    )


@router.get("/{asset_id}", response_model=AssetDetail, operation_id="get_asset")
def get_asset(
    asset_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> AssetDetail:
    # 論理削除済みでも200で返す(Run詳細・系列グラフから引き続き参照できるようにするため)。
    # 他人の Asset は存在しないものと同じ 404(ADR-0025)。
    asset = get_visible_asset(db, user, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail=t("assets.notFound"))
    produced_by_run = None
    if asset.produced_by_run_id is not None:
        produced_by_run = db.get(Run, asset.produced_by_run_id)
    return _to_detail(db, asset, produced_by_run, user)


@router.delete("/{asset_id}", status_code=204, operation_id="delete_asset")
def delete_asset(
    asset_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> None:
    """論理削除のみ。原本・派生ファイルは消さない(ADR-0008「削除」追加分)。"""
    asset = get_visible_asset(db, user, asset_id)
    if asset is None or asset.deleted_at is not None:
        raise HTTPException(status_code=404, detail=t("assets.notFound"))

    asset.deleted_at = _utcnow()
    db.commit()


@router.post("/{asset_id}/restore", response_model=AssetDetail, operation_id="restore_asset")
def restore_asset(
    asset_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> AssetDetail:
    """論理削除した Asset を復元する。変更するのは `asset.deleted_at` だけ
    (ADR-0008「Assetの復元」)。生んだ Run が削除済みの場合は復元できない。
    """
    asset = get_visible_asset(db, user, asset_id)
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
    return _to_detail(db, asset, produced_by_run, user)


@content_router.get("/{asset_id}/content", operation_id="get_asset_content")
def get_asset_content(
    asset_id: uuid.UUID,
    request: Request,
    variant: Literal["thumb", "preview", "original"] = Query(default="preview"),
    download: int = Query(default=0),
    db: Session = Depends(get_session),
    store: AssetStore = Depends(get_store),
    user: CurrentUser = Depends(require_user_or_api_token),
) -> Response:
    # 論理削除済みでも配信する(系列グラフ・Run詳細のサムネイル表示用。原本ファイルは消さない)。
    # 他人の Asset は存在しないものと同じ 404(ADR-0025。アクセストークンでも同じ)。
    asset = get_visible_asset(db, user, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail=t("assets.notFound"))

    return asset_content_response(
        db,
        store,
        asset,
        viewer=user,
        variant=variant,
        download=bool(download),
        if_none_match=request.headers.get("if-none-match"),
    )


def asset_content_response(
    db: Session,
    store: AssetStore,
    asset: Asset,
    *,
    viewer: CurrentUser | None,
    variant: Literal["thumb", "preview", "original"],
    download: bool,
    if_none_match: str | None = None,
    cache_control: str = "private, immutable, max-age=31536000",
) -> Response:
    """画像の本体の応答。`GET /api/assets/{id}/content` と、1回限りのダウンロード URL
    (`GET /api/downloads/{token}`。ADR-0023 8章 3)で共通。見えるかどうかは呼び出し側が
    確かめる。ファイルが無ければ 404。`viewer` が None なら、原本をダウンロードするときも
    系列情報を埋め込まない(ログイン不要の共有リンク。ADR-0029 4章)。

    ローカルFSは `FileResponse`(Range 要求に応える)、オブジェクトストレージは読み出した
    チャンクをそのまま流す(`Content-Length` 付き、Range には応えない。ADR-0028 4章)。"""
    # ADR-0014(2026-09-24 追記): original をダウンロードする PNG にだけ、系列情報
    # (gakei チャンク)を埋め込んで返す。保存している原本・画面表示用の
    # original/thumb/preview(download=0)は変えない(ETag も区別する)。
    embed_meta = (
        viewer is not None and variant == "original" and download and asset.mime == "image/png"
    )
    if embed_meta:
        etag = f'"{asset.sha256}-original-gakei1"'
    elif variant == "original":
        etag = f'"{asset.sha256}-original"'
    else:
        # 派生は作り方の版を含める(ADR-0036 3章)。版が変われば別の ETag になる。
        etag = f'"{asset.sha256}-{variant}-d{derivatives.DERIVED_VERSION}"'
    headers = {"ETag": etag, "Cache-Control": cache_control}

    if if_none_match == etag:
        # 本体は読まない(オブジェクトストレージで原本を丸ごと取りに行かないため)。
        # 派生の ETag は版を含むので、一致すればブラウザは今の版の中身を持っている。今の版の
        # 派生が消えていても、原本があれば同じ中身を作り直せるので 304 でよい(ここでは
        # 作り直さず、次に本体を要求されたときに `ensure_derived` が作る)。原本も無ければ、
        # これまでどおり 304 ではなく 404。
        if not store.content_exists(asset.blob_key, asset.sha256, variant) and (
            variant == "original"
            or not store.content_exists(asset.blob_key, asset.sha256, "original")
        ):
            raise HTTPException(status_code=404, detail=t("assets.contentNotFound"))
        return Response(status_code=304, headers=headers)

    if variant == "original":
        content = store.open_content(asset.blob_key, asset.sha256, variant)
    else:
        # 今の版の派生が無ければ原本から作る(ADR-0036 2章)。
        content = derivatives.ensure_derived(store, asset.blob_key, asset.sha256, variant)
    if content is None:
        raise HTTPException(status_code=404, detail=t("assets.contentNotFound"))

    media_type = asset.mime if variant == "original" else _DERIVED_MEDIA_TYPE
    if download:
        ext = media_type.split("/")[-1]
        headers["Content-Disposition"] = f'attachment; filename="{asset.id}-{variant}.{ext}"'

    if embed_meta:
        data = content.read_all()
        assert viewer is not None
        meta = build_lineage_meta(db, asset, viewer=viewer)
        embedded = embed_gakei_chunk(data, meta)
        return Response(content=embedded, media_type=media_type, headers=headers)

    if content.path is not None:
        return FileResponse(content.path, media_type=media_type, headers=headers)
    headers["Content-Length"] = str(content.size)
    return StreamingResponse(content.chunks, media_type=media_type, headers=headers)


@router.get(
    "/{asset_id}/lineage", response_model=AssetLineageResponse, operation_id="get_asset_lineage"
)
def get_asset_lineage(
    asset_id: uuid.UUID,
    up: int = Query(default=DEFAULT_UP, ge=0, le=MAX_DEPTH, description="祖先方向の深さ上限"),
    down: int = Query(default=DEFAULT_DOWN, ge=0, le=MAX_DEPTH, description="子孫方向の深さ上限"),
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> AssetLineageResponse:
    try:
        return build_asset_lineage(db, asset_id, viewer=user, up=up, down=down)
    except LineageNotFoundError as e:
        raise HTTPException(status_code=404, detail=t("assets.notFound")) from e


# -- タイトルとタグ(ADR-0024) -------------------------------------------------
# 編集できるのは、その Asset が見える利用者(ADR-0025。見えなければ 404)。マスクと
# 削除済みの Asset は対象外(409)。いずれも更新後の注釈(title / title_source / tags /
# annotation)を返す。


def _annotation_target(db: Session, user: CurrentUser, asset_id: uuid.UUID) -> Asset:
    try:
        return annotations_domain.get_target_asset(db, user, asset_id)
    except annotations_domain.AnnotationTargetError as e:
        status = 404 if e.kind == "not_found" else 409
        raise HTTPException(status_code=status, detail=str(e)) from e


@router.patch(
    "/{asset_id}/title",
    response_model=AssetAnnotationResponse,
    operation_id="update_asset_title",
)
def update_asset_title(
    asset_id: uuid.UUID,
    body: AssetTitleUpdateRequest,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> AssetAnnotationResponse:
    """タイトルを人が決める(以後の再推定で上書きしない)。null・空文字はタイトルを消し、
    その状態も人の決定として保つ。"""
    asset = _annotation_target(db, user, asset_id)
    try:
        annotations_domain.set_title(db, asset, body.title)
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(e)) from e
    db.commit()
    return annotations_domain.annotation_response(db, asset_id)


@router.post(
    "/{asset_id}/tags",
    response_model=AssetAnnotationResponse,
    operation_id="add_asset_tag",
)
def add_asset_tag(
    asset_id: uuid.UUID,
    body: AssetTagAddRequest,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> AssetAnnotationResponse:
    """タグを足す(人のタグになる。自動のタグや、消したタグを付け直すのもこれ)。"""
    asset = _annotation_target(db, user, asset_id)
    try:
        annotations_domain.add_tag(db, asset, body.name)
    except annotations_domain.TagNameError as e:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(e)) from e
    db.commit()
    return annotations_domain.annotation_response(db, asset_id)


@router.delete(
    "/{asset_id}/tags/{name:path}",
    response_model=AssetAnnotationResponse,
    operation_id="remove_asset_tag",
)
def remove_asset_tag(
    asset_id: uuid.UUID,
    name: str,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> AssetAnnotationResponse:
    """タグを外す。消したことを記録し、同じタグを再推定で付け直さない。付いていなければ 404。"""
    asset = _annotation_target(db, user, asset_id)
    try:
        removed = annotations_domain.remove_tag(db, asset, name)
    except annotations_domain.TagNameError as e:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(e)) from e
    if not removed:
        db.rollback()
        raise HTTPException(status_code=404, detail=t("annotations.tagNotFound"))
    db.commit()
    return annotations_domain.annotation_response(db, asset_id)


@router.post(
    "/{asset_id}/annotate",
    response_model=AssetAnnotationResponse,
    operation_id="annotate_asset",
)
def annotate_asset(
    asset_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
    settings: Settings = Depends(get_settings),
    annotator: Annotator = Depends(get_annotator),
) -> AssetAnnotationResponse:
    """この Asset の(再)推定を待ち行列に入れる。人が決めたタイトルとタグは保つ。
    使えるエンジンが無ければ 409。既に待ち行列にあれば何もしない。"""
    asset = _annotation_target(db, user, asset_id)
    config = annotation_settings.load(db)
    if not annotations_domain.usable_engines(config, settings):
        raise HTTPException(status_code=409, detail=t("annotations.noEngine"))
    annotations_domain.request_annotation(db, asset)
    db.commit()
    annotator.notify()
    return annotations_domain.annotation_response(db, asset_id)


# -- 埋め込み(ADR-0033) ----------------------------------------------------------


@router.post(
    "/{asset_id}/embedding",
    response_model=AssetEmbeddingStatus,
    operation_id="request_asset_embedding",
)
def request_asset_embedding(
    asset_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
    settings: Settings = Depends(get_settings),
    embedder: Embedder = Depends(get_embedder),
) -> AssetEmbeddingStatus:
    """この Asset の埋め込みを、使うモデルで計算し直す(待ち行列に入れる)。見えなければ 404、
    マスクと削除済み、埋め込みが無効・使えないときは 409。既に待ち行列にあれば何もしない。"""
    asset = get_visible_asset(db, user, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail=t("assets.notFound"))
    if asset.kind == AssetKind.MASK:
        raise HTTPException(status_code=409, detail=t("embeddings.maskNotSupported"))
    if asset.deleted_at is not None:
        raise HTTPException(status_code=409, detail=t("embeddings.assetDeleted"))
    config = embedding_settings.load(db)
    if not embedding_settings.usable(config, settings):
        raise HTTPException(status_code=409, detail=t("embeddings.notUsable"))
    model_key = embedding_settings.active_model_key(config, settings)
    assert model_key is not None
    embeddings_domain.request_embedding(db, asset, model_key)
    db.commit()
    embedder.notify()
    row = embeddings_domain.embedding_status(db, asset_id, model_key)
    assert row is not None
    return AssetEmbeddingStatus(
        asset_id=asset_id,
        model_key=model_key,
        status=row.status,  # type: ignore[arg-type]
        error=row.error,
        requested_at=row.requested_at,
        finished_at=row.finished_at,
    )
