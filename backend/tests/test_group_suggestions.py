"""グループの提案(ADR-0056)。集め方、特徴的なタグ、作品、既存のグループとの重なり、
キャッシュ、可視性、受け入れ。

API のテストは、FAKE の埋め込みが付けたベクトルを、合成の(よく離れた集まりの)ベクトルで
上書きし、`Embedder.bump` で版を上げてから提案を取る。ベクトルの正本は BLOB なので、
pgvector のある PostgreSQL でも同じに動く(`subset` は BLOB を読む)。
"""

from __future__ import annotations

import io
import time
import uuid
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from app.domain import annotations as annotations_domain
from app.domain import group_suggestions
from app.domain.group_suggestions import (
    cluster_vectors,
    distinctive_tags,
    franchise_of_tag,
)
from app.domain.models import Asset, AssetEmbedding
from app.embedding.base import vector_to_blob
from app.embedding.catalog import XENOVA_CLIP_REVISION
from app.embedding.fake import FAKE_DIM
from tests.conftest import login_as

ACTIVE_KEY = f"fake:onnx:clip-vit-b32-u8@{XENOVA_CLIP_REVISION}"


# -- 合成のベクトル -------------------------------------------------------------------


def _blobs(sizes: list[int], outliers: int = 0, dim: int = 32, seed: int = 1) -> np.ndarray:
    """よく離れた集まり(各軸の近く)と、どの集まりからも遠い外れ値。行は集まりの順。"""
    rng = np.random.default_rng(seed)
    rows: list[np.ndarray] = []
    for c, size in enumerate(sizes):
        center = np.zeros(dim, dtype=np.float32)
        center[c] = 1.0
        for _ in range(size):
            rows.append(center + rng.normal(0, 0.05, dim).astype(np.float32))
    for i in range(outliers):
        # 集まりに使っていない軸を2本ずつ混ぜる(どの集まりとも、互いとも遠い)。
        v = np.zeros(dim, dtype=np.float32)
        v[len(sizes) + 2 * i] = 1.0
        v[len(sizes) + 2 * i + 1] = 0.7
        rows.append(v)
    return np.stack(rows)


def test_cluster_vectors_separates_blobs_and_keeps_outliers() -> None:
    matrix = _blobs([12, 8, 6], outliers=3)
    result = cluster_vectors(matrix)
    assert [sorted(c) for c in result.clusters] == [
        list(range(0, 12)),
        list(range(12, 20)),
        list(range(20, 26)),
    ]
    assert sorted(result.outliers) == [26, 27, 28]


def test_cluster_vectors_orders_members_by_centroid() -> None:
    matrix = _blobs([10, 10])
    # 1枚だけ中心から少し離す。
    matrix[3] += np.eye(32, dtype=np.float32)[5] * 0.3
    result = cluster_vectors(matrix)
    first = next(c for c in result.clusters if 3 in c)
    assert first[-1] == 3


def test_cluster_vectors_small_inputs() -> None:
    assert cluster_vectors(np.zeros((0, 4), dtype=np.float32)).clusters == ()
    few = cluster_vectors(_blobs([3]))
    assert few.clusters == () and sorted(few.outliers) == [0, 1, 2]


def test_cluster_vectors_pca_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """件数が多いときの PCA + ユークリッドでも、同じように分かれる。"""
    monkeypatch.setattr(group_suggestions, "PCA_THRESHOLD", 10)
    monkeypatch.setattr(group_suggestions, "PCA_DIM", 8)
    matrix = _blobs([12, 8, 6], outliers=2)
    result = cluster_vectors(matrix)
    assert [sorted(c) for c in result.clusters] == [
        list(range(0, 12)),
        list(range(12, 20)),
        list(range(20, 26)),
    ]


