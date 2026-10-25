"""生成のアシスタントの会話とメッセージ(ADR-0048 3章)。

会話は証跡ではないので、名前の変更と論理削除ができる。見えるのは本人の会話だけ(ADR-0025。
個人モードでは全部)。

メッセージの `content`(JSON)の形:

- `user`: `{"text": str, "attachments": [Asset ID の文字列]}`
- `assistant`: `{"text": str, "tool_calls": [{"id", "name", "arguments": {...}}],
  "status": "complete" | "stopped" | "error", "error": str | None}`
- `tool`: `{"tool_call_id": str, "name": str, "ok": bool, "result": {...}, "run_ids": [...],
  "undo": {...} | None, "undone_at": ISO 8601 | None}`
  - `result` は道具が LLM に返した JSON。書き込みの道具は変更前(`before`)と変更後(`after`)を
    入れる(プロンプトセットには版の履歴が無いため。4章)。
  - `undo` は「元に戻す」に要る値(種類、対象、書き込んだ直後の `updated_at`)。ストックの
    整理(13章)は、変えた画像ごとの変更前と書き込んだ直後の状態(`app/assistant/stock.py`)。
  - `undo_result` はストックの整理を戻した結果(戻した枚数と飛ばした枚数)。

画像の中身(base64)は保存しない(ADR-0004)。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.auth.identity import CurrentUser
from app.domain.models import AssistantConversation, AssistantMessage
from app.domain.schemas import (
    AssistantConversationResponse,
    AssistantImagePermissionView,
    AssistantMessageResponse,
    AssistantToolCallView,
    AssistantUndoResult,
)
from app.domain.visibility import (
    assistant_conversation_visible,
    get_visible_assistant_conversation,
)
from app.i18n import t

ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
ROLE_TOOL = "tool"

# 最初の発言から作るタイトルの長さ(文字)。
TITLE_FROM_MESSAGE_CHARS = 40


class ConversationNotFoundError(Exception):
    def __init__(self) -> None:
        super().__init__(t("assistant.conversationNotFound"))


def utcnow() -> datetime:
    return datetime.now(UTC)


def title_from_text(text: str) -> str:
    """発言の先頭からタイトルを作る(最初の空でない行。空白をまとめ、長ければ切って … を付ける)。"""
    line = next((line for line in text.splitlines() if line.strip()), "")
    collapsed = " ".join(line.split())
    if len(collapsed) <= TITLE_FROM_MESSAGE_CHARS:
        return collapsed
    return collapsed[: TITLE_FROM_MESSAGE_CHARS - 1] + "…"


# -- 応答の形 -------------------------------------------------------------------


def to_conversation_response(conversation: AssistantConversation) -> AssistantConversationResponse:
    return AssistantConversationResponse(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def _uuid_list(values: Any) -> list[uuid.UUID]:
    result: list[uuid.UUID] = []
    for value in values or []:
        try:
            result.append(uuid.UUID(str(value)))
        except ValueError:
            continue
    return result


def to_message_response(message: AssistantMessage) -> AssistantMessageResponse:
    content = message.content or {}
    base: dict[str, Any] = {
        "id": message.id,
        "conversation_id": message.conversation_id,
        "position": message.position,
        "role": message.role,
        "created_at": message.created_at,
    }
    if message.role == ROLE_USER:
        return AssistantMessageResponse(
            **base,
            text=content.get("text", ""),
            attachments=_uuid_list(content.get("attachments")),
            withheld_attachments=_uuid_list(content.get("withheld_attachments")),
        )
    if message.role == ROLE_ASSISTANT:
        return AssistantMessageResponse(
            **base,
            text=content.get("text", ""),
            tool_calls=[
                AssistantToolCallView(
                    id=str(call.get("id", "")),
                    name=str(call.get("name", "")),
                    arguments=call.get("arguments") or {},
                )
                for call in content.get("tool_calls") or []
                if isinstance(call, dict)
            ],
            status=content.get("status") or "complete",
            error=content.get("error"),
        )
    undone_raw = content.get("undone_at")
    return AssistantMessageResponse(
        **base,
        tool_call_id=content.get("tool_call_id"),
        tool_name=content.get("name"),
        ok=bool(content.get("ok")),
        result=content.get("result") or {},
        run_ids=_uuid_list(content.get("run_ids")),
        undoable=bool(content.get("undo")) and undone_raw is None,
        undone_at=datetime.fromisoformat(undone_raw) if isinstance(undone_raw, str) else None,
        undo_result=_undo_result(content.get("undo_result")),
        image_permission=_image_permission(content.get("permission")),
    )


def _image_permission(raw: Any) -> AssistantImagePermissionView | None:
    if not isinstance(raw, dict):
        return None
    try:
        return AssistantImagePermissionView.model_validate(raw)
    except ValueError:
        return None


def _undo_result(raw: Any) -> AssistantUndoResult | None:
    if not isinstance(raw, dict):
        return None
    try:
        return AssistantUndoResult.model_validate(raw)
    except ValueError:
        return None


# -- 会話 ----------------------------------------------------------------------


def get_conversation(
    db: Session, user: CurrentUser, conversation_id: uuid.UUID
) -> AssistantConversation:
    conversation = get_visible_assistant_conversation(db, user, conversation_id)
    if conversation is None:
        raise ConversationNotFoundError
    return conversation


def list_conversations(db: Session, user: CurrentUser) -> list[AssistantConversation]:
    return list(
        db.execute(
            select(AssistantConversation)
            .where(
                AssistantConversation.deleted_at.is_(None),
                assistant_conversation_visible(user),
            )
            .order_by(AssistantConversation.updated_at.desc(), AssistantConversation.id.desc())
        )
        .scalars()
        .all()
    )


def create_conversation(db: Session, user: CurrentUser, title: str = "") -> AssistantConversation:
    now = utcnow()
    conversation = AssistantConversation(
        title=title.strip(), created_by_user_id=user.id, created_at=now, updated_at=now
    )
    db.add(conversation)
    db.commit()
    return conversation


def rename_conversation(
    db: Session, user: CurrentUser, conversation_id: uuid.UUID, title: str
) -> AssistantConversation:
    conversation = get_conversation(db, user, conversation_id)
    conversation.title = title.strip()
    conversation.updated_at = utcnow()
    db.commit()
    return conversation


def delete_conversation(db: Session, user: CurrentUser, conversation_id: uuid.UUID) -> None:
    conversation = get_conversation(db, user, conversation_id)
    conversation.deleted_at = utcnow()
    db.commit()


# -- メッセージ ------------------------------------------------------------------


def list_messages(db: Session, conversation_id: uuid.UUID) -> list[AssistantMessage]:
    return list(
        db.execute(
            select(AssistantMessage)
            .where(AssistantMessage.conversation_id == conversation_id)
            .order_by(AssistantMessage.position)
        )
        .scalars()
        .all()
    )


def append_message(
    db: Session, conversation_id: uuid.UUID, role: str, content: dict[str, Any]
) -> AssistantMessage:
    """会話の末尾にメッセージを足して commit する。会話の `updated_at` も進める。

    同じ会話の発言は同時に1つしか処理しない(`AssistantRuntime.begin`)ので、位置は最大値 + 1
    でよい(万一重なれば一意制約で失敗する)。
    """
    position = db.execute(
        select(func.coalesce(func.max(AssistantMessage.position), -1)).where(
            AssistantMessage.conversation_id == conversation_id
        )
    ).scalar_one()
    now = utcnow()
    message = AssistantMessage(
        conversation_id=conversation_id,
        position=position + 1,
        role=role,
        content=content,
        created_at=now,
    )
    db.add(message)
    conversation = db.get(AssistantConversation, conversation_id)
    if conversation is not None:
        conversation.updated_at = now
    db.commit()
    return message


def set_title_if_empty(db: Session, conversation_id: uuid.UUID, text: str) -> None:
    """タイトルがまだ無ければ、発言の先頭から作る(3章)。"""
    conversation = db.get(AssistantConversation, conversation_id)
    if conversation is None or conversation.title:
        return
    title = title_from_text(text)
    if title:
        conversation.title = title
        db.commit()


def get_message_for_user(
    db: Session, user: CurrentUser, message_id: uuid.UUID
) -> AssistantMessage | None:
    """見える会話(削除されていない)のメッセージ。それ以外は None。"""
    message = db.get(AssistantMessage, message_id)
    if message is None:
        return None
    if get_visible_assistant_conversation(db, user, message.conversation_id) is None:
        return None
    return message


def mark_undone(
    db: Session,
    message: AssistantMessage,
    when: datetime,
    undo_result: dict[str, Any] | None = None,
) -> None:
    """戻した印を付けて commit する。ストックの整理は戻した枚数なども残す(ADR-0048 13章)。"""
    content = dict(message.content or {})
    content["undone_at"] = when.isoformat()
    if undo_result is not None:
        content["undo_result"] = undo_result
    message.content = content
    flag_modified(message, "content")
    db.commit()
