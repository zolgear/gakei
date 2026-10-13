"""ストックの捜索の組み立て(MCP とアシスタントで共用。ADR-0023 7章、ADR-0048 10章)。

MCP の `search_assets` / `find_similar_assets` / `get_asset` と、アシスタントの同名の道具が
同じ関数を呼ぶ。検索そのものは `domain/search.py`(キーワード)と `domain/semantic_search.py`
(画像の埋め込み。ADR-0033)にあり、ここでは絞り込みと結果の辞書の組み立てだけを行う。

見える範囲は REST と同じ(ADR-0025)。結果に画像の中身は入れない。結果の1件の土台
(Asset ID や URL)は呼ぶ側が `brief` で渡す(MCP は URL を足し、アシスタントは足さない)。
失敗は `AssetSearchError`(メッセージは道具のエラーとしてそのまま返す)。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any, Literal

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.auth.identity import CurrentUser
from app.domain import annotations as annotations_domain
from app.domain import semantic_search as semantic_domain
from app.domain.asset_groups import group_for_asset, list_groups
from app.domain.models import Asset, AssetGroupMember, Run, RunInput, RunInputRole
from app.domain.run_views import run_text_outputs
from app.domain.search import MAX_LIMIT as SEARCH_MAX_LIMIT
from app.domain.search import InvalidSearchQueryError, search
from app.domain.visibility import asset_visible, get_visible_group, visible_asset_ids

# 1回の検索で返す件数の上限(REST の検索と同じ)。
SEARCH_LIMIT_MAX = SEARCH_MAX_LIMIT

SearchMode = Literal["keyword", "semantic"]
AssetBrief = Callable[[Asset], dict[str, Any]]


class AssetSearchError(Exception):
    """検索の失敗(見えないグループ、埋め込みが無効など)。メッセージは英語で、道具の
    エラーとしてエージェントにそのまま返す。"""


SEMANTIC_ERRORS = (
    semantic_domain.EmbeddingsConflictError,
    semantic_domain.EmbeddingsNotFoundError,
    semantic_domain.EmbeddingsInvalidQueryError,
    semantic_domain.EmbeddingsEngineError,
)

EMBEDDINGS_UNAVAILABLE_MESSAGE = (
    "Image embeddings are not available (turned off or no model is ready in the "
    "GAKEI admin settings). Use search_assets in keyword mode instead."
)


def semantic_error(e: Exception) -> AssetSearchError:
    """埋め込みの検索の失敗を、エージェント向けの文に直す。"""
    if isinstance(e, semantic_domain.EmbeddingsConflictError):
        if e.code == "embeddings_unavailable":
            return AssetSearchError(EMBEDDINGS_UNAVAILABLE_MESSAGE)
        if e.code == "embedding_pending":
            return AssetSearchError(
                "The embedding of this image is still being computed. Try later."
            )
        if e.code == "embedding_not_supported":
            return AssetSearchError("Masks have no embeddings.")
        return AssetSearchError(
            "This image has no embedding yet (it was not computed or computing failed). "
            "It can be computed from the image details in the GAKEI web UI."
        )
    if isinstance(e, semantic_domain.EmbeddingsEngineError):
        return AssetSearchError(f"Could not compute the embedding of the query: {e}")
    return AssetSearchError(str(e))


def text_outputs_payload(run: Run) -> list[dict[str, Any]] | None:
    """最終プロンプト(PE の出力)など、実行時に作られたテキスト(ADR-0030 4章)。無ければ None。"""
    items = run_text_outputs(run)
    if items is None:
        return None
    return [item.model_dump(mode="json") for item in items]


def _decorate(
    db: Session,
    viewer: CurrentUser,
    assets: list[Asset],
    brief: AssetBrief,
    *,
    scores: dict[uuid.UUID, float] | None = None,
    snippets: dict[uuid.UUID, str | None] | None = None,
) -> list[dict[str, Any]]:
    """結果の1件ずつに、タイトル、タグ、グループ(と類似度・プロンプトの抜粋)を足す。"""
    asset_ids = [a.id for a in assets]
    titles = annotations_domain.bulk_titles(db, asset_ids)
    tags_map = annotations_domain.bulk_tags(db, asset_ids)
    items = []
    for asset in assets:
        item = brief(asset)
        if scores is not None:
            item["score"] = round(scores[asset.id], 4)
        item["title"] = titles.get(asset.id)
        item["tags"] = [ref.model_dump(mode="json") for ref in tags_map.get(asset.id, [])]
        group = group_for_asset(db, asset.id, viewer)
        item["group"] = group.model_dump(mode="json") if group else None
        if snippets is not None and asset.id in snippets:
            item["prompt_snippet"] = snippets[asset.id]
        items.append(item)
    return items


def scored_items(
    db: Session,
    viewer: CurrentUser,
    hits: list[tuple[uuid.UUID, float]],
    brief: AssetBrief,
) -> tuple[list[dict[str, Any]], list[Asset]]:
    """類似度つきの結果(文章での検索、似た画像)。並びは `hits` の順(類似度の高い順)。"""
    ids = [asset_id for asset_id, _ in hits]
    assets_by_id = (
        {a.id: a for a in db.execute(select(Asset).where(Asset.id.in_(ids))).scalars().all()}
        if ids
        else {}
    )
    assets = [assets_by_id[asset_id] for asset_id, _ in hits if asset_id in assets_by_id]
    scores = dict(hits)
    return _decorate(db, viewer, assets, brief, scores=scores), assets


def search_assets(
    db: Session,
    state: Any,
    viewer: CurrentUser,
    brief: AssetBrief,
    *,
    query: str | None,
    mode: SearchMode = "keyword",
    kind: str | None = None,
    group_id: uuid.UUID | None = None,
    tag: str | None = None,
    limit: int = 20,
) -> tuple[dict[str, Any], list[Asset]]:
    """ストックを探す。`state` は FastAPI の `app.state`(settings、vector_index、embedder、
    query_vector_cache)。返すのは結果の辞書(`mode`、`items`、`truncated` など)と、結果の
    Asset(呼ぶ側がサムネイルを付けるため)。"""
    tag_value = tag.strip() if tag and tag.strip() else None
    if group_id is not None and get_visible_group(db, viewer, group_id) is None:
        raise AssetSearchError(f"Group {group_id} not found.")
    if mode == "semantic":
        if not query or not query.strip():
            raise AssetSearchError("semantic mode needs a query (a description of the image).")
        if kind == "mask":
            # REST(`GET /api/search/semantic`)と同じく、マスクは種類に指定できない
            # (マスクには埋め込みが無い。ADR-0033)。
            raise AssetSearchError(
                "Masks have no embeddings: kind 'mask' cannot be used in semantic mode."
            )
        try:
            response = semantic_domain.semantic_search(
                db,
                settings=state.settings,
                index=state.vector_index,
                engines=state.embedder.engines,
                cache=state.query_vector_cache,
                viewer=viewer,
                q=query,
                limit=limit,
                group_id=group_id,
                tag=tag_value,
                kind=kind,
            )
        except SEMANTIC_ERRORS as e:
            raise semantic_error(e) from e
        items, assets = scored_items(db, viewer, [(h.id, h.score) for h in response.assets], brief)
        payload = {
            "mode": "semantic",
            "model_key": response.model_key,
            "multilingual": response.multilingual,
            "truncated": False,
            "items": items,
        }
        return payload, assets

    tag_clause = None
    if tag_value is not None:
        try:
            tag_clause = annotations_domain.tag_filter(tag_value)
        except annotations_domain.TagNameError as e:
            raise AssetSearchError(str(e)) from e
    snippets: dict[uuid.UUID, str | None] = {}
    if query and query.strip():
        try:
            hits = search(
                db,
                query,
                viewer=viewer,
                limit=SEARCH_LIMIT_MAX,
                types={"asset"},
                tag=tag_value,
            ).assets
        except InvalidSearchQueryError as e:
            raise AssetSearchError(str(e)) from e
        snippets = {h.id: h.prompt_snippet for h in hits}
        ids = [h.id for h in hits]
        assets_by_id = {
            a.id: a
            for a in db.execute(select(Asset).where(Asset.id.in_(ids), asset_visible(viewer)))
            .scalars()
            .all()
        }
        assets = [assets_by_id[i] for i in ids if i in assets_by_id]
        if kind is not None:
            assets = [a for a in assets if a.kind == kind]
        if group_id is not None:
            member_ids = set(
                db.execute(
                    select(AssetGroupMember.asset_id).where(
                        AssetGroupMember.asset_group_id == group_id
                    )
                )
                .scalars()
                .all()
            )
            assets = [a for a in assets if a.id in member_ids]
    else:
        q = select(Asset).where(Asset.deleted_at.is_(None), asset_visible(viewer))
        if kind is not None:
            q = q.where(Asset.kind == kind)
        if tag_clause is not None:
            q = q.where(tag_clause)
        if group_id is not None:
            q = q.join(
                AssetGroupMember,
                and_(
                    AssetGroupMember.asset_id == Asset.id,
                    AssetGroupMember.asset_group_id == group_id,
                ),
            )
        q = q.order_by(Asset.created_at.desc(), Asset.id.desc()).limit(limit + 1)
        assets = list(db.execute(q).scalars().all())

    truncated = len(assets) > limit
    assets = assets[:limit]
    items = _decorate(db, viewer, assets, brief, snippets=snippets)
    return {"mode": "keyword", "items": items, "truncated": truncated}, assets


def find_similar_assets(
    db: Session,
    state: Any,
    viewer: CurrentUser,
    brief: AssetBrief,
    *,
    asset_id: uuid.UUID,
    limit: int = 12,
) -> tuple[dict[str, Any], list[Asset]]:
    """指定した画像に似た画像(近い順。起点は含まない)。起点が見えなければ「無い」と同じ。"""
    try:
        response = semantic_domain.similar_assets(
            db,
            settings=state.settings,
            index=state.vector_index,
            viewer=viewer,
            asset_id=asset_id,
            limit=limit,
        )
    except semantic_domain.EmbeddingsNotFoundError as e:
        raise AssetSearchError(f"Asset {asset_id} not found.") from e
    except SEMANTIC_ERRORS as e:
        raise semantic_error(e) from e
    items, assets = scored_items(db, viewer, [(h.id, h.score) for h in response.assets], brief)
    payload = {"asset_id": str(asset_id), "model_key": response.model_key, "items": items}
    return payload, assets


def asset_details(db: Session, viewer: CurrentUser, asset: Asset) -> dict[str, Any]:
    """1枚の画像の情報: タイトル(と出どころ)、タグ、グループ、生成した Run(プロンプト、
    モデル、パラメーター、実行時のテキスト)、主たる親(見えるときだけ)。"""
    payload: dict[str, Any] = {}
    fields = annotations_domain.annotation_fields(db, asset.id)
    payload["title"] = fields["title"]
    payload["title_source"] = fields["title_source"]
    payload["tags"] = [ref.model_dump(mode="json") for ref in fields["tags"]]
    group = group_for_asset(db, asset.id, viewer)
    payload["group"] = group.model_dump(mode="json") if group else None
    payload["produced_by_run"] = None
    payload["primary_parent_asset_id"] = None
    if asset.produced_by_run_id is not None:
        run = db.get(Run, asset.produced_by_run_id)
        if run is not None:
            payload["produced_by_run"] = {
                "run_id": str(run.id),
                "status": str(run.status),
                "operation": str(run.operation),
                "provider": run.provider,
                "model": run.model,
                "prompt": run.prompt,
                "params": run.params or {},
                "origin": run.origin,
                "text_outputs": text_outputs_payload(run),
            }
            parent = db.execute(
                select(RunInput.asset_id).where(
                    RunInput.run_id == run.id,
                    RunInput.role == RunInputRole.IMAGE,
                    RunInput.position == 0,
                )
            ).scalar_one_or_none()
            if parent is not None and not visible_asset_ids(db, viewer, [parent]):
                parent = None
            payload["primary_parent_asset_id"] = str(parent) if parent else None
    return payload


def groups_payload(db: Session, viewer: CurrentUser) -> dict[str, Any]:
    """本人に見えるグループの一覧(メンバーの数つき)。"""
    return {"items": [g.model_dump(mode="json") for g in list_groups(db, viewer)]}
