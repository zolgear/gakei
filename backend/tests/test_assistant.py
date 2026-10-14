"""生成のアシスタント(ADR-0048)の API。Fake のエンジン(`FAKE_PROVIDER=1`)で、会話の CRUD、
ストリームのイベント、各道具、上限、元に戻す、無効時の拒否、管理者設定を確かめる。実 API は
呼ばない。

Fake のエンジンは、発言の `/tool <名前> <JSON>` の行ごとに道具を1つ呼び、最後に決まった本文を
返す(`app/assistant/engine.py`)。
"""

from __future__ import annotations

import io
import json
import uuid
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.assistant.engine import FakeAssistantEngine
from app.assistant.service import MAX_TOOL_CALLS_PER_MESSAGE
from app.domain import llm_connections
from app.domain.models import RUN_ORIGIN_ASSISTANT, Run
from tests.conftest import login_as, make_png_bytes, wait_for_run_terminal

SETTINGS = "/api/settings/assistant"
CONVERSATIONS = "/api/assistant/conversations"


# -- 準備 ------------------------------------------------------------------------


def _enable(client: TestClient, **extra: Any) -> dict:
    response = client.patch(SETTINGS, json={"enabled": True, **extra})
    assert response.status_code == 200, response.text
    return response.json()


def _conversation(client: TestClient, title: str = "") -> str:
    response = client.post(CONVERSATIONS, json={"title": title})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _parse_sse(text: str) -> list[dict]:
    events: list[dict] = []
    for block in text.strip().split("\n\n"):
        event_type = None
        data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                event_type = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data = json.loads(line.removeprefix("data: "))
        assert data is not None and data["type"] == event_type, block
        events.append(data)
    return events


