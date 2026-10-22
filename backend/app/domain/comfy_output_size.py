"""ComfyUI のグラフの整数の入力値(出力の幅・高さ)を決める(ADR-0053 6章)。

四角の差し込み先(Create Bounding Boxes)の `width` / `height` は、定数のほか、整数の Primitive
(`PrimitiveInt` など、入力 `value` に値を持つノード)や ComfyUI 本体の Resolution Selector
(`comfy_extras/nodes_resolution.py`。出力 0 が幅、1 が高さ)から配線される。

- `resolve_int`: 値を差し込んだ後のグラフから、整数を決める(`comfy_bounding_boxes.inject`)。
- `describe_size`: テンプレートと「フォームの項目が差し込む入力」から、大きさの決まり方
  (`OutputSizeRule`)を組み立てる。フォームはこれを今の値で計算して白紙のキャンバスの大きさに
  する(`frontend/src/features/run-form/outputSize.ts`。同じ表・同じ丸め)。

DB と HTTP には依存しない純粋な関数だけを置く。決められないときは None。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from app.providers.base import OutputDimension, OutputSizeOperand, OutputSizeRule

RESOLUTION_SELECTOR = "ResolutionSelector"
# Resolution Selector の選択肢と比。ノードの表(`ASPECT_RATIOS`)と同じ。知らない選択肢は
# 決められない扱いにする(文字列の先頭を読んで推測しない)。
ASPECT_RATIOS: dict[str, tuple[int, int]] = {
    "1:1 (Square)": (1, 1),
    "2:3 (Portrait Photo)": (2, 3),
    "3:2 (Photo)": (3, 2),
    "3:4 (Portrait Standard)": (3, 4),
    "4:3 (Standard)": (4, 3),
    "9:16 (Portrait Widescreen)": (9, 16),
    "16:9 (Widescreen)": (16, 9),
    "21:9 (Ultrawide)": (21, 9),
}
# 入力が無いときのノードの既定値。
DEFAULT_ASPECT_RATIO = "1:1 (Square)"
DEFAULT_MEGAPIXELS = 1.0
DEFAULT_MULTIPLE = 8

# 配線を辿る深さの上限(循環の備え)。
_MAX_DEPTH = 8


def _is_link(value: Any) -> bool:
    return isinstance(value, list) and len(value) == 2 and isinstance(value[0], (str, int))


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _positive_int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def resolution_selector_size(
    aspect_ratio: Any, megapixels: Any, multiple: Any
) -> tuple[int, int] | None:
    """Resolution Selector の幅・高さ。ノードの `execute` と同じ式・同じ丸め(Python の `round`)。"""
    if not isinstance(aspect_ratio, str) or aspect_ratio not in ASPECT_RATIOS:
        return None
    if not _is_number(megapixels) or megapixels <= 0:
        return None
    if _positive_int(multiple) is None:
        return None
    w_ratio, h_ratio = ASPECT_RATIOS[aspect_ratio]
    total_pixels = megapixels * 1024 * 1024
    scale = math.sqrt(total_pixels / (w_ratio * h_ratio))
    width = round(w_ratio * scale / multiple) * multiple
    height = round(h_ratio * scale / multiple) * multiple
    if width <= 0 or height <= 0:
        return None
    return width, height


def _node(graph: Mapping[str, Any], node_id: Any) -> dict[str, Any] | None:
    node = graph.get(str(node_id))
    if isinstance(node, dict) and isinstance(node.get("inputs"), dict):
        return node
    return None


def _resolve_scalar(graph: Mapping[str, Any], value: Any, depth: int) -> Any:
    """定数ならそのまま。配線なら、入力 `value` に値を持つノード(Primitive)を辿る。"""
    if depth > _MAX_DEPTH:
        return None
    if not _is_link(value):
        return value if isinstance(value, (str, int, float)) else None
    node = _node(graph, value[0])
    if node is None or "value" not in node["inputs"]:
        return None
    return _resolve_scalar(graph, node["inputs"]["value"], depth + 1)


def _selector_input(node: dict[str, Any], name: str, default: Any) -> Any:
    return node["inputs"].get(name, default)


def resolve_int(graph: Mapping[str, Any], value: Any, *, _depth: int = 0) -> int | None:
    """グラフの整数の入力値を決める。決められなければ None。

    定数、入力 `value` に整数を持つノードへの配線、Resolution Selector の出力 0 / 1 への配線を扱う。
    """
    if _depth > _MAX_DEPTH:
        return None
    if not _is_link(value):
        return _positive_int(value)
    node = _node(graph, value[0])
    if node is None:
        return None
    if node.get("class_type") == RESOLUTION_SELECTOR:
        output = value[1]
        if output not in (0, 1) or isinstance(output, bool):
            return None
        args = [
            _resolve_scalar(graph, _selector_input(node, name, default), _depth + 1)
            for name, default in (
                ("aspect_ratio", DEFAULT_ASPECT_RATIO),
                ("megapixels", DEFAULT_MEGAPIXELS),
                ("multiple", DEFAULT_MULTIPLE),
            )
        ]
        size = resolution_selector_size(*args)
        return None if size is None else size[output]
    if "value" in node["inputs"]:
        return resolve_int(graph, node["inputs"]["value"], _depth=_depth + 1)
    return None


# -- フォーム向けの決まり方 -----------------------------------------------------------

# (ノード, 入力) → その入力に値を差し込むフォームの項目の名前。
ParamTargets = Mapping[tuple[str, str], str]


def _describe_operand(
    template: Mapping[str, Any], node_id: str, input_name: str, targets: ParamTargets, depth: int
) -> OutputSizeOperand | None:
    """1つの入力の値を、フォームの項目か定数で表す。"""
    if depth > _MAX_DEPTH:
        return None
    param = targets.get((node_id, input_name))
    if param is not None:
        return OutputSizeOperand(param=param)
    node = _node(template, node_id)
    if node is None or input_name not in node["inputs"]:
        return None
    value = node["inputs"][input_name]
    if not _is_link(value):
        return OutputSizeOperand(value=value) if isinstance(value, (str, int, float)) else None
    source = _node(template, value[0])
    if source is None or "value" not in source["inputs"]:
        return None
    return _describe_operand(template, str(value[0]), "value", targets, depth + 1)


def _describe_dimension(
    template: Mapping[str, Any],
    node_id: str,
    input_name: str,
    targets: ParamTargets,
    depth: int = 0,
) -> OutputDimension | None:
    if depth > _MAX_DEPTH:
        return None
    param = targets.get((node_id, input_name))
    if param is not None:
        return OutputDimension(source="int", value=OutputSizeOperand(param=param))
    node = _node(template, node_id)
    if node is None or input_name not in node["inputs"]:
        return None
    value = node["inputs"][input_name]
    if not _is_link(value):
        constant = _positive_int(value)
        if constant is None:
            return None
        return OutputDimension(source="int", value=OutputSizeOperand(value=constant))
    source_id = str(value[0])
    source = _node(template, source_id)
    if source is None:
        return None
    if source.get("class_type") == RESOLUTION_SELECTOR:
        output = value[1]
        if output not in (0, 1) or isinstance(output, bool):
            return None
        operands: dict[str, OutputSizeOperand] = {}
        for name, default in (
            ("aspect_ratio", DEFAULT_ASPECT_RATIO),
            ("megapixels", DEFAULT_MEGAPIXELS),
            ("multiple", DEFAULT_MULTIPLE),
        ):
            if (source_id, name) not in targets and name not in source["inputs"]:
                operands[name] = OutputSizeOperand(value=default)
                continue
            operand = _describe_operand(template, source_id, name, targets, depth + 1)
            if operand is None:
                return None
            operands[name] = operand
        return OutputDimension(source="resolution_selector", output=output, **operands)
    if "value" in source["inputs"]:
        return _describe_dimension(template, source_id, "value", targets, depth + 1)
    return None


def describe_size(
    template: Mapping[str, Any],
    node_id: str,
    targets: ParamTargets,
    *,
    width_input: str = "width",
    height_input: str = "height",
) -> OutputSizeRule | None:
    """ノード `node_id` の幅・高さの決まり方。どちらかが決められなければ None。"""
    width = _describe_dimension(template, node_id, width_input, targets)
    height = _describe_dimension(template, node_id, height_input, targets)
    if width is None or height is None:
        return None
    return OutputSizeRule(width=width, height=height)
