"""画像から生成の設定を読み込む(`POST /api/image-settings/import`。ADR-0038 11章)。

- GAKEI の埋め込み(`gakei` チャンク)から Run のレシピを読む。サーバー専有のキーを除き、
  実際の seed を `seed` に戻す。
- ComfyUI の `prompt` チャンクを登録済みワークフローと照合し、束縛と公開パラメーターの値を読む。
  合うものが無ければ 422。GAKEI の ComfyUI の Run でワークフローがこの環境に無いときも照合する。
- A1111 形式は SD WebUI の対応付け(9章)。接続していなければ 409。
- 画像を保存しない(Asset が増えない)。

ComfyUI と SD WebUI には接続しない。モデル名などはすべて架空のもの。
"""

from __future__ import annotations

import io
import json
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image, PngImagePlugin
from sqlalchemy import func, select

from app.domain.comfy_workflow import Bindings, ExposedParam, InputRef, compute_template_sha256
from app.domain.embedded_meta import embed_gakei_chunk
from app.domain.models import Asset, ComfyWorkflow
from tests.comfyui_graphs import T2I_GRAPH, clone
from tests.sdwebui_fake import FakeSdWebui, install_fake_factories

PATH = "/api/image-settings/import"
INSTANCE = "11111111-1111-1111-1111-111111111111"
ROOT = "22222222-2222-2222-2222-222222222222"
RUN = "33333333-3333-3333-3333-333333333333"


def _png(texts: dict[str, str] | None = None) -> bytes:
    image = Image.new("RGB", (16, 16), (40, 80, 120))
    info = PngImagePlugin.PngInfo()
    for key, value in (texts or {}).items():
        info.add_text(key, value)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", pnginfo=info)
    return buffer.getvalue()


def _lineage(run: dict[str, Any] | None) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = [
        {"type": "asset", "id": ROOT, "instance": INSTANCE, "sha256": "a" * 64, "kind": "generated"}
    ]
    edges: list[dict[str, Any]] = []
    if run is not None:
        nodes.append({"type": "run", "id": RUN, "instance": INSTANCE, **run})
        edges.append({"source": RUN, "target": ROOT, "kind": "output"})
    return {
        "schema": "gakei.lineage/2",
        "instance": INSTANCE,
        "root": ROOT,
        "nodes": nodes,
        "edges": edges,
        "truncated": False,
    }


def _gakei_png(run: dict[str, Any] | None, texts: dict[str, str] | None = None) -> bytes:
    return embed_gakei_chunk(_png(texts), _lineage(run))


def _post(client: TestClient, data: bytes):  # noqa: ANN202
    return client.post(PATH, files={"file": ("x.png", data, "image/png")})


def _t2i_bindings() -> Bindings:
    return Bindings(
        prompt=InputRef(node="6", input="text"),
        negative_prompt=InputRef(node="7", input="text"),
        seed=[InputRef(node="3", input="seed")],
        width=InputRef(node="5", input="width"),
        height=InputRef(node="5", input="height"),
        outputs=["9"],
    )


def _steps_param() -> ExposedParam:
    return ExposedParam(
        name="steps", node="3", input="steps", type="int", label="Steps", minimum=1, maximum=100
    )


def _create_workflow(
    client: TestClient,
    *,
    name: str = "t2i",
    template: dict[str, Any] | None = None,
    bindings: Bindings | None = None,
    exposed: list[ExposedParam] | None = None,
) -> str:
    template = template if template is not None else clone(T2I_GRAPH)
    now = datetime.now(UTC)
    wf = ComfyWorkflow(
        name=name,
        operation="generate",
        template=template,
        bindings=(bindings or _t2i_bindings()).model_dump(mode="json"),
        exposed_params=[p.model_dump(mode="json") for p in (exposed or [])],
        template_sha256=compute_template_sha256(template),
        created_at=now,
        updated_at=now,
    )
    with client.app.state.session_factory() as db:
        db.add(wf)
        db.commit()
        return str(wf.id)


