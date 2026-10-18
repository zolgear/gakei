"""`app/api/comfyui.py` の HTTP テスト(ADR-0013)。

実物の ComfyUI には接続しない。`client` フィクスチャは `conftest.py` の autouse で
`COMFYUI_URL=""` になっているので、到達可能/不可のケースは `check_available` を
monkeypatch する。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.comfyui import _parse_object_info_spec
from tests.comfyui_graphs import (
    IMG2IMG_GRAPH,
    INPAINT_GRAPH,
    QWEN_LIKE_GRAPH,
    QWEN_LIKE_OBJECT_INFO,
    T2I_GRAPH,
    clone,
)

# -- /status ------------------------------------------------------------------


def test_status_disabled_when_comfyui_url_is_empty(client: TestClient) -> None:
    response = client.get("/api/comfyui/status")
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "url": None,
        "enabled": False,
        "available": False,
        "reason": None,
        "version": None,
        "device": None,
        "source": "none",
        "locked": False,
        "loopback": None,
        "disabled": False,
    }


@pytest.fixture
def client_with_comfyui_url(
    monkeypatch: pytest.MonkeyPatch, data_dir: Path
) -> Iterator[TestClient]:
    """`COMFYUI_URL` を設定した状態でアプリを起動する(設定は起動時に固定されるため)。"""
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("FAKE_PROVIDER", "1")
    monkeypatch.setenv("COMFYUI_URL", "http://127.0.0.1:8188")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    from app.main import create_app

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


def test_status_available_when_reachable(
    client_with_comfyui_url: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_check_available(base_url: str, timeout: float = 1.0):
        assert base_url == "http://127.0.0.1:8188"
        return True, None, {"system": {"comfyui_version": "0.3.0"}, "devices": [{"name": "cuda:0"}]}

    monkeypatch.setattr("app.api.comfyui.check_available", fake_check_available)

    response = client_with_comfyui_url.get("/api/comfyui/status")
    assert response.status_code == 200
    body = response.json()
    assert body["url"] == "http://127.0.0.1:8188"
    assert body["enabled"] is True
    assert body["available"] is True
    assert body["version"] == "0.3.0"
    assert body["device"] == "cuda:0"


def test_status_unavailable_when_unreachable(
    client_with_comfyui_url: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_check_available(base_url: str, timeout: float = 1.0):
        return False, "ComfyUI に接続できません。", None

    monkeypatch.setattr("app.api.comfyui.check_available", fake_check_available)

    response = client_with_comfyui_url.get("/api/comfyui/status")
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert body["available"] is False
    assert body["reason"] == "ComfyUI に接続できません。"
    assert body["version"] is None


# -- /workflows/analyze ---------------------------------------------------------


def test_analyze_t2i_returns_suggestions(client: TestClient) -> None:
    response = client.post("/api/comfyui/workflows/analyze", json={"template": clone(T2I_GRAPH)})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["suggested_operation"] == "generate"
    assert body["suggested_bindings"]["prompt"] == {"node": "6", "input": "text"}
    assert body["suggested_bindings"]["outputs"] == ["9"]
    assert len(body["nodes"]) == len(T2I_GRAPH)
    names = {p["name"] for p in body["candidate_params"]}
    assert "ckpt_name" in names


def test_analyze_ui_format_returns_422(client: TestClient) -> None:
    response = client.post(
        "/api/comfyui/workflows/analyze", json={"template": {"nodes": [], "links": []}}
    )
    assert response.status_code == 422
    assert "Export" in response.json()["detail"]


def test_analyze_non_api_format_returns_422(client: TestClient) -> None:
    response = client.post(
        "/api/comfyui/workflows/analyze", json={"template": {"1": {"foo": "bar"}}}
    )
    assert response.status_code == 422


def test_analyze_enriches_candidate_params_via_object_info_when_reachable(
    client_with_comfyui_url: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.api.comfyui.check_available", lambda base_url, timeout=1.0: (True, None, {})
    )
    object_info = {
        "CheckpointLoaderSimple": {
            "input": {"required": {"ckpt_name": [["a.safetensors", "b.safetensors"]]}}
        },
        "KSampler": {
            "input": {
                "required": {"steps": ["INT", {"default": 99, "min": 1, "max": 150, "step": 1}]}
            }
        },
    }
    monkeypatch.setattr("app.api.comfyui.httpx.get", lambda *a, **k: _FakeResponse(object_info))

    response = client_with_comfyui_url.post(
        "/api/comfyui/workflows/analyze", json={"template": clone(T2I_GRAPH)}
    )
    assert response.status_code == 200, response.text
    by_name = {p["name"]: p for p in response.json()["candidate_params"]}
    assert by_name["ckpt_name"]["type"] == "enum"
    assert by_name["ckpt_name"]["choices"] == ["a.safetensors", "b.safetensors"]
    assert by_name["steps"]["type"] == "int"
    assert by_name["steps"]["minimum"] == 1
    assert by_name["steps"]["maximum"] == 150
    # 既定値はノード定義の default(99)ではなく、ワークフローに設定されている値。
    template_steps = next(
        n["inputs"]["steps"] for n in T2I_GRAPH.values() if n["class_type"] == "KSampler"
    )
    assert by_name["steps"]["default"] == template_steps


def test_analyze_keeps_guessed_params_when_object_info_unreachable(
    client_with_comfyui_url: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.api.comfyui.check_available",
        lambda base_url, timeout=1.0: (False, "unreachable", None),
    )
    response = client_with_comfyui_url.post(
        "/api/comfyui/workflows/analyze", json={"template": clone(T2I_GRAPH)}
    )
    assert response.status_code == 200, response.text
    names = {p["name"] for p in response.json()["candidate_params"]}
    assert "ckpt_name" in names


def test_analyze_qwen_like_finds_partial_suggestions_without_object_info(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/comfyui/workflows/analyze", json={"template": clone(QWEN_LIKE_GRAPH)}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    bindings = body["suggested_bindings"]
    assert bindings["prompt"] == {"node": "459:452", "input": "prompt"}
    assert bindings["negative_prompt"] == {"node": "459:452", "input": "negative_prompt"}
    assert bindings["seed"] == [{"node": "459:458", "input": "seed"}]
    assert bindings["outputs"] == ["461"]
    assert body["warnings"] == []


def test_analyze_qwen_like_via_object_info_enriches_v3_combo_and_dynamiccombo(
    client_with_comfyui_url: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0013 フォローアップ: ResolutionSelector の COMBO、SaveImageAdvanced の
    COMFY_DYNAMICCOMBO_V3、KSampler の control_after_generate な seed、旧形式の
    sampler_name の choices が、実物の /object_info の形のまま解釈できること。
    """
    monkeypatch.setattr(
        "app.api.comfyui.check_available", lambda base_url, timeout=1.0: (True, None, {})
    )
    monkeypatch.setattr(
        "app.api.comfyui.httpx.get", lambda *a, **k: _FakeResponse(QWEN_LIKE_OBJECT_INFO)
    )

    response = client_with_comfyui_url.post(
        "/api/comfyui/workflows/analyze", json={"template": clone(QWEN_LIKE_GRAPH)}
    )
    assert response.status_code == 200, response.text
    body = response.json()

    bindings = body["suggested_bindings"]
    assert bindings["prompt"] == {"node": "459:452", "input": "prompt"}
    assert bindings["negative_prompt"] == {"node": "459:452", "input": "negative_prompt"}
    assert bindings["seed"] == [{"node": "459:458", "input": "seed"}]
    assert bindings["outputs"] == ["461"]  # PreviewImage(output_node: true)は候補から除く
    assert body["warnings"] == []

    by_name = {p["name"]: p for p in body["candidate_params"]}
    assert by_name["aspect_ratio"]["type"] == "enum"
    assert "1:1 (Square)" in by_name["aspect_ratio"]["choices"]
    assert by_name["format"]["type"] == "enum"
    assert by_name["format"]["choices"] == ["png", "jpeg", "webp"]
    assert by_name["sampler_name"]["type"] == "enum"
    assert by_name["sampler_name"]["choices"] == ["euler", "euler_ancestral", "dpmpp_2m"]
    # seed は差し込み先として使われているので候補には出ない。
    assert "seed" not in by_name


