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
from app.domain import api_key as api_key_domain
from app.domain.asset_groups import get_active_group_or_none
from app.domain.models import Asset, Run, RunInput, RunInputRole, RunStatus
from app.domain.run_validation import RunInputMeta, RunValidationError, validate_run_request
from app.domain.run_views import (
    bulk_asset_groups,
    bulk_descendant_run_counts,
    bulk_input_summary,
    bulk_output_refs,
    bulk_users,
    run_summary_fields,
)
from app.domain.schemas import (
    RunCancelResponse,
    RunCreateRequest,
    RunCreateResponse,
    RunDetail,
    RunInputRef,
    RunListResponse,
    RunSummary,
)
from app.i18n import t
from app.providers.base import ProviderUnavailableError, RunDraft
from app.providers.registry import ProviderRegistry
from app.worker.progress import ProgressBus
from app.worker.runner import Runner

router = APIRouter(prefix="/api/runs", tags=["runs"])


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _to_summary(db: Session, run: Run) -> RunSummary:
    outputs = bulk_output_refs(db, [run.id])[run.id]
    input_count, primary_parent_asset_id = bulk_input_summary(db, [run.id])[run.id]
    descendant_run_count = bulk_descendant_run_counts(db, [run.id])[run.id]
    created_by = bulk_users(db, [run.created_by_user_id]).get(run.created_by_user_id)
    asset_group = bulk_asset_groups(db, [run.asset_group_id]).get(run.asset_group_id)
    return RunSummary(
        **run_summary_fields(
            run,
            outputs,
            input_count,
            primary_parent_asset_id,
            descendant_run_count,
            created_by,
            asset_group,
        )
    )


def _to_detail(db: Session, run: Run) -> RunDetail:
    inputs = (
        db.execute(select(RunInput).where(RunInput.run_id == run.id).order_by(RunInput.position))
        .scalars()
        .all()
    )
    outputs = bulk_output_refs(db, [run.id])[run.id]
    input_count = sum(1 for i in inputs if i.role == RunInputRole.IMAGE)
    primary_parent_asset_id = next(
        (i.asset_id for i in inputs if i.role == RunInputRole.IMAGE and i.position == 0), None
    )
    descendant_run_count = bulk_descendant_run_counts(db, [run.id])[run.id]
    created_by = bulk_users(db, [run.created_by_user_id]).get(run.created_by_user_id)
    asset_group = bulk_asset_groups(db, [run.asset_group_id]).get(run.asset_group_id)
    return RunDetail(
        **run_summary_fields(
            run,
            outputs,
            input_count,
            primary_parent_asset_id,
            descendant_run_count,
            created_by,
            asset_group,
        ),
        deployment=run.deployment,
        provider_request_id=run.provider_request_id,
        inputs=[RunInputRef(asset_id=i.asset_id, role=i.role, position=i.position) for i in inputs],
    )


