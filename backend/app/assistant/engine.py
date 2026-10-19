"""生成のアシスタントの LLM エンジン(ADR-0048 7章)。

1回の `stream()` が LLM への1リクエストにあたる。応答の本文の差分(`TextDelta`)と、道具の
呼び出し(`ToolCall`)を届いた順に返す。道具を実行して結果を足し、もう一度呼ぶのは呼び出し側
(`app/assistant/service.py`)の仕事。

- `OpenAIAssistantEngine`: OpenAI 互換 API を OpenAI Python SDK で呼ぶ。接続先の API 形式
  (ADR-0032)で、Responses API と Chat Completions を切り替える。どちらも tool calling と
  ストリーミングを使う。クライアントのキャッシュは自動タイトル・タグと同じ作り
  (`app/llm/clients.py`)。
- `FakeAssistantEngine`: `FAKE_PROVIDER=1` とテスト用。決まった応答と道具の呼び出しを返す
  (課金なし)。

画像(添付と `view_assets` の結果)は、メッセージの `images` に JPEG のバイト列で持ち、送る
直前に data URL にする。DB には保存しない(ADR-0004)。道具の結果に画像を直接入れられない
API があるので、道具の結果の画像は、結果の後に利用者のメッセージとして足す。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import openai

from app.domain.llm_connections import LlmTarget
from app.i18n import t
from app.llm.clients import LlmClientCache
from app.llm.errors import safe_api_message

logger = logging.getLogger(__name__)

# 1回の LLM リクエストのタイムアウト(秒)。ストリーミングなので、届き始めるまでの上限に近い。
_TIMEOUT_SECONDS = 180.0

_TOOL_IMAGES_NOTE = "Images returned by the {name} tool (in the order requested): {ids}"


class AssistantEngineError(Exception):
    """LLM の呼び出しに失敗した(利用者に `error` イベントで伝える)。"""


# -- メッセージと道具の型 ----------------------------------------------------------


@dataclass(frozen=True)
class LlmImage:
    """LLM に送る画像(縮めた JPEG)。`label` は何の画像か(Asset ID など)。"""

    data: bytes
    label: str = ""
    mime: str = "image/jpeg"

    def data_url(self) -> str:
        return f"data:{self.mime};base64," + base64.b64encode(self.data).decode("ascii")


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    # 引数の JSON が読めなかったとき、その理由(道具は実行せずにエラーを返す)。
    arguments_error: str | None = None

    def arguments_json(self) -> str:
        return json.dumps(self.arguments, ensure_ascii=False)


@dataclass
class LlmMessage:
    role: Literal["user", "assistant", "tool"]
    text: str = ""
    images: list[LlmImage] = field(default_factory=list)
    # assistant のとき: 呼んだ道具。
    tool_calls: list[ToolCall] = field(default_factory=list)
    # tool のとき: どの呼び出しの結果か。
    tool_call_id: str | None = None
    name: str | None = None


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class EngineRequest:
    target: LlmTarget | None
    system: str
    messages: list[LlmMessage]
    tools: list[ToolSpec]
    locale: str = "ja"


@dataclass(frozen=True)
class TextDelta:
    text: str


EngineEvent = TextDelta | ToolCall


class AssistantEngine(Protocol):
    # 送り先(接続先とキー)が要るか。Fake は要らない。
    requires_target: bool

    def stream(self, request: EngineRequest) -> AsyncIterator[EngineEvent]: ...


def parse_arguments(raw: str | None) -> tuple[dict[str, Any], str | None]:
    """道具の引数の JSON を読む。読めなければ ({}, 理由)。"""
    if not raw or not raw.strip():
        return {}, None
    try:
        value = json.loads(raw)
    except ValueError as e:
        return {}, f"Invalid JSON arguments: {e}"
    if not isinstance(value, dict):
        return {}, "Arguments must be a JSON object."
    return value, None


# -- メッセージの変換 --------------------------------------------------------------


def _image_note(message: LlmMessage) -> str:
    ids = ", ".join(image.label for image in message.images if image.label)
    return _TOOL_IMAGES_NOTE.format(name=message.name or "tool", ids=ids or "-")


def _tool_image_groups(messages: list[LlmMessage]) -> list[tuple[int, list[LlmMessage]]]:
    """連続する tool のメッセージのうち画像を持つものを、その連続の最後の位置ごとにまとめる。"""
    groups: list[tuple[int, list[LlmMessage]]] = []
    pending: list[LlmMessage] = []
    for index, message in enumerate(messages):
        if message.role == "tool":
            if message.images:
                pending.append(message)
            is_last = index + 1 >= len(messages) or messages[index + 1].role != "tool"
            if is_last and pending:
                groups.append((index, pending))
                pending = []
    return groups


def to_chat_messages(system: str, messages: list[LlmMessage]) -> list[dict[str, Any]]:
    """Chat Completions の `messages` にする。"""
    result: list[dict[str, Any]] = [{"role": "system", "content": system}]
    image_groups = dict(_tool_image_groups(messages))
    for index, message in enumerate(messages):
        if message.role == "user":
            if message.images:
                parts: list[dict[str, Any]] = [{"type": "text", "text": message.text}]
                parts.extend(
                    {"type": "image_url", "image_url": {"url": image.data_url()}}
                    for image in message.images
                )
                result.append({"role": "user", "content": parts})
            else:
                result.append({"role": "user", "content": message.text})
        elif message.role == "assistant":
            entry: dict[str, Any] = {"role": "assistant", "content": message.text or None}
            if message.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": call.arguments_json()},
                    }
                    for call in message.tool_calls
                ]
            result.append(entry)
        else:
            result.append(
                {"role": "tool", "tool_call_id": message.tool_call_id, "content": message.text}
            )
        for tool_message in image_groups.get(index, []):
            parts = [{"type": "text", "text": _image_note(tool_message)}]
            parts.extend(
                {"type": "image_url", "image_url": {"url": image.data_url()}}
                for image in tool_message.images
            )
            result.append({"role": "user", "content": parts})
    return result


def to_responses_input(messages: list[LlmMessage]) -> list[dict[str, Any]]:
    """Responses API の `input` にする(`instructions` は別に渡す)。"""
    result: list[dict[str, Any]] = []
    image_groups = dict(_tool_image_groups(messages))
    for index, message in enumerate(messages):
        if message.role == "user":
            parts: list[dict[str, Any]] = [{"type": "input_text", "text": message.text}]
            parts.extend(
                {"type": "input_image", "image_url": image.data_url()} for image in message.images
            )
            result.append({"role": "user", "content": parts})
        elif message.role == "assistant":
            if message.text:
                result.append({"role": "assistant", "content": message.text})
            for call in message.tool_calls:
                result.append(
                    {
                        "type": "function_call",
                        "call_id": call.id,
                        "name": call.name,
                        "arguments": call.arguments_json(),
                    }
                )
        else:
            result.append(
                {
                    "type": "function_call_output",
                    "call_id": message.tool_call_id,
                    "output": message.text,
                }
            )
        for tool_message in image_groups.get(index, []):
            parts = [{"type": "input_text", "text": _image_note(tool_message)}]
            parts.extend(
                {"type": "input_image", "image_url": image.data_url()}
                for image in tool_message.images
            )
            result.append({"role": "user", "content": parts})
    return result


def chat_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        for tool in tools
    ]


def responses_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        }
        for tool in tools
    ]


# -- OpenAI 互換 API --------------------------------------------------------------


class OpenAIAssistantEngine:
    requires_target = True

    def __init__(self) -> None:
        # 会話は同時に複数進むので、キャッシュの上限を超えても使用中のものを捨てにくいよう、
        # 接続先の数(数個)より十分大きい上限のまま使う。
        self._cache = LlmClientCache(timeout=_TIMEOUT_SECONDS)
        # Chat Completions で道具を使うときに `reasoning_effort: "none"` が要る(接続先、モデル)。
        self._chat_needs_no_reasoning: set[tuple[str | None, str]] = set()

    async def stream(self, request: EngineRequest) -> AsyncIterator[EngineEvent]:
        target = request.target
        if target is None:
            raise AssistantEngineError(t("assistant.connectionMissing"))
        if not target.api_key:
            raise AssistantEngineError(t("assistant.apiKeyMissing"))
        client = self._cache.get(target)
        try:
            if target.api_style == "chat":
                async for event in self._stream_chat(client, target, request):
                    yield event
            else:
                async for event in self._stream_responses(client, target, request):
                    yield event
        except openai.APIError as e:
            raise AssistantEngineError(safe_api_message(e)) from e

    async def _stream_chat(
        self, client: Any, target: LlmTarget, request: EngineRequest
    ) -> AsyncIterator[EngineEvent]:
        kwargs: dict[str, Any] = {
            "model": target.model,
            "messages": to_chat_messages(request.system, request.messages),
            "stream": True,
        }
        if request.tools:
            kwargs["tools"] = chat_tools(request.tools)
        stream = await self._create_chat(client, target, kwargs)
        # index ごとに、届いた断片をつなぐ(id、名前、引数の JSON)。
        pending: dict[int, dict[str, str]] = {}
        try:
            async for chunk in stream:
                for choice in getattr(chunk, "choices", None) or []:
                    delta = getattr(choice, "delta", None)
                    if delta is None:
                        continue
                    content = getattr(delta, "content", None)
                    if content:
                        yield TextDelta(content)
                    for part in getattr(delta, "tool_calls", None) or []:
                        index = getattr(part, "index", None) or 0
                        slot = pending.setdefault(index, {"id": "", "name": "", "arguments": ""})
                        if getattr(part, "id", None):
                            slot["id"] = part.id
                        function = getattr(part, "function", None)
                        if function is not None:
                            if getattr(function, "name", None):
                                slot["name"] += function.name
                            if getattr(function, "arguments", None):
                                slot["arguments"] += function.arguments
        finally:
            await _close_stream(stream)
        for index in sorted(pending):
            slot = pending[index]
            arguments, error = parse_arguments(slot["arguments"])
            yield ToolCall(
                id=slot["id"] or f"call_{index}",
                name=slot["name"],
                arguments=arguments,
                arguments_error=error,
            )

    async def _create_chat(self, client: Any, target: LlmTarget, kwargs: dict[str, Any]) -> Any:
        """Chat Completions を呼ぶ。OpenAI の推論モデル(gpt-5.6、GPT-6 系など)は、Chat
        Completions で道具を使うときに `reasoning_effort: "none"` を求めて 400 を返す。ローカルの
        サーバーは知らない引数を断ることがあるので常には付けず、この 400 が返ったときだけ付けて
        送り直す。一度断られた接続先とモデルは覚えておき、次からは最初から付ける。"""
        key = (target.base_url, target.model)
        if "tools" in kwargs and key in self._chat_needs_no_reasoning:
            return await client.chat.completions.create(**kwargs, reasoning_effort="none")
        try:
            return await client.chat.completions.create(**kwargs)
        except openai.BadRequestError as e:
            if "tools" not in kwargs or "reasoning_effort" not in str(e):
                raise
            self._chat_needs_no_reasoning.add(key)
            return await client.chat.completions.create(**kwargs, reasoning_effort="none")

    async def _stream_responses(
        self, client: Any, target: LlmTarget, request: EngineRequest
    ) -> AsyncIterator[EngineEvent]:
        kwargs: dict[str, Any] = {
            "model": target.model,
            "instructions": request.system,
            "input": to_responses_input(request.messages),
            "stream": True,
        }
        if request.tools:
            kwargs["tools"] = responses_tools(request.tools)
        stream = await client.responses.create(**kwargs)
        try:
            async for event in stream:
                kind = getattr(event, "type", "")
                if kind == "response.output_text.delta":
                    delta = getattr(event, "delta", "")
                    if delta:
                        yield TextDelta(delta)
                elif kind == "response.output_item.done":
                    item = getattr(event, "item", None)
                    if getattr(item, "type", None) == "function_call":
                        arguments, error = parse_arguments(getattr(item, "arguments", ""))
                        yield ToolCall(
                            id=getattr(item, "call_id", None) or getattr(item, "id", "") or "",
                            name=getattr(item, "name", ""),
                            arguments=arguments,
                            arguments_error=error,
                        )
                elif kind == "response.failed":
                    response = getattr(event, "response", None)
                    error = getattr(response, "error", None)
                    message = getattr(error, "message", None) or "response.failed"
                    raise AssistantEngineError(message)
                elif kind == "error":
                    raise AssistantEngineError(getattr(event, "message", None) or "error")
        finally:
            await _close_stream(stream)


async def _close_stream(stream: Any) -> None:
    close = getattr(stream, "close", None)
    if close is None:
        return
    try:
        result = close()
        if asyncio.iscoroutine(result):
            await result
    except Exception:  # noqa: BLE001 - 後始末の失敗は無視する
        logger.debug("LLM のストリームを閉じられませんでした", exc_info=True)


# -- FAKE_PROVIDER=1 とテスト用 ------------------------------------------------------

# 利用者の発言の行の書式(Fake だけが読む):
#   /tool <道具の名前> <引数の JSON>   … 1回の応答で、その道具を1つ呼ぶ(行ごとに1応答)
#   /loop <道具の名前> <引数の JSON>   … 以後の応答で、その道具を呼び続ける(回数の上限の確認用)
#   /error <文言>                     … LLM のエラーにする
# 書式の行が無ければ、決まった本文だけを返す。
_COMMAND_RE = re.compile(r"^/(tool|loop|error)\b\s*(.*)$")
_FAKE_CHUNK = 8


@dataclass
class _FakeStep:
    kind: str
    name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    message: str = ""


def _parse_fake_steps(text: str) -> list[_FakeStep]:
    steps: list[_FakeStep] = []
    for line in text.splitlines():
        match = _COMMAND_RE.match(line.strip())
        if not match:
            continue
        kind, rest = match.group(1), match.group(2).strip()
        if kind == "error":
            steps.append(_FakeStep(kind="error", message=rest or "fake error"))
            continue
        name, _, raw = rest.partition(" ")
        arguments, _error = parse_arguments(raw)
        steps.append(_FakeStep(kind=kind, name=name, arguments=arguments))
    return steps


class FakeAssistantEngine:
    """決まった応答を返す。利用者の発言に `/tool` などの行があれば、その道具を呼ぶ。

    今の発言(最後の user のメッセージ)より後の、道具を呼んだ応答の数で、次に何をするかを
    決める(状態を持たない)。最後に本文「ダミー…」を返して終わる。
    """

    requires_target = False

    def __init__(self, *, chunk_delay: float = 0.0) -> None:
        self.chunk_delay = chunk_delay
        # テストで中身を確かめるため、受け取ったリクエストを覚えておく。
        self.requests: list[EngineRequest] = []

    async def stream(self, request: EngineRequest) -> AsyncIterator[EngineEvent]:
        self.requests.append(request)
        last_user = max(
            (i for i, m in enumerate(request.messages) if m.role == "user" and not m.tool_call_id),
            default=-1,
        )
        user_text = request.messages[last_user].text if last_user >= 0 else ""
        after = request.messages[last_user + 1 :] if last_user >= 0 else []
        responses_with_calls = sum(1 for m in after if m.role == "assistant" and m.tool_calls)
        tool_results = sum(1 for m in after if m.role == "tool")
        steps = _parse_fake_steps(user_text)

        step: _FakeStep | None = None
        if responses_with_calls < len(steps):
            step = steps[responses_with_calls]
        elif steps and steps[-1].kind == "loop":
            step = steps[-1]

        if step is not None and step.kind == "error":
            raise AssistantEngineError(step.message)
        if step is not None:
            yield ToolCall(
                id=f"fake_call_{responses_with_calls}",
                name=step.name,
                arguments=dict(step.arguments),
            )
            return

        if request.locale == "en":
            text = f"Fake reply. Tools called: {tool_results}."
        else:
            text = f"ダミーの応答です。道具を {tool_results} 回呼びました。"
        for start in range(0, len(text), _FAKE_CHUNK):
            if self.chunk_delay:
                await asyncio.sleep(self.chunk_delay)
            yield TextDelta(text[start : start + _FAKE_CHUNK])
