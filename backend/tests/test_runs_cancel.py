"""cancel の状態遷移。queued のみ canceled にできる。"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.domain.models import Run, RunStatus


def _create_queued_run(client: TestClient) -> str:
    response = client.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": "gpt-image-2.5-sunburst",
            "prompt": "will not run because runner is stopped",
            "params": {"n": 1},
        },
    )
    assert response.status_code == 202, response.text
    return response.json()["id"]


def test_cancel_queued_run_succeeds(client_no_runner: TestClient) -> None:
    run_id = _create_queued_run(client_no_runner)

    response = client_no_runner.get(f"/api/runs/{run_id}")
    assert response.json()["status"] == "queued"

    cancel_response = client_no_runner.post(f"/api/runs/{run_id}/cancel")
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "canceled"

    detail = client_no_runner.get(f"/api/runs/{run_id}").json()
    assert detail["status"] == "canceled"


def test_cancel_running_run_returns_409(client_no_runner: TestClient) -> None:
    run_id = _create_queued_run(client_no_runner)

    # runner を止めているので、直接 DB を触って running 状態を作る。
    session_factory = client_no_runner.app.state.session_factory
    with session_factory() as session:
        run = session.get(Run, uuid.UUID(run_id))
        run.status = RunStatus.RUNNING
        session.commit()

    cancel_response = client_no_runner.post(f"/api/runs/{run_id}/cancel")
    assert cancel_response.status_code == 409


def test_cancel_publishes_canceled_event_to_progress_bus(client_no_runner: TestClient) -> None:
    run_id = _create_queued_run(client_no_runner)

    bus = client_no_runner.app.state.progress_bus
    queue = bus.subscribe(uuid.UUID(run_id))

    cancel_response = client_no_runner.post(f"/api/runs/{run_id}/cancel")
    assert cancel_response.status_code == 200

    event = queue.get_nowait()
    assert event == {"type": "status", "status": "canceled"}
