"""ComfyUI のグラフの出力の幅・高さの決め方(ADR-0053 6章)。

Resolution Selector(ComfyUI 本体の `comfy_extras/nodes_resolution.py`)の計算をテストの中に写し、
同じ値になることを確かめる(ComfyUI 本体は import しない)。フロントの
`frontend/src/features/run-form/outputSize.test.ts` も同じ表・同じ例で確かめる。
"""

from __future__ import annotations

import math
import uuid
from typing import Any

import pytest

from app.domain import comfy_bounding_boxes as bb
from app.domain import comfy_output_size as cos
from app.domain.comfy_workflow import (
    Bindings,
    ExposedParam,
    InputRef,
    resolve_prompt,
    workflow_model_capabilities,
)
from app.domain.models import ComfyWorkflow
from app.domain.schemas import RegionBox
from tests.comfyui_graphs import BBOX_LAYOUT_GRAPH, BBOX_RESOLUTION_GRAPH, clone

# -- ノードの計算を写したもの ---------------------------------------------------------------

_NODE_ASPECT_RATIOS = {
    "1:1 (Square)": (1, 1),
    "2:3 (Portrait Photo)": (2, 3),
    "3:2 (Photo)": (3, 2),
    "3:4 (Portrait Standard)": (3, 4),
    "4:3 (Standard)": (4, 3),
    "9:16 (Portrait Widescreen)": (9, 16),
    "16:9 (Widescreen)": (16, 9),
    "21:9 (Ultrawide)": (21, 9),
}


def _node_execute(aspect_ratio: str, megapixels: float, multiple: int) -> tuple[int, int]:
    w_ratio, h_ratio = _NODE_ASPECT_RATIOS[aspect_ratio]
    total_pixels = megapixels * 1024 * 1024
    scale = math.sqrt(total_pixels / (w_ratio * h_ratio))
    width = round(w_ratio * scale / multiple) * multiple
    height = round(h_ratio * scale / multiple) * multiple
    return width, height


def test_aspect_ratio_table_matches_node() -> None:
    assert cos.ASPECT_RATIOS == _NODE_ASPECT_RATIOS


@pytest.mark.parametrize("aspect_ratio", list(_NODE_ASPECT_RATIOS))
@pytest.mark.parametrize(
    ("megapixels", "multiple"), [(1.0, 8), (1.5, 16), (0.5, 64), (2.3, 32), (4, 128), (0.1, 12)]
)
def test_resolution_selector_matches_node(
    aspect_ratio: str, megapixels: float, multiple: int
) -> None:
    got = cos.resolution_selector_size(aspect_ratio, megapixels, multiple)
    assert got == _node_execute(aspect_ratio, megapixels, multiple)


def test_resolution_selector_examples() -> None:
    # フロントのテストと同じ例。
    assert cos.resolution_selector_size("9:16 (Portrait Widescreen)", 1.5, 16) == (944, 1680)
    assert cos.resolution_selector_size("1:1 (Square)", 1.0, 8) == (1024, 1024)
    assert cos.resolution_selector_size("21:9 (Ultrawide)", 1.0, 8) == (1568, 672)
    # ちょうど半分は偶数へ丸める(Python の round)。sqrt = 20 → 20 / 8 = 2.5 → 2。
    assert cos.resolution_selector_size("1:1 (Square)", 25 / 65536, 8) == (16, 16)
    # sqrt = 28 → 3.5 → 4。
    assert cos.resolution_selector_size("1:1 (Square)", 784 / 1048576, 8) == (32, 32)


def test_resolution_selector_unknown_or_bad_inputs() -> None:
    # 知らない選択肢は、先頭が "W:H" でも決めない。
    assert cos.resolution_selector_size("5:4 (New)", 1.0, 8) is None
    assert cos.resolution_selector_size("1:1 (Square)", 0, 8) is None
    assert cos.resolution_selector_size("1:1 (Square)", "1.0", 8) is None
    assert cos.resolution_selector_size("1:1 (Square)", 1.0, 0) is None
    assert cos.resolution_selector_size("1:1 (Square)", 1.0, True) is None


# -- グラフの整数の入力値 -----------------------------------------------------------------


def test_resolve_int_constant_primitive_and_selector() -> None:
    graph = clone(BBOX_RESOLUTION_GRAPH)
    graph["30"] = {"class_type": "PrimitiveInt", "inputs": {"value": 768}}
    graph["31"] = {"class_type": "PrimitiveInt", "inputs": {"value": ["30", 0]}}
    assert cos.resolve_int(graph, 512) == 512
    assert cos.resolve_int(graph, ["30", 0]) == 768
    assert cos.resolve_int(graph, ["31", 0]) == 768
    assert cos.resolve_int(graph, ["13", 0]) == 944
    assert cos.resolve_int(graph, ["13", 1]) == 1680
    assert cos.resolve_int(graph, ["13", 2]) is None
    assert cos.resolve_int(graph, 0) is None
    assert cos.resolve_int(graph, ["99", 0]) is None


