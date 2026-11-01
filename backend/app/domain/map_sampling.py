"""マップのノードの抽出(ADR-0057)。候補が上限を超えたときに、どの画像を並べるかを決める。

- `recent`(新しい順): 候補(新しい順)の先頭から上限まで。ADR-0033 からの挙動。
- `spread`(全体から均等に。既定): 候補の全件に球面 k-means(k ≈ √n。ADR-0054 の部品)を掛け、
  クラスタごとの配分を √サイズ に比例させて上限に収める。クラスタの中は、重心に近い点と、
  選んだ点から最も離れた点を交互に選ぶ。間引いた画像は、同じクラスタの中で最も近い選んだ点に
  数え、ノードごとの代表数にする(合計は候補の数)。

どちらも決定的(乱数の種は固定)で、選んだ点は候補の並び(新しい順)のまま返す。`build_graph` と
`graph_scores` は同じ引数ならこのモジュールを同じように通るので、ノードの並びが一致する。
"""

from __future__ import annotations

import hashlib
import math
import threading
import uuid
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np

from app.domain.schemas import MapSampling

__all__ = ["MapSampling"]

DEFAULT_MAP_SAMPLING: MapSampling = "spread"
SAMPLE_SEED = 0


@dataclass(frozen=True)
class MapSample:
    """選んだ行の添字(昇順 = 候補の並び)と、それぞれが代表する候補の数(自分を含む)。"""

    indices: list[int]
    represents: list[int]


def recent_sample(n: int, limit: int) -> MapSample:
    """新しい順の先頭から `limit` 件。間引いた画像は誰にも数えない(代表数はすべて 1)。"""
    count = max(0, min(n, limit))
    return MapSample(indices=list(range(count)), represents=[1] * count)


def allocate(sizes: Sequence[int], limit: int) -> list[int]:
    """クラスタごとの枚数。どのクラスタにも 1 枚は配り、残りを √サイズ に比例して配る。

    サイズを超えて配らない(余った分はほかのクラスタへ回す)。合計は `min(limit, 合計サイズ)`
    で、クラスタの数が `limit` を超えるときは呼ばない前提(呼ぶ側が k を `limit` 以下にする)。
    端数は割り当ての大きい順(同じなら前のクラスタ)に 1 枚ずつ配るので、決定的。
    """
    size = np.asarray(sizes, dtype=np.int64)
    total = int(size.sum())
    if total <= limit:
        return [int(s) for s in size]
    if len(size) > limit:
        raise ValueError("クラスタの数が上限を超えています")
    alloc = np.minimum(size, 1)
    remaining = limit - int(alloc.sum())
    weights = np.sqrt(size.astype(np.float64))
    while remaining > 0:
        room = size - alloc
        w = np.where(room > 0, weights, 0.0)
        share = remaining * w / w.sum()
        add = np.minimum(np.floor(share).astype(np.int64), room)
        if int(add.sum()) == 0:
            # 端数だけが残った。割り当ての大きい順に 1 枚ずつ。
            for c in np.argsort(-share, kind="stable"):
                if remaining == 0:
                    break
                if room[c] > 0:
                    alloc[c] += 1
                    remaining -= 1
            continue
        alloc += add
        remaining -= int(add.sum())
    return [int(a) for a in alloc]


def spread_within(vectors: np.ndarray, center: np.ndarray, count: int) -> list[int]:
    """クラスタの中から `count` 件を選ぶ(行の添字、選んだ順)。`vectors` は正規化済み。

    重心に近い順の次の点(代表点)と、選んだ点との最大の類似度が最も小さい点(最も離れた点)を
    交互に取る。同点は前の行(新しい画像)を取る。
    """
    size = len(vectors)
    if count >= size:
        return list(range(size))
    by_center = np.argsort(-(vectors @ center), kind="stable")
    chosen = np.zeros(size, dtype=bool)
    nearest = np.full(size, -np.inf, dtype=np.float32)
    picks: list[int] = []
    cursor = 0
    for step in range(count):
        if step % 2 == 0:
            while chosen[by_center[cursor]]:
                cursor += 1
            pick = int(by_center[cursor])
        else:
            pick = int(np.argmin(np.where(chosen, np.inf, nearest)))
        chosen[pick] = True
        picks.append(pick)
        nearest = np.maximum(nearest, vectors @ vectors[pick])
    return picks


