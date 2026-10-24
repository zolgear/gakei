"""履歴の並べ替え(ADR-0054)。画像単位の並び(グループ、似た画像の順)を作る。

- 対象は、見える Run(削除されていない)が作った、削除されていない生成画像だけ(1章)。
- グループ: グループの並び(`position ASC, created_at DESC, id DESC`)、中は古い順。どのグループにも
  入っていない画像(削除済み・見えないグループに入っているものを含む)は最後(2章)。カーソルは keyset。
- 似た画像の順: 使うモデルのベクトルで、k-means の重心の鎖とクラスタの中の鎖で1列にする(3章)。
  埋め込みの無い画像は最後に新しい順。結果はプロセスの中にキャッシュし、カーソルは順位。
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import threading
import uuid
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np
from sqlalchemy import ColumnElement, and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.auth.identity import CurrentUser
from app.config import Settings
from app.domain.favorites import favorite_asset_ids
from app.domain.history_filters import HistoryLabelFilter
from app.domain.models import Asset, AssetGroup, AssetGroupMember, AssetKind, Run
from app.domain.run_views import summarize_runs
from app.domain.schemas import HistoryAssetItem, HistoryAssetListResponse, HistoryGroupSection
from app.domain.semantic_search import require_active
from app.domain.vector_index import VectorIndex
from app.domain.visibility import group_visible, run_visible
from app.i18n import t


class InvalidHistoryCursorError(ValueError):
    pass


# -- 対象の画像 ---------------------------------------------------------------------


_NO_LABELS = HistoryLabelFilter()


def _base_conditions(
    viewer: CurrentUser, *, favorite: bool = False, labels: HistoryLabelFilter = _NO_LABELS
) -> list[ColumnElement[bool]]:
    """見える Run が作った、削除されていない生成画像(Run と結合して使う)。`favorite` なら
    `viewer` のお気に入りだけ(ADR-0055)。`labels` は形・透過・領域の絞り込み(形はその画像の形)。"""
    conditions = [
        Asset.deleted_at.is_(None),
        Asset.kind == AssetKind.GENERATED,
        Run.deleted_at.is_(None),
        run_visible(viewer),
    ]
    if favorite:
        conditions.append(Asset.id.in_(favorite_asset_ids(viewer)))
    conditions.extend(labels.asset_conditions())
    return conditions


def _items(
    db: Session, viewer: CurrentUser, rows: Sequence[tuple[uuid.UUID, uuid.UUID, uuid.UUID | None]]
) -> list[HistoryAssetItem]:
    """(asset_id, run_id, group_id) の並びを、同じ並びの項目にする。"""
    run_ids = list(dict.fromkeys(run_id for _a, run_id, _g in rows))
    runs = {r.id: r for r in db.execute(select(Run).where(Run.id.in_(run_ids))).scalars()}
    ordered_runs = [runs[i] for i in run_ids if i in runs]
    summaries = {s.id: s for s in summarize_runs(db, ordered_runs, viewer)}
    return [
        HistoryAssetItem(asset_id=asset_id, group_id=group_id, run=summaries[run_id])
        for asset_id, run_id, group_id in rows
        if run_id in summaries
    ]


def _encode(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode(cursor: str) -> dict[str, Any]:
    try:
        value = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise InvalidHistoryCursorError(t("pagination.invalidCursor")) from e
    if not isinstance(value, dict):
        raise InvalidHistoryCursorError(t("pagination.invalidCursor"))
    return value


# -- グループの並び -----------------------------------------------------------------


@dataclass(frozen=True)
class GroupCursor:
    """直前のページの最後の項目の位置。`group_id` が None なら「グループなし」の節。"""

    group_id: uuid.UUID | None
    group_position: int | None
    group_created_at: datetime | None
    asset_created_at: datetime
    output_index: int
    asset_id: uuid.UUID

    def encode(self) -> str:
        return _encode(
            {
                "g": str(self.group_id) if self.group_id else None,
                "gp": self.group_position,
                "gc": self.group_created_at.isoformat() if self.group_created_at else None,
                "ac": self.asset_created_at.isoformat(),
                "oi": self.output_index,
                "a": str(self.asset_id),
            }
        )

    @classmethod
    def decode(cls, cursor: str) -> GroupCursor:
        value = _decode(cursor)
        try:
            group_id = uuid.UUID(value["g"]) if value.get("g") else None
            return cls(
                group_id=group_id,
                group_position=int(value["gp"]) if group_id else None,
                group_created_at=datetime.fromisoformat(value["gc"]) if group_id else None,
                asset_created_at=datetime.fromisoformat(value["ac"]),
                output_index=int(value["oi"]),
                asset_id=uuid.UUID(value["a"]),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise InvalidHistoryCursorError(t("pagination.invalidCursor")) from e


def _output_index() -> ColumnElement[int]:
    return func.coalesce(Asset.output_index, 0)


def _grouped_query(
    viewer: CurrentUser, *, favorite: bool = False, labels: HistoryLabelFilter = _NO_LABELS
) -> Any:
    """画像と、その画像が入っている見えるグループ(無ければ NULL の列)。ADR-0022 により所属は1つ。"""
    return (
        select(
            Asset.id,
            Asset.produced_by_run_id,
            Asset.created_at,
            _output_index().label("oi"),
            AssetGroup.id.label("gid"),
            AssetGroup.position,
            AssetGroup.created_at.label("gcreated"),
        )
        .join(Run, Run.id == Asset.produced_by_run_id)
        .outerjoin(AssetGroupMember, AssetGroupMember.asset_id == Asset.id)
        .outerjoin(
            AssetGroup,
            and_(
                AssetGroup.id == AssetGroupMember.asset_group_id,
                AssetGroup.deleted_at.is_(None),
                group_visible(viewer),
            ),
        )
        .where(*_base_conditions(viewer, favorite=favorite, labels=labels))
    )


def _after_asset(cursor: GroupCursor) -> ColumnElement[bool]:
    """同じ節の中で、カーソルより後(古い順: 作成時刻、出力の番号、id の昇順)。"""
    return or_(
        Asset.created_at > cursor.asset_created_at,
        and_(Asset.created_at == cursor.asset_created_at, _output_index() > cursor.output_index),
        and_(
            Asset.created_at == cursor.asset_created_at,
            _output_index() == cursor.output_index,
            Asset.id > cursor.asset_id,
        ),
    )


def _after_cursor(cursor: GroupCursor) -> ColumnElement[bool]:
    if cursor.group_id is None:
        # 「グループなし」の節は最後なので、その節の続きだけ。
        return and_(AssetGroup.id.is_(None), _after_asset(cursor))
    later_group = or_(
        AssetGroup.position > cursor.group_position,
        and_(
            AssetGroup.position == cursor.group_position,
            AssetGroup.created_at < cursor.group_created_at,
        ),
        and_(
            AssetGroup.position == cursor.group_position,
            AssetGroup.created_at == cursor.group_created_at,
            AssetGroup.id < cursor.group_id,
        ),
    )
    return or_(
        AssetGroup.id.is_(None),
        later_group,
        and_(AssetGroup.id == cursor.group_id, _after_asset(cursor)),
    )


def group_sections(
    db: Session,
    viewer: CurrentUser,
    *,
    favorite: bool = False,
    labels: HistoryLabelFilter = _NO_LABELS,
) -> list[HistoryGroupSection]:
    """グループの並び順の節と件数(画像のある節だけ)。「グループなし」は最後。件数は絞り込みの後。"""
    counts = db.execute(
        select(AssetGroup.id, func.count(Asset.id))
        .select_from(Asset)
        .join(Run, Run.id == Asset.produced_by_run_id)
        .outerjoin(AssetGroupMember, AssetGroupMember.asset_id == Asset.id)
        .outerjoin(
            AssetGroup,
            and_(
                AssetGroup.id == AssetGroupMember.asset_group_id,
                AssetGroup.deleted_at.is_(None),
                group_visible(viewer),
            ),
        )
        .where(*_base_conditions(viewer, favorite=favorite, labels=labels))
        .group_by(AssetGroup.id)
    ).all()
    by_group = {group_id: count for group_id, count in counts}
    groups = db.execute(
        select(AssetGroup.id, AssetGroup.name)
        .where(AssetGroup.deleted_at.is_(None), group_visible(viewer))
        .order_by(AssetGroup.position.asc(), AssetGroup.created_at.desc(), AssetGroup.id.desc())
    ).all()
    sections = [
        HistoryGroupSection(id=group_id, name=name, count=by_group[group_id])
        for group_id, name in groups
        if by_group.get(group_id)
    ]
    if by_group.get(None):
        sections.append(HistoryGroupSection(id=None, name=None, count=by_group[None]))
    return sections


def list_by_group(
    db: Session,
    viewer: CurrentUser,
    *,
    limit: int,
    cursor: str | None,
    favorite: bool = False,
    labels: HistoryLabelFilter = _NO_LABELS,
) -> HistoryAssetListResponse:
    query = _grouped_query(viewer, favorite=favorite, labels=labels)
    if cursor is not None:
        query = query.where(_after_cursor(GroupCursor.decode(cursor)))
    query = query.order_by(
        case((AssetGroup.id.is_(None), 1), else_=0),
        AssetGroup.position.asc(),
        AssetGroup.created_at.desc(),
        AssetGroup.id.desc(),
        Asset.created_at.asc(),
        _output_index().asc(),
        Asset.id.asc(),
    ).limit(limit + 1)
    rows = db.execute(query).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    items = _items(db, viewer, [(r.id, r.produced_by_run_id, r.gid) for r in rows])
    next_cursor = None
    if has_more and rows:
        last = rows[-1]
        next_cursor = GroupCursor(
            group_id=last.gid,
            group_position=last.position,
            group_created_at=last.gcreated,
            asset_created_at=last.created_at,
            output_index=last.oi,
            asset_id=last.id,
        ).encode()
    return HistoryAssetListResponse(
        items=items,
        next_cursor=next_cursor,
        groups=group_sections(db, viewer, favorite=favorite, labels=labels),
    )


# -- 似た画像の順 -------------------------------------------------------------------

KMEANS_MAX_ITER = 8


def _normalize(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (matrix / norms).astype(np.float32, copy=False)


def _greedy_chain(vectors: np.ndarray, start: int) -> list[int]:
    """`start` から、まだ通っていない中で最も近い(内積が大きい)ものへ順に進む。メモリは O(n)。"""
    n = len(vectors)
    visited = np.zeros(n, dtype=bool)
    order = [start]
    visited[start] = True
    current = start
    for _ in range(n - 1):
        sims = vectors @ vectors[current]
        sims[visited] = -np.inf
        current = int(np.argmax(sims))
        visited[current] = True
        order.append(current)
    return order


def _kmeans_pp(vectors: np.ndarray, k: int, first: int, rng: np.random.Generator) -> np.ndarray:
    """k-means++ の初期値(コサイン距離)。最初の中心は `first`。"""
    centers = [first]
    best = vectors @ vectors[first]
    for _ in range(1, k):
        dist = np.clip(1.0 - best, 0.0, None) ** 2
        total = float(dist.sum())
        if total <= 0:
            break
        nxt = int(rng.choice(len(vectors), p=dist / total))
        centers.append(nxt)
        best = np.maximum(best, vectors @ vectors[nxt])
    return vectors[centers].copy()


def embedding_order(matrix: np.ndarray, *, start: int = 0, seed: int = 0) -> list[int]:
    """似たもの同士が近くに並ぶ順(行の添字の並び)。全件の距離行列は作らない(ADR-0054 3章)。

    1. 球面 k-means(k ≈ √n)でまとめる。2. 重心を `start` のクラスタから最近傍の鎖でつなぐ。
    3. 各クラスタの中を、直前の画像に最も近いものから最近傍の鎖で並べる。
    同じ入力なら同じ順になる(乱数の種は固定)。
    """
    n = len(matrix)
    if n <= 2:
        return list(range(n))
    vectors = _normalize(np.asarray(matrix, dtype=np.float32))
    k = max(1, round(math.sqrt(n)))
    if k == 1:
        return _greedy_chain(vectors, start)

    rng = np.random.default_rng(seed)
    centers = _kmeans_pp(vectors, k, start, rng)
    labels = np.full(n, -1, dtype=np.int64)
    for _ in range(KMEANS_MAX_ITER):
        new_labels = np.argmax(vectors @ centers.T, axis=1)
        if np.array_equal(new_labels, labels):
            break
        labels = new_labels
        for c in range(len(centers)):
            members = vectors[labels == c]
            if len(members):
                centers[c] = members.mean(axis=0)
        centers = _normalize(centers)

    clusters = [np.flatnonzero(labels == c) for c in range(len(centers))]
    present = [c for c in range(len(centers)) if len(clusters[c])]
    centroid_order = [
        present[i] for i in _greedy_chain(centers[present], present.index(int(labels[start])))
    ]

    order: list[int] = []
    for c in centroid_order:
        members = clusters[c]
        sub = vectors[members]
        if not order:
            first_local = int(np.flatnonzero(members == start)[0])
        else:
            first_local = int(np.argmax(sub @ vectors[order[-1]]))
        order.extend(int(members[i]) for i in _greedy_chain(sub, first_local))
    return order


class EmbeddingOrderCache:
    """並びの結果(Asset の id の列)をプロセスの中に持つ。直近の数件だけ(ADR-0054 3章)。"""

    def __init__(self, max_entries: int = 8) -> None:
        self._entries: OrderedDict[tuple[str, int, str, str], list[uuid.UUID]] = OrderedDict()
        self._max = max_entries
        self._lock = threading.Lock()

    def get_or_build(
        self, key: tuple[str, int, str, str], build: Callable[[], list[uuid.UUID]]
    ) -> list[uuid.UUID]:
        with self._lock:
            cached = self._entries.get(key)
            if cached is not None:
                self._entries.move_to_end(key)
                return cached
        # 計算は鍵ごとにロックの外で行う(同じ鍵が同時に来たら2回計算するが、結果は同じ)。
        value = build()
        with self._lock:
            self._entries[key] = value
            self._entries.move_to_end(key)
            while len(self._entries) > self._max:
                self._entries.popitem(last=False)
        return value


def _ids_digest(ids: Sequence[uuid.UUID]) -> str:
    digest = hashlib.sha256()
    for asset_id in ids:
        digest.update(asset_id.bytes)
    return digest.hexdigest()


def list_by_embedding(
    db: Session,
    viewer: CurrentUser,
    *,
    settings: Settings,
    index: VectorIndex,
    version_of: Callable[[str], int],
    cache: EmbeddingOrderCache,
    limit: int,
    cursor: str | None,
    favorite: bool = False,
    labels: HistoryLabelFilter = _NO_LABELS,
) -> HistoryAssetListResponse:
    """似た画像の順。埋め込みが無効・使えなければ `EmbeddingsConflictError`(409)。
    `favorite` ならお気に入りの画像だけで並びを作る(全体の並びから抜き出すのではない。ADR-0055)。
    形・透過・領域の絞り込み(`labels`)も同じく、絞った画像だけで並びを作る。"""
    active = require_active(db, settings)
    offset = 0
    if cursor is not None:
        value = _decode(cursor)
        try:
            offset = int(value["o"])
        except (KeyError, TypeError, ValueError) as e:
            raise InvalidHistoryCursorError(t("pagination.invalidCursor")) from e
        if offset < 0:
            raise InvalidHistoryCursorError(t("pagination.invalidCursor"))

    rows = db.execute(
        select(Asset.id, Asset.produced_by_run_id)
        .join(Run, Run.id == Asset.produced_by_run_id)
        .where(*_base_conditions(viewer, favorite=favorite, labels=labels))
        .order_by(Asset.created_at.desc(), Asset.id.desc())
    ).all()
    run_of = {asset_id: run_id for asset_id, run_id in rows}
    newest_first = [asset_id for asset_id, _run in rows]

    def build() -> list[uuid.UUID]:
        with_vectors, matrix = index.subset(db, active.model_key, newest_first)
        if with_vectors:
            ordered = [with_vectors[i] for i in embedding_order(matrix)]
        else:
            ordered = []
        seen = set(ordered)
        # 埋め込みの無い画像は最後に、新しい順。
        return ordered + [asset_id for asset_id in newest_first if asset_id not in seen]

    # 対象の集合の要約が変われば鍵も変わるが、絞り込みは意味として鍵に明示する(ADR-0055)。
    scope = "favorite" if favorite else "all"
    if labels.scope_key():
        scope = f"{scope};{labels.scope_key()}"
    key = (active.model_key, version_of(active.model_key), scope, _ids_digest(newest_first))
    order = cache.get_or_build(key, build)
    page = order[offset : offset + limit]
    items = _items(db, viewer, [(asset_id, run_of[asset_id], None) for asset_id in page])
    next_offset = offset + len(page)
    next_cursor = _encode({"o": next_offset}) if next_offset < len(order) else None
    return HistoryAssetListResponse(items=items, next_cursor=next_cursor)
