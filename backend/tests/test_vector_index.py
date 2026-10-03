"""埋め込みの近傍検索(ADR-0033 6章)。numpy と pgvector の両方で同じテストを通す。

pgvector の方は、`GAKEI_TEST_DATABASE_URL` が拡張 `vector` を使える PostgreSQL のときだけ
動く(それ以外は skip)。見える範囲(ADR-0025)、削除済みとマスクの除外、絞り込みは、上位 k 件を
取る前に掛かることを確かめる。
"""

from __future__ import annotations

import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.auth.identity import LOCAL_ADMIN, CurrentUser
from app.domain import embedding_index
from app.domain.models import (
    AppUser,
    Asset,
    AssetEmbedding,
    AssetGroup,
    AssetGroupMember,
    AssetKind,
    AssetTag,
    Tag,
)
from app.domain.semantic_search import group_pairs
from app.domain.vector_index import (
    AssetFilter,
    NumpyIndex,
    PgvectorIndex,
    knn_matrix,
    neighbors,
    pairs_at_least,
)
from app.embedding.base import l2_normalize, vector_to_blob
from app.model_store.residency import ModelResidency

MODEL = "test:model@1"
DIM = 16


class _Versions:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}

    def __call__(self, model_key: str) -> int:
        return self.values.get(model_key, 0)

    def bump(self, model_key: str = MODEL) -> None:
        self.values[model_key] = self.values.get(model_key, 0) + 1


def _pgvector_available(factory: sessionmaker) -> bool:
    engine = factory.kw["bind"]
    if engine.dialect.name != "postgresql":
        return False
    with engine.connect() as conn:
        return embedding_index.has_embedding_column(conn)


@pytest.fixture(params=["numpy", "pgvector"])
def backend(request: pytest.FixtureRequest, db_session_factory: sessionmaker) -> str:
    if request.param == "pgvector" and not _pgvector_available(db_session_factory):
        pytest.skip("pgvector の PostgreSQL が無い")
    return request.param


class _World:
    """テスト用の Asset とベクトルを直接 DB に入れる。"""

    def __init__(self, factory: sessionmaker, backend: str) -> None:
        self.factory = factory
        self.backend = backend
        self.versions = _Versions()
        self.index = PgvectorIndex() if backend == "pgvector" else NumpyIndex(self.versions)
        self.vectors: dict[uuid.UUID, np.ndarray] = {}
        self._t0 = datetime(2026, 10, 1, tzinfo=UTC)
        self._n = 0

    def user(self, email: str, role: str = "user") -> CurrentUser:
        with self.factory() as db:
            row = AppUser(issuer="https://idp.test", subject=email, email=email, role=role)
            db.add(row)
            db.commit()
            return CurrentUser(id=row.id, name=None, email=email, role=role)  # type: ignore[arg-type]

    def asset(
        self,
        vector: np.ndarray | None,
        *,
        kind: AssetKind = AssetKind.UPLOAD,
        owner: uuid.UUID | None = None,
        deleted: bool = False,
        model_key: str = MODEL,
    ) -> uuid.UUID:
        self._n += 1
        with self.factory() as db:
            asset = Asset(
                kind=kind,
                sha256=f"{self._n:064x}",
                blob_key=f"assets/test/{self._n}",
                mime="image/png",
                width=10,
                height=10,
                bytes=1,
                created_at=self._t0 + timedelta(minutes=self._n),
                created_by_user_id=owner,
                deleted_at=self._t0 if deleted else None,
            )
            db.add(asset)
            db.flush()
            if vector is not None:
                values = l2_normalize(vector)[0]
                db.add(
                    AssetEmbedding(
                        asset_id=asset.id,
                        model_key=model_key,
                        status="succeeded",
                        dim=int(values.shape[0]),
                        vector=vector_to_blob(values),
                        updated_at=self._t0,
                    )
                )
                db.flush()
                if self.backend == "pgvector":
                    embedding_index.write_embedding_column(
                        db.connection(), asset.id, model_key, values
                    )
                self.vectors[asset.id] = values
            db.commit()
            return asset.id

    def finish(self) -> None:
        self.versions.bump()
        if self.backend == "pgvector":
            assert embedding_index.ensure_hnsw_index(self.factory.kw["bind"], MODEL, DIM)

    def expected(self, query: np.ndarray, ids: list[uuid.UUID], k: int) -> list[uuid.UUID]:
        q = l2_normalize(query)[0]
        scored = sorted(ids, key=lambda i: (-float(self.vectors[i] @ q), str(i)))
        return scored[:k]


