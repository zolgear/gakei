"""ComfyUI のワークフローの読み取り(analyze。ADR-0052 2章)。保存はしない。

出力(保存ノード)を錨にし、出力に届く「生きている部分」だけを見て、差し込み先と公開
パラメーターの候補を提案する。項目ごとに確信度(`marked` / `unique` / `ranked` / `ask` /
`absent`)、理由の一文、出力までの道筋を返す。決めきれない項目は推測で埋めずに問う。

「生きている部分」の判定は保存時の検証(`comfy_workflow.check_workflow`)と同じ
`WorkflowGraph` を使う。提案した差し込み先にも同じ検証を掛けて応答に含める。

DB や HTTP には依存しない純粋な関数群。テンプレートは変えない。
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.domain.comfy_graph import (
    NUMBERED_IMAGE_INPUT_RE,
    NodeState,
    WorkflowGraph,
    is_plain_int,
    is_wire,
    natural_id_key,
)
from app.domain.comfy_workflow import (
    ExposedParam,
    InputRef,
    MaskBinding,
    SuggestedBindings,
    ValidationIssue,
    check_workflow,
)
from app.domain.secret_values import find_secret_inputs
from app.i18n import t
from app.providers.base import ParamType

Confidence = Literal["marked", "unique", "ranked", "ask", "absent"]
BindingField = Literal[
    "prompt",
    "negative_prompt",
    "seed",
    "width",
    "height",
    "batch_size",
    "images",
    "mask",
    "outputs",
    "final_prompt",
]
ParamTier = Literal["recommended", "other", "hidden"]
# 「公開しない」の理由(画面が文にする)。推奨・その他では None。
HiddenReason = Literal["unused_branch", "secret", "fixed_setting", "file_or_model", "fixed_text"]

_FIELD_ORDER: tuple[BindingField, ...] = (
    "prompt",
    "negative_prompt",
    "seed",
    "width",
    "height",
    "batch_size",
    "images",
    "mask",
    "outputs",
    "final_prompt",
)


# -- 応答の形 -----------------------------------------------------------------


class PathStep(BaseModel):
    """道筋の1段。`input` はそのノードに入ってきた入力名(先頭は差し込み先の入力名)。"""

    node: str
    class_type: str | None = None
    input: str | None = None


class BindingCandidate(BaseModel):
    """`ask` / `ranked` の項目の候補(順位の高い順)。"""

    node: str
    input: str | None = None
    score: float
    # 画面に出す名前(タイトル、無ければ class_type)。
    label: str
    path: list[PathStep] = Field(default_factory=list)


class BindingReading(BaseModel):
    """差し込み先1項目の読み取り結果。値そのものは `suggested_bindings` にある。"""

    field: BindingField
    confidence: Confidence
    reason: str
    # 値ごとの道筋(seed・画像の枠は複数)。差し込み先から出力までの順。
    paths: list[list[PathStep]] = Field(default_factory=list)
    candidates: list[BindingCandidate] = Field(default_factory=list)
    # `ask` のときの問いの文。
    question: str | None = None


class AnalyzeWarning(BaseModel):
    """analyze の警告1件。`reading` は読み取り(`readings`)で同じことを示している警告で、
    カードの画面では出さない。`general` は読み取りでは扱わない警告(秘密に見える値など)。"""

    kind: Literal["reading", "general"]
    field: BindingField | None = None
    message: str


class ParamTierInfo(BaseModel):
    """公開パラメーターの候補の段(推奨 / その他 / 公開しない)。"""

    name: str
    node: str
    input: str
    tier: ParamTier
    # `hidden` のときだけ、公開しない理由。
    hidden_reason: HiddenReason | None = None


@dataclass
class AnalyzeResult:
    nodes: list[dict[str, Any]]
    suggested_bindings: SuggestedBindings
    suggested_operation: Literal["generate", "edit"]
    candidate_params: list[ExposedParam]
    warnings: list[str] = field(default_factory=list)
    # `warnings` と同じ順・同じ文に、読み取りで扱っているかの種別を付けたもの。
    warning_items: list[AnalyzeWarning] = field(default_factory=list)
    readings: list[BindingReading] = field(default_factory=list)
    param_tiers: list[ParamTierInfo] = field(default_factory=list)
    validation: list[ValidationIssue] = field(default_factory=list)


# -- 判定の表 -------------------------------------------------------------------

_TITLE_SLOTS = {
    "gakei:prompt",
    "gakei:negative",
    "gakei:seed",
    "gakei:mask",
    "gakei:output",
    "gakei:final_prompt",
}
# 画像の枠のタイトル規約: `gakei:image`(枠1つ)、`gakei:image1`、`gakei:image2`…(番号順)。
_IMAGE_TITLE_RE = re.compile(r"^gakei:image(\d*)$")

# エンコーダーの文字列の入力名(`/object_info` が無いとき)。
_ENCODER_TEXT_NAMES = ("text", "prompt", "text_g", "text_l", "negative_prompt")

# 文字列を運ばない入力名(配線を遡るときに越えない)。
_NON_STRING_INPUTS = frozenset(
    {
        "clip",
        "model",
        "vae",
        "image",
        "images",
        "mask",
        "latent",
        "latent_image",
        "samples",
        "pixels",
        "switch",
        "boolean",
        "condition",
        "control_net",
        "noise",
        "guider",
        "sampler",
        "sigmas",
        "positive",
        "negative",
        "conditioning",
        "cond1",
        "cond2",
        "upscale_model",
    }
)
# 可変長の入力で、文字列を運ぶもの(`values.a` など)。
_STRING_GROUP_PREFIXES = ("values.", "strings.", "texts.", "inputs.")
# 文字列を出さないノード(`/object_info` が無いときの配線元の判定)。
_NON_STRING_SOURCE_RE = re.compile(
    r"Loader|^Load|^Empty|Sampler|VAE|TextEncode|ConditioningZeroOut|Guider"
)
_STRINGISH_TYPES = frozenset({"STRING", "*", "COMFY_MATCHTYPE_V3", "COMFY_AUTOGROW_V3"})

# 根(配線の無い文字列の直値)として見てよい入力名(`/object_info` が無いとき)。
_ROOT_NAME_RE = re.compile(
    r"^(value|text|prompt|string(_\w+)?|text_[gl]|f_string|source|input|\w*prompt)$"
)
_ROOT_NAME_EXCLUDE = frozenset(
    {"regex_pattern", "replace", "delimiter", "flags", "filename_prefix"}
)

# 根の順位付け(タイトルの語)。
_PROMPT_GOOD = re.compile(
    r"prompt|指示|instruction|入力|input|差し替え|ここ|user|caption|positive|テキスト|text", re.I
)
_PROMPT_BAD = re.compile(
    r"system|suffix|prefix|接頭|接尾|template|テンプレ|書式|format"
    r"|do not edit|編集しない|negative|ネガ",
    re.I,
)
_RANK_MARGIN = 2.0
_LONG_TEXT = 400

# 公開パラメーターの推奨(入力名)。
_RECOMMENDED_INPUTS = frozenset(
    {"steps", "cfg", "sampler_name", "scheduler", "denoise", "guidance"}
)
_FILE_VALUE_RE = re.compile(
    r"\.(safetensors|ckpt|pt|pth|bin|gguf|onnx|png|jpe?g|webp|json|yaml)$", re.I
)
_HIDDEN_INPUTS = frozenset({"filename_prefix", "device", "weight_dtype"})
_FIXED_TEXT = 200

_PROMPT_CANDIDATE_MAX_COUNT = 5

# マスクの方式が GAKEI から渡せない(キャンバスに直接描くなど)ノード。
_UNSUPPORTED_MASK_MARKERS = ("Painter", "MaskEditor")


# -- 内部の型 -------------------------------------------------------------------


@dataclass
class _Root:
    node: str
    input: str
    depth: int
    # 根の値が入っていく先(受け側のノード, 受け側の入力名)。書式の位置の判定に使う。
    via: tuple[str, str] | None


@dataclass
class _StringTrace:
    roots: list[_Root] = field(default_factory=list)
    # 遡った途中のノードと深さ。
    visited: dict[str, int] = field(default_factory=dict)


@dataclass
class _Ranked:
    root: _Root
    score: float


def _root_message(field_name: str, kind: str, **params: Any) -> str:
    """プロンプト・ネガティブの根の理由と問いの文(i18n のキーはリテラルで書く)。"""
    if field_name == "prompt":
        messages = {
            "unique": lambda: t("comfyui.analyze.prompt.unique", **params),
            "ranked": lambda: t("comfyui.analyze.prompt.ranked", **params),
            "ask": lambda: t("comfyui.analyze.prompt.ask", **params),
            "noRoot": lambda: t("comfyui.analyze.prompt.noRoot", **params),
            "question": lambda: t("comfyui.analyze.question.prompt", **params),
        }
    else:
        messages = {
            "unique": lambda: t("comfyui.analyze.negative.unique", **params),
            "ranked": lambda: t("comfyui.analyze.negative.ranked", **params),
            "ask": lambda: t("comfyui.analyze.negative.ask", **params),
            "noRoot": lambda: t("comfyui.analyze.negative.noRoot", **params),
            "question": lambda: t("comfyui.analyze.question.negative_prompt", **params),
        }
    return messages[kind]()


# -- 読み取り -------------------------------------------------------------------


class _Reader:
    def __init__(self, template: dict[str, Any], object_info: dict[str, Any] | None) -> None:
        self.template = template
        self.g = WorkflowGraph(template, object_info)
        self.states: dict[str, NodeState] = self.g.node_states()
        self.live: set[str] = {nid for nid, s in self.states.items() if s == "live"}
        self.anchors: set[str] = set(self.g.anchor_ids())
        if self.live:
            scope = self.live
        else:
            # 保存ノードが無い: 表示のノードから遡れる範囲、それも無ければ全体を読む(提案は
            # 返すが、出力が無いことは警告する)。
            display_roots = [nid for nid in self.g.ids if self.g.is_display_node(nid)]
            scope = self.g.upstream(display_roots) if display_roots else set(self.g.ids)
        self.scope_ids = [nid for nid in self.g.ids if nid in scope]
        self.scope_set = set(self.scope_ids)
        self.marked: dict[str, list[str]] = {}
        for nid in self.g.ids:
            title = self.g.title(nid)
            if title in _TITLE_SLOTS:
                self.marked.setdefault(title, []).append(nid)
        self.used: dict[tuple[str, str], str] = {}
        self.recommended: set[tuple[str, str]] = set()
        self.readings: dict[BindingField, BindingReading] = {}
        self.warnings: list[str] = []
        # 正のエンコーダーの (ノード, 入力名) と、その文字列の遡り。最終プロンプトにも使う。
        self.positive_inputs: list[tuple[str, str]] = []
        self.positive_traces: list[_StringTrace] = []

    # -- 共通 --------------------------------------------------------------------

    def _label(self, node_id: str) -> str:
        if self.g.has_custom_title(node_id):
            return self.g.title(node_id) or node_id
        return self.g.class_type(node_id) or node_id

    def _path(
        self, node_id: str, input_name: str | None, field_name: str | None = None
    ) -> list[PathStep]:
        prefer = {"prompt": "positive", "negative_prompt": "negative"}.get(field_name or "")
        steps = self.g.path_to_anchor(node_id, input_name, self.live, self.anchors, prefer)
        return [
            PathStep(node=nid, class_type=self.g.class_type(nid) or None, input=name)
            for nid, name in steps
        ]

    def _set(
        self,
        field_name: BindingField,
        confidence: Confidence,
        reason: str,
        *,
        paths: list[list[PathStep]] | None = None,
        candidates: list[BindingCandidate] | None = None,
        question: str | None = None,
    ) -> None:
        self.readings[field_name] = BindingReading(
            field=field_name,
            confidence=confidence,
            reason=reason,
            paths=paths or [],
            candidates=candidates or [],
            question=question,
        )

    def _claim(self, field_name: BindingField, ref: InputRef) -> str | None:
        """差し込み先を使う。ほかの項目が使っていれば、その項目名を返す(重複させない)。"""
        key = (ref.node, ref.input)
        if key in self.used:
            return self.used[key]
        self.used[key] = field_name
        return None

    def _single(
        self, field_name: BindingField, ref: InputRef, confidence: Confidence, reason: str
    ) -> InputRef | None:
        """1つの差し込み先を確定する。重複していれば `absent` にする。"""
        other = self._claim(field_name, ref)
        if other is not None:
            self._set(
                field_name,
                "absent",
                t("comfyui.analyze.sharedWith", node=ref.node, input=ref.input, field=other),
            )
            return None
        self._set(
            field_name, confidence, reason, paths=[self._path(ref.node, ref.input, field_name)]
        )
        return ref

    def _marked_text_ref(self, node_id: str) -> InputRef | None:
        inputs = self.g.inputs(node_id)
        for name in ("text", "value", "prompt"):
            if isinstance(inputs.get(name), str):
                return InputRef(node=node_id, input=name)
        for name, value in inputs.items():
            if isinstance(value, str):
                return InputRef(node=node_id, input=name)
        return None

    # -- サンプラーとエンコーダー ------------------------------------------------------

    def _samplers(self) -> list[tuple[str, str, str | None]]:
        """`(ノード, 正の条件付けの入力名, 負の条件付けの入力名)`。モデルと条件付けを受ける
        ノード(KSampler、CFGGuider、DualCFGGuider、BasicGuider など。class_type は決め打ちしない)。
        """
        found: list[tuple[str, str, str | None]] = []
        for nid in self.scope_ids:
            inputs = self.g.inputs(nid)
            if not is_wire(inputs.get("model")):
                continue
            negative = "negative" if is_wire(inputs.get("negative")) else None
            for name in ("positive", "cond1", "conditioning"):
                if is_wire(inputs.get(name)):
                    found.append((nid, name, negative if name != "conditioning" else None))
                    break
        return found

    def _conditioning_input(self, node_id: str) -> str | None:
        inputs = self.g.inputs(node_id)
        for name, value in inputs.items():
            if is_wire(value) and self.g.input_type(node_id, name) == "CONDITIONING":
                return name
        for name, value in inputs.items():
            if is_wire(value) and "conditioning" in name:
                return name
        return None

    def _encoder_text_inputs(self, node_id: str) -> list[str]:
        inputs = self.g.inputs(node_id)
        if self.g.knows_class(node_id):
            names = [
                name
                for name, value in inputs.items()
                if self.g.input_type(node_id, name) == "STRING"
                and (isinstance(value, str) or is_wire(value))
            ]
            if names:
                return names
        return [
            name
            for name in _ENCODER_TEXT_NAMES
            if name in inputs and (isinstance(inputs[name], str) or is_wire(inputs[name]))
        ]

    def _trace_encoder(self, wire: Any) -> tuple[str | None, int, str]:
        """条件付けの配線を遡ってエンコーダーを探す。`(ノード, 出力番号, 状態)`。
        状態は `ok`、`zeroed`(ConditioningZeroOut で空にしている)、`none`。
        """
        hops = 0
        while is_wire(wire) and hops < 12:
            nid, index = wire[0], wire[1]
            if not self.g.has(nid):
                return None, 0, "none"
            if self.g.is_switch(nid):
                chosen = self.g.switch_choice(nid)
                if chosen is None:
                    return None, 0, "none"
                wire = self.g.inputs(nid).get(chosen)
                hops += 1
                continue
            if self.g.class_type(nid) == "ConditioningZeroOut":
                return None, 0, "zeroed"
            if self._encoder_text_inputs(nid):
                return nid, index, "ok"
            inputs = self.g.inputs(nid)
            if is_wire(inputs.get("positive")) and is_wire(inputs.get("negative")):
                # InpaintModelConditioning・ControlNetApplyAdvanced など:
                # 出力 0 が positive、1 が negative。
                name: str | None = "negative" if index == 1 else "positive"
            else:
                name = self._conditioning_input(nid)
            if name is None:
                return None, 0, "none"
            wire = inputs.get(name)
            hops += 1
        return None, 0, "none"

    def _encoder_input(
        self, encoder: str, side: Literal["positive", "negative"], positive_encoders: set[str]
    ) -> str | None:
        names = self._encoder_text_inputs(encoder)
        positive_names = [n for n in names if "negative" not in n]
        negative_names = [n for n in names if "negative" in n]
        if side == "negative" and encoder in positive_encoders:
            # 同じノードの別の出力(TextEncodeQwenImage21 の prompt / negative_prompt など)。
            return negative_names[0] if negative_names else None
        for preferred in ("text", "prompt"):
            if preferred in positive_names:
                return preferred
        return positive_names[0] if positive_names else None

    # -- 文字列の根 ----------------------------------------------------------------

    def _carries_string(self, node_id: str, name: str, wire: Any) -> bool:
        if name in _NON_STRING_INPUTS or "seed" in name:
            return False
        if "." in name and not name.startswith(_STRING_GROUP_PREFIXES):
            return False
        declared = self.g.input_type(node_id, name)
        if declared is not None and declared not in _STRINGISH_TYPES:
            return False
        source = wire[0]
        if self.g.class_type(source).startswith("Primitive"):
            return isinstance(self.g.inputs(source).get("value"), str)
        outputs = self.g.output_types(source)
        if outputs is not None and 0 <= wire[1] < len(outputs):
            return outputs[wire[1]] in _STRINGISH_TYPES
        return not _NON_STRING_SOURCE_RE.search(self.g.class_type(source))

    def _is_root_input(self, node_id: str, name: str) -> bool:
        if name in _ROOT_NAME_EXCLUDE:
            return False
        if self.g.knows_class(node_id):
            return self.g.input_type(node_id, name) == "STRING"
        return bool(_ROOT_NAME_RE.match(name))

    def _string_roots(self, node_id: str, input_name: str) -> _StringTrace:
        """`(node_id, input_name)` に入る文字列の根を探す。根は「文字列の入力がすべて直値の
        ノードの直値」(ADR-0052 2章)。切り替えは選ばれた側だけを辿る。
        """
        trace = _StringTrace()
        value = self.g.inputs(node_id).get(input_name)
        if not is_wire(value):
            if isinstance(value, str):
                trace.roots.append(_Root(node_id, input_name, 0, None))
            return trace
        queue: deque[tuple[Any, int, tuple[str, str]]] = deque([(value, 1, (node_id, input_name))])
        while queue:
            wire, depth, via = queue.popleft()
            cur = wire[0]
            if cur in trace.visited or not self.g.has(cur) or depth > 32:
                continue
            trace.visited[cur] = depth
            wired = [
                (name, v)
                for name, v in self.g.live_input_items(cur)
                if is_wire(v) and self._carries_string(cur, name, v)
            ]
            if wired:
                for name, v in wired:
                    queue.append((v, depth + 1, (cur, name)))
                continue
            for name, v in self.g.inputs(cur).items():
                if isinstance(v, str) and self._is_root_input(cur, name):
                    trace.roots.append(_Root(cur, name, depth, via))
        return trace

    def _first_position(self, consumer: str, input_name: str) -> bool:
        """書式・連結のノードで、`input_name` が先頭に来る入力か(2つ以上の文字列を受けるとき)。"""
        wired = [
            name
            for name, v in self.g.live_input_items(consumer)
            if is_wire(v) and self._carries_string(consumer, name, v)
        ]
        if len(wired) < 2:
            return False
        # 書式のノード(`StringFormat` など。`f_string` を持つもの)だけを見る。単なる連結は
        # 固定の前置きが先頭に来ることが多いので、順位に使わない。
        f_string = self.g.inputs(consumer).get("f_string")
        if not isinstance(f_string, str) and self.g.class_type(consumer) != "StringFormat":
            return False

        def _order(name: str) -> tuple[int, str]:
            if isinstance(f_string, str):
                key = name.split(".")[-1]
                position = f_string.find("{" + key + "}")
                if position >= 0:
                    return position, name
                return 10**9, name
            return 0, name

        return min(wired, key=_order) == input_name

    def _score(self, root: _Root) -> float:
        score = 0.0
        if self.g.has_custom_title(root.node):
            title = self.g.title(root.node) or ""
            if _PROMPT_GOOD.search(title):
                score += 2
            if _PROMPT_BAD.search(title):
                score -= 3
        if _PROMPT_BAD.search(root.input):
            score -= 3
        value = self.g.inputs(root.node).get(root.input)
        if isinstance(value, str) and len(value) > _LONG_TEXT:
            score -= 1
        if self.g.class_type(root.node).startswith("PrimitiveString"):
            score += 0.5
        if root.via is not None and self._first_position(*root.via):
            score += 1
        return score

    def _rank(self, roots: list[_Root]) -> list[_Ranked]:
        ranked = [_Ranked(root, self._score(root)) for root in roots]
        ranked.sort(
            key=lambda r: (-r.score, r.root.depth, natural_id_key(r.root.node), r.root.input)
        )
        return ranked

    def _candidates(self, ranked: list[_Ranked], field_name: str) -> list[BindingCandidate]:
        return [
            BindingCandidate(
                node=r.root.node,
                input=r.root.input,
                score=r.score,
                label=self._label(r.root.node),
                path=self._path(r.root.node, r.root.input, field_name),
            )
            for r in ranked
        ]

    @staticmethod
    def _dedupe_roots(roots: list[_Root]) -> list[_Root]:
        by_key: dict[tuple[str, str], _Root] = {}
        for root in roots:
            key = (root.node, root.input)
            if key not in by_key or root.depth < by_key[key].depth:
                by_key[key] = root
        return list(by_key.values())

    def _choose_root(
        self, field_name: Literal["prompt", "negative_prompt"], roots: list[_Root]
    ) -> InputRef | None:
        msg = _root_message
        roots = [r for r in self._dedupe_roots(roots) if (r.node, r.input) not in self.used]
        if not roots:
            self._set(field_name, "absent", msg(field_name, "noRoot"))
            return None
        if len(roots) == 1:
            root = roots[0]
            return self._single(
                field_name,
                InputRef(node=root.node, input=root.input),
                "unique",
                msg(field_name, "unique", node=root.node, input=root.input),
            )
        ranked = self._rank(roots)
        candidates = self._candidates(ranked, field_name)
        if ranked[0].score - ranked[1].score >= _RANK_MARGIN:
            top = ranked[0].root
            ref = InputRef(node=top.node, input=top.input)
            self._claim(field_name, ref)
            self._set(
                field_name,
                "ranked",
                msg(field_name, "ranked", count=len(ranked), node=top.node, input=top.input),
                paths=[self._path(top.node, top.input, field_name)],
                candidates=candidates,
            )
            return ref
        self._set(
            field_name,
            "ask",
            msg(field_name, "ask", count=len(ranked)),
            candidates=candidates,
            question=msg(field_name, "question"),
        )
        return None

    # -- 項目ごと ------------------------------------------------------------------

    def read_prompt(self, samplers: list[tuple[str, str, str | None]]) -> InputRef | None:
        positive_encoders: list[tuple[str, int]] = []
        for sampler, positive_name, _negative in samplers:
            encoder, index, _status = self._trace_encoder(self.g.inputs(sampler)[positive_name])
            if encoder is not None and (encoder, index) not in positive_encoders:
                positive_encoders.append((encoder, index))
        encoder_ids = {encoder for encoder, _ in positive_encoders}
        roots: list[_Root] = []
        for encoder, _index in positive_encoders:
            name = self._encoder_input(encoder, "positive", encoder_ids)
            if name is None:
                continue
            if (encoder, name) not in self.positive_inputs:
                self.positive_inputs.append((encoder, name))
                trace = self._string_roots(encoder, name)
                self.positive_traces.append(trace)
                roots.extend(trace.roots)

        if "gakei:prompt" in self.marked:
            node_id = self.marked["gakei:prompt"][0]
            ref = self._marked_text_ref(node_id)
            if ref is not None:
                return self._single(
                    "prompt",
                    ref,
                    "marked",
                    t("comfyui.analyze.marked", title="gakei:prompt"),
                )

        if not samplers:
            self._set("prompt", "absent", t("comfyui.analyze.prompt.noSampler"))
            return None
        if not positive_encoders:
            self._set("prompt", "absent", t("comfyui.analyze.prompt.noEncoder"))
            return None
        if not roots:
            self._set("prompt", "absent", t("comfyui.analyze.prompt.noRoot"))
            return None
        return self._choose_root("prompt", roots)

    def read_negative(self, samplers: list[tuple[str, str, str | None]]) -> InputRef | None:
        if "gakei:negative" in self.marked:
            ref = self._marked_text_ref(self.marked["gakei:negative"][0])
            if ref is not None:
                return self._single(
                    "negative_prompt",
                    ref,
                    "marked",
                    t("comfyui.analyze.marked", title="gakei:negative"),
                )
        positive_encoders = {encoder for encoder, _name in self.positive_inputs}
        statuses: set[str] = set()
        roots: list[_Root] = []
        seen_inputs: set[tuple[str, str]] = set()
        for sampler, positive_name, negative_name in samplers:
            if negative_name is None:
                continue
            inputs = self.g.inputs(sampler)
            if inputs[negative_name] == inputs[positive_name]:
                statuses.add("same")
                continue
            encoder, _index, status = self._trace_encoder(inputs[negative_name])
            if encoder is None:
                statuses.add(status)
                continue
            name = self._encoder_input(encoder, "negative", positive_encoders)
            if name is None or (encoder, name) in self.positive_inputs:
                statuses.add("same")
                continue
            if (encoder, name) in seen_inputs:
                continue
            seen_inputs.add((encoder, name))
            roots.extend(self._string_roots(encoder, name).roots)

        roots = [r for r in roots if (r.node, r.input) not in self.used]
        if roots:
            return self._choose_root("negative_prompt", roots)
        if "zeroed" in statuses:
            reason = t("comfyui.analyze.negative.zeroed")
        elif "same" in statuses:
            reason = t("comfyui.analyze.negative.sameAsPositive")
        elif not any(negative for _s, _p, negative in samplers):
            reason = t("comfyui.analyze.negative.noInput")
        else:
            reason = t("comfyui.analyze.negative.noRoot")
        self._set("negative_prompt", "absent", reason)
        return None

    def _seed_names(self, node_id: str) -> list[str]:
        names: list[str] = []
        inputs = self.g.inputs(node_id)
        for name in ("seed", "noise_seed"):
            if name in inputs:
                names.append(name)
        for name in inputs:
            if name in names:
                continue
            spec = self.g.input_spec(node_id, name)
            if spec is None or spec[0] != "INT" or not isinstance(spec[1], dict):
                continue
            # `PrimitiveInt` は `control_after_generate` に文字列("fixed" など)を返すので、
            # 真偽値の True だけを seed とみなす。
            if spec[1].get("control_after_generate") is True:
                names.append(name)
        return names

    def read_seed(self) -> list[InputRef]:
        if "gakei:seed" in self.marked:
            refs: list[InputRef] = []
            for node_id in self.marked["gakei:seed"]:
                inputs = self.g.inputs(node_id)
                for name in ("seed", "noise_seed", "value"):
                    if is_plain_int(inputs.get(name)):
                        ref = InputRef(node=node_id, input=name)
                        if self._claim("seed", ref) is None:
                            refs.append(ref)
                        break
            if refs:
                self._set(
                    "seed",
                    "marked",
                    t("comfyui.analyze.marked", title="gakei:seed"),
                    paths=[self._path(r.node, r.input) for r in refs],
                )
                return refs
        refs = []
        for node_id in self.scope_ids:
            for name in self._seed_names(node_id):
                value = self.g.inputs(node_id).get(name)
                ref: InputRef | None = None
                if is_plain_int(value):
                    ref = InputRef(node=node_id, input=name)
                elif is_wire(value):
                    source = value[0]
                    if self.g.class_type(source).startswith("Primitive") and is_plain_int(
                        self.g.inputs(source).get("value")
                    ):
                        ref = InputRef(node=source, input="value")
                if ref is None or ref in refs:
                    continue
                if self._claim("seed", ref) is None:
                    refs.append(ref)
        if refs:
            self._set(
                "seed",
                "unique",
                t("comfyui.analyze.seed.unique", count=len(refs)),
                paths=[self._path(r.node, r.input) for r in refs],
            )
        else:
            self._set("seed", "absent", t("comfyui.analyze.seed.absent"))
        return refs

    def _trace_latent(self, wire: Any, depth: int = 0) -> list[tuple[str, str]]:
        """latent の配線を遡り、`("empty", ノード)` か `("other", ノード)` の並びを返す。"""
        hops = 0
        while is_wire(wire) and hops < 12 and depth < 6:
            nid = wire[0]
            if not self.g.has(nid):
                return []
            if self.g.is_switch(nid):
                chosen = self.g.switch_choice(nid)
                inputs = self.g.inputs(nid)
                if chosen is None:
                    found: list[tuple[str, str]] = []
                    for name in ("on_true", "on_false"):
                        found.extend(self._trace_latent(inputs.get(name), depth + 1))
                    return found
                wire = inputs.get(chosen)
                hops += 1
                continue
            class_type = self.g.class_type(nid)
            inputs = self.g.inputs(nid)
            if class_type.startswith("Empty") and "Latent" in class_type:
                return [("empty", nid)]
            if "width" in inputs and "height" in inputs and "batch_size" in inputs:
                return [("empty", nid)]
            next_name = next(
                (name for name in ("latent_image", "samples") if is_wire(inputs.get(name))), None
            )
            if next_name is None:
                return [("other", nid)]
            wire = inputs[next_name]
            hops += 1
        return []

    def read_size(self) -> tuple[InputRef | None, InputRef | None, InputRef | None]:
        results: list[tuple[str, str]] = []
        for nid in self.scope_ids:
            wire = self.g.inputs(nid).get("latent_image")
            if is_wire(wire):
                for item in self._trace_latent(wire):
                    if item not in results:
                        results.append(item)
        empties = [nid for kind, nid in results if kind == "empty"]
        others = [nid for kind, nid in results if kind == "other"]
        if len(empties) > 1:
            candidates = [
                BindingCandidate(
                    node=nid,
                    input=None,
                    score=0,
                    label=self._label(nid),
                    path=self._path(nid, None),
                )
                for nid in empties
            ]
            for field_name in ("width", "height", "batch_size"):
                self._set(
                    field_name,  # type: ignore[arg-type]
                    "ask",
                    t("comfyui.analyze.size.ask", count=len(empties)),
                    candidates=candidates,
                    question=t("comfyui.analyze.question.size"),
                )
            return None, None, None
        if not empties:
            if others:
                other = others[0]
                reason = t(
                    "comfyui.analyze.size.fromImage",
                    node=other,
                    class_type=self.g.class_type(other) or "?",
                )
            else:
                reason = t("comfyui.analyze.size.noLatent")
            for field_name in ("width", "height", "batch_size"):
                self._set(field_name, "absent", reason)  # type: ignore[arg-type]
            return None, None, None

        latent = empties[0]
        refs: list[InputRef | None] = []
        for field_name in ("width", "height", "batch_size"):
            refs.append(self._read_size_input(latent, field_name))  # type: ignore[arg-type]
        return refs[0], refs[1], refs[2]

    def _read_size_input(
        self, latent: str, field_name: Literal["width", "height", "batch_size"]
    ) -> InputRef | None:
        inputs = self.g.inputs(latent)
        if field_name not in inputs:
            self._set(
                field_name,
                "absent",
                t("comfyui.analyze.size.inputMissing", node=latent, input=field_name),
            )
            return None
        value = inputs[field_name]
        if is_plain_int(value):
            return self._single(
                field_name,
                InputRef(node=latent, input=field_name),
                "unique",
                t(
                    "comfyui.analyze.size.direct",
                    node=latent,
                    class_type=self.g.class_type(latent) or "?",
                    input=field_name,
                ),
            )
        if is_wire(value):
            source = value[0]
            source_inputs = self.g.inputs(source)
            if self.g.class_type(source).startswith("Primitive") and is_plain_int(
                source_inputs.get("value")
            ):
                return self._single(
                    field_name,
                    InputRef(node=source, input="value"),
                    "unique",
                    t(
                        "comfyui.analyze.size.primitive",
                        latent=latent,
                        input=field_name,
                        node=source,
                    ),
                )
            # 計算するノード(解像度の選択など): 差し込まず、その入力を公開パラメーターに薦める。
            computed = [name for name, v in source_inputs.items() if not is_wire(v)]
            for name in computed:
                self.recommended.add((source, name))
            self._set(
                field_name,
                "absent",
                t(
                    "comfyui.analyze.size.computed",
                    input=field_name,
                    node=source,
                    class_type=self.g.class_type(source) or "?",
                ),
                paths=[self._path(latent, field_name)],
            )
            return None
        self._set(
            field_name,
            "absent",
            t("comfyui.analyze.size.inputMissing", node=latent, input=field_name),
        )
        return None

    def _title_image_slots(self) -> list[InputRef] | None:
        matches: list[tuple[int, str]] = []
        for nid in self.g.ids:
            title = self.g.title(nid)
            if title is None:
                continue
            match = _IMAGE_TITLE_RE.match(title)
            if match is None:
                continue
            matches.append((int(match.group(1)) if match.group(1) else 0, nid))
        if not matches:
            return None
        matches.sort(key=lambda item: (item[0], natural_id_key(item[1])))
        slots = [
            InputRef(node=nid, input="image") for _, nid in matches if "image" in self.g.inputs(nid)
        ]
        return slots or None

    def _image_number(self, load_id: str, encoders: set[str]) -> tuple[int, int]:
        """受け側の番号付きの入力(`images.image_N`、`imageN`)の番号。画像を素通しするノードを
        越えて探す。`(エンコーダーでの番号, ほかでの番号)`。無ければ大きな値。
        """
        none = 10**6
        best_encoder, best_other = none, none
        queue = deque([(load_id, 0)])
        seen: set[str] = set()
        while queue:
            cur, depth = queue.popleft()
            if cur in seen or depth > 4:
                continue
            seen.add(cur)
            for consumer, name, index in self.g.consumers.get(cur, []):
                if index != 0 or consumer not in self.scope_set:
                    continue
                match = NUMBERED_IMAGE_INPUT_RE.search(name)
                if match is not None:
                    number = int(match.group(1))
                    if consumer in encoders:
                        best_encoder = min(best_encoder, number)
                    else:
                        best_other = min(best_other, number)
                elif name in ("image", "images", "pixels"):
                    queue.append((consumer, depth + 1))
        return best_encoder, best_other

    def read_images(self) -> tuple[list[InputRef], bool]:
        """画像の枠と、出力に届く LoadImage があるか(edit にするか)。"""
        load_ids = [nid for nid in self.scope_ids if self.g.class_type(nid) == "LoadImage"]
        has_load_image = bool(load_ids)
        titled = self._title_image_slots()
        if titled is not None:
            refs = [ref for ref in titled if self._claim("images", ref) is None]
            self._set(
                "images",
                "marked",
                t("comfyui.analyze.marked", title="gakei:image"),
                paths=[self._path(r.node, r.input) for r in refs],
            )
            return refs, True
        slot_ids = [nid for nid in load_ids if isinstance(self.g.inputs(nid).get("image"), str)]
        if not slot_ids:
            self._set("images", "absent", t("comfyui.analyze.images.absent"))
            return [], has_load_image
        encoders = {encoder for encoder, _name in self.positive_inputs}
        numbers = {nid: self._image_number(nid, encoders) for nid in slot_ids}
        ordered = sorted(slot_ids, key=lambda nid: (*numbers[nid], natural_id_key(nid)))
        refs = []
        for nid in ordered:
            ref = InputRef(node=nid, input="image")
            if self._claim("images", ref) is None:
                refs.append(ref)
        paths = [self._path(r.node, r.input) for r in refs]
        if len(refs) == 1:
            self._set(
                "images",
                "unique",
                t("comfyui.analyze.images.single", node=refs[0].node),
                paths=paths,
            )
            return refs, True
        numbered = [min(numbers[nid]) for nid in ordered]
        if all(n < 10**6 for n in numbered) and len(set(numbered)) == len(numbered):
            self._set(
                "images",
                "unique",
                t("comfyui.analyze.images.numbered", count=len(refs)),
                paths=paths,
            )
            return refs, True
        self._set(
            "images",
            "ask",
            t("comfyui.analyze.images.idOrder", count=len(refs)),
            paths=paths,
            candidates=[
                BindingCandidate(
                    node=r.node,
                    input=r.input,
                    score=float(len(refs) - i),
                    label=self._label(r.node),
                    path=p,
                )
                for i, (r, p) in enumerate(zip(refs, paths, strict=True))
            ],
            question=t("comfyui.analyze.question.images"),
        )
        return refs, True

    def read_mask(self, images: list[InputRef], operation: str) -> MaskBinding | None:
        if operation != "edit":
            self._set("mask", "absent", t("comfyui.analyze.mask.generate"))
            return None
        if "gakei:mask" in self.marked:
            node_id = self.marked["gakei:mask"][0]
            if self.g.class_type(node_id) == "LoadImageMask" and "image" in self.g.inputs(node_id):
                ref = InputRef(node=node_id, input="image")
                if self._claim("mask", ref) is None:
                    self._set(
                        "mask",
                        "marked",
                        t("comfyui.analyze.marked", title="gakei:mask"),
                        paths=[self._path(node_id, "image")],
                    )
                    return MaskBinding(mode="load_image_mask", node=node_id, input="image")
        mask_ids = [
            nid
            for nid in self.scope_ids
            if self.g.class_type(nid) == "LoadImageMask"
            and isinstance(self.g.inputs(nid).get("image"), str)
        ]
        if mask_ids:
            node_id = mask_ids[0]
            ref = InputRef(node=node_id, input="image")
            if self._claim("mask", ref) is None:
                if len(mask_ids) == 1:
                    self._set(
                        "mask",
                        "unique",
                        t("comfyui.analyze.mask.loadImageMask", node=node_id),
                        paths=[self._path(node_id, "image")],
                    )
                else:
                    self._set(
                        "mask",
                        "ask",
                        t("comfyui.analyze.mask.loadImageMask", node=node_id),
                        paths=[self._path(node_id, "image")],
                        candidates=[
                            BindingCandidate(
                                node=nid,
                                input="image",
                                score=float(len(mask_ids) - i),
                                label=self._label(nid),
                                path=self._path(nid, "image"),
                            )
                            for i, nid in enumerate(mask_ids)
                        ],
                        question=t("comfyui.analyze.question.mask"),
                    )
                return MaskBinding(mode="load_image_mask", node=node_id, input="image")
        if images:
            first = images[0].node
            inpaint, other = self.g.mask_uses(first, 1, self.live or set(self.scope_ids))
            if inpaint:
                consumer, name = inpaint[0]
                self._set(
                    "mask",
                    "unique",
                    t("comfyui.analyze.mask.imageAlpha", consumer=f"{consumer}.{name}"),
                    paths=[self._path(first, "image")],
                )
                return MaskBinding(mode="image_alpha")
            if other:
                joiner = t("comfyui.workflow.listSeparator")
                consumers = joiner.join(
                    f"{nid}.{name}({self.g.class_type(nid) or '?'})" for nid, name in other
                )
                self._set(
                    "mask", "absent", t("comfyui.analyze.mask.alphaOnly", consumers=consumers)
                )
                return None
        self._set("mask", "absent", t("comfyui.analyze.mask.absent"))
        return None

    def read_outputs(self) -> list[str]:
        if "gakei:output" in self.marked:
            outputs = list(self.marked["gakei:output"])
            self._set(
                "outputs",
                "marked",
                t("comfyui.analyze.marked", title="gakei:output"),
                paths=[
                    [PathStep(node=n, class_type=self.g.class_type(n) or None)] for n in outputs
                ],
            )
            return outputs
        outputs = [nid for nid in self.g.save_node_ids() if self.g.generated_image_reaches(nid)]
        if outputs:
            self._set(
                "outputs",
                "unique",
                t(
                    "comfyui.analyze.outputs.found",
                    count=len(outputs),
                    nodes=t("comfyui.workflow.listSeparator").join(outputs),
                ),
                paths=[
                    [PathStep(node=n, class_type=self.g.class_type(n) or None)] for n in outputs
                ],
            )
            return outputs
        has_display_image = any(
            self.g.is_display_node(nid) and not self.g.is_text_display_node(nid)
            for nid in self.g.ids
        )
        self._set(
            "outputs",
            "absent",
            t("comfyui.analyze.outputs.previewOnly")
            if has_display_image
            else t("comfyui.analyze.outputs.absent"),
        )
        return []

    def read_final_prompt(self) -> str | None:
        if "gakei:final_prompt" in self.marked:
            node_id = self.marked["gakei:final_prompt"][0]
            self._set(
                "final_prompt",
                "marked",
                t("comfyui.analyze.marked", title="gakei:final_prompt"),
                paths=[[PathStep(node=node_id, class_type=self.g.class_type(node_id) or None)]],
            )
            return node_id
        # (段, 深さ, ノード)。段 0 はエンコーダーに入る文字列そのものを表示するノード
        # (経路の途中、または同じ配線を横から表示する)、段 1 は文字列の遡りの途中を表示するノード。
        best: tuple[int, int, tuple[Any, ...], str] | None = None
        for (encoder, name), trace in zip(self.positive_inputs, self.positive_traces, strict=True):
            same_wires = self._same_string_wires(self.g.inputs(encoder).get(name))
            for nid in self.g.ids:
                if not self.g.is_text_display_node(nid):
                    continue
                indexes = [i for i, (source, _out) in enumerate(same_wires) if source == nid]
                indexes += [
                    same_wires.index((v[0], v[1]))
                    for v in self.g.inputs(nid).values()
                    if is_wire(v) and (v[0], v[1]) in same_wires
                ]
                if indexes:
                    candidate = (0, min(indexes), natural_id_key(nid), nid)
                    best = candidate if best is None else min(best, candidate)
            for nid, depth in trace.visited.items():
                if self.g.is_text_display_node(nid):
                    candidate = (1, depth, natural_id_key(nid), nid)
                    best = candidate if best is None else min(best, candidate)
        if best is None:
            self._set("final_prompt", "absent", t("comfyui.analyze.finalPrompt.absent"))
            return None
        exact = best[0] == 0
        node_id = best[3]
        self._set(
            "final_prompt",
            "unique" if exact else "ranked",
            t("comfyui.analyze.finalPrompt.found", node=node_id)
            if exact
            else t("comfyui.analyze.finalPrompt.nearest", node=node_id),
            paths=[[PathStep(node=node_id, class_type=self.g.class_type(node_id) or None)]],
        )
        return node_id

    def _same_string_wires(self, wire: Any) -> list[tuple[str, int]]:
        """エンコーダーに入る配線と、切り替え・文字列の表示だけを越えて遡った配線(どれも
        エンコーダーに入る文字列そのものを運ぶ)。"""
        wires: list[tuple[str, int]] = []
        while is_wire(wire) and len(wires) < 12:
            key = (wire[0], wire[1])
            if key in wires:
                break
            wires.append(key)
            source = wire[0]
            if self.g.is_switch(source):
                chosen = self.g.switch_choice(source)
                if chosen is None:
                    break
                wire = self.g.inputs(source).get(chosen)
            elif self.g.is_text_display_node(source):
                wire = next((v for v in self.g.inputs(source).values() if is_wire(v)), None)
            else:
                break
        return wires

    # -- 公開パラメーター --------------------------------------------------------------

    def _collect_switch_controls(self) -> None:
        for nid in self.scope_ids:
            if self.g.is_switch(nid):
                control = self.g.switch_control(nid)
                if control is not None:
                    self.recommended.add(control)

    def param_tier(
        self, node_id: str, input_name: str, value: Any, secrets: set
    ) -> tuple[ParamTier, HiddenReason | None]:
        """候補の段と、公開しないときの理由。"""
        if (node_id, input_name) in self.recommended:
            return "recommended", None
        if self.live and self.states.get(node_id) != "live":
            return "hidden", "unused_branch"
        if (node_id, input_name) in secrets:
            return "hidden", "secret"
        if input_name in _HIDDEN_INPUTS:
            return "hidden", "fixed_setting"
        if input_name.endswith("_name") and input_name != "sampler_name":
            return "hidden", "file_or_model"
        if isinstance(value, str) and _FILE_VALUE_RE.search(value):
            return "hidden", "file_or_model"
        if isinstance(value, str) and (len(value) > _FIXED_TEXT or "\n" in value):
            return "hidden", "fixed_text"
        if self.g.class_type(node_id).startswith("Primitive") and self.g.has_custom_title(node_id):
            return "recommended", None
        if input_name in _RECOMMENDED_INPUTS:
            return "recommended", None
        return "other", None


# -- 公開パラメーターの候補(ADR-0013 からの互換) -------------------------------------


def _infer_param_type(value: Any) -> ParamType | None:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "text"
    return None


def _sanitize_param_name(name: str) -> str:
    sanitized = re.sub(r"[^a-z0-9_]", "_", name.lower())
    if not sanitized or not sanitized[0].isalpha():
        sanitized = f"p_{sanitized}" if sanitized else "p"
    return sanitized


def _build_candidate_params(
    template: dict[str, Any], used: set[tuple[str, str]]
) -> list[ExposedParam]:
    raw: list[tuple[str, str, str, Any, ParamType]] = []  # (node, input, base_name, value, type)
    for node_id, node in template.items():
        if not isinstance(node, dict):
            continue
        for input_name, value in (node.get("inputs") or {}).items():
            if (node_id, input_name) in used or is_wire(value):
                continue
            ptype = _infer_param_type(value)
            if ptype is None:
                continue
            raw.append((node_id, input_name, _sanitize_param_name(input_name), value, ptype))

    by_name: dict[str, list[tuple[str, str, str, Any, ParamType]]] = {}
    for item in raw:
        by_name.setdefault(item[2], []).append(item)

    candidates: list[ExposedParam] = []
    for node_id, input_name, base_name, value, ptype in raw:
        group = by_name[base_name]
        final_name = base_name if len(group) == 1 else f"{base_name}_{node_id}"
        final_name = _sanitize_param_name(final_name)
        try:
            candidates.append(
                ExposedParam(
                    name=final_name,
                    node=node_id,
                    input=input_name,
                    type=ptype,
                    label=input_name,
                    default=value,
                )
            )
        except ValueError:
            # 予約語などで公開パラメータ名にできない候補は黙って除外する
            # (登録画面で手動なら別名を付けられる)。
            continue
    return candidates


def _build_analyze_nodes(
    graph: WorkflowGraph, states: dict[str, NodeState]
) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for node_id, node in graph.template.items():
        if not isinstance(node, dict):
            continue
        inputs_info = [
            {"name": name, "value": value, "linked": is_wire(value)}
            for name, value in (node.get("inputs") or {}).items()
        ]
        nodes.append(
            {
                "id": node_id,
                "class_type": node.get("class_type"),
                "title": graph.title(node_id),
                "inputs": inputs_info,
                "state": states.get(node_id, "unused"),
            }
        )
    return nodes


def _format_candidate(graph: WorkflowGraph, candidate: BindingCandidate) -> str:
    label = candidate.label
    if len(label) > 30:
        label = label[:30] + "…"
    return f"{candidate.node}「{label}」.{candidate.input}"


# -- 入口 ------------------------------------------------------------------------


def analyze_workflow(
    template: dict[str, Any], object_info: dict[str, Any] | None = None
) -> AnalyzeResult:
    """保存前の読み取り(ADR-0052 2章)。テンプレートは変更しない。UI 形式・API 形式でない
    ものは呼び出し側で先に弾く想定(`is_ui_format_template` / `is_api_format_template`)。

    `object_info` は ComfyUI の `/object_info`(接続できなければ None)。必須ではなく、あれば
    型、選択肢、出力ノードの判定を上書きする。HTTP は呼ばない。
    """
    reader = _Reader(template, object_info)
    g = reader.g

    samplers = reader._samplers()
    prompt_ref = reader.read_prompt(samplers)
    negative_ref = reader.read_negative(samplers)
    seed_refs = reader.read_seed()
    width_ref, height_ref, batch_size_ref = reader.read_size()
    image_refs, has_load_image = reader.read_images()
    suggested_operation: Literal["generate", "edit"] = "edit" if has_load_image else "generate"
    mask_binding = reader.read_mask(image_refs, suggested_operation)
    output_ids = reader.read_outputs()
    final_prompt = reader.read_final_prompt()

    warnings = reader.warnings
    warning_items: list[AnalyzeWarning] = []

    def _warn(message: str, field_name: BindingField | None = None) -> None:
        # field を持つ警告は、その項目の読み取り(理由・問い)で同じことを示している。
        warnings.append(message)
        warning_items.append(
            AnalyzeWarning(
                kind="reading" if field_name is not None else "general",
                field=field_name,
                message=message,
            )
        )

    # -- これまでの警告(画面の既存の警告表示に出す) --
    if not output_ids:
        class_types = [g.class_type(nid) for nid in g.ids]
        if "PreviewImage" in class_types and not any("Save" in c for c in class_types):
            _warn(t("comfyui.workflow.previewImageNotSupported"), "outputs")
        else:
            referenced = {
                value[0] for nid in g.ids for value in g.inputs(nid).values() if is_wire(value)
            }
            fallback = [
                nid
                for nid in g.ids
                if nid not in referenced
                and "Preview" not in g.class_type(nid)
                and "Compare" not in g.class_type(nid)
            ]
            if fallback:
                _warn(
                    t("comfyui.workflow.outputNodeNotFoundWithCandidates") + ", ".join(fallback),
                    "outputs",
                )
            else:
                _warn(t("comfyui.workflow.outputNodeNotFound"), "outputs")

    prompt_reading = reader.readings["prompt"]
    if prompt_ref is None:
        if prompt_reading.confidence == "ask" and prompt_reading.candidates:
            shown = prompt_reading.candidates[:_PROMPT_CANDIDATE_MAX_COUNT]
            formatted = t("comfyui.workflow.listSeparator").join(
                _format_candidate(g, c) for c in shown
            )
            remainder = len(prompt_reading.candidates) - len(shown)
            if remainder > 0:
                formatted += t("comfyui.workflow.moreCandidatesSuffix", count=remainder)
            _warn(t("comfyui.workflow.promptBindingCandidates", candidates=formatted), "prompt")
        else:
            _warn(t("comfyui.workflow.promptBindingNotFound"), "prompt")
    if suggested_operation == "edit" and not image_refs:
        _warn(t("comfyui.workflow.imageBindingNotFound"), "images")
    # 決めきれない項目の問い(プロンプトは上の候補の一覧で出している)。
    asked: set[str] = set()
    for name in _FIELD_ORDER:
        reading = reader.readings.get(name)
        if name == "prompt" or reading is None or reading.confidence != "ask":
            continue
        if reading.question and reading.question not in asked:
            asked.add(reading.question)
            _warn(reading.question, name)

    if suggested_operation == "edit" and mask_binding is None:
        # GAKEI の2方式(load_image_mask / image_alpha)のどちらにも当てはまらない方式
        # (キャンバスに直接描く Painter 系など)のノードがあれば案内する。
        if any(
            any(marker in g.class_type(nid) for marker in _UNSUPPORTED_MASK_MARKERS)
            for nid in g.ids
        ):
            _warn(t("comfyui.workflow.maskMethodUnsupported"))

    # 秘密に見える値(API キーなど)が書かれた入力。値そのものは出さない。登録は止めない
    # (判定は推測のため。ADR-0013 8章、2026-10-01 追記)。
    secrets = find_secret_inputs(template)
    for found in secrets:
        _warn(
            t(
                "comfyui.workflow.secretValueFound",
                node=found.node_id,
                class_type=found.class_type or "?",
                input=found.input,
            )
        )

    suggested_bindings = SuggestedBindings(
        prompt=prompt_ref,
        negative_prompt=negative_ref,
        seed=seed_refs,
        width=width_ref,
        height=height_ref,
        batch_size=batch_size_ref,
        images=image_refs,
        mask=mask_binding,
        outputs=output_ids,
        final_prompt=final_prompt,
    )

    used: set[tuple[str, str]] = set(reader.used)
    candidate_params = _build_candidate_params(template, used)
    reader._collect_switch_controls()
    secret_keys = {(s.node_id, s.input) for s in secrets}
    param_tiers: list[ParamTierInfo] = []
    for param in candidate_params:
        tier, hidden_reason = reader.param_tier(
            param.node, param.input, g.inputs(param.node).get(param.input), secret_keys
        )
        param_tiers.append(
            ParamTierInfo(
                name=param.name,
                node=param.node,
                input=param.input,
                tier=tier,
                hidden_reason=hidden_reason,
            )
        )

    validation = check_workflow(template, suggested_operation, suggested_bindings, [], object_info)

    return AnalyzeResult(
        nodes=_build_analyze_nodes(g, reader.states),
        suggested_bindings=suggested_bindings,
        suggested_operation=suggested_operation,
        candidate_params=candidate_params,
        warnings=warnings,
        warning_items=warning_items,
        readings=[reader.readings[name] for name in _FIELD_ORDER if name in reader.readings],
        param_tiers=param_tiers,
        validation=validation,
    )
