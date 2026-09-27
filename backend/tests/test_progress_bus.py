"""worker.progress.ProgressBus の pub/sub 単体テスト。

SSE 経由の「複数イベントを実況で受け取る」ケースは TestClient の同期実行モデルとは相性が
悪いため、この単体テストで代替する。「接続時点の状態が届く」部分は
test_run_events_sse.py で実際の SSE エンドポイントを使って確認する。
"""

from __future__ import annotations

import asyncio
import uuid


def test_publish_delivers_to_subscriber() -> None:
    async def scenario() -> None:
        from app.worker.progress import ProgressBus

        bus = ProgressBus()
        run_id = uuid.uuid4()
        queue = bus.subscribe(run_id)

        await bus.publish(run_id, {"type": "status", "status": "running"})
        await bus.publish(run_id, {"type": "partial", "index": 0})
        await bus.publish(run_id, {"type": "status", "status": "succeeded"})

        events = [queue.get_nowait() for _ in range(3)]
        assert events[0]["status"] == "running"
        assert events[1]["type"] == "partial"
        assert events[2]["status"] == "succeeded"

    asyncio.run(scenario())


def test_unsubscribed_queue_does_not_receive_events() -> None:
    async def scenario() -> None:
        from app.worker.progress import ProgressBus

        bus = ProgressBus()
        run_id = uuid.uuid4()
        queue = bus.subscribe(run_id)
        bus.unsubscribe(run_id, queue)

        await bus.publish(run_id, {"type": "status", "status": "running"})
        assert queue.empty()

    asyncio.run(scenario())


def test_publish_to_unknown_run_id_is_a_noop() -> None:
    async def scenario() -> None:
        from app.worker.progress import ProgressBus

        bus = ProgressBus()
        # 誰も購読していない run_id への publish は何も起きない(例外にならない)。
        await bus.publish(uuid.uuid4(), {"type": "status", "status": "queued"})

    asyncio.run(scenario())


def test_late_subscriber_receives_latest_partial_per_output_and_latest_progress() -> None:
    """画面遷移やリロードで実行中の Run に戻ってきたとき、次のイベントを待たずに
    直近の途中経過(出力ごとに最新の partial 1件、progress 1件)がすぐ届くことを確認する。
    古い partial(同じ output_index の上書き前のもの)は再送されない。"""

    async def scenario() -> None:
        from app.worker.progress import ProgressBus

        bus = ProgressBus()
        run_id = uuid.uuid4()

        await bus.publish(run_id, {"type": "status", "status": "running"})
        await bus.publish(run_id, {"type": "progress", "value": 1, "max": 20, "node": "KSampler"})
        await bus.publish(run_id, {"type": "partial", "index": 0, "output_index": 0})
        await bus.publish(run_id, {"type": "partial", "index": 1, "output_index": 1})
        # output_index=0 を上書き。古い方(index=0)は再送されないはず。
        await bus.publish(run_id, {"type": "partial", "index": 2, "output_index": 0})
        await bus.publish(run_id, {"type": "progress", "value": 5, "max": 20, "node": "KSampler"})

        queue = bus.subscribe(run_id)
        received = [queue.get_nowait() for _ in range(3)]
        assert queue.empty()

        partials = [e for e in received if e["type"] == "partial"]
        progresses = [e for e in received if e["type"] == "progress"]
        assert len(partials) == 2
        assert len(progresses) == 1
        assert {p["output_index"] for p in partials} == {0, 1}
        # output_index=0 は上書き後(index=2)の方だけが残る。
        assert next(p for p in partials if p["output_index"] == 0)["index"] == 2
        assert progresses[0]["value"] == 5

    asyncio.run(scenario())


def test_partial_without_output_index_is_treated_as_output_index_zero() -> None:
    """output_index が None の partial は 0 番として扱い、同じ 0 番の partial と
    衝突(上書き)することを確認する。"""

    async def scenario() -> None:
        from app.worker.progress import ProgressBus

        bus = ProgressBus()
        run_id = uuid.uuid4()

        await bus.publish(run_id, {"type": "partial", "index": 0, "output_index": None})
        await bus.publish(run_id, {"type": "partial", "index": 1, "output_index": 0})

        queue = bus.subscribe(run_id)
        received = [queue.get_nowait() for _ in range(1)]
        assert queue.empty()
        assert received[0]["index"] == 1

    asyncio.run(scenario())


def test_terminal_status_discards_retained_progress_and_partials() -> None:
    """Run 終了後は途中経過ファイルも消えるので、終了イベントの後に subscribe しても
    古い partial / progress は再送されない。"""

    async def scenario() -> None:
        from app.worker.progress import ProgressBus

        bus = ProgressBus()
        run_id = uuid.uuid4()

        await bus.publish(run_id, {"type": "progress", "value": 10, "max": 20, "node": "KSampler"})
        await bus.publish(run_id, {"type": "partial", "index": 0, "output_index": 0})
        await bus.publish(run_id, {"type": "status", "status": "succeeded", "output_asset_ids": []})

        queue = bus.subscribe(run_id)
        assert queue.empty()

    asyncio.run(scenario())
