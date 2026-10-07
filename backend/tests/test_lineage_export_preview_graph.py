"""ADR-0037 1章(2026-10-07 追記): 書き出し前の系列のプレビュー(`include_graph=true`)。

プレビューのグラフに出る Asset と Run が、同じ範囲で実際に書き出した ZIP の manifest と
一致すること。
辺は manifest の来歴(入力・出力・スケッチの下地)と一致すること。見える範囲(ADR-0025)、
論理削除、範囲の外の入力の数。
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.domain.models import RunInput, RunInputRole
from tests.conftest import login_as, make_png_bytes
from tests.test_lineage_export_import import _export, _manifest, _out, _run, _upload

pytestmark = pytest.mark.windows


def _preview(client: TestClient, asset_id: str, scope: str) -> dict:
    response = client.get(
        f"/api/assets/{asset_id}/export/preview",
        params={"scope": scope, "include_graph": "true"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _ids(graph: dict, node_type: str) -> set[str]:
    return {n["id"] for n in graph["nodes"] if n["type"] == node_type}


def _manifest_edges(manifest: dict) -> set[tuple[str, str, str]]:
    edges: set[tuple[str, str, str]] = set()
    for run in manifest["runs"]:
        for i in run["inputs"]:
            edges.add((i["asset_id"], run["id"], "input"))
    for asset in manifest["assets"]:
        if asset["produced_by_run_id"]:
            edges.add((asset["produced_by_run_id"], asset["id"], "output"))
        if asset["source_asset_id"]:
            edges.add((asset["source_asset_id"], asset["id"], "sketch_source"))
    return edges


def _assert_matches_export(client: TestClient, asset_id: str, scope: str) -> dict:
    body = _preview(client, asset_id, scope)
    manifest = _manifest(_export(client, asset_id, scope))
    graph = body["graph"]
    assert graph["root_asset_id"] == asset_id
    assert _ids(graph, "asset") == {a["id"] for a in manifest["assets"]}
    assert _ids(graph, "run") == {r["id"] for r in manifest["runs"]}
    assert {(e["source"], e["target"], e["kind"]) for e in graph["edges"]} == _manifest_edges(
        manifest
    )
    assert body["asset_count"] == len(manifest["assets"])
    assert body["run_count"] == len(manifest["runs"])
    assert body["omitted_input_count"] == sum(r["omitted_input_count"] for r in manifest["runs"])
    # 起点の深さは 0。Run は、その出力の1つ上。
    depths = {n["id"]: n["depth"] for n in graph["nodes"]}
    assert depths[asset_id] == 0
    for asset in manifest["assets"]:
        if asset["produced_by_run_id"]:
            assert depths[asset["produced_by_run_id"]] <= depths[asset["id"]] - 1
    return body


def _tree(client: TestClient) -> dict[str, dict]:
    """r0 → a0 → r1 → a1 → (r2 → a2、rc(a1 と範囲の外のアップロード) → c)、a1 のスケッチ。"""
    r0 = _run(client, "root")
    r1 = _run(client, "middle", [_out(r0)])
    r2 = _run(client, "child", [_out(r1)])
    other = _upload(client, make_png_bytes(color=(7, 8, 9)))
    rc = _run(client, "with other", [_out(r1), other])
    sketch = _upload(
        client, make_png_bytes(color=(11, 21, 31)), kind="sketch", source_asset_id=_out(r1)
    )
    return {
        "r0": r0,
        "r1": r1,
        "r2": r2,
        "rc": rc,
        "other": {"id": other},
        "sketch": {"id": sketch},
    }


@pytest.mark.parametrize("scope", ["ancestors", "descendants", "lineage"])
def test_preview_graph_matches_export(client: TestClient, scope: str) -> None:
    t = _tree(client)
    body = _assert_matches_export(client, _out(t["r1"]), scope)
    graph = body["graph"]
    if scope == "descendants":
        # 起点を生んだ Run と祖先は出さない。範囲の外のアップロードは数だけ。
        assert t["r1"]["id"] not in _ids(graph, "run")
        assert _out(t["r0"]) not in _ids(graph, "asset")
        assert t["other"]["id"] not in _ids(graph, "asset")
        assert body["omitted_input_count"] == 1
        assert t["sketch"]["id"] in _ids(graph, "asset")
    if scope == "ancestors":
        assert _ids(graph, "asset") == {_out(t["r0"]), _out(t["r1"])}
        assert body["omitted_input_count"] == 0
    if scope == "lineage":
        # 系列全体でも、子孫の Run の別の入力(起点の祖先でも子孫でもない)は範囲の外。
        assert _ids(graph, "asset") >= {_out(t["r0"]), _out(t["r1"]), _out(t["r2"])}
        assert t["other"]["id"] not in _ids(graph, "asset")
        assert body["omitted_input_count"] == 1


def test_preview_without_include_graph_has_no_graph(client: TestClient) -> None:
    r0 = _run(client, "root")
    response = client.get(f"/api/assets/{_out(r0)}/export/preview", params={"scope": "lineage"})
    assert response.status_code == 200
    assert response.json()["graph"] is None


def test_preview_graph_excludes_deleted(client: TestClient) -> None:
    r0 = _run(client, "root")
    r1 = _run(client, "child", [_out(r0)])
    assert client.delete(f"/api/assets/{_out(r0)}").status_code == 204
    body = _assert_matches_export(client, _out(r1), "ancestors")
    assert _ids(body["graph"], "asset") == {_out(r1)}
    assert _ids(body["graph"], "run") == {r1["id"]}
    assert body["graph"]["edges"] == [
        {
            "source": r1["id"],
            "target": _out(r1),
            "kind": "output",
            "role": None,
            "position": None,
            "output_index": 0,
            "primary": False,
        }
    ]
    assert body["omitted_input_count"] == 1


def test_preview_graph_respects_visibility(client_oidc: TestClient) -> None:
    login_as(client_oidc, "b@example.com", name="B")
    b_upload = _upload(client_oidc, make_png_bytes(color=(4, 5, 6)))

    login_as(client_oidc, "a@example.com", name="Alice")
    r0 = _run(client_oidc, "root")
    r1 = _run(client_oidc, "child", [_out(r0)])
    with client_oidc.app.state.session_factory() as db:
        db.add(
            RunInput(
                run_id=uuid.UUID(r1["id"]),
                asset_id=uuid.UUID(b_upload),
                role=RunInputRole.REFERENCE,
                position=1,
            )
        )
        db.commit()

    for scope in ("ancestors", "lineage"):
        body = _assert_matches_export(client_oidc, _out(r1), scope)
        assert b_upload not in {n["id"] for n in body["graph"]["nodes"]}
        assert body["omitted_input_count"] == 1

    login_as(client_oidc, "b@example.com", name="B")
    response = client_oidc.get(
        f"/api/assets/{_out(r1)}/export/preview", params={"include_graph": "true"}
    )
    assert response.status_code == 404