def test_parse_object_info_spec_clamps_int_max_to_js_safe_integer() -> None:
    updates = _parse_object_info_spec("INT", {"min": 0, "max": 18446744073709551615})
    assert updates["type"] == "int"
    assert updates["maximum"] == 2**53 - 1


def test_parse_object_info_spec_v3_combo() -> None:
    updates = _parse_object_info_spec("COMBO", {"default": "a", "options": ["a", "b"]})
    assert updates == {"type": "enum", "choices": ["a", "b"], "default": "a"}


def test_parse_object_info_spec_v3_dynamiccombo() -> None:
    updates = _parse_object_info_spec(
        "COMFY_DYNAMICCOMBO_V3",
        {"options": [{"key": "png", "inputs": {}}, {"key": "jpeg", "inputs": {}}]},
    )
    assert updates == {"type": "enum", "choices": ["png", "jpeg"]}


def test_parse_object_info_spec_legacy_choice_list() -> None:
    updates = _parse_object_info_spec(["euler", "dpmpp_2m"], {})
    assert updates == {"type": "enum", "choices": ["euler", "dpmpp_2m"]}


def test_parse_object_info_spec_string_type() -> None:
    updates = _parse_object_info_spec("STRING", {"multiline": True})
    assert updates == {"type": "text"}


