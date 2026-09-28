"""GET /api/runs/{id}/events の実機テスト。

TestClient は同期実行のため、真に並行した実況(途中経過)をここで再現するのは難しい。
ここでは「接続時点で確定している状態が SSE として届く」ところまでを実際のエンドポイントで確認し、
pub/sub 自体の挙動は test_progress_bus.py で別途検証する(完了条件の代替方針どおり)。
"""

from __future__ import annotations

import asyncio
import json
import uuid

from fastapi.testclient import TestClient

from app.api.events import stream_run_events
from app.auth.identity import LOCAL_ADMIN
from app.domain.models import Run, RunStatus
from tests.conftest import wait_for_run_terminal


def _first_event(stream) -> dict:  # noqa: ANN001
    lines = [line for line in stream.iter_lines() if line.startswith("data:")]
    assert lines, "SSEイベントが1件も届かなかった"
    return json.loads(lines[0][len("data:") :])


def test_sse_delivers_succeeded_status_with_output_asset_ids(client: TestClient) -> None:
    response = client.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": "gpt-image-2.5-sunburst",
            "prompt": "sse smoke test",
            "params": {"n": 1},
        },
    )
    run_id = response.json()["id"]
    detail = wait_for_run_terminal(client, run_id)  # 先に終わらせておく

    with client.stream("GET", f"/api/runs/{run_id}/events") as stream:
        assert stream.status_code == 200
        payload = _first_event(stream)
        assert payload["type"] == "status"
        assert payload["status"] == "succeeded"
        assert payload["output_asset_ids"] == [o["asset_id"] for o in detail["outputs"]]


def test_sse_delivers_failed_status_with_error_message(client: TestClient) -> None:
    response = client.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": "gpt-image-2.5-sunburst",
            "prompt": "[[fail:contentFilter]] sse failure test",
            "params": {"n": 1},
        },
    )
    run_id = response.json()["id"]
    wait_for_run_terminal(client, run_id)

    with client.stream("GET", f"/api/runs/{run_id}/events") as stream:
        assert stream.status_code == 200
        payload = _first_event(stream)
        assert payload["type"] == "status"
        assert payload["status"] == "failed"
        assert payload["error_code"] == "contentFilter"
        assert payload["error_message"]


class _NeverDisconnectedRequest:
    """`stream_run_events` が使うのは `is_disconnected()` だけなので、それだけを持つ代役。"""

    async def is_disconnected(self) -> bool:
        return False


def test_sse_replays_latest_progress_and_partial_when_connecting_mid_run(
    client_no_runner: TestClient,
) -> None:
    """ADR-0013: 実行中の Run に途中から接続した場合(画面遷移やリロードで戻ってきた場合)、
    初期の status イベントの後に、それまでに発行済みの直近の progress / partial
    (出力ごとに最新1件)がすぐ届く(`ProgressBus` が保持分をキューに先詰めして返すため)。

    終了しない SSE を `TestClient.stream()` で開くとレスポンスの完了まで戻ってこないので、
    ここだけは HTTP を介さず、エンドポイントが返すジェネレーターを直接読む。
    """
    response = client_no_runner.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": "gpt-image-2.5-sunburst",
            "prompt": "mid-run reconnect test",
            "params": {"n": 1},
        },
    )
    run_id = uuid.UUID(response.json()["id"])

    # runner を止めているので、直接 DB を触って running 状態を作る。
    session_factory = client_no_runner.app.state.session_factory
    with session_factory() as session:
        run = session.get(Run, run_id)
        assert run is not None
        run.status = RunStatus.RUNNING
        session.commit()

    bus = client_no_runner.app.state.progress_bus

    async def scenario() -> list[dict]:
        # 接続前に発行しておき、「途中から接続」の状況を作る。
        await bus.publish(run_id, {"type": "progress", "value": 3, "max": 10, "node": "KSampler"})
        await bus.publish(run_id, {"type": "partial", "index": 0, "output_index": 0})

        with session_factory() as session:
            streaming = await stream_run_events(
                run_id, _NeverDisconnectedRequest(), session, bus, LOCAL_ADMIN
            )
            events: list[dict] = []
            iterator = streaming.body_iterator.__aiter__()
            try:
                while len(events) < 3:
                    chunk = await asyncio.wait_for(anext(iterator), timeout=5.0)
                    for line in chunk.splitlines():
                        if line.startswith("data:"):
                            events.append(json.loads(line[len("data:") :]))
            finally:
                await iterator.aclose()
        return events

    events = asyncio.run(scenario())

    assert events[0]["type"] == "status"
    assert events[0]["status"] == "running"
    assert {events[1]["type"], events[2]["type"]} == {"progress", "partial"}
    progress_event = next(e for e in events if e["type"] == "progress")
    partial_event = next(e for e in events if e["type"] == "partial")
    assert progress_event["value"] == 3
    assert progress_event["max"] == 10
    assert partial_event["index"] == 0
    assert partial_event["output_index"] == 0
