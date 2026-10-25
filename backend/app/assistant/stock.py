"""アシスタントのストックの整理(ADR-0048 13章)。書き込みと、変更前の記録と、「元に戻す」。

書き込みは REST と同じドメインの関数(`domain/annotations.py`、`domain/asset_groups.py`)で行い、
見える範囲と書き込める範囲も画面と同じ(ADR-0025)。タグとタイトルは人の編集と同じ関数で書く
(付けたタグは `source = user`、外したタグは `removed = True`、タイトルは `title_source = user`)。

書き込むたびに、変えた画像ごとの変更前と書き込んだ直後の状態を `undo` に残す(tool の
メッセージの JSON に入る。DB の形は変えない)。

- タグ: 変えた (画像, タグ) の行ごとの `before`(行が無ければ None)と `after`。
- タイトル: `title` と `title_source`。
- グループ: 画像ごとの所属(削除されていないグループの ID、無ければ None)。

「元に戻す」は画像ごとに、今の状態が書き込んだ直後の状態と同じものだけを戻す。変わっていた
画像は飛ばし、戻した枚数と飛ばした枚数を返す。

ここでは commit まで行う(呼び出し側は Session を渡すだけ)。失敗は `StockError`(メッセージは
LLM にそのまま返す)。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.auth.identity import CurrentUser
from app.domain import annotations as annotations_domain
from app.domain import asset_groups as asset_groups_domain
from app.domain import content_rating as content_rating_domain
from app.domain import focal_points as focal_points_domain
from app.domain import prompt_embeddings as prompt_embeddings_domain
from app.domain.models import Asset, AssetGroup, AssetKind
from app.domain.schemas import AssetGroupCreate
from app.domain.visibility import get_visible_asset, get_visible_group

# 1回の道具の呼び出しで扱う画像の上限(13章)。
STOCK_ASSETS_MAX = 200
# 1回の呼び出しで付ける・外すタグの上限。
STOCK_TAGS_MAX = 20
# 結果のカードに出すサムネイルの数(結果に入れる画像の上限)。
PREVIEW_MAX = 12

# `undo.kind`。
KIND_TAGS = "stock_tags"
KIND_TITLE = "stock_title"
KIND_GROUPS = "stock_groups"
KIND_GROUP_RENAME = "stock_group_rename"
STOCK_UNDO_KINDS = frozenset({KIND_TAGS, KIND_TITLE, KIND_GROUPS, KIND_GROUP_RENAME})


class StockError(Exception):
    """書き込めない(見えない画像、使えないタグ名など)。何も変えない。"""


class StockUndoConflictError(Exception):
    """戻せるものが無かった(全部がその後に変わっていた)。"""


@dataclass
class StockWrite:
    result: dict[str, Any]
    # 何も変えなかったときは None(「元に戻す」を出さない)。
    undo: dict[str, Any] | None
    # 埋め込みを計算し直す必要が出た(タイトルの変更。ADR-0045 2章)。
    requeued_embeddings: bool = False


@dataclass
class StockUndoResult:
    restored: int
    skipped: int
    # 作ったグループを戻したが、別の画像が入っていたので消さなかった。
    group_kept: bool = False
    # 作ったグループを消した。
    group_deleted: bool = False
    requeued_embeddings: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "restored": self.restored,
            "skipped": self.skipped,
            "group_kept": self.group_kept,
            "group_deleted": self.group_deleted,
        }


# -- 共通 -----------------------------------------------------------------------------


def _unique(ids: list[uuid.UUID]) -> list[uuid.UUID]:
    return list(dict.fromkeys(ids))


def _load_assets(
    db: Session, user: CurrentUser, asset_ids: list[uuid.UUID], *, annotatable: bool
) -> list[Asset]:
    """見える、削除されていない画像。1枚でも無ければ全体を拒む(REST のグループと同じ)。
    タグとタイトルではマスクも拒む(画面と同じ)。"""
    ids = _unique(asset_ids)
    if len(ids) > STOCK_ASSETS_MAX:
        raise StockError(f"Too many images: at most {STOCK_ASSETS_MAX} per call.")
    assets: list[Asset] = []
    missing: list[str] = []
    masks: list[str] = []
    for asset_id in ids:
        asset = get_visible_asset(db, user, asset_id)
        if asset is None or asset.deleted_at is not None:
            missing.append(str(asset_id))
        elif annotatable and asset.kind == AssetKind.MASK:
            masks.append(str(asset_id))
        else:
            assets.append(asset)
    if missing:
        raise StockError("Assets not found: " + ", ".join(missing) + ". Nothing was changed.")
    if masks:
        raise StockError(
            "Masks cannot have tags or titles: " + ", ".join(masks) + ". Nothing was changed."
        )
    return assets


def _preview(db: Session, asset_ids: list[uuid.UUID]) -> list[dict[str, Any]]:
    """結果のカードのサムネイル(先頭 `PREVIEW_MAX` 枚)。焦点(ADR-0043)とレーティング
    (ADR-0049)は画面がサムネイルの位置とマスクに使う。"""
    ids = asset_ids[:PREVIEW_MAX]
    focal = focal_points_domain.bulk_get(db, ids)
    ratings = content_rating_domain.bulk_levels(db, ids)
    out = []
    for asset_id in ids:
        point = focal.get(asset_id)
        out.append(
            {
                "asset_id": str(asset_id),
                "focal_point": point.model_dump(mode="json") if point else None,
                "content_rating": ratings.get(asset_id),
            }
        )
    return out


def _tag_names(raw: list[str]) -> list[str]:
    names: list[str] = []
    for value in raw:
        try:
            name = annotations_domain.normalize_tag_name(value)
        except annotations_domain.TagNameError as e:
            raise StockError(f"Invalid tag {value!r}: {e}") from e
        if name not in names:
            names.append(name)
    if not names:
        raise StockError("Pass at least one tag.")
    if len(names) > STOCK_TAGS_MAX:
        raise StockError(f"Too many tags: at most {STOCK_TAGS_MAX} per call.")
    return names


def _group_name(raw: str) -> str:
    try:
        return AssetGroupCreate(name=raw).name
    except ValidationError as e:
        raise StockError("The group name must be 1-100 characters.") from e


def _visible_group(db: Session, user: CurrentUser, group_id: uuid.UUID) -> AssetGroup:
    group = get_visible_group(db, user, group_id)
    if group is None:
        raise StockError(f"Group {group_id} not found.")
    return group


def _id(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value is not None else None
    except ValueError:
        return None


# -- タグ -------------------------------------------------------------------------------


def _write_tags(
    db: Session, user: CurrentUser, asset_ids: list[uuid.UUID], raw_tags: list[str], *, add: bool
) -> StockWrite:
    names = _tag_names(raw_tags)
    assets = _load_assets(db, user, asset_ids, annotatable=True)
    entries: list[dict[str, Any]] = []
    changed: list[uuid.UUID] = []
    for asset in assets:
        asset_changed = False
        for name in names:
            tag_id = annotations_domain.tag_id_by_name(db, name)
            before = (
                annotations_domain.tag_row_state(db, asset.id, tag_id)
                if tag_id is not None
                else None
            )
            if add:
                if before is not None and before["source"] == "user" and not before["removed"]:
                    continue
                annotations_domain.add_tag(db, asset, name)
            elif not annotations_domain.remove_tag(db, asset, name):
                continue
            tag_id = annotations_domain.tag_id_by_name(db, name)
            assert tag_id is not None
            entries.append(
                {
                    "asset_id": str(asset.id),
                    "tag_id": str(tag_id),
                    "tag": name,
                    "before": before,
                    "after": annotations_domain.tag_row_state(db, asset.id, tag_id),
                }
            )
            asset_changed = True
        if asset_changed:
            changed.append(asset.id)
    preview = _preview(db, changed or [a.id for a in assets])
    db.commit()
    result = {
        "operation": "add_tags" if add else "remove_tags",
        "tags": names,
        "count": len(assets),
        "changed": len(changed),
        "unchanged": len(assets) - len(changed),
        "preview": preview,
        "note": _WRITE_NOTE,
    }
    undo = {"kind": KIND_TAGS, "entries": entries} if entries else None
    return StockWrite(result=result, undo=undo)


def add_tags(
    db: Session, user: CurrentUser, asset_ids: list[uuid.UUID], tags: list[str]
) -> StockWrite:
    return _write_tags(db, user, asset_ids, tags, add=True)


def remove_tags(
    db: Session, user: CurrentUser, asset_ids: list[uuid.UUID], tags: list[str]
) -> StockWrite:
    return _write_tags(db, user, asset_ids, tags, add=False)


def _undo_tags(db: Session, user: CurrentUser, undo: dict[str, Any]) -> StockUndoResult:
    by_asset: dict[uuid.UUID, list[tuple[uuid.UUID, dict[str, Any]]]] = {}
    for entry in undo.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        asset_id, tag_id = _id(entry.get("asset_id")), _id(entry.get("tag_id"))
        if asset_id is None or tag_id is None:
            continue
        by_asset.setdefault(asset_id, []).append((tag_id, entry))
    restored = skipped = 0
    for asset_id, rows in by_asset.items():
        asset = get_visible_asset(db, user, asset_id)
        unchanged = (
            asset is not None
            and asset.deleted_at is None
            and all(
                annotations_domain.tag_row_state(db, asset_id, tag_id) == entry.get("after")
                for tag_id, entry in rows
            )
        )
        if not unchanged:
            skipped += 1
            continue
        for tag_id, entry in rows:
            annotations_domain.restore_tag_row(db, asset_id, tag_id, entry.get("before"))
        restored += 1
    return StockUndoResult(restored=restored, skipped=skipped)


# -- タイトル -------------------------------------------------------------------------


def set_title(
    db: Session, user: CurrentUser, asset_id: uuid.UUID, title: str | None, settings: Any
) -> StockWrite:
    (asset,) = _load_assets(db, user, [asset_id], annotatable=True)
    before = annotations_domain.title_state(db, asset.id)
    try:
        annotations_domain.set_title(db, asset, title)
    except ValueError as e:
        db.rollback()
        raise StockError(str(e)) from e
    after = annotations_domain.title_state(db, asset.id)
    requeued = prompt_embeddings_domain.on_title_changed(db, asset, settings)
    preview = _preview(db, [asset.id])
    db.commit()
    result = {
        "operation": "set_title",
        "count": 1,
        "changed": 0 if before == after else 1,
        "before": {"title": before["title"]},
        "after": {"title": after["title"]},
        "preview": preview,
        "note": _WRITE_NOTE,
    }
    undo = (
        None
        if before == after
        else {"kind": KIND_TITLE, "asset_id": str(asset.id), "before": before, "after": after}
    )
    return StockWrite(result=result, undo=undo, requeued_embeddings=requeued)


def _undo_title(
    db: Session, user: CurrentUser, undo: dict[str, Any], settings: Any
) -> StockUndoResult:
    asset_id = _id(undo.get("asset_id"))
    asset = get_visible_asset(db, user, asset_id) if asset_id is not None else None
    if (
        asset is None
        or asset.deleted_at is not None
        or annotations_domain.title_state(db, asset.id) != undo.get("after")
    ):
        return StockUndoResult(restored=0, skipped=1)
    annotations_domain.restore_title(db, asset.id, undo.get("before") or {})
    requeued = prompt_embeddings_domain.on_title_changed(db, asset, settings)
    return StockUndoResult(restored=1, skipped=0, requeued_embeddings=requeued)


# -- グループ ---------------------------------------------------------------------------


def _group_view(group: AssetGroup) -> dict[str, Any]:
    return {"id": str(group.id), "name": group.name}


def _move(
    db: Session, user: CurrentUser, group: AssetGroup, assets: list[Asset]
) -> list[dict[str, Any]]:
    """`group` へ移し、所属が変わった画像ごとの変更前と変更後を返す。"""
    ids = [a.id for a in assets]
    before = asset_groups_domain.active_memberships(db, ids)
    moving = [asset_id for asset_id in ids if before.get(asset_id) != group.id]
    if moving:
        asset_groups_domain.add_members(db, group, moving, viewer=user)
    return [
        {
            "asset_id": str(asset_id),
            "before": str(before[asset_id]) if asset_id in before else None,
            "after": str(group.id),
        }
        for asset_id in moving
    ]


def create_group(
    db: Session, user: CurrentUser, name: str, asset_ids: list[uuid.UUID]
) -> StockWrite:
    normalized = _group_name(name)
    assets = _load_assets(db, user, asset_ids, annotatable=False) if asset_ids else []
    row = asset_groups_domain.create_group(db, normalized, user)
    group = db.get(AssetGroup, row.id)
    assert group is not None
    entries = _move(db, user, group, assets) if assets else []
    preview = _preview(db, [a.id for a in assets])
    db.commit()
    result = {
        "operation": "create_group",
        "group": _group_view(group),
        "count": len(assets),
        "changed": len(entries),
        "moved_from_other_groups": sum(1 for e in entries if e["before"] is not None),
        "preview": preview,
        "note": _WRITE_NOTE,
    }
    undo = {"kind": KIND_GROUPS, "group_id": str(group.id), "created": True, "entries": entries}
    return StockWrite(result=result, undo=undo)


def rename_group(db: Session, user: CurrentUser, group_id: uuid.UUID, name: str) -> StockWrite:
    normalized = _group_name(name)
    group = _visible_group(db, user, group_id)
    before = group.name
    if before != normalized:
        asset_groups_domain.rename_group(db, group, normalized, user)
    db.commit()
    result = {
        "operation": "rename_group",
        "group": _group_view(group),
        "before": {"name": before},
        "after": {"name": group.name},
        "note": _WRITE_NOTE,
    }
    undo = (
        None
        if before == group.name
        else {
            "kind": KIND_GROUP_RENAME,
            "group_id": str(group.id),
            "before": before,
            "after": group.name,
        }
    )
    return StockWrite(result=result, undo=undo)


def move_to_group(
    db: Session, user: CurrentUser, group_id: uuid.UUID, asset_ids: list[uuid.UUID]
) -> StockWrite:
    group = _visible_group(db, user, group_id)
    assets = _load_assets(db, user, asset_ids, annotatable=False)
    entries = _move(db, user, group, assets)
    changed = [_id(e["asset_id"]) for e in entries]
    preview = _preview(db, [i for i in changed if i is not None] or [a.id for a in assets])
    db.commit()
    result = {
        "operation": "move_to_group",
        "group": _group_view(group),
        "count": len(assets),
        "changed": len(entries),
        "already_in_group": len(assets) - len(entries),
        "moved_from_other_groups": sum(1 for e in entries if e["before"] is not None),
        "preview": preview,
        "note": _WRITE_NOTE,
    }
    undo = (
        {"kind": KIND_GROUPS, "group_id": str(group.id), "created": False, "entries": entries}
        if entries
        else None
    )
    return StockWrite(result=result, undo=undo)


def remove_from_group(
    db: Session, user: CurrentUser, group_id: uuid.UUID, asset_ids: list[uuid.UUID]
) -> StockWrite:
    group = _visible_group(db, user, group_id)
    assets = _load_assets(db, user, asset_ids, annotatable=False)
    ids = [a.id for a in assets]
    current = asset_groups_domain.active_memberships(db, ids)
    members = [asset_id for asset_id in ids if current.get(asset_id) == group.id]
    if members:
        asset_groups_domain.remove_members(db, group, members, user)
    entries = [
        {"asset_id": str(asset_id), "before": str(group.id), "after": None} for asset_id in members
    ]
    preview = _preview(db, members or ids)
    db.commit()
    result = {
        "operation": "remove_from_group",
        "group": _group_view(group),
        "count": len(assets),
        "changed": len(members),
        "not_in_group": len(assets) - len(members),
        "preview": preview,
        "note": _WRITE_NOTE,
    }
    undo = (
        {"kind": KIND_GROUPS, "group_id": str(group.id), "created": False, "entries": entries}
        if entries
        else None
    )
    return StockWrite(result=result, undo=undo)


def _undo_groups(db: Session, user: CurrentUser, undo: dict[str, Any]) -> StockUndoResult:
    entries = [e for e in undo.get("entries") or [] if isinstance(e, dict)]
    ids = [i for i in (_id(e.get("asset_id")) for e in entries) if i is not None]
    current = asset_groups_domain.active_memberships(db, ids)
    # 戻す先(変更前のグループ。None は「グループなし」)ごとに、今のグループから移す。
    restore_to: dict[uuid.UUID | None, list[uuid.UUID]] = {}
    remove_from: dict[uuid.UUID, list[uuid.UUID]] = {}
    restored = skipped = 0
    for entry in entries:
        asset_id = _id(entry.get("asset_id"))
        if asset_id is None:
            continue
        asset = get_visible_asset(db, user, asset_id)
        after = _id(entry.get("after"))
        before = _id(entry.get("before"))
        if asset is None or asset.deleted_at is not None or current.get(asset_id) != after:
            skipped += 1
            continue
        if before is not None and asset_groups_domain.get_active_group_or_none(db, before) is None:
            # 元のグループがその後に消された。戻す先が無い。
            skipped += 1
            continue
        if before is None:
            assert after is not None
            remove_from.setdefault(after, []).append(asset_id)
        else:
            restore_to.setdefault(before, []).append(asset_id)
        restored += 1
    for group_id, asset_ids in remove_from.items():
        group = asset_groups_domain.get_active_group_or_none(db, group_id)
        if group is not None:
            asset_groups_domain.remove_members(db, group, asset_ids, user)
    for group_id, asset_ids in restore_to.items():
        assert group_id is not None
        group = asset_groups_domain.get_active_group_or_none(db, group_id)
        if group is not None:
            asset_groups_domain.add_members(db, group, asset_ids, viewer=user)

    result = StockUndoResult(restored=restored, skipped=skipped)
    if undo.get("created"):
        group_id = _id(undo.get("group_id"))
        group = (
            asset_groups_domain.get_active_group_or_none(db, group_id)
            if group_id is not None
            else None
        )
        if group is not None:
            db.flush()
            if asset_groups_domain.active_member_count(db, group.id) == 0:
                asset_groups_domain.delete_group(db, group)
                db.flush()
                result.group_deleted = True
            else:
                result.group_kept = True
    return result


def _undo_rename(db: Session, user: CurrentUser, undo: dict[str, Any]) -> StockUndoResult:
    group_id = _id(undo.get("group_id"))
    group = get_visible_group(db, user, group_id) if group_id is not None else None
    if group is None or group.name != undo.get("after") or not isinstance(undo.get("before"), str):
        return StockUndoResult(restored=0, skipped=1)
    asset_groups_domain.rename_group(db, group, str(undo.get("before")), user)
    return StockUndoResult(restored=1, skipped=0)


# -- 元に戻す ---------------------------------------------------------------------------


def undo_stock(
    db: Session, user: CurrentUser, undo: dict[str, Any], settings: Any
) -> StockUndoResult:
    """戻す。何も戻せなければ `StockUndoConflictError`(何も変えない)。commit は呼び出し側。"""
    kind = undo.get("kind")
    if kind == KIND_TAGS:
        result = _undo_tags(db, user, undo)
    elif kind == KIND_TITLE:
        result = _undo_title(db, user, undo, settings)
    elif kind == KIND_GROUPS:
        result = _undo_groups(db, user, undo)
    elif kind == KIND_GROUP_RENAME:
        result = _undo_rename(db, user, undo)
    else:
        raise ValueError(f"not a stock undo: {kind}")
    if result.restored == 0 and not result.group_deleted:
        db.rollback()
        raise StockUndoConflictError()
    db.flush()
    return result


_WRITE_NOTE = (
    "Applied. The user sees a card with the images and an Undo button. Tell the user briefly "
    "what you changed and how many images."
)
