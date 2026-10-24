"""グループの提案(ADR-0056)。画像の埋め込みで集まりを見つけ、グループにする案を出す。

- 自動では何も保存しない。提案は表示のたびに計算し、人が受け入れたものだけをグループにする(1章)。
- 集め方: 使うモデルのベクトルを L2 正規化し、HDBSCAN(コサイン)で集める。3000 件を超えたら
  PCA 50 次元のユークリッドにする。外れ値は外れ値として残す。「細かく分ける」なら、対象の 20% を
  超える集まりを球面 k-means で割る(2章)。
- 集める計算は対象の全件で行い、範囲(グループ外だけ / すべて)と既存のグループとの重なりは
  後から当てはめる(3章・4章)。集めた結果はプロセス内の LRU にキャッシュする(8章)。
- 特徴的なタグ(informative Dirichlet prior 付きの log-odds)と作品を、カードの手がかりに出す(5章)。
- 名前は付けない(`name` は常に null)。外部の AI を呼ばないので `ai.before_send` は通さない。
  名前の候補(LLM)の入力は `naming_input` が返し、呼び出しは `app/domain/group_naming.py` と
  `app/api/group_naming.py` が行う。
"""

from __future__ import annotations

import hashlib
import math
import re
import threading
import uuid
from collections import Counter, OrderedDict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Literal

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.identity import CurrentUser
from app.config import Settings
from app.domain import (
    content_rating,
    focal_points,
    imported_assets,
)
from app.domain import tag_dictionaries as tag_dictionaries_domain
from app.domain.asset_groups import AssetGroupAssetsMissingError, add_members, create_group
from app.domain.content_withholding import summary_fields
from app.domain.models import (
    Asset,
    AssetGroup,
    AssetGroupMember,
    AssetTag,
    Tag,
    TagDictionaryEntry,
)
from app.domain.schemas import (
    AssetGroupRow,
    AssetSummary,
    GroupSuggestion,
    GroupSuggestionFranchise,
    GroupSuggestionsResponse,
    GroupSuggestionTag,
    GroupSuggestionTarget,
)
from app.domain.semantic_search import require_active
from app.domain.vector_index import AssetFilter, VectorIndex, candidate_ids
from app.domain.visibility import get_visible_group, group_visible

Scope = Literal["ungrouped", "all"]
Granularity = Literal["normal", "fine"]

# -- 集め方の設定(ADR-0056 2章) -----------------------------------------------------

MIN_CLUSTER_SIZE = 5
MIN_SAMPLES = 2
# これを超えたら PCA で次元を落としてユークリッドで集める(コサインの全組の距離を作らない)。
PCA_THRESHOLD = 3000
PCA_DIM = 50
# 「細かく分ける」で割る集まりの大きさ(対象全体に対する割合)と、選ぶ k の範囲。
FINE_SPLIT_RATIO = 0.2
FINE_K_MIN = 2
FINE_K_MAX = 6
# シルエット係数を計算するときの標本の上限(全組の距離を作らない)。
SILHOUETTE_SAMPLE = 2000
KMEANS_MAX_ITER = 30

# 一度に見る画像の数(新しい順)。
MAX_ASSETS = 10000
# 表紙の枚数。
COVER_COUNT = 12

# -- 既存のグループとの重なり(4章) ---------------------------------------------------

TARGET_OVERLAP = 0.5

# -- 特徴的なタグと作品(5章) ---------------------------------------------------------