def _comfy_output_graph() -> dict[str, Any]:
    """登録済みの T2I のワークフローで実行したときの、画像に埋め込まれるグラフ。"""
    graph = clone(T2I_GRAPH)
    graph["6"]["inputs"]["text"] = "a red fox in the snow"
    graph["7"]["inputs"]["text"] = "lowres"
    graph["3"]["inputs"]["seed"] = 42
    graph["3"]["inputs"]["steps"] = 28
    graph["3"]["inputs"]["cfg"] = 5.5
    graph["5"]["inputs"]["width"] = 832
    graph["5"]["inputs"]["height"] = 1216
    graph["4"]["inputs"]["ckpt_name"] = "other-model.safetensors"
    return graph


def _asset_count(client: TestClient) -> int:
    with client.app.state.session_factory() as db:
        return db.execute(select(func.count()).select_from(Asset)).scalar_one()


# -- GAKEI ------------------------------------------------------------------------------


def test_gakei_recipe(client: TestClient) -> None:
    run = {
        "provider": "openai",
        "model": "gpt-image-x",
        "operation": "generate",
        "prompt": "a lighthouse at dusk",
        "params": {"size": "1536x1024", "quality": "low", "nested": {"a": 1}},
    }
    before = _asset_count(client)
    response = _post(client, _gakei_png(run))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "gakei"
    assert body["provider"] == "openai"
    assert body["model"] == "gpt-image-x"
    assert body["prompt"] == "a lighthouse at dusk"
    # 入れ子の値はフォームに入れない
    assert body["params"] == {"size": "1536x1024", "quality": "low"}
    assert body["notes"] == []
    assert body["software"] == "GAKEI"
    # 画像は保存しない
    assert _asset_count(client) == before


def test_gakei_edit_notes_inputs_not_restored(client: TestClient) -> None:
    run = {"provider": "openai", "model": "m", "operation": "edit", "prompt": "p", "params": {}}
    body = _post(client, _gakei_png(run)).json()
    assert [n["code"] for n in body["notes"]] == ["inputsNotRestored"]


def test_gakei_sdwebui_seed_is_restored(client: TestClient) -> None:
    run = {
        "provider": "sdwebui",
        "model": "model-a",
        "operation": "generate",
        "prompt": "p",
        "params": {"steps": 20, "sdwebui_seed": 1234, "sdwebui_task_id": "x"},
    }
    body = _post(client, _gakei_png(run)).json()
    assert body["params"] == {"steps": 20, "seed": 1234}


def test_gakei_without_run_falls_through(client: TestClient) -> None:
    """Run を持たない(アップロードの)チャンクだけなら、読める生成情報は無い。"""
    response = _post(client, _gakei_png(None))
    assert response.status_code == 422


def test_gakei_comfyui_registered_workflow(client: TestClient) -> None:
    wf_id = _create_workflow(client)
    run = {
        "provider": "comfyui",
        "model": wf_id,
        "operation": "generate",
        "prompt": "p",
        "params": {
            "steps": 30,
            "comfyui_seed": 99,
            "comfyui_workflow": {"id": wf_id, "name": "t2i"},
        },
    }
    body = _post(client, _gakei_png(run)).json()
    assert body["source"] == "gakei"
    assert body["model"] == wf_id
    assert body["params"] == {"steps": 30, "seed": 99}


def test_gakei_comfyui_unknown_workflow_matches_graph(client: TestClient) -> None:
    """他の環境のワークフローの Run は、画像のグラフで登録済みのものと照合する。"""
    wf_id = _create_workflow(client, exposed=[_steps_param()])
    run = {
        "provider": "comfyui",
        "model": str(uuid.uuid4()),
        "operation": "generate",
        "prompt": "p",
        "params": {"comfyui_workflow": {"id": "x", "name": "elsewhere"}},
    }
    data = _gakei_png(run, {"prompt": json.dumps(_comfy_output_graph())})
    body = _post(client, data).json()
    assert body["source"] == "comfyui"
    assert body["model"] == wf_id
    assert body["prompt"] == "a red fox in the snow"


def test_gakei_comfyui_unknown_workflow_without_match_is_422(client: TestClient) -> None:
    run = {
        "provider": "comfyui",
        "model": str(uuid.uuid4()),
        "operation": "generate",
        "prompt": "p",
        "params": {"comfyui_workflow": {"id": "x", "name": "elsewhere"}},
    }
    response = _post(client, _gakei_png(run))
    assert response.status_code == 422
    assert "elsewhere" in response.json()["detail"]


# -- ComfyUI ----------------------------------------------------------------------------


