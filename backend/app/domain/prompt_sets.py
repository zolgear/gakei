"""プロンプトセット(ADR-0009 2章)のドメイン関数。REST(`app/api/prompt_sets.py`)と、
生成のアシスタントの道具(ADR-0048 4章)が同じ関数を使う。

証跡ではないので更新・論理削除ができる。`run` との外部キーは張らない。版の履歴は持たない。

ADR-0025: 認証モードでは本人が作ったもの(作成者が記録されていない以前のものは管理者)だけを
扱う。他人のセットは存在しないものと同じ `PromptSetNotFoundError`。

どの関数も最後に commit する(呼び出し側は結果の行をそのまま使える)。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.auth.identity import CurrentUser
from app.domain.models import PromptSet, PromptSetItem
from app.domain.schemas import PromptSetItemResponse, PromptSetResponse
from app.domain.visibility import get_visible_prompt_set, prompt_set_visible
from app.i18n import t


class PromptSetNotFoundError(Exception):
    """セットが無い・削除済み・見えない(API 層で 404)。"""

    def __init__(self) -> None:
        super().__init__(t("promptSets.notFound"))


class PromptSetItemNotFoundError(Exception):
    """項目が無い・削除済み・別のセットのもの(API 層で 404)。"""

    def __init__(self) -> None:
        super().__init__(t("promptSets.itemNotFound"))


@dataclass(frozen=True)
class ItemInput:
    label: str | None
    text: str


def utcnow() -> datetime:
    return datetime.now(UTC)


# -- 応答の形 -------------------------------------------------------------------


def to_item_response(item: PromptSetItem) -> PromptSetItemResponse:
    return PromptSetItemResponse(
        id=item.id,
        label=item.label,
        text=item.text,
        position=item.position,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def to_set_response(prompt_set: PromptSet, items: list[PromptSetItem]) -> PromptSetResponse:
    return PromptSetResponse(
        id=prompt_set.id,
        name=prompt_set.name,
        created_at=prompt_set.created_at,
        updated_at=prompt_set.updated_at,
        items=[to_item_response(i) for i in items],
    )


# -- 読み取り -------------------------------------------------------------------


def get_active_set(db: Session, user: CurrentUser, prompt_set_id: uuid.UUID) -> PromptSet:
    prompt_set = get_visible_prompt_set(db, user, prompt_set_id)
    if prompt_set is None:
        raise PromptSetNotFoundError
    return prompt_set


def get_active_items(db: Session, prompt_set_id: uuid.UUID) -> list[PromptSetItem]:
    return list(
        db.execute(
            select(PromptSetItem)
            .where(
                PromptSetItem.prompt_set_id == prompt_set_id,
                PromptSetItem.deleted_at.is_(None),
            )
            .order_by(PromptSetItem.position)
        )
        .scalars()
        .all()
    )


def get_active_item(db: Session, prompt_set_id: uuid.UUID, item_id: uuid.UUID) -> PromptSetItem:
    item = db.get(PromptSetItem, item_id)
    if item is None or item.deleted_at is not None or item.prompt_set_id != prompt_set_id:
        raise PromptSetItemNotFoundError
    return item


def list_sets(db: Session, user: CurrentUser) -> list[PromptSetResponse]:
    """見えるセットを新しい順に(項目つき)。件数が少ない前提でページングしない。"""
    prompt_sets = (
        db.execute(
            select(PromptSet)
            .where(PromptSet.deleted_at.is_(None), prompt_set_visible(user))
            .order_by(PromptSet.updated_at.desc(), PromptSet.id.desc())
        )
        .scalars()
        .all()
    )
    return [to_set_response(ps, get_active_items(db, ps.id)) for ps in prompt_sets]


def get_set(db: Session, user: CurrentUser, prompt_set_id: uuid.UUID) -> PromptSetResponse:
    prompt_set = get_active_set(db, user, prompt_set_id)
    return to_set_response(prompt_set, get_active_items(db, prompt_set_id))


# -- 書き込み -------------------------------------------------------------------


def _renumber(items: list[PromptSetItem]) -> None:
    """`items` は望む並び順で渡す前提。position を 0 から振り直す。"""
    for index, item in enumerate(items):
        item.position = index


def _touch_set(db: Session, prompt_set_id: uuid.UUID, now: datetime) -> None:
    """項目側の変更でも updated_at を更新する(セット自体は変えていなくても「新しい順」に出す)。"""
    db.execute(update(PromptSet).where(PromptSet.id == prompt_set_id).values(updated_at=now))


def create_set(
    db: Session, user: CurrentUser, name: str, items: list[ItemInput]
) -> PromptSetResponse:
    now = utcnow()
    prompt_set = PromptSet(name=name, created_by_user_id=user.id, created_at=now, updated_at=now)
    db.add(prompt_set)
    db.flush()

    rows: list[PromptSetItem] = []
    for index, item_in in enumerate(items):
        item = PromptSetItem(
            prompt_set_id=prompt_set.id,
            label=item_in.label,
            text=item_in.text,
            position=index,
            created_at=now,
            updated_at=now,
        )
        db.add(item)
        rows.append(item)

    db.commit()
    return to_set_response(prompt_set, rows)


def rename_set(
    db: Session, user: CurrentUser, prompt_set_id: uuid.UUID, name: str
) -> PromptSetResponse:
    prompt_set = get_active_set(db, user, prompt_set_id)
    prompt_set.name = name
    prompt_set.updated_at = utcnow()
    db.commit()
    return to_set_response(prompt_set, get_active_items(db, prompt_set_id))


def delete_set(db: Session, user: CurrentUser, prompt_set_id: uuid.UUID) -> None:
    prompt_set = get_active_set(db, user, prompt_set_id)
    prompt_set.deleted_at = utcnow()
    db.commit()


def add_items(
    db: Session, user: CurrentUser, prompt_set_id: uuid.UUID, items: list[ItemInput]
) -> list[PromptSetItemResponse]:
    """項目を末尾に足す(順に)。"""
    get_active_set(db, user, prompt_set_id)
    existing = get_active_items(db, prompt_set_id)
    now = utcnow()
    rows: list[PromptSetItem] = []
    for offset, item_in in enumerate(items):
        item = PromptSetItem(
            prompt_set_id=prompt_set_id,
            label=item_in.label,
            text=item_in.text,
            position=len(existing) + offset,
            created_at=now,
            updated_at=now,
        )
        db.add(item)
        rows.append(item)
    _touch_set(db, prompt_set_id, now)
    db.commit()
    return [to_item_response(item) for item in rows]


_UNSET = object()


def update_item(
    db: Session,
    user: CurrentUser,
    prompt_set_id: uuid.UUID,
    item_id: uuid.UUID,
    *,
    label: str | None | object = _UNSET,
    text: str | None = None,
    position: int | None = None,
) -> PromptSetItemResponse:
    """項目のラベル・本文・並び順を変える。`label` は渡したときだけ変える(None で消す)。
    `text` と `position` は None なら変えない。"""
    get_active_set(db, user, prompt_set_id)
    item = get_active_item(db, prompt_set_id, item_id)
    now = utcnow()

    if label is not _UNSET:
        item.label = label  # type: ignore[assignment]
    if text is not None:
        item.text = text

    if position is not None:
        ordered = [i for i in get_active_items(db, prompt_set_id) if i.id != item_id]
        target_position = max(0, min(position, len(ordered)))
        ordered.insert(target_position, item)
        _renumber(ordered)

    item.updated_at = now
    _touch_set(db, prompt_set_id, now)
    db.commit()
    return to_item_response(item)


def delete_item(
    db: Session, user: CurrentUser, prompt_set_id: uuid.UUID, item_id: uuid.UUID
) -> None:
    get_active_set(db, user, prompt_set_id)
    item = get_active_item(db, prompt_set_id, item_id)
    now = utcnow()
    item.deleted_at = now
    item.updated_at = now

    remaining = [i for i in get_active_items(db, prompt_set_id) if i.id != item_id]
    _renumber(remaining)

    _touch_set(db, prompt_set_id, now)
    db.commit()


def delete_items(db: Session, prompt_set_id: uuid.UUID, item_ids: list[uuid.UUID]) -> None:
    """項目をまとめて論理削除する(アシスタントの「元に戻す」用。可視性は呼び出し側で確かめる)。"""
    now = utcnow()
    targets = set(item_ids)
    for item in get_active_items(db, prompt_set_id):
        if item.id in targets:
            item.deleted_at = now
            item.updated_at = now
    remaining = [i for i in get_active_items(db, prompt_set_id) if i.id not in targets]
    _renumber(remaining)
    _touch_set(db, prompt_set_id, now)
    db.commit()
