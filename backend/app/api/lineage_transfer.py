"""系列の持ち出し(エクスポート)と取り込み(インポート)(ADR-0037)。中身は
`app/domain/lineage_export.py` と `app/domain/lineage_import.py` にあり、ここは薄い。

- `GET /api/assets/{id}/export/preview?scope=&include_graph=`: 書き出す前の確認(枚数と大きさ。
  `include_graph=true` なら ZIP に入る Asset と Run の系列グラフも)。
- `GET /api/assets/{id}/export?scope=&mode=&include_creator_names=`: ZIP(`manifest.json` +
  `assets/{asset_id}.{拡張子}`)をストリーミングで返す。`mode=delivery`(納品用。ADR-0037 4章)は
  `index.html` と `README.txt` を加える。実行者の名前は `include_creator_names` のときだけ入れる。
- `POST /api/imports/lineage`: ZIP を受け取り、取り込んだ利用者の Asset と Run にする。

`main.py` で `require_user` の括りに入れる。
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.auth.deps import require_user
from app.auth.identity import CurrentUser
from app.config import Settings
from app.deps import get_annotator, get_embedder, get_session, get_settings, get_store
from app.domain import ingest_hooks
from app.domain import lineage_delivery as delivery_domain
from app.domain import lineage_export as export_domain
from app.domain import lineage_import as import_domain
from app.domain.schemas import LineageExportPreviewResponse, LineageImportResponse
from app.domain.storage import AssetStore
from app.i18n import current_locale, t
from app.version import get_version
from app.worker.annotator import Annotator
from app.worker.embedder import Embedder

router = APIRouter(tags=["lineage-transfer"])

ScopeParam = Literal["ancestors", "descendants", "lineage"]
ModeParam = Literal["import", "delivery"]
LangParam = Literal["ja", "en"]


def _plan(
    db: Session,
    user: CurrentUser,
    asset_id: uuid.UUID,
    scope: ScopeParam,
    *,
    mode: ModeParam = "import",
    include_creator_names: bool = False,
    include_graph: bool = False,
) -> export_domain.ExportPlan:
    try:
        return export_domain.build_export(
            db,
            user,
            asset_id,
            scope,
            gakei_version=get_version(),
            mode=mode,
            include_creator_names=include_creator_names,
            include_graph=include_graph,
        )
    except export_domain.ExportTargetNotFoundError as e:
        raise HTTPException(status_code=404, detail=t("assets.notFound")) from e


@router.get(
    "/api/assets/{asset_id}/export/preview",
    response_model=LineageExportPreviewResponse,
    operation_id="preview_lineage_export",
)
def preview_lineage_export(
    asset_id: uuid.UUID,
    scope: ScopeParam = Query(default="ancestors"),
    include_graph: bool = Query(default=False),
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> LineageExportPreviewResponse:
    """書き出す前に、範囲に含まれる画像と Run の数、原本の合計を確かめる。何も書き込まない。
    `include_graph` なら、ZIP に入る Asset と Run だけの系列グラフも返す(書き出しと同じ計算)。"""
    plan = _plan(db, user, asset_id, scope, include_graph=include_graph)
    return LineageExportPreviewResponse(
        scope=scope,
        asset_count=len(plan.files),
        run_count=plan.run_count,
        total_bytes=plan.total_bytes,
        truncated=plan.truncated,
        omitted_input_count=plan.omitted_input_count,
        graph=plan.graph,
    )


@router.get(
    "/api/assets/{asset_id}/export",
    operation_id="export_lineage",
    response_class=StreamingResponse,
    responses={200: {"content": {"application/zip": {}}}},
)
def export_lineage(
    asset_id: uuid.UUID,
    scope: ScopeParam = Query(default="ancestors"),
    mode: ModeParam = Query(default="import"),
    include_creator_names: bool = Query(default=False),
    lang: LangParam | None = Query(default=None, description="納品用の index.html の言語"),
    tz: str | None = Query(
        default=None, max_length=64, description="納品用の index.html の日時のタイムゾーン(IANA)"
    ),
    db: Session = Depends(get_session),
    store: AssetStore = Depends(get_store),
    user: CurrentUser = Depends(require_user),
) -> StreamingResponse:
    """系列を ZIP で書き出す(ADR-0037 1章・4章)。範囲の計算は共有リンクと同じ。

    納品用の index.html の言語は、画面の表示言語(`lang`。ダウンロードはリンクで行うので
    `Accept-Language` が画面の言語と一致するとは限らない)、無ければ `Accept-Language`。
    """
    plan = _plan(db, user, asset_id, scope, mode=mode, include_creator_names=include_creator_names)
    # ZIP を流す間は DB を使わない。原本が無ければ流し始める前に断る。
    db.close()
    try:
        export_domain.ensure_contents_exist(store, plan)
    except export_domain.ExportContentMissingError as e:
        raise HTTPException(status_code=409, detail=t("lineageExport.contentMissing")) from e
    if mode == "delivery":
        # ZIP を流し始める前に作っておく(流す間はリクエストの言語の文脈が無いため)。
        plan.extra_entries = delivery_domain.build_delivery_entries(
            plan, locale=lang or current_locale(), tz_name=tz
        )
    headers = {
        "Content-Disposition": f'attachment; filename="{plan.filename()}"',
        "Cache-Control": "no-store",
    }
    return StreamingResponse(
        export_domain.iter_export_zip(store, plan), media_type="application/zip", headers=headers
    )


@router.post(
    "/api/imports/lineage",
    response_model=LineageImportResponse,
    status_code=201,
    operation_id="import_lineage",
)
def import_lineage(
    file: UploadFile = File(...),
    db: Session = Depends(get_session),
    store: AssetStore = Depends(get_store),
    user: CurrentUser = Depends(require_user),
    settings: Settings = Depends(get_settings),
    annotator: Annotator = Depends(get_annotator),
    embedder: Embedder = Depends(get_embedder),
) -> LineageImportResponse:
    """系列の ZIP を取り込む(ADR-0037 2章)。検証に1つでも当たれば ZIP 全体を断り、DB には
    何も書かない。取り込んだ Asset と Run の持ち主は、取り込んだ利用者。"""
    try:
        archive = import_domain.open_archive(file.file)
    except import_domain.LineageImportError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e)) from e
    try:
        import_domain.verify_contents(archive)
        result = import_domain.import_archive(db, store, user, archive)
        # ADR-0024 4章・ADR-0033 5章: 新しく作った Asset だけ、自動タイトル・タグと埋め込みの
        # 待ち行列に入れる(設定がオンのときだけ)。
        queued = ingest_hooks.IngestQueued()
        for asset in result.created_assets:
            queued = queued | ingest_hooks.enqueue_after_ingest(db, asset, settings)
        db.commit()
    except import_domain.LineageImportError as e:
        db.rollback()
        raise HTTPException(status_code=e.status_code, detail=str(e)) from e
    except Exception:
        db.rollback()
        raise
    finally:
        archive.zf.close()
    ingest_hooks.notify_workers(queued, annotator=annotator, embedder=embedder)
    return LineageImportResponse(
        root_asset_id=result.root_asset_id,
        asset_count=result.asset_count,
        run_count=result.run_count,
        created_asset_count=len(result.created_assets),
        created_run_count=result.created_run_count,
    )