def test_fine_granularity_splits_large_cluster() -> None:
    """全体の 20% を超える集まりの中に2つのまとまりがあれば、細かく分けると割れる。"""
    rng = np.random.default_rng(3)
    dim = 32
    base = np.zeros(dim, dtype=np.float32)
    base[0] = 1.0
    rows = []
    for sub in (1, 2):
        for _ in range(10):
            v = base.copy()
            v[sub] = 0.6
            rows.append(v + rng.normal(0, 0.02, dim).astype(np.float32))
    other = np.zeros(dim, dtype=np.float32)
    other[10] = 1.0
    rows.extend(other + rng.normal(0, 0.02, dim).astype(np.float32) for _ in range(6))
    matrix = np.stack(rows)
    normal = cluster_vectors(matrix, granularity="normal")
    fine = cluster_vectors(matrix, granularity="fine")
    assert len(fine.clusters) >= len(normal.clusters)
    sets = [set(c) for c in fine.clusters]
    assert set(range(0, 10)) in sets
    assert set(range(10, 20)) in sets
    assert set(range(20, 26)) in sets


# -- 特徴的なタグと作品 --------------------------------------------------------------


def test_distinctive_tags_drops_wide_tags() -> None:
    total = 40
    # 集まり 10 枚。1girl は全体に広い、red hair は集まりに偏る、smile は少ない。
    cluster = {"1girl": 10, "red hair": 9, "smile": 2, "rare": 3}
    global_counts = {"1girl": 38, "red hair": 10, "smile": 12, "rare": 30}
    scores = distinctive_tags(cluster, 10, global_counts, total)
    tags = [s.tag for s in scores]
    assert tags[0] == "red hair"
    assert "1girl" not in tags  # 全体の出現率が 50% を超える
    assert "smile" not in tags  # 集まりでの出現率が 25% 未満
    assert "rare" not in tags  # 全体の出現率が 50% を超える
    assert scores[0].rate == pytest.approx(0.9)
    assert scores[0].global_rate == pytest.approx(0.25)


def test_distinctive_tags_requires_bias() -> None:
    # 集まりと全体で同じ割合なら偏っていない(z が小さい)。
    scores = distinctive_tags({"hat": 3}, 10, {"hat": 12}, 40)
    assert scores == []


def test_franchise_of_tag() -> None:
    assert franchise_of_tag("aru (blue archive)", None, False) == "blue archive"
    assert franchise_of_tag("aru (blue archive)", 4, True) == "blue archive"
    assert franchise_of_tag("blue archive", 3, True) == "blue archive"
    assert franchise_of_tag("bow (weapon)", 0, True) is None
    assert franchise_of_tag("saber (cosplay)", None, False) is None
    # 辞書に無いタグの、一般の括弧書きは作品にしない。辞書でキャラクターと分かれば数える。
    assert franchise_of_tag("star (sky)", None, False) is None
    assert franchise_of_tag("star (sky)", 4, True) == "sky"
    assert franchise_of_tag("smile", None, False) is None


# -- API の共通 ---------------------------------------------------------------------


def _png(seed: int) -> bytes:
    rng = np.random.default_rng(seed)
    pixels = rng.integers(0, 255, (16, 16, 3), dtype=np.uint8)
    buffer = io.BytesIO()
    Image.fromarray(pixels, "RGB").save(buffer, format="PNG")
    return buffer.getvalue()


_SEED = iter(range(10_000, 1_000_000))


