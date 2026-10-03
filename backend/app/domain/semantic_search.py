"""埋め込みを使った検索(ADR-0033 6章・7章)。REST(`app/api/embeddings.py`)と MCP が使う。

- 文章での検索: 検索の文章を使うモデルの文章側で1件だけ計算し(`QueryVectorCache` で
  覚える)、近い画像を返す。推論は同期で行うので、呼び出し側はスレッドで呼ぶ(FastAPI の
  同期エンドポイント、MCP の `_in_thread`)。
- 似た画像: 起点の画像のベクトルに近い画像(起点は除く)。ベクトルがまだ無ければ理由を返す
  (計算中、失敗、未計算)。
- 重複の候補: 類似度がしきい値以上で、かつ知覚ハッシュが近い組を、代表との類似度でまとめる
  (ADR-0033 12章)。
- マップ: ノード(新しい順に上限まで)と、各ノードの k 近傍(自分自身を先頭に含める)。

見える範囲(ADR-0025)は近傍を探す前に掛ける(`vector_index.AssetFilter`)。埋め込みが無効、
または使えるモデルが無い間は `EmbeddingsConflictError(embeddings_unavailable)`(API では 409)。
"""

from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.auth.identity import CurrentUser
from app.domain import annotations as annotations_domain
from app.domain import embedding_settings, perceptual_hash
from app.domain import embeddings as embeddings_domain
from app.domain.models import Asset, AssetKind, AssetPerceptualHash, RunInput, RunInputRole
from app.domain.schemas import (
    DuplicateAsset,
    DuplicateGroup,
    DuplicatesResponse,
    EmbeddingGraphNode,
    EmbeddingGraphResponse,
    SemanticAssetHit,
    SemanticSearchResponse,
    SimilarAssetsResponse,
)
from app.domain.vector_index import (
    AssetFilter,
    Hit,
    VectorIndex,
    candidate_ids,
    knn_matrix,
    neighbors,
    pairs_at_least,
)
from app.domain.visibility import get_visible_asset, get_visible_group
from app.embedding.base import EmbeddingError
from app.embedding.catalog import CLIP_MODELS
from app.i18n import t

if TYPE_CHECKING:
    from app.config import Settings
    from app.embedding.engines import EmbeddingEngines

SEARCH_DEFAULT_LIMIT = 20
SEARCH_MAX_LIMIT = 50
QUERY_MAX_CHARS = 1000

GRAPH_DEFAULT_K = 10
GRAPH_MAX_K = 30
GRAPH_DEFAULT_LIMIT = 1000
GRAPH_MAX_LIMIT = 5000

# 重複の候補で比べる画像の上限(新しい順)。全組の内積なので、件数の2乗で重くなる
# (5000 件で 5000×5000×512 の積和。Raspberry Pi 5 で数秒)。
DUPLICATES_MAX_ASSETS = 5000
DUPLICATES_DEFAULT_GROUPS = 100
DUPLICATES_MAX_GROUPS = 500

QUERY_CACHE_SIZE = 128


