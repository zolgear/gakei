"""埋め込みベクトルの近傍検索(ADR-0033 6章)。

方式は起動時に決める(`embedding_index.prepare`)。どちらも同じ口(`VectorIndex`)を持ち、
同じテストを通す(ADR-0027 の「両方で同じ動作」)。

- **`NumpyIndex`**(SQLite と、pgvector の無い PostgreSQL): モデルごとに、ベクトルのある行を
  `(asset_id の一覧, float32 の行列)` としてメモリに持ち、numpy で内積を取る。worker が
  ベクトルを書く・消すたびに上げる版(`Embedder.version(model_key)`)が変わったら読み直す。
- **`PgvectorIndex`**: `ORDER BY embedding::vector(<次元>) <=> :q LIMIT k`。部分 HNSW 索引
  (`embedding_index.ensure_hnsw_index`)を使えるよう、式(`embedding::vector(<次元>)`)と
  条件(`model_key = '<リテラル>'`)を索引と同じ形で書く(バインド変数の `model_key` では
  部分索引の条件を満たすと証明できないことがある)。絞り込みで件数が欠けないよう
  `hnsw.iterative_scan = relaxed_order`(pgvector 0.8 以降)を使い、順番は Python で並べ直す。

見える範囲(ADR-0025)、削除済みとマスクの除外、グループ・タグ・種類の絞り込みは、上位 k 件を
取る前に掛ける(`AssetFilter`)。他人の画像は、似た画像にも重複にもマップにも出さない。

全組の近傍(マップ、重複の候補)は、どちらの方式でも、選んだ部分集合(件数に上限がある)の
ベクトルを行列にして numpy で計算する(`knn_matrix`、`pairs_at_least`)。pgvector で1件ずつ
HNSW を引くと、5000 件なら 5000 回の往復になり、しかも近似になる。部分集合の行列は高々
5000×512 の float32(約 10MB)で、全組の内積を区切って計算しても十分に速い。
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np
from sqlalchemy import Float, and_, bindparam, cast, exists, literal_column, select, text
from sqlalchemy.orm import Session
from sqlalchemy.types import UserDefinedType

from app.auth.identity import CurrentUser
from app.domain import annotations as annotations_domain
from app.domain import embedding_index
from app.domain.models import Asset, AssetEmbedding, AssetGroupMember, AssetKind
from app.domain.visibility import asset_visible
from app.embedding.base import blob_to_vector, l2_normalize

logger = logging.getLogger(__name__)

# 全組の内積を区切って計算するときの、1回の行数。
_CHUNK_ROWS = 512


@dataclass(frozen=True)
class AssetFilter:
    """近傍を探す対象の絞り込み。見える範囲・削除済み・マスクの除外は常に掛ける。"""

    viewer: CurrentUser
    group_id: uuid.UUID | None = None
    # 正規化前のタグ名(`annotations.tag_filter` が正規化する。不正なら TagNameError)。
    tag: str | None = None
    kind: str | None = None

    def conditions(self) -> list[Any]:
        clauses: list[Any] = [
            Asset.deleted_at.is_(None),
            Asset.kind != AssetKind.MASK,
            asset_visible(self.viewer),
        ]
        if self.kind is not None:
            clauses.append(Asset.kind == self.kind)
        if self.tag is not None and self.tag.strip():
            clauses.append(annotations_domain.tag_filter(self.tag))
        if self.group_id is not None:
            clauses.append(
                exists().where(
                    AssetGroupMember.asset_id == Asset.id,
                    AssetGroupMember.asset_group_id == self.group_id,
                )
            )
        return clauses


@dataclass(frozen=True)
class Hit:
    asset_id: uuid.UUID
    # コサイン類似度(ベクトルは L2 正規化済みなので内積)。
    score: float


def _has_vector() -> Any:
    return AssetEmbedding.vector.is_not(None)


def candidate_ids(
    db: Session,
    model_key: str,
    flt: AssetFilter,
    *,
    limit: int | None = None,
) -> list[uuid.UUID]:
    """絞り込みに合い、そのモデルのベクトルがある Asset の id(新しい順)。"""
    query = (
        select(Asset.id)
        .join(
            AssetEmbedding,
            and_(AssetEmbedding.asset_id == Asset.id, AssetEmbedding.model_key == model_key),
        )
        .where(_has_vector(), *flt.conditions())
        .order_by(Asset.created_at.desc(), Asset.id.desc())
    )
    if limit is not None:
        query = query.limit(limit)
    return list(db.execute(query).scalars().all())


def read_vectors(
    db: Session, model_key: str, asset_ids: Sequence[uuid.UUID]
) -> tuple[list[uuid.UUID], np.ndarray]:
    """BLOB(正本)からベクトルを読む。`asset_ids` の順で、ベクトルがあるものだけを返す。"""
    found: dict[uuid.UUID, np.ndarray] = {}
    ids = list(asset_ids)
    for start in range(0, len(ids), 900):
        rows = db.execute(
            select(AssetEmbedding.asset_id, AssetEmbedding.vector).where(
                AssetEmbedding.model_key == model_key,
                AssetEmbedding.asset_id.in_(ids[start : start + 900]),
                _has_vector(),
            )
        ).all()
        for asset_id, blob in rows:
            found[asset_id] = blob_to_vector(bytes(blob))
    ordered = [i for i in ids if i in found]
    return ordered, _stack([found[i] for i in ordered])


def _stack(vectors: list[np.ndarray]) -> np.ndarray:
    if not vectors:
        return np.zeros((0, 0), dtype=np.float32)
    dim = vectors[0].shape[0]
    if any(v.shape[0] != dim for v in vectors):
        # 同じ model_key で次元が違う(リモートの接続先のモデルを替えた)。多い方の次元に揃える。
        counts: dict[int, int] = {}
        for v in vectors:
            counts[v.shape[0]] = counts.get(v.shape[0], 0) + 1
        dim = max(counts, key=lambda d: counts[d])
        logger.warning("埋め込みの次元が揃っていません。%d 次元のものだけを使います", dim)
        vectors = [v if v.shape[0] == dim else np.zeros(dim, dtype=np.float32) for v in vectors]
    return np.stack(vectors).astype(np.float32, copy=False)


def _top_indices(scores: np.ndarray, k: int) -> np.ndarray:
    """スコアの高い順に最大 k 個の位置(同点は位置の小さい方を先に)。"""
    n = scores.shape[0]
    if n == 0 or k <= 0:
        return np.zeros(0, dtype=np.int64)
    if k < n:
        part = np.argpartition(-scores, k - 1)[:k]
    else:
        part = np.arange(n)
    order = np.lexsort((part, -scores[part]))
    return part[order]


def knn_matrix(matrix: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
    """各行の近傍(自分自身を先頭に、ほか最大 k 件を類似度の高い順)。

    返すのは `(indices, similarities)`。どちらも形が `(n, 1 + min(k, n - 1))`。先頭の列は
    自分自身(類似度 1、距離 0。umap-js の `setPrecomputedKNN` にそのまま渡せる形)。
    """
    n = matrix.shape[0]
    width = 1 + min(k, max(0, n - 1))
    indices = np.zeros((n, width), dtype=np.int64)
    sims = np.zeros((n, width), dtype=np.float32)
    for start in range(0, n, _CHUNK_ROWS):
        block = matrix[start : start + _CHUNK_ROWS] @ matrix.T
        for offset in range(block.shape[0]):
            row = start + offset
            scores = block[offset].copy()
            scores[row] = -np.inf
            others = _top_indices(scores, width - 1)
            indices[row, 0] = row
            sims[row, 0] = 1.0
            indices[row, 1:] = others
            sims[row, 1:] = np.clip(scores[others], -1.0, 1.0)
    return indices, sims


def pairs_at_least(matrix: np.ndarray, threshold: float) -> list[tuple[int, int, float]]:
    """類似度がしきい値以上の組 `(i, j, 類似度)`(i < j)。"""
    pairs: list[tuple[int, int, float]] = []
    n = matrix.shape[0]
    for start in range(0, n, _CHUNK_ROWS):
        block = matrix[start : start + _CHUNK_ROWS] @ matrix.T
        rows, cols = np.nonzero(block >= threshold)
        for r, c in zip(rows.tolist(), cols.tolist(), strict=True):
            i = start + r
            if i < c:
                pairs.append((i, c, float(min(1.0, block[r, c]))))
    return pairs


class VectorIndex(Protocol):
    backend: str

    def top_k(
        self,
        db: Session,
        model_key: str,
        query: np.ndarray,
        flt: AssetFilter,
        k: int,
        exclude_ids: Sequence[uuid.UUID] = (),
    ) -> list[Hit]: ...

    def vector_of(self, db: Session, model_key: str, asset_id: uuid.UUID) -> np.ndarray | None: ...

    def subset(
        self, db: Session, model_key: str, asset_ids: Sequence[uuid.UUID]
    ) -> tuple[list[uuid.UUID], np.ndarray]: ...


def neighbors(
    index: VectorIndex,
    db: Session,
    model_key: str,
    asset_id: uuid.UUID,
    flt: AssetFilter,
    k: int,
) -> list[Hit] | None:
    """1枚の画像に近い画像(自分自身を除く)。その画像のベクトルが無ければ None。"""
    vector = index.vector_of(db, model_key, asset_id)
    if vector is None:
        return None
    return index.top_k(db, model_key, vector, flt, k, exclude_ids=(asset_id,))


# -- numpy ------------------------------------------------------------------------


@dataclass
class _Matrix:
    version: int
    ids: list[uuid.UUID]
    positions: dict[uuid.UUID, int]
    matrix: np.ndarray


class NumpyIndex:
    """モデルごとの行列をメモリに持つ(版が変わったら読み直す)。"""

    backend = embedding_index.BACKEND_NUMPY

    def __init__(self, version_of: Callable[[str], int]) -> None:
        self._version_of = version_of
        self._lock = threading.Lock()
        self._cache: dict[str, _Matrix] = {}
        # テストで読み直した回数を確かめる。
        self.loads = 0

    def _matrix(self, db: Session, model_key: str) -> _Matrix:
        with self._lock:
            version = self._version_of(model_key)
            cached = self._cache.get(model_key)
            if cached is not None and cached.version == version:
                return cached
            # 版は読む前に取る(読んでいる間に書かれたら、次の呼び出しで読み直す)。
            rows = db.execute(
                select(AssetEmbedding.asset_id, AssetEmbedding.vector)
                .where(AssetEmbedding.model_key == model_key, _has_vector())
                .order_by(AssetEmbedding.asset_id)
            ).all()
            ids = [asset_id for asset_id, _ in rows]
            matrix = _stack([blob_to_vector(bytes(blob)) for _, blob in rows])
            entry = _Matrix(
                version=version,
                ids=ids,
                positions={asset_id: i for i, asset_id in enumerate(ids)},
                matrix=matrix,
            )
            # 使うモデルは1つなので、ほかのモデルの行列は捨てる(メモリを抱えない)。
            self._cache = {model_key: entry}
            self.loads += 1
            return entry

    def top_k(
        self,
        db: Session,
        model_key: str,
        query: np.ndarray,
        flt: AssetFilter,
        k: int,
        exclude_ids: Sequence[uuid.UUID] = (),
    ) -> list[Hit]:
        entry = self._matrix(db, model_key)
        if not entry.ids:
            return []
        excluded = set(exclude_ids)
        allowed = [
            entry.positions[i]
            for i in candidate_ids(db, model_key, flt)
            if i in entry.positions and i not in excluded
        ]
        if not allowed:
            return []
        rows = np.asarray(allowed, dtype=np.int64)
        q = l2_normalize(np.asarray(query, dtype=np.float32).reshape(-1))[0]
        if q.shape[0] != entry.matrix.shape[1]:
            return []
        scores = entry.matrix[rows] @ q
        top = _top_indices(scores, k)
        hits = [
            Hit(asset_id=entry.ids[int(rows[i])], score=float(np.clip(scores[i], -1.0, 1.0)))
            for i in top
        ]
        # 同点の並びを pgvector の方式と揃える。
        hits.sort(key=lambda h: (-h.score, str(h.asset_id)))
        return hits

    def vector_of(self, db: Session, model_key: str, asset_id: uuid.UUID) -> np.ndarray | None:
        entry = self._matrix(db, model_key)
        position = entry.positions.get(asset_id)
        return None if position is None else entry.matrix[position]

    def subset(
        self, db: Session, model_key: str, asset_ids: Sequence[uuid.UUID]
    ) -> tuple[list[uuid.UUID], np.ndarray]:
        entry = self._matrix(db, model_key)
        ordered = [i for i in asset_ids if i in entry.positions]
        if not ordered:
            return [], np.zeros((0, 0), dtype=np.float32)
        rows = np.asarray([entry.positions[i] for i in ordered], dtype=np.int64)
        return ordered, entry.matrix[rows]


# -- pgvector ---------------------------------------------------------------------


class _PgVector(UserDefinedType):
    """`CAST(:q AS vector(<次元>))` を書くための型(値は `'[...]'` の文字列で渡す)。"""

    cache_ok = True

    def __init__(self, dim: int) -> None:
        self.dim = int(dim)

    def get_col_spec(self, **_kw: Any) -> str:
        return f"vector({self.dim})"


# relaxed_order は pgvector 0.8 から。
_ITERATIVE_SCAN_MIN_VERSION = (0, 8)
_EF_SEARCH_DEFAULT = 40
_EF_SEARCH_MAX = 1000


def _parse_version(value: str) -> tuple[int, ...]:
    parts: list[int] = []
    for piece in value.split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


class PgvectorIndex:
    """PostgreSQL の pgvector で探す(部分 HNSW 索引)。"""

    backend = embedding_index.BACKEND_PGVECTOR

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._iterative_scan: bool | None = None

    def _supports_iterative_scan(self, db: Session) -> bool:
        with self._lock:
            if self._iterative_scan is None:
                version = db.execute(
                    text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
                ).scalar_one_or_none()
                self._iterative_scan = (
                    version is not None
                    and _parse_version(str(version)) >= _ITERATIVE_SCAN_MIN_VERSION
                )
            return self._iterative_scan

    @staticmethod
    def _dim(db: Session, model_key: str) -> int | None:
        return db.execute(
            select(AssetEmbedding.dim)
            .where(
                AssetEmbedding.model_key == model_key,
                AssetEmbedding.dim.is_not(None),
                _has_vector(),
            )
            .limit(1)
        ).scalar_one_or_none()

    def query_statement(
        self,
        model_key: str,
        dim: int,
        query: np.ndarray,
        flt: AssetFilter,
        k: int,
        exclude_ids: Sequence[uuid.UUID] = (),
    ) -> Any:
        """上位 k 件の SELECT(EXPLAIN でも使う)。式と条件は部分 HNSW 索引と同じ形にする。"""
        dim = int(dim)
        column = f"{embedding_index.TABLE}.{embedding_index.COLUMN}::vector({dim})"
        distance = literal_column(column).op("<=>", return_type=Float)(
            cast(bindparam("query_vector", embedding_index.vector_literal(query)), _PgVector(dim))
        )
        statement = (
            select(AssetEmbedding.asset_id, distance.label("distance"))
            .join(Asset, Asset.id == AssetEmbedding.asset_id)
            .where(
                # 部分索引の条件と同じリテラル(バインド変数にしない)。
                text(
                    f"{embedding_index.TABLE}.model_key = "
                    f"{embedding_index.quote_literal(model_key)}"
                ),
                text(f"{embedding_index.TABLE}.{embedding_index.COLUMN} IS NOT NULL"),
                *flt.conditions(),
            )
            .order_by(distance)
            .limit(k)
        )
        if exclude_ids:
            statement = statement.where(AssetEmbedding.asset_id.not_in(list(exclude_ids)))
        return statement

    def prepare_scan(self, db: Session, k: int) -> None:
        """このトランザクションの HNSW の探索の設定(`SET LOCAL`)。"""
        ef_search = min(_EF_SEARCH_MAX, max(_EF_SEARCH_DEFAULT, k * 2))
        db.execute(text(f"SET LOCAL hnsw.ef_search = {int(ef_search)}"))
        if self._supports_iterative_scan(db):
            db.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))

    def top_k(
        self,
        db: Session,
        model_key: str,
        query: np.ndarray,
        flt: AssetFilter,
        k: int,
        exclude_ids: Sequence[uuid.UUID] = (),
    ) -> list[Hit]:
        if k <= 0:
            return []
        q = np.asarray(query, dtype=np.float32).reshape(-1)
        dim = self._dim(db, model_key)
        if dim is None or dim != q.shape[0]:
            return []
        self.prepare_scan(db, k)
        rows = db.execute(self.query_statement(model_key, dim, q, flt, k, exclude_ids)).all()
        hits = [
            Hit(asset_id=asset_id, score=float(min(1.0, max(-1.0, 1.0 - float(distance)))))
            for asset_id, distance in rows
            if distance is not None
        ]
        # relaxed_order では順番が少し前後することがあるので並べ直す。
        hits.sort(key=lambda h: (-h.score, str(h.asset_id)))
        return hits

    def vector_of(self, db: Session, model_key: str, asset_id: uuid.UUID) -> np.ndarray | None:
        blob = db.execute(
            select(AssetEmbedding.vector).where(
                AssetEmbedding.asset_id == asset_id,
                AssetEmbedding.model_key == model_key,
                _has_vector(),
            )
        ).scalar_one_or_none()
        return None if blob is None else blob_to_vector(bytes(blob))

    def subset(
        self, db: Session, model_key: str, asset_ids: Sequence[uuid.UUID]
    ) -> tuple[list[uuid.UUID], np.ndarray]:
        return read_vectors(db, model_key, asset_ids)


def build_index(backend: str, version_of: Callable[[str], int]) -> VectorIndex:
    if backend == embedding_index.BACKEND_PGVECTOR:
        return PgvectorIndex()
    return NumpyIndex(version_of)