def _rand(rng: np.random.Generator) -> np.ndarray:
    return rng.standard_normal(DIM).astype(np.float32)


def test_top_k_parity_and_filters(db_session_factory: sessionmaker, backend: str) -> None:
    rng = np.random.default_rng(1)
    world = _World(db_session_factory, backend)
    alice = world.user("alice@example.com")
    bob = world.user("bob@example.com")
    alice_ids = [world.asset(_rand(rng), owner=alice.id) for _ in range(12)]
    bob_ids = [world.asset(_rand(rng), owner=bob.id) for _ in range(12)]
    deleted = world.asset(_rand(rng), owner=alice.id, deleted=True)
    mask = world.asset(_rand(rng), owner=alice.id, kind=AssetKind.MASK)
    sketch = world.asset(_rand(rng), owner=alice.id, kind=AssetKind.SKETCH)
    no_vector = world.asset(None, owner=alice.id)
    other_model = world.asset(_rand(rng), owner=alice.id, model_key="other:model")
    world.finish()

    query = _rand(rng)
    with db_session_factory() as db:
        # 個人モード(全部見える)。削除済み・マスク・ベクトルの無いもの・別のモデルは出ない。
        hits = world.index.top_k(db, MODEL, query, AssetFilter(viewer=LOCAL_ADMIN), 50)
        visible = [*alice_ids, *bob_ids, sketch]
        assert [h.asset_id for h in hits] == world.expected(query, visible, 50)
        for excluded in (deleted, mask, no_vector, other_model):
            assert excluded not in {h.asset_id for h in hits}
        # 類似度は内積(コサイン)。
        q = l2_normalize(query)[0]
        for hit in hits:
            assert hit.score == pytest.approx(float(world.vectors[hit.asset_id] @ q), abs=1e-5)

        # 認証モード: 本人のものだけ(上位 k 件を取る前に絞る)。
        hits = world.index.top_k(db, MODEL, query, AssetFilter(viewer=bob), 5)
        assert [h.asset_id for h in hits] == world.expected(query, bob_ids, 5)
        hits = world.index.top_k(db, MODEL, query, AssetFilter(viewer=alice), 100)
        assert {h.asset_id for h in hits} == {*alice_ids, sketch}

        # 種類、除外。
        hits = world.index.top_k(db, MODEL, query, AssetFilter(viewer=alice, kind="sketch"), 10)
        assert [h.asset_id for h in hits] == [sketch]
        top = world.expected(query, alice_ids + [sketch], 3)
        hits = world.index.top_k(
            db, MODEL, query, AssetFilter(viewer=alice), 2, exclude_ids=[top[0]]
        )
        assert [h.asset_id for h in hits] == top[1:3]


def test_group_and_tag_filters(db_session_factory: sessionmaker, backend: str) -> None:
    rng = np.random.default_rng(2)
    world = _World(db_session_factory, backend)
    ids = [world.asset(_rand(rng)) for _ in range(8)]
    world.finish()
    with db_session_factory() as db:
        group = AssetGroup(name="g")
        db.add(group)
        db.flush()
        for asset_id in ids[:3]:
            db.add(AssetGroupMember(asset_group_id=group.id, asset_id=asset_id))
        tag = Tag(name="cat")
        db.add(tag)
        db.flush()
        db.add(AssetTag(asset_id=ids[5], tag_id=tag.id, source="user"))
        db.add(AssetTag(asset_id=ids[6], tag_id=tag.id, source="auto", removed=True))
        db.commit()
        group_id = group.id

    query = _rand(rng)
    with db_session_factory() as db:
        hits = world.index.top_k(
            db, MODEL, query, AssetFilter(viewer=LOCAL_ADMIN, group_id=group_id), 10
        )
        assert [h.asset_id for h in hits] == world.expected(query, ids[:3], 10)
        hits = world.index.top_k(db, MODEL, query, AssetFilter(viewer=LOCAL_ADMIN, tag="Cat"), 10)
        assert [h.asset_id for h in hits] == [ids[5]]