def test_resolve_int_selector_inputs_from_primitives_and_defaults() -> None:
    graph = clone(BBOX_RESOLUTION_GRAPH)
    graph["40"] = {
        "class_type": "PrimitiveStringMultiline",
        "inputs": {"value": "16:9 (Widescreen)"},
    }
    graph["41"] = {"class_type": "PrimitiveFloat", "inputs": {"value": 1.0}}
    graph["13"]["inputs"] = {"aspect_ratio": ["40", 0], "megapixels": ["41", 0]}
    # multiple は入力が無いのでノードの既定(8)。
    assert cos.resolve_int(graph, ["13", 0]) == 1368
    assert cos.resolve_int(graph, ["13", 1]) == 768
    graph["13"]["inputs"]["aspect_ratio"] = "5:4 (New)"
    assert cos.resolve_int(graph, ["13", 0]) is None


def test_resolve_int_stops_on_cycle() -> None:
    graph = {
        "1": {"class_type": "PrimitiveInt", "inputs": {"value": ["2", 0]}},
        "2": {"class_type": "PrimitiveInt", "inputs": {"value": ["1", 0]}},
    }
    assert cos.resolve_int(graph, ["1", 0]) is None


# -- 四角の差し込み -----------------------------------------------------------------------


def _region(number: int, box: list[float], **extra: Any) -> RegionBox:
    return RegionBox.model_validate({"number": number, "box": box, **extra})


def test_inject_follows_resolution_selector() -> None:
    graph = clone(BBOX_RESOLUTION_GRAPH)
    value = bb.encode([_region(1, [0.25, 0.5, 0.75, 1.0], label="a cat")])
    bb.inject(graph, "20", value)
    [box] = graph["20"]["inputs"]["editor_state"]["__value__"]
    # 944 × 1680。
    assert (box["x"], box["y"], box["width"], box["height"]) == (236, 840, 472, 840)


def _selector_bindings() -> Bindings:
    return Bindings.model_validate(
        {
            "prompt": {"node": "21", "input": "prompt"},
            "seed": [{"node": "3", "input": "seed"}],
            "outputs": ["9"],
            "bounding_boxes": {"node": "20", "input": "bboxes"},
        }
    )


def _aspect_param(**extra: Any) -> ExposedParam:
    return ExposedParam.model_validate(
        {
            "name": "aspect_ratio",
            "node": "13",
            "input": "aspect_ratio",
            "type": "enum",
            "label": "Aspect",
            "choices": list(_NODE_ASPECT_RATIOS),
            **extra,
        }
    )


_COMMON: dict[str, Any] = {"prompt": "a room", "seed": 1, "image_names": [], "mask_name": None}


def test_resolve_prompt_editor_state_uses_exposed_aspect_ratio() -> None:
    value = bb.encode([_region(1, [0.0, 0.0, 0.5, 0.5], label="a cat")])
    graph = resolve_prompt(
        clone(BBOX_RESOLUTION_GRAPH),
        _selector_bindings(),
        [_aspect_param()],
        **_COMMON,
        params={"aspect_ratio": "16:9 (Widescreen)"},
        bounding_boxes=value,
    )
    assert graph["13"]["inputs"]["aspect_ratio"] == "16:9 (Widescreen)"
    [box] = graph["20"]["inputs"]["editor_state"]["__value__"]
    # 1.5 MP、16 の倍数の 16:9 は 1680 × 944(テンプレートの 9:16 ではない)。
    assert (box["width"], box["height"]) == (840, 472)


def test_resolve_prompt_editor_state_uses_exposed_default() -> None:
    value = bb.encode([_region(1, [0.0, 0.0, 1.0, 1.0])])
    graph = resolve_prompt(
        clone(BBOX_RESOLUTION_GRAPH),
        _selector_bindings(),
        [_aspect_param(default="1:1 (Square)")],
        **_COMMON,
        params={},
        bounding_boxes=value,
    )
    [box] = graph["20"]["inputs"]["editor_state"]["__value__"]
    assert (box["width"], box["height"]) == (1248, 1248)


def test_resolve_prompt_editor_state_uses_exposed_width() -> None:
    # 差し込み先のノードの幅・高さそのものを公開パラメーターにしても、その値で計算する。
    graph_template = clone(BBOX_LAYOUT_GRAPH)
    exposed = [
        ExposedParam(name="bw", node="20", input="width", type="int", label="W"),
        ExposedParam(name="bh", node="20", input="height", type="int", label="H"),
    ]
    value = bb.encode([_region(1, [0.0, 0.0, 0.5, 0.5])])
    graph = resolve_prompt(
        graph_template,
        _selector_bindings(),
        exposed,
        **_COMMON,
        params={"bw": 1344, "bh": 768},
        bounding_boxes=value,
    )
    [box] = graph["20"]["inputs"]["editor_state"]["__value__"]
    assert (box["width"], box["height"]) == (672, 384)