def test_comfyui_graph_matches_workflow(client: TestClient) -> None:
    wf_id = _create_workflow(client, exposed=[_steps_param()])
    before = _asset_count(client)
    response = _post(client, _png({"prompt": json.dumps(_comfy_output_graph())}))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "comfyui"
    assert body["provider"] == "comfyui"
    assert body["model"] == wf_id
    assert body["prompt"] == "a red fox in the snow"
    assert body["params"] == {
        "negative_prompt": "lowres",
        "seed": 42,
        "width": 832,
        "height": 1216,
        "steps": 28,
    }
    # 束縛していない入力でテンプレートと違うもの
    unapplied = {u["name"]: u["value"] for u in body["unapplied"]}
    assert unapplied == {
        "CheckpointLoaderSimple › ckpt_name": "other-model.safetensors",
        "KSampler › cfg": "5.5",
    }
    assert body["notes"] == []
    assert body["software"] == "ComfyUI"
    assert _asset_count(client) == before


def test_comfyui_picks_best_matching_workflow(client: TestClient) -> None:
    # 束縛は満たすが、ノードが少ない(一致が少ない)ワークフロー
    partial = clone(T2I_GRAPH)
    del partial["8"]
    _create_workflow(client, name="partial", template=partial)
    full_id = _create_workflow(client, name="full")
    body = _post(client, _png({"prompt": json.dumps(_comfy_output_graph())})).json()
    assert body["model"] == full_id


def test_comfyui_partial_match_has_note(client: TestClient) -> None:
    _create_workflow(client, name="t2i")
    graph = _comfy_output_graph()
    graph["20"] = {"class_type": "ImageScale", "inputs": {"image": ["8", 0]}}
    body = _post(client, _png({"prompt": json.dumps(graph)})).json()
    assert [n["code"] for n in body["notes"]] == ["workflowPartialMatch"]


def test_comfyui_bound_node_class_mismatch_is_not_matched(client: TestClient) -> None:
    _create_workflow(client)
    graph = _comfy_output_graph()
    graph["3"]["class_type"] = "KSamplerAdvanced"
    response = _post(client, _png({"prompt": json.dumps(graph)}))
    assert response.status_code == 422


def test_comfyui_without_workflows_is_422(client: TestClient) -> None:
    response = _post(client, _png({"prompt": json.dumps(_comfy_output_graph())}))
    assert response.status_code == 422


def test_comfyui_deleted_workflow_is_not_matched(client: TestClient) -> None:
    wf_id = _create_workflow(client)
    with client.app.state.session_factory() as db:
        wf = db.get(ComfyWorkflow, uuid.UUID(wf_id))
        assert wf is not None
        wf.deleted_at = datetime.now(UTC)
        db.commit()
    response = _post(client, _png({"prompt": json.dumps(_comfy_output_graph())}))
    assert response.status_code == 422


def test_comfyui_linked_prompt_has_note(client: TestClient) -> None:
    _create_workflow(client)
    graph = _comfy_output_graph()
    graph["6"]["inputs"]["text"] = ["30", 0]
    body = _post(client, _png({"prompt": json.dumps(graph)})).json()
    assert body["prompt"] == ""
    assert "promptLinked" in [n["code"] for n in body["notes"]]


# -- A1111 ------------------------------------------------------------------------------

INFOTEXT = (
    "a cat\nSteps: 20, Sampler: Euler a, CFG scale: 7, Seed: 5, Size: 512x768, Model: model-a"
)


def test_a1111_without_sdwebui_is_409(client: TestClient) -> None:
    assert _post(client, _png({"parameters": INFOTEXT})).status_code == 409


def test_a1111_with_sdwebui(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_factories(monkeypatch, FakeSdWebui())
    response = client.put("/api/sdwebui/connection", json={"url": "http://127.0.0.1:7860"})
    assert response.status_code == 200, response.text
    response = _post(client, _png({"parameters": INFOTEXT}))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "a1111"
    assert body["provider"] == "sdwebui"
    assert body["model"] == "model-a"
    assert body["prompt"] == "a cat"
    assert body["params"]["seed"] == 5


# -- 入力 -------------------------------------------------------------------------------


def test_no_metadata_is_422(client: TestClient) -> None:
    assert _post(client, _png()).status_code == 422


def test_asset_id_is_422(client: TestClient) -> None:
    response = client.post(PATH, json={"asset_id": str(uuid.uuid4())})
    assert response.status_code == 422
