"""生成のアシスタントの1回の発言の処理(ADR-0048 7章・9章)。履歴の組み立てと、切断
(止めるボタン)のときの後始末を、HTTP を介さずに確かめる(TestClient は応答を全部読んでから
返すので、途中で切る操作を再現できない)。"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient

from app.assistant.engine import FakeAssistantEngine
from app.assistant.service import AssistantTurn, TurnInput, build_history
from app.auth.identity import LOCAL_ADMIN
from app.domain import assistant as assistant_domain
from app.domain.assistant_settings import AssistantConfig
from app.domain.models import AssistantMessage, Run


def _row(position: int, role: str, content: dict[str, Any]) -> AssistantMessage:
    return AssistantMessage(
        id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        position=position,
        role=role,
        content=content,
        created_at=datetime.now(UTC),
    )


def _call(call_id: str, name: str = "get_form_context") -> dict[str, Any]:
    return {"id": call_id, "name": name, "arguments": {}}


def _tool(call_id: str, result: dict | None = None) -> dict[str, Any]:
    return {"tool_call_id": call_id, "name": "get_form_context", "ok": True, "result": result or {}}


def test_history_pairs_tool_calls_and_results() -> None:
    rows = [
        _row(0, "user", {"text": "first"}),
        # 止めたので結果の無い呼び出し(c2)が残っている。
        _row(1, "assistant", {"text": "", "tool_calls": [_call("c1"), _call("c2")]}),
        _row(2, "tool", _tool("c1")),
        # 呼び出しの無い結果は捨てる。
        _row(3, "tool", _tool("zz")),
        _row(4, "assistant", {"text": "", "tool_calls": [_call("c3")]}),
        _row(5, "user", {"text": "second", "attachments": ["a-1"]}),
    ]
    history = build_history(rows, rows[-1].id, {})
    assert [m.role for m in history] == ["user", "assistant", "tool", "user"]
    assert [c.id for c in history[1].tool_calls] == ["c1"]
    assert history[2].tool_call_id == "c1"
    assert "a-1" in history[3].text


def test_history_keeps_recent_turns_within_limit() -> None:
    rows = []
    for i in range(10):
        rows.append(_row(2 * i, "user", {"text": f"q{i} " + "x" * 100}))
        rows.append(_row(2 * i + 1, "assistant", {"text": f"a{i} " + "y" * 100}))
    current = _row(20, "user", {"text": "now"})
    rows.append(current)
    history = build_history(rows, current.id, {}, max_chars=500)
    # 発言の区切りで切る(先頭は利用者の発言)。今の発言は必ず入る。
    assert history[0].role == "user"
    assert history[-1].text == "now"
    assert 1 < len(history) < len(rows)
    assert sum(len(m.text) for m in history) <= 500
    # 上限が小さすぎても、今の発言だけは送る。
    assert [m.text for m in build_history(rows, current.id, {}, max_chars=1)] == ["now"]


def test_history_marks_undone_writes() -> None:
    rows = [
        _row(0, "user", {"text": "q"}),
        _row(1, "assistant", {"text": "", "tool_calls": [_call("c1", "create_prompt_set")]}),
        _row(
            2,
            "tool",
            {**_tool("c1", {"prompt_set_id": "s"}), "undone_at": "2026-10-13T00:00:00+00:00"},
        ),
        _row(3, "user", {"text": "next"}),
    ]
    history = build_history(rows, rows[-1].id, {})
    assert json.loads(history[2].text)["undone_by_user"] is True


# -- 切断 -----------------------------------------------------------------------------


def _start_turn(
    client: TestClient, text: str, engine: FakeAssistantEngine
) -> tuple[AssistantTurn, uuid.UUID]:
    state = client.app.state
    runtime = state.assistant
    runtime.engine = engine
    with state.session_factory() as db:
        conversation = assistant_domain.create_conversation(db, LOCAL_ADMIN)
        conversation_id = conversation.id
        message = assistant_domain.append_message(
            db, conversation_id, assistant_domain.ROLE_USER, {"text": text, "attachments": []}
        )
        db.refresh(message)
        db.expunge(message)
    assert runtime.begin(conversation_id)
    turn = AssistantTurn(
        state,
        runtime,
        TurnInput(
            conversation_id=conversation_id,
            user=LOCAL_ADMIN,
            config=AssistantConfig(enabled=True),
            target=None,
            locale="ja",
            user_message=message,
        ),
    )
    return turn, conversation_id


def _messages(client: TestClient, conversation_id: uuid.UUID) -> list[AssistantMessage]:
    with client.app.state.session_factory() as db:
        rows = assistant_domain.list_messages(db, conversation_id)
        for row in rows:
            db.expunge(row)
        return rows


def test_closing_the_stream_saves_partial_text(client: TestClient) -> None:
    turn, conversation_id = _start_turn(client, "hello", FakeAssistantEngine())

    async def scenario() -> None:
        events = turn.events()
        async for chunk in events:
            if "event: text_delta" in chunk:
                break
        await events.aclose()

    client.portal.call(scenario)
    rows = _messages(client, conversation_id)
    assert [r.role for r in rows] == ["user", "assistant"]
    assert rows[1].content["status"] == "stopped"
    assert rows[1].content["text"] == "ダミーの応答です"
    # 処理中の印は外れる(次の発言を送れる)。
    assert client.app.state.assistant.begin(conversation_id)
    client.app.state.assistant.end(conversation_id)


def test_cancel_cancels_queued_runs_of_the_turn(client_no_runner: TestClient) -> None:
    client = client_no_runner
    text = '/tool generate_image {"prompt": "a", "count": 2}\n/loop get_form_context {}'
    turn, conversation_id = _start_turn(client, text, FakeAssistantEngine(chunk_delay=0.01))

    async def scenario() -> list[str]:
        seen: list[str] = []
        created = asyncio.Event()

        async def consume() -> None:
            async for chunk in turn.events():
                seen.append(chunk)
                if "event: run_created" in chunk and sum("run_created" in c for c in seen) == 2:
                    created.set()

        task = asyncio.create_task(consume())
        await asyncio.wait_for(created.wait(), timeout=10)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        # 取り消しの通知を送るタスクを待つ。
        await asyncio.sleep(0.05)
        return seen

    seen = client.portal.call(scenario)
    assert not any("event: done" in c for c in seen)
    with client.app.state.session_factory() as db:
        runs = db.query(Run).filter(Run.origin == "assistant").all()
        assert len(runs) == 2
        assert {r.status for r in runs} == {"canceled"}
    assert client.app.state.assistant.begin(conversation_id)
