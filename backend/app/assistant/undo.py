"""アシスタントの書き込みを「元に戻す」(ADR-0048 4章・13章)。

tool のメッセージに残した `undo`(種類、対象、書き込んだ直後の `updated_at`)をもとに戻す。
戻せるのは、その後に誰も対象を変えていないときだけ(`updated_at` で比べる)。変わっていれば
戻さずに `UndoConflictError`。作ったセットを戻すと、そのセットは論理削除になる(利用者の操作で
あり、アシスタントの道具ではない)。

ストックの整理(13章)は `app/assistant/stock.py` が画像ごとに戻す。変わっていた画像は飛ばし、
戻した枚数と飛ばした枚数を返す。1枚も戻せなければ `UndoConflictError`。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.assistant import stock as stock_domain
from app.auth.identity import CurrentUser
from app.domain import assistant as assistant_domain
from app.domain import prompt_sets as prompt_sets_domain
from app.domain.models import AssistantMessage, PromptSetItem
from app.domain.schemas import PromptSetResponse
from app.domain.visibility import get_visible_prompt_set
from app.i18n import t


class UndoNotAvailableError(Exception):
    """戻せる書き込みではない、または既に戻した(API 層で 409)。"""


class UndoConflictError(Exception):
    """その後に対象が変わった・消えたので戻さない(API 層で 409)。"""


def _same_time(recorded: Any, actual: datetime | None) -> bool:
    if not isinstance(recorded, str) or actual is None:
        return False
    try:
        return datetime.fromisoformat(recorded) == actual
    except ValueError:
        return False


def _conflict() -> UndoConflictError:
    return UndoConflictError(t("assistant.undoConflict"))


@dataclass
class UndoOutcome:
    # 戻した後のプロンプトセット(作成を取り消したときと、ストックの整理では None)。
    prompt_set: PromptSetResponse | None = None
    # ストックの整理を戻した結果(13章)。
    stock: stock_domain.StockUndoResult | None = None


def undo_message(
    db: Session, user: CurrentUser, message: AssistantMessage, *, settings: Any = None
) -> UndoOutcome:
    """戻して、その結果を返す。commit まで行う。`settings` はタイトルを戻したときの埋め込みの
    計算し直しの判定に使う(ADR-0045 2章)。"""
    content = message.content or {}
    undo = content.get("undo")
    if message.role != assistant_domain.ROLE_TOOL or not isinstance(undo, dict):
        raise UndoNotAvailableError(t("assistant.undoNotAvailable"))
    if content.get("undone_at"):
        raise UndoNotAvailableError(t("assistant.undoAlreadyDone"))

    kind = undo.get("kind")
    if kind in stock_domain.STOCK_UNDO_KINDS:
        try:
            stock_result = stock_domain.undo_stock(db, user, undo, settings)
        except stock_domain.StockUndoConflictError as e:
            if kind in (stock_domain.KIND_GROUP_RENAME, stock_domain.KIND_TITLE):
                raise _conflict() from e
            raise UndoConflictError(t("assistant.undoStockConflict")) from e
        assistant_domain.mark_undone(
            db, message, prompt_sets_domain.utcnow(), stock_result.as_dict()
        )
        return UndoOutcome(stock=stock_result)

    try:
        prompt_set_id = uuid.UUID(str(undo.get("prompt_set_id")))
    except ValueError as e:
        raise UndoNotAvailableError(t("assistant.undoNotAvailable")) from e
    prompt_set = get_visible_prompt_set(db, user, prompt_set_id)
    if prompt_set is None:
        raise _conflict()

    result: PromptSetResponse | None
    if kind == "create_prompt_set":
        if not _same_time(undo.get("updated_at"), prompt_set.updated_at):
            raise _conflict()
        prompt_sets_domain.delete_set(db, user, prompt_set_id)
        result = None
    elif kind == "rename_prompt_set":
        if not _same_time(undo.get("updated_at"), prompt_set.updated_at):
            raise _conflict()
        result = prompt_sets_domain.rename_set(db, user, prompt_set_id, str(undo.get("name")))
    elif kind == "add_prompt_set_items":
        entries = undo.get("items") or []
        item_ids: list[uuid.UUID] = []
        for entry in entries:
            item = db.get(PromptSetItem, uuid.UUID(str(entry.get("id"))))
            if (
                item is None
                or item.deleted_at is not None
                or item.prompt_set_id != prompt_set_id
                or not _same_time(entry.get("updated_at"), item.updated_at)
            ):
                raise _conflict()
            item_ids.append(item.id)
        prompt_sets_domain.delete_items(db, prompt_set_id, item_ids)
        result = prompt_sets_domain.get_set(db, user, prompt_set_id)
    elif kind == "update_prompt_set_item":
        item_id = uuid.UUID(str(undo.get("item_id")))
        item = db.get(PromptSetItem, item_id)
        if (
            item is None
            or item.deleted_at is not None
            or item.prompt_set_id != prompt_set_id
            or not _same_time(undo.get("updated_at"), item.updated_at)
        ):
            raise _conflict()
        before = undo.get("before") or {}
        prompt_sets_domain.update_item(
            db,
            user,
            prompt_set_id,
            item_id,
            label=before.get("label"),
            text=before.get("text"),
            position=before.get("position"),
        )
        result = prompt_sets_domain.get_set(db, user, prompt_set_id)
    else:
        raise UndoNotAvailableError(t("assistant.undoNotAvailable"))

    assistant_domain.mark_undone(db, message, prompt_sets_domain.utcnow())
    return UndoOutcome(prompt_set=result)
