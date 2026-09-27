"""プロセス内 pub/sub。ADR-0005 の `LISTEN/NOTIFY` に相当する部分をローカルMVPでは
プロセス内の `asyncio.Queue` で代替する(ブラウザから見た SSE の I/F は変わらない)。
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

_TERMINAL_STATUSES = {"succeeded", "failed", "canceled"}


class ProgressBus:
    """run_id ごとに購読者(asyncio.Queue)を持つだけの単純な pub/sub。

    直近の progress / partial は run_id ごとに保持しておき、subscribe した時点で
    キューに先詰めして返す(ADR-0013)。画面遷移やリロードで実行中の Run に戻ってきたとき、
    次のイベントが来るまで何も表示されない(途中経過の実体である
    `data/tmp/partial/{run_id}/` の PNG 自体はまだ残っている)のを防ぐため。
    Run が終了すると worker がそのファイル群を消すので、保持分もここで一緒に捨てる
    (以後の subscribe に古い partial の URL を返しても 404 になるし、持ち続けると
    run_id が増えるだけメモリを食う)。
    """

    def __init__(self) -> None:
        self._subscribers: dict[uuid.UUID, list[asyncio.Queue[Any]]] = {}
        self._latest_progress: dict[uuid.UUID, dict[str, Any]] = {}
        # output_index ごとに最新の1件だけを持つ。フロントの `latestPartialsPerOutput`
        # も出力ごとに最新1枚しか使わないので、これ以上は不要。
        self._latest_partials: dict[uuid.UUID, dict[int, dict[str, Any]]] = {}

    def subscribe(self, run_id: uuid.UUID) -> asyncio.Queue[Any]:
        queue: asyncio.Queue[Any] = asyncio.Queue()
        # 保持分を先に詰める(partial → progress の順。フロントの reducer は
        # イベントの型で状態を更新するだけなので、この2種類の間の順序は結果に影響しない)。
        for partial_event in self._latest_partials.get(run_id, {}).values():
            queue.put_nowait(partial_event)
        progress_event = self._latest_progress.get(run_id)
        if progress_event is not None:
            queue.put_nowait(progress_event)
        self._subscribers.setdefault(run_id, []).append(queue)
        return queue

    def unsubscribe(self, run_id: uuid.UUID, queue: asyncio.Queue[Any]) -> None:
        subscribers = self._subscribers.get(run_id)
        if not subscribers:
            return
        if queue in subscribers:
            subscribers.remove(queue)
        if not subscribers:
            self._subscribers.pop(run_id, None)

    async def publish(self, run_id: uuid.UUID, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "progress":
            self._latest_progress[run_id] = event
        elif event_type == "partial":
            output_index = event.get("output_index")
            self._latest_partials.setdefault(run_id, {})[output_index or 0] = event
        elif event_type == "status" and event.get("status") in _TERMINAL_STATUSES:
            # Run が終わった後は再送する意味がない(ファイルも消える)ので、保持分を破棄する。
            self._latest_progress.pop(run_id, None)
            self._latest_partials.pop(run_id, None)

        for queue in list(self._subscribers.get(run_id, [])):
            await queue.put(event)
