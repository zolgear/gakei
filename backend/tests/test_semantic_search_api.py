"""埋め込みでの検索の API と MCP(ADR-0033 6章・7章、ADR-0023 10章)。

FAKE のアプリでは埋め込みのエンジンもダミー(`FakeEmbeddingEngine`)で、似た画像は似た
ベクトルになる。検索の文章のベクトルは乱数なので、決まった結果を確かめるときは
`query_vector_cache` に画像のベクトルを入れておく。
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

from app.domain.models import AssetEmbedding
from app.embedding.base import blob_to_vector
from app.embedding.catalog import XENOVA_CLIP_REVISION
from tests.conftest import login_as, wait_for_run_terminal

ACTIVE_KEY = f"fake:onnx:clip-vit-b32-u8@{XENOVA_CLIP_REVISION}"
_MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "MCP-Protocol-Version": "2025-11-25",
}


def _png(color: tuple[int, int, int], split: tuple[int, int, int] | None = None) -> bytes:
    """左右で色を変えた画像(split が None なら単色)。"""
    image = Image.new("RGB", (64, 64), color)
    if split is not None:
        image.paste(Image.new("RGB", (32, 64), split), (32, 0))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _upload(client: TestClient, data: bytes) -> str:
    response = client.post(
        "/api/assets", files={"file": ("a.png", data, "image/png")}, data={"kind": "upload"}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _enable(client: TestClient, **values: Any) -> None:
    response = client.patch("/api/settings/embeddings", json={"enabled": True, **values})
    assert response.status_code == 200, response.text


def _wait_embedded(client: TestClient, *asset_ids: str) -> None:
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


def _vector(client: TestClient, asset_id: str) -> np.ndarray:
    with client.app.state.session_factory() as db:
        row = db.get(AssetEmbedding, (uuid.UUID(asset_id), ACTIVE_KEY))
        assert row is not None and row.vector is not None
        return blob_to_vector(row.vector)


def _three_images(client: TestClient) -> tuple[str, str, str]:
    """赤、ほぼ同じ赤、青と黄の画像。"""
    red = _upload(client, _png((220, 30, 30)))
    red2 = _upload(client, _png((222, 31, 30)))
    other = _upload(client, _png((20, 40, 230), split=(240, 230, 20)))
    _wait_embedded(client, red, red2, other)
    return red, red2, other


# -- 無効なとき ---------------------------------------------------------------------


def test_disabled_returns_409_and_capabilities(client: TestClient) -> None:
    asset_id = _upload(client, _png((1, 2, 3)))
    caps = client.get("/api/capabilities").json()["embeddings"]
    assert caps["available"] is False
    assert caps["model_key"] is None
    assert caps["index_backend"] in ("numpy", "pgvector")
    for path in (
        "/api/search/semantic?q=cat",
        f"/api/assets/{asset_id}/similar",
        "/api/embeddings/duplicates",
        "/api/embeddings/graph",
    ):
        response = client.get(path)
        assert response.status_code == 409, (path, response.text)
        assert response.json()["detail"]["code"] == "embeddings_unavailable"
        assert response.json()["detail"]["message"]

    _enable(client, onnx_model="clip-japanese-base")
    caps = client.get("/api/capabilities").json()["embeddings"]
    assert caps["available"] is True
    assert caps["model_key"].startswith("fake:onnx:clip-japanese-base@")
    assert caps["languages"] == ["ja", "en"]
    assert caps["multilingual"] is True


# -- 文章での検索 -------------------------------------------------------------------


def test_semantic_search_orders_by_similarity(client: TestClient) -> None:
    _enable(client)
    red, red2, other = _three_images(client)
    cache = client.app.state.query_vector_cache
    cache.put(ACTIVE_KEY, "a red square", _vector(client, red))

    body = client.get("/api/search/semantic", params={"q": "  a red square "}).json()
    assert body["query"] == "a red square"
    assert body["model_key"] == ACTIVE_KEY
    assert body["languages"] == ["en"]
    assert body["multilingual"] is False
    ids = [a["id"] for a in body["assets"]]
    assert ids[:2] == [red, red2]
    assert ids[2] == other
    assert body["assets"][0]["score"] == pytest.approx(1.0, abs=1e-4)
    assert body["assets"][0]["score"] >= body["assets"][1]["score"] >= body["assets"][2]["score"]
    assert {"kind", "width", "height", "created_at", "title", "mime"} <= set(body["assets"][0])

    limited = client.get("/api/search/semantic", params={"q": "a red square", "limit": 1}).json()
    assert [a["id"] for a in limited["assets"]] == [red]

    assert client.get("/api/search/semantic", params={"q": "  "}).status_code == 422
    assert client.get("/api/search/semantic", params={"q": "x", "limit": 51}).status_code == 422
    assert client.get("/api/search/semantic", params={"q": "x" * 1001}).status_code == 422
    missing_group = client.get(
        "/api/search/semantic", params={"q": "x", "group_id": str(uuid.uuid4())}
    )
    assert missing_group.status_code == 404


def test_query_vector_is_cached(client: TestClient) -> None:
    _enable(client)
    _three_images(client)
    engine = client.app.state.embedder.engines._fakes[ACTIVE_KEY]
    before = len(engine.text_calls)
    for _ in range(3):
        assert client.get("/api/search/semantic", params={"q": "sunset"}).status_code == 200
    assert len(engine.text_calls) == before + 1


def test_semantic_search_group_and_tag(client: TestClient) -> None:
    _enable(client)
    red, red2, other = _three_images(client)
    group = client.post("/api/asset-groups", json={"name": "g"})
    assert group.status_code in (200, 201), group.text
    group_id = group.json()["id"]
    added = client.post(f"/api/asset-groups/{group_id}/assets", json={"asset_ids": [other]})
    assert added.status_code == 200, added.text
    client.app.state.query_vector_cache.put(ACTIVE_KEY, "red", _vector(client, red))
    body = client.get("/api/search/semantic", params={"q": "red", "group_id": group_id}).json()
    assert [a["id"] for a in body["assets"]] == [other]

    response = client.post(f"/api/assets/{red2}/tags", json={"name": "crimson"})
    assert response.status_code == 200, response.text
    body = client.get("/api/search/semantic", params={"q": "red", "tag": "crimson"}).json()
    assert [a["id"] for a in body["assets"]] == [red2]


def test_deleted_and_masks_are_excluded(client: TestClient) -> None:
    _enable(client)
    red, red2, other = _three_images(client)
    assert client.delete(f"/api/assets/{red2}").status_code in (200, 204)
    mask = client.post(
        "/api/assets",
        files={"file": ("m.png", _png((220, 30, 30)), "image/png")},
        data={"kind": "mask"},
    )
    assert mask.status_code == 201
    client.app.state.query_vector_cache.put(ACTIVE_KEY, "red", _vector(client, red))
    body = client.get("/api/search/semantic", params={"q": "red"}).json()
    assert [a["id"] for a in body["assets"]] == [red, other]
    similar = client.get(f"/api/assets/{red}/similar").json()
    assert [a["id"] for a in similar["assets"]] == [other]
    # マスクの似た画像は 409(対象外)。
    response = client.get(f"/api/assets/{mask.json()['id']}/similar")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "embedding_not_supported"


# -- 似た画像 -----------------------------------------------------------------------


def test_similar_assets(client: TestClient) -> None:
    _enable(client)
    red, red2, other = _three_images(client)
    body = client.get(f"/api/assets/{red}/similar").json()
    assert body["asset_id"] == red
    assert body["model_key"] == ACTIVE_KEY
    ids = [a["id"] for a in body["assets"]]
    assert ids == [red2, other]
    assert body["assets"][0]["score"] > 0.99
    assert [a["id"] for a in client.get(f"/api/assets/{red}/similar?limit=1").json()["assets"]] == [
        red2
    ]
    assert client.get(f"/api/assets/{uuid.uuid4()}/similar").status_code == 404


def test_similar_reports_why_vector_is_missing(client: TestClient) -> None:
    _enable(client, auto_on_ingest=False)
    asset_id = _upload(client, _png((5, 6, 7)))
    response = client.get(f"/api/assets/{asset_id}/similar")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "embedding_missing"

    with client.app.state.session_factory() as db:
        db.add(AssetEmbedding(asset_id=uuid.UUID(asset_id), model_key=ACTIVE_KEY, status="running"))
        db.commit()
    assert client.get(f"/api/assets/{asset_id}/similar").json()["detail"]["code"] == (
        "embedding_pending"
    )
    with client.app.state.session_factory() as db:
        row = db.get(AssetEmbedding, (uuid.UUID(asset_id), ACTIVE_KEY))
        row.status = "failed"
        db.commit()
    assert client.get(f"/api/assets/{asset_id}/similar").json()["detail"]["code"] == (
        "embedding_failed"
    )


# -- 重複の候補 ---------------------------------------------------------------------


def test_duplicates(client: TestClient) -> None:
    _enable(client)
    red, red2, other = _three_images(client)
    blue = _upload(client, _png((20, 40, 230), split=(241, 231, 21)))
    _wait_embedded(client, blue)

    body = client.get("/api/embeddings/duplicates", params={"threshold": 0.99}).json()
    assert body["model_key"] == ACTIVE_KEY
    assert body["threshold"] == 0.99
    assert body["scanned"] == 4
    assert body["truncated"] is False
    groups = [[a["id"] for a in g["assets"]] for g in body["groups"]]
    assert sorted(map(sorted, groups)) == sorted([sorted([red, red2]), sorted([other, blue])])
    for group in body["groups"]:
        # 古い順。
        created = [a["created_at"] for a in group["assets"]]
        assert created == sorted(created)
        assert group["max_score"] >= 0.99
        assert all(a["max_score"] >= 0.99 for a in group["assets"])

    # しきい値を省くと管理者設定の値(既定 0.90。ADR-0033 12章)。
    body = client.get("/api/embeddings/duplicates").json()
    assert body["threshold"] == 0.90
    body = client.get("/api/embeddings/duplicates", params={"limit": 1}).json()
    assert len(body["groups"]) == 1
    assert body["groups_truncated"] is True
    assert client.get("/api/embeddings/duplicates?threshold=0.2").status_code == 422


def test_duplicates_cap_sets_truncated(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domain import semantic_search

    _enable(client)
    red, red2, other = _three_images(client)
    original = semantic_search.find_duplicates

    def capped(*args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("max_assets", 2)
        return original(*args, **kwargs)

    monkeypatch.setattr(semantic_search, "find_duplicates", capped)
    body = client.get("/api/embeddings/duplicates", params={"threshold": 0.99}).json()
    assert body["scanned"] == 2
    assert body["truncated"] is True
    # 新しい順に 2 件(red2 と other)だけを比べるので、red と red2 の組は出ない。
    assert body["groups"] == []


# -- マップ --------------------------------------------------------------------------


def test_graph_shape_and_lineage(client: TestClient) -> None:
    _enable(client)
    red, red2, other = _three_images(client)
    edit = client.post(
        "/api/runs",
        json={
            "operation": "edit",
            "model": "gpt-image-2.5-sunburst",
            "prompt": "edit",
            "params": {"n": 1},
            "inputs": [{"asset_id": red, "role": "image", "position": 0}],
        },
    )
    assert edit.status_code == 202, edit.text
    detail = wait_for_run_terminal(client, edit.json()["id"])
    child = detail["outputs"][0]["asset_id"]
    _wait_embedded(client, child)

    body = client.get("/api/embeddings/graph", params={"k": 2}).json()
    assert body["model_key"] == ACTIVE_KEY
    assert body["k"] == 2
    assert body["total"] == 4
    assert body["truncated"] is False
    ids = [n["id"] for n in body["nodes"]]
    # 新しい順。
    assert ids == [child, other, red2, red]
    assert body["lineage_edges"] is None
    assert len(body["neighbor_indices"]) == len(body["neighbor_similarities"]) == 4
    for i, (row, sims) in enumerate(
        zip(body["neighbor_indices"], body["neighbor_similarities"], strict=True)
    ):
        assert len(row) == len(sims) == 3
        assert row[0] == i
        assert sims[0] == 1.0
        assert i not in row[1:]
        assert sims[1] >= sims[2]
    red_index = ids.index(red)
    assert body["neighbor_indices"][red_index][1] == ids.index(red2)

    body = client.get(
        "/api/embeddings/graph", params={"k": 30, "limit": 3, "include_lineage": True}
    ).json()
    assert body["total"] == 4
    assert body["truncated"] is True
    assert len(body["nodes"]) == 3
    assert all(len(row) == 3 for row in body["neighbor_indices"])
    # 親(red)が返っていないので、系列の辺は無い。
    assert body["lineage_edges"] == []
    body = client.get("/api/embeddings/graph", params={"include_lineage": True}).json()
    ids = [n["id"] for n in body["nodes"]]
    assert body["lineage_edges"] == [[ids.index(red), ids.index(child)]]
    assert client.get("/api/embeddings/graph?k=31").status_code == 422
    assert client.get("/api/embeddings/graph?limit=5001").status_code == 422


def _tag(client: TestClient, asset_id: str, name: str) -> None:
    response = client.post(f"/api/assets/{asset_id}/tags", json={"name": name})
    assert response.status_code == 200, response.text


def test_graph_multiple_tags_are_and(client: TestClient) -> None:
    """`tag` を繰り返すと、すべてのタグが付いた画像だけ(AND)。1つなら従来どおり。"""
    _enable(client)
    red, red2, other = _three_images(client)
    _tag(client, red, "cat")
    _tag(client, red, "Outdoor")
    _tag(client, red2, "cat")
    _tag(client, other, "outdoor")

    def node_ids(params: Any) -> set[str]:
        response = client.get("/api/embeddings/graph", params=params)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == len(body["nodes"])
        return {n["id"] for n in body["nodes"]}

    assert node_ids({"tag": "cat"}) == {red, red2}
    assert node_ids({"tag": "outdoor"}) == {red, other}
    assert node_ids([("tag", "cat"), ("tag", "outdoor")]) == {red}
    # 正規化(大文字・前後の空白)と重複、空の値は無視する。
    assert node_ids([("tag", " CAT "), ("tag", "cat"), ("tag", "Outdoor"), ("tag", "")]) == {red}
    assert node_ids([("tag", "cat"), ("tag", "nothing")]) == set()
    # 消したタグは条件に当たらない。
    response = client.delete(f"/api/assets/{red}/tags/outdoor")
    assert response.status_code == 200, response.text
    assert node_ids([("tag", "cat"), ("tag", "outdoor")]) == set()


def test_cache_follows_new_vectors(client: TestClient) -> None:
    """worker が書いたベクトルは、次の検索から見える(numpy の行列は版で読み直す)。"""
    _enable(client)
    red, _red2, _other = _three_images(client)
    assert len(client.get(f"/api/assets/{red}/similar").json()["assets"]) == 2
    late = _upload(client, _png((221, 30, 31)))
    _wait_embedded(client, late)
    ids = [a["id"] for a in client.get(f"/api/assets/{red}/similar").json()["assets"]]
    assert late in ids and len(ids) == 3
    # ベクトルを消すと出なくなる。
    response = client.delete(f"/api/settings/embeddings/vectors/{ACTIVE_KEY}")
    assert response.status_code == 200, response.text
    assert client.get(f"/api/assets/{red}/similar").status_code == 409


# -- 見える範囲(oidc) -----------------------------------------------------------------


def test_oidc_other_users_assets_never_appear(client_oidc: TestClient) -> None:
    login_as(client_oidc, "admin@example.com")
    _enable(client_oidc)
    client_oidc.cookies.clear()

    login_as(client_oidc, "alice@example.com")
    alice_red = _upload(client_oidc, _png((220, 30, 30)))
    alice_blue = _upload(client_oidc, _png((20, 40, 230)))
    client_oidc.cookies.clear()

    login_as(client_oidc, "bob@example.com")
    bob_red = _upload(client_oidc, _png((221, 30, 30)))
    bob_other = _upload(client_oidc, _png((30, 200, 30), split=(10, 10, 10)))
    _wait_embedded(client_oidc, alice_red, alice_blue, bob_red, bob_other)
    alice_ids = {alice_red, alice_blue}

    client_oidc.app.state.query_vector_cache.put(ACTIVE_KEY, "red", _vector(client_oidc, alice_red))
    body = client_oidc.get("/api/search/semantic", params={"q": "red"}).json()
    assert [a["id"] for a in body["assets"]] == [bob_red, bob_other]

    similar = client_oidc.get(f"/api/assets/{bob_red}/similar").json()
    assert [a["id"] for a in similar["assets"]] == [bob_other]
    # 他人の画像の似た画像は「無い」と同じ 404。
    assert client_oidc.get(f"/api/assets/{alice_red}/similar").status_code == 404

    dups = client_oidc.get("/api/embeddings/duplicates", params={"threshold": 0.5}).json()
    assert dups["scanned"] == 2
    seen = {a["id"] for g in dups["groups"] for a in g["assets"]}
    assert not seen & alice_ids

    graph = client_oidc.get("/api/embeddings/graph", params={"include_lineage": True}).json()
    assert {n["id"] for n in graph["nodes"]} == {bob_red, bob_other}
    assert graph["total"] == 2

    # タグの AND で絞っても、他人の画像は出ない(同じタグが付いていても)。
    _tag(client_oidc, bob_red, "warm")
    _tag(client_oidc, bob_red, "square")
    client_oidc.cookies.clear()
    login_as(client_oidc, "alice@example.com")
    _tag(client_oidc, alice_red, "warm")
    _tag(client_oidc, alice_red, "square")
    graph = client_oidc.get(
        "/api/embeddings/graph", params=[("tag", "warm"), ("tag", "square")]
    ).json()
    assert [n["id"] for n in graph["nodes"]] == [alice_red]
    client_oidc.cookies.clear()
    login_as(client_oidc, "bob@example.com")
    graph = client_oidc.get(
        "/api/embeddings/graph", params=[("tag", "warm"), ("tag", "square")]
    ).json()
    assert [n["id"] for n in graph["nodes"]] == [bob_red]


# -- MCP ----------------------------------------------------------------------------


def _mcp(client: TestClient, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        headers=_MCP_HEADERS,
    )
    assert response.status_code == 200, response.text
    return response.json()["result"]


def test_mcp_semantic_search_and_similar(client: TestClient) -> None:
    assert client.patch("/api/settings/mcp", json={"enabled": True}).status_code == 200
    caps = _mcp(client, "get_capabilities", {})["structuredContent"]
    assert caps["embeddings"]["available"] is False
    result = _mcp(client, "search_assets", {"query": "red", "mode": "semantic"})
    assert result["isError"] is True
    assert "not available" in result["content"][0]["text"]

    _enable(client)
    red, red2, other = _three_images(client)
    caps = _mcp(client, "get_capabilities", {})["structuredContent"]
    assert caps["embeddings"]["available"] is True
    client.app.state.query_vector_cache.put(ACTIVE_KEY, "red", _vector(client, red))

    payload = _mcp(client, "search_assets", {"query": "red", "mode": "semantic", "limit": 2})[
        "structuredContent"
    ]
    assert payload["mode"] == "semantic"
    assert [i["asset_id"] for i in payload["items"]] == [red, red2]
    assert payload["items"][0]["score"] == pytest.approx(1.0, abs=1e-3)
    payload = _mcp(
        client, "search_assets", {"query": "red", "mode": "semantic", "kind": "generated"}
    )["structuredContent"]
    assert payload["items"] == []
    # 既定はキーワード検索のまま。
    payload = _mcp(client, "search_assets", {})["structuredContent"]
    assert payload["mode"] == "keyword"
    assert "score" not in payload["items"][0]

    payload = _mcp(client, "find_similar_assets", {"asset_id": red, "include_thumbnails": True})
    assert not payload.get("isError")
    assert [i["asset_id"] for i in payload["structuredContent"]["items"]] == [red2, other]
    assert any(c["type"] == "image" for c in payload["content"])
    missing = _mcp(client, "find_similar_assets", {"asset_id": str(uuid.uuid4())})
    assert missing["isError"] is True
    assert "not found" in missing["content"][0]["text"]