class _FakeResponse:
    def __init__(self, body: dict) -> None:
        self._body = body
        self.status_code = 200

    def json(self) -> dict:
        return self._body


# -- workflows CRUD --------------------------------------------------------------


def _t2i_bindings_body() -> dict:
    return {
        "prompt": {"node": "6", "input": "text"},
        "negative_prompt": {"node": "7", "input": "text"},
        "seed": [{"node": "3", "input": "seed"}],
        "width": {"node": "5", "input": "width"},
        "height": {"node": "5", "input": "height"},
        "batch_size": {"node": "5", "input": "batch_size"},
        "outputs": ["9"],
    }


def _create_t2i_workflow(client: TestClient, name: str = "t2i サンプル") -> dict:
    response = client.post(
        "/api/comfyui/workflows",
        json={
            "name": name,
            "operation": "generate",
            "template": clone(T2I_GRAPH),
            "bindings": _t2i_bindings_body(),
            "exposed_params": [
                {
                    "name": "steps",
                    "node": "3",
                    "input": "steps",
                    "type": "int",
                    "label": "ステップ数",
                    "minimum": 1,
                    "maximum": 100,
                }
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_create_workflow(client: TestClient) -> None:
    created = _create_t2i_workflow(client)
    assert created["name"] == "t2i サンプル"
    assert created["operation"] == "generate"
    assert created["bindings"]["prompt"] == {"node": "6", "input": "text"}
    assert len(created["exposed_params"]) == 1
    assert created["template_sha256"]


def test_create_workflow_rejects_ui_format(client: TestClient) -> None:
    response = client.post(
        "/api/comfyui/workflows",
        json={
            "name": "x",
            "operation": "generate",
            "template": {"nodes": [], "links": []},
            "bindings": _t2i_bindings_body(),
            "exposed_params": [],
        },
    )
    assert response.status_code == 422


def test_create_workflow_rejects_missing_node_binding(client: TestClient) -> None:
    bindings = _t2i_bindings_body()
    bindings["prompt"] = {"node": "does-not-exist", "input": "text"}
    response = client.post(
        "/api/comfyui/workflows",
        json={
            "name": "x",
            "operation": "generate",
            "template": clone(T2I_GRAPH),
            "bindings": bindings,
            "exposed_params": [],
        },
    )
    assert response.status_code == 422


def test_create_workflow_rejects_edit_without_image(client: TestClient) -> None:
    response = client.post(
        "/api/comfyui/workflows",
        json={
            "name": "x",
            "operation": "edit",
            "template": clone(IMG2IMG_GRAPH),
            "bindings": _t2i_bindings_body(),
            "exposed_params": [],
        },
    )
    assert response.status_code == 422


def test_create_workflow_rejects_reserved_param_name(client: TestClient) -> None:
    response = client.post(
        "/api/comfyui/workflows",
        json={
            "name": "x",
            "operation": "generate",
            "template": clone(T2I_GRAPH),
            "bindings": _t2i_bindings_body(),
            "exposed_params": [
                {"name": "seed", "node": "3", "input": "steps", "type": "int", "label": "x"}
            ],
        },
    )
    assert response.status_code == 422


def test_create_workflow_rejects_comfyui_prefixed_param_name(client: TestClient) -> None:
    response = client.post(
        "/api/comfyui/workflows",
        json={
            "name": "x",
            "operation": "generate",
            "template": clone(T2I_GRAPH),
            "bindings": _t2i_bindings_body(),
            "exposed_params": [
                {
                    "name": "comfyui_steps",
                    "node": "3",
                    "input": "steps",
                    "type": "int",
                    "label": "x",
                }
            ],
        },
    )
    assert response.status_code == 422


def test_create_workflow_rejects_malformed_param_name(client: TestClient) -> None:
    response = client.post(
        "/api/comfyui/workflows",
        json={
            "name": "x",
            "operation": "generate",
            "template": clone(T2I_GRAPH),
            "bindings": _t2i_bindings_body(),
            "exposed_params": [
                {"name": "Steps-Count", "node": "3", "input": "steps", "type": "int", "label": "x"}
            ],
        },
    )
    assert response.status_code == 422


def test_create_workflow_rejects_duplicate_binding_target(client: TestClient) -> None:
    bindings = _t2i_bindings_body()
    bindings["negative_prompt"] = {"node": "6", "input": "text"}
    response = client.post(
        "/api/comfyui/workflows",
        json={
            "name": "x",
            "operation": "generate",
            "template": clone(T2I_GRAPH),
            "bindings": bindings,
            "exposed_params": [],
        },
    )
    assert response.status_code == 422


def test_list_and_get_workflow(client: TestClient) -> None:
    created = _create_t2i_workflow(client)

    listed = client.get("/api/comfyui/workflows").json()["items"]
    ids = [w["id"] for w in listed]
    assert created["id"] in ids

    fetched = client.get(f"/api/comfyui/workflows/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == created["id"]
    assert fetched.json()["template"]["4"]["class_type"] == "CheckpointLoaderSimple"


def test_get_unknown_workflow_returns_404(client: TestClient) -> None:
    fake_id = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"/api/comfyui/workflows/{fake_id}").status_code == 404


def test_patch_workflow_updates_fields(client: TestClient) -> None:
    created = _create_t2i_workflow(client)
    response = client.patch(f"/api/comfyui/workflows/{created['id']}", json={"name": "改名した"})
    assert response.status_code == 200
    assert response.json()["name"] == "改名した"
    # template を変えていないので sha は変わらない。
    assert response.json()["template_sha256"] == created["template_sha256"]


def test_patch_workflow_template_recomputes_sha(client: TestClient) -> None:
    created = _create_t2i_workflow(client)
    new_template = clone(IMG2IMG_GRAPH)
    response = client.patch(
        f"/api/comfyui/workflows/{created['id']}",
        json={
            "template": new_template,
            "operation": "edit",
            "bindings": {
                "prompt": {"node": "6", "input": "text"},
                "negative_prompt": {"node": "7", "input": "text"},
                "seed": [{"node": "3", "input": "seed"}],
                "image": {"node": "10", "input": "image"},
                "outputs": ["9"],
            },
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["template_sha256"] != created["template_sha256"]
    assert body["operation"] == "edit"


def test_patch_workflow_revalidates_whole_document(client: TestClient) -> None:
    created = _create_t2i_workflow(client)
    # bindings だけ壊れた形に差し替える(template には存在しないノードを指す)。
    broken_bindings = _t2i_bindings_body()
    broken_bindings["prompt"] = {"node": "no-such-node", "input": "text"}
    response = client.patch(
        f"/api/comfyui/workflows/{created['id']}", json={"bindings": broken_bindings}
    )
    assert response.status_code == 422


def test_delete_workflow_removes_from_list_and_get(client: TestClient) -> None:
    created = _create_t2i_workflow(client)
    response = client.delete(f"/api/comfyui/workflows/{created['id']}")
    assert response.status_code == 204

    listed = client.get("/api/comfyui/workflows").json()["items"]
    assert created["id"] not in [w["id"] for w in listed]
    assert client.get(f"/api/comfyui/workflows/{created['id']}").status_code == 404


def test_delete_unknown_workflow_returns_404(client: TestClient) -> None:
    fake_id = "00000000-0000-0000-0000-000000000000"
    assert client.delete(f"/api/comfyui/workflows/{fake_id}").status_code == 404


def test_create_inpaint_workflow_with_mask(client: TestClient) -> None:
    response = client.post(
        "/api/comfyui/workflows",
        json={
            "name": "inpaint サンプル",
            "operation": "edit",
            "template": clone(INPAINT_GRAPH),
            "bindings": {
                "prompt": {"node": "6", "input": "text"},
                "negative_prompt": {"node": "7", "input": "text"},
                "seed": [{"node": "3", "input": "seed"}],
                "image": {"node": "10", "input": "image"},
                "mask": {"mode": "load_image_mask", "node": "12", "input": "image"},
                "outputs": ["9"],
            },
            "exposed_params": [],
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["bindings"]["mask"] == {
        "mode": "load_image_mask",
        "node": "12",
        "input": "image",
    }


# -- ADR-0052: 読み取りの応答と、保存時の検証(エラーと警告) ---------------------------------


def test_analyze_returns_readings_states_tiers_and_validation(client: TestClient) -> None:
    graph = clone(T2I_GRAPH)
    graph["20"] = {"class_type": "PreviewImage", "inputs": {"images": ["8", 0]}}
    response = client.post("/api/comfyui/workflows/analyze", json={"template": graph})
    assert response.status_code == 200, response.text
    body = response.json()
    readings = {r["field"]: r for r in body["readings"]}
    assert readings["prompt"]["confidence"] == "unique"
    assert readings["prompt"]["reason"]
    path = readings["prompt"]["paths"][0]
    assert path[0] == {"node": "6", "class_type": "CLIPTextEncode", "input": "text"}
    assert path[-1]["node"] == "9"
    assert readings["images"]["confidence"] == "absent"
    states = {n["id"]: n["state"] for n in body["nodes"]}
    assert states["3"] == "live"
    assert states["20"] == "display"
    tiers = {(p["node"], p["input"]): p["tier"] for p in body["param_tiers"]}
    assert tiers[("3", "steps")] == "recommended"
    assert tiers[("4", "ckpt_name")] == "hidden"
    assert body["validation"] == []
    # 読むだけの図に使う配線と錨の種類(ADR-0052 4章)。
    edges = {(e["source"], e["target"], e["input"]) for e in body["edges"]}
    assert ("6", "3", "positive") in edges
    assert ("8", "20", "images") in edges
    assert all(e["followed"] for e in body["edges"])
    kinds = {n["id"]: n["kind"] for n in body["nodes"]}
    assert kinds["3"] == "sampler"
    assert kinds["9"] == "save"
    assert kinds["20"] == "display"
    assert readings["mask"]["evidence"] == []


def test_create_workflow_rejects_wired_binding(client: TestClient) -> None:
    bindings = _t2i_bindings_body()
    bindings["prompt"] = {"node": "3", "input": "positive"}
    response = client.post(
        "/api/comfyui/workflows",
        json={
            "name": "配線",
            "operation": "generate",
            "template": clone(T2I_GRAPH),
            "bindings": bindings,
            "exposed_params": [],
        },
    )
    assert response.status_code == 422
    assert "配線" in response.json()["detail"]


def test_create_workflow_rejects_type_mismatch_and_output_without_image(
    client: TestClient,
) -> None:
    bindings = _t2i_bindings_body()
    bindings["seed"] = [{"node": "3", "input": "sampler_name"}]
    bindings["outputs"] = ["4"]
    response = client.post(
        "/api/comfyui/workflows",
        json={
            "name": "型",
            "operation": "generate",
            "template": clone(T2I_GRAPH),
            "bindings": bindings,
            "exposed_params": [],
        },
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    # エラーが複数なら改行でつなぐ。
    assert "整数" in detail
    assert "生成した画像" in detail


def test_create_workflow_keeps_warnings_in_response(client: TestClient) -> None:
    graph = clone(T2I_GRAPH)
    graph["20"] = {"class_type": "PreviewImage", "inputs": {"images": ["8", 0]}}
    bindings = _t2i_bindings_body()
    bindings["outputs"] = ["9", "20"]
    response = client.post(
        "/api/comfyui/workflows",
        json={
            "name": "警告",
            "operation": "generate",
            "template": graph,
            "bindings": bindings,
            "exposed_params": [],
        },
    )
    assert response.status_code == 201, response.text
    issues = response.json()["validation_issues"]
    assert [(i["level"], i["code"], i["node"]) for i in issues] == [
        ("warning", "display_only_output", "20")
    ]
    # 取得でも、保存済みの差し込み先を今の検証に掛けた結果が出る。
    fetched = client.get(f"/api/comfyui/workflows/{response.json()['id']}").json()
    assert [i["code"] for i in fetched["validation_issues"]] == ["display_only_output"]


def test_patch_workflow_rejects_wired_exposed_param_and_returns_warnings(
    client: TestClient,
) -> None:
    created = _create_t2i_workflow(client)
    assert created["validation_issues"] == []
    response = client.patch(
        f"/api/comfyui/workflows/{created['id']}",
        json={
            "exposed_params": [
                {
                    "name": "latent",
                    "node": "3",
                    "input": "latent_image",
                    "type": "text",
                    "label": "x",
                }
            ]
        },
    )
    assert response.status_code == 422
    assert "配線" in response.json()["detail"]

    graph = clone(T2I_GRAPH)
    graph["30"] = {"class_type": "EmptyLatentImage", "inputs": {"width": 64, "height": 64}}
    response = client.patch(
        f"/api/comfyui/workflows/{created['id']}",
        json={
            "template": graph,
            "exposed_params": [
                {"name": "spare_width", "node": "30", "input": "width", "type": "int", "label": "w"}
            ],
        },
    )
    assert response.status_code == 200, response.text
    assert [i["code"] for i in response.json()["validation_issues"]] == ["unused_branch"]


# -- /workflows/validate(保存せずに検証だけ。ADR-0052 3章) ------------------------------


def test_validate_returns_errors_and_warnings_without_saving(client: TestClient) -> None:
    graph = clone(T2I_GRAPH)
    graph["20"] = {"class_type": "PreviewImage", "inputs": {"images": ["8", 0]}}
    bindings = _t2i_bindings_body()
    bindings["prompt"] = {"node": "3", "input": "positive"}
    bindings["outputs"] = ["9", "20"]
    response = client.post(
        "/api/comfyui/workflows/validate",
        json={"template": graph, "operation": "generate", "bindings": bindings},
    )
    assert response.status_code == 200, response.text
    issues = [(i["level"], i["code"], i["field"]) for i in response.json()["issues"]]
    assert ("error", "wired_input", "prompt") in issues
    assert ("warning", "display_only_output", "outputs") in issues
    # 保存はしない。
    assert client.get("/api/comfyui/workflows").json()["items"] == []


def test_validate_accepts_missing_required_fields(client: TestClient) -> None:
    # まだ選んでいない必須項目(prompt、outputs)は問わない(画面が別に示す)。
    response = client.post(
        "/api/comfyui/workflows/validate",
        json={"template": clone(T2I_GRAPH), "operation": "generate", "bindings": {}},
    )
    assert response.status_code == 200, response.text
    assert response.json()["issues"] == []


def test_validate_checks_exposed_params_and_rejects_ui_format(client: TestClient) -> None:
    response = client.post(
        "/api/comfyui/workflows/validate",
        json={
            "template": clone(T2I_GRAPH),
            "operation": "generate",
            "bindings": _t2i_bindings_body(),
            "exposed_params": [
                {"name": "steps", "node": "3", "input": "seed", "type": "int", "label": "s"}
            ],
        },
    )
    assert response.status_code == 200
    codes = [(i["code"], i["field"]) for i in response.json()["issues"]]
    assert ("duplicate", "exposed_params.steps") in codes

    response = client.post(
        "/api/comfyui/workflows/validate",
        json={
            "template": {"nodes": [], "links": []},
            "operation": "generate",
            "bindings": {},
        },
    )
    assert response.status_code == 422


def test_analyze_param_tiers_carry_hidden_reason(client: TestClient) -> None:
    response = client.post("/api/comfyui/workflows/analyze", json={"template": clone(T2I_GRAPH)})
    tiers = {(p["node"], p["input"]): p for p in response.json()["param_tiers"]}
    assert tiers[("4", "ckpt_name")]["hidden_reason"] == "file_or_model"
    assert tiers[("3", "steps")]["hidden_reason"] is None


# -- 公開パラメーターの型(1.0 を FLOAT のまま読む)と、保存したテンプレートの読み取り -------------


def test_analyze_keeps_float_type_for_integral_float_values(client: TestClient) -> None:
    # T2I_GRAPH の cfg は 8.0、denoise は 1.0。JSON の 1.0 は Python では float のまま読める。
    response = client.post("/api/comfyui/workflows/analyze", json={"template": clone(T2I_GRAPH)})
    params = {(p["node"], p["input"]): p for p in response.json()["candidate_params"]}
    assert params[("3", "cfg")]["type"] == "float"
    assert params[("3", "denoise")]["type"] == "float"
    assert params[("3", "steps")]["type"] == "int"


def test_analyze_by_workflow_id_reads_saved_template(client: TestClient) -> None:
    created = _create_t2i_workflow(client)
    response = client.post("/api/comfyui/workflows/analyze", json={"workflow_id": created["id"]})
    assert response.status_code == 200, response.text
    params = {(p["node"], p["input"]): p for p in response.json()["candidate_params"]}
    assert params[("3", "denoise")]["type"] == "float"


def test_analyze_requires_exactly_one_of_template_or_workflow_id(client: TestClient) -> None:
    assert client.post("/api/comfyui/workflows/analyze", json={}).status_code == 422
    created = _create_t2i_workflow(client)
    response = client.post(
        "/api/comfyui/workflows/analyze",
        json={"template": clone(T2I_GRAPH), "workflow_id": created["id"]},
    )
    assert response.status_code == 422
    missing = client.post(
        "/api/comfyui/workflows/analyze",
        json={"workflow_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert missing.status_code == 404


# -- 生成画面からの閲覧(ADR-0052 7章) ------------------------------------------------

# テスト用の、形だけそれらしい値(実在のキーではない)。
_FAKE_KEY = "sk-proj-" + "Ab3dEf6hIj9kLm2nOp5qRs8t"


def _connect(client: TestClient) -> None:
    response = client.put("/api/comfyui/connection", json={"url": "http://127.0.0.1:8189"})
    assert response.status_code == 200, response.text


def _create_workflow_with_secret(client: TestClient) -> dict:
    template = clone(T2I_GRAPH)
    # 出力に届かないカスタムノードに、秘密に見える値を2通り(名前と値の形)で書く。
    template["90"] = {
        "class_type": "ExternalApiNode",
        "inputs": {"api_key": "plain-looking", "note": f"use {_FAKE_KEY}", "count": 3},
        "_meta": {"title": "外部 API"},
    }
    response = client.post(
        "/api/comfyui/workflows",
        json={
            "name": "秘密入り",
            "operation": "generate",
            "template": template,
            "bindings": _t2i_bindings_body(),
            "exposed_params": [
                {
                    "name": "steps",
                    "node": "3",
                    "input": "steps",
                    "type": "int",
                    "label": "ステップ数",
                },
                {
                    "name": "note",
                    "node": "90",
                    "input": "note",
                    "type": "text",
                    "label": "メモ",
                    "default": f"use {_FAKE_KEY}",
                },
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_view_returns_structure_and_redacts_secrets(client: TestClient) -> None:
    _connect(client)
    workflow = _create_workflow_with_secret(client)

    response = client.get(f"/api/comfyui/workflows/{workflow['id']}/view")
    assert response.status_code == 200, response.text
    body = response.json()
    assert "plain-looking" not in response.text
    assert _FAKE_KEY not in response.text
    # テンプレートそのものは返さない。
    assert "template" not in body
    assert body["name"] == "秘密入り"
    assert body["operation"] == "generate"

    nodes = {n["id"]: n for n in body["nodes"]}
    assert nodes["3"]["kind"] == "sampler"
    assert nodes["3"]["state"] == "live"
    assert nodes["90"]["state"] == "unused"
    inputs = {i["name"]: i for i in nodes["90"]["inputs"]}
    assert inputs["api_key"] == {
        "name": "api_key",
        "value": None,
        "linked": False,
        "redacted": True,
    }
    assert inputs["note"]["redacted"] is True and inputs["note"]["value"] is None
    assert inputs["count"] == {"name": "count", "value": 3, "linked": False, "redacted": False}
    sampler_inputs = {i["name"]: i for i in nodes["3"]["inputs"]}
    assert sampler_inputs["model"]["linked"] is True
    assert sampler_inputs["model"]["value"] == ["4", 0]

    assert any(e["source"] == "3" and e["target"] == "8" for e in body["edges"])
    assert body["bindings"]["prompt"] == {"node": "6", "input": "text"}
    assert body["bindings"]["outputs"] == ["9"]

    params = {p["name"]: p for p in body["exposed_params"]}
    assert params["steps"]["default_redacted"] is False
    assert params["steps"]["label"] == "ステップ数"
    assert params["note"]["default"] is None
    assert params["note"]["default_redacted"] is True


def test_view_returns_404_when_connection_inactive_or_workflow_deleted(
    client: TestClient,
) -> None:
    # 接続が無い(未設定)間は、生成画面に出ないので閲覧もできない。
    workflow = _create_t2i_workflow(client)
    response = client.get(f"/api/comfyui/workflows/{workflow['id']}/view")
    assert response.status_code == 404

    _connect(client)
    assert client.get(f"/api/comfyui/workflows/{workflow['id']}/view").status_code == 200

    # 無効にした接続。
    assert client.post("/api/comfyui/connection/disable").status_code == 200
    assert client.get(f"/api/comfyui/workflows/{workflow['id']}/view").status_code == 404
    assert client.post("/api/comfyui/connection/enable").status_code == 200
    assert client.get(f"/api/comfyui/workflows/{workflow['id']}/view").status_code == 200

    # 削除したワークフロー・無いワークフロー。
    assert client.delete(f"/api/comfyui/workflows/{workflow['id']}").status_code == 204
    assert client.get(f"/api/comfyui/workflows/{workflow['id']}/view").status_code == 404
    unknown = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"/api/comfyui/workflows/{unknown}/view").status_code == 404
