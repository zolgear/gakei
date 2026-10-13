"""生成のアシスタントの LLM エンジン(ADR-0048 7章)。実 API は呼ばず、SDK のクライアントを
差し替えて、Responses と Chat Completions の両方で tool calling とストリーミングを確かめる。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from app.assistant.engine import (
    AssistantEngineError,
    EngineRequest,
    FakeAssistantEngine,
    LlmImage,
    LlmMessage,
    OpenAIAssistantEngine,
    TextDelta,
    ToolCall,
    ToolSpec,
    parse_arguments,
    to_chat_messages,
    to_responses_input,
)
from app.assistant.prompts import build_system_prompt
from app.assistant.tools import TOOLS, parameters_schema, tool_specs
from app.domain.llm_connections import LlmTarget


class _Stream:
    """SDK の AsyncStream の代わり(非同期に反復でき、close できる)。"""

    def __init__(self, events: list[Any]) -> None:
        self._events = events
        self.closed = False

    def __aiter__(self) -> Any:
        async def gen() -> Any:
            for event in self._events:
                yield event

        return gen()

    async def close(self) -> None:
        self.closed = True


class _Client:
    def __init__(self, events: list[Any]) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.stream = _Stream(events)

        async def responses_create(**kwargs: Any) -> Any:
            self.calls.append(("responses", kwargs))
            return self.stream

        async def chat_create(**kwargs: Any) -> Any:
            self.calls.append(("chat", kwargs))
            return self.stream

        self.responses = SimpleNamespace(create=responses_create)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=chat_create))


def _engine(client: _Client) -> OpenAIAssistantEngine:
    engine = OpenAIAssistantEngine()
    engine._cache = SimpleNamespace(get=lambda target: client)  # type: ignore[assignment]
    return engine


def _target(style: str = "responses", api_key: str | None = "sk-test") -> LlmTarget:
    return LlmTarget(
        connection_id="c", model="m-1", api_key=api_key, base_url=None, api_style=style
    )  # type: ignore[arg-type]


def _request(target: LlmTarget | None, messages: list[LlmMessage] | None = None) -> EngineRequest:
    return EngineRequest(
        target=target,
        system="SYSTEM",
        messages=messages or [LlmMessage(role="user", text="hi")],
        tools=[ToolSpec("get_form_context", "desc", {"type": "object", "properties": {}})],
    )


async def _collect(engine: Any, request: EngineRequest) -> list[Any]:
    return [event async for event in engine.stream(request)]


def test_responses_stream_text_and_tool_call() -> None:
    events = [
        SimpleNamespace(type="response.created"),
        SimpleNamespace(type="response.output_text.delta", delta="こん"),
        SimpleNamespace(type="response.output_text.delta", delta="にちは"),
        SimpleNamespace(
            type="response.output_item.done",
            item=SimpleNamespace(
                type="function_call",
                call_id="call_1",
                id="fc_1",
                name="get_form_context",
                arguments="{}",
            ),
        ),
        SimpleNamespace(
            type="response.output_item.done", item=SimpleNamespace(type="message", id="m")
        ),
    ]
    client = _Client(events)
    result = asyncio.run(_collect(_engine(client), _request(_target())))
    assert result == [
        TextDelta("こん"),
        TextDelta("にちは"),
        ToolCall(id="call_1", name="get_form_context", arguments={}),
    ]
    style, kwargs = client.calls[0]
    assert style == "responses"
    assert kwargs["model"] == "m-1"
    assert kwargs["stream"] is True
    assert kwargs["instructions"] == "SYSTEM"
    assert kwargs["tools"][0] == {
        "type": "function",
        "name": "get_form_context",
        "description": "desc",
        "parameters": {"type": "object", "properties": {}},
    }
    assert client.stream.closed


def test_responses_failed_event_raises() -> None:
    events = [
        SimpleNamespace(
            type="response.failed",
            response=SimpleNamespace(error=SimpleNamespace(message="model not found")),
        )
    ]
    with pytest.raises(AssistantEngineError, match="model not found"):
        asyncio.run(_collect(_engine(_Client(events)), _request(_target())))


def _chunk(content: str | None = None, tool_calls: list[Any] | None = None) -> Any:
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=content, tool_calls=tool_calls))]
    )


def _tool_part(index: int, id: str | None, name: str | None, arguments: str | None) -> Any:
    return SimpleNamespace(
        index=index, id=id, function=SimpleNamespace(name=name, arguments=arguments)
    )


def test_chat_stream_assembles_tool_call_fragments() -> None:
    events = [
        _chunk("A"),
        _chunk("B"),
        _chunk(tool_calls=[_tool_part(0, "call_x", "propose_prompt", '{"prom')]),
        _chunk(tool_calls=[_tool_part(0, None, None, 'pt": "a cat"}')]),
        _chunk(tool_calls=[_tool_part(1, "call_y", "get_form_context", "")]),
        SimpleNamespace(choices=[]),
    ]
    client = _Client(events)
    result = asyncio.run(_collect(_engine(client), _request(_target("chat"))))
    assert result == [
        TextDelta("A"),
        TextDelta("B"),
        ToolCall(id="call_x", name="propose_prompt", arguments={"prompt": "a cat"}),
        ToolCall(id="call_y", name="get_form_context", arguments={}),
    ]
    style, kwargs = client.calls[0]
    assert style == "chat"
    assert kwargs["messages"][0] == {"role": "system", "content": "SYSTEM"}
    assert kwargs["tools"][0]["function"]["name"] == "get_form_context"


def _bad_request(message: str) -> Any:
    import httpx
    import openai

    response = httpx.Response(400, request=httpx.Request("POST", "http://x/v1/chat/completions"))
    return openai.BadRequestError(message, response=response, body=None)


def test_chat_retries_with_reasoning_none_when_tools_need_it() -> None:
    client = _Client([_chunk("ok")])
    attempts: list[dict[str, Any]] = []

    async def chat_create(**kwargs: Any) -> Any:
        attempts.append(kwargs)
        if "reasoning_effort" not in kwargs:
            raise _bad_request(
                "Function tools with reasoning_effort are not supported for m-1 in "
                "/v1/chat/completions."
            )
        return client.stream

    client.chat = SimpleNamespace(completions=SimpleNamespace(create=chat_create))
    engine = _engine(client)
    assert asyncio.run(_collect(engine, _request(_target("chat")))) == [TextDelta("ok")]
    assert [a.get("reasoning_effort") for a in attempts] == [None, "none"]

    # 一度断られた接続先とモデルは、次から最初に付けて送る。
    attempts.clear()
    client.stream = _Stream([_chunk("again")])
    assert asyncio.run(_collect(engine, _request(_target("chat")))) == [TextDelta("again")]
    assert [a.get("reasoning_effort") for a in attempts] == ["none"]


def test_chat_other_bad_request_is_not_retried() -> None:
    client = _Client([])
    attempts: list[dict[str, Any]] = []

    async def chat_create(**kwargs: Any) -> Any:
        attempts.append(kwargs)
        raise _bad_request("model not found")

    client.chat = SimpleNamespace(completions=SimpleNamespace(create=chat_create))
    with pytest.raises(AssistantEngineError):
        asyncio.run(_collect(_engine(client), _request(_target("chat"))))
    assert len(attempts) == 1


def test_chat_invalid_arguments_are_reported() -> None:
    events = [_chunk(tool_calls=[_tool_part(0, "c", "propose_prompt", "{not json")])]
    result = asyncio.run(_collect(_engine(_Client(events)), _request(_target("chat"))))
    assert result[0].arguments == {}
    assert result[0].arguments_error


def test_missing_key_is_an_error() -> None:
    with pytest.raises(AssistantEngineError):
        asyncio.run(_collect(_engine(_Client([])), _request(_target(api_key=None))))
    with pytest.raises(AssistantEngineError):
        asyncio.run(_collect(_engine(_Client([])), _request(None)))


def _conversation() -> list[LlmMessage]:
    image = LlmImage(data=b"\xff\xd8jpeg", label="asset-1")
    return [
        LlmMessage(role="user", text="look", images=[image]),
        LlmMessage(
            role="assistant",
            text="ok",
            tool_calls=[ToolCall(id="c1", name="view_assets", arguments={"asset_ids": ["x"]})],
        ),
        LlmMessage(
            role="tool",
            text='{"ok": true}',
            tool_call_id="c1",
            name="view_assets",
            images=[LlmImage(data=b"\xff\xd8x", label="x")],
        ),
        LlmMessage(role="assistant", text="done"),
    ]


def test_chat_messages_shape() -> None:
    messages = to_chat_messages("S", _conversation())
    assert [m["role"] for m in messages] == [
        "system",
        "user",
        "assistant",
        "tool",
        "user",
        "assistant",
    ]
    assert messages[1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert messages[2]["tool_calls"][0]["function"]["arguments"] == '{"asset_ids": ["x"]}'
    assert messages[3] == {"role": "tool", "tool_call_id": "c1", "content": '{"ok": true}'}
    # 道具の結果の画像は、結果の後に利用者のメッセージとして足す。
    assert messages[4]["content"][0]["type"] == "text"
    assert messages[4]["content"][1]["type"] == "image_url"


def test_responses_input_shape() -> None:
    items = to_responses_input(_conversation())
    assert items[0]["role"] == "user"
    assert items[0]["content"][1]["type"] == "input_image"
    assert items[1] == {"role": "assistant", "content": "ok"}
    assert items[2]["type"] == "function_call"
    assert items[2]["call_id"] == "c1"
    assert items[3] == {"type": "function_call_output", "call_id": "c1", "output": '{"ok": true}'}
    assert items[4]["role"] == "user"
    assert items[4]["content"][1]["type"] == "input_image"
    assert items[5] == {"role": "assistant", "content": "done"}


def test_fake_engine_follows_commands() -> None:
    engine = FakeAssistantEngine()
    text = '/tool get_form_context {}\n/tool propose_prompt {"prompt": "x"}'
    first = asyncio.run(_collect(engine, _request(None, [LlmMessage(role="user", text=text)])))
    assert first == [ToolCall(id="fake_call_0", name="get_form_context", arguments={})]
    history = [
        LlmMessage(role="user", text=text),
        LlmMessage(role="assistant", tool_calls=first),
        LlmMessage(role="tool", text="{}", tool_call_id="fake_call_0", name="get_form_context"),
    ]
    second = asyncio.run(_collect(engine, _request(None, history)))
    assert second[0].name == "propose_prompt"
    history += [
        LlmMessage(role="assistant", tool_calls=second),
        LlmMessage(role="tool", text="{}", tool_call_id="fake_call_1", name="propose_prompt"),
    ]
    third = asyncio.run(_collect(engine, _request(None, history)))
    assert all(isinstance(e, TextDelta) for e in third)
    assert "2" in "".join(e.text for e in third)


def test_fake_engine_error_command() -> None:
    with pytest.raises(AssistantEngineError, match="boom"):
        asyncio.run(
            _collect(FakeAssistantEngine(), _request(None, [LlmMessage("user", "/error boom")]))
        )


def test_parse_arguments() -> None:
    assert parse_arguments('{"a": 1}') == ({"a": 1}, None)
    assert parse_arguments("") == ({}, None)
    assert parse_arguments("[1]")[1]
    assert parse_arguments("{")[1]


def test_tool_schemas_are_inline_objects() -> None:
    specs = tool_specs()
    assert {s.name for s in specs} == {t.name for t in TOOLS}
    # 削除の道具は作らない(ADR-0048 4章)。
    assert not any("delete" in s.name for s in specs)
    for spec in specs:
        assert spec.parameters["type"] == "object"
        assert "$defs" not in str(spec.parameters)
        assert "$ref" not in str(spec.parameters)
    # 入れ子のモデル(項目)も展開される。
    create = next(s for s in specs if s.name == "create_prompt_set")
    assert create.parameters["properties"]["items"]["items"]["properties"]["text"]["type"] == (
        "string"
    )
    assert parameters_schema(TOOLS[0].args)["properties"] == {}


def test_system_prompt_language_follows_locale() -> None:
    assert "Japanese" in build_system_prompt("ja")
    assert "English" in build_system_prompt("en")
