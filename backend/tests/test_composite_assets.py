"""入力画像の配置(`kind=composite`、ADR-0050)。

- 保存: `POST /api/assets` の `composition`(レシピ)の検証、素材の確認、詳細の `composition`、
  未使用・使用済みの置き換え。
- 系列: `composite_source` の辺(主たる素材は position 0)、子孫の方向、置き換えで消えた配置を
  辿らないこと。共有リンク・書き出し・取り込み・埋め込み・Mermaid にも辺が入ること。
- レーティング: 取り込みのたびに判定の待ち行列に入ること。
"""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import AssetComposition, AssetCompositionSource
from tests.conftest import login_as, make_png_bytes, wait_for_run_terminal

pytestmark = pytest.mark.windows

_MODEL = "gpt-image-2.5-sunburst"


# -- 準備 ----------------------------------------------------------------------


def _upload(client: TestClient, kind: str = "upload", color=(10, 20, 30)) -> str:
    response = client.post(
        "/api/assets",
        files={"file": ("f.png", make_png_bytes(color=color), "image/png")},
        data={"kind": kind},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _layer(source: str, x: float = 0, y: float = 0, size: float = 100, **extra: Any) -> dict:
    return {
        "source_asset_id": source,
        "corners": [[x, y], [x + size, y], [x + size, y + size], [x, y + size]],
        "flip_x": False,
        "flip_y": False,
        **extra,
    }


def _recipe(primary: str, *layers: dict, **canvas: Any) -> dict:
    return {
        "version": 2,
        "canvas": {"width": 256, "height": 256, "background": "white", **canvas},
        "primary_source_asset_id": primary,
        "layers": list(layers),
    }


def _post(client: TestClient, recipe: Any | None, **form: str):  # noqa: ANN202
    data: dict[str, str] = {"kind": "composite", **form}
    if recipe is not None:
        data["composition"] = recipe if isinstance(recipe, str) else json.dumps(recipe)
    return client.post(
        "/api/assets",
        files={"file": ("composite.png", make_png_bytes(256, 256, (1, 2, 3)), "image/png")},
        data=data,
    )


def _create(client: TestClient, recipe: dict, **form: str) -> dict:
    response = _post(client, recipe, **form)
    assert response.status_code == 201, response.text
    return response.json()


def _two_sources(client: TestClient) -> tuple[str, str, dict]:
    a = _upload(client, color=(200, 0, 0))
    b = _upload(client, color=(0, 200, 0))
    # 下から b、a、b(同じ素材を2つのレイヤーに使う)。主たる素材は a。
    recipe = _recipe(a, _layer(b, 0, 0), _layer(a, 50, 50), _layer(b, 120, 0))
    return a, b, recipe


def _run_edit(client: TestClient, asset_id: str) -> dict:
    response = client.post(
        "/api/runs",
        json={
            "operation": "edit",
            "model": _MODEL,
            "prompt": "finish this arrangement",
            "params": {"n": 1, "quality": "low"},
            "inputs": [{"asset_id": asset_id, "role": "image", "position": 0}],
        },
    )
    assert response.status_code == 202, response.text
    detail = wait_for_run_terminal(client, response.json()["id"])
    assert detail["status"] == "succeeded", detail
    return detail


# -- 保存 ----------------------------------------------------------------------


def test_create_composite_saves_recipe_and_sources(client: TestClient) -> None:
    a, b, recipe = _two_sources(client)
    body = _create(client, recipe)
    assert body["kind"] == "composite"
    assert body["source_asset_id"] is None
    assert body["ingest_outcome"] == "created"
    composition = body["composition"]
    assert composition["recipe"] == recipe
    # 主たる素材が先頭、残りはレイヤーの順で重ならないように。
    assert composition["sources"] == [
        {"asset_id": a, "available": True},
        {"asset_id": b, "available": True},
    ]

    detail = client.get(f"/api/assets/{body['id']}").json()
    assert detail["composition"] == composition

    with client.app.state.session_factory() as db:
        rows = db.execute(
            select(AssetCompositionSource)
            .where(AssetCompositionSource.asset_id == uuid.UUID(body["id"]))
            .order_by(AssetCompositionSource.position)
        ).scalars()
        assert [(r.position, str(r.source_asset_id)) for r in rows] == [(0, a), (1, b)]
        assert db.get(AssetComposition, uuid.UUID(body["id"])) is not None

    # ほかの種類の Asset には composition が無い。
    assert client.get(f"/api/assets/{a}").json()["composition"] is None


@pytest.mark.parametrize("version", [1, 2])
def test_composite_accepts_recipe_versions(client: TestClient, version: int) -> None:
    # 版 1(射影変換で描いたもの)も、作り直しで開けるよう引き続き受け付ける。
    a = _upload(client)
    recipe = _recipe(a, _layer(a))
    recipe["version"] = version
    body = _create(client, recipe)
    assert body["composition"]["recipe"]["version"] == version
    detail = client.get(f"/api/assets/{body['id']}").json()
    assert detail["composition"]["recipe"]["version"] == version


def test_composite_is_listed_by_kind(client: TestClient) -> None:
    a, _b, recipe = _two_sources(client)
    composite = _create(client, recipe)["id"]
    items = client.get("/api/assets", params={"kind": "composite"}).json()["items"]
    assert [item["id"] for item in items] == [composite]
    both = client.get("/api/assets", params=[("kind", "composite"), ("kind", "sketch")])
    assert both.status_code == 200
    assert a not in {item["id"] for item in both.json()["items"]}


def test_composite_accepts_sketch_and_composite_sources(client: TestClient) -> None:
    sketch = _upload(client, kind="sketch")
    first = _create(client, _recipe(sketch, _layer(sketch)))["id"]
    nested = _create(client, _recipe(first, _layer(first)))
    assert nested["composition"]["sources"] == [{"asset_id": first, "available": True}]


@pytest.mark.parametrize(
    ("mutate", "fragment"),
    [
        (lambda r: r.update(version=3), "version"),
        (lambda r: r.update(version=0), "version"),
        (lambda r: r["canvas"].update(background="red"), "background"),
        (lambda r: r["canvas"].update(width=0), "width"),
        (lambda r: r["canvas"].update(height=100_000), "height"),
        (lambda r: r.update(layers=[]), "layers"),
        (lambda r: r.update(primary_source_asset_id=str(uuid.uuid4())), "primary"),
        # 凹んだ四角形
        (
            lambda r: r["layers"][0].update(corners=[[0, 0], [100, 0], [20, 20], [0, 100]]),
            "convex",
        ),
        # ねじれた四角形(右上と右下を入れ替える)
        (
            lambda r: r["layers"][0].update(corners=[[0, 0], [100, 100], [100, 0], [0, 100]]),
            "convex",
        ),
        # 面積が無い(一直線)
        (
            lambda r: r["layers"][0].update(corners=[[0, 0], [10, 0], [20, 0], [30, 0]]),
            "convex",
        ),
        (lambda r: r["layers"][0].update(corners=[[0, 0], [100, 0], [100, 100]]), "corners"),
        (
            lambda r: r["layers"][0].update(
                corners=[[0, 0], [100_000, 0], [100_000, 100], [0, 100]]
            ),
            "outside",
        ),
        (lambda r: r.update(unexpected=True), "unexpected"),
    ],
)
def test_invalid_recipe_is_rejected(client: TestClient, mutate, fragment: str) -> None:  # noqa: ANN001
    a = _upload(client)
    recipe = _recipe(a, _layer(a))
    mutate(recipe)
    response = _post(client, recipe)
    assert response.status_code == 422, response.text
    assert fragment in response.text


def test_non_finite_corner_is_rejected(client: TestClient) -> None:
    a = _upload(client)
    raw = json.dumps(_recipe(a, _layer(a))).replace("[100,", "[NaN,", 1)
    assert "NaN" in raw
    assert _post(client, raw).status_code == 422


def test_too_many_layers_is_rejected(client: TestClient) -> None:
    a = _upload(client)
    recipe = _recipe(a, *[_layer(a, i, i) for i in range(17)])
    assert _post(client, recipe).status_code == 422
    recipe["layers"] = recipe["layers"][:16]
    assert _post(client, recipe).status_code == 201


def test_recipe_must_be_json_and_present(client: TestClient) -> None:
    assert _post(client, "{not json").status_code == 422
    missing = _post(client, None)
    assert missing.status_code == 422
    assert "composition" in missing.text
    a = _upload(client)
    # composite 以外には付けられない。
    response = client.post(
        "/api/assets",
        files={"file": ("f.png", make_png_bytes(), "image/png")},
        data={"kind": "upload", "composition": json.dumps(_recipe(a, _layer(a)))},
    )
    assert response.status_code == 422


def test_source_asset_id_is_rejected_for_composite(client: TestClient) -> None:
    a = _upload(client)
    response = _post(client, _recipe(a, _layer(a)), source_asset_id=a)
    assert response.status_code == 422


def test_unknown_deleted_and_mask_sources_are_rejected(client: TestClient) -> None:
    a = _upload(client)
    unknown = str(uuid.uuid4())
    assert _post(client, _recipe(a, _layer(a), _layer(unknown))).status_code == 422

    deleted = _upload(client, color=(5, 5, 5))
    assert client.delete(f"/api/assets/{deleted}").status_code == 204
    assert _post(client, _recipe(a, _layer(a), _layer(deleted))).status_code == 422

    mask = _upload(client, kind="mask")
    response = _post(client, _recipe(mask, _layer(mask)))
    assert response.status_code == 422
    with client.app.state.session_factory() as db:
        assert db.execute(select(AssetComposition)).first() is None


def test_foreign_source_is_rejected(client_oidc: TestClient) -> None:
    client = client_oidc
    login_as(client, "alice@example.com", "Alice")
    alice_asset = _upload(client)
    login_as(client, "bob@example.com", "Bob")
    bob_asset = _upload(client)
    response = _post(client, _recipe(bob_asset, _layer(bob_asset), _layer(alice_asset)))
    assert response.status_code == 422
    assert _post(client, _recipe(bob_asset, _layer(bob_asset))).status_code == 201


def test_unavailable_sources_are_still_listed(client: TestClient) -> None:
    a, b, recipe = _two_sources(client)
    composite = _create(client, recipe)["id"]
    assert client.delete(f"/api/assets/{b}").status_code == 204
    sources = client.get(f"/api/assets/{composite}").json()["composition"]["sources"]
    assert sources == [
        {"asset_id": a, "available": True},
        {"asset_id": b, "available": False},
    ]


# -- 置き換え ----------------------------------------------------------------------


def test_replace_unused_composite_deletes_old(client: TestClient) -> None:
    a, b, recipe = _two_sources(client)
    old = _create(client, recipe)["id"]
    new_recipe = _recipe(b, _layer(b), _layer(a, 10, 10))
    new = _create(client, new_recipe, replaces_asset_id=old)
    assert new["id"] != old
    assert new["composition"]["recipe"] == new_recipe
    assert [s["asset_id"] for s in new["composition"]["sources"]] == [b, a]
    assert client.get(f"/api/assets/{old}").json()["deleted_at"] is not None

    # 置き換えで消えた配置は、素材の子孫として辿らない。
    lineage = client.get(f"/api/assets/{a}/lineage").json()
    node_ids = {n["id"] for n in lineage["nodes"]}
    assert old not in node_ids
    assert new["id"] in node_ids


def test_replace_used_composite_keeps_old(client: TestClient) -> None:
    a, _b, recipe = _two_sources(client)
    old = _create(client, recipe)["id"]
    _run_edit(client, old)
    new = _create(client, _recipe(a, _layer(a)), replaces_asset_id=old)
    assert client.get(f"/api/assets/{old}").json()["deleted_at"] is None
    assert new["source_asset_id"] is None
    assert [s["asset_id"] for s in new["composition"]["sources"]] == [a]


def test_replace_requires_same_kind(client: TestClient) -> None:
    a = _upload(client)
    sketch = _upload(client, kind="sketch")
    response = _post(client, _recipe(a, _layer(a)), replaces_asset_id=sketch)
    assert response.status_code == 422


# -- 系列 ----------------------------------------------------------------------


def _composite_edges(lineage: dict) -> list[tuple[str, str, int | None, bool]]:
    return sorted(
        (e["source"], e["target"], e["position"], e["primary"])
        for e in lineage["edges"]
        if e["kind"] == "composite_source"
    )


def test_lineage_has_composite_source_edges(client: TestClient) -> None:
    a, b, recipe = _two_sources(client)
    composite = _create(client, recipe)["id"]
    run = _run_edit(client, composite)
    output = run["outputs"][0]["asset_id"]

    lineage = client.get(f"/api/assets/{output}/lineage").json()
    node_ids = {n["id"] for n in lineage["nodes"]}
    assert {a, b, composite, output} <= node_ids
    assert _composite_edges(lineage) == sorted([(a, composite, 0, True), (b, composite, 1, False)])
    depth = {n["id"]: n["depth"] for n in lineage["nodes"]}
    assert depth[a] == depth[b] == depth[composite] - 1

    # 素材から子孫の方向へも辿る(主たる素材でない b からも)。
    down = client.get(f"/api/assets/{b}/lineage").json()
    assert {composite, output} <= {n["id"] for n in down["nodes"]}
    assert (b, composite, 1, False) in _composite_edges(down)

    # Mermaid にも辺のラベルが出る。
    from app.auth.identity import LOCAL_ADMIN
    from app.domain.lineage import asset_lineage_graph
    from app.domain.lineage_mermaid import render_lineage_mermaid

    with client.app.state.session_factory() as db:
        graph = asset_lineage_graph(
            db, uuid.UUID(output), viewer=LOCAL_ADMIN, up=10, down=10, max_nodes=100
        )
        assert "composite source" in render_lineage_mermaid(graph)


def test_share_includes_composite_edges(client: TestClient) -> None:
    from tests.test_shares import _create as create_share
    from tests.test_shares import _enable, _public, _token

    _enable(client)
    a, b, recipe = _two_sources(client)
    composite = _create(client, recipe)["id"]
    share = create_share(client, composite, "ancestors")
    body = _public(client, _token(share)).json()
    assert {x["id"] for x in body["assets"]} == {a, b, composite}
    edges = sorted(
        (e["source"], e["position"], e["primary"])
        for e in body["edges"]
        if e["kind"] == "composite_source" and e["target"] == composite
    )
    assert edges == sorted([(a, 0, True), (b, 1, False)])


def _export(client: TestClient, asset_id: str, scope: str = "ancestors") -> bytes:
    response = client.get(f"/api/assets/{asset_id}/export", params={"scope": scope})
    assert response.status_code == 200, response.text
    return response.content


def _manifest(zip_bytes: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        return json.loads(zf.read("manifest.json"))


def test_export_and_import_keep_composition(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tests.test_lineage_export_import import _second_client

    a, b, recipe = _two_sources(client)
    composite = _create(client, recipe)["id"]
    zip_bytes = _export(client, composite)
    manifest = _manifest(zip_bytes)
    entry = next(x for x in manifest["assets"] if x["id"] == composite)
    assert entry["kind"] == "composite"
    assert entry["composition"]["recipe"] == recipe
    assert entry["composition"]["sources"] == [
        {"asset_id": a, "position": 0},
        {"asset_id": b, "position": 1},
    ]
    assert next(x for x in manifest["assets"] if x["id"] == a)["composition"] is None

    preview = client.get(
        f"/api/assets/{composite}/export/preview",
        params={"scope": "ancestors", "include_graph": "true"},
    )
    assert preview.status_code == 200, preview.text
    assert _composite_edges(preview.json()["graph"]) == sorted(
        [(a, composite, 0, True), (b, composite, 1, False)]
    )

    # 納品用の index.html にも辺と素材が出る。
    delivery = client.get(
        f"/api/assets/{composite}/export", params={"scope": "ancestors", "mode": "delivery"}
    )
    assert delivery.status_code == 200, delivery.text
    with zipfile.ZipFile(io.BytesIO(delivery.content)) as zf:
        html = zf.read("index.html").decode("utf-8")
    assert "配置の素材" in html or "Arrangement sources" in html

    with _second_client(monkeypatch, tmp_path, "other") as other:
        response = other.post(
            "/api/imports/lineage",
            files={"file": ("lineage.zip", zip_bytes, "application/zip")},
        )
        assert response.status_code == 201, response.text
        new_root = response.json()["root_asset_id"]
        detail = other.get(f"/api/assets/{new_root}").json()
        assert detail["kind"] == "composite"
        composition = detail["composition"]
        assert composition is not None
        new_a, new_b = (s["asset_id"] for s in composition["sources"])
        assert composition["recipe"]["primary_source_asset_id"] == new_a
        assert [layer["source_asset_id"] for layer in composition["recipe"]["layers"]] == [
            new_b,
            new_a,
            new_b,
        ]
        lineage = other.get(f"/api/assets/{new_root}/lineage").json()
        assert _composite_edges(lineage) == sorted(
            [(new_a, new_root, 0, True), (new_b, new_root, 1, False)]
        )


def test_export_drops_out_of_scope_sources(client: TestClient) -> None:
    a, b, recipe = _two_sources(client)
    composite = _create(client, recipe)["id"]
    run = _run_edit(client, composite)
    output = run["outputs"][0]["asset_id"]
    # 素材 b を消すと、範囲(削除済みを含めない)の外になる。
    assert client.delete(f"/api/assets/{b}").status_code == 204
    manifest = _manifest(_export(client, output))
    entry = next(x for x in manifest["assets"] if x["id"] == composite)
    assert entry["composition"]["sources"] == [{"asset_id": a, "position": 0}]
    assert b not in json.dumps(manifest)
    assert [layer["source_asset_id"] for layer in entry["composition"]["recipe"]["layers"]] == [a]


def test_import_rejects_inconsistent_composition(client: TestClient) -> None:
    from tests.test_lineage_export_import import _entries, _rezip

    a, _b, recipe = _two_sources(client)
    composite = _create(client, recipe)["id"]
    entries = _entries(_export(client, composite))
    manifest = json.loads(entries["manifest.json"])
    entry = next(x for x in manifest["assets"] if x["id"] == composite)
    entry["composition"]["sources"] = [{"asset_id": a, "position": 0}]  # b が一覧に無い
    entries["manifest.json"] = json.dumps(manifest).encode()
    response = client.post(
        "/api/imports/lineage",
        files={"file": ("lineage.zip", _rezip(entries), "application/zip")},
    )
    assert response.status_code == 422, response.text


def test_download_embeds_composite_edges(client: TestClient) -> None:
    from app.domain.embedded_meta import read_gakei_meta

    a, b, recipe = _two_sources(client)
    composite = _create(client, recipe)["id"]
    response = client.get(
        f"/api/assets/{composite}/content", params={"variant": "original", "download": 1}
    )
    assert response.status_code == 200
    meta = read_gakei_meta(response.content)
    assert meta is not None
    edges = sorted(
        (e["source"], e.get("position"))
        for e in meta["edges"]
        if e["kind"] == "composite_source" and e["target"] == composite
    )
    assert edges == sorted([(a, 0), (b, 1)])

    # 取り込み直した画像(内容が変わったもの)は、埋め込みのグラフから主たる素材を判定する。
    from app.domain.embedded_meta import normalize_lineage_meta

    normalized = normalize_lineage_meta(meta)
    assert normalized is not None
    assert any(e["kind"] == "composite_source" and e["position"] == 0 for e in normalized["edges"])


# -- レーティング ----------------------------------------------------------------


def test_composite_is_queued_for_content_rating(client: TestClient) -> None:
    from tests.test_content_rating import _engines, _patch_settings, _wait_rating

    _engines(client).rating_level = "g"
    _patch_settings(client, content_rating_enabled=True)
    _a, _b, recipe = _two_sources(client)
    composite = _create(client, recipe)["id"]
    body = _wait_rating(client, composite)
    assert body["content_rating_detail"]["status"] == "succeeded"