def _send(client: TestClient, conversation_id: str, text: str, **body: Any) -> list[dict]:
    response = client.post(
        f"{CONVERSATIONS}/{conversation_id}/messages", json={"text": text, **body}
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    return _parse_sse(response.text)


def _tool(name: str, args: dict | None = None) -> str:
    return f"/tool {name} {json.dumps(args or {})}"


def _tool_results(events: list[dict]) -> list[dict]:
    return [e["message"] for e in events if e["type"] == "tool_result"]


def _upload(client: TestClient, color: tuple[int, int, int] = (10, 120, 200)) -> str:
    response = client.post(
        "/api/assets",
        files={"file": ("x.png", io.BytesIO(make_png_bytes(64, 48, color)), "image/png")},
        data={"kind": "upload"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _engine(client: TestClient) -> FakeAssistantEngine:
    engine = client.app.state.assistant.engine
    assert isinstance(engine, FakeAssistantEngine)
    return engine


def _assistant_runs(client: TestClient) -> list[Run]:
    with client.app.state.session_factory() as db:
        return list(
            db.execute(select(Run).where(Run.origin == RUN_ORIGIN_ASSISTANT)).scalars().all()
        )


# -- 管理者設定 ----------------------------------------------------------------------


def test_settings_defaults_and_update(client: TestClient) -> None:
    body = client.get(SETTINGS).json()
    assert body["enabled"] is False
    assert body["usable"] is False
    assert body["connection_id"] == "openai"
    assert body["runs_per_message_limit"] == 20
    assert body["hourly_run_limit"] == 60
    assert body["requests_last_hour"] == 0

    body = _enable(
        client,
        model="local-model",
        runs_per_message_limit=3,
        hourly_run_limit=5,
        hourly_request_limit=7,
    )
    assert body["enabled"] is True
    # FAKE_PROVIDER=1 ではキーが無くても使える(ダミーのエンジン)。
    assert body["usable"] is True
    assert (body["model"], body["runs_per_message_limit"], body["hourly_run_limit"]) == (
        "local-model",
        3,
        5,
    )
    assert body["hourly_request_limit"] == 7


def test_settings_validation(client: TestClient) -> None:
    for payload in (
        {"enabled": None},
        {"enabled": "yes"},
        {"connection_id": "nope"},
        {"model": ""},
        {"runs_per_message_limit": -1},
        {"hourly_run_limit": 100000},
        {"hourly_request_limit": 0},
    ):
        response = client.patch(SETTINGS, json=payload)
        assert response.status_code == 422, (payload, response.text)
    # 一部が不正なら何も保存しない。
    response = client.patch(SETTINGS, json={"enabled": True, "model": ""})
    assert response.status_code == 422
    assert client.get(SETTINGS).json()["enabled"] is False


def test_settings_update_requires_admin(client_oidc: TestClient) -> None:
    login_as(client_oidc, "user@example.com")
    assert client_oidc.get(SETTINGS).status_code == 200
    assert client_oidc.patch(SETTINGS, json={"enabled": True}).status_code == 403
    login_as(client_oidc, "admin@example.com")
    assert client_oidc.patch(SETTINGS, json={"enabled": True}).status_code == 200


def test_register_usage_marks_connection_in_use(client: TestClient) -> None:
    base = "/api/settings/llm-connections"
    created = client.post(
        base, json={"name": "local", "base_url": "http://127.0.0.1:11434/v1", "api_style": "chat"}
    )
    assert created.status_code == 201, created.text
    connection_id = created.json()["connections"][-1]["id"]
    # 無効で接続先も選んでいなければ、どの接続先も使っていない。
    with client.app.state.session_factory() as db:
        assert "assistant" not in sum(llm_connections.features_using(db).values(), [])

    _enable(client, connection_id=connection_id)
    connection = next(c for c in client.get(base).json()["connections"] if c["id"] == connection_id)
    assert connection["used_by"] == ["assistant"]
    # 使っている接続先は削除できない。
    response = client.delete(f"{base}/{connection_id}")
    assert response.status_code == 409
    assert "アシスタント" in response.json()["detail"]


def test_enabled_with_default_connection_marks_builtin(client: TestClient) -> None:
    _enable(client)
    builtin = client.get("/api/settings/llm-connections").json()["connections"][0]
    assert "assistant" in builtin["used_by"]


# -- 会話の CRUD --------------------------------------------------------------------


def test_conversation_crud(client: TestClient) -> None:
    first = _conversation(client)
    second = _conversation(client, "名前つき")
    items = client.get(CONVERSATIONS).json()["items"]
    assert [c["id"] for c in items] == [second, first]
    assert items[0]["title"] == "名前つき"

    renamed = client.patch(f"{CONVERSATIONS}/{first}", json={"title": "新しい名前"})
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "新しい名前"
    assert client.patch(f"{CONVERSATIONS}/{first}", json={"title": ""}).status_code == 422

    detail = client.get(f"{CONVERSATIONS}/{first}").json()
    assert detail["title"] == "新しい名前"
    assert detail["messages"] == []

    assert client.delete(f"{CONVERSATIONS}/{first}").status_code == 204
    assert client.get(f"{CONVERSATIONS}/{first}").status_code == 404
    assert client.delete(f"{CONVERSATIONS}/{first}").status_code == 404
    assert [c["id"] for c in client.get(CONVERSATIONS).json()["items"]] == [second]
    assert client.get(f"{CONVERSATIONS}/{uuid.uuid4()}").status_code == 404


def test_conversations_are_private(client_oidc: TestClient) -> None:
    client = client_oidc
    login_as(client, "admin@example.com")
    _enable(client)
    login_as(client, "alice@example.com")
    conversation_id = _conversation(client, "alice")
    events = _send(client, conversation_id, _tool("create_prompt_set", {"name": "alice set"}))
    tool_message_id = _tool_results(events)[0]["id"]

    for who in ("bob@example.com", "admin@example.com"):
        login_as(client, who)
        assert client.get(CONVERSATIONS).json()["items"] == []
        assert client.get(f"{CONVERSATIONS}/{conversation_id}").status_code == 404
        assert (
            client.patch(f"{CONVERSATIONS}/{conversation_id}", json={"title": "x"}).status_code
            == 404
        )
        assert client.delete(f"{CONVERSATIONS}/{conversation_id}").status_code == 404
        response = client.post(
            f"{CONVERSATIONS}/{conversation_id}/messages", json={"text": "hijack"}
        )
        assert response.status_code == 404
        assert client.post(f"/api/assistant/undo/{tool_message_id}").status_code == 404

    login_as(client, "alice@example.com")
    detail = client.get(f"{CONVERSATIONS}/{conversation_id}").json()
    assert detail["title"] == "alice"
    assert len(detail["messages"]) == 4


def test_send_rejected_when_disabled(client: TestClient) -> None:
    conversation_id = _conversation(client)
    response = client.post(f"{CONVERSATIONS}/{conversation_id}/messages", json={"text": "hi"})
    assert response.status_code == 409
    assert client.get(f"{CONVERSATIONS}/{conversation_id}").json()["messages"] == []
    # 存在しない会話は、無効でも 404。
    response = client.post(f"{CONVERSATIONS}/{uuid.uuid4()}/messages", json={"text": "hi"})
    assert response.status_code == 404


# -- ストリーム ----------------------------------------------------------------------


def test_stream_events_and_persistence(client: TestClient) -> None:
    _enable(client)
    conversation_id = _conversation(client)
    events = _send(client, conversation_id, "夕焼けの海のプロンプトを\n作ってください")

    types = [e["type"] for e in events]
    assert types[0] == "user_message"
    assert types[-2:] == ["assistant_message", "done"]
    assert "text_delta" in types
    text = "".join(e["text"] for e in events if e["type"] == "text_delta")
    assert text == "ダミーの応答です。道具を 0 回呼びました。"
    assert events[-2]["message"]["text"] == text
    done = events[-1]
    assert done["status"] == "complete"
    # タイトルは最初の発言の先頭(最初の行)から作る。
    assert done["conversation"]["title"] == "夕焼けの海のプロンプトを"

    detail = client.get(f"{CONVERSATIONS}/{conversation_id}").json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert [m["position"] for m in detail["messages"]] == [0, 1]
    assert detail["messages"][1]["status"] == "complete"

    # 2回目の発言ではタイトルを変えない。履歴は LLM に送られる。
    _send(client, conversation_id, "もう少し明るく")
    assert client.get(f"{CONVERSATIONS}/{conversation_id}").json()["title"] == (
        "夕焼けの海のプロンプトを"
    )
    request = _engine(client).requests[-1]
    assert [m.role for m in request.messages] == ["user", "assistant", "user"]
    assert "Japanese" in request.system


def test_reply_language_follows_locale(client: TestClient) -> None:
    _enable(client)
    conversation_id = _conversation(client)
    events = _send(client, conversation_id, "hello", locale="en")
    text = "".join(e["text"] for e in events if e["type"] == "text_delta")
    assert text.startswith("Fake reply")
    assert "English" in _engine(client).requests[-1].system


def test_llm_error_event(client: TestClient) -> None:
    _enable(client)
    conversation_id = _conversation(client)
    events = _send(client, conversation_id, "/error boom")
    assert events[-2]["type"] == "error"
    assert events[-2]["code"] == "llm_error"
    assert "boom" in events[-2]["message"]
    assert events[-1]["status"] == "error"


def test_tool_call_events(client: TestClient) -> None:
    _enable(client)
    conversation_id = _conversation(client)
    events = _send(client, conversation_id, _tool("propose_prompt", {"prompt": "a red fox"}))
    types = [e["type"] for e in events]
    assert types == [
        "user_message",
        "tool_call",
        "assistant_message",
        "tool_result",
        *["text_delta"] * (types.count("text_delta")),
        "assistant_message",
        "done",
    ]
    call = events[1]
    assert (call["name"], call["arguments"]) == ("propose_prompt", {"prompt": "a red fox"})
    result = _tool_results(events)[0]
    assert result["tool_name"] == "propose_prompt"
    assert result["tool_call_id"] == call["tool_call_id"]
    assert result["ok"] is True
    assert result["result"]["prompt"] == "a red fox"
    assert result["undoable"] is False

    # 次のリクエストには、道具の呼び出しと結果の組が入る。
    request = _engine(client).requests[-1]
    assert [m.role for m in request.messages] == ["user", "assistant", "tool"]
    assert request.messages[1].tool_calls[0].id == request.messages[2].tool_call_id


def test_invalid_tool_arguments_and_unknown_tool(client: TestClient) -> None:
    _enable(client)
    conversation_id = _conversation(client)
    events = _send(
        client,
        conversation_id,
        _tool("propose_prompt", {"prompt": ""}) + "\n" + _tool("delete_prompt_set", {}),
    )
    first, second = _tool_results(events)
    assert first["ok"] is False and "Invalid arguments" in first["result"]["error"]
    assert second["ok"] is False and "Unknown tool" in second["result"]["error"]


def test_busy_conversation_is_rejected(client: TestClient) -> None:
    _enable(client)
    conversation_id = _conversation(client)
    runtime = client.app.state.assistant
    assert runtime.begin(uuid.UUID(conversation_id))
    try:
        response = client.post(f"{CONVERSATIONS}/{conversation_id}/messages", json={"text": "x"})
        assert response.status_code == 409
    finally:
        runtime.end(uuid.UUID(conversation_id))
    _send(client, conversation_id, "x")


# -- 読み取りの道具 ----------------------------------------------------------------------


def test_get_form_context_returns_form(client: TestClient) -> None:
    _enable(client)
    conversation_id = _conversation(client)
    form = {
        "provider": "fake",
        "model": "gpt-image-2.5-flare",
        "operation": "generate",
        "prompt": "a lighthouse",
        "params": {"size": "1024x1024"},
    }
    events = _send(client, conversation_id, _tool("get_form_context"), form=form)
    result = _tool_results(events)[0]["result"]
    assert result["available"] is True
    assert result["prompt"] == "a lighthouse"
    assert result["params"] == {"size": "1024x1024"}

    events = _send(client, conversation_id, _tool("get_form_context"))
    assert _tool_results(events)[0]["result"]["available"] is False


def test_list_prompt_sets_and_capabilities(client: TestClient) -> None:
    _enable(client)
    client.post("/api/prompt-sets", json={"name": "風景", "items": [{"text": "sea"}]})
    conversation_id = _conversation(client)
    events = _send(
        client,
        conversation_id,
        _tool("list_prompt_sets") + "\n" + _tool("get_capabilities", {"provider": "fake"}),
    )
    sets, caps = _tool_results(events)
    assert [s["name"] for s in sets["result"]["prompt_sets"]] == ["風景"]
    assert sets["result"]["prompt_sets"][0]["items"][0]["text"] == "sea"
    assert [p["provider"] for p in caps["result"]["providers"]] == ["fake"]

    events = _send(client, conversation_id, _tool("get_capabilities", {"provider": "nope"}))
    assert _tool_results(events)[0]["ok"] is False


def test_view_assets_sends_images_to_llm(client: TestClient) -> None:
    _enable(client)
    asset_id = _upload(client)
    conversation_id = _conversation(client)
    events = _send(client, conversation_id, _tool("view_assets", {"asset_ids": [asset_id]}))
    result = _tool_results(events)[0]
    assert result["ok"] is True
    assert result["result"]["assets"][0]["asset_id"] == asset_id
    # 画像は DB に保存しない(結果の JSON に base64 が無い)。
    assert "base64" not in json.dumps(result)

    request = _engine(client).requests[-1]
    tool_message = request.messages[-1]
    assert tool_message.role == "tool"
    assert len(tool_message.images) == 1
    assert tool_message.images[0].data[:2] == b"\xff\xd8"  # JPEG
    assert tool_message.images[0].label == asset_id

    # 次の発言では、前の画像は送らない(その発言の間だけ)。
    _send(client, conversation_id, "next")
    assert all(not m.images for m in _engine(client).requests[-1].messages)

    events = _send(
        client, conversation_id, _tool("view_assets", {"asset_ids": [str(uuid.uuid4())]})
    )
    assert _tool_results(events)[0]["ok"] is False


def test_attachments(client: TestClient) -> None:
    _enable(client)
    asset_id = _upload(client)
    conversation_id = _conversation(client)
    events = _send(client, conversation_id, "この画像のプロンプトを", attachments=[asset_id])
    assert events[0]["message"]["attachments"] == [asset_id]
    user_message = _engine(client).requests[-1].messages[0]
    assert len(user_message.images) == 1
    assert asset_id in user_message.text

    url = f"{CONVERSATIONS}/{conversation_id}/messages"
    response = client.post(url, json={"text": "x", "attachments": [str(uuid.uuid4())]})
    assert response.status_code == 404
    response = client.post(url, json={"text": "x", "attachments": [asset_id] * 5})
    assert response.status_code == 422


def test_attachments_of_others_are_not_found(client_oidc: TestClient) -> None:
    client = client_oidc
    login_as(client, "admin@example.com")
    _enable(client)
    login_as(client, "bob@example.com")
    bob_asset = _upload(client)
    login_as(client, "alice@example.com")
    conversation_id = _conversation(client)
    response = client.post(
        f"{CONVERSATIONS}/{conversation_id}/messages",
        json={"text": "x", "attachments": [bob_asset]},
    )
    assert response.status_code == 404
    events = _send(client, conversation_id, _tool("view_assets", {"asset_ids": [bob_asset]}))
    assert _tool_results(events)[0]["ok"] is False


# -- プロンプトセットの書き込みと元に戻す -------------------------------------------------------


def _undo(client: TestClient, message_id: str) -> Any:
    return client.post(f"/api/assistant/undo/{message_id}")


def _sets(client: TestClient) -> dict[str, dict]:
    return {s["id"]: s for s in client.get("/api/prompt-sets").json()["items"]}


def test_create_prompt_set_and_undo(client: TestClient) -> None:
    _enable(client)
    conversation_id = _conversation(client)
    events = _send(
        client,
        conversation_id,
        _tool(
            "create_prompt_set",
            {"name": "海", "items": [{"label": "朝", "text": "sea at dawn"}, {"text": "sea"}]},
        ),
    )
    message = _tool_results(events)[0]
    assert message["ok"] is True and message["undoable"] is True
    set_id = message["result"]["prompt_set_id"]
    assert message["result"]["before"] is None
    assert [i["text"] for i in message["result"]["after"]["items"]] == ["sea at dawn", "sea"]
    assert _sets(client)[set_id]["name"] == "海"

    response = _undo(client, message["id"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["prompt_set"] is None
    assert body["message"]["undoable"] is False
    assert body["message"]["undone_at"] is not None
    # 作成を戻すと論理削除になる。
    assert set_id not in _sets(client)
    # 2回は戻せない。
    assert _undo(client, message["id"]).status_code == 409


def test_create_undo_conflicts_after_change(client: TestClient) -> None:
    _enable(client)
    conversation_id = _conversation(client)
    events = _send(client, conversation_id, _tool("create_prompt_set", {"name": "A"}))
    message = _tool_results(events)[0]
    set_id = message["result"]["prompt_set_id"]
    client.patch(f"/api/prompt-sets/{set_id}", json={"name": "利用者が変えた"})
    response = _undo(client, message["id"])
    assert response.status_code == 409
    assert _sets(client)[set_id]["name"] == "利用者が変えた"
    detail = client.get(f"{CONVERSATIONS}/{conversation_id}").json()
    assert next(m for m in detail["messages"] if m["id"] == message["id"])["undoable"] is True


def test_rename_and_undo(client: TestClient) -> None:
    _enable(client)
    set_id = client.post("/api/prompt-sets", json={"name": "旧名"}).json()["id"]
    conversation_id = _conversation(client)
    events = _send(
        client,
        conversation_id,
        _tool("rename_prompt_set", {"prompt_set_id": set_id, "name": "新名"}),
    )
    message = _tool_results(events)[0]
    assert message["result"]["before"] == {"name": "旧名"}
    assert message["result"]["after"] == {"name": "新名"}
    assert _sets(client)[set_id]["name"] == "新名"
    response = _undo(client, message["id"])
    assert response.status_code == 200
    assert response.json()["prompt_set"]["name"] == "旧名"
    assert _sets(client)[set_id]["name"] == "旧名"


def test_add_items_and_undo(client: TestClient) -> None:
    _enable(client)
    created = client.post("/api/prompt-sets", json={"name": "S", "items": [{"text": "a"}]}).json()
    set_id = created["id"]
    conversation_id = _conversation(client)
    events = _send(
        client,
        conversation_id,
        _tool(
            "add_prompt_set_items",
            {"prompt_set_id": set_id, "items": [{"text": "b"}, {"label": "c", "text": "c"}]},
        ),
    )
    message = _tool_results(events)[0]
    assert [i["position"] for i in message["result"]["after"]["items"]] == [1, 2]
    assert [i["text"] for i in _sets(client)[set_id]["items"]] == ["a", "b", "c"]

    response = _undo(client, message["id"])
    assert response.status_code == 200
    assert [i["text"] for i in response.json()["prompt_set"]["items"]] == ["a"]
    assert [i["text"] for i in _sets(client)[set_id]["items"]] == ["a"]


def test_add_items_undo_conflicts_when_item_edited(client: TestClient) -> None:
    _enable(client)
    set_id = client.post("/api/prompt-sets", json={"name": "S"}).json()["id"]
    conversation_id = _conversation(client)
    events = _send(
        client,
        conversation_id,
        _tool("add_prompt_set_items", {"prompt_set_id": set_id, "items": [{"text": "b"}]}),
    )
    message = _tool_results(events)[0]
    item_id = message["result"]["after"]["items"][0]["id"]
    client.patch(f"/api/prompt-sets/{set_id}/items/{item_id}", json={"text": "edited"})
    assert _undo(client, message["id"]).status_code == 409
    assert [i["text"] for i in _sets(client)[set_id]["items"]] == ["edited"]


def test_update_item_and_undo(client: TestClient) -> None:
    _enable(client)
    created = client.post(
        "/api/prompt-sets",
        json={"name": "S", "items": [{"label": "L", "text": "one"}, {"text": "two"}]},
    ).json()
    set_id = created["id"]
    item_id = created["items"][0]["id"]
    conversation_id = _conversation(client)
    events = _send(
        client,
        conversation_id,
        _tool(
            "update_prompt_set_item",
            {
                "prompt_set_id": set_id,
                "item_id": item_id,
                "text": "ONE",
                "label": "",
                "position": 1,
            },
        ),
    )
    message = _tool_results(events)[0]
    assert message["result"]["before"] == {"label": "L", "text": "one", "position": 0}
    assert message["result"]["after"] == {"label": None, "text": "ONE", "position": 1}
    assert [i["text"] for i in _sets(client)[set_id]["items"]] == ["two", "ONE"]

    response = _undo(client, message["id"])
    assert response.status_code == 200
    items = _sets(client)[set_id]["items"]
    assert [(i["label"], i["text"]) for i in items] == [("L", "one"), (None, "two")]


def test_update_item_undo_conflict(client: TestClient) -> None:
    _enable(client)
    created = client.post("/api/prompt-sets", json={"name": "S", "items": [{"text": "one"}]}).json()
    set_id, item_id = created["id"], created["items"][0]["id"]
    conversation_id = _conversation(client)
    events = _send(
        client,
        conversation_id,
        _tool("update_prompt_set_item", {"prompt_set_id": set_id, "item_id": item_id, "text": "x"}),
    )
    message = _tool_results(events)[0]
    client.patch(f"/api/prompt-sets/{set_id}/items/{item_id}", json={"text": "by user"})
    response = _undo(client, message["id"])
    assert response.status_code == 409
    assert _sets(client)[set_id]["items"][0]["text"] == "by user"


def test_update_item_requires_a_change_and_existing_targets(client: TestClient) -> None:
    _enable(client)
    created = client.post("/api/prompt-sets", json={"name": "S", "items": [{"text": "one"}]}).json()
    conversation_id = _conversation(client)
    events = _send(
        client,
        conversation_id,
        "\n".join(
            [
                _tool(
                    "update_prompt_set_item",
                    {"prompt_set_id": created["id"], "item_id": created["items"][0]["id"]},
                ),
                _tool("rename_prompt_set", {"prompt_set_id": str(uuid.uuid4()), "name": "x"}),
            ]
        ),
    )
    assert [m["ok"] for m in _tool_results(events)] == [False, False]


def test_undo_rejects_non_write_and_unknown(client: TestClient) -> None:
    _enable(client)
    conversation_id = _conversation(client)
    events = _send(client, conversation_id, _tool("propose_prompt", {"prompt": "x"}))
    assert _undo(client, _tool_results(events)[0]["id"]).status_code == 409
    assert _undo(client, events[0]["message"]["id"]).status_code == 409
    assert _undo(client, str(uuid.uuid4())).status_code == 404


# -- 生成 -----------------------------------------------------------------------------


def test_generate_image_uses_form_and_records_origin(client: TestClient) -> None:
    _enable(client)
    conversation_id = _conversation(client)
    form = {
        "provider": "fake",
        "model": "gpt-image-2.5-flare",
        "operation": "generate",
        "prompt": "a lighthouse at dusk",
        "params": {"size": "1024x1024", "quality": "low"},
    }
    events = _send(client, conversation_id, _tool("generate_image", {"count": 2}), form=form)
    message = _tool_results(events)[0]
    assert message["ok"] is True, message
    result = message["result"]
    assert len(result["run_ids"]) == 2
    assert result["prompt"] == "a lighthouse at dusk"
    assert result["params"]["size"] == "1024x1024"
    assert "estimated_cost_usd" in result
    assert message["run_ids"] == result["run_ids"]
    created = [e for e in events if e["type"] == "run_created"]
    assert [e["run_id"] for e in created] == result["run_ids"]
    assert all(e["message_id"] == message["id"] for e in created)

    for run_id in result["run_ids"]:
        detail = wait_for_run_terminal(client, run_id)
        assert detail["status"] == "succeeded"
    runs = _assistant_runs(client)
    assert len(runs) == 2
    assert all(r.origin == "assistant" and r.prompt == "a lighthouse at dusk" for r in runs)


def test_generate_then_get_run_waits(client: TestClient) -> None:
    _enable(client)
    conversation_id = _conversation(client)
    events = _send(client, conversation_id, _tool("generate_image", {"prompt": "a cat"}))
    run_id = _tool_results(events)[0]["result"]["run_ids"][0]
    events = _send(
        client, conversation_id, _tool("get_run", {"run_id": run_id, "wait_seconds": 25})
    )
    result = _tool_results(events)[0]["result"]
    assert result["status"] == "succeeded"
    assert result["origin"] == "assistant"
    assert len(result["outputs"]) == 1
    output = result["outputs"][0]["asset_id"]

    # 出力を次の入力にできる(系列に残る)。
    events = _send(
        client,
        conversation_id,
        _tool(
            "generate_image",
            {"prompt": "make it blue", "operation": "edit", "input_asset_ids": [output]},
        ),
    )
    message = _tool_results(events)[0]
    assert message["ok"] is True, message
    edit_run = wait_for_run_terminal(client, message["result"]["run_ids"][0])
    assert edit_run["inputs"][0]["asset_id"] == output


def test_generate_without_prompt_fails(client: TestClient) -> None:
    _enable(client)
    conversation_id = _conversation(client)
    events = _send(client, conversation_id, _tool("generate_image"))
    message = _tool_results(events)[0]
    assert message["ok"] is False
    assert _assistant_runs(client) == []


def test_runs_per_message_limit(client: TestClient) -> None:
    _enable(client, runs_per_message_limit=2)
    conversation_id = _conversation(client)
    events = _send(
        client,
        conversation_id,
        "\n".join(
            [
                _tool("generate_image", {"prompt": "a", "count": 2}),
                _tool("generate_image", {"prompt": "b"}),
            ]
        ),
    )
    first, second = _tool_results(events)
    assert first["ok"] is True
    assert second["ok"] is False
    assert "Run を作りませんでした" in second["result"]["error"]
    assert len(_assistant_runs(client)) == 2
    # 発言が変われば、また作れる。
    events = _send(client, conversation_id, _tool("generate_image", {"prompt": "c"}))
    assert _tool_results(events)[0]["ok"] is True


def test_hourly_run_limit(client: TestClient) -> None:
    _enable(client, hourly_run_limit=1)
    conversation_id = _conversation(client)
    events = _send(client, conversation_id, _tool("generate_image", {"prompt": "a"}))
    assert _tool_results(events)[0]["ok"] is True
    events = _send(client, conversation_id, _tool("generate_image", {"prompt": "b"}))
    message = _tool_results(events)[0]
    assert message["ok"] is False
    assert "1時間" in message["result"]["error"]
    assert len(_assistant_runs(client)) == 1
    assert client.get(SETTINGS).json()["runs_last_hour"] == 1


def test_hourly_run_limit_zero_stops_generation(client: TestClient) -> None:
    _enable(client, hourly_run_limit=0)
    conversation_id = _conversation(client)
    events = _send(client, conversation_id, _tool("generate_image", {"prompt": "a"}))
    assert _tool_results(events)[0]["ok"] is False
    assert _assistant_runs(client) == []


def test_cancel_run(client_no_runner: TestClient) -> None:
    client = client_no_runner
    _enable(client)
    conversation_id = _conversation(client)
    events = _send(client, conversation_id, _tool("generate_image", {"prompt": "a"}))
    run_id = _tool_results(events)[0]["result"]["run_ids"][0]
    events = _send(client, conversation_id, _tool("cancel_run", {"run_id": run_id}))
    assert _tool_results(events)[0]["ok"] is True
    assert client.get(f"/api/runs/{run_id}").json()["status"] == "canceled"
    # 2回目(既に終わっている)と、アシスタントが作っていない Run は断る。
    events = _send(client, conversation_id, _tool("cancel_run", {"run_id": run_id}))
    assert _tool_results(events)[0]["ok"] is False
    response = client.post(
        "/api/runs",
        json={"operation": "generate", "model": "gpt-image-2.5-flare", "prompt": "ui"},
    )
    ui_run = response.json()["id"]
    events = _send(client, conversation_id, _tool("cancel_run", {"run_id": ui_run}))
    assert _tool_results(events)[0]["ok"] is False
    assert client.get(f"/api/runs/{ui_run}").json()["status"] == "queued"


# -- 上限 -----------------------------------------------------------------------------


def test_tool_call_limit_per_message(client: TestClient) -> None:
    _enable(client)
    conversation_id = _conversation(client)
    events = _send(client, conversation_id, _tool("get_form_context").replace("/tool", "/loop"))
    results = _tool_results(events)
    assert len(results) == MAX_TOOL_CALLS_PER_MESSAGE + 1
    assert all(m["ok"] for m in results[:-1])
    assert results[-1]["ok"] is False
    assert events[-2]["type"] == "error" and events[-2]["code"] == "tool_limit"
    assert events[-1]["status"] == "limit"


def test_hourly_request_limit(client: TestClient) -> None:
    _enable(client, hourly_request_limit=2)
    conversation_id = _conversation(client)
    # 道具を1回呼ぶ発言は LLM を2回呼ぶ。
    events = _send(client, conversation_id, _tool("get_form_context"))
    assert events[-1]["status"] == "complete"
    assert client.get(SETTINGS).json()["requests_last_hour"] == 2
    # 上限に達していれば、送る前に断る(待たせない)。
    response = client.post(f"{CONVERSATIONS}/{conversation_id}/messages", json={"text": "x"})
    assert response.status_code == 429


def test_hourly_request_limit_during_turn(client: TestClient) -> None:
    _enable(client, hourly_request_limit=1)
    conversation_id = _conversation(client)
    events = _send(client, conversation_id, _tool("get_form_context"))
    assert events[-2]["type"] == "error" and events[-2]["code"] == "rate_limited"
    assert events[-1]["status"] == "limit"
