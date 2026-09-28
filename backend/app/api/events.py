"""SSE で Run の状態変化と途中経過を流す。"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import require_user
from app.auth.identity import CurrentUser
from app.deps import get_data_dir, get_progress_bus, get_session
from app.domain.models import Asset, Run, RunStatus
from app.domain.schemas import RunEvent
from app.domain.visibility import get_visible_run, sees_everything
from app.i18n import t
from app.worker.progress import ProgressBus

router = APIRouter(prefix="/api/runs", tags=["runs"])

_TERMINAL_STATUSES = {"succeeded", "failed", "canceled"}
_KEEPALIVE_SECONDS = 15.0


def _sse(data: dict[str, Any]) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _initial_event(db: Session, run: Run) -> dict[str, Any]:
    """接続時点の状態を、runner が publish するイベントと同じ形で組み立てる。"""
    event: dict[str, Any] = {"type": "status", "status": run.status}
    if run.status == RunStatus.FAILED:
        event["error_code"] = run.error_code
        event["error_message"] = run.error_message
    elif run.status == RunStatus.SUCCEEDED:
        output_ids = (
            db.execute(
                select(Asset.id)
                .where(Asset.produced_by_run_id == run.id)
                .order_by(Asset.output_index)
            )
            .scalars()
            .all()
        )
        event["output_asset_ids"] = [str(asset_id) for asset_id in output_ids]
    return event


@router.get(
    "/{run_id}/events",
    operation_id="stream_run_events",
    response_model=RunEvent,
    responses={
        200: {
            "description": (
                "Run の状態変化と途中経過を Server-Sent Events (text/event-stream) で流す。"
            ),
            "content": {"text/event-stream": {"schema": {"$ref": "#/components/schemas/RunEvent"}}},
        }
    },
)
async def stream_run_events(
    run_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_session),
    bus: ProgressBus = Depends(get_progress_bus),
    user: CurrentUser = Depends(require_user),
) -> StreamingResponse:
    # ADR-0025: 他人の Run は存在しないものと同じ 404(購読もさせない)。進捗は Run ごとの
    # 購読なので、見える Run のイベントだけが流れる。
    run = get_visible_run(db, user, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=t("runs.notFound"))

    # 先に購読してから最新状態を読む。
    # (先に状態を読んでから購読すると、その間に終了イベントが発行された場合に
    #  クライアントが取りこぼして待ち続けてしまう。)
    queue = bus.subscribe(run_id)
    try:
        db.refresh(run)
        initial_event = _initial_event(db, run)
    except Exception:
        bus.unsubscribe(run_id, queue)
        raise

    async def event_generator() -> AsyncIterator[str]:
        try:
            yield _sse(initial_event)
            if initial_event["status"] in _TERMINAL_STATUSES:
                return

            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=_KEEPALIVE_SECONDS)
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue

                yield _sse(event)
                if event.get("type") == "status" and event.get("status") in _TERMINAL_STATUSES:
                    break
        finally:
            bus.unsubscribe(run_id, queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/{run_id}/partials/{index}", operation_id="get_run_partial")
def get_run_partial(
    run_id: uuid.UUID,
    index: int,
    data_dir: Path = Depends(get_data_dir),
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> FileResponse:
    # ADR-0025: 途中経過画像も、見える Run のものだけ(個人モードは従来どおり Run を引かない)。
    if not sees_everything(user) and get_visible_run(db, user, run_id) is None:
        raise HTTPException(status_code=404, detail=t("runs.partialNotFound"))
    path = data_dir / "tmp" / "partial" / str(run_id) / f"{index}.png"
    if not path.exists():
        raise HTTPException(status_code=404, detail=t("runs.partialNotFound"))
    # L-5(2026-09-27 追記): 途中経過画像はキャッシュさせない(共有キャッシュにも置かせない)。
    return FileResponse(
        path, media_type="image/png", headers={"Cache-Control": "private, no-store"}
    )