class EmbeddingsConflictError(Exception):
    """409 にする(`code` は `EmbeddingErrorDetail.code`)。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class EmbeddingsNotFoundError(Exception):
    """起点の画像やグループが無い・見えない(404)。"""


class EmbeddingsInvalidQueryError(ValueError):
    """検索の文章やタグが不正(422)。"""


class EmbeddingsEngineError(Exception):
    """検索の文章の推論に失敗した(503。モデルが読めない、推論サーバーに届かないなど)。"""


@dataclass(frozen=True)
class ActiveModel:
    config: embedding_settings.EmbeddingConfig
    model_key: str
    # 対応言語(ローカルのモデルだけ分かる)。
    languages: tuple[str, ...] | None

    @property
    def multilingual(self) -> bool | None:
        return None if self.languages is None else "ja" in self.languages


def active_languages(config: embedding_settings.EmbeddingConfig) -> tuple[str, ...] | None:
    if config.engine == "onnx" and config.onnx_model in CLIP_MODELS:
        return tuple(CLIP_MODELS[config.onnx_model].languages)
    return None


def require_active(db: Session, settings: Settings) -> ActiveModel:
    """使うモデル。埋め込みが無効、または使えなければ EmbeddingsConflictError。"""
    config = embedding_settings.load(db)
    model_key = embedding_settings.active_model_key(config, settings)
    if model_key is None or not embedding_settings.usable(config, settings):
        raise EmbeddingsConflictError("embeddings_unavailable", t("embeddings.notUsable"))
    return ActiveModel(config=config, model_key=model_key, languages=active_languages(config))


# -- 検索の文章のベクトル ----------------------------------------------------------


class QueryVectorCache:
    """検索の文章のベクトルを (model_key, 文章) ごとに覚える(少数の LRU)。"""

    def __init__(self, size: int = QUERY_CACHE_SIZE) -> None:
        self.size = size
        self._lock = threading.Lock()
        self._items: OrderedDict[tuple[str, str], np.ndarray] = OrderedDict()

    def get(self, model_key: str, text: str) -> np.ndarray | None:
        with self._lock:
            value = self._items.get((model_key, text))
            if value is not None:
                self._items.move_to_end((model_key, text))
            return value

    def put(self, model_key: str, text: str, vector: np.ndarray) -> None:
        with self._lock:
            self._items[(model_key, text)] = vector
            self._items.move_to_end((model_key, text))
            while len(self._items) > self.size:
                self._items.popitem(last=False)


def embed_query(
    db: Session,
    active: ActiveModel,
    text: str,
    engines: EmbeddingEngines,
    cache: QueryVectorCache,
) -> np.ndarray:
    """検索の文章のベクトル(同期。呼び出し側がスレッドで呼ぶ)。"""
    cached = cache.get(active.model_key, text)
    if cached is not None:
        return cached
    try:
        engine = engines.engine_for(db, active.config)
        vectors = engine.embed_texts([text])
    except EmbeddingError as e:
        raise EmbeddingsEngineError(str(e)) from e
    if vectors.shape[0] != 1:
        raise EmbeddingsEngineError(t("embeddings.remoteInvalidResponse"))
    vector = np.asarray(vectors[0], dtype=np.float32)
    cache.put(active.model_key, text, vector)
    return vector


# -- 絞り込み ----------------------------------------------------------------------


def build_filter(
    db: Session,
    viewer: CurrentUser,
    *,
    group_id: uuid.UUID | None = None,
    tag: str | None = None,
    kind: str | None = None,
) -> AssetFilter:
    """グループが見えなければ EmbeddingsNotFoundError、タグ名が不正なら
    EmbeddingsInvalidQueryError。"""
    if group_id is not None and get_visible_group(db, viewer, group_id) is None:
        raise EmbeddingsNotFoundError(t("assetGroups.notFound"))
    tag_value = tag.strip() if tag is not None and tag.strip() else None
    if tag_value is not None:
        try:
            annotations_domain.normalize_tag_name(tag_value)
        except annotations_domain.TagNameError as e:
            raise EmbeddingsInvalidQueryError(str(e)) from e
    return AssetFilter(viewer=viewer, group_id=group_id, tag=tag_value, kind=kind)


def _hits_to_assets(db: Session, hits: Sequence[Hit]) -> list[SemanticAssetHit]:
    ids = [h.asset_id for h in hits]
    if not ids:
        return []
    assets = {a.id: a for a in db.execute(select(Asset).where(Asset.id.in_(ids))).scalars()}
    titles = annotations_domain.bulk_titles(db, ids)
    result: list[SemanticAssetHit] = []
    for hit in hits:
        asset = assets.get(hit.asset_id)
        if asset is None:
            continue
        result.append(
            SemanticAssetHit(
                id=asset.id,
                kind=asset.kind,
                mime=asset.mime,
                width=asset.width,
                height=asset.height,
                bytes=asset.bytes,
                created_at=asset.created_at,
                title=titles.get(asset.id),
                score=round(hit.score, 6),
            )
        )
    return result


# -- 文章での検索 -------------------------------------------------------------------


def semantic_search(
    db: Session,
    *,
    settings: Settings,
    index: VectorIndex,
    engines: EmbeddingEngines,
    cache: QueryVectorCache,
    viewer: CurrentUser,
    q: str,
    limit: int = SEARCH_DEFAULT_LIMIT,
    group_id: uuid.UUID | None = None,
    tag: str | None = None,
    kind: str | None = None,
) -> SemanticSearchResponse:
    text = q.strip()
    if not text:
        raise EmbeddingsInvalidQueryError(t("search.emptyQuery"))
    if len(text) > QUERY_MAX_CHARS:
        raise EmbeddingsInvalidQueryError(t("embeddings.queryTooLong", max=QUERY_MAX_CHARS))
    active = require_active(db, settings)
    flt = build_filter(db, viewer, group_id=group_id, tag=tag, kind=kind)
    vector = embed_query(db, active, text, engines, cache)
    hits = index.top_k(db, active.model_key, vector, flt, limit)
    return SemanticSearchResponse(
        query=text,
        model_key=active.model_key,
        languages=list(active.languages) if active.languages is not None else None,  # type: ignore[arg-type]
        multilingual=active.multilingual,
        assets=_hits_to_assets(db, hits),
    )


# -- 似た画像 -----------------------------------------------------------------------


def _missing_vector_error(db: Session, asset: Asset, model_key: str) -> EmbeddingsConflictError:
    if asset.kind == AssetKind.MASK:
        return EmbeddingsConflictError("embedding_not_supported", t("embeddings.maskNotSupported"))
    row = embeddings_domain.embedding_status(db, asset.id, model_key)
    if row is not None and row.status in (
        embeddings_domain.STATUS_QUEUED,
        embeddings_domain.STATUS_RUNNING,
    ):
        return EmbeddingsConflictError("embedding_pending", t("embeddings.vectorPending"))
    if row is not None and row.status == embeddings_domain.STATUS_FAILED:
        return EmbeddingsConflictError("embedding_failed", t("embeddings.vectorFailed"))
    return EmbeddingsConflictError("embedding_missing", t("embeddings.vectorMissing"))


def similar_assets(
    db: Session,
    *,
    settings: Settings,
    index: VectorIndex,
    viewer: CurrentUser,
    asset_id: uuid.UUID,
    limit: int = SEARCH_DEFAULT_LIMIT,
    group_id: uuid.UUID | None = None,
    tag: str | None = None,
    kind: str | None = None,
) -> SimilarAssetsResponse:
    """起点の画像が見えなければ EmbeddingsNotFoundError(他人の画像は「無い」と同じ)。"""
    asset = get_visible_asset(db, viewer, asset_id)
    if asset is None:
        raise EmbeddingsNotFoundError(t("assets.notFound"))
    active = require_active(db, settings)
    flt = build_filter(db, viewer, group_id=group_id, tag=tag, kind=kind)
    hits = neighbors(index, db, active.model_key, asset.id, flt, limit)
    if hits is None:
        raise _missing_vector_error(db, asset, active.model_key)
    return SimilarAssetsResponse(
        asset_id=asset.id, model_key=active.model_key, assets=_hits_to_assets(db, hits)
    )


# -- 重複の候補 ---------------------------------------------------------------------


def leader_groups(
    n: int, pairs: Sequence[tuple[int, int, float]]
) -> list[tuple[int, list[tuple[int, float]]]]:
    """組を代表との類似度でまとめる(ADR-0033 12章)。位置は新しい順(0 がいちばん新しい)。

    古い画像(位置の大きいもの)から順に、まだどのグループにも入っていない画像を代表にし、
    代表と組になっている、まだどのグループにも入っていない画像をそのグループに入れる。
    union-find と違い、A〜B、B〜C の組があっても A と C が組でなければ、A と C は同じグループに
    ならない(連鎖しない)。返すのは `(代表, [(メンバー, 代表との類似度), ...])` で、メンバーが
    1件以上あるものだけ。
    """
    neighbors: dict[int, dict[int, float]] = {}
    for i, j, score in pairs:
        neighbors.setdefault(i, {})[j] = score
        neighbors.setdefault(j, {})[i] = score
    assigned: set[int] = set()
    groups: list[tuple[int, list[tuple[int, float]]]] = []
    for leader in range(n - 1, -1, -1):
        if leader in assigned or leader not in neighbors:
            continue
        members = sorted((x, score) for x, score in neighbors[leader].items() if x not in assigned)
        if not members:
            continue
        assigned.add(leader)
        assigned.update(x for x, _ in members)
        groups.append((leader, members))
    return groups


def _load_hashes(
    db: Session, ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, perceptual_hash.PerceptualHash]:
    """今の版の知覚ハッシュ(無い、版が古いものは含めない)。"""
    found: dict[uuid.UUID, perceptual_hash.PerceptualHash] = {}
    ids = list(ids)
    for start in range(0, len(ids), 900):
        rows = db.execute(
            select(
                AssetPerceptualHash.asset_id,
                AssetPerceptualHash.dhash,
                AssetPerceptualHash.color,
                AssetPerceptualHash.version,
            ).where(AssetPerceptualHash.asset_id.in_(ids[start : start + 900]))
        ).all()
        for asset_id, dhash, color, version in rows:
            value = perceptual_hash.from_stored(dhash, color, version)
            if value is not None:
                found[asset_id] = value
    return found


def filter_by_hash(
    pairs: Sequence[tuple[int, int, float]],
    hashes: Sequence[perceptual_hash.PerceptualHash | None],
) -> list[tuple[int, int, float]]:
    """知覚ハッシュが近くない組を外す。どちらかにハッシュが無い組は、CLIP だけで判定する
    (残す)。`hashes` は位置ごとのハッシュ。"""
    if not pairs:
        return []
    table = perceptual_hash.decode_many(list(hashes))
    i = np.fromiter((p[0] for p in pairs), dtype=np.int64, count=len(pairs))
    j = np.fromiter((p[1] for p in pairs), dtype=np.int64, count=len(pairs))
    both = table.present[i] & table.present[j]
    keep = ~both
    if both.any():
        keep[both] = perceptual_hash.close_pairs(table, i[both], j[both])
    return [pair for pair, ok in zip(pairs, keep.tolist(), strict=True) if ok]


def find_duplicates(
    db: Session,
    *,
    settings: Settings,
    index: VectorIndex,
    viewer: CurrentUser,
    threshold: float | None = None,
    limit: int = DUPLICATES_DEFAULT_GROUPS,
    max_assets: int = DUPLICATES_MAX_ASSETS,
) -> DuplicatesResponse:
    active = require_active(db, settings)
    value = active.config.duplicate_threshold if threshold is None else float(threshold)
    ids = candidate_ids(db, active.model_key, AssetFilter(viewer=viewer), limit=max_assets + 1)
    truncated = len(ids) > max_assets
    ids = ids[:max_assets]
    ordered, matrix = index.subset(db, active.model_key, ids)
    pairs = pairs_at_least(matrix, value) if len(ordered) >= 2 else []

    involved = sorted({x for i, j, _ in pairs for x in (i, j)})
    hashes = _load_hashes(db, [ordered[x] for x in involved])
    by_position = [hashes.get(asset_id) for asset_id in ordered]
    pairs = filter_by_hash(pairs, by_position)

    raw_groups = leader_groups(len(ordered), pairs)
    # 大きい順。同じ大きさなら類似度の高い順(確かな重複から見せる)、さらに同じなら、
    # いちばん新しい画像を含むグループを先に(位置が小さい)。
    raw_groups.sort(
        key=lambda g: (
            -(len(g[1]) + 1),
            -max(score for _, score in g[1]),
            min(g[0], g[1][0][0]),
        )
    )
    groups_truncated = len(raw_groups) > limit
    raw_groups = raw_groups[:limit]

    member_ids = [ordered[x] for leader, members in raw_groups for x in (leader, *dict(members))]
    assets = (
        {a.id: a for a in db.execute(select(Asset).where(Asset.id.in_(member_ids))).scalars()}
        if member_ids
        else {}
    )
    titles = annotations_domain.bulk_titles(db, member_ids)
    groups: list[DuplicateGroup] = []
    for leader, members in raw_groups:
        # 代表は、メンバーとの類似度の最大値。メンバーは代表との類似度。
        scores = [(leader, max(score for _, score in members)), *members]
        items: list[DuplicateAsset] = []
        for x, score in scores:
            asset = assets.get(ordered[x])
            if asset is None:
                continue
            items.append(
                DuplicateAsset(
                    id=asset.id,
                    kind=asset.kind,
                    mime=asset.mime,
                    width=asset.width,
                    height=asset.height,
                    bytes=asset.bytes,
                    created_at=asset.created_at,
                    title=titles.get(asset.id),
                    max_score=round(score, 6),
                    hash_missing=by_position[x] is None,
                )
            )
        items.sort(key=lambda a: (a.created_at, str(a.id)))
        if len(items) >= 2:
            groups.append(
                DuplicateGroup(
                    assets=items,
                    max_score=max(a.max_score for a in items),
                    hash_missing=any(bool(a.hash_missing) for a in items),
                )
            )
    return DuplicatesResponse(
        model_key=active.model_key,
        threshold=value,
        groups=groups,
        scanned=len(ordered),
        truncated=truncated,
        groups_truncated=groups_truncated,
    )


# -- マップ --------------------------------------------------------------------------


def _lineage_edges(db: Session, ids: Sequence[uuid.UUID]) -> list[list[int]]:
    """主たる親の辺(ADR-0003。position 0 の image 入力と、スケッチの下地)のうち、両端が
    `ids` にあるもの。"""
    position = {asset_id: i for i, asset_id in enumerate(ids)}
    edges: set[tuple[int, int]] = set()
    id_list = list(ids)
    for start in range(0, len(id_list), 900):
        chunk = id_list[start : start + 900]
        rows: list[Any] = list(
            db.execute(
                select(RunInput.asset_id, Asset.id)
                .join(
                    RunInput,
                    and_(
                        RunInput.run_id == Asset.produced_by_run_id,
                        RunInput.role == RunInputRole.IMAGE,
                        RunInput.position == 0,
                    ),
                )
                .where(Asset.id.in_(chunk))
            ).all()
        )
        rows.extend(
            db.execute(
                select(Asset.source_asset_id, Asset.id).where(
                    Asset.id.in_(chunk), Asset.source_asset_id.is_not(None)
                )
            ).all()
        )
        for parent, child in rows:
            if parent in position and child in position and parent != child:
                edges.add((position[parent], position[child]))
    return [list(edge) for edge in sorted(edges)]


def build_graph(
    db: Session,
    *,
    settings: Settings,
    index: VectorIndex,
    viewer: CurrentUser,
    k: int = GRAPH_DEFAULT_K,
    limit: int = GRAPH_DEFAULT_LIMIT,
    group_id: uuid.UUID | None = None,
    tag: str | None = None,
    include_lineage: bool = False,
) -> EmbeddingGraphResponse:
    active = require_active(db, settings)
    flt = build_filter(db, viewer, group_id=group_id, tag=tag)
    all_ids = candidate_ids(db, active.model_key, flt)
    total = len(all_ids)
    ordered, matrix = index.subset(db, active.model_key, all_ids[:limit])
    if ordered:
        indices, sims = knn_matrix(matrix, k)
    else:
        indices = np.zeros((0, 1), dtype=np.int64)
        sims = np.zeros((0, 1), dtype=np.float32)
    assets = (
        {a.id: a for a in db.execute(select(Asset).where(Asset.id.in_(ordered))).scalars()}
        if ordered
        else {}
    )
    titles = annotations_domain.bulk_titles(db, ordered)
    nodes = [
        EmbeddingGraphNode(
            id=asset_id,
            kind=assets[asset_id].kind,  # type: ignore[arg-type]
            width=assets[asset_id].width,
            height=assets[asset_id].height,
            title=titles.get(asset_id),
        )
        for asset_id in ordered
    ]
    return EmbeddingGraphResponse(
        model_key=active.model_key,
        k=k,
        nodes=nodes,
        neighbor_indices=indices.tolist(),
        neighbor_similarities=[[round(float(v), 4) for v in row] for row in sims],
        lineage_edges=_lineage_edges(db, ordered) if include_lineage else None,
        total=total,
        truncated=total > len(ordered),
    )
