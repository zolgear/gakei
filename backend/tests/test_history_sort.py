"""履歴の並べ替え(ADR-0054)。グループの並び、似た画像の順(計算と API)。"""

from __future__ import annotations

import time
import uuid
from typing import Any

import numpy as np
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.history_sort import embedding_order
from app.domain.models import AssetEmbedding
from app.embedding.catalog import XENOVA_CLIP_REVISION
from tests.conftest import login_as, wait_for_run_terminal

ACTIVE_KEY = f"fake:onnx:clip-vit-b32-u8@{XENOVA_CLIP_REVISION}"


# -- 似た画像の順の計算 ---------------------------------------------------------------


def _clusters(sizes: list[int], dim: int = 16, seed: int = 1) -> tuple[np.ndarray, list[int]]:
    """よく離れたクラスタのベクトルと、各行のクラスタ番号。行はクラスタが混ざる順に並べる。"""
    rng = np.random.default_rng(seed)
    centers = np.eye(dim, dtype=np.float32)[: len(sizes)] * 10
    rows: list[np.ndarray] = []
    labels: list[int] = []
    for c, size in enumerate(sizes):
        for _ in range(size):
            rows.append(centers[c] + rng.normal(0, 0.3, dim).astype(np.float32))
            labels.append(c)
    perm = rng.permutation(len(rows))
    return np.stack([rows[i] for i in perm]), [labels[i] for i in perm]


def test_embedding_order_is_a_permutation_starting_at_start() -> None:
    matrix, _labels = _clusters([7, 9, 8])
    order = embedding_order(matrix, start=3)
    assert sorted(order) == list(range(len(matrix)))
    assert order[0] == 3


def test_embedding_order_keeps_clusters_together() -> None:
    matrix, labels = _clusters([10, 12, 9, 11])
    order = embedding_order(matrix)
    sequence = [labels[i] for i in order]
    # 似たもの同士が並ぶので、クラスタが変わる回数はクラスタの数 - 1 だけ。
    changes = sum(1 for a, b in zip(sequence, sequence[1:], strict=False) if a != b)
    assert changes == 3


def test_embedding_order_is_deterministic() -> None:
    matrix, _labels = _clusters([15, 15, 15], seed=5)
    assert embedding_order(matrix) == embedding_order(matrix)


def test_embedding_order_small_inputs() -> None:
    assert embedding_order(np.zeros((0, 4), dtype=np.float32)) == []
    assert embedding_order(np.ones((1, 4), dtype=np.float32)) == [0]
    assert embedding_order(np.ones((2, 4), dtype=np.float32)) == [0, 1]
    # 3 枚(k = 2)でも全部が1回ずつ出る。
    three = np.asarray([[1, 0], [0, 1], [1, 0.1]], dtype=np.float32)
    order = embedding_order(three)
    assert sorted(order) == [0, 1, 2]
    assert order[0] == 0 and order[1] == 2


# -- API の共通 ---------------------------------------------------------------------


def _run(client: TestClient, prompt: str, *, n: int = 1, group: str | None = None) -> dict:
    body: dict[str, Any] = {
        "operation": "generate",
        "model": "gpt-image-2.5-sunburst",
        "prompt": prompt,
        "params": {"n": n},
    }
    if group is not None:
        body["asset_group_id"] = group
    response = client.post("/api/runs", json=body)
    assert response.status_code == 202, response.text
    return wait_for_run_terminal(client, response.json()["id"])


