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
