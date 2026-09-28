"""グローバル検索(ADR-0009 6章)。テキストの部分一致のみ(FTS・形態素解析は入れない)。

対象は Run の `prompt`、Asset は「それを生んだ Run の prompt」(kind=generated)に加えて
「画像に埋め込まれた生成情報の prompt / negative_prompt」(`asset.embedded_meta`、kind を
問わない。ADR-0018、2026-09-27 追記)、プロンプトセットは名前と項目本文。削除済みは除外する。

将来 embedding 検索を足すときは、このモジュール(`search()` の中身)だけを差し替えれば
よいように、API 層(app/api/search.py)からはここの関数を呼ぶだけにしている。

ADR-0025: 見る人(`viewer`)に見えるものだけを検索する(`app/domain/visibility.py`)。
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.auth.identity import CurrentUser
from app.domain.models import Asset, AssetKind, PromptSet, PromptSetItem, Run
from app.domain.run_views import (
    bulk_asset_groups,
    bulk_descendant_run_counts,
    bulk_input_summary,
    bulk_output_refs,
    bulk_users,
    run_summary_fields,
)
from app.domain.schemas import (
    SearchAssetHit,
    SearchPromptSetHit,
    SearchPromptSetItemHit,
    SearchResponse,
    SearchRunHit,
    SearchTruncated,
)
from app.domain.visibility import asset_visible, prompt_set_visible, run_visible
from app.i18n import t

VALID_TYPES = frozenset({"run", "asset", "prompt_set"})
DEFAULT_LIMIT = 20
MAX_LIMIT = 50
SNIPPET_CONTEXT = 60

_LIKE_ESCAPE_CHAR = "\\"


class InvalidSearchQueryError(ValueError):
    """検索クエリが不正な場合(空文字、未知の types など)。API 層で 422 に変換する。"""


def parse_query_terms(q: str) -> list[str]:
    """前後の空白を除き、空白区切りで単語に分ける。空なら InvalidSearchQueryError。"""
    stripped = q.strip()
    if not stripped:
        raise InvalidSearchQueryError(t("search.emptyQuery"))
    return stripped.split()


def parse_types(raw: str | None) -> set[str]:
    """`"run,asset,prompt_set"` のようなカンマ区切りを解釈する。省略時は全種別。"""
    if raw is None or not raw.strip():
        return set(VALID_TYPES)
    values = {v.strip() for v in raw.split(",") if v.strip()}
    unknown = values - VALID_TYPES
    if unknown:
        raise InvalidSearchQueryError(
            t("search.unknownTypes", types=sorted(unknown), validTypes=sorted(VALID_TYPES))
        )
    return values


def escape_like(term: str) -> str:
    """SQLite の LIKE 用に `\\` `%` `_` をエスケープする(`ESCAPE '\\'` と組で使う)。"""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _like_pattern(term: str) -> str:
    return f"%{escape_like(term.lower())}%"


def build_snippet(text: str, terms: list[str], context: int = SNIPPET_CONTEXT) -> str:
    """`text` 中で `terms` のいずれかが最初に見つかった位置の前後 `context` 文字を切り出す。

    大文字小文字は区別しない。一致が1つも見つからない場合(呼び出し側の想定外)は
    先頭を切り詰めて返す。純粋関数(DB非依存)なので単体テストしやすい。
    """
    lowered = text.lower()
    best_pos: int | None = None
    best_len = 0
    for term in terms:
        term_lower = term.lower()
        if not term_lower:
            continue
        pos = lowered.find(term_lower)
        if pos != -1 and (best_pos is None or pos < best_pos):
            best_pos = pos
            best_len = len(term)

    if best_pos is None:
        snippet = text[: context * 2]
        return snippet + ("…" if len(text) > len(snippet) else "")

    start = max(0, best_pos - context)
    end = min(len(text), best_pos + best_len + context)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{text[start:end]}{suffix}"


def _search_runs(
    db: Session, terms: list[str], limit: int, viewer: CurrentUser
) -> tuple[list[SearchRunHit], bool]:
    conditions = [
        func.lower(Run.prompt).like(_like_pattern(term), escape=_LIKE_ESCAPE_CHAR) for term in terms
    ]
    query = (
        select(Run)
        .where(Run.deleted_at.is_(None), run_visible(viewer), *conditions)
        .order_by(Run.queued_at.desc(), Run.id.desc())
        .limit(limit + 1)
    )
    rows = list(db.execute(query).scalars().all())
    truncated = len(rows) > limit
    rows = rows[:limit]

    run_ids = [r.id for r in rows]
    outputs_map = bulk_output_refs(db, run_ids)
    inputs_map = bulk_input_summary(db, run_ids, viewer)
    descendant_map = bulk_descendant_run_counts(db, run_ids, viewer)
    users_map = bulk_users(db, [r.created_by_user_id for r in rows])
    groups_map = bulk_asset_groups(db, [r.asset_group_id for r in rows], viewer)

    hits = [
        SearchRunHit(
            **run_summary_fields(
                run,
                outputs_map[run.id],
                *inputs_map[run.id],
                descendant_map[run.id],
                users_map.get(run.created_by_user_id),
                groups_map.get(run.asset_group_id),
            ),
            snippet=build_snippet(run.prompt, terms),
        )
        for run in rows
    ]
    return hits, truncated


def _embedded_prompt_text_expr() -> Any:
    """`asset.embedded_meta`(`gakei.embedded/1`)の prompt / negative_prompt を連結した、
    大文字小文字を無視した検索用テキストの SQL 式。

    SQLite の `json_extract` を使う。PostgreSQL へ移行して `embedded_meta` が JSONB に
    なったときは、この関数の中身だけを `Asset.embedded_meta["prompt"].astext` 相当の式に
    差し替えればよい(ADR-0008、ローカルMVPは SQLite)。
    """
    prompt = func.coalesce(func.json_extract(Asset.embedded_meta, "$.prompt"), "")
    negative_prompt = func.coalesce(func.json_extract(Asset.embedded_meta, "$.negative_prompt"), "")
    return func.lower(prompt.op("||")(" ").op("||")(negative_prompt))


def _search_assets(
    db: Session, terms: list[str], limit: int, viewer: CurrentUser
) -> tuple[list[SearchAssetHit], bool]:
    lowered_terms = [term.lower() for term in terms]
    run_prompt_conditions = [
        func.lower(Run.prompt).like(_like_pattern(term), escape=_LIKE_ESCAPE_CHAR) for term in terms
    ]
    embedded_text = _embedded_prompt_text_expr()
    embedded_conditions = [
        embedded_text.like(_like_pattern(term), escape=_LIKE_ESCAPE_CHAR) for term in terms
    ]
    # それを生んだ Run の prompt が一致(kind=generated、Run 未削除)、または画像に
    # 埋め込まれた生成情報が一致(kind を問わない。ADR-0018)のどちらか。
    query = (
        select(Asset, Run.prompt)
        .outerjoin(Run, Asset.produced_by_run_id == Run.id)
        .where(
            Asset.deleted_at.is_(None),
            asset_visible(viewer),
            or_(
                and_(
                    Asset.kind == AssetKind.GENERATED,
                    Run.deleted_at.is_(None),
                    *run_prompt_conditions,
                ),
                and_(*embedded_conditions),
            ),
        )
        .order_by(Asset.created_at.desc(), Asset.id.desc())
        .limit(limit + 1)
    )
    rows = db.execute(query).all()
    truncated = len(rows) > limit
    rows = rows[:limit]

    hits: list[SearchAssetHit] = []
    for asset, run_prompt in rows:
        run_matched = run_prompt is not None and all(
            term in run_prompt.lower() for term in lowered_terms
        )
        prompt_source: Literal["run", "embedded"]
        if run_matched:
            prompt_source = "run"
            snippet_text = run_prompt
        else:
            prompt_source = "embedded"
            embedded_meta = asset.embedded_meta or {}
            embedded_prompt = embedded_meta.get("prompt") or ""
            embedded_negative = embedded_meta.get("negative_prompt") or ""
            if all(term in embedded_prompt.lower() for term in lowered_terms):
                snippet_text = embedded_prompt
            else:
                snippet_text = embedded_negative or embedded_prompt
        hits.append(
            SearchAssetHit(
                id=asset.id,
                kind=asset.kind,
                mime=asset.mime,
                width=asset.width,
                height=asset.height,
                bytes=asset.bytes,
                created_at=asset.created_at,
                produced_by_run_id=asset.produced_by_run_id,
                prompt_source=prompt_source,
                prompt_snippet=build_snippet(snippet_text, terms),
            )
        )
    return hits, truncated


def _search_prompt_sets(
    db: Session, terms: list[str], limit: int, viewer: CurrentUser
) -> tuple[list[SearchPromptSetHit], bool]:
    prompt_sets = (
        db.execute(
            select(PromptSet)
            .where(PromptSet.deleted_at.is_(None), prompt_set_visible(viewer))
            .order_by(PromptSet.updated_at.desc(), PromptSet.id.desc())
        )
        .scalars()
        .all()
    )
    if not prompt_sets:
        return [], False

    prompt_set_ids = [ps.id for ps in prompt_sets]
    items_by_set: dict[uuid.UUID, list[PromptSetItem]] = {psid: [] for psid in prompt_set_ids}
    item_rows = (
        db.execute(
            select(PromptSetItem)
            .where(
                PromptSetItem.prompt_set_id.in_(prompt_set_ids),
                PromptSetItem.deleted_at.is_(None),
            )
            .order_by(PromptSetItem.position)
        )
        .scalars()
        .all()
    )
    for item in item_rows:
        items_by_set[item.prompt_set_id].append(item)

    # 件数が少ない前提(prompt_set 一覧はページングなし)なので、名前×項目本文の
    # 横断 AND 一致を SQL に落とし込まず Python で判定する。
    lowered_terms = [t.lower() for t in terms]
    all_hits: list[SearchPromptSetHit] = []
    for prompt_set in prompt_sets:
        items = items_by_set[prompt_set.id]
        name_lower = prompt_set.name.lower()

        def _term_in_set(
            term: str,
            items: list[PromptSetItem] = items,
            name_lower: str = name_lower,
        ) -> bool:
            if term in name_lower:
                return True
            return any(term in item.text.lower() for item in items)

        if not all(_term_in_set(term) for term in lowered_terms):
            continue

        matched_items = [
            item for item in items if any(term in item.text.lower() for term in lowered_terms)
        ]
        all_hits.append(
            SearchPromptSetHit(
                id=prompt_set.id,
                name=prompt_set.name,
                matched_items=[
                    SearchPromptSetItemHit(
                        id=item.id, label=item.label, snippet=build_snippet(item.text, terms)
                    )
                    for item in matched_items
                ],
            )
        )

    truncated = len(all_hits) > limit
    return all_hits[:limit], truncated


def search(
    db: Session,
    q: str,
    *,
    viewer: CurrentUser,
    limit: int = DEFAULT_LIMIT,
    types: set[str] | None = None,
) -> SearchResponse:
    """テキストの部分一致でRun/Asset/プロンプトセットを横断検索する。"""
    terms = parse_query_terms(q)
    selected_types: set[str] = types if types is not None else set(VALID_TYPES)

    run_hits: list[SearchRunHit] = []
    asset_hits: list[SearchAssetHit] = []
    prompt_set_hits: list[SearchPromptSetHit] = []
    truncated_kwargs: dict[str, Any] = {}

    if "run" in selected_types:
        run_hits, truncated_kwargs["runs"] = _search_runs(db, terms, limit, viewer)
    if "asset" in selected_types:
        asset_hits, truncated_kwargs["assets"] = _search_assets(db, terms, limit, viewer)
    if "prompt_set" in selected_types:
        prompt_set_hits, truncated_kwargs["prompt_sets"] = _search_prompt_sets(
            db, terms, limit, viewer
        )

    return SearchResponse(
        query=q.strip(),
        runs=run_hits,
        assets=asset_hits,
        prompt_sets=prompt_set_hits,
        truncated=SearchTruncated(**truncated_kwargs),
    )
