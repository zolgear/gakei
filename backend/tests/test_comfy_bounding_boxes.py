"""領域の指示の四角を ComfyUI の Create Bounding Boxes に差し込む(ADR-0053 6章)。

- 変換(`comfy_bounding_boxes`): GAKEI の `[x0, y0, x1, y1]`(0〜1)から、ノードの要素
  (`bbox` は `[ymin, xmin, ymax, xmax]` の 0〜1000)へ。ノードの `elements_to_boxes` と同じ
  計算をテストの中に写し、意図したピクセルに戻ることを確かめる(ComfyUI 本体は import しない)。
- 読み取り・保存時の検証・capabilities・差し込み。
- Run の作成(種類・参照画像の断り、入力画像の無い Run の座標)と実行(`/object_info` の確認)。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.domain import comfy_bounding_boxes as bb
from app.domain.comfy_analyze import analyze_workflow
from app.domain.comfy_workflow import (
    Bindings,
    InputRef,
    check_workflow,
    resolve_prompt,
    workflow_model_capabilities,
)
from app.domain.models import ComfyWorkflow
from app.domain.schemas import RegionBox
from app.providers.comfyui.client import ComfyUIClient
from app.providers.comfyui.provider import ComfyUIProvider
from tests.comfyui_fake import FakeComfyUI, unavailable_ws_connect
from tests.comfyui_graphs import BBOX_LAYOUT_GRAPH, BBOX_OBJECT_INFO, T2I_GRAPH, clone
from tests.conftest import make_png_bytes, wait_for_run_terminal


def _region(number: int, box: list[float], **extra: Any) -> RegionBox:
    return RegionBox.model_validate({"number": number, "box": box, **extra})


# -- ノードの読み方を写したもの(comfy_extras/nodes_bounding_boxes.py の elements_to_boxes) ------


def _node_elements_to_boxes(elements: list[dict[str, Any]], width: int, height: int) -> list:
    boxes = []
    for element in elements:
        ymin, xmin, ymax, xmax = (float(v) / 1000.0 for v in element["bbox"])
        etype = "text" if element.get("type") == "text" else "obj"
        boxes.append(
            {
                "x": round(min(xmin, xmax) * width),
                "y": round(min(ymin, ymax) * height),
                "width": round(abs(xmax - xmin) * width),
                "height": round(abs(ymax - ymin) * height),
                "metadata": {
                    "type": etype,
                    "text": element.get("text", "") if etype == "text" else "",
                    "desc": element.get("desc", ""),
                },
            }
        )
    return boxes


# -- 変換 -----------------------------------------------------------------------------


def test_to_bbox_swaps_axes_and_scales_to_1000() -> None:
    assert bb.to_bbox([0.1, 0.2, 0.5, 0.9]) == [200, 100, 900, 500]
    assert bb.to_bbox([0.0, 0.0, 1.0, 1.0]) == [0, 0, 1000, 1000]


def test_to_bbox_rounds_half_up_and_keeps_tiny_boxes_non_empty() -> None:
    # 0.1235 * 1000 = 123.5 → 124(四捨五入。偶数丸めにしない)
    assert bb.to_bbox([0.1235, 0.0, 0.5, 0.5])[1] == 124
    # 幅が丸めで 0 になる四角は 1 目盛り広げる。右端・下端に寄っていれば内側へ広げる。
    assert bb.to_bbox([0.3001, 0.3001, 0.3002, 0.3002]) == [300, 300, 301, 301]
    assert bb.to_bbox([0.9999, 0.9999, 1.0, 1.0]) == [999, 999, 1000, 1000]


def test_to_elements_desc_text_and_order() -> None:
    regions = [
        _region(2, [0.5, 0.5, 0.9, 0.9], label="a sign", text="OPEN"),
        _region(1, [0.0, 0.0, 0.4, 0.4], kind="add", label="a cat", instruction="sleeping"),
        _region(3, [0.1, 0.6, 0.3, 0.8], label="a cup"),
        _region(4, [0.6, 0.0, 0.8, 0.2]),
    ]
    elements = bb.to_elements(regions)
    assert elements == [
        # 番号の順。desc は指示を優先し、種類(add)は送らない。
        {"type": "obj", "bbox": [0, 0, 400, 400], "desc": "sleeping"},
        # 描く文字があれば text。指示が無ければ名前を desc に入れる。
        {"type": "text", "bbox": [500, 500, 900, 900], "text": "OPEN", "desc": "a sign"},
        {"type": "obj", "bbox": [600, 100, 800, 300], "desc": "a cup"},
        # 名前も指示も無ければ空。
        {"type": "obj", "bbox": [0, 600, 200, 800], "desc": ""},
    ]


def test_encode_is_compact_json_and_keeps_non_ascii() -> None:
    value = bb.encode([_region(1, [0.0, 0.0, 0.5, 0.5], instruction="赤い帽子")])
    assert value == '[{"type":"obj","bbox":[0,0,500,500],"desc":"赤い帽子"}]'
    assert json.loads(value)[0]["desc"] == "赤い帽子"


@pytest.mark.parametrize(("width", "height"), [(1024, 1024), (1344, 768), (832, 1216)])
def test_node_turns_elements_back_into_intended_pixels(width: int, height: int) -> None:
    box = [0.25, 0.1, 0.75, 0.6]
    elements = json.loads(bb.encode([_region(1, box, label="x", text="OPEN")]))
    [pixels] = _node_elements_to_boxes(elements, width, height)
    # 0〜1000 の格子(1/1000)で丸めたぶんの誤差だけ(1 ピクセル + 格子 1 目盛り分)。
    tolerance = max(width, height) / 1000 + 1
    assert abs(pixels["x"] - box[0] * width) <= tolerance
    assert abs(pixels["y"] - box[1] * height) <= tolerance
    assert abs(pixels["width"] - (box[2] - box[0]) * width) <= tolerance
    assert abs(pixels["height"] - (box[3] - box[1]) * height) <= tolerance
    assert pixels["metadata"] == {"type": "text", "text": "OPEN", "desc": "x"}


def test_node_reads_1024_square_exactly() -> None:
    elements = json.loads(bb.encode([_region(1, [0.25, 0.5, 0.75, 1.0])]))
    [pixels] = _node_elements_to_boxes(elements, 1024, 1024)
    assert pixels == {
        "x": 256,
        "y": 512,
        "width": 512,
        "height": 512,
        "metadata": {"type": "obj", "text": "", "desc": ""},
    }


def test_inject_overwrites_wire_and_clears_last_incoming() -> None:
    graph = {"20": {"class_type": bb.CLASS_TYPE, "inputs": {"bboxes": ["30", 0]}}}
    bb.inject(graph, "20", "[]")
    assert graph["20"]["inputs"]["bboxes"] == "[]"
    assert graph["20"]["inputs"]["last_incoming"] == {"__value__": []}


# -- /object_info の確認 -------------------------------------------------------------


def test_check_object_info_accepts_multitype_with_string() -> None:
    assert bb.check_object_info({bb.CLASS_TYPE: BBOX_OBJECT_INFO}) is None


def test_check_object_info_node_missing() -> None:
    # 未知のノードには空の応答が返る。
    assert bb.check_object_info({}) == "node_missing"
    assert bb.check_object_info(None) == "node_missing"


def test_check_object_info_input_changed() -> None:
    info = json.loads(json.dumps(BBOX_OBJECT_INFO))
    info["input"]["optional"]["bboxes"] = ["BOUNDING_BOX,ARRAY", {}]
    assert bb.check_object_info({bb.CLASS_TYPE: info}) == "input_not_string"
    del info["input"]["optional"]["bboxes"]
    assert bb.check_object_info({bb.CLASS_TYPE: info}) == "input_not_string"


# -- 読み取り・検証・capabilities・差し込み --------------------------------------------------


def _layout_bindings(**extra: Any) -> dict[str, Any]:
    return {
        "prompt": {"node": "21", "input": "prompt"},
        "seed": [{"node": "3", "input": "seed"}],
        "width": {"node": "5", "input": "width"},
        "height": {"node": "5", "input": "height"},
        "outputs": ["9"],
        "bounding_boxes": {"node": "20", "input": "bboxes"},
        **extra,
    }


def test_analyze_finds_unique_live_bounding_boxes() -> None:
    result = analyze_workflow(clone(BBOX_LAYOUT_GRAPH))
    reading = next(r for r in result.readings if r.field == "bounding_boxes")
    assert reading.confidence == "unique"
    assert result.suggested_bindings.bounding_boxes == InputRef(node="20", input="bboxes")
    # `bboxes` はテンプレートに無いが、提案に検証を掛けてもエラーにならない。
    assert [v for v in result.validation if v.level == "error"] == []
    # 内部の状態(`editor_state` など)を公開パラメーターの候補にしない。
    assert all(p.node != "20" or p.input in ("width", "height") for p in result.candidate_params)


def test_analyze_asks_when_two_live_nodes() -> None:
    graph = clone(BBOX_LAYOUT_GRAPH)
    graph["22"] = clone({"n": graph["20"]})["n"]
    graph["23"] = {
        "class_type": "MOSSImageLayoutPrompt",
        "inputs": {"prompt": "", "element": ["22", 2], "width": 512, "height": 512},
    }
    graph["21"]["inputs"]["character_boxes"] = ["23", 0]
    result = analyze_workflow(graph)
    reading = next(r for r in result.readings if r.field == "bounding_boxes")
    assert reading.confidence == "ask"
    assert {c.node for c in reading.candidates} == {"20", "22"}
    assert reading.question
    assert result.suggested_bindings.bounding_boxes is None


def test_analyze_absent_without_node_and_ignores_unused_branch() -> None:
    result = analyze_workflow(clone(T2I_GRAPH))
    reading = next(r for r in result.readings if r.field == "bounding_boxes")
    assert reading.confidence == "absent"

    graph = clone(T2I_GRAPH)
    graph["20"] = clone(BBOX_LAYOUT_GRAPH)["20"]  # 出力につながらない
    result = analyze_workflow(graph)
    reading = next(r for r in result.readings if r.field == "bounding_boxes")
    assert reading.confidence == "absent"


def test_check_workflow_accepts_wired_bboxes() -> None:
    graph = clone(BBOX_LAYOUT_GRAPH)
    graph["30"] = {"class_type": "PrimitiveString", "inputs": {"value": "[]"}}
    graph["20"]["inputs"]["bboxes"] = ["30", 0]
    bindings = Bindings.model_validate(_layout_bindings())
    issues = check_workflow(graph, "generate", bindings, [])
    assert [i for i in issues if i.level == "error"] == []


def test_check_workflow_rejects_other_node_or_input() -> None:
    graph = clone(BBOX_LAYOUT_GRAPH)
    for ref in ({"node": "21", "input": "prompt2"}, {"node": "20", "input": "width"}):
        bindings = Bindings.model_validate(_layout_bindings(bounding_boxes=ref))
        errors = [i for i in check_workflow(graph, "generate", bindings, []) if i.level == "error"]
        assert [e.code for e in errors] == ["bounding_boxes_not_node"]
        assert errors[0].field == "bounding_boxes"
    bindings = Bindings.model_validate(
        _layout_bindings(bounding_boxes={"node": "99", "input": "bboxes"})
    )
    errors = [i for i in check_workflow(graph, "generate", bindings, []) if i.level == "error"]
    assert [e.code for e in errors] == ["node_missing"]


def _workflow_row(bindings: dict[str, Any], operation: str = "generate") -> ComfyWorkflow:
    return ComfyWorkflow(
        id=uuid.uuid4(),
        name="layout",
        operation=operation,
        template=clone(BBOX_LAYOUT_GRAPH),
        template_sha256="x",
        bindings=bindings,
        exposed_params=[],
    )


def test_capabilities_with_bounding_boxes() -> None:
    caps = workflow_model_capabilities(_workflow_row(_layout_bindings()))
    [op] = caps.operations
    assert op.supports_regions is True
    assert op.regions_delivery == "comfyui_bboxes"
    assert op.regions_kinds == ["change", "add"]
    assert op.regions_reference_images is False
    assert op.regions_requires_input_image is False


def test_capabilities_without_bounding_boxes() -> None:
    bindings = _layout_bindings()
    del bindings["bounding_boxes"]
    [op] = workflow_model_capabilities(_workflow_row(bindings)).operations
    assert op.supports_regions is False
    assert op.regions_delivery is None
    assert op.regions_kinds == []


def test_resolve_prompt_injects_only_with_regions() -> None:
    bindings = Bindings.model_validate(_layout_bindings())
    common: dict[str, Any] = {
        "prompt": "a room",
        "params": {},
        "seed": 1,
        "image_names": [],
        "mask_name": None,
    }
    untouched = resolve_prompt(clone(BBOX_LAYOUT_GRAPH), bindings, [], **common)
    assert "bboxes" not in untouched["20"]["inputs"]
    assert "last_incoming" not in untouched["20"]["inputs"]

    value = bb.encode([_region(1, [0.0, 0.0, 0.5, 0.5], label="a cat")])
    injected = resolve_prompt(
        clone(BBOX_LAYOUT_GRAPH), bindings, [], **common, bounding_boxes=value
    )
    assert injected["20"]["inputs"]["bboxes"] == value
    assert injected["20"]["inputs"]["last_incoming"] == {"__value__": []}
    # キャンバスの値は変えない(四角の無い Run ではこちらが使われる)。
    canvas = BBOX_LAYOUT_GRAPH["20"]["inputs"]["editor_state"]
    assert injected["20"]["inputs"]["editor_state"] == canvas


# -- Run の作成と実行(API を通す) ------------------------------------------------------


@pytest.fixture
def comfy_client(monkeypatch: pytest.MonkeyPatch, data_dir: Path) -> Iterator[TestClient]:
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("FAKE_PROVIDER", "1")
    monkeypatch.setenv("COMFYUI_URL", "http://127.0.0.1:8188")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        "app.providers.comfyui.provider.check_available",
        lambda base_url, timeout=1.0: (True, None, {}),
    )

    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


def _install_fake(client: TestClient, fake: FakeComfyUI) -> None:
    settings = client.app.state.settings

    def client_factory() -> ComfyUIClient:
        return ComfyUIClient(
            settings.comfyui_url,
            http=fake.make_async_client(),
            ws_connect=unavailable_ws_connect(),
        )

    client.app.state.registry.providers["comfyui"] = ComfyUIProvider(
        settings.comfyui_url,
        client.app.state.session_factory,
        settings.comfyui_timeout_seconds,
        client_factory=client_factory,
    )


def _fake_with_output(*, object_info: dict[str, Any] | None = BBOX_OBJECT_INFO) -> FakeComfyUI:
    fake = FakeComfyUI()
    if object_info is not None:
        fake.object_info_responses[bb.CLASS_TYPE] = object_info
    fake.set_history(
        "prompt-1",
        {
            "outputs": {
                "9": {"images": [{"filename": "out.png", "subfolder": "", "type": "output"}]}
            },
            "status": {"status_str": "success", "completed": True},
        },
    )
    fake.add_output_file("out.png", "", "output", make_png_bytes())
    return fake


def _register(client: TestClient, bindings: dict[str, Any] | None = None) -> str:
    response = client.post(
        "/api/comfyui/workflows",
        json={
            "name": "layout",
            "operation": "generate",
            "template": clone(BBOX_LAYOUT_GRAPH),
            "bindings": bindings or _layout_bindings(),
            "exposed_params": [],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _run(client: TestClient, workflow_id: str, regions: list[dict[str, Any]]) -> Any:
    return client.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": workflow_id,
            "prompt": "a cozy room",
            "provider": "comfyui",
            "params": {"width": 1344, "height": 768},
            "regions": regions,
        },
    )


def test_capabilities_api_reports_regions_for_comfyui_and_openai(
    comfy_client: TestClient,
) -> None:
    workflow_id = _register(comfy_client)
    body = comfy_client.get("/api/capabilities").json()
    comfy = next(p for p in body["providers"] if p["provider"] == "comfyui")
    [op] = next(m for m in comfy["models"] if m["model"] == workflow_id)["operations"]
    assert op["supports_regions"] is True
    assert op["regions_delivery"] == "comfyui_bboxes"
    assert op["regions_kinds"] == ["change", "add"]
    assert op["regions_reference_images"] is False
    assert op["regions_requires_input_image"] is False

    fake = next(p for p in body["providers"] if p["provider"] == "fake")
    edit = next(o for o in fake["models"][0]["operations"] if o["operation"] == "edit")
    assert edit["regions_delivery"] == "image_and_text"
    assert edit["regions_kinds"] == ["change", "add", "keep", "remove", "move"]
    assert edit["regions_reference_images"] is True
    assert edit["regions_requires_input_image"] is True


def test_comfyui_run_injects_boxes_without_input_image(comfy_client: TestClient) -> None:
    workflow_id = _register(comfy_client)
    fake = _fake_with_output()
    _install_fake(comfy_client, fake)

    regions = [
        {"number": 2, "kind": "add", "box": [0.5, 0.5, 1.0, 1.0], "text": "OPEN"},
        {"number": 1, "box": [0.0, 0.0, 0.5, 0.5], "label": "a cat", "instruction": "sleeping"},
    ]
    response = _run(comfy_client, workflow_id, regions)
    assert response.status_code == 202, response.text
    detail = wait_for_run_terminal(comfy_client, response.json()["id"])
    assert detail["status"] == "succeeded", detail

    expected = (
        '[{"type":"obj","bbox":[0,0,500,500],"desc":"sleeping"},'
        '{"type":"text","bbox":[500,500,1000,1000],"text":"OPEN","desc":""}]'
    )
    # run.params に差し込んだ値が入る。プロンプトに文は足さない。
    node = detail["params"]["comfyui_prompt"]["20"]["inputs"]
    assert node["bboxes"] == expected
    assert node["last_incoming"] == {"__value__": []}
    assert detail["params"]["comfyui_bounding_boxes"] == {"node": "20", "input": "bboxes"}
    assert detail["prompt"] == "a cozy room"
    # 送ったグラフにも同じ値。
    assert fake.queued_prompts[0]["prompt"]["20"]["inputs"]["bboxes"] == expected

    record = detail["regions"]
    assert record["delivery"] == "comfyui_bboxes"
    assert record["frame"] == "output"
    assert record["appended_text"] == ""
    assert record["overlay_asset_id"] is None
    assert [r["number"] for r in record["regions"]] == [1, 2]
    # 入力は足さない(重ねた画像が無い)。
    assert detail["inputs"] == []


def test_comfyui_run_without_boxes_keeps_canvas(comfy_client: TestClient) -> None:
    workflow_id = _register(comfy_client)
    # 四角が無ければ /object_info も見ない(ノードが無い ComfyUI でもそのまま動く)。
    fake = _fake_with_output(object_info=None)
    _install_fake(comfy_client, fake)
    response = _run(comfy_client, workflow_id, [])
    detail = wait_for_run_terminal(comfy_client, response.json()["id"])
    assert detail["status"] == "succeeded", detail
    assert "bboxes" not in detail["params"]["comfyui_prompt"]["20"]["inputs"]
    assert "comfyui_bounding_boxes" not in detail["params"]
    assert detail["regions"] is None


@pytest.mark.parametrize(
    ("object_info", "fragment"),
    [
        (None, "CreateBoundingBoxes"),
        (
            {"input": {"optional": {"bboxes": ["BOUNDING_BOX,ARRAY", {}]}}},
            "bboxes",
        ),
    ],
)
def test_comfyui_run_fails_when_node_shape_changed(
    comfy_client: TestClient, object_info: dict[str, Any] | None, fragment: str
) -> None:
    workflow_id = _register(comfy_client)
    fake = _fake_with_output(object_info=object_info)
    _install_fake(comfy_client, fake)
    response = _run(comfy_client, workflow_id, [{"number": 1, "box": [0.1, 0.1, 0.4, 0.4]}])
    assert response.status_code == 202, response.text
    detail = wait_for_run_terminal(comfy_client, response.json()["id"])
    assert detail["status"] == "failed"
    assert detail["error_code"] == "comfyuiRegionsUnsupported"
    assert fragment in detail["error_message"]
    # 黙って違う形で送らない。
    assert fake.queued_prompts == []


@pytest.mark.parametrize(
    "region",
    [
        {"number": 1, "kind": "keep", "box": [0.1, 0.1, 0.4, 0.4]},
        {"number": 1, "kind": "remove", "box": [0.1, 0.1, 0.4, 0.4]},
        {
            "number": 1,
            "kind": "move",
            "box": [0.5, 0.5, 0.8, 0.8],
            "source_box": [0.1, 0.1, 0.4, 0.4],
        },
        {"number": 1, "box": [0.1, 0.1, 0.4, 0.4], "reference_asset_id": str(uuid.uuid4())},
    ],
)
def test_comfyui_run_rejects_unsupported_region_options(
    comfy_client: TestClient, region: dict[str, Any]
) -> None:
    workflow_id = _register(comfy_client)
    _install_fake(comfy_client, _fake_with_output())
    response = _run(comfy_client, workflow_id, [region])
    assert response.status_code == 422, response.text
    assert "1" in response.json()["detail"]


def test_comfyui_run_rejects_regions_without_binding(comfy_client: TestClient) -> None:
    bindings = _layout_bindings()
    del bindings["bounding_boxes"]
    workflow_id = _register(comfy_client, bindings)
    _install_fake(comfy_client, _fake_with_output())
    response = _run(comfy_client, workflow_id, [{"number": 1, "box": [0.1, 0.1, 0.4, 0.4]}])
    assert response.status_code == 422, response.text
