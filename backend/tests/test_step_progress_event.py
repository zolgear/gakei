"""ADR-0013: `StepProgressEvent` が SSE の `progress` イベントとして
`ProgressBus` に流れること。

真の並行実況を TestClient 越しに確認するのは難しい(test_run_events_sse.py の方針と同じ)
ため、`Runner._execute_run` を直接呼び、`ProgressBus` の購読キューで確認する。
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import sessionmaker

from app.domain.models import Run, RunStatus
from app.domain.storage import LocalFsStore
from app.providers import openai_spec
from app.providers.base import ProviderCapabilities, RunOutputImage, RunResult, StepProgressEvent
from app.providers.registry import ProviderRegistry
from app.worker.progress import ProgressBus
from app.worker.runner import Runner
from tests.conftest import make_png_bytes
from tests.openai_mock import run_async


class _ProgressEmittingProvider:
    name = "fake"
    label = "Fake"
    requires_api_key = False
    supports_pricing = True

    def capabilities(self) -> ProviderCapabilities:
        return openai_spec.build_capabilities(self.name, self.label)

    def availability(self) -> tuple[bool, str | None]:
        return True, None

    def finalize_params(self, db, draft) -> dict:  # noqa: ANN001
        return dict(draft.params)

    async def execute(self, run, on_progress) -> RunResult:  # noqa: ANN001
        await on_progress(StepProgressEvent(value=3, max=10, node="KSampler"))
        return RunResult(outputs=[RunOutputImage(data=make_png_bytes(), mime="image/png")])


def _insert_running_run(session_factory: sessionmaker) -> Run:
    with session_factory() as session:
        run = Run(
            provider="fake",
            model="gpt-image-2.5-sunburst",
            deployment=None,
            operation="generate",
            prompt="progress test",
            params={"n": 1},
            status=RunStatus.RUNNING,
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        session.expunge(run)
        return run


@run_async
async def test_step_progress_event_is_published_as_progress(
    db_session_factory: sessionmaker, local_store: LocalFsStore
) -> None:
    provider = _ProgressEmittingProvider()
    registry = ProviderRegistry(providers={"fake": provider}, primary="fake")
    bus = ProgressBus()
    runner = Runner(db_session_factory, local_store, registry, bus, local_store.root)

    run = _insert_running_run(db_session_factory)
    queue = bus.subscribe(run.id)

    await runner._execute_run(run, provider)  # noqa: SLF001

    events = []
    while not queue.empty():
        events.append(queue.get_nowait())

    assert {"type": "progress", "value": 3, "max": 10, "node": "KSampler"} in events
    assert any(e.get("type") == "status" and e.get("status") == "succeeded" for e in events)


@run_async
async def test_progress_bus_delivers_progress_event_shape() -> None:
    """`ProgressBus` 自体は型を問わない pub/sub であることの確認(契約上の wire format)。"""
    bus = ProgressBus()
    run_id = uuid.uuid4()
    queue = bus.subscribe(run_id)

    await bus.publish(run_id, {"type": "progress", "value": 1, "max": 5, "node": None})

    event = queue.get_nowait()
    assert event == {"type": "progress", "value": 1, "max": 5, "node": None}