TAG_MIN_Z = 1.5
TAG_MIN_RATE = 0.25
TAG_MAX_GLOBAL_RATE = 0.5
TAG_MAX_COUNT = 8
FRANCHISE_MIN_RATE = 0.25
FRANCHISE_MIN_COUNT = 2
# danbooru の体系のカテゴリー(ADR-0041)。
_CATEGORY_COPYRIGHT = 3
_CATEGORY_CHARACTER = 4
_PARENTHETICAL = re.compile(r"^(.+?)\s*\(([^()]+)\)$")
# 括弧書きのうち作品でないもの。`cosplay` はキャラクターのタグにも付く。辞書に無いタグを括弧書き
# だけで数えるときは、一般のタグの括弧書き(`star (sky)`、`bow (weapon)` など)を作品と取り違え
# ないよう、よく出るものを除く(辞書のカテゴリーが分かるタグには使わない)。
_NOT_FRANCHISE = frozenset({"cosplay"})
_GENERAL_QUALIFIERS = frozenset(
    {
        "animal",
        "artwork",
        "emblem",
        "flower",
        "food",
        "instrument",
        "medium",
        "meme",
        "object",
        "phrase",
        "plant",
        "shape",
        "sky",
        "style",
        "symbol",
        "vehicle",
        "weapon",
    }
)

_IN_CHUNK = 900


class GroupSuggestionTargetNotFoundError(Exception):
    """受け入れの追加先のグループが無い・見えない・削除済み(404)。"""


# -- 集める ------------------------------------------------------------------------


