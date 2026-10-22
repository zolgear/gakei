"""ComfyUI の Create Bounding Boxes への四角の差し込み(ADR-0053 6章)。

ComfyUI 本体のノード `CreateBoundingBoxes`(`comfy_extras/nodes_bounding_boxes.py`)の
`bboxes` 入力は、四角の「要素」の一覧を JSON の文字列でも受ける。要素は
`{"type": "obj" | "text", "bbox": [ymin, xmin, ymax, xmax], "text": ..., "desc": ...}` で、
`bbox` は 0〜1000(ノードが自分の `width` / `height` でピクセルに直す)。

値の形は ComfyUI の側で変わりうるので、変換と形の確認をこの1か所に閉じ込める。DB と HTTP には
依存しない純粋な関数だけを置く。
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from typing import Any, Literal

from app.providers.base import RegionKind

CLASS_TYPE = "CreateBoundingBoxes"
# 四角を差し込む入力と、ノードが「前回キャンバスを初期化した上流の値」を覚えておく入力。
BBOXES_INPUT = "bboxes"
LAST_INCOMING_INPUT = "last_incoming"
# キャンバスの状態(ピクセルの四角の一覧)。PNG に埋め込まれたグラフを読み込み直すと、これが出る。
EDITOR_STATE_INPUT = "editor_state"

# ComfyUI で選べる種類(ADR-0053 6章)。「そのまま残す」「消す」はノードの形で表せず(送ると物を
# 足す指示になる)、「動かす」は元の四角を持てない。
REGION_KINDS: tuple[RegionKind, ...] = ("change", "add")

# ノードの座標の格子(0〜1000)。
GRID = 1000


def _grid(value: float) -> int:
    """0〜1 の割合を 0〜1000 の整数にする(四捨五入。範囲に切り詰める)。"""
    return max(0, min(GRID, math.floor(value * GRID + 0.5)))


def to_bbox(box: Sequence[float]) -> list[int]:
    """GAKEI の `[x0, y0, x1, y1]`(0〜1)を、ノードの `[ymin, xmin, ymax, xmax]`(0〜1000)にする。

    丸めで幅か高さが 0 になる小さな四角は、1 目盛りだけ広げる(空の四角を送らない)。
    """
    x0, y0, x1, y1 = box
    xmin, xmax = sorted((_grid(x0), _grid(x1)))
    ymin, ymax = sorted((_grid(y0), _grid(y1)))
    if xmax == xmin:
        xmin, xmax = (xmin - 1, xmax) if xmax == GRID else (xmin, xmax + 1)
    if ymax == ymin:
        ymin, ymax = (ymin - 1, ymax) if ymax == GRID else (ymin, ymax + 1)
    return [ymin, xmin, ymax, xmax]


def to_elements(regions: Sequence[Any]) -> list[dict[str, Any]]:
    """四角(`RegionBox`)の一覧を、ノードの要素の一覧にする。並びは番号の順。

    - `desc` は指示、無ければ名前、どちらも無ければ空。
    - 描く文字があれば `type: "text"` にして `text` に入れる。無ければ `obj`。
    - 種類はノードに送らない(選べるのは「変える」「足す」だけ。`REGION_KINDS`)。
    """
    elements: list[dict[str, Any]] = []
    for region in sorted(regions, key=lambda r: r.number):
        desc = region.instruction or region.label or ""
        element_type: Literal["obj", "text"] = "text" if region.text is not None else "obj"
        element: dict[str, Any] = {"type": element_type, "bbox": to_bbox(region.box)}
        if element_type == "text":
            element["text"] = region.text
        element["desc"] = desc
        elements.append(element)
    return elements


def encode(regions: Sequence[Any]) -> str:
    """`bboxes` に入れる値(要素の一覧の JSON の文字列)。同じ四角からは同じ文字列になる。"""
    return json.dumps(to_elements(regions), ensure_ascii=False, separators=(",", ":"))


def to_editor_state(elements: Sequence[dict[str, Any]], width: int, height: int) -> list[dict]:
    """要素の一覧を、キャンバスの状態(`editor_state`)の四角の一覧(ピクセル)にする。

    ノードの `elements_to_boxes` と同じ計算・同じ丸め(Python の `round`。偶数丸め)にする。
    `width` / `height` はノードの入力(ピクセルの格子)。
    """
    boxes: list[dict[str, Any]] = []
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
                    "palette": element.get("color_palette", []) or [],
                },
            }
        )
    return boxes


def _int_constant(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def _resolve_dimension(graph: dict[str, Any], value: Any) -> int | None:
    """ノードの `width` / `height` の値を整数で決める。決められなければ None。

    定数ならそのまま。配線(`[node_id, index]`)なら、配線先が入力 `value` に整数の定数を持つ
    ノード(`PrimitiveInt` など)のときだけ辿る。
    """
    constant = _int_constant(value)
    if constant is not None:
        return constant
    if isinstance(value, list) and len(value) == 2:
        source = graph.get(str(value[0]))
        if isinstance(source, dict) and isinstance(source.get("inputs"), dict):
            return _int_constant(source["inputs"].get("value"))
    return None


def inject(graph: dict[str, Any], node_id: str, value: str) -> None:
    """グラフの `node_id` のノードに四角を差し込む(`graph` を書き換える)。

    `bboxes` が配線されていても定数で上書きする。`last_incoming` を空にして、ノードがキャンバスの
    値(`editor_state`)ではなく差し込んだ値を使うようにする。

    空の配列は `{"__value__": []}` で包む。API 形式では配列の値が配線とみなされるため、ComfyUI は
    配列の値をこの形で送る(画面の書き出しと同じ形。実行時に中身が取り出される)。包みが取り出され
    なくても、ノードは配列でない `last_incoming` を空とみなすので、同じく差し込んだ値を使う。

    `editor_state` にも同じ四角をピクセルで書く。出力 PNG に埋め込まれるのはこのグラフで、
    ComfyUI に読み込み直すとソケットの `bboxes` は落ち、キャンバスにはこちらが出るため。
    ノードの `width` / `height` が決められなければ、古い四角を残さないよう空にする。
    """
    inputs = graph[node_id]["inputs"]
    inputs[BBOXES_INPUT] = value
    inputs[LAST_INCOMING_INPUT] = {"__value__": []}
    width = _resolve_dimension(graph, inputs.get("width"))
    height = _resolve_dimension(graph, inputs.get("height"))
    state: list[dict[str, Any]] = []
    if width is not None and height is not None:
        state = to_editor_state(json.loads(value), width, height)
    inputs[EDITOR_STATE_INPUT] = {"__value__": state}


ObjectInfoProblem = Literal["node_missing", "input_not_string"]


def _accepts_string(type_spec: Any) -> bool:
    """`/object_info` の入力の型が文字列を受けるか。複数の型を受ける入力は `A,B,C` の形で並ぶ。"""
    if isinstance(type_spec, str):
        names = {name.strip() for name in type_spec.split(",")}
        return "STRING" in names or "*" in names
    if isinstance(type_spec, list):
        # 選択肢(COMBO)の並びではなく、型名の並びとして書かれた形にも備える。
        return any(isinstance(name, str) and name in ("STRING", "*") for name in type_spec)
    return False


def check_object_info(object_info: Any) -> ObjectInfoProblem | None:
    """`/object_info/CreateBoundingBoxes` の応答から、差し込める形かを確かめる(ADR-0053 6章)。

    ノードが無い(未知のノードには空の応答が返る)なら `node_missing`、`bboxes` 入力が無いか文字列を
    受けないなら `input_not_string`。差し込めるなら None。
    """
    if not isinstance(object_info, dict):
        return "node_missing"
    info = object_info.get(CLASS_TYPE)
    if not isinstance(info, dict):
        return "node_missing"
    inputs = info.get("input")
    if not isinstance(inputs, dict):
        return "input_not_string"
    for section in ("required", "optional"):
        group = inputs.get(section)
        if not isinstance(group, dict):
            continue
        spec = group.get(BBOXES_INPUT)
        if isinstance(spec, list) and spec:
            return None if _accepts_string(spec[0]) else "input_not_string"
    return "input_not_string"
