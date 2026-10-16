"""生成のアシスタントの API(ADR-0048 8章)。

| メソッドとパス | 内容 |
|---|---|
| `GET /api/assistant/conversations` | 本人の会話の一覧(新しい順) |
| `POST /api/assistant/conversations` | 会話を作る |
| `GET /api/assistant/conversations/{id}` | 会話とメッセージ |
| `PATCH /api/assistant/conversations/{id}` | 名前を変える |
| `DELETE /api/assistant/conversations/{id}` | 論理削除 |
| `POST /api/assistant/conversations/{id}/messages` | 発言を送る(応答は `text/event-stream`) |
| `POST /api/assistant/undo/{message_id}` | 書き込みを元に戻す |

見えるのは本人の会話だけ(ADR-0025)。他人の会話は存在しないものと同じ 404。管理者設定
(`/api/settings/assistant`)で無効のときは、発言の送信を 409 で断る(会話の一覧・閲覧・
名前の変更・削除はできる)。
"""

from __future__ import annotations

import uuid
from typing import Any

import anyio.to_thread
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.assistant.engine import LlmImage
from app.assistant.runtime import AssistantRuntime, user_key
from app.assistant.service import AssistantTurn, TurnInput
from app.assistant.tools import (
    ImageWithheldFailure,
    ToolContext,
    ToolFailure,
    load_visible_images,
)
from app.assistant.undo import UndoConflictError, UndoNotAvailableError, undo_message
from app.auth.deps import require_user
from app.auth.identity import CurrentUser
from app.deps import get_session
from app.domain import assistant as domain
from app.domain import assistant_settings
from app.domain.llm_connections import ConnectionNotFoundError, LlmTarget
from app.domain.models import AssistantMessage
from app.domain.schemas import (
    AssistantConversationCreateRequest,
    AssistantConversationDetailResponse,
    AssistantConversationListResponse,
    AssistantConversationResponse,
    AssistantConversationUpdateRequest,
    AssistantMessageCreateRequest,
    AssistantStreamEvent,
    AssistantUndoResponse,
)
from app.i18n import set_locale, t

router = APIRouter(prefix="/api/assistant", tags=["assistant"])


def get_assistant_runtime(request: Request) -> AssistantRuntime:
    return request.app.state.assistant


def _not_found(e: Exception) -> HTTPException:
    return HTTPException(status_code=404, detail=str(e))


@router.get(
    "/conversations",
    response_model=AssistantConversationListResponse,
    operation_id="list_assistant_conversations",
)
def list_conversations(
    db: Session = Depends(get_session), user: CurrentUser = Depends(require_user)
) -> AssistantConversationListResponse:
    return AssistantConversationListResponse(
        items=[domain.to_conversation_response(c) for c in domain.list_conversations(db, user)]
    )


