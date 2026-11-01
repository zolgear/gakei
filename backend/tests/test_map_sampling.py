"""マップのノードの抽出(ADR-0057)。純粋関数(配分、クラスタの中の選び方、全体の抽出)と
キャッシュ。API は `test_semantic_search_api.py`。"""

from __future__ import annotations

import uuid

import numpy as np

from app.domain.map_sampling import (
    MapSampleCache,
    allocate,
    recent_sample,
    spread_sample,
    spread_within,
)


def _clustered(sizes: list[int], dim: int = 32, seed: int = 1, spread: float = 0.05) -> np.ndarray:
    """方向の違う塊を `sizes` の順に縦に積んだ行列(先頭が「新しい」画像)。"""
    rng = np.random.default_rng(seed)
    rows = []
    for size in sizes:
        center = rng.normal(size=dim)
        center /= np.linalg.norm(center)
        rows.append(center + rng.normal(scale=spread, size=(size, dim)))
    return np.vstack(rows).astype(np.float32)


def _cluster_of(sizes: list[int]) -> np.ndarray:
    return np.repeat(np.arange(len(sizes)), sizes)


def test_limit_or_less_returns_everything() -> None:
    matrix = _clustered([5, 3])
    sample = spread_sample(matrix, 8)
    assert sample.indices == list(range(8))
    assert sample.represents == [1] * 8
    assert spread_sample(matrix, 100).indices == list(range(8))
    assert spread_sample(np.zeros((0, 4), dtype=np.float32), 10).indices == []


def test_recent_sample_is_the_head() -> None:
    sample = recent_sample(10, 4)
    assert sample.indices == [0, 1, 2, 3]
    assert sample.represents == [1, 1, 1, 1]
    assert recent_sample(3, 10).indices == [0, 1, 2]


def test_spread_sample_is_deterministic_and_sums_to_candidates() -> None:
    sizes = [400, 60, 30, 10]
    matrix = _clustered(sizes)
    first = spread_sample(matrix, 50)
    again = spread_sample(matrix.copy(), 50)
    assert first == again
    assert len(first.indices) == 50
    # 候補の並び(新しい順)のまま、重なりなく。
    assert first.indices == sorted(set(first.indices))
    assert all(count >= 1 for count in first.represents)
    assert sum(first.represents) == sum(sizes)


def test_spread_sample_reaches_every_cluster() -> None:
    """新しい側に大きな塊があっても、古い小さな塊からも選ぶ(新しい順では選ばれない)。"""
    sizes = [900] + [12] * 10
    matrix = _clustered(sizes, seed=3)
    cluster = _cluster_of(sizes)
    sample = spread_sample(matrix, 100)
    picked = cluster[sample.indices]
    assert set(picked.tolist()) == set(range(len(sizes)))
    # 大きな塊は、件数の比(900 / 1020 ≈ 88%)より少ない割合に抑えられる。
    assert (picked == 0).sum() < 88
    # 新しい順なら大きな塊だけになる。
    assert set(cluster[recent_sample(len(sizes), 100).indices].tolist()) == {0}


def test_represents_counts_stay_in_the_same_cluster() -> None:
    sizes = [300, 40, 20]
    matrix = _clustered(sizes, seed=5, spread=0.01)
    cluster = _cluster_of(sizes)
    sample = spread_sample(matrix, 30)
    by_cluster: dict[int, int] = {}
    for index, count in zip(sample.indices, sample.represents, strict=True):
        by_cluster[int(cluster[index])] = by_cluster.get(int(cluster[index]), 0) + count
    # 塊がはっきり分かれていれば、代表数の合計は塊の大きさに一致する。
    assert by_cluster == {0: 300, 1: 40, 2: 20}


def test_allocate_is_sqrt_proportional_capped_and_exact() -> None:
    alloc = allocate([400, 100], 30)
    assert sum(alloc) == 30
    # √400 : √100 = 2 : 1。
    assert alloc[0] > alloc[1]
    assert abs(alloc[0] - 20) <= 1
    # どのクラスタにも 1 枚は配り、サイズは超えない。
    alloc = allocate([1000, 2, 1, 1], 20)
    assert sum(alloc) == 20
    assert alloc[1] >= 1 and alloc[2:] == [1, 1]
    # 小さなクラスタの取り分がサイズを超える分は、ほかへ回す。
    assert allocate([30, 2, 2], 33) == [29, 2, 2]
    # 合計が上限以下なら全部。
    assert allocate([3, 4], 10) == [3, 4]
    # 決定的。
    assert allocate([7, 7, 7], 10) == allocate([7, 7, 7], 10)
    assert sum(allocate([7, 7, 7], 10)) == 10


def test_spread_within_alternates_center_and_far() -> None:
    # 1 次元に近い並び: 重心は 0 番、最も離れたのは 4 番。
    angles = np.array([0.0, 0.1, -0.1, 0.2, 0.6])
    vectors = np.stack([np.cos(angles), np.sin(angles)], axis=1).astype(np.float32)
    center = np.array([1.0, 0.0], dtype=np.float32)
    picks = spread_within(vectors, center, 3)
    assert picks[0] == 0
    assert picks[1] == 4
    # 3 つ目は重心に近い残り(0.1 と -0.1 は同点で、前の行)。
    assert picks[2] == 1
    assert spread_within(vectors, center, 10) == [0, 1, 2, 3, 4]


def test_identical_vectors_still_count_themselves() -> None:
    matrix = np.ones((20, 4), dtype=np.float32)
    sample = spread_sample(matrix, 5)
    assert len(sample.indices) == 5
    assert all(count >= 1 for count in sample.represents)
    assert sum(sample.represents) == 20


def test_cache_reuses_and_evicts() -> None:
    cache = MapSampleCache(max_entries=2)
    calls = 0
    ids = [uuid.uuid4()]

    def build() -> tuple[list[uuid.UUID], list[int]]:
        nonlocal calls
        calls += 1
        return ids, [3]

    key = ("m", 1, "scope", "digest", 10)
    assert cache.get_or_build(key, build) == (ids, [3])
    assert cache.get_or_build(key, build) == (ids, [3])
    assert calls == 1
    cache.get_or_build(("m", 2, "scope", "digest", 10), build)
    cache.get_or_build(("m", 3, "scope", "digest", 10), build)
    assert len(cache) == 2
    cache.get_or_build(key, build)
    assert calls == 4