def test_neighbors_and_subset(db_session_factory: sessionmaker, backend: str) -> None:
    rng = np.random.default_rng(3)
    world = _World(db_session_factory, backend)
    base = _rand(rng)
    a = world.asset(base)
    b = world.asset(base + 0.05 * _rand(rng))
    others = [world.asset(_rand(rng)) for _ in range(5)]
    missing = world.asset(None)
    world.finish()
    with db_session_factory() as db:
        hits = neighbors(world.index, db, MODEL, a, AssetFilter(viewer=LOCAL_ADMIN), 3)
        assert hits is not None
        assert hits[0].asset_id == b
        assert a not in {h.asset_id for h in hits}
        assert (
            neighbors(world.index, db, MODEL, missing, AssetFilter(viewer=LOCAL_ADMIN), 3) is None
        )
        ordered, matrix = world.index.subset(db, MODEL, [others[1], missing, a])
        assert ordered == [others[1], a]
        assert matrix.shape == (2, DIM)
        np.testing.assert_allclose(matrix[1], world.vectors[a], atol=1e-6)


def test_numpy_index_reloads_when_version_changes(db_session_factory: sessionmaker) -> None:
    rng = np.random.default_rng(4)
    world = _World(db_session_factory, "numpy")
    first = world.asset(_rand(rng))
    world.finish()
    index = world.index
    assert isinstance(index, NumpyIndex)
    flt = AssetFilter(viewer=LOCAL_ADMIN)
    with db_session_factory() as db:
        assert [h.asset_id for h in index.top_k(db, MODEL, _rand(rng), flt, 10)] == [first]
        assert index.loads == 1
        # 版が同じ間は読み直さない(書き込まれても見えない)。
        second = world.asset(_rand(rng))
        assert len(index.top_k(db, MODEL, _rand(rng), flt, 10)) == 1
        assert index.loads == 1
        # 版が上がったら読み直す。
        world.versions.bump()
        assert {h.asset_id for h in index.top_k(db, MODEL, _rand(rng), flt, 10)} == {
            first,
            second,
        }
        assert index.loads == 2


def test_knn_matrix_self_first() -> None:
    rng = np.random.default_rng(5)
    matrix = l2_normalize(rng.standard_normal((7, DIM)))
    indices, sims = knn_matrix(matrix, 3)
    assert indices.shape == sims.shape == (7, 4)
    full = matrix @ matrix.T
    for row in range(7):
        assert indices[row, 0] == row
        assert sims[row, 0] == 1.0
        scores = full[row].copy()
        scores[row] = -np.inf
        expected = list(np.argsort(-scores)[:3])
        assert list(indices[row, 1:]) == expected
        assert list(sims[row, 1:]) == pytest.approx([full[row, j] for j in expected], abs=1e-6)
    # k がノード数以上なら、自分自身 + ほか全部。
    indices, _ = knn_matrix(matrix[:3], 10)
    assert indices.shape == (3, 3)
    indices, _ = knn_matrix(matrix[:1], 10)
    assert indices.tolist() == [[0]]


def test_pairs_and_union_find() -> None:
    base = np.zeros((5, 3), dtype=np.float32)
    base[0] = [1, 0, 0]
    base[1] = [0.99, 0.14, 0]  # 0 と近い
    base[2] = [0.96, 0.28, 0]  # 1 と近い(0 とはしきい値未満)
    base[3] = [0, 1, 0]
    base[4] = [0, 0, 1]
    matrix = l2_normalize(base)
    pairs = pairs_at_least(matrix, 0.985)
    assert {(i, j) for i, j, _ in pairs} == {(0, 1), (1, 2)}
    # 連鎖(0-1、1-2)は1つのグループになる。
    assert group_pairs(5, pairs) == [[0, 1, 2]]
    assert group_pairs(5, []) == []


