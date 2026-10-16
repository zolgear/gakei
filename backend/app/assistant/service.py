"""1回の発言の処理(ADR-0048 7章・8章・9章)。

利用者の発言を保存した後、LLM の呼び出しと道具の実行を、道具の呼び出しが無くなるまで
(または上限に達するまで)繰り返す。LLM の1回の応答ごとにアシスタントのメッセージを、道具の
結果ごとに tool のメッセージを保存し、SSE のイベント(`app/domain/schemas.py` の
`Assistant*Event`)を返す。

- 1時間の上限: LLM のリクエストのたびに、利用者ごとの記録(`AssistantRuntime.requests`)で
  確かめる。達したら待たせずに止める。
- 道具の回数: 1回の発言で `MAX_TOOL_CALLS_PER_MESSAGE` 回まで。超えた呼び出しは実行せずに
  エラーの結果を返し、そこで止める。
- 切断(止めるボタン): ジェネレーターが閉じられたら、LLM の呼び出しをやめ、そこまでの本文を
  `status = "stopped"` で保存し、この発言で作った Run のうち `queued` のものを取り消す。
- 履歴: 直近から数えて `HISTORY_MAX_CHARS` 文字まで送る。発言の途中で切らない。
- 送る前のチェック: LLM を呼ぶたびに(道具の往復を含む)、Mod の `ai.before_send`(目的
  `assistant`。ADR-0051 2章)を呼ぶ。拒否されたら、その回の応答を止めて理由をエラーで出す。
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

import anyio
import anyio.to_thread
from pydantic import BaseModel

from app.assistant.engine import (
    AssistantEngineError,
    EngineRequest,
    LlmImage,
    LlmMessage,
    TextDelta,
    ToolCall,
)
from app.assistant.prompts import build_system_prompt
from app.assistant.runtime import AssistantRuntime, user_key
from app.assistant.tools import (
    ToolContext,
    ToolOutcome,
    cancel_queued_runs,
    execute_tool,
    publish_canceled,
    tool_specs,
)
from app.auth.identity import CurrentUser
from app.domain import assistant as assistant_domain
from app.domain import llm_connections
from app.domain.assistant_settings import AssistantConfig
from app.domain.llm_connections import LlmTarget
from app.domain.models import Asset, AssistantConversation, AssistantMessage
from app.domain.schemas import (
    AssistantDoneEvent,
    AssistantErrorEvent,
    AssistantFormState,
    AssistantMessageEvent,
    AssistantRunCreatedEvent,
    AssistantTextDeltaEvent,
    AssistantToolCallEvent,
    AssistantToolResultEvent,
    AssistantUserMessageEvent,
)
from app.i18n import set_locale, t
from app.mods import events as mods_events
from app.mods.api import AI_BEFORE_SEND, BeforeSendSubject, TextPart
from app.mods.runtime import Denial

logger = logging.getLogger(__name__)

# 1回の発言で道具を呼ぶ回数の上限(7章・9章)。
MAX_TOOL_CALLS_PER_MESSAGE = 40
# LLM に送る履歴の上限(文字)。直近から数える(3章)。
HISTORY_MAX_CHARS = 60_000
# 1つの道具の結果を LLM に送る上限(文字)。
TOOL_RESULT_MAX_CHARS = 20_000


async def _in_thread[T](fn: Callable[[], T]) -> T:
    return await anyio.to_thread.run_sync(fn)


def sse(event: BaseModel) -> str:
    """SSE の1イベント(`event:` と `data:`)。"""
    payload = event.model_dump(mode="json")
    data = json.dumps(payload, ensure_ascii=False)
    return f"event: {payload['type']}\ndata: {data}\n\n"


# -- 履歴 ---------------------------------------------------------------------------


def _user_text(content: dict[str, Any]) -> str:
    text = str(content.get("text", ""))
    attachments = content.get("attachments") or []
    if attachments:
        text += "\n\n[Attached images (asset ids): " + ", ".join(map(str, attachments)) + "]"
    return text


def _tool_text(content: dict[str, Any]) -> str:
    payload = {"ok": bool(content.get("ok")), **(content.get("result") or {})}
    if content.get("undone_at"):
        payload["undone_by_user"] = True
    text = json.dumps(payload, ensure_ascii=False, default=str)
    if len(text) > TOOL_RESULT_MAX_CHARS:
        text = text[:TOOL_RESULT_MAX_CHARS] + "...(truncated)"
    return text


def _to_llm(row: AssistantMessage) -> LlmMessage | None:
    content = row.content or {}
    if row.role == assistant_domain.ROLE_USER:
        return LlmMessage(role="user", text=_user_text(content))
    if row.role == assistant_domain.ROLE_ASSISTANT:
        calls = [
            ToolCall(
                id=str(call.get("id", "")),
                name=str(call.get("name", "")),
                arguments=call.get("arguments") or {},
            )
            for call in content.get("tool_calls") or []
            if isinstance(call, dict)
        ]
        return LlmMessage(role="assistant", text=str(content.get("text", "")), tool_calls=calls)
    if row.role == assistant_domain.ROLE_TOOL:
        return LlmMessage(
            role="tool",
            text=_tool_text(content),
            tool_call_id=str(content.get("tool_call_id", "")),
            name=str(content.get("name", "")),
        )
    return None


def _pair_tool_calls(
    items: list[tuple[uuid.UUID, LlmMessage]],
) -> list[tuple[uuid.UUID, LlmMessage]]:
    """道具の呼び出しと結果の組をそろえる(止めたときに片方だけ残ることがあるため)。

    結果の無い呼び出しは消し、直前の応答に対応する呼び出しの無い結果は捨てる。本文も呼び出しも
    無くなったアシスタントのメッセージは捨てる。
    """
    result: list[tuple[uuid.UUID, LlmMessage]] = []
    open_index: int | None = None
    answered: set[str] = set()

    def close_open() -> None:
        nonlocal open_index
        if open_index is None:
            return
        message = result[open_index][1]
        message.tool_calls = [c for c in message.tool_calls if c.id in answered]
        open_index = None

    for message_id, message in items:
        if message.role == "tool":
            if open_index is None:
                continue
            calls = {c.id for c in result[open_index][1].tool_calls}
            if message.tool_call_id in calls and message.tool_call_id not in answered:
                answered.add(message.tool_call_id or "")
                result.append((message_id, message))
            continue
        close_open()
        result.append((message_id, message))
        if message.role == "assistant" and message.tool_calls:
            open_index = len(result) - 1
            answered = set()
    close_open()
    return [
        (message_id, message)
        for message_id, message in result
        if not (message.role == "assistant" and not message.text and not message.tool_calls)
    ]


def _size(message: LlmMessage) -> int:
    size = len(message.text)
    for call in message.tool_calls:
        size += len(call.name) + len(call.arguments_json())
    return size


def build_history(
    rows: list[AssistantMessage],
    current_user_message_id: uuid.UUID,
    images: dict[uuid.UUID, list[LlmImage]],
    max_chars: int = HISTORY_MAX_CHARS,
) -> list[LlmMessage]:
    """保存したメッセージから LLM に送る履歴を作る。`images` はこの発言の間だけ送る画像
    (添付と `view_assets` の結果。メッセージの id ごと)。"""
    converted: list[tuple[uuid.UUID, LlmMessage]] = []
    for row in rows:
        message = _to_llm(row)
        if message is not None:
            converted.append((row.id, message))
    paired = _pair_tool_calls(converted)

    # 今の発言より前は、利用者の発言の区切りで、上限に収まる範囲だけ残す。
    current = next(
        (i for i, (message_id, _) in enumerate(paired) if message_id == current_user_message_id),
        0,
    )
    start = current
    total = sum(_size(m) for _, m in paired[current:])
    for index in range(current - 1, -1, -1):
        total += _size(paired[index][1])
        if total > max_chars:
            break
        if paired[index][1].role == "user":
            start = index

    history: list[LlmMessage] = []
    for message_id, message in paired[start:]:
        message.images = list(images.get(message_id, []))
        history.append(message)
    return history


# -- 1回の発言 -------------------------------------------------------------------------


@dataclass
class TurnInput:
    conversation_id: uuid.UUID
    user: CurrentUser
    config: AssistantConfig
    target: LlmTarget | None
    locale: str
    user_message: AssistantMessage
    form: AssistantFormState | None = None
    attachment_images: list[LlmImage] = field(default_factory=list)


class AssistantTurn:
    """1回の発言の処理。`events()` が SSE の文字列を返す。閉じられたら止める(9章)。"""

    def __init__(self, state: Any, runtime: AssistantRuntime, turn: TurnInput) -> None:
        self.state = state
        self.runtime = runtime
        self.turn = turn
        self.tool_ctx = ToolContext(state=state, user=turn.user, config=turn.config, form=turn.form)
        self.images: dict[uuid.UUID, list[LlmImage]] = {}
        if turn.attachment_images:
            self.images[turn.user_message.id] = list(turn.attachment_images)
        # 保存していない本文(切断されたときに保存する)。
        self._pending_text: list[str] = []
        self._finished = False

    def _session(self) -> Any:
        return self.state.session_factory()

    def _append(self, role: str, content: dict[str, Any]) -> AssistantMessage:
        with self._session() as db:
            message = assistant_domain.append_message(db, self.turn.conversation_id, role, content)
            db.expunge(message)
            return message

    def _history(self) -> list[LlmMessage]:
        with self._session() as db:
            rows = assistant_domain.list_messages(db, self.turn.conversation_id)
        return build_history(rows, self.turn.user_message.id, self.images)

    def _conversation_response(self) -> Any:
        with self._session() as db:
            conversation = db.get(AssistantConversation, self.turn.conversation_id)
            assert conversation is not None
            return assistant_domain.to_conversation_response(conversation)

    async def events(self) -> AsyncIterator[str]:
        set_locale(self.turn.locale)  # type: ignore[arg-type]
        try:
            async for chunk in self._run():
                yield chunk
            self._finished = True
        finally:
            if not self._finished:
                self._on_stopped()
            self.runtime.end(self.turn.conversation_id)

    def _on_stopped(self) -> None:
        """切断されたときの後始末(同期で行う。取り消しの通知だけは shield して送る)。"""
        try:
            if self._pending_text:
                self._append(
                    assistant_domain.ROLE_ASSISTANT,
                    {
                        "text": "".join(self._pending_text),
                        "tool_calls": [],
                        "status": "stopped",
                        "error": None,
                    },
                )
                self._pending_text = []
            canceled = cancel_queued_runs(self.state, list(self.tool_ctx.runs_this_turn))
        except Exception:  # noqa: BLE001 - 後始末の失敗で切断の処理を止めない
            logger.exception("アシスタントの発言を止めたときの後始末に失敗しました")
            return
        if canceled:
            self._schedule_publish(canceled)

    def _schedule_publish(self, run_ids: list[uuid.UUID]) -> None:
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(publish_canceled(self.state, run_ids))
        _background.add(task)
        task.add_done_callback(_background.discard)

    async def _run(self) -> AsyncIterator[str]:
        turn = self.turn
        user_message_view = assistant_domain.to_message_response(turn.user_message)
        yield sse(AssistantUserMessageEvent(message=user_message_view))

        system = build_system_prompt(turn.locale)
        tools = tool_specs()
        tool_calls_total = 0
        status = "complete"
        key = user_key(turn.user.id)

        while True:
            if not self.runtime.requests.try_acquire(key, turn.config.hourly_request_limit):
                yield sse(
                    AssistantErrorEvent(
                        code="rate_limited",
                        message=t(
                            "assistant.hourlyLimitReached",
                            limit=turn.config.hourly_request_limit,
                        ),
                    )
                )
                status = "limit"
                break

            history = await _in_thread(self._history)
            request = EngineRequest(
                target=turn.target,
                system=system,
                messages=history,
                tools=tools,
                locale=turn.locale,
            )
            denial = await self._check_before_send(request)
            if denial is not None:
                yield sse(AssistantErrorEvent(code="hook_denied", message=denial.message))
                status = "error"
                break
            calls: list[ToolCall] = []
            self._pending_text = []
            error: str | None = None
            try:
                async for event in self.runtime.engine.stream(request):
                    if isinstance(event, TextDelta):
                        self._pending_text.append(event.text)
                        yield sse(AssistantTextDeltaEvent(text=event.text))
                    else:
                        calls.append(event)
                        yield sse(
                            AssistantToolCallEvent(
                                tool_call_id=event.id, name=event.name, arguments=event.arguments
                            )
                        )
            except AssistantEngineError as e:
                error = str(e)
            except Exception as e:  # noqa: BLE001 - 想定外の失敗も利用者に伝えて止める
                logger.exception("アシスタントの LLM の呼び出しに失敗しました")
                error = f"{e.__class__.__name__}: {e}"

            text = "".join(self._pending_text)
            self._pending_text = []
            if error is not None:
                if text:
                    error_content = {
                        "text": text,
                        "tool_calls": [],
                        "status": "error",
                        "error": error,
                    }
                    saved = await _in_thread(
                        lambda c=error_content: self._append(assistant_domain.ROLE_ASSISTANT, c)
                    )
                    yield sse(
                        AssistantMessageEvent(message=assistant_domain.to_message_response(saved))
                    )
                yield sse(
                    AssistantErrorEvent(
                        code="llm_error", message=t("assistant.llmError", error=error)
                    )
                )
                status = "error"
                break

            content = {
                "text": text,
                "tool_calls": [
                    {"id": c.id, "name": c.name, "arguments": c.arguments} for c in calls
                ],
                "status": "complete",
                "error": None,
            }
            saved = await _in_thread(
                lambda c=content: self._append(assistant_domain.ROLE_ASSISTANT, c)
            )
            yield sse(AssistantMessageEvent(message=assistant_domain.to_message_response(saved)))
            if not calls:
                break

            limit_hit = False
            for call in calls:
                if tool_calls_total >= MAX_TOOL_CALLS_PER_MESSAGE:
                    limit_hit = True
                    outcome = ToolOutcome(
                        ok=False,
                        result={
                            "error": t(
                                "assistant.toolLimitReached", limit=MAX_TOOL_CALLS_PER_MESSAGE
                            )
                        },
                    )
                else:
                    tool_calls_total += 1
                    outcome = await self._execute(call)
                tool_content = {
                    "tool_call_id": call.id,
                    "name": call.name,
                    "ok": outcome.ok,
                    "result": outcome.result,
                    "run_ids": [str(run_id) for run_id in outcome.run_ids],
                    "undo": outcome.undo,
                    "undone_at": None,
                }
                tool_message = await _in_thread(
                    lambda content=tool_content: self._append(assistant_domain.ROLE_TOOL, content)
                )
                if outcome.images:
                    self.images[tool_message.id] = outcome.images
                yield sse(
                    AssistantToolResultEvent(
                        message=assistant_domain.to_message_response(tool_message)
                    )
                )
                for run_id in outcome.run_ids:
                    yield sse(
                        AssistantRunCreatedEvent(
                            run_id=run_id, tool_call_id=call.id, message_id=tool_message.id
                        )
                    )
            if limit_hit:
                yield sse(
                    AssistantErrorEvent(
                        code="tool_limit",
                        message=t("assistant.toolLimitReached", limit=MAX_TOOL_CALLS_PER_MESSAGE),
                    )
                )
                status = "limit"
                break

        conversation = await _in_thread(self._conversation_response)
        yield sse(AssistantDoneEvent(status=status, conversation=conversation))  # type: ignore[arg-type]

    async def _check_before_send(self, request: EngineRequest) -> Denial | None:
        """LLM を呼ぶ直前の `ai.before_send`(ADR-0051)。有効な購読が無ければ何もしない。"""
        mods = getattr(self.state, "mods", None)
        if mods is None or not mods.wants(AI_BEFORE_SEND, purpose="assistant"):
            return None
        turn = self.turn

        def build() -> BeforeSendSubject:
            with self._session() as db:
                connections = llm_connections.load(db)
                connection = llm_connections.find_connection(
                    connections, turn.config.connection_id
                ) or llm_connections.ConnectionConfig(
                    id=turn.config.connection_id, name="", base_url=None
                )
                base_url = (
                    turn.target.base_url
                    if turn.target is not None
                    else llm_connections.display_base_url(connection, self.state.settings)
                )
                text = [TextPart(role="system", text=request.system)]
                image_ids: list[uuid.UUID] = []
                for message in request.messages:
                    if message.text:
                        text.append(TextPart(role=message.role, text=message.text))
                    for image in message.images:
                        try:
                            image_ids.append(uuid.UUID(image.label))
                        except ValueError:
                            continue
                images = []
                for asset_id in dict.fromkeys(image_ids):
                    asset = db.get(Asset, asset_id)
                    if asset is not None:
                        images.append(mods_events.image_ref(asset, "attachment"))
                return BeforeSendSubject(
                    purpose="assistant",
                    target=mods_events.llm_target(
                        db,
                        self.state.settings,
                        connection,
                        model=turn.target.model if turn.target is not None else turn.config.model,
                        base_url=base_url,
                    ),
                    text=text,
                    params={
                        "model": turn.config.model,
                        "tools": [tool.name for tool in request.tools],
                        "conversation_id": str(turn.conversation_id),
                    },
                    images=images,
                )

        subject = await _in_thread(build)
        return await mods.before_send(subject, actor=mods_events.actor_from_user(turn.user))

    async def _execute(self, call: ToolCall) -> ToolOutcome:
        try:
            return await execute_tool(self.tool_ctx, call)
        except Exception as e:  # noqa: BLE001 - 道具の想定外の失敗は結果として LLM に返す
            logger.exception("アシスタントの道具 %s に失敗しました", call.name)
            return ToolOutcome(
                ok=False, result={"error": f"Internal error: {e.__class__.__name__}"}
            )


_background: set[Any] = set()