@router.post("", response_model=RunCreateResponse, status_code=202, operation_id="create_run")
def create_run(
    body: RunCreateRequest,
    db: Session = Depends(get_session),
    registry: ProviderRegistry = Depends(get_registry),
    runner: Runner = Depends(get_runner),
    settings: Settings = Depends(get_settings),
    user: CurrentUser = Depends(require_user),
) -> RunCreateResponse:
    # provider を引く(省略時は主プロバイダー)。未知の provider は 422(ADR-0013)。
    provider_name = body.provider or registry.primary
    provider = registry.get(provider_name)
    if provider is None:
        raise HTTPException(
            status_code=422,
            detail=t("runs.unknownProvider", provider=provider_name),
        )

    # ADR-0012: キーが無い状態で Run を作らない(Run は追記のみの証跡のため、実行して
    # missingApiKey で failed にするより、作る前に断る方が記録を汚さない)。
    if getattr(provider, "requires_api_key", False):
        api_key, _source = api_key_domain.resolve_key(settings)
        if not api_key:
            raise HTTPException(
                status_code=409,
                detail=t("openai.missingApiKey"),
            )

    # ADR-0013: 接続できない等でプロバイダーが使えない場合も、Run を作らずに 409 で断る。
    available, unavailable_reason = provider.availability()
    if not available:
        raise HTTPException(
            status_code=409,
            detail=unavailable_reason or t("runs.providerUnavailable", label=provider.label),
        )

    # ADR-0022: 出力を入れるグループ。存在しない・削除済みなら Run を作らずに 404。
    if (
        body.asset_group_id is not None
        and get_active_group_or_none(db, body.asset_group_id) is None
    ):
        raise HTTPException(status_code=404, detail=t("assetGroups.notFound"))

    caps = provider.capabilities()

    input_metas: list[RunInputMeta] = []
    for item in body.inputs:
        asset = db.get(Asset, item.asset_id)
        if asset is None or asset.deleted_at is not None:
            raise HTTPException(
                status_code=422,
                detail=t("runs.assetNotFound", id=item.asset_id),
            )
        input_metas.append(
            RunInputMeta(
                asset_id=item.asset_id,
                role=item.role,
                position=item.position,
                width=asset.width,
                height=asset.height,
                sha256=asset.sha256,
                mime=asset.mime,
            )
        )

    try:
        validate_run_request(
            caps, body.operation, body.model, body.prompt, body.params, input_metas
        )
    except RunValidationError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    draft = RunDraft(
        operation=body.operation,
        model=body.model,
        prompt=body.prompt,
        params=body.params,
        inputs=input_metas,
    )
    try:
        params = provider.finalize_params(db, draft)
    except RunValidationError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except ProviderUnavailableError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e

    run = Run(
        # レジストリのキー(provider_name)を記録する。runner はこのキーで実行レーンを選ぶ
        # ため、provider.name(自己申告の属性)ではなくこちらを正とする。
        provider=provider_name,
        model=body.model,
        deployment=None,
        operation=body.operation,
        prompt=body.prompt,
        params=params,
        status=RunStatus.QUEUED,
        created_by_user_id=user.id,
        asset_group_id=body.asset_group_id,
    )
    db.add(run)
    db.flush()

    for item in body.inputs:
        db.add(
            RunInput(run_id=run.id, asset_id=item.asset_id, role=item.role, position=item.position)
        )

    db.commit()
    runner.notify()

    return RunCreateResponse(id=run.id, status=run.status)


@router.get("", response_model=RunListResponse, operation_id="list_runs")
def list_runs(
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    db: Session = Depends(get_session),
) -> RunListResponse:
    query = select(Run).where(Run.deleted_at.is_(None))
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
    inputs_map = bulk_input_summary(db, run_ids)
    descendant_map = bulk_descendant_run_counts(db, run_ids)
    users_map = bulk_users(db, [r.created_by_user_id for r in rows])
    groups_map = bulk_asset_groups(db, [r.asset_group_id for r in rows])
    items = [
        RunSummary(
            **run_summary_fields(
                r,
                outputs_map[r.id],
                *inputs_map[r.id],
                descendant_map[r.id],
                users_map.get(r.created_by_user_id),
                groups_map.get(r.asset_group_id),
            )
        )
        for r in rows
    ]

    next_cursor = encode_cursor(rows[-1].queued_at, rows[-1].id) if has_more and rows else None
    return RunListResponse(items=items, next_cursor=next_cursor)


@router.get("/{run_id}", response_model=RunDetail, operation_id="get_run")
def get_run(run_id: uuid.UUID, db: Session = Depends(get_session)) -> RunDetail:
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=t("runs.notFound"))
    return _to_detail(db, run)


@router.delete("/{run_id}", status_code=204, operation_id="delete_run")
def delete_run(run_id: uuid.UUID, db: Session = Depends(get_session)) -> None:
    """論理削除。終了状態(succeeded/failed/canceled)の Run だけ削除できる。

    出力 Asset(`produced_by_run_id = run_id` で未削除のもの)も同一トランザクションで
    論理削除する(ADR-0008「削除」追加分)。`run`/`run_input` の他の列は変更しない。
    """
    run = db.get(Run, run_id)
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
) -> RunCancelResponse:
    run = db.get(Run, run_id)
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