def _upload(client: TestClient) -> str:
    response = client.post(
        "/api/assets",
        files={"file": ("a.png", _png(next(_SEED)), "image/png")},
        data={"kind": "upload"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _enable(client: TestClient) -> None:
    response = client.patch("/api/settings/embeddings", json={"enabled": True})
    assert response.status_code == 200, response.text


def _wait_embedded(client: TestClient, asset_ids: list[str]) -> None:
    deadline = time.monotonic() + 20
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


def _set_vectors(client: TestClient, asset_ids: list[str], matrix: np.ndarray) -> None:
    """FAKE のベクトルを合成のベクトルで上書きし、版を上げる。"""
    padded = np.zeros((len(asset_ids), FAKE_DIM), dtype=np.float32)
    padded[:, : matrix.shape[1]] = matrix
    padded /= np.linalg.norm(padded, axis=1, keepdims=True)
    with client.app.state.session_factory() as db:
        for asset_id, vector in zip(asset_ids, padded, strict=True):
            row = db.get(AssetEmbedding, (uuid.UUID(asset_id), ACTIVE_KEY))
            assert row is not None
            row.vector = vector_to_blob(vector)
            row.dim = FAKE_DIM
        db.commit()
    client.app.state.embedder.bump(ACTIVE_KEY)


def _add_tag(client: TestClient, asset_ids: list[str], name: str) -> None:
    with client.app.state.session_factory() as db:
        for asset_id in asset_ids:
            asset = db.get(Asset, uuid.UUID(asset_id))
            assert asset is not None
            annotations_domain.add_tag(db, asset, name)
        db.commit()


def _suggestions(client: TestClient, **params: Any) -> dict:
    response = client.get("/api/asset-groups/suggestions", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _group(client: TestClient, name: str, asset_ids: list[str]) -> str:
    response = client.post("/api/asset-groups", json={"name": name})
    assert response.status_code == 201, response.text
    group_id = response.json()["id"]
    if asset_ids:
        added = client.post(f"/api/asset-groups/{group_id}/assets", json={"asset_ids": asset_ids})
        assert added.status_code == 200, added.text
    return group_id


def _setup(client: TestClient, sizes: list[int], outliers: int = 0) -> list[list[str]]:
    """集まりごとの Asset の id(最後の要素は外れ値)。"""
    _enable(client)
    groups = [[_upload(client) for _ in range(size)] for size in sizes]
    lonely = [_upload(client) for _ in range(outliers)]
    ids = [a for g in groups for a in g] + lonely
    _wait_embedded(client, ids)
    _set_vectors(client, ids, _blobs(sizes, outliers=outliers))
    return [*groups, lonely]


# -- API ----------------------------------------------------------------------------


def test_suggestions_require_embeddings(client: TestClient) -> None:
    response = client.get("/api/asset-groups/suggestions")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "embeddings_unavailable"


def test_suggestions_new_groups_target_and_outliers(client: TestClient) -> None:
    first, second, third, lonely = _setup(client, [8, 6, 6], outliers=2)
    # 1つ目の集まりの 5 枚(50% 以上)が既存のグループに入っている。
    existing = _group(client, "Existing", first[:5])
    _add_tag(client, second, "red hair")
    _add_tag(client, second + third + first, "1girl")
    _add_tag(client, second[:4], "aru (blue archive)")

    body = _suggestions(client)
    assert body["scope"] == "ungrouped" and body["granularity"] == "normal"
    assert body["scanned"] == 22 and body["truncated"] is False
    by_members = {frozenset(s["member_ids"]): s for s in body["suggestions"]}
    assert set(by_members) == {frozenset(first[5:]), frozenset(second), frozenset(third)}

    target = by_members[frozenset(first[5:])]
    assert target["target_group"]["id"] == existing
    assert target["target_group"]["name"] == "Existing"
    assert target["target_group"]["overlap"] == pytest.approx(5 / 8, abs=1e-3)
    assert target["size"] == 3 and target["moving_count"] == 0

    red = by_members[frozenset(second)]
    assert red["target_group"] is None and red["name"] is None
    assert red["cover_ids"] == red["member_ids"][:12]
    tags = [t["tag"] for t in red["top_tags"]]
    assert "red hair" in tags and "1girl" not in tags
    assert red["top_tags"][tags.index("red hair")]["label"] == "red hair"
    assert red["franchise"] == {
        "name": "blue archive",
        "label": None,
        "rate": pytest.approx(4 / 6, abs=1e-3),
    }

    assert sorted(body["outlier_ids"]) == sorted(lonely)
    shown = {a["id"] for a in body["assets"]}
    assert shown == set(first[5:] + second + third + lonely)
    # 同じ入力なら同じ id。
    again = _suggestions(client)
    assert [s["id"] for s in again["suggestions"]] == [s["id"] for s in body["suggestions"]]


def test_suggestions_scope_all_includes_grouped(client: TestClient) -> None:
    first, second, _lonely = _setup(client, [8, 8])
    # 2つ目の集まりの 2 枚(50% 未満)が別のグループに入っている。
    _group(client, "Other", second[:2])
    ungrouped = _suggestions(client)
    by_members = {frozenset(s["member_ids"]): s for s in ungrouped["suggestions"]}
    assert set(by_members) == {frozenset(first), frozenset(second[2:])}

    everything = _suggestions(client, scope="all")
    by_members = {frozenset(s["member_ids"]): s for s in everything["suggestions"]}
    assert set(by_members) == {frozenset(first), frozenset(second)}
    assert by_members[frozenset(second)]["moving_count"] == 2
    assert sorted(by_members[frozenset(second)]["moving_ids"]) == sorted(second[:2])
    assert by_members[frozenset(second)]["target_group"] is None


def test_suggestions_drop_small_remainder_into_outliers(client: TestClient) -> None:
    """大半が複数のグループに分かれて入っている集まりの残り数枚は、新しいグループにしない。"""
    (cluster, _lonely) = _setup(client, [10])
    _group(client, "A", cluster[:4])
    _group(client, "B", cluster[4:8])
    body = _suggestions(client)
    assert body["suggestions"] == []
    assert sorted(body["outlier_ids"]) == sorted(cluster[8:])


def test_suggestions_cache(client: TestClient) -> None:
    first, second, _lonely = _setup(client, [6, 6])
    cache = client.app.state.group_suggestion_cache
    _suggestions(client)
    builds = cache.builds
    # 範囲を変えても、グループを変えても、集め直さない。
    _suggestions(client, scope="all")
    _group(client, "G", first)
    body = _suggestions(client)
    assert cache.builds == builds
    assert [set(s["member_ids"]) for s in body["suggestions"]] == [set(second)]
    # 粒度が違えば別の鍵。
    _suggestions(client, granularity="fine")
    assert cache.builds == builds + 1
    # 埋め込みの版が上がれば集め直す。
    client.app.state.embedder.bump(ACTIVE_KEY)
    _suggestions(client)
    assert cache.builds == builds + 2
    # 画像が減れば鍵が変わる。
    assert client.delete(f"/api/assets/{second[0]}").status_code == 204
    _suggestions(client)
    assert cache.builds == builds + 3


def test_accept_creates_group_and_moves_into_target(client: TestClient) -> None:
    first, second, _lonely = _setup(client, [8, 6])
    existing = _group(client, "Existing", first[:5])
    body = _suggestions(client)
    new = next(s for s in body["suggestions"] if s["target_group"] is None)
    add = next(s for s in body["suggestions"] if s["target_group"] is not None)

    # 1枚を外して受け入れる。
    chosen = new["member_ids"][1:]
    response = client.post(
        "/api/asset-groups/suggestions/accept", json={"name": "  Picked ", "asset_ids": chosen}
    )
    assert response.status_code == 200, response.text
    created = response.json()
    assert created["name"] == "Picked" and created["member_count"] == len(chosen)
    listing = client.get("/api/asset-groups").json()["items"]
    assert listing[0]["id"] == created["id"]  # 新しいグループは先頭

    response = client.post(
        "/api/asset-groups/suggestions/accept",
        json={"target_group_id": existing, "asset_ids": add["member_ids"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["member_count"] == 8

    # 外した1枚は、作ったグループへの追加の提案になる(集まりの大半がそのグループに入った)。
    after = _suggestions(client)
    assert [(s["target_group"]["id"], s["member_ids"]) for s in after["suggestions"]] == [
        (created["id"], [new["member_ids"][0]])
    ]
    assert after["outlier_ids"] == []


def test_accept_validation(client: TestClient) -> None:
    (cluster, _lonely) = _setup(client, [5])
    url = "/api/asset-groups/suggestions/accept"
    assert client.post(url, json={"asset_ids": cluster}).status_code == 422
    group = _group(client, "G", [])
    both = {"name": "x", "target_group_id": group, "asset_ids": cluster}
    assert client.post(url, json=both).status_code == 422
    assert client.post(url, json={"name": "x", "asset_ids": []}).status_code == 422
    missing = {"target_group_id": str(uuid.uuid4()), "asset_ids": cluster}
    assert client.post(url, json=missing).status_code == 404
    unknown = {"name": "x", "asset_ids": [*cluster, str(uuid.uuid4())]}
    assert client.post(url, json=unknown).status_code == 404
    # 全体を拒んだので、グループは作られていない。
    names = [g["name"] for g in client.get("/api/asset-groups").json()["items"]]
    assert names == ["G"]


def test_suggestions_oidc_other_users_never_appear(client_oidc: TestClient) -> None:
    login_as(client_oidc, "admin@example.com")
    _enable(client_oidc)
    client_oidc.cookies.clear()

    login_as(client_oidc, "alice@example.com")
    alice = [_upload(client_oidc) for _ in range(6)]
    alice_group = client_oidc.post("/api/asset-groups", json={"name": "Alice"}).json()["id"]
    client_oidc.cookies.clear()

    login_as(client_oidc, "bob@example.com")
    bob = [_upload(client_oidc) for _ in range(6)]
    bob_other = [_upload(client_oidc) for _ in range(6)]
    _wait_embedded(client_oidc, alice + bob + bob_other)
    # Alice の画像と Bob の 6 枚を同じ集まりにする。
    _set_vectors(client_oidc, alice + bob + bob_other, _blobs([12, 6]))

    body = _suggestions(client_oidc)
    assert body["scanned"] == 12
    assert sorted(sorted(s["member_ids"]) for s in body["suggestions"]) == sorted(
        [sorted(bob), sorted(bob_other)]
    )
    shown = {a["id"] for a in body["assets"]} | set(body["outlier_ids"])
    assert not shown & set(alice)

    url = "/api/asset-groups/suggestions/accept"
    others = client_oidc.post(url, json={"name": "x", "asset_ids": alice})
    assert others.status_code == 404
    into_alice = client_oidc.post(url, json={"target_group_id": alice_group, "asset_ids": bob})
    assert into_alice.status_code == 404


def test_suggestions_use_tag_dictionary(client: TestClient) -> None:
    """タグ辞書の訳で表示し、作品のカテゴリーのタグを作品として数える(ADR-0056 5章)。"""
    from app.domain.models import TagDictionary, TagDictionaryEntry, TagDictionaryTranslation

    first, *others, _lonely = _setup(client, [6, 6, 6, 6])
    _add_tag(client, first, "red hair")
    _add_tag(client, first[:5], "some series")
    for i, other in enumerate(others):
        _add_tag(client, first + other, "1girl")
        _add_tag(client, other, f"other {i}")
        _add_tag(client, other, f"another {i}")
    with client.app.state.session_factory() as db:
        tags = TagDictionary(
            filename="tags.csv", kind="tags", category_scheme="danbooru", status="ready"
        )
        words = TagDictionary(filename="ja.csv", kind="translations", status="ready")
        db.add_all([tags, words])
        db.flush()
        db.add_all(
            [
                TagDictionaryEntry(
                    dictionary_id=tags.id, name_key="some_series", name="some_series", category=3
                ),
                TagDictionaryEntry(
                    dictionary_id=tags.id, name_key="red_hair", name="red_hair", category=0
                ),
                TagDictionaryTranslation(
                    dictionary_id=words.id,
                    name_key="red_hair",
                    name="red_hair",
                    translation="赤髪",
                    translations="赤髪",
                    search_key=",赤髪,",
                ),
                TagDictionaryTranslation(
                    dictionary_id=words.id,
                    name_key="some_series",
                    name="some_series",
                    translation="あるシリーズ",
                    translations="あるシリーズ",
                    search_key=",あるシリーズ,",
                ),
            ]
        )
        db.commit()

    body = _suggestions(client)
    red = next(s for s in body["suggestions"] if set(s["member_ids"]) == set(first))
    labels = {t["tag"]: t["label"] for t in red["top_tags"]}
    assert labels["red hair"] == "赤髪"
    assert red["franchise"]["name"] == "some series"
    assert red["franchise"]["label"] == "あるシリーズ"
    assert red["franchise"]["rate"] == pytest.approx(5 / 6, abs=1e-3)
