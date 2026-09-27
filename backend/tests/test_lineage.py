"""系列グラフ GET /api/assets/{id}/lineage(ADR-0009)。

generate → edit(マスク付き) → edit という連鎖を作り、中間の Asset を起点に
祖先・子孫・主たる親(primary)・マスクの入力エッジ・失敗した Run の含有・深さ上限・
存在しない ID の 404 を確認する。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import make_png_bytes, wait_for_run_terminal

_SIZE = "1024x1024"
_DIM = 1024


def _upload(client: TestClient, kind: str = "upload") -> str:
    data = make_png_bytes(width=_DIM, height=_DIM)
    response = client.post(
        "/api/assets",
        files={"file": ("f.png", data, "image/png")},
        data={"kind": kind},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _generate(client: TestClient, prompt: str = "root generate") -> tuple[str, str]:
    response = client.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": "gpt-image-2.5-sunburst",
            "prompt": prompt,
            "params": {"n": 1, "size": _SIZE},
        },
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]
    detail = wait_for_run_terminal(client, run_id)
    assert detail["status"] == "succeeded", detail
    return run_id, detail["outputs"][0]["asset_id"]


def _edit(
    client: TestClient, base_asset_id: str, mask_asset_id: str | None, prompt: str
) -> tuple[str, dict]:
    inputs = [{"asset_id": base_asset_id, "role": "image", "position": 0}]
    if mask_asset_id is not None:
        inputs.append({"asset_id": mask_asset_id, "role": "mask", "position": 0})
    response = client.post(
        "/api/runs",
        json={
            "operation": "edit",
            "model": "gpt-image-2.5-sunburst",
            "prompt": prompt,
            "params": {"n": 1},
            "inputs": inputs,
        },
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]
    detail = wait_for_run_terminal(client, run_id)
    return run_id, detail


class _Chain:
    """テスト用の連鎖。

    generate(root) -> A -> edit(mask) -> B -> edit -> C, B から failed も生やす。
    """

    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.generate_run_id, self.asset_a_id = _generate(client, "generate for lineage test")
        self.mask_id = _upload(client, kind="mask")
        self.edit1_run_id, edit1_detail = _edit(
            client, self.asset_a_id, self.mask_id, "edit A with mask"
        )
        assert edit1_detail["status"] == "succeeded", edit1_detail
        self.asset_b_id = edit1_detail["outputs"][0]["asset_id"]

        self.edit2_run_id, edit2_detail = _edit(client, self.asset_b_id, None, "edit B again")
        assert edit2_detail["status"] == "succeeded", edit2_detail
        self.asset_c_id = edit2_detail["outputs"][0]["asset_id"]

        self.failed_run_id, failed_detail = _edit(
            client, self.asset_b_id, None, "[[fail:contentFilter]] edit B that fails"
        )
        assert failed_detail["status"] == "failed", failed_detail


def _get_lineage(client: TestClient, asset_id: str, **params) -> dict:
    response = client.get(f"/api/assets/{asset_id}/lineage", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_lineage_default_depths_includes_full_chain(client: TestClient) -> None:
    chain = _Chain(client)

    body = _get_lineage(client, chain.asset_b_id)
    assert body["root_asset_id"] == chain.asset_b_id
    assert body["truncated"] is False

    nodes_by_id = {n["id"]: n for n in body["nodes"]}

    expected_ids = {
        chain.asset_b_id,
        chain.edit1_run_id,
        chain.asset_a_id,
        chain.mask_id,
        chain.generate_run_id,
        chain.edit2_run_id,
        chain.asset_c_id,
        chain.failed_run_id,
    }
    assert expected_ids <= nodes_by_id.keys()

    # 起点
    assert nodes_by_id[chain.asset_b_id]["type"] == "asset"
    assert nodes_by_id[chain.asset_b_id]["depth"] == 0

    # 祖先方向の深さ
    assert nodes_by_id[chain.edit1_run_id]["depth"] == -1
    assert nodes_by_id[chain.asset_a_id]["depth"] == -2
    assert nodes_by_id[chain.mask_id]["depth"] == -2
    assert nodes_by_id[chain.generate_run_id]["depth"] == -3

    # 子孫方向の深さ
    assert nodes_by_id[chain.edit2_run_id]["depth"] == 1
    assert nodes_by_id[chain.failed_run_id]["depth"] == 1
    assert nodes_by_id[chain.asset_c_id]["depth"] == 2

    # 失敗した Run も含まれ、status が failed のまま出る。
    assert nodes_by_id[chain.failed_run_id]["run"]["status"] == "failed"
    assert nodes_by_id[chain.failed_run_id]["run"]["error_code"] == "contentFilter"

    # 論理削除フラグ(このテストでは削除していないので False)。
    assert nodes_by_id[chain.asset_a_id]["deleted"] is False


def test_lineage_edges_mark_primary_parent_and_mask(client: TestClient) -> None:
    chain = _Chain(client)
    body = _get_lineage(client, chain.asset_b_id)

    def find_edge(source: str, target: str, kind: str) -> dict:
        matches = [
            e
            for e in body["edges"]
            if e["source"] == source and e["target"] == target and e["kind"] == kind
        ]
        assert len(matches) == 1, (source, target, kind, body["edges"])
        return matches[0]

    # A -> edit1 は role=image, position=0 の「主たる親」。
    image_edge = find_edge(chain.asset_a_id, chain.edit1_run_id, "input")
    assert image_edge["role"] == "image"
    assert image_edge["position"] == 0
    assert image_edge["primary"] is True

    # mask -> edit1 は role=mask で、primary ではない。
    mask_edge = find_edge(chain.mask_id, chain.edit1_run_id, "input")
    assert mask_edge["role"] == "mask"
    assert mask_edge["primary"] is False

    # edit1 -> B は output エッジ。
    output_edge = find_edge(chain.edit1_run_id, chain.asset_b_id, "output")
    assert output_edge["primary"] is False

    # B -> edit2 も主たる親のエッジ。
    b_to_edit2 = find_edge(chain.asset_b_id, chain.edit2_run_id, "input")
    assert b_to_edit2["primary"] is True

    # 失敗した Run には出力エッジが無い。
    failed_output_edges = [e for e in body["edges"] if e["source"] == chain.failed_run_id]
    assert failed_output_edges == []


def test_lineage_up_limit_stops_before_grandparent(client: TestClient) -> None:
    chain = _Chain(client)
    body = _get_lineage(client, chain.asset_b_id, up=1, down=0)

    node_ids = {n["id"] for n in body["nodes"]}
    assert chain.asset_b_id in node_ids
    assert chain.edit1_run_id in node_ids
    # up=1 は「起点を生んだRunまで」。その入力(A/mask)までは辿らない。
    assert chain.asset_a_id not in node_ids
    assert chain.mask_id not in node_ids
    assert chain.generate_run_id not in node_ids


def test_lineage_down_limit_stops_before_grandchild(client: TestClient) -> None:
    chain = _Chain(client)
    body = _get_lineage(client, chain.asset_b_id, up=0, down=1)

    node_ids = {n["id"] for n in body["nodes"]}
    assert chain.asset_b_id in node_ids
    # down=1 は「Bを使ったRunまで」。その出力(C)までは辿らない。
    assert chain.edit2_run_id in node_ids
    assert chain.failed_run_id in node_ids
    assert chain.asset_c_id not in node_ids


def test_lineage_asset_nodes_expose_restorable(client: TestClient) -> None:
    chain = _Chain(client)

    # 削除前は restorable が false(未削除なので復元の対象ではない)。
    body_before = _get_lineage(client, chain.asset_b_id)
    nodes_before = {n["id"]: n for n in body_before["nodes"]}
    assert nodes_before[chain.asset_a_id]["asset"]["restorable"] is False

    # asset_a_id を個別に削除すると、生んだRun(generate_run_id)は生きているので復元できる。
    assert client.delete(f"/api/assets/{chain.asset_a_id}").status_code == 204
    body_after_asset_delete = _get_lineage(client, chain.asset_b_id)
    nodes_after = {n["id"]: n for n in body_after_asset_delete["nodes"]}
    assert nodes_after[chain.asset_a_id]["deleted"] is True
    assert nodes_after[chain.asset_a_id]["asset"]["restorable"] is True

    # C の生みの親(edit2_run_id)を削除すると、C は復元できなくなる。
    assert client.delete(f"/api/runs/{chain.edit2_run_id}").status_code == 204
    body_after_run_delete = _get_lineage(client, chain.asset_b_id)
    nodes_after_run = {n["id"]: n for n in body_after_run_delete["nodes"]}
    assert nodes_after_run[chain.asset_c_id]["deleted"] is True
    assert nodes_after_run[chain.asset_c_id]["asset"]["restorable"] is False


def test_lineage_ancestors_include_sibling_outputs(client: TestClient) -> None:
    """n=3 の Run の出力の1つを起点にしても、他の2つの兄弟出力が depth 0 で出て、
    Run から各出力への output エッジがちょうど1本ずつあること(取りこぼしの再現)。
    """
    response = client.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": "gpt-image-2.5-sunburst",
            "prompt": "sibling outputs for lineage test",
            "params": {"n": 3, "size": _SIZE},
        },
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]
    detail = wait_for_run_terminal(client, run_id)
    assert detail["status"] == "succeeded", detail
    output_asset_ids = {o["asset_id"] for o in detail["outputs"]}
    assert len(output_asset_ids) == 3

    root_asset_id = detail["outputs"][0]["asset_id"]
    body = _get_lineage(client, root_asset_id)

    nodes_by_id = {n["id"]: n for n in body["nodes"]}
    assert output_asset_ids <= nodes_by_id.keys()
    for asset_id in output_asset_ids:
        assert nodes_by_id[asset_id]["type"] == "asset"
        assert nodes_by_id[asset_id]["depth"] == 0

    output_edges = [e for e in body["edges"] if e["source"] == run_id and e["kind"] == "output"]
    assert {e["target"] for e in output_edges} == output_asset_ids
    # 重複していない(取りこぼしの逆、二重にもならない)ことを確認する。
    assert len(output_edges) == 3


def test_lineage_unknown_asset_returns_404(client: TestClient) -> None:
    response = client.get("/api/assets/00000000-0000-0000-0000-000000000000/lineage")
    assert response.status_code == 404


def test_lineage_query_params_are_bounded(client: TestClient) -> None:
    chain = _Chain(client)
    assert (
        client.get(f"/api/assets/{chain.asset_b_id}/lineage", params={"up": 256}).status_code == 422
    )
    assert (
        client.get(f"/api/assets/{chain.asset_b_id}/lineage", params={"down": -1}).status_code
        == 422
    )


# -- 上描きスケッチの source_asset_id 辺(ADR-0010、2026-09-23 追記) --------------------


def _find_edge(edges: list[dict], source: str, target: str, kind: str) -> dict:
    matches = [
        e for e in edges if e["source"] == source and e["target"] == target and e["kind"] == kind
    ]
    assert len(matches) == 1, (source, target, kind, edges)
    return matches[0]


class _SketchChain:
    """base(upload) -> sketch(source_asset_id=base) -> edit(sketch を主たる入力) -> output。"""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.base_id = _upload(client)

        sketch_response = client.post(
            "/api/assets",
            files={"file": ("sketch.png", make_png_bytes(width=_DIM, height=_DIM), "image/png")},
            data={"kind": "sketch", "source_asset_id": self.base_id},
        )
        assert sketch_response.status_code == 201, sketch_response.text
        self.sketch_id = sketch_response.json()["id"]

        self.edit_run_id, edit_detail = _edit(client, self.sketch_id, None, "edit from sketch")
        assert edit_detail["status"] == "succeeded", edit_detail
        self.output_id = edit_detail["outputs"][0]["asset_id"]


def test_lineage_ancestors_follow_sketch_source_edge(client: TestClient) -> None:
    chain = _SketchChain(client)

    body = _get_lineage(client, chain.output_id)
    nodes_by_id = {n["id"]: n for n in body["nodes"]}
    assert {
        chain.output_id,
        chain.edit_run_id,
        chain.sketch_id,
        chain.base_id,
    } <= nodes_by_id.keys()

    assert nodes_by_id[chain.sketch_id]["asset"]["kind"] == "sketch"
    assert nodes_by_id[chain.sketch_id]["depth"] == -2
    # sketch_source 辺は Run を介さない直接の辺なので、sketch の1つ祖先で base に届く。
    assert nodes_by_id[chain.base_id]["depth"] == -3

    image_edge = _find_edge(body["edges"], chain.sketch_id, chain.edit_run_id, "input")
    assert image_edge["role"] == "image"
    assert image_edge["primary"] is True

    sketch_edge = _find_edge(body["edges"], chain.base_id, chain.sketch_id, "sketch_source")
    assert sketch_edge["primary"] is True


def test_lineage_descendants_follow_sketch_source_edge(client: TestClient) -> None:
    chain = _SketchChain(client)

    body = _get_lineage(client, chain.base_id)
    nodes_by_id = {n["id"]: n for n in body["nodes"]}
    assert {
        chain.base_id,
        chain.sketch_id,
        chain.edit_run_id,
        chain.output_id,
    } <= nodes_by_id.keys()

    assert nodes_by_id[chain.sketch_id]["depth"] == 1
    assert nodes_by_id[chain.edit_run_id]["depth"] == 2
    assert nodes_by_id[chain.output_id]["depth"] == 3

    sketch_edge = _find_edge(body["edges"], chain.base_id, chain.sketch_id, "sketch_source")
    assert sketch_edge["primary"] is True

    image_edge = _find_edge(body["edges"], chain.sketch_id, chain.edit_run_id, "input")
    assert image_edge["primary"] is True


def test_lineage_from_sketch_asset_itself_reaches_both_directions(client: TestClient) -> None:
    chain = _SketchChain(client)

    body = _get_lineage(client, chain.sketch_id)
    nodes_by_id = {n["id"]: n for n in body["nodes"]}
    assert chain.base_id in nodes_by_id
    assert nodes_by_id[chain.base_id]["depth"] == -1
    assert chain.edit_run_id in nodes_by_id
    assert chain.output_id in nodes_by_id


# -- 未使用スケッチの再編集は系列に旧ノードを残さない(ADR-0010、2026-09-25 追記) -----------


def test_lineage_descendants_skip_sketch_replaced_while_unused(client: TestClient) -> None:
    base_id = _upload(client)
    sketch_response = client.post(
        "/api/assets",
        files={"file": ("sketch.png", make_png_bytes(width=_DIM, height=_DIM), "image/png")},
        data={"kind": "sketch", "source_asset_id": base_id},
    )
    sketch_id = sketch_response.json()["id"]

    replace_response = client.post(
        "/api/assets",
        files={"file": ("sketch2.png", make_png_bytes(width=_DIM, height=_DIM), "image/png")},
        data={"kind": "sketch", "replaces_asset_id": sketch_id},
    )
    assert replace_response.status_code == 201, replace_response.text
    replacement_id = replace_response.json()["id"]

    body = _get_lineage(client, base_id)
    node_ids = {n["id"] for n in body["nodes"]}
    assert sketch_id not in node_ids
    assert replacement_id in node_ids

    # 起点(root)自身が置き換えられて消えたスケッチでも、そのままノードとして出る。
    root_body = _get_lineage(client, sketch_id)
    assert root_body["nodes"][0]["id"] == sketch_id
    assert root_body["nodes"][0]["deleted"] is True


def test_lineage_descendants_keeps_sketch_replaced_while_used(client: TestClient) -> None:
    sketch_response = client.post(
        "/api/assets",
        files={"file": ("sketch.png", make_png_bytes(width=_DIM, height=_DIM), "image/png")},
        data={"kind": "sketch"},
    )
    sketch_id = sketch_response.json()["id"]
    _edit(client, sketch_id, None, "edit from sketch, used before replace")

    replace_response = client.post(
        "/api/assets",
        files={"file": ("sketch2.png", make_png_bytes(width=_DIM, height=_DIM), "image/png")},
        data={"kind": "sketch", "replaces_asset_id": sketch_id},
    )
    assert replace_response.status_code == 201, replace_response.text
    replacement_id = replace_response.json()["id"]

    # 使用済みのスケッチは証跡なので削除されず、系列にも残る(置き換えは source_asset_id の連鎖)。
    body = _get_lineage(client, sketch_id)
    node_ids = {n["id"] for n in body["nodes"]}
    assert sketch_id in node_ids
    assert replacement_id in node_ids
    sketch_edge = _find_edge(body["edges"], sketch_id, replacement_id, "sketch_source")
    assert sketch_edge["primary"] is True