def spread_sample(matrix: np.ndarray, limit: int, *, seed: int = SAMPLE_SEED) -> MapSample:
    """全体から均等に `limit` 件を選ぶ。候補が `limit` 以下なら全件(代表数はすべて 1)。"""
    from app.domain.history_sort import normalize_rows, spherical_kmeans

    n = len(matrix)
    if n <= limit:
        return MapSample(indices=list(range(n)), represents=[1] * n)
    if limit <= 0:
        return MapSample(indices=[], represents=[])
    vectors = normalize_rows(matrix)
    k = min(limit, max(1, round(math.sqrt(n))))
    if k == 1:
        labels = np.zeros(n, dtype=np.int64)
        centers = normalize_rows(vectors.mean(axis=0, keepdims=True))
    else:
        # 最初の中心は最も新しい画像(添字 0)。
        labels, centers = spherical_kmeans(vectors, k, first=0, seed=seed)
    clusters = [np.flatnonzero(labels == c) for c in range(len(centers))]
    present = [c for c in range(len(centers)) if len(clusters[c])]
    quotas = allocate([len(clusters[c]) for c in present], limit)

    represents: dict[int, int] = {}
    for c, quota in zip(present, quotas, strict=True):
        members = clusters[c]
        sub = vectors[members]
        picks = spread_within(sub, centers[c], quota)
        # 間引いた画像は、同じクラスタの中で最も近い選んだ点に数える。選んだ点は自分に数える
        # (同じベクトルの画像が複数選ばれても、どれも 1 以上になるように)。
        owner = np.argmax(sub @ sub[picks].T, axis=1)
        owner[picks] = np.arange(len(picks))
        counts = np.bincount(owner, minlength=len(picks))
        for j, local in enumerate(picks):
            represents[int(members[local])] = int(counts[j])
    indices = sorted(represents)
    return MapSample(indices=indices, represents=[represents[i] for i in indices])


# -- キャッシュ ----------------------------------------------------------------------

CacheKey = tuple[str, int, str, str, int]


class MapSampleCache:
    """抽出の結果(選んだ Asset の id と代表数)をプロセスの中に持つ。直近の数件だけ。

    鍵は `(model_key, 埋め込みの版, 見る人と絞り込みの範囲, 候補の id の要約, 上限)`
    (ADR-0054 3章の `EmbeddingOrderCache` と同じ考え方)。画像の追加・削除や埋め込みの更新で
    鍵が変わり、作り直す。
    """

    def __init__(self, max_entries: int = 8) -> None:
        self._entries: OrderedDict[CacheKey, tuple[list[uuid.UUID], list[int]]] = OrderedDict()
        self._max = max_entries
        self._lock = threading.Lock()

    def get_or_build(
        self, key: CacheKey, build: Callable[[], tuple[list[uuid.UUID], list[int]]]
    ) -> tuple[list[uuid.UUID], list[int]]:
        with self._lock:
            cached = self._entries.get(key)
            if cached is not None:
                self._entries.move_to_end(key)
                return cached
        # 計算はロックの外で行う(同じ鍵が同時に来たら2回計算するが、結果は同じ)。
        value = build()
        with self._lock:
            self._entries[key] = value
            self._entries.move_to_end(key)
            while len(self._entries) > self._max:
                self._entries.popitem(last=False)
        return value

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


def ids_digest(ids: Sequence[uuid.UUID]) -> str:
    digest = hashlib.sha256()
    for asset_id in ids:
        digest.update(asset_id.bytes)
    return digest.hexdigest()
