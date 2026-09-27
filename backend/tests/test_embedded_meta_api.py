"""ADR-0014 の API 経路: ダウンロード時の埋め込みと、アップロード時の照合。

`variant=original&download=1` の PNG にだけ `gakei` チャンクが付き、画面表示用の
original(download=0)は変わらないこと、再アップロードで既存 Asset に一致する/しない
ケース、系列グラフに `origin` エッジが出ることを確認する。実 API は呼ばない(FAKE_PROVIDER=1)。
"""

from __future__ import annotations

import io
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.domain.embedded_meta import embed_gakei_chunk, read_gakei_meta, strip_gakei_chunk
from tests.conftest import make_png_bytes, wait_for_run_terminal


def _upload(client: TestClient, kind: str = "upload", data: bytes | None = None) -> dict:
    if data is None:
        data = make_png_bytes()
    response = client.post(
        "/api/assets",
        files={"file": ("f.png", data, "image/png")},
        data={"kind": kind},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _generate(client: TestClient) -> str:
    response = client.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": "gpt-image-2.5-sunburst",
            "prompt": "ADR-0014 埋め込みテスト用",
            "params": {"n": 1, "size": "1024x1024"},
        },
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]
    detail = wait_for_run_terminal(client, run_id)
    assert detail["status"] == "succeeded", detail
    return detail["outputs"][0]["asset_id"]


def _download_original(client: TestClient, asset_id: str, *, embed: bool) -> bytes:
    params = {"variant": "original", "download": 1 if embed else 0}
    response = client.get(f"/api/assets/{asset_id}/content", params=params)
    assert response.status_code == 200, response.text
    return response.content


def _edit(client: TestClient, base_asset_id: str, prompt: str) -> str:
    response = client.post(
        "/api/runs",
        json={
            "operation": "edit",
            "model": "gpt-image-2.5-sunburst",
            "prompt": prompt,
            "params": {"n": 1},
            "inputs": [{"asset_id": base_asset_id, "role": "image", "position": 0}],
        },
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]
    detail = wait_for_run_terminal(client, run_id)
    assert detail["status"] == "succeeded", detail
    return detail["outputs"][0]["asset_id"]


