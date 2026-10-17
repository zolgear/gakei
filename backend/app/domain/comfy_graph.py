"""ComfyUI のワークフロー(API 形式)のグラフを読む道具(ADR-0052 2章)。

登録時の読み取り(`comfy_analyze`)と保存時の検証(`comfy_workflow.validate_workflow`)が、
同じ「生きている部分」の判定を使うための共通部品。グラフは変えない(読むだけ)。

- 錨: 保存ノード(出力)。`/object_info` があれば `output_node` と入力の型で判定し、無ければ
  クラス名(`Save` を含み、Preview・Compare を含まない)と画像の配線で判定する。
- 切り替え: `ComfySwitchNode` など(`on_true` / `on_false` と、`switch` などの真偽値)を、
  直値(Primitive から配線された直値を含む)で解き、選ばれた側だけを辿る。別のノードの出力で
  切り替わるものは、両方の枝を生きているとみなす。
- ノードの状態: 「出力につながる(live)」「表示だけ(display)」「使われない(unused)」。

DB や HTTP には依存しない純粋な関数群。
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Iterable, Iterator
from typing import Any, Literal

NodeState = Literal["live", "display", "unused"]

# 切り替えの入力名。`on_true` / `on_false` の枝と、どちらを選ぶかの真偽値。
_SWITCH_BRANCHES = ("on_true", "on_false")
_SWITCH_CONTROLS = ("switch", "boolean", "condition")

# 表示だけのノード(画像)。保存ノードではない。
_DISPLAY_MARKERS = ("Preview", "Compare")
# 文字列を表示するノード(`/object_info` が無いときの推定に使う)。ADR-0030 1章。
TEXT_DISPLAY_CLASS_TYPES = frozenset({"PreviewAny", "PreviewText", "ShowText|pysssss", "ShowText"})
# `/object_info` の `output_node: true` でも、画像・動画・音声などを扱うものは文字列の表示にしない。
_NON_TEXT_OUTPUT_MARKERS = ("Image", "Video", "Audio", "Mesh", "Save", "Compare", "Latent")

# 部分編集のマスクを受けるノード(ADR-0052 2章「マスク」)。入力名が `mask` でなくても、
# 出力が LATENT / CONDITIONING のノードは部分編集に使うとみなす。`/object_info` が無いときの表。
INPAINT_MASK_CONSUMERS = frozenset(
    {
        "SetLatentNoiseMask",
        "VAEEncodeForInpaint",
        "InpaintModelConditioning",
        "DifferentialDiffusion",
        "ImageCompositeMasked",
        "LatentCompositeMasked",
    }
)

# 画像の読み込みノード(枠になりうるもの)。生成した画像が届くかの判定にも使う。
_IMAGE_SOURCE_PREFIX = "LoadImage"

# 可変長・番号付きの画像入力(`images.image_1`、`image1`、`image_2`)。
NUMBERED_IMAGE_INPUT_RE = re.compile(r"(?:^|\.)image_?(\d+)$")


def is_wire(value: Any) -> bool:
    """配線([node_id, output_index] の形)かどうか。"""
    return (
        isinstance(value, list)
        and len(value) == 2
        and isinstance(value[0], str)
        and isinstance(value[1], int)
        and not isinstance(value[1], bool)
    )


def is_plain_str(value: Any) -> bool:
    return isinstance(value, str)


def is_plain_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def natural_id_key(node_id: str) -> tuple[Any, ...]:
    """ノード id の並び(`"10" < "459:452" < "461"` のように数として比べる)。"""
    parts: list[Any] = []
    for part in re.split(r"[:.]", node_id):
        if part.isdigit():
            parts.append((0, int(part), ""))
        else:
            parts.append((1, 0, part))
    return tuple(parts)


class WorkflowGraph:
    """テンプレート1つぶんの読み取り。`object_info` は任意(あれば型と出力ノードの判定に使う)。"""

    def __init__(self, template: dict[str, Any], object_info: dict[str, Any] | None = None) -> None:
        self.template = template
        self.object_info = object_info
        self.ids: list[str] = sorted(
            (nid for nid, node in template.items() if isinstance(node, dict)), key=natural_id_key
        )
        # 配線元 → [(受け側のノード, 受け側の入力名, 配線元の出力番号)]
        self.consumers: dict[str, list[tuple[str, str, int]]] = {}
        for nid in self.ids:
            for name, value in self.inputs(nid).items():
                if is_wire(value):
                    self.consumers.setdefault(value[0], []).append((nid, name, value[1]))

    # -- 基本 ------------------------------------------------------------------

    def has(self, node_id: str) -> bool:
        return isinstance(self.template.get(node_id), dict)

    def class_type(self, node_id: str) -> str:
        node = self.template.get(node_id)
        if not isinstance(node, dict):
            return ""
        value = node.get("class_type")
        return value if isinstance(value, str) else ""

    def inputs(self, node_id: str) -> dict[str, Any]:
        node = self.template.get(node_id)
        if not isinstance(node, dict):
            return {}
        inputs = node.get("inputs")
        return inputs if isinstance(inputs, dict) else {}

    def title(self, node_id: str) -> str | None:
        node = self.template.get(node_id)
        if not isinstance(node, dict):
            return None
        meta = node.get("_meta")
        if isinstance(meta, dict) and isinstance(meta.get("title"), str):
            return meta["title"]
        return None

    def has_custom_title(self, node_id: str) -> bool:
        title = self.title(node_id)
        return bool(title) and title != self.class_type(node_id)

    # -- /object_info ------------------------------------------------------------

    def _node_info(self, node_id: str) -> dict[str, Any] | None:
        if self.object_info is None:
            return None
        info = self.object_info.get(self.class_type(node_id))
        return info if isinstance(info, dict) else None

    def knows_class(self, node_id: str) -> bool:
        return self._node_info(node_id) is not None

    def input_spec(self, node_id: str, name: str) -> tuple[Any, Any] | None:
        """`/object_info` の入力1つぶん(`(型, 設定)`)。`values.a` のような可変長は
        先頭の名前でも引く。"""
        info = self._node_info(node_id)
        if info is None:
            return None
        groups = info.get("input")
        if not isinstance(groups, dict):
            return None
        for key in (name, name.split(".")[0]):
            for group_name in ("required", "optional"):
                group = groups.get(group_name)
                if not isinstance(group, dict):
                    continue
                spec = group.get(key)
                if isinstance(spec, list) and spec:
                    return spec[0], (spec[1] if len(spec) > 1 else None)
        return None

    def input_type(self, node_id: str, name: str) -> str | None:
        """入力の型(`STRING`、`INT` など。選択肢は `COMBO`)。分からなければ None。"""
        spec = self.input_spec(node_id, name)
        if spec is None:
            return None
        type_spec = spec[0]
        if isinstance(type_spec, list):
            return "COMBO"
        return type_spec if isinstance(type_spec, str) else None

    def output_types(self, node_id: str) -> list[str] | None:
        info = self._node_info(node_id)
        if info is None:
            return None
        outputs = info.get("output")
        if isinstance(outputs, list):
            return [o if isinstance(o, str) else "COMBO" for o in outputs]
        return None

    # -- 切り替え ----------------------------------------------------------------

    def is_switch(self, node_id: str) -> bool:
        inputs = self.inputs(node_id)
        has_branch = any(name in inputs for name in _SWITCH_BRANCHES)
        has_control = any(name in inputs for name in _SWITCH_CONTROLS)
        return has_branch and has_control

    def switch_control(self, node_id: str) -> tuple[str, str] | None:
        """切り替えの真偽値の置き場所(`(node, input)`)。Primitive から配線されていれば
        Primitive の `value`。別のノードの出力なら None(解けない)。
        """
        if not self.is_switch(node_id):
            return None
        inputs = self.inputs(node_id)
        for name in _SWITCH_CONTROLS:
            if name not in inputs:
                continue
            value = inputs[name]
            if isinstance(value, bool):
                return node_id, name
            if is_wire(value):
                source = value[0]
                if self.class_type(source).startswith("Primitive") and isinstance(
                    self.inputs(source).get("value"), bool
                ):
                    return source, "value"
            return None
        return None

    def switch_choice(self, node_id: str) -> str | None:
        """切り替えで選ばれた枝の入力名(`on_true` / `on_false`)。解けなければ None。"""
        control = self.switch_control(node_id)
        if control is None:
            return None
        value = self.inputs(control[0]).get(control[1])
        if not isinstance(value, bool):
            return None
        return "on_true" if value else "on_false"

    def live_input_items(self, node_id: str) -> Iterator[tuple[str, Any]]:
        """入力の (名前, 値)。切り替えが解ければ、選ばれなかった枝を除く。"""
        chosen = self.switch_choice(node_id) if self.is_switch(node_id) else None
        for name, value in self.inputs(node_id).items():
            if chosen is not None and name in _SWITCH_BRANCHES and name != chosen:
                continue
            yield name, value

    def is_live_edge(self, consumer: str, input_name: str) -> bool:
        """受け側の入力が、切り替えで選ばれなかった枝でないか。"""
        if input_name not in _SWITCH_BRANCHES or not self.is_switch(consumer):
            return True
        chosen = self.switch_choice(consumer)
        return chosen is None or chosen == input_name

    def upstream(self, starts: Iterable[str]) -> set[str]:
        """`starts` から入力の配線を遡って届くノード(`starts` を含む。切り替えは選ばれた側だけ)。"""
        seen: set[str] = set()
        queue = deque(starts)
        while queue:
            nid = queue.popleft()
            if nid in seen or not self.has(nid):
                continue
            seen.add(nid)
            for _name, value in self.live_input_items(nid):
                if is_wire(value):
                    queue.append(value[0])
        return seen

    # -- 出力・表示 ----------------------------------------------------------------

    def _has_wired_image_input(self, node_id: str) -> bool:
        inputs = self.inputs(node_id)
        for name, value in inputs.items():
            if not is_wire(value):
                continue
            input_type = self.input_type(node_id, name)
            if input_type == "IMAGE":
                return True
            if input_type is None and name in ("images", "image"):
                return True
        return False

    def is_display_node(self, node_id: str) -> bool:
        """表示だけのノード(`PreviewImage`、比較のノード、文字列の表示)。"""
        class_type = self.class_type(node_id)
        if any(marker in class_type for marker in _DISPLAY_MARKERS):
            return True
        if class_type in TEXT_DISPLAY_CLASS_TYPES:
            return True
        info = self._node_info(node_id)
        if info is not None and info.get("output_node") is True:
            return not self._is_save_by_info(node_id)
        return False

    def _is_save_by_info(self, node_id: str) -> bool:
        info = self._node_info(node_id)
        if info is None or info.get("output_node") is not True:
            return False
        class_type = self.class_type(node_id)
        if any(marker in class_type for marker in _DISPLAY_MARKERS):
            return False
        return self._has_wired_image_input(node_id)

    def is_save_node(self, node_id: str) -> bool:
        """画像の保存ノード(錨)。"""
        class_type = self.class_type(node_id)
        if any(marker in class_type for marker in _DISPLAY_MARKERS):
            return False
        if self.knows_class(node_id):
            return self._is_save_by_info(node_id)
        return "Save" in class_type and self._has_wired_image_input(node_id)

    def is_text_display_node(self, node_id: str) -> bool:
        class_type = self.class_type(node_id)
        if class_type in TEXT_DISPLAY_CLASS_TYPES:
            return True
        info = self._node_info(node_id)
        if info is None or info.get("output_node") is not True:
            return False
        return not any(marker in class_type for marker in _NON_TEXT_OUTPUT_MARKERS)

    def marked_output_ids(self) -> list[str]:
        return [nid for nid in self.ids if self.title(nid) == "gakei:output"]

    def save_node_ids(self) -> list[str]:
        return [nid for nid in self.ids if self.is_save_node(nid)]

    def anchor_ids(self, extra: Iterable[str] = ()) -> list[str]:
        """錨: 保存ノード、タイトル `gakei:output`、`extra`(保存時に選ばれた出力。表示だけの
        ノードは除く)。"""
        anchors = set(self.save_node_ids()) | set(self.marked_output_ids())
        for nid in extra:
            if self.has(nid) and not self.is_display_node(nid):
                anchors.add(nid)
        return sorted(anchors, key=natural_id_key)

    def node_states(self, extra_anchors: Iterable[str] = ()) -> dict[str, NodeState]:
        """各ノードを「出力につながる」「表示だけ」「使われない」に分ける(ADR-0052 2章)。"""
        live = self.upstream(self.anchor_ids(extra_anchors))
        display_roots = [nid for nid in self.ids if nid not in live and self.is_display_node(nid)]
        display = self.upstream(display_roots) - live
        states: dict[str, NodeState] = {}
        for nid in self.ids:
            if nid in live:
                states[nid] = "live"
            elif nid in display:
                states[nid] = "display"
            else:
                states[nid] = "unused"
        return states

    # -- 道筋 ------------------------------------------------------------------

    def path_to_anchor(
        self,
        start_node: str,
        start_input: str | None,
        live: set[str],
        anchors: set[str],
        prefer: str | None = None,
    ) -> list[tuple[str, str | None]]:
        """`start_node` から配線を下って錨に届く最短の道筋。各段は `(ノード, 入ってきた入力名)`。
        先頭は `(start_node, start_input)`。届かなければ先頭だけを返す。同じ長さの道筋が
        複数あるときは、受け側の入力名が `prefer`(`positive` など)のもの、出力番号の小さいもの
        を選ぶ。
        """
        first = (start_node, start_input)
        if start_node in anchors or start_node not in live:
            return [first]
        parents: dict[str, tuple[str, str]] = {}
        queue = deque([start_node])
        seen = {start_node}
        found: str | None = None
        while queue and found is None:
            cur = queue.popleft()
            for consumer, name, _index in sorted(
                self.consumers.get(cur, []),
                key=lambda c: (c[1] != prefer, c[2], natural_id_key(c[0]), c[1]),
            ):
                if consumer in seen or consumer not in live:
                    continue
                if not self.is_live_edge(consumer, name):
                    continue
                seen.add(consumer)
                parents[consumer] = (cur, name)
                if consumer in anchors:
                    found = consumer
                    break
                queue.append(consumer)
        if found is None:
            return [first]
        steps: list[tuple[str, str | None]] = []
        cur = found
        while cur != start_node:
            prev, name = parents[cur]
            steps.append((cur, name))
            cur = prev
        steps.reverse()
        return [first, *steps]

    # -- 画像・マスク --------------------------------------------------------------

    def is_image_source(self, node_id: str) -> bool:
        return self.class_type(node_id).startswith(_IMAGE_SOURCE_PREFIX)

    def generated_image_reaches(self, node_id: str) -> bool:
        """出力のノードに、生成した画像が届くか。入力画像(`LoadImage*`)を素通しする
        ノードだけから画像を受けているもの、配線を1本も持たないものは届かないとみなす。
        """
        wires = [value for _name, value in self.live_input_items(node_id) if is_wire(value)]
        if not wires:
            return False
        input_only = self._input_only_nodes()
        return any(wire[0] not in input_only for wire in wires)

    def _input_only_nodes(self) -> set[str]:
        """入力画像の読み込みと、それだけを配線元に持つノード(拡大縮小など)。"""
        result = {nid for nid in self.ids if self.is_image_source(nid)}
        changed = True
        while changed:
            changed = False
            for nid in self.ids:
                if nid in result:
                    continue
                wires = [value for _n, value in self.live_input_items(nid) if is_wire(value)]
                if wires and all(wire[0] in result for wire in wires):
                    result.add(nid)
                    changed = True
        return result

    def is_mask_op(self, node_id: str) -> bool:
        """MASK を加工して MASK を返すノード(`InvertMask`、`GrowMask` など)。"""
        outputs = self.output_types(node_id)
        if outputs is not None:
            return outputs == ["MASK"]
        class_type = self.class_type(node_id)
        if class_type in INPAINT_MASK_CONSUMERS:
            return False
        if class_type.startswith(("SetLatent", "Load")):
            return False
        return class_type.endswith("Mask") and is_wire(self.inputs(node_id).get("mask"))

    def _is_inpaint_consumer(self, node_id: str, input_name: str) -> bool:
        if input_name == "mask":
            return True
        if self.class_type(node_id) in INPAINT_MASK_CONSUMERS:
            return True
        outputs = self.output_types(node_id)
        return outputs is not None and any(o in ("LATENT", "CONDITIONING") for o in outputs)

    def mask_uses(
        self, node_id: str, output_index: int, live: set[str]
    ) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        """MASK 出力の使われ方。`(部分編集に使う受け側, それ以外の受け側)` を返す。
        MASK を加工するノード(`InvertMask` など)は越えて辿る。生きているノードだけを見る。
        """
        inpaint: list[tuple[str, str]] = []
        other: list[tuple[str, str]] = []
        seen: set[tuple[str, int]] = set()
        queue = deque([(node_id, output_index, 0)])
        while queue:
            cur, index, depth = queue.popleft()
            if (cur, index) in seen or depth > 6:
                continue
            seen.add((cur, index))
            for consumer, name, out_index in self.consumers.get(cur, []):
                if out_index != index or consumer not in live:
                    continue
                if not self.is_live_edge(consumer, name):
                    continue
                if self.is_mask_op(consumer):
                    queue.append((consumer, 0, depth + 1))
                    continue
                if self._is_inpaint_consumer(consumer, name):
                    inpaint.append((consumer, name))
                else:
                    other.append((consumer, name))
        return inpaint, other
