"""アシスタントが LLM に送る情報の形と絞り込み(ADR-0048 14章)。

- X 以上の画像(14.3): アシスタントの接続先の `locality` が `external` のとき、段階が `x`・`xx`・
  `xxx` の画像(ADR-0049。`asset_content_rating.level` は人が選んだ段階を優先した値)は、ID、
  種類、段階、グループだけを送り、`withheld: true` を付ける。判定は `withholds` の1か所。
  未判定の画像は対象にしない。接続先が `local` なら絞らない。
- 一覧の1件の短い形(14.4): ID、種類、タイトル、タグの名前(人のタグを先に最大 12 個)、段階、
  グループの名前。
- 件数での打ち切り(14.4): 一覧は `LIST_BUDGET_CHARS` に収まる件数で切り、`returned`・`total`・
  `next_offset` を付ける。文字数での最後の切り捨ては `cap_text` で行い、必ず `truncated: true`
  を先頭に付ける。
- 画面用の値(14.4): 整理の結果の `preview`、`list_groups` の `cover_asset_id`・`position`・
  日時、`show_assets` の焦点・幅・高さ・日時は、保存はするが LLM には送らない(`strip_display`)。
- 古い道具の結果(14.5): 画像の ID の一覧と件数だけを残して短くする(`compact_payload`)。
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.domain import content_rating as content_rating_domain
from app.domain import llm_connections
from app.domain import locality as locality_domain
from app.domain.assistant_settings import AssistantConfig
from app.i18n import t

# 1つの道具の結果を LLM に送る上限(文字)。
TOOL_RESULT_MAX_CHARS = 20_000
# 一覧の道具が件数で打ち切るときの目安(注記などの分を残す)。
LIST_BUDGET_CHARS = TOOL_RESULT_MAX_CHARS - 2_000
# 一覧の1件で送るタグの数の上限(人が付けたものを先に)。
COMPACT_TAGS_MAX = 12
# 古い道具の結果を短くする条件(14.5): 直近のこの件数より前で、この文字数を超えるもの。
RECENT_TOOL_RESULTS_KEPT = 3
OLD_RESULT_MAX_CHARS = 2_000

# 外部の接続先に情報を送らない段階(14.3)。
WITHHELD_LEVELS: frozenset[str] = frozenset(
    {content_rating_domain.LEVEL_X, content_rating_domain.LEVEL_XX, content_rating_domain.LEVEL_XXX}
)

WITHHELD_NOTE = (
    "Withheld: this image is rated X or higher and the assistant's LLM connection is external, "
    "so GAKEI does not send its title, tags, prompt, parameters or image. Its id still works "
    "with the organizing tools."
)
WITHHELD_IMAGE_NOTE = (
    "Not sent: GAKEI does not send images rated X or higher to an external LLM connection."
)


def withholds(level: str | None, *, external: bool) -> bool:
    """X 以上の画像の情報を LLM に送らないか(14.3)。判定はこの1か所で行う。

    接続先が外部で、段階が X・XX・XXX のときだけ True。未判定(None)は段階が分からないので
    対象にしない。"""
    return external and level in WITHHELD_LEVELS


@dataclass(frozen=True)
class Disclosure:
    """アシスタントの接続先についての、送る情報の決まり。"""

    external: bool
    connection_name: str

    def withheld_ids(self, db: Session, asset_ids: Iterable[uuid.UUID]) -> set[uuid.UUID]:
        """`asset_ids` のうち、情報を送らない画像。"""
        if not self.external:
            return set()
        levels = content_rating_domain.bulk_levels(db, asset_ids)
        return {i for i, level in levels.items() if withholds(level, external=True)}

    def excluded_levels(self) -> frozenset[str]:
        """数え上げ(`list_tags`、`suggest_groups` の特徴的なタグ)から外す段階。"""
        return WITHHELD_LEVELS if self.external else frozenset()


def resolve_disclosure(db: Session, settings: Any, config: AssistantConfig) -> Disclosure:
    """アシスタントの接続先(管理者設定)の `locality` と名前(ADR-0051 3章)。"""
    connection = llm_connections.find_connection(
        llm_connections.load(db), config.connection_id
    ) or llm_connections.ConnectionConfig(id=config.connection_id, name="", base_url=None)
    locality, _source = locality_domain.connection_locality(
        db, settings, builtin=connection.builtin, value=connection.locality
    )
    name = connection.name or (
        t("assistant.builtinConnectionName") if connection.builtin else connection.id
    )
    return Disclosure(external=locality == "external", connection_name=name)


# -- 1件の形 -----------------------------------------------------------------------


def _group_name(group: Any) -> str | None:
    if isinstance(group, dict):
        name = group.get("name")
        return str(name) if name is not None else None
    if isinstance(group, str):
        return group
    return None


def withheld_entry(asset_id: Any, kind: Any, level: str | None, group: Any) -> dict[str, Any]:
    """X 以上の画像の1件(ID、種類、段階、グループだけ)。"""
    return {
        "asset_id": str(asset_id),
        "kind": str(kind) if kind is not None else None,
        "content_rating": level,
        "group": _group_name(group),
        "withheld": True,
    }


def tag_names(tags: Any, limit: int = COMPACT_TAGS_MAX) -> list[str]:
    """タグの名前(人が付けたものを先に、最大 `limit` 個)。`tags` は `AssetTagRef` の辞書か名前。"""
    if not isinstance(tags, list):
        return []
    user: list[str] = []
    auto: list[str] = []
    for tag in tags:
        if isinstance(tag, dict):
            name = tag.get("name")
            if not isinstance(name, str):
                continue
            (user if tag.get("source") == "user" else auto).append(name)
        elif isinstance(tag, str):
            auto.append(tag)
    return list(dict.fromkeys([*user, *auto]))[:limit]


def compact_asset(entry: dict[str, Any], *, external: bool) -> dict[str, Any]:
    """一覧の1件の短い形(14.4)。`entry` は検索の結果の1件(`asset_search._decorate` の形)。"""
    level = entry.get("content_rating")
    if withholds(level, external=external):
        return withheld_entry(entry.get("asset_id"), entry.get("kind"), level, entry.get("group"))
    out: dict[str, Any] = {
        "asset_id": entry.get("asset_id"),
        "kind": entry.get("kind"),
        "title": entry.get("title"),
        "tags": tag_names(entry.get("tags")),
        "content_rating": level,
        "group": _group_name(entry.get("group")),
    }
    if "score" in entry:
        out["score"] = entry["score"]
    if "matched" in entry:
        out["matched"] = entry["matched"]
    return out


# -- 件数での打ち切り ----------------------------------------------------------------


def _json_len(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str))


def fit_count(base: dict[str, Any], items: list[Any], budget: int | None = None) -> int:
    """`base` に `items` を先頭から足して `budget` 文字に収まる件数。最低 1 件は入れる
    (1件でも収まらなければ、最後の切り捨てで `truncated: true` が付く)。"""
    budget = LIST_BUDGET_CHARS if budget is None else budget
    size = _json_len(base) + len(', "items": []') + 64
    count = 0
    for item in items:
        size += _json_len(item) + 2
        if size > budget and count > 0:
            break
        count += 1
    return count


def paged(
    items: list[Any],
    *,
    offset: int,
    has_more: bool,
    total: int | None,
    extra: dict[str, Any] | None = None,
    key: str = "items",
    budget: int | None = None,
) -> dict[str, Any]:
    """一覧の結果(14.4)。予算に収まる件数で切り、`returned`・`total`・`next_offset` を付ける。

    `items` は `offset` から先の候補。`has_more` は `items` の後にも続きがあるか。続きが
    あれば `next_offset`(無ければ null)。"""
    base: dict[str, Any] = dict(extra or {})
    base.update({"offset": offset, "returned": 0, "total": total, "next_offset": None})
    count = fit_count(base, items, budget) if items else 0
    shown = items[:count]
    more = has_more or count < len(items)
    base[key] = shown
    base["returned"] = len(shown)
    base["next_offset"] = offset + len(shown) if more else None
    if more:
        base["more_note"] = (
            "There are more results. Call again with offset=next_offset to read them before "
            "acting on the whole set."
        )
    return base


# -- 送る直前の整形 -------------------------------------------------------------------

_STOCK_PREVIEW_TOOLS = frozenset(
    {
        "add_tags",
        "remove_tags",
        "set_title",
        "create_group",
        "rename_group",
        "move_to_group",
        "remove_from_group",
    }
)
_GROUP_KEYS = ("id", "name", "member_count")
_SHOWN_KEYS = ("asset_id", "kind", "title", "content_rating", "withheld", "group", "tags")


def strip_display(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """画面のためだけの値を落とす(14.4)。保存した結果は変えない(写しを返す)。"""
    out = dict(payload)
    if name in _STOCK_PREVIEW_TOOLS:
        out.pop("preview", None)
    elif name == "list_groups" and isinstance(out.get("items"), list):
        out["items"] = [
            {k: g.get(k) for k in _GROUP_KEYS if k in g} if isinstance(g, dict) else g
            for g in out["items"]
        ]
    elif name == "show_assets" and isinstance(out.get("assets"), list):
        out["assets"] = [
            {k: a.get(k) for k in _SHOWN_KEYS if k in a} if isinstance(a, dict) else a
            for a in out["assets"]
        ]
    return out


_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
# 短くした結果にも残す、上の段の値(数・真偽値のほかに残す文字の項目)。
_KEPT_STRING_KEYS = frozenset(
    {"operation", "mode", "status", "error", "run_id", "prompt_set_id", "suggestion_id"}
)


def _collect_asset_ids(value: Any, key: str | None, out: list[str]) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            _collect_asset_ids(v, k, out)
    elif isinstance(value, list):
        for v in value:
            _collect_asset_ids(v, key, out)
    elif isinstance(value, str) and key is not None and "asset_id" in key and _UUID_RE.match(value):
        out.append(value)


def compact_payload(payload: dict[str, Any], *, reason: str) -> dict[str, Any]:
    """道具の結果を、画像の ID の一覧と件数だけにする(14.5)。タイトル、タグ、プロンプトなどは
    落とす。上の段の数・真偽値と、決まった短い文字の項目(操作、状態など)は残す。"""
    ids: list[str] = []
    _collect_asset_ids(payload, None, ids)
    unique = list(dict.fromkeys(ids))
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, bool | int | float) or value is None:
            if value is not None:
                out[key] = value
        elif isinstance(value, str) and key in _KEPT_STRING_KEYS and len(value) <= 500:
            out[key] = value
    run_ids = payload.get("run_ids")
    if isinstance(run_ids, list):
        out["run_ids"] = [str(r) for r in run_ids]
    out["compacted"] = True
    out["asset_ids"] = unique
    out["asset_count"] = len(unique)
    out["compacted_note"] = reason
    return out


OLD_RESULT_REASON = (
    "This older tool result was shortened to save space: only image ids and counts are kept. "
    "Call the tool again if you need the titles, tags or other details."
)
LOCAL_RESULT_REASON = (
    "This tool result was made for a local LLM connection and is not sent to the current "
    "external connection: only image ids and counts are kept."
)


def cap_text(text: str, limit: int = TOOL_RESULT_MAX_CHARS) -> str:
    """文字数での最後の切り捨て。切ったときは、先頭の行に必ず `truncated: true` を書く。"""
    if len(text) <= limit:
        return text
    header = json.dumps(
        {
            "truncated": True,
            "note": (
                f"This tool result was cut at {limit} characters; the JSON below is incomplete "
                "and some items are missing. Use offset or narrower arguments to read the rest."
            ),
        }
    )
    return header + "\n" + text[: max(0, limit - len(header) - 1)]