def _group(client: TestClient, name: str) -> str:
    response = client.post("/api/asset-groups", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _all_pages(client: TestClient, sort: str, limit: int) -> tuple[list[dict], dict]:
    items: list[dict] = []
    cursor: str | None = None
    first: dict = {}
    while True:
        params: dict[str, Any] = {"sort": sort, "limit": limit}
        if cursor:
            params["cursor"] = cursor
        response = client.get("/api/history/assets", params=params)
        assert response.status_code == 200, response.text
        body = response.json()
        first = first or body
        items.extend(body["items"])
        cursor = body["next_cursor"]
        if not cursor:
            return items, first


def _output_ids(run: dict) -> list[str]:
    return [o["asset_id"] for o in sorted(run["outputs"], key=lambda o: o["output_index"])]


# -- グループの並び -----------------------------------------------------------------


def test_group_sort_orders_groups_then_oldest_first(client: TestClient) -> None:
    older = _group(client, "Older")
    newer = _group(client, "Newer")  # 新しいグループは先頭(ADR-0022 2章)
    plain1 = _run(client, "plain 1")
    a1 = _run(client, "older 1", n=2, group=older)
    b1 = _run(client, "newer 1", group=newer)
    a2 = _run(client, "older 2", group=older)
    plain2 = _run(client, "plain 2")
    failed = _run(client, "x [[fail:contentFilter]]", group=older)
    assert failed["status"] == "failed"

    expected = (
        _output_ids(b1)
        + _output_ids(a1)
        + _output_ids(a2)
        + _output_ids(plain1)
        + _output_ids(plain2)
    )
    for limit in (1, 2, 50):
        items, first = _all_pages(client, "group", limit)
        assert [i["asset_id"] for i in items] == expected
    assert [i["group_id"] for i in items] == [newer] + [older] * 3 + [None] * 2
    # 画像の無い Run(失敗)は出さない。項目には Run の要約が付く。
    assert all(i["run"]["status"] == "succeeded" for i in items)
    assert items[1]["run"]["id"] == a1["id"]
    assert first["groups"] == [
        {"id": newer, "name": "Newer", "count": 1},
        {"id": older, "name": "Older", "count": 3},
        {"id": None, "name": None, "count": 2},
    ]


def test_group_sort_follows_group_order_and_deleted_groups(client: TestClient) -> None:
    first = _group(client, "First")
    second = _group(client, "Second")
    r1 = _run(client, "one", group=first)
    r2 = _run(client, "two", group=second)
    # 並べ替え: First を先頭に。
    response = client.put("/api/asset-groups/order", json={"group_ids": [first, second]})
    assert response.status_code == 200, response.text
    items, body = _all_pages(client, "group", 50)
    assert [i["asset_id"] for i in items] == _output_ids(r1) + _output_ids(r2)

    # 削除したグループの画像は「グループなし」に回る。
    assert client.delete(f"/api/asset-groups/{first}").status_code == 204
    items, body = _all_pages(client, "group", 50)
    assert [i["asset_id"] for i in items] == _output_ids(r2) + _output_ids(r1)
    assert [g["id"] for g in body["groups"]] == [second, None]


def test_group_sort_rejects_bad_cursor(client: TestClient) -> None:
    response = client.get("/api/history/assets", params={"sort": "group", "cursor": "!!"})
    assert response.status_code == 400
    response = client.get("/api/history/assets", params={"sort": "bogus"})
    assert response.status_code == 422


def test_group_sort_oidc_shows_only_own(client_oidc: TestClient) -> None:
    login_as(client_oidc, "alice@example.com")
    alice_group = _group(client_oidc, "Alice")
    alice = _run(client_oidc, "alice", group=alice_group)
    client_oidc.cookies.clear()

    login_as(client_oidc, "bob@example.com")
    bob = _run(client_oidc, "bob")
    items, body = _all_pages(client_oidc, "group", 50)
    assert [i["asset_id"] for i in items] == _output_ids(bob)
    assert body["groups"] == [{"id": None, "name": None, "count": 1}]
    assert not set(_output_ids(alice)) & {i["asset_id"] for i in items}


# -- 似た画像の順 -------------------------------------------------------------------


def _enable_embeddings(client: TestClient) -> None:
    response = client.patch("/api/settings/embeddings", json={"enabled": True})
    assert response.status_code == 200, response.text


def _wait_embedded(client: TestClient, asset_ids: list[str]) -> None:
    deadline = time.monotonic() + 15
    pending = {uuid.UUID(a) for a in asset_ids}
    while time.monotonic() < deadline:
        with client.app.state.session_factory() as db:
            rows = db.execute(
                select(AssetEmbedding.asset_id).where(
                    AssetEmbedding.model_key == ACTIVE_KEY,
                    AssetEmbedding.status == "succeeded",
                    AssetEmbedding.asset_id.in_(pending),
                )
            ).all()
        pending -= {r[0] for r in rows}
        if not pending:
            return
        time.sleep(0.05)
    raise TimeoutError(f"埋め込みが終わりませんでした: {pending}")


def test_embedding_sort_requires_embeddings(client: TestClient) -> None:
    response = client.get("/api/history/assets", params={"sort": "embedding"})
    assert response.status_code == 409


def test_embedding_sort_lists_every_image_once(client: TestClient) -> None:
    before = _run(client, "before enabling")  # 有効にする前の画像はベクトルが無い
    _enable_embeddings(client)
    runs = [_run(client, f"image {i}", n=2 if i % 2 else 1) for i in range(5)]
    failed = _run(client, "x [[fail:contentFilter]]")
    assert failed["status"] == "failed"
    embedded = [a for r in runs for a in _output_ids(r)]
    _wait_embedded(client, embedded)

    items, body = _all_pages(client, "embedding", 50)
    ids = [i["asset_id"] for i in items]
    assert sorted(ids) == sorted(embedded + _output_ids(before))
    # 埋め込みの無い画像は最後。
    assert ids[-1] == _output_ids(before)[0]
    assert body["groups"] is None
    assert all(i["run"]["status"] == "succeeded" for i in items)

    # ページを分けても同じ並び(キャッシュが効く)。
    paged, _ = _all_pages(client, "embedding", 2)
    assert [i["asset_id"] for i in paged] == ids

    # 画像を消すと並びから外れる(作り直す)。
    deleted = runs[0]["id"]
    assert client.delete(f"/api/runs/{deleted}").status_code == 204
    items, _ = _all_pages(client, "embedding", 50)
    assert not set(_output_ids(runs[0])) & {i["asset_id"] for i in items}