def _normalize(matrix: np.ndarray) -> np.ndarray:
    matrix = np.asarray(matrix, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (matrix / norms).astype(np.float32, copy=False)


def _hdbscan_labels(vectors: np.ndarray) -> np.ndarray:
    """HDBSCAN のラベル(-1 は外れ値)。`vectors` は L2 正規化済み。"""
    from sklearn.cluster import HDBSCAN

    n = len(vectors)
    if n < MIN_CLUSTER_SIZE:
        return np.full(n, -1, dtype=np.int64)
    if n > PCA_THRESHOLD:
        from sklearn.decomposition import PCA

        dim = min(PCA_DIM, n, vectors.shape[1])
        reduced = PCA(n_components=dim, random_state=0).fit_transform(vectors)
        model = HDBSCAN(min_cluster_size=MIN_CLUSTER_SIZE, min_samples=MIN_SAMPLES, copy=True)
        return np.asarray(model.fit_predict(reduced), dtype=np.int64)
    model = HDBSCAN(
        metric="cosine", min_cluster_size=MIN_CLUSTER_SIZE, min_samples=MIN_SAMPLES, copy=True
    )
    return np.asarray(model.fit_predict(vectors.astype(np.float64)), dtype=np.int64)


def spherical_kmeans(vectors: np.ndarray, k: int, *, seed: int = 0) -> np.ndarray:
    """球面 k-means(コサイン)のラベル。初期値は k-means++、乱数の種は固定。"""
    n = len(vectors)
    rng = np.random.default_rng(seed)
    first = int(rng.integers(n))
    centers = [first]
    best = vectors @ vectors[first]
    for _ in range(1, k):
        dist = np.clip(1.0 - best, 0.0, None) ** 2
        total = float(dist.sum())
        if total <= 0:
            break
        nxt = int(rng.choice(n, p=dist / total))
        centers.append(nxt)
        best = np.maximum(best, vectors @ vectors[nxt])
    center_matrix = vectors[centers].copy()
    labels = np.full(n, -1, dtype=np.int64)
    for _ in range(KMEANS_MAX_ITER):
        new_labels = np.argmax(vectors @ center_matrix.T, axis=1)
        if np.array_equal(new_labels, labels):
            break
        labels = new_labels
        for c in range(len(center_matrix)):
            members = vectors[labels == c]
            if len(members):
                center_matrix[c] = members.mean(axis=0)
        center_matrix = _normalize(center_matrix)
    return labels


def _split_large(vectors: np.ndarray) -> list[np.ndarray] | None:
    """大きな集まりを球面 k-means で割る。k は 2〜6 からシルエット係数(コサイン)で選ぶ。
    各片が `MIN_CLUSTER_SIZE` に満たない k は選ばない。どれも満たさなければ None(割らない)。"""
    from sklearn.metrics import silhouette_score

    n = len(vectors)
    best: tuple[float, np.ndarray] | None = None
    for k in range(FINE_K_MIN, FINE_K_MAX + 1):
        if n < k * MIN_CLUSTER_SIZE:
            break
        labels = spherical_kmeans(vectors, k)
        counts = np.bincount(labels, minlength=k)
        if len(np.unique(labels)) < k or counts.min() < MIN_CLUSTER_SIZE:
            continue
        score = float(
            silhouette_score(
                vectors,
                labels,
                metric="cosine",
                sample_size=min(n, SILHOUETTE_SAMPLE),
                random_state=0,
            )
        )
        if best is None or score > best[0]:
            best = (score, labels)
    if best is None:
        return None
    labels = best[1]
    return [np.flatnonzero(labels == c) for c in range(int(labels.max()) + 1)]


def _by_centroid(vectors: np.ndarray, members: np.ndarray) -> list[int]:
    """集まりの画像を、重心(正規化した平均)に近い順に。同じ近さなら元の順(新しい順)。"""
    sub = vectors[members]
    centroid = sub.mean(axis=0)
    norm = float(np.linalg.norm(centroid))
    if norm > 0:
        centroid = centroid / norm
    scores = sub @ centroid
    order = sorted(range(len(members)), key=lambda i: (-float(scores[i]), int(members[i])))
    return [int(members[i]) for i in order]


@dataclass(frozen=True)
class Clustering:
    """集めた結果。添字は入力の行(新しい順)。集まりは大きい順、中は重心に近い順。"""

    clusters: tuple[tuple[int, ...], ...]
    outliers: tuple[int, ...]


def cluster_vectors(matrix: np.ndarray, *, granularity: Granularity = "normal") -> Clustering:
    """ベクトルの行列を集める(ADR-0056 2章)。同じ入力なら同じ結果。"""
    n = len(matrix)
    if n == 0:
        return Clustering(clusters=(), outliers=())
    vectors = _normalize(matrix)
    labels = _hdbscan_labels(vectors)
    groups = [np.flatnonzero(labels == c) for c in sorted(set(labels.tolist()) - {-1})]
    if granularity == "fine":
        refined: list[np.ndarray] = []
        for members in groups:
            if len(members) > FINE_SPLIT_RATIO * n:
                pieces = _split_large(vectors[members])
                if pieces is not None:
                    refined.extend(members[p] for p in pieces)
                    continue
            refined.append(members)
        groups = refined
    clusters = [tuple(_by_centroid(vectors, members)) for members in groups if len(members)]
    clusters.sort(key=lambda c: (-len(c), min(c)))
    outliers = tuple(int(i) for i in np.flatnonzero(labels == -1))
    return Clustering(clusters=tuple(clusters), outliers=outliers)


# -- キャッシュ(8章) ----------------------------------------------------------------

CacheKey = tuple[str, int, str, str]


class GroupSuggestionCache:
    """集めた結果(Asset の id の並び)をプロセスの中に持つ LRU。永続化しない。"""

    def __init__(self, max_entries: int = 8) -> None:
        self._entries: OrderedDict[CacheKey, _CachedClustering] = OrderedDict()
        self._max = max_entries
        self._lock = threading.Lock()
        self.builds = 0

    def get_or_build(
        self, key: CacheKey, build: Callable[[], _CachedClustering]
    ) -> _CachedClustering:
        with self._lock:
            cached = self._entries.get(key)
            if cached is not None:
                self._entries.move_to_end(key)
                return cached
        # 計算はロックの外で行う(同じ鍵が同時に来たら2回計算するが、結果は同じ)。
        value = build()
        with self._lock:
            self.builds += 1
            self._entries[key] = value
            self._entries.move_to_end(key)
            while len(self._entries) > self._max:
                self._entries.popitem(last=False)
        return value


@dataclass(frozen=True)
class _CachedClustering:
    clusters: tuple[tuple[uuid.UUID, ...], ...]
    outliers: tuple[uuid.UUID, ...]


def _ids_digest(ids: Sequence[uuid.UUID]) -> str:
    digest = hashlib.sha256()
    for asset_id in ids:
        digest.update(asset_id.bytes)
    return digest.hexdigest()


def _params_key(granularity: Granularity) -> str:
    return (
        f"{granularity}:mcs={MIN_CLUSTER_SIZE}:ms={MIN_SAMPLES}:pca={PCA_THRESHOLD}/{PCA_DIM}"
        f":fine={FINE_SPLIT_RATIO}/{FINE_K_MIN}-{FINE_K_MAX}"
    )


# -- 特徴的なタグ(5章) --------------------------------------------------------------


@dataclass(frozen=True)
class TagScore:
    tag: str
    z: float
    rate: float
    global_rate: float


def distinctive_tags(
    cluster_counts: dict[str, int],
    cluster_size: int,
    global_counts: dict[str, int],
    total: int,
    *,
    limit: int = TAG_MAX_COUNT,
) -> list[TagScore]:
    """集まりに偏るタグ。informative Dirichlet prior 付きの log-odds(Monroe ら)で、集まりと
    それ以外を比べる。事前分布は対象全体での出現数。数えるのは画像の枚数(1枚に1回)。

    条件は z > 1.5、集まりでの出現率 25% 以上、全体での出現率 50% 以下。z の大きい順。
    """
    if cluster_size <= 0 or total <= 0:
        return []
    n_i = sum(cluster_counts.values())
    n_all = sum(global_counts.values())
    n_j = n_all - n_i
    # 事前分布の総和に1を足す(タグが1種類しか無いときも分母が 0 にならないように)。
    alpha0 = float(n_all) + 1.0
    scores: list[TagScore] = []
    for tag, y_i in cluster_counts.items():
        y_all = global_counts.get(tag, y_i)
        rate = y_i / cluster_size
        global_rate = y_all / total
        if rate < TAG_MIN_RATE or global_rate > TAG_MAX_GLOBAL_RATE:
            continue
        alpha = float(y_all)
        y_j = max(0, y_all - y_i)
        delta = math.log((y_i + alpha) / (n_i + alpha0 - y_i - alpha)) - math.log(
            (y_j + alpha) / (n_j + alpha0 - y_j - alpha)
        )
        variance = 1.0 / (y_i + alpha) + 1.0 / (y_j + alpha)
        z = delta / math.sqrt(variance)
        if z > TAG_MIN_Z:
            scores.append(TagScore(tag=tag, z=z, rate=rate, global_rate=global_rate))
    scores.sort(key=lambda s: (-s.z, -s.rate, s.tag))
    return scores[:limit]


def franchise_of_tag(tag: str, category: int | None, in_dictionary: bool) -> str | None:
    """タグが示す作品の名前(5章)。作品のタグはその名前、キャラクターのタグ(または辞書に無い
    タグ)は末尾の括弧書きの中。辞書に載っていて作品・キャラクター以外のタグは None。"""
    if in_dictionary and category == _CATEGORY_COPYRIGHT:
        return tag
    if in_dictionary and category != _CATEGORY_CHARACTER:
        return None
    match = _PARENTHETICAL.match(tag)
    if match is None:
        return None
    name = match.group(2).strip()
    if not name or name in _NOT_FRANCHISE:
        return None
    if not in_dictionary and name in _GENERAL_QUALIFIERS:
        return None
    return name


# -- DB からの読み込み ------------------------------------------------------------------


def _chunks(ids: Sequence[uuid.UUID]) -> Iterable[list[uuid.UUID]]:
    ids = list(ids)
    for start in range(0, len(ids), _IN_CHUNK):
        yield ids[start : start + _IN_CHUNK]


def _memberships(db: Session, ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, uuid.UUID]:
    """削除されていないグループへの所属(asset_id → group_id)。所属は1つ(ADR-0022)。"""
    found: dict[uuid.UUID, uuid.UUID] = {}
    for chunk in _chunks(ids):
        rows = db.execute(
            select(AssetGroupMember.asset_id, AssetGroupMember.asset_group_id)
            .join(AssetGroup, AssetGroup.id == AssetGroupMember.asset_group_id)
            .where(AssetGroupMember.asset_id.in_(chunk), AssetGroup.deleted_at.is_(None))
        ).all()
        for asset_id, group_id in rows:
            found[asset_id] = group_id
    return found


def _visible_groups(db: Session, viewer: CurrentUser) -> dict[uuid.UUID, str]:
    rows = db.execute(
        select(AssetGroup.id, AssetGroup.name).where(
            AssetGroup.deleted_at.is_(None), group_visible(viewer)
        )
    ).all()
    return {group_id: name for group_id, name in rows}


def _english_tags(db: Session, ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, set[str]]:
    """画像ごとの英語のタグ(ASCII の名前。人が消したものは除く)。"""
    found: dict[uuid.UUID, set[str]] = {}
    for chunk in _chunks(ids):
        rows = db.execute(
            select(AssetTag.asset_id, Tag.name)
            .join(Tag, Tag.id == AssetTag.tag_id)
            .where(AssetTag.asset_id.in_(chunk), AssetTag.removed.is_(False))
        ).all()
        for asset_id, name in rows:
            if name.isascii():
                found.setdefault(asset_id, set()).add(name)
    return found


def _tag_categories(db: Session, names: Iterable[str]) -> dict[str, int | None]:
    """有効なタグ辞書(体系が danbooru)に載っているタグのカテゴリー。先に登録した辞書を優先。
    載っていないタグは含めない。"""
    active = tag_dictionaries_domain.active_dictionaries(db)
    dictionary_ids = [
        i
        for i, scheme in active.tags.items()
        if (scheme or tag_dictionaries_domain.DEFAULT_CATEGORY_SCHEME) == "danbooru"
    ]
    if not dictionary_ids:
        return {}
    rank = {d: i for i, d in enumerate(dictionary_ids)}
    keys = {name: tag_dictionaries_domain.make_key(name) for name in set(names)}
    by_key: dict[str, tuple[int, int | None]] = {}
    ordered = sorted({k for k in keys.values() if k})
    for start in range(0, len(ordered), _IN_CHUNK):
        rows = db.execute(
            select(
                TagDictionaryEntry.dictionary_id,
                TagDictionaryEntry.name_key,
                TagDictionaryEntry.category,
            ).where(
                TagDictionaryEntry.dictionary_id.in_(dictionary_ids),
                TagDictionaryEntry.name_key.in_(ordered[start : start + _IN_CHUNK]),
            )
        ).all()
        for dictionary_id, key, category in rows:
            r = rank[dictionary_id]
            current = by_key.get(key)
            if current is None or r < current[0]:
                by_key[key] = (r, category)
    return {name: by_key[key][1] for name, key in keys.items() if key in by_key}


def _asset_summaries(db: Session, ids: Sequence[uuid.UUID]) -> list[AssetSummary]:
    assets: dict[uuid.UUID, Asset] = {}
    for chunk in _chunks(ids):
        for asset in db.execute(select(Asset).where(Asset.id.in_(chunk))).scalars():
            assets[asset.id] = asset
    focals = focal_points.bulk_get(db, ids)
    ratings = content_rating.bulk_levels(db, ids)
    imported_ids = imported_assets.bulk_imported_ids(db, assets.values())
    result: list[AssetSummary] = []
    for asset_id in ids:
        asset = assets.get(asset_id)
        if asset is None:
            continue
        result.append(
            AssetSummary(
                id=asset.id,
                kind=asset.kind,  # type: ignore[arg-type]
                mime=asset.mime,
                width=asset.width,
                height=asset.height,
                bytes=asset.bytes,
                created_at=asset.created_at,
                focal_point=focals.get(asset.id),
                content_rating=ratings.get(asset.id),  # type: ignore[arg-type]
                imported=asset.id in imported_ids,
                **summary_fields(asset),
            )
        )
    return result


# -- 提案 --------------------------------------------------------------------------


def _suggestion_id(target: uuid.UUID | None, member_ids: Iterable[uuid.UUID]) -> str:
    digest = hashlib.sha256()
    digest.update((str(target) if target else "new").encode("ascii"))
    for asset_id in sorted(member_ids, key=lambda i: i.bytes):
        digest.update(asset_id.bytes)
    return digest.hexdigest()[:16]


@dataclass(frozen=True)
class NamingCluster:
    """名前の候補(ADR-0056「名前の候補」)に使う、新しいグループの提案1つのタグの集計。
    数えるのは集まりの全員(提案から落ちた画像も含む。カードの特徴的なタグと同じ)。"""

    suggestion_id: str
    # 集まりの枚数。
    size: int
    # タグ → 集まりの中で付いている枚数。
    tag_counts: dict[str, int]
    # 集まりの画像ごとのタグ(作品の集計に使う)。
    member_tags: tuple[frozenset[str], ...]


@dataclass(frozen=True)
class NamingInput:
    """名前の候補の入力。`clusters` は提案の順(既存のグループへの追加の提案は含まない)。"""

    clusters: tuple[NamingCluster, ...]
    # 対象全体でのタグ → 枚数と、対象の枚数。
    global_counts: dict[str, int]
    total: int
    # タグ辞書(danbooru の体系)のカテゴリー。辞書に無いタグは含まない。
    categories: dict[str, int | None]


def suggest_groups(
    db: Session,
    *,
    settings: Settings,
    index: VectorIndex,
    version_of: Callable[[str], int],
    cache: GroupSuggestionCache,
    viewer: CurrentUser,
    scope: Scope = "ungrouped",
    granularity: Granularity = "normal",
    max_assets: int = MAX_ASSETS,
) -> GroupSuggestionsResponse:
    """グループの提案。埋め込みが無効・使えなければ `EmbeddingsConflictError`(409)。"""
    response, _naming = _suggest(
        db,
        settings=settings,
        index=index,
        version_of=version_of,
        cache=cache,
        viewer=viewer,
        scope=scope,
        granularity=granularity,
        max_assets=max_assets,
        with_assets=True,
    )
    return response


def naming_input(
    db: Session,
    *,
    settings: Settings,
    index: VectorIndex,
    version_of: Callable[[str], int],
    cache: GroupSuggestionCache,
    viewer: CurrentUser,
    scope: Scope = "ungrouped",
    granularity: Granularity = "normal",
    suggestion_ids: Iterable[str] | None = None,
    max_assets: int = MAX_ASSETS,
) -> NamingInput:
    """名前の候補の入力。提案を同じ条件で計算し直し(集めた結果はキャッシュが効く)、新しい
    グループの提案(`target_group` の無いもの)のうち `suggestion_ids` に入るもの(None なら全部)の
    タグの集計を返す。埋め込みが無効・使えなければ `EmbeddingsConflictError`(409)。"""
    _response, naming = _suggest(
        db,
        settings=settings,
        index=index,
        version_of=version_of,
        cache=cache,
        viewer=viewer,
        scope=scope,
        granularity=granularity,
        max_assets=max_assets,
        with_assets=False,
    )
    if suggestion_ids is None:
        return naming
    wanted = set(suggestion_ids)
    return replace(naming, clusters=tuple(c for c in naming.clusters if c.suggestion_id in wanted))


def _suggest(
    db: Session,
    *,
    settings: Settings,
    index: VectorIndex,
    version_of: Callable[[str], int],
    cache: GroupSuggestionCache,
    viewer: CurrentUser,
    scope: Scope,
    granularity: Granularity,
    max_assets: int,
    with_assets: bool,
) -> tuple[GroupSuggestionsResponse, NamingInput]:
    active = require_active(db, settings)
    ids = candidate_ids(db, active.model_key, AssetFilter(viewer=viewer), limit=max_assets + 1)
    truncated = len(ids) > max_assets
    ids = ids[:max_assets]
    ordered, matrix = index.subset(db, active.model_key, ids)

    def build() -> _CachedClustering:
        result = cluster_vectors(matrix, granularity=granularity)
        return _CachedClustering(
            clusters=tuple(tuple(ordered[i] for i in c) for c in result.clusters),
            outliers=tuple(ordered[i] for i in result.outliers),
        )

    key: CacheKey = (
        active.model_key,
        version_of(active.model_key),
        _ids_digest(ordered),
        _params_key(granularity),
    )
    clustering = cache.get_or_build(key, build) if ordered else _CachedClustering((), ())

    membership = _memberships(db, ordered)
    visible_groups = _visible_groups(db, viewer)
    tags_by_asset = _english_tags(db, ordered)
    total = len(ordered)
    global_counts: Counter[str] = Counter()
    for names in tags_by_asset.values():
        global_counts.update(names)

    def eligible(asset_id: uuid.UUID) -> bool:
        group_id = membership.get(asset_id)
        if group_id is None:
            return True
        # 見えないグループに入っている画像は、どちらの範囲でも提案に入れない(3章)。
        return scope == "all" and group_id in visible_groups

    drafts: list[tuple[tuple[uuid.UUID, ...], list[uuid.UUID], uuid.UUID | None, float]] = []
    suggested: set[uuid.UUID] = set()
    for members in clustering.clusters:
        in_groups = Counter(membership[a] for a in members if membership.get(a) in visible_groups)
        target: uuid.UUID | None = None
        overlap = 0.0
        if in_groups:
            group_id, count = max(in_groups.items(), key=lambda kv: (kv[1], str(kv[0])))
            if count / len(members) >= TARGET_OVERLAP:
                target, overlap = group_id, count / len(members)
        proposal = [
            a for a in members if eligible(a) and (target is None or membership.get(a) != target)
        ]
        if target is None and len(proposal) < MIN_CLUSTER_SIZE:
            continue
        if not proposal:
            continue
        drafts.append((members, proposal, target, overlap))
        suggested.update(proposal)

    # 作品とタグの訳に使う辞書の引き当ては、提案に出るタグの分だけ。
    cluster_tag_counts: list[Counter[str]] = []
    for members, _proposal, _target, _overlap in drafts:
        counts: Counter[str] = Counter()
        for a in members:
            counts.update(tags_by_asset.get(a, ()))
        cluster_tag_counts.append(counts)
    all_names = {name for counts in cluster_tag_counts for name in counts}
    categories = _tag_categories(db, all_names)

    tag_scores = [
        distinctive_tags(dict(counts), len(members), dict(global_counts), total)
        for counts, (members, *_rest) in zip(cluster_tag_counts, drafts, strict=True)
    ]
    franchises: list[tuple[str, float] | None] = []
    for members, *_rest in drafts:
        per_asset: Counter[str] = Counter()
        for a in members:
            names = {
                f
                for tag in tags_by_asset.get(a, ())
                if (f := franchise_of_tag(tag, categories.get(tag), tag in categories))
            }
            per_asset.update(names)
        if not per_asset:
            franchises.append(None)
            continue
        name, count = min(per_asset.items(), key=lambda kv: (-kv[1], kv[0]))
        rate = count / len(members)
        ok = count >= FRANCHISE_MIN_COUNT and rate >= FRANCHISE_MIN_RATE
        franchises.append((name, rate) if ok else None)

    label_names = {s.tag for scores in tag_scores for s in scores} | {
        f[0] for f in franchises if f is not None
    }
    translations = tag_dictionaries_domain.lookup_translations(db, label_names)

    suggestions: list[GroupSuggestion] = []
    naming_by_id: dict[str, NamingCluster] = {}
    for (members, proposal, target, overlap), scores, franchise, counts in zip(
        drafts, tag_scores, franchises, cluster_tag_counts, strict=True
    ):
        # 範囲が「すべて」のとき、別のグループから移る画像(3章)。
        moving = [a for a in proposal if membership.get(a) is not None]
        suggestion_id = _suggestion_id(target, proposal)
        if target is None:
            naming_by_id[suggestion_id] = NamingCluster(
                suggestion_id=suggestion_id,
                size=len(members),
                tag_counts=dict(counts),
                member_tags=tuple(frozenset(tags_by_asset.get(a, ())) for a in members),
            )
        suggestions.append(
            GroupSuggestion(
                id=suggestion_id,
                size=len(proposal),
                member_ids=proposal,
                cover_ids=proposal[:COVER_COUNT],
                top_tags=[
                    GroupSuggestionTag(
                        tag=s.tag,
                        label=translations.get(s.tag) or s.tag,
                        rate=round(s.rate, 4),
                        global_rate=round(s.global_rate, 4),
                    )
                    for s in scores
                ],
                franchise=(
                    GroupSuggestionFranchise(
                        name=franchise[0],
                        label=translations.get(franchise[0]),
                        rate=round(franchise[1], 4),
                    )
                    if franchise is not None
                    else None
                ),
                target_group=(
                    GroupSuggestionTarget(
                        id=target, name=visible_groups[target], overlap=round(overlap, 4)
                    )
                    if target is not None
                    else None
                ),
                moving_count=len(moving),
                moving_ids=moving,
                name=None,
            )
        )
    suggestions.sort(key=lambda s: (-s.size, s.id))

    # 未分類: 提案に入らなかった、どのグループにも入っていない画像(新しい順。4章)。
    outlier_ids = [a for a in ordered if a not in suggested and membership.get(a) is None]

    shown = [a for s in suggestions for a in s.member_ids] + outlier_ids
    response = GroupSuggestionsResponse(
        model_key=active.model_key,
        scope=scope,
        granularity=granularity,
        scanned=total,
        truncated=truncated,
        suggestions=suggestions,
        outlier_ids=outlier_ids,
        assets=_asset_summaries(db, list(dict.fromkeys(shown))) if with_assets else [],
    )
    naming = NamingInput(
        clusters=tuple(naming_by_id[s.id] for s in suggestions if s.id in naming_by_id),
        global_counts=dict(global_counts),
        total=total,
        categories=categories,
    )
    return response, naming


# -- 受け入れ ----------------------------------------------------------------------


def accept_suggestion(
    db: Session,
    viewer: CurrentUser,
    *,
    asset_ids: list[uuid.UUID],
    name: str | None = None,
    target_group_id: uuid.UUID | None = None,
) -> AssetGroupRow:
    """提案を受け入れる(7章)。`target_group_id` ならそのグループへ移し、無ければ `name` で
    新しいグループを作って入れる。1回のトランザクションの中で行う(呼び出し側が commit する)。

    追加先が無ければ `GroupSuggestionTargetNotFoundError`、見えない・削除済みの画像を含めば
    `AssetGroupAssetsMissingError`(どちらも何も変えない。呼び出し側が rollback する)。
    """
    if target_group_id is not None:
        group = get_visible_group(db, viewer, target_group_id)
        if group is None:
            raise GroupSuggestionTargetNotFoundError(str(target_group_id))
    else:
        if name is None:
            raise ValueError("name or target_group_id is required")
        row = create_group(db, name, viewer)
        group = db.get(AssetGroup, row.id)
        assert group is not None
    return add_members(db, group, asset_ids, viewer=viewer)


__all__ = [
    "AssetGroupAssetsMissingError",
    "Clustering",
    "GroupSuggestionCache",
    "GroupSuggestionTargetNotFoundError",
    "NamingCluster",
    "NamingInput",
    "accept_suggestion",
    "cluster_vectors",
    "distinctive_tags",
    "franchise_of_tag",
    "naming_input",
    "spherical_kmeans",
    "suggest_groups",
]