def test_resolve_prompt_boxes_win_over_exposed_bboxes() -> None:
    # 差し込み先の `bboxes` を公開パラメーターにしていても、四角を付けた Run では四角が勝つ。
    exposed = [ExposedParam(name="raw", node="20", input="bboxes", type="text", label="raw")]
    value = bb.encode([_region(1, [0.0, 0.0, 0.5, 0.5])])
    graph = resolve_prompt(
        clone(BBOX_RESOLUTION_GRAPH),
        _selector_bindings(),
        exposed,
        **_COMMON,
        params={"raw": "[]"},
        bounding_boxes=value,
    )
    assert graph["20"]["inputs"]["bboxes"] == value
    # 四角の無い Run では公開パラメーターの値のまま。
    untouched = resolve_prompt(
        clone(BBOX_RESOLUTION_GRAPH),
        _selector_bindings(),
        exposed,
        **_COMMON,
        params={"raw": "[]"},
    )
    assert untouched["20"]["inputs"]["bboxes"] == "[]"


# -- capabilities の決まり方 --------------------------------------------------------------


def _row(template: dict[str, Any], bindings: Bindings, exposed: list[ExposedParam]) -> Any:
    return ComfyWorkflow(
        id=uuid.uuid4(),
        name="layout",
        operation="generate",
        template=template,
        template_sha256="x",
        bindings=bindings.model_dump(exclude_none=True),
        exposed_params=[p.model_dump(exclude_none=True) for p in exposed],
    )


def _output_size(template: dict[str, Any], bindings: Bindings, exposed: list[ExposedParam]) -> Any:
    [op] = workflow_model_capabilities(_row(template, bindings, exposed)).operations
    return None if op.output_size is None else op.output_size.model_dump(exclude_none=True)


def test_capabilities_output_size_from_resolution_selector() -> None:
    rule = _output_size(clone(BBOX_RESOLUTION_GRAPH), _selector_bindings(), [_aspect_param()])
    assert rule == {
        "width": {
            "source": "resolution_selector",
            "output": 0,
            "aspect_ratio": {"param": "aspect_ratio"},
            "megapixels": {"value": 1.5},
            "multiple": {"value": 16},
        },
        "height": {
            "source": "resolution_selector",
            "output": 1,
            "aspect_ratio": {"param": "aspect_ratio"},
            "megapixels": {"value": 1.5},
            "multiple": {"value": 16},
        },
    }


def test_capabilities_output_size_follows_primitive_to_exposed_param() -> None:
    template = clone(BBOX_RESOLUTION_GRAPH)
    template["41"] = {"class_type": "PrimitiveFloat", "inputs": {"value": 1.0}}
    template["13"]["inputs"]["megapixels"] = ["41", 0]
    del template["13"]["inputs"]["multiple"]
    exposed = [ExposedParam(name="mp", node="41", input="value", type="float", label="MP")]
    rule = _output_size(template, _selector_bindings(), exposed)
    assert rule["width"]["megapixels"] == {"param": "mp"}
    assert rule["width"]["aspect_ratio"] == {"value": "9:16 (Portrait Widescreen)"}
    # 入力が無ければノードの既定値。
    assert rule["width"]["multiple"] == {"value": 8}


def test_capabilities_output_size_with_width_height_bindings() -> None:
    bindings = _selector_bindings().model_copy(
        update={
            "width": InputRef(node="5", input="width"),
            "height": InputRef(node="5", input="height"),
        }
    )
    rule = _output_size(clone(BBOX_LAYOUT_GRAPH), bindings, [])
    assert rule == {
        "width": {"source": "int", "value": {"param": "width"}},
        "height": {"source": "int", "value": {"param": "height"}},
    }


def test_capabilities_output_size_constant_and_unknown() -> None:
    assert _output_size(clone(BBOX_LAYOUT_GRAPH), _selector_bindings(), []) == {
        "width": {"source": "int", "value": {"value": 1024}},
        "height": {"source": "int", "value": {"value": 1024}},
    }
    template = clone(BBOX_LAYOUT_GRAPH)
    template["30"] = {"class_type": "SomeMath", "inputs": {"a": 1, "b": 2}}
    template["20"]["inputs"]["width"] = ["30", 0]
    assert _output_size(template, _selector_bindings(), []) is None
    # 差し込み先が無ければ載せない。
    no_boxes = Bindings.model_validate(
        {"prompt": {"node": "21", "input": "prompt"}, "outputs": ["9"]}
    )
    assert _output_size(clone(BBOX_RESOLUTION_GRAPH), no_boxes, []) is None
