"""画像を LLM に送る前の許可(ADR-0048 14.1)。`view_assets` と `view_result` が画像を読むと、
`permission_request` を送り、応答を開いたまま答えを待つ。答えは `once` / `always` / `deny`。
10 分(テストでは短くする)答えが無い・止めた・切断は「許可しない」。答えは tool のメッセージに残す。

TestClient は応答を全部読んでから返すので、待っている間の答えは、発言の処理と同じイベントループ
(`client.portal`)の中で返す。HTTP の答えの経路(`POST /api/assistant/permissions/{id}`)は、
発言を別のスレッドで送り、待っている間に答えて確かめる。実 API は呼ばない。
"""

from __future__ import annotations

import io
import json
import threading
import time
import uuid
from typing import Any

import anyio
import pytest
from fastapi.testclient import TestClient

from app.assistant import service as service_module
from app.assistant.engine import FakeAssistantEngine
from app.assistant.service import AssistantTurn, TurnInput
from app.auth.identity import LOCAL_ADMIN
from app.domain import assistant as assistant_domain
from app.domain.assistant_settings import AssistantConfig
from app.domain.models import AssistantMessage
from tests.conftest import login_as, make_png_bytes

PREFS = "/api/users/me/preferences"


def _upload(client: TestClient, color: tuple[int, int, int] = (10, 120, 200)) -> str:
    response = client.post(
        "/api/assets",
        files={"file": ("x.png", io.BytesIO(make_png_bytes(32, 32, color)), "image/png")},
        data={"kind": "upload"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _enable(client: TestClient) -> None:
    assert client.patch("/api/settings/assistant", json={"enabled": True}).status_code == 200


def _start_turn(client: TestClient, text: str) -> tuple[AssistantTurn, uuid.UUID]:
    state = client.app.state
    runtime = state.assistant
    runtime.engine = FakeAssistantEngine()
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


def _event(chunk: str) -> dict[str, Any] | None:
    for line in chunk.splitlines():
        if line.startswith("data: "):
            return json.loads(line.removeprefix("data: "))
    return None


def _run_with_answer(client: TestClient, text: str, answer: str | None) -> list[str]:
    """発言を処理し、許可を求められたら `answer` で答える(None なら答えない)。"""
    turn, _conversation_id = _start_turn(client, text)
    registry = client.app.state.assistant.permissions

    async def scenario() -> list[str]:
        seen: list[str] = []
        async for chunk in turn.events():
            seen.append(chunk)
            event = _event(chunk)
            if event and event["type"] == "permission_request" and answer is not None:
                assert registry.answer(event["request_id"], "local", answer)
        return seen

    return client.portal.call(scenario)


def _tool_messages(client: TestClient) -> list[AssistantMessage]:
    with client.app.state.session_factory() as db:
        rows = [
            r
            for r in db.query(AssistantMessage).order_by(AssistantMessage.created_at).all()
            if r.role == "tool"
        ]
        for row in rows:
            db.expunge(row)
        return rows


def _engine(client: TestClient) -> FakeAssistantEngine:
    return client.app.state.assistant.engine


def test_once_sends_the_images_and_records_the_answer(client: TestClient) -> None:
    asset_id = _upload(client)
    seen = _run_with_answer(client, f'/tool view_assets {{"asset_ids": ["{asset_id}"]}}', "once")
    requests = [e for e in map(_event, seen) if e and e["type"] == "permission_request"]
    assert len(requests) == 1
    assert requests[0]["asset_ids"] == [asset_id]
    assert requests[0]["connection_name"] == "OpenAI"

    tool = _tool_messages(client)[-1]
    assert tool.content["permission"]["answer"] == "once"
    assert tool.content["permission"]["reason"] == "user"
    # 画像は送った。
    sent = _engine(client).requests[-1].messages[-1]
    assert sent.role == "tool" and len(sent.images) == 1
    # 次も聞く(1度だけ)。
    seen = _run_with_answer(client, f'/tool view_assets {{"asset_ids": ["{asset_id}"]}}', "once")
    assert any((_event(c) or {}).get("type") == "permission_request" for c in seen)


def test_deny_sends_no_image(client: TestClient) -> None:
    asset_id = _upload(client)
    _run_with_answer(client, f'/tool view_result {{"asset_id": "{asset_id}"}}', "deny")
    tool = _tool_messages(client)[-1]
    assert tool.content["permission"]["answer"] == "deny"
    assert tool.content["result"]["images_sent"] is False
    assert "declined" in tool.content["result"]["permission_note"]
    sent = _engine(client).requests[-1].messages[-1]
    assert sent.role == "tool" and sent.images == []
    assert "declined" in sent.text


def test_timeout_is_deny(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service_module, "PERMISSION_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(service_module, "PERMISSION_KEEPALIVE_SECONDS", 0.1)
    asset_id = _upload(client)
    seen = _run_with_answer(client, f'/tool view_assets {{"asset_ids": ["{asset_id}"]}}', None)
    # 待つ間は SSE のコメント行で接続を保つ。
    assert any(c.startswith(":") for c in seen)
    tool = _tool_messages(client)[-1]
    assert tool.content["permission"]["answer"] == "deny"
    assert tool.content["permission"]["reason"] == "timeout"
    assert client.app.state.assistant.permissions.pending_ids() == []


def test_stopping_while_waiting_is_deny(client: TestClient) -> None:
    asset_id = _upload(client)
    turn, conversation_id = _start_turn(
        client, f'/tool view_assets {{"asset_ids": ["{asset_id}"]}}'
    )

    async def scenario() -> None:
        events = turn.events()
        async for chunk in events:
            if "event: permission_request" in chunk:
                break
        await events.aclose()

    client.portal.call(scenario)
    rows = _messages(client, conversation_id)
    tool = [r for r in rows if r.role == "tool"][-1]
    assert tool.content["permission"]["answer"] == "deny"
    assert tool.content["permission"]["reason"] == "stopped"
    assert client.app.state.assistant.permissions.pending_ids() == []
    # 会話を開き直したときの形(ボタンは出さず答えを出す)。
    view = assistant_domain.to_message_response(tool)
    assert view.image_permission is not None and view.image_permission.answer == "deny"


def test_always_preference_skips_the_question(client: TestClient) -> None:
    asset_id = _upload(client)
    assert client.patch(PREFS, json={"assistant.image_send": "always"}).status_code == 200
    seen = _run_with_answer(client, f'/tool view_assets {{"asset_ids": ["{asset_id}"]}}', None)
    assert not any((_event(c) or {}).get("type") == "permission_request" for c in seen)
    tool = _tool_messages(client)[-1]
    assert "permission" not in tool.content
    assert len(_engine(client).requests[-1].messages[-1].images) == 1


def test_attachments_are_not_asked(client: TestClient) -> None:
    _enable(client)
    asset_id = _upload(client)
    conversation_id = client.post("/api/assistant/conversations", json={"title": ""}).json()["id"]
    response = client.post(
        f"/api/assistant/conversations/{conversation_id}/messages",
        json={"text": "これを見て", "attachments": [asset_id]},
    )
    assert response.status_code == 200
    assert "permission_request" not in response.text


def _send_in_thread(client: TestClient, text: str) -> tuple[threading.Thread, dict[str, Any]]:
    conversation_id = client.post("/api/assistant/conversations", json={"title": ""}).json()["id"]
    box: dict[str, Any] = {}

    def run() -> None:
        box["response"] = client.post(
            f"/api/assistant/conversations/{conversation_id}/messages", json={"text": text}
        )

    thread = threading.Thread(target=run)
    thread.start()
    return thread, box


def _wait_pending(client: TestClient) -> str:
    registry = client.app.state.assistant.permissions
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        pending = registry.pending_ids()
        if pending:
            return pending[0]
        time.sleep(0.02)
    raise TimeoutError("許可の求めが来ませんでした")


def test_answer_endpoint_always_saves_the_preference(client: TestClient) -> None:
    _enable(client)
    asset_id = _upload(client)
    thread, box = _send_in_thread(client, f'/tool view_assets {{"asset_ids": ["{asset_id}"]}}')
    request_id = _wait_pending(client)
    answered = client.post(f"/api/assistant/permissions/{request_id}", json={"answer": "always"})
    assert answered.status_code == 204, answered.text
    thread.join(timeout=30)
    assert box["response"].status_code == 200
    assert "permission_request" in box["response"].text
    assert client.get(PREFS).json()["assistant.image_send"] == "always"
    # 答え済みの要求には答えられない。
    again = client.post(f"/api/assistant/permissions/{request_id}", json={"answer": "deny"})
    assert again.status_code == 404
    assert (
        client.post(
            f"/api/assistant/permissions/{uuid.uuid4()}", json={"answer": "once"}
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/assistant/permissions/{request_id}", json={"answer": "maybe"}
        ).status_code
        == 422
    )


def test_only_the_requester_can_answer(client_oidc: TestClient) -> None:
    client = client_oidc
    login_as(client, "admin@example.com")
    _enable(client)
    login_as(client, "alice@example.com")
    asset_id = _upload(client)
    registry = client.app.state.assistant.permissions

    async def make() -> str:
        return registry.create("someone-else", uuid.uuid4()).id

    request_id = client.portal.call(make)
    response = client.post(f"/api/assistant/permissions/{request_id}", json={"answer": "once"})
    assert response.status_code == 404
    assert registry.owns(request_id, "someone-else")

    async def cleanup() -> None:
        registry.discard(request_id)
        await anyio.sleep(0)

    client.portal.call(cleanup)
    assert asset_id
