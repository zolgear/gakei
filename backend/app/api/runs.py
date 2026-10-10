"""Run の作成・一覧・詳細・キャンセル。
POST /api/runs はサーバー側で capabilities に照らして検証する。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from app.api.pagination import InvalidCursorError, decode_cursor, encode_cursor
from app.auth.deps import require_user
from app.auth.identity import CurrentUser
from app.config import Settings
from app.deps import get_progress_bus, get_registry, get_runner, get_session, get_settings
from app.domain import focal_points
from app.domain import run_create as run_create_domain
from app.domain.models import (
    RUN_ORIGIN_IMPORT,
    Asset,
    Run,
    RunImport,
    RunInput,
    RunInputRole,
    RunStatus,
)
from app.domain.run_views import (
    bulk_asset_groups,
    bulk_descendant_run_counts,
    bulk_input_summary,
    bulk_output_refs,
    bulk_parent_focal_points,
    bulk_users,
    run_summary_fields,
)
from app.domain.schemas import (
    RunCancelResponse,
    RunCreatedRef,
    RunCreateRequest,
    RunCreateResponse,
    RunDetail,
    RunImportInfo,
    RunInputRef,
    RunListResponse,
    RunSummary,
)
from app.domain.visibility import get_visible_run, run_visible, visible_asset_ids
from app.i18n import t
from app.providers.registry import ProviderRegistry
from app.worker.progress import ProgressBus
from app.worker.runner import Runner

router = APIRouter(prefix="/api/runs", tags=["runs"])


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _to_detail(db: Session, run: Run, user: CurrentUser) -> RunDetail:
    inputs = (
        db.execute(select(RunInput).where(RunInput.run_id == run.id).order_by(RunInput.position))
        .scalars()
        .all()
    )
    outputs = bulk_output_refs(db, [run.id])[run.id]
    input_count = sum(1 for i in inputs if i.role == RunInputRole.IMAGE)
    # ADR-0025: 見えない Asset(以前のデータで他人の Asset を入力にしていた場合)は、
    # 入力の一覧・主たる親に出さない(id も返さない)。枚数は Run 自身の情報なので数える。
    visible_inputs = visible_asset_ids(db, user, [i.asset_id for i in inputs])
    inputs = [i for i in inputs if i.asset_id in visible_inputs]
    primary_parent_asset_id = next(
        (i.asset_id for i in inputs if i.role == RunInputRole.IMAGE and i.position == 0), None
    )
    descendant_run_count = bulk_descendant_run_counts(db, [run.id], user)[run.id]
    created_by = bulk_users(db, [run.created_by_user_id]).get(run.created_by_user_id)
    asset_group = bulk_asset_groups(db, [run.asset_group_id], user).get(run.asset_group_id)
    # サムネイルの焦点(ADR-0043)。入力と主たる親の分をまとめて引く。
    focals = focal_points.bulk_get(db, [i.asset_id for i in inputs])
    return RunDetail(
        **run_summary_fields(
            run,
            outputs,
            input_count,
            primary_parent_asset_id,
            descendant_run_count,
            created_by,
            asset_group,
            primary_parent_focal_point=focals.get(primary_parent_asset_id)
            if primary_parent_asset_id is not None
            else None,
        ),
        deployment=run.deployment,
        provider_request_id=run.provider_request_id,
        inputs=[
            RunInputRef(
                asset_id=i.asset_id,
                role=i.role,
                position=i.position,
                focal_point=focals.get(i.asset_id),
            )
            for i in inputs
        ],
        imported=_import_info(db, run),
    )


def _import_info(db: Session, run: Run) -> RunImportInfo | None:
    """取り込んだ Run(ADR-0037)の、書き出し元での記録。それ以外の Run は None。"""
    if run.origin != RUN_ORIGIN_IMPORT:
        return None
    row = db.get(RunImport, run.id)
    if row is None:
        return None
    return RunImportInfo(
        source_run_id=row.source_run_id,
        source_creator_name=row.source_creator_name,
        source_created_at=row.source_created_at,
        source_finished_at=row.source_finished_at,
        source_gakei_version=row.source_gakei_version,
        imported_at=row.imported_at,
    )


_ERROR_STATUS = {"invalid": 422, "conflict": 409, "not_found": 404}


@router.post("", response_model=RunCreateResponse, status_code=202, operation_id="create_run")
def create_run(
    body: RunCreateRequest,
    db: Session = Depends(get_session),
    registry: ProviderRegistry = Depends(get_registry),
    runner: Runner = Depends(get_runner),
    settings: Settings = Depends(get_settings),
    user: CurrentUser = Depends(require_user),
) -> RunCreateResponse:
    # 検証〜挿入〜runner への投入は MCP と共通のドメイン関数に任せる(ADR-0023 1章)。
    # 繰り返し回数(ADR-0042)は REST だけが受ける。
    try:
        runs = run_create_domain.create_runs(
            db, registry, runner, settings, body, viewer=user, repeat=body.repeat
        )
    except run_create_domain.RunCreateError as e:
        raise HTTPException(status_code=_ERROR_STATUS[e.kind], detail=str(e)) from e
    return RunCreateResponse(
        id=runs[0].id,
        status=runs[0].status,
        runs=[RunCreatedRef(id=r.id, status=r.status) for r in runs],
    )


@router.get("", response_model=RunListResponse, operation_id="list_runs")
def list_runs(
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> RunListResponse:
    # ADR-0025: 見える Run だけ(App バーの実行中・待機中の件数もこの一覧から数える)。
    query = select(Run).where(Run.deleted_at.is_(None), run_visible(user))
    if cursor is not None:
        try:
            moment, cursor_id = decode_cursor(cursor)
        except InvalidCursorError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        query = query.where(
            or_(
                Run.queued_at < moment,
                and_(Run.queued_at == moment, Run.id < cursor_id),
            )
        )
    query = query.order_by(Run.queued_at.desc(), Run.id.desc()).limit(limit + 1)

    rows = list(db.execute(query).scalars().all())
    has_more = len(rows) > limit
    rows = rows[:limit]

    # 1ページ分の run_id をまとめて引く(N+1 を避ける)。
    run_ids = [r.id for r in rows]
    outputs_map = bulk_output_refs(db, run_ids)
    inputs_map = bulk_input_summary(db, run_ids, user)
    descendant_map = bulk_descendant_run_counts(db, run_ids, user)
    users_map = bulk_users(db, [r.created_by_user_id for r in rows])
    groups_map = bulk_asset_groups(db, [r.asset_group_id for r in rows], user)
    parent_focals = bulk_parent_focal_points(db, inputs_map)
    items = [
        RunSummary(
            **run_summary_fields(
                r,
                outputs_map[r.id],
                *inputs_map[r.id],
                descendant_map[r.id],
                users_map.get(r.created_by_user_id),
                groups_map.get(r.asset_group_id),
                primary_parent_focal_point=parent_focals.get(inputs_map[r.id][1]),
            )
        )
        for r in rows
    ]

    next_cursor = encode_cursor(rows[-1].queued_at, rows[-1].id) if has_more and rows else None
    return RunListResponse(items=items, next_cursor=next_cursor)


@router.get("/{run_id}", response_model=RunDetail, operation_id="get_run")
def get_run(
    run_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> RunDetail:
    run = get_visible_run(db, user, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=t("runs.notFound"))
    return _to_detail(db, run, user)


@router.delete("/{run_id}", status_code=204, operation_id="delete_run")
def delete_run(
    run_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> None:
    """論理削除。終了状態(succeeded/failed/canceled)の Run だけ削除できる。

    出力 Asset(`produced_by_run_id = run_id` で未削除のもの)も同一トランザクションで
    論理削除する(ADR-0008「削除」追加分)。`run`/`run_input` の他の列は変更しない。
    他人の Run は存在しないものと同じ 404(ADR-0025)。
    """
    run = get_visible_run(db, user, run_id)
    if run is None or run.deleted_at is not None:
        raise HTTPException(status_code=404, detail=t("runs.notFound"))
    if run.status not in (RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELED):
        raise HTTPException(
            status_code=409,
            detail=t("runs.cannotDeleteRunning"),
        )

    now = _utcnow()
    run.deleted_at = now
    db.execute(
        update(Asset)
        .where(Asset.produced_by_run_id == run_id, Asset.deleted_at.is_(None))
        .values(deleted_at=now)
    )
    db.commit()


@router.post("/{run_id}/cancel", response_model=RunCancelResponse, operation_id="cancel_run")
async def cancel_run(
    run_id: uuid.UUID,
    db: Session = Depends(get_session),
    bus: ProgressBus = Depends(get_progress_bus),
    user: CurrentUser = Depends(require_user),
) -> RunCancelResponse:
    run = get_visible_run(db, user, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=t("runs.notFound"))
    if run.status != RunStatus.QUEUED:
        raise HTTPException(
            status_code=409,
            detail=t("runs.cannotCancelStatus", status=run.status),
        )

    result = db.execute(
        update(Run)
        .where(Run.id == run_id, Run.status == RunStatus.QUEUED)
        .values(status=RunStatus.CANCELED, finished_at=_utcnow())
    )
    db.commit()
    if result.rowcount == 0:
        raise HTTPException(
            status_code=409,
            detail=t("runs.cannotCancelAlreadyRunning"),
        )

    # このハンドラは async のため、イベントループ上で安全に asyncio.Queue へ publish できる。
    await bus.publish(run_id, {"type": "status", "status": "canceled"})

    return RunCancelResponse(id=run_id, status=RunStatus.CANCELED)