def test_pgvector_query_uses_partial_hnsw_index(db_session_factory: sessionmaker) -> None:
    """部分 HNSW 索引が使われる(式 `embedding::vector(<次元>)` と条件 `model_key = '<リテラル>'`
    が索引と一致する)。行が少ないと並べ替えの方が安く見積もられるので、実際に近い件数
    (2000 件)を入れ、計画の設定は変えずに確かめる。"""
    if not _pgvector_available(db_session_factory):
        pytest.skip("pgvector の PostgreSQL が無い")
    rng = np.random.default_rng(0)
    world = _World(db_session_factory, "pgvector")
    engine = db_session_factory.kw["bind"]
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    assets, embeddings = [], []
    for i in range(2000):
        asset_id = uuid.uuid4()
        values = l2_normalize(_rand(rng))[0]
        assets.append({"id": asset_id, "sha": f"{i:064x}", "t": t0 + timedelta(seconds=i)})
        embeddings.append(
            {
                "id": asset_id,
                "b": vector_to_blob(values),
                "e": embedding_index.vector_literal(values),
            }
        )
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO asset (id, kind, sha256, blob_key, mime, width, height, bytes, "
                "created_at) VALUES (:id, 'upload', :sha, 'k', 'image/png', 1, 1, 1, :t)"
            ),
            assets,
        )
        conn.execute(
            text(
                "INSERT INTO asset_embedding (asset_id, model_key, status, dim, vector, "
                f"updated_at, embedding) VALUES (:id, '{MODEL}', 'succeeded', {DIM}, :b, now(), "
                "CAST(:e AS vector))"
            ),
            embeddings,
        )
        conn.execute(text("ANALYZE asset"))
        conn.execute(text("ANALYZE asset_embedding"))
    world.finish()

    index = world.index
    assert isinstance(index, PgvectorIndex)
    statement = index.query_statement(MODEL, DIM, _rand(rng), AssetFilter(viewer=LOCAL_ADMIN), 20)
    with db_session_factory() as db:
        sql = str(statement.compile(dialect=engine.dialect, compile_kwargs={"literal_binds": True}))
        index.prepare_scan(db, 20)
        plan = "\n".join(row[0] for row in db.connection().exec_driver_sql("EXPLAIN " + sql).all())
        # 結果も正しく取れる(近似でも、この件数なら厳密な上位と一致する)。
        hits = index.top_k(
            db, MODEL, _rand(np.random.default_rng(9)), AssetFilter(viewer=LOCAL_ADMIN), 5
        )
    print(plan)
    assert f"Index Scan using {embedding_index.hnsw_index_name(MODEL)}" in plan, plan
    assert len(hits) == 5


# -- 調停の優先の区間(文章での検索) ------------------------------------------------


def test_priority_use_evicts_idle_holder_immediately() -> None:
    clock = {"now": 100.0}
    dropped: list[str] = []
    residency = ModelResidency(grace_seconds=2.0, max_hold_seconds=30.0, clock=lambda: clock["now"])
    residency.register("wd", lambda: dropped.append("wd"))
    residency.register("embedding", lambda: dropped.append("embedding"))
    with residency.use("wd"):
        pass
    clock["now"] = 100.5  # grace(2 秒)も max_hold(30 秒)も過ぎていない
    started = time.monotonic()
    with residency.use("embedding", priority=True):
        assert residency.holder == "embedding"
    assert time.monotonic() - started < 0.2
    assert dropped == ["wd"]


def test_priority_use_waits_only_for_in_flight_inference() -> None:
    residency = ModelResidency(grace_seconds=60.0, max_hold_seconds=600.0)
    residency.register("wd", lambda: None)
    residency.register("embedding", lambda: None)
    in_flight = threading.Event()
    release = threading.Event()
    order: list[str] = []

    def wd_worker() -> None:
        # 一括実行のように、推論を続けて何回も行う。
        for i in range(3):
            with residency.use("wd"):
                if i == 0:
                    in_flight.set()
                    release.wait(5)
                order.append(f"wd{i}")

    thread = threading.Thread(target=wd_worker)
    thread.start()
    assert in_flight.wait(5)

    def search() -> None:
        with residency.use("embedding", priority=True):
            order.append("search")

    searcher = threading.Thread(target=search)
    searcher.start()
    time.sleep(0.3)
    assert "search" not in order  # 推論の最中は待つ
    release.set()
    searcher.join(5)
    thread.join(5)
    # WD の推論1回分だけ待ち、次の WD の推論より先に入る(max_hold の 600 秒は待たない)。
    assert order[:2] == ["wd0", "search"]