@router.post(
    "/conversations",
    response_model=AssistantConversationResponse,
    status_code=201,
    operation_id="create_assistant_conversation",
)
def create_conversation(
    body: AssistantConversationCreateRequest,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> AssistantConversationResponse:
    return domain.to_conversation_response(domain.create_conversation(db, user, body.title))


@router.get(
    "/conversations/{conversation_id}",
    response_model=AssistantConversationDetailResponse,
    operation_id="get_assistant_conversation",
)
def get_conversation(
    conversation_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> AssistantConversationDetailResponse:
    try:
        conversation = domain.get_conversation(db, user, conversation_id)
    except domain.ConversationNotFoundError as e:
        raise _not_found(e) from e
    return AssistantConversationDetailResponse(
        **domain.to_conversation_response(conversation).model_dump(),
        messages=[domain.to_message_response(m) for m in domain.list_messages(db, conversation.id)],
    )


@router.patch(
    "/conversations/{conversation_id}",
    response_model=AssistantConversationResponse,
    operation_id="update_assistant_conversation",
)
def update_conversation(
    conversation_id: uuid.UUID,
    body: AssistantConversationUpdateRequest,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> AssistantConversationResponse:
    try:
        conversation = domain.rename_conversation(db, user, conversation_id, body.title)
    except domain.ConversationNotFoundError as e:
        raise _not_found(e) from e
    return domain.to_conversation_response(conversation)


@router.delete(
    "/conversations/{conversation_id}",
    status_code=204,
    operation_id="delete_assistant_conversation",
)
def delete_conversation(
    conversation_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> None:
    try:
        domain.delete_conversation(db, user, conversation_id)
    except domain.ConversationNotFoundError as e:
        raise _not_found(e) from e


_STREAM_DOC = """発言を送り、応答を `text/event-stream` で返す(ADR-0048 8章)。ブラウザは
`EventSource` ではなく `fetch` で読む。止めるときは接続を切る(LLM の呼び出しをやめ、そこまでの
本文を保存し、この発言で作った Run のうち `queued` のものを取り消す)。

各イベントは `event: <type>` と `data: <JSON>` の組。JSON の形は `AssistantStreamEvent.event`
(`type` で見分ける):

- `user_message`: 保存した利用者の発言(最初に1回)。
- `text_delta`: アシスタントの本文の差分(`text`)。
- `tool_call`: 道具の呼び出し(`tool_call_id`、`name`、`arguments`)。実行の前に出る。
- `assistant_message`: 保存したアシスタントのメッセージ(LLM の1回の応答ごと)。
- `tool_result`: 保存した道具の結果(tool のメッセージ。書き込みは `before` / `after` と
  `undoable`)。
- `run_created`: `generate_image` が作った Run(`run_id`)。進捗は `GET /api/runs/{id}/events`。
- `error`: 止めた理由(`code` は `llm_error` / `rate_limited` / `tool_limit` / `internal`)。
- `done`: 最後に1回(`status` は `complete` / `error` / `limit`、`conversation`)。

送る前に断るもの(JSON のエラー): 無効(409)、会話が無い(404)、添付が見えない(404)、
LLM の1時間の上限(429)、接続先・キーが無い(409)、同じ会話で処理中(409)。
"""


def _resolve_target(
    db: Session, request: Request, config: assistant_settings.AssistantConfig, needs: bool
) -> LlmTarget | None:
    if not needs:
        return None
    try:
        target = assistant_settings.resolve_target(db, config, request.app.state.settings)
    except ConnectionNotFoundError as e:
        raise HTTPException(status_code=409, detail=t("assistant.connectionMissing")) from e
    if not target.api_key:
        raise HTTPException(status_code=409, detail=t("assistant.apiKeyMissing"))
    return target


@router.post(
    "/conversations/{conversation_id}/messages",
    operation_id="send_assistant_message",
    description=_STREAM_DOC,
    response_class=StreamingResponse,
    responses={
        200: {
            "model": AssistantStreamEvent,
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
            "description": "SSE のイベント列(形は AssistantStreamEvent.event)。",
        }
    },
)
async def send_message(
    conversation_id: uuid.UUID,
    body: AssistantMessageCreateRequest,
    request: Request,
    user: CurrentUser = Depends(require_user),
    runtime: AssistantRuntime = Depends(get_assistant_runtime),
) -> StreamingResponse:
    set_locale(body.locale)
    state = request.app.state

    def _prepare() -> tuple[assistant_settings.AssistantConfig, LlmTarget | None]:
        with state.session_factory() as db:
            # 他人の会話は、無効かどうかより先に 404(存在を明かさない。ADR-0025)。
            try:
                domain.get_conversation(db, user, conversation_id)
            except domain.ConversationNotFoundError as e:
                raise _not_found(e) from e
            config = assistant_settings.load(db)
            if not config.enabled:
                raise HTTPException(status_code=409, detail=t("assistant.disabled"))
            target = _resolve_target(db, request, config, runtime.engine.requires_target)
        return config, target

    config, target = await anyio.to_thread.run_sync(_prepare)
    if runtime.requests.count(user_key(user.id)) >= config.hourly_request_limit:
        raise HTTPException(
            status_code=429,
            detail=t("assistant.hourlyLimitReached", limit=config.hourly_request_limit),
        )

    attachment_images: list[LlmImage] = []
    if body.attachments:
        ctx = ToolContext(state=state, user=user, config=config)
        try:
            _briefs, attachment_images = await anyio.to_thread.run_sync(
                lambda: load_visible_images(ctx, list(dict.fromkeys(body.attachments)))
            )
        except ImageWithheldFailure as e:
            # ADR-0051 4章: `deny` の判定を受けた画像は添付として送らない。
            raise HTTPException(status_code=409, detail=t("mods.attachmentDenied")) from e
        except ToolFailure as e:
            raise HTTPException(status_code=404, detail=t("assistant.attachmentNotFound")) from e

    if not runtime.begin(conversation_id):
        raise HTTPException(status_code=409, detail=t("assistant.busy"))
    try:

        def _save_user_message() -> AssistantMessage:
            with state.session_factory() as db:
                message = domain.append_message(
                    db,
                    conversation_id,
                    domain.ROLE_USER,
                    {
                        "text": body.text,
                        "attachments": [str(a) for a in dict.fromkeys(body.attachments)],
                    },
                )
                domain.set_title_if_empty(db, conversation_id, body.text)
                db.refresh(message)
                db.expunge(message)
                return message

        user_message = await anyio.to_thread.run_sync(_save_user_message)
    except BaseException:
        runtime.end(conversation_id)
        raise

    turn = AssistantTurn(
        state,
        runtime,
        TurnInput(
            conversation_id=conversation_id,
            user=user,
            config=config,
            target=target,
            locale=body.locale,
            user_message=user_message,
            form=body.form,
            attachment_images=attachment_images,
        ),
    )
    return StreamingResponse(
        turn.events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.post(
    "/undo/{message_id}",
    response_model=AssistantUndoResponse,
    operation_id="undo_assistant_write",
)
def undo(
    message_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> AssistantUndoResponse:
    """アシスタントのプロンプトセットへの書き込みを元に戻す(ADR-0048 4章)。その後に対象が
    変わっていれば戻さずに 409。作成を戻すとセットは論理削除になる。"""
    message = domain.get_message_for_user(db, user, message_id)
    if message is None:
        raise HTTPException(status_code=404, detail=t("assistant.messageNotFound"))
    try:
        prompt_set = undo_message(db, user, message)
    except (UndoConflictError, UndoNotAvailableError) as e:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(e)) from e
    db.refresh(message)
    response: dict[str, Any] = {
        "message": domain.to_message_response(message),
        "prompt_set": prompt_set,
    }
    return AssistantUndoResponse(**response)