def _get_lineage(client: TestClient, asset_id: str, **params) -> dict:
    response = client.get(f"/api/assets/{asset_id}/lineage", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _second_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str) -> TestClient:
    """別の `DATA_DIR`(= 別の GAKEI インスタンス)で動く、もう1つのアプリ。

    `create_app()` 自体は env を読まず、`TestClient.__enter__`(lifespan の起動)で初めて
    `get_settings()` が呼ばれるので、呼び出し直前に `DATA_DIR` を差し替えればよい
    (`tests/conftest.py` の `client` フィクスチャと同じ組み立て方)。
    """
    monkeypatch.setenv("DATA_DIR", str(tmp_path / name))
    monkeypatch.setenv("FAKE_PROVIDER", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    from app.main import create_app

    app = create_app()
    return TestClient(app)


def test_download_with_download_flag_embeds_meta_with_asset_and_run_info(
    client: TestClient,
) -> None:
    asset_id = _generate(client)
    embedded = _download_original(client, asset_id, embed=True)

    meta = read_gakei_meta(embedded)
    assert meta is not None
    assert meta["schema"] == "gakei.lineage/2"
    assert meta["root"] == asset_id
    nodes_by_id = {n["id"]: n for n in meta["nodes"]}
    assert nodes_by_id[asset_id]["type"] == "asset"
    run_node = next(n for n in meta["nodes"] if n["type"] == "run")
    assert run_node["operation"] == "generate"
    assert run_node["model"] == "gpt-image-2.5-sunburst"
    assert run_node["prompt"] == "ADR-0014 埋め込みテスト用"

    etag = client.get(
        f"/api/assets/{asset_id}/content", params={"variant": "original", "download": 1}
    ).headers["etag"]
    assert etag.endswith('-original-gakei1"')


def test_download_without_download_flag_is_byte_identical_to_stored_original(
    client: TestClient,
) -> None:
    asset_id = _generate(client)
    plain = _download_original(client, asset_id, embed=False)
    embedded = _download_original(client, asset_id, embed=True)

    assert read_gakei_meta(plain) is None
    assert strip_gakei_chunk(embedded) == plain
    # 表示用の original は変わらない(繰り返し取得しても同じバイト列)。
    assert _download_original(client, asset_id, embed=False) == plain


def test_reupload_of_downloaded_file_matches_existing_asset(client: TestClient) -> None:
    asset_id = _generate(client)
    embedded = _download_original(client, asset_id, embed=True)

    before = client.get("/api/assets").json()["items"]

    result = _upload(client, kind="upload", data=embedded)
    assert result["ingest_outcome"] == "matched_existing"
    assert result["id"] == asset_id

    after = client.get("/api/assets").json()["items"]
    assert len(after) == len(before)  # 新しい行は増えない


def test_reupload_with_one_pixel_changed_creates_new_asset_with_origin(
    client: TestClient,
) -> None:
    asset_id = _generate(client)
    embedded = _download_original(client, asset_id, embed=True)
    meta = read_gakei_meta(embedded)
    assert meta is not None

    # 画素を1つ変える(gakei チャンクは付けずに再エンコードしてから、読み取った meta を
    # そのまま埋め込み直す = 中身は改変されたが由来を自称する偽造 PNG)。
    with Image.open(io.BytesIO(embedded)) as image:
        image = image.convert("RGB")
        original_pixel = image.getpixel((0, 0))
        new_pixel = (255, 0, 0) if original_pixel != (255, 0, 0) else (0, 255, 0)
        image.putpixel((0, 0), new_pixel)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        modified = buffer.getvalue()
    forged = embed_gakei_chunk(modified, meta)

    result = _upload(client, kind="upload", data=forged)
    assert result["ingest_outcome"] == "created"
    assert result["id"] != asset_id
    assert result["origin"] is not None
    assert result["origin"]["asset_id"] == asset_id
    assert result["origin"]["same_instance"] is True
    assert result["origin"]["verified"] is False

    lineage = client.get(f"/api/assets/{result['id']}/lineage").json()
    origin_edges = [e for e in lineage["edges"] if e["kind"] == "origin"]
    assert any(e["source"] == asset_id and e["target"] == result["id"] for e in origin_edges)
    assert all(e["primary"] is False for e in origin_edges)


def test_reupload_pointing_to_deleted_asset_creates_new_asset_with_origin(
    client: TestClient,
) -> None:
    asset_id = _generate(client)
    embedded = _download_original(client, asset_id, embed=True)

    delete_response = client.delete(f"/api/assets/{asset_id}")
    assert delete_response.status_code == 204

    result = _upload(client, kind="upload", data=embedded)
    assert result["ingest_outcome"] == "created"
    assert result["id"] != asset_id
    assert result["origin"]["asset_id"] == asset_id
    assert result["origin"]["same_instance"] is True


def test_reupload_with_different_instance_id_is_not_matched(client: TestClient) -> None:
    asset_id = _generate(client)
    embedded = _download_original(client, asset_id, embed=True)
    meta = read_gakei_meta(embedded)
    assert meta is not None
    meta["instance"] = str(uuid.uuid4())  # 別インスタンスを自称させる
    foreign = embed_gakei_chunk(strip_gakei_chunk(embedded), meta)

    result = _upload(client, kind="upload", data=foreign)
    assert result["ingest_outcome"] == "created"
    assert result["id"] != asset_id
    assert result["origin"]["asset_id"] is None
    assert result["origin"]["same_instance"] is False


def test_upload_with_mask_kind_is_never_matched_even_with_identical_content(
    client: TestClient,
) -> None:
    asset_id = _generate(client)
    embedded = _download_original(client, asset_id, embed=True)

    result = _upload(client, kind="mask", data=embedded)
    assert result["ingest_outcome"] == "created"
    assert result["id"] != asset_id
    assert result["kind"] == "mask"


# -- 系列グラフでの埋め込み(未検証)ノード(ADR-0014 6章、2026-09-24 追記) ------------------


def test_lineage_api_shows_embedded_ancestor_nodes_and_origin_edge_for_foreign_v2_upload(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # instance A: generate(P) -> edit(C)。C をダウンロードして別インスタンスに取り込む。
    p_asset_id = _generate(client)
    c_asset_id = _edit(client, p_asset_id, "A: edit to C")
    c_bytes = _download_original(client, c_asset_id, embed=True)

    client_b = _second_client(monkeypatch, tmp_path, "instance_b")
    with client_b:
        result = _upload(client_b, kind="upload", data=c_bytes)
        assert result["ingest_outcome"] == "created"
        assert result["origin"]["same_instance"] is False
        y_id = result["id"]

        body = _get_lineage(client_b, y_id)
        nodes_by_id = {n["id"]: n for n in body["nodes"]}

        # Y は C の書き出しを加工せずに取り込んだもの(origin_ref_asset_id == C)なので、
        # C(埋め込みグラフの root)のノードは作らず、Y が C の位置に入る(同じ画像を
        # 2つのノードに分けない)。C を生んだ Run の output の辺は Y につながる。
        assert c_asset_id not in nodes_by_id
        assert nodes_by_id[y_id]["embedded"] is False

        run_e_nodes = [
            n
            for n in body["nodes"]
            if n.get("embedded") and n["type"] == "run" and n["depth"] == -1
        ]
        assert len(run_e_nodes) == 1
        run_e = run_e_nodes[0]
        assert run_e["run"]["prompt"] == "A: edit to C"
        assert run_e["run"]["operation"] == "edit"
        assert any(
            e["kind"] == "output" and e["source"] == run_e["id"] and e["target"] == y_id
            for e in body["edges"]
        )

        assert nodes_by_id[p_asset_id]["embedded"] is True
        assert nodes_by_id[p_asset_id]["depth"] == -2

        run_g_nodes = [
            n
            for n in body["nodes"]
            if n.get("embedded") and n["type"] == "run" and n["depth"] == -3
        ]
        assert len(run_g_nodes) == 1
        assert run_g_nodes[0]["run"]["operation"] == "generate"

        assert [e for e in body["edges"] if e["kind"] == "origin"] == []


def test_lineage_api_up_param_bounds_embedded_ancestor_expansion(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    p_asset_id = _generate(client)
    c_asset_id = _edit(client, p_asset_id, "A: edit to C")
    c_bytes = _download_original(client, c_asset_id, embed=True)

    client_b = _second_client(monkeypatch, tmp_path, "instance_b")
    with client_b:
        result = _upload(client_b, kind="upload", data=c_bytes)
        y_id = result["id"]

        body = _get_lineage(client_b, y_id, up=1, down=0)
        node_ids = {n["id"] for n in body["nodes"]}
        assert y_id in node_ids
        # 加工せずに取り込んだので C のノードは作らない(Y が C の位置に入る)。
        assert c_asset_id not in node_ids
        # up=1 は Y を生んだ(埋め込みの)Run まで。その入力 P は出ない。
        run_nodes = [n for n in body["nodes"] if n["type"] == "run"]
        assert len(run_nodes) == 1
        assert run_nodes[0]["embedded"] is True
        assert p_asset_id not in node_ids


def test_lineage_api_resolves_embedded_asset_node_to_locally_imported_unmodified_parent(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    p_asset_id = _generate(client)
    c_asset_id = _edit(client, p_asset_id, "A: edit to C")
    c_bytes = _download_original(client, c_asset_id, embed=True)
    p_bytes = _download_original(client, p_asset_id, embed=True)

    client_b = _second_client(monkeypatch, tmp_path, "instance_b")
    with client_b:
        # P の書き出しファイルを(改変せず)先に取り込んでおく。
        z_result = _upload(client_b, kind="upload", data=p_bytes)
        assert z_result["ingest_outcome"] == "created"
        z_id = z_result["id"]

        y_result = _upload(client_b, kind="upload", data=c_bytes)
        y_id = y_result["id"]

        body = _get_lineage(client_b, y_id)
        nodes_by_id = {n["id"]: n for n in body["nodes"]}
        assert nodes_by_id[p_asset_id]["embedded"] is True
        assert nodes_by_id[p_asset_id]["resolved_asset_id"] == z_id


def test_lineage_api_does_not_resolve_embedded_asset_node_for_modified_parent_file(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    p_asset_id = _generate(client)
    c_asset_id = _edit(client, p_asset_id, "A: edit to C")
    c_bytes = _download_original(client, c_asset_id, embed=True)
    p_bytes = _download_original(client, p_asset_id, embed=True)
    p_meta = read_gakei_meta(p_bytes)
    assert p_meta is not None

    with Image.open(io.BytesIO(p_bytes)) as image:
        image = image.convert("RGB")
        original_pixel = image.getpixel((0, 0))
        new_pixel = (255, 0, 0) if original_pixel != (255, 0, 0) else (0, 255, 0)
        image.putpixel((0, 0), new_pixel)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        modified = buffer.getvalue()
    forged_p_bytes = embed_gakei_chunk(modified, p_meta)

    client_b = _second_client(monkeypatch, tmp_path, "instance_b")
    with client_b:
        w_result = _upload(client_b, kind="upload", data=forged_p_bytes)
        assert w_result["ingest_outcome"] == "created"

        y_result = _upload(client_b, kind="upload", data=c_bytes)
        y_id = y_result["id"]

        body = _get_lineage(client_b, y_id)
        nodes_by_id = {n["id"]: n for n in body["nodes"]}
        assert nodes_by_id[p_asset_id]["embedded"] is True
        assert nodes_by_id[p_asset_id]["resolved_asset_id"] is None


def test_lineage_api_v1_meta_still_shown_as_embedded_nodes(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """旧バージョン(v1)を埋め込んだ画像を取り込んでも、埋め込み(未検証)ノードとして
    系列グラフに出ること(6章: 読み取りは v1 にも対応)。
    """
    asset_id = _generate(client)
    embedded = _download_original(client, asset_id, embed=True)
    meta = read_gakei_meta(embedded)
    assert meta is not None
    assert meta["schema"] == "gakei.lineage/2"

    # v1 相当のペイロードに手で作り替える(v1 を埋め込んだ古い GAKEI からの書き出しを模す)。
    run_node = next(n for n in meta["nodes"] if n["type"] == "run")
    v1_meta = {
        "schema": "gakei.lineage/1",
        "instance": meta["instance"],
        "asset": {
            "id": asset_id,
            "sha256": next(n for n in meta["nodes"] if n["id"] == asset_id)["sha256"],
            "kind": "generated",
            "output_index": 0,
            "created_at": next(n for n in meta["nodes"] if n["id"] == asset_id)["created_at"],
        },
        "run": {
            "id": run_node["id"],
            "provider": run_node["provider"],
            "model": run_node["model"],
            "operation": run_node["operation"],
            "prompt": run_node["prompt"],
            "params": run_node["params"],
            "finished_at": run_node["finished_at"],
        },
    }
    v1_embedded = embed_gakei_chunk(strip_gakei_chunk(embedded), v1_meta)

    client_b = _second_client(monkeypatch, tmp_path, "instance_b")
    with client_b:
        result = _upload(client_b, kind="upload", data=v1_embedded)
        y_id = result["id"]

        body = _get_lineage(client_b, y_id)
        nodes_by_id = {n["id"]: n for n in body["nodes"]}
        # 加工せずに取り込んだので、root(asset_id)のノードは作らず Y がその位置に入る。
        assert asset_id not in nodes_by_id
        run_nodes = [n for n in body["nodes"] if n["type"] == "run" and n.get("embedded")]
        assert len(run_nodes) == 1
        assert run_nodes[0]["depth"] == -1
        assert run_nodes[0]["run"]["prompt"] == "ADR-0014 埋め込みテスト用"


def test_lineage_api_embedded_comfyui_run_shows_workflow_name_as_model_label(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ComfyUI の Run は `model` がワークフローの id なので、系列グラフのノードには
    `params.comfyui_workflow.name` を `model_label` として出す(ADR-0013)。
    埋め込み(未検証)の Run ノードでも同じ。ComfyUI 以外の Run は None のまま。
    """
    asset_id = _generate(client)
    embedded = _download_original(client, asset_id, embed=True)
    meta = read_gakei_meta(embedded)
    assert meta is not None

    # 生成元が ComfyUI だった場合を模す(Fake プロバイダーの出力を書き換える)。
    workflow_id = str(uuid.uuid4())
    run_node = next(n for n in meta["nodes"] if n["type"] == "run")
    run_node["provider"] = "comfyui"
    run_node["model"] = workflow_id
    run_node["params"] = {
        "comfyui_workflow": {
            "id": workflow_id,
            "name": "Qwen Image edit",
            "template_sha256": "0" * 64,
        },
        "comfyui_seed": 1,
    }
    comfy_embedded = embed_gakei_chunk(strip_gakei_chunk(embedded), meta)

    client_b = _second_client(monkeypatch, tmp_path, "instance_b")
    with client_b:
        comfy_id = _upload(client_b, kind="upload", data=comfy_embedded)["id"]
        plain_id = _upload(client_b, kind="upload", data=embedded)["id"]
        comfy_body = _get_lineage(client_b, comfy_id)
        plain_body = _get_lineage(client_b, plain_id)

    comfy_run = next(n for n in comfy_body["nodes"] if n["type"] == "run")
    assert comfy_run["embedded"] is True
    assert comfy_run["run"]["model"] == workflow_id
    assert comfy_run["run"]["model_label"] == "Qwen Image edit"

    plain_run = next(n for n in plain_body["nodes"] if n["type"] == "run")
    assert plain_run["run"]["model_label"] is None


def test_unmodified_import_is_not_duplicated_across_three_instances(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A で作った C を B に加工せずに取り込み(Y)、B で編集した Z を C 環境に取り込む。
    書き出しでは Y が C の位置に入り(`origin_ref` に C を残す)、どの環境の系列グラフにも
    同じ画像のノードが2つ並ばない。C 環境で A の C も取り込めば、Y のノードに対応付く。
    """
    p_asset_id = _generate(client)
    c_asset_id = _edit(client, p_asset_id, "A: edit to C")
    c_bytes = _download_original(client, c_asset_id, embed=True)

    client_b = _second_client(monkeypatch, tmp_path, "instance_b")
    with client_b:
        y_id = _upload(client_b, kind="upload", data=c_bytes)["id"]
        z_id = _edit(client_b, y_id, "B: edit Y to Z")
        z_bytes = _download_original(client_b, z_id, embed=True)

    meta = read_gakei_meta(z_bytes)
    assert meta is not None
    meta_nodes = {n["id"]: n for n in meta["nodes"]}
    assert c_asset_id not in meta_nodes
    assert meta_nodes[y_id]["origin_ref"]["id"] == c_asset_id
    assert meta_nodes[y_id]["origin_ref"]["instance"] != meta_nodes[y_id]["instance"]
    assert all(e["kind"] != "origin" for e in meta["edges"])
    # A の Run(C を生んだ)の output の辺は Y につながる。
    a_edit_run = next(
        n for n in meta["nodes"] if n["type"] == "run" and n.get("prompt") == "A: edit to C"
    )
    assert any(
        e["kind"] == "output" and e["source"] == a_edit_run["id"] and e["target"] == y_id
        for e in meta["edges"]
    )

    client_c = _second_client(monkeypatch, tmp_path, "instance_c")
    with client_c:
        z_local = _upload(client_c, kind="upload", data=z_bytes)["id"]
        body = _get_lineage(client_c, z_local)
        nodes_by_id = {n["id"]: n for n in body["nodes"]}
        # Z は Z' として取り込まれ(Z のノードは作らない)、Y と P は埋め込みノード、C は無い。
        assert z_id not in nodes_by_id
        assert c_asset_id not in nodes_by_id
        assert nodes_by_id[y_id]["embedded"] is True
        assert nodes_by_id[p_asset_id]["embedded"] is True
        assert [e for e in body["edges"] if e["kind"] == "origin"] == []
        assert nodes_by_id[y_id]["resolved_asset_id"] is None

        # A の C も取り込むと、Y のノード(origin_ref = C)がその画像に対応付く。
        c_local = _upload(client_c, kind="upload", data=c_bytes)["id"]
        body = _get_lineage(client_c, z_local)
        nodes_by_id = {n["id"]: n for n in body["nodes"]}
        assert nodes_by_id[y_id]["resolved_asset_id"] == c_local
