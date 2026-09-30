"""ComfyUI のワークフロー登録(ADR-0013)。差し込み先(`Bindings`)と公開パラメータ
(`ExposedParam`)の定義、登録時の検証、`capabilities()` への変換、値の差し込み
(`resolve_prompt`)、登録画面向けの提案(`analyze_workflow`)をまとめる。

いずれも DB やプロバイダーの実行(`app/providers/comfyui/`)には依存しない純粋な関数群。
グラフの構造そのものは変えない(ADR-0013: ノードの追加・削除・配線の変更はしない)。
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.domain.models import ComfyWorkflow
from app.i18n import t
from app.providers.base import ModelCapabilities, OperationCapabilities, ParamDef, ParamType

SEED_MAX = 2**53 - 1

# ExposedParam.name の規則。予約語は Run 作成時の共通パラメータと衝突するため使えない。
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_RESERVED_NAMES = frozenset(
    {
        "prompt",
        "negative_prompt",
        "seed",
        "width",
        "height",
        "batch_size",
        "size",
        "n",
        "model",
        "operation",
    }
)


def ui_format_message() -> str:
    return t("comfyui.workflow.uiFormatMessage")


def not_api_format_message() -> str:
    return t("comfyui.workflow.notApiFormatMessage")


class WorkflowValidationError(ValueError):
    """ワークフロー登録の検証失敗。API 層で 422 に変換する。"""


# -- 差し込み先・公開パラメータ ----------------------------------------------


class InputRef(BaseModel):
    """テンプレートの1つの入力を指す。"""

    node: str
    input: str


class MaskBinding(BaseModel):
    """マスクの渡し方。`load_image_mask` はマスク PNG をそのまま渡す先、`image_alpha` は
    入力画像に alpha を合成した PNG を作って `image` の LoadImage に渡す形(node/input は
    使わない、常に None)。
    """

    mode: Literal["load_image_mask", "image_alpha"]
    node: str | None = None
    input: str | None = None

    @model_validator(mode="after")
    def _check_load_image_mask_fields(self) -> MaskBinding:
        if self.mode == "load_image_mask" and (self.node is None or self.input is None):
            raise ValueError(t("comfyui.workflow.maskBindingRequiresNodeInput"))
        return self


def _convert_legacy_image_binding(data: Any) -> Any:
    """旧形式(`image` 単数)を新形式(`images` の順序付きリスト)に変換する。DB に残る
    既存行や、移行前のクライアントからの入力との互換のため(ADR-0013 3節)。`images` が
    既にあれば(空でも)そちらを優先し、`image` は無視する。
    """
    if not isinstance(data, dict):
        return data
    if not data.get("images") and data.get("image") is not None:
        data = dict(data)
        data["images"] = [data["image"]]
    return data


class Bindings(BaseModel):
    """テンプレートへの差し込み先。"""

    prompt: InputRef
    negative_prompt: InputRef | None = None
    seed: list[InputRef] = Field(default_factory=list)
    width: InputRef | None = None
    height: InputRef | None = None
    batch_size: InputRef | None = None
    # 画像の枠(順序付き)。GAKEI の入力の n 枚目を n 番目の枠に差し込む。1枚目が
    # 系列の主たる親になる(ADR-0013 3節)。
    images: list[InputRef] = Field(default_factory=list)
    mask: MaskBinding | None = None
    outputs: list[str] = Field(min_length=1)
    # 最終プロンプト(PE の出力)を読むノードの id(ADR-0030 1章)。実行後に
    # `outputs[id].text` を `run.text_outputs` に記録する。旧形式(キー無し)は None。
    final_prompt: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _legacy_image(cls, data: Any) -> Any:
        return _convert_legacy_image_binding(data)


class SuggestedBindings(BaseModel):
    """analyze の提案(保存はしない)。項目は `Bindings` と同じだが、必須項目が見つからない
    ことがあるため `prompt` は省略可能、`outputs` は空でもよい。見つからない必須項目は
    `AnalyzeResult.warnings` に書く(ADR-0013 フォローアップ)。
    """

    prompt: InputRef | None = None
    negative_prompt: InputRef | None = None
    seed: list[InputRef] = Field(default_factory=list)
    width: InputRef | None = None
    height: InputRef | None = None
    batch_size: InputRef | None = None
    images: list[InputRef] = Field(default_factory=list)
    mask: MaskBinding | None = None
    outputs: list[str] = Field(default_factory=list)
    final_prompt: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _legacy_image(cls, data: Any) -> Any:
        return _convert_legacy_image_binding(data)


class ExposedParam(BaseModel):
    """利用者がフォームで指定できる、差し込み先以外の入力。"""

    name: str
    node: str
    input: str
    type: ParamType
    label: str
    default: Any | None = None
    minimum: int | float | None = None
    maximum: int | float | None = None
    step: float | None = None
    choices: list[str] | None = None
    max_length: int | None = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if not _NAME_RE.match(value):
            raise ValueError(t("comfyui.workflow.invalidExposedParamName", name=value))
        if value.startswith("comfyui_"):
            raise ValueError(t("comfyui.workflow.exposedParamReservedPrefix", name=value))
        if value in _RESERVED_NAMES:
            raise ValueError(t("comfyui.workflow.exposedParamReservedWord", name=value))
        return value


# -- テンプレートの形式判定 ---------------------------------------------------


def is_ui_format_template(data: Any) -> bool:
    """ComfyUI の UI 形式(トップレベルに `nodes` と `links` を持つ)かどうか。"""
    return isinstance(data, dict) and "nodes" in data and "links" in data


def is_api_format_template(data: Any) -> bool:
    """値がすべて `class_type` と `inputs` を持つ dict なら API 形式とみなす。"""
    if not isinstance(data, dict) or not data:
        return False
    for node in data.values():
        if not isinstance(node, dict) or "class_type" not in node or "inputs" not in node:
            return False
        if not isinstance(node["inputs"], dict):
            return False
    return True


def _is_wire(value: Any) -> bool:
    """配線([node_id, output_index] の形)かどうか。"""
    return (
        isinstance(value, list)
        and len(value) == 2
        and isinstance(value[0], str)
        and isinstance(value[1], int)
        and not isinstance(value[1], bool)
    )


# -- sha256 -------------------------------------------------------------


def compute_template_sha256(template: dict[str, Any]) -> str:
    encoded = json.dumps(template, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


# -- 検証(保存時) ------------------------------------------------------


def validate_workflow(
    template: dict[str, Any],
    operation: Literal["generate", "edit"],
    bindings: Bindings,
    exposed_params: list[ExposedParam],
) -> None:
    """保存(POST/PATCH)前の検証。422 にすべき問題は `WorkflowValidationError` を投げる。"""
    if is_ui_format_template(template):
        raise WorkflowValidationError(ui_format_message())
    if not is_api_format_template(template):
        raise WorkflowValidationError(not_api_format_message())

    used: set[tuple[str, str]] = set()

    def _check_ref(ref: InputRef, label: str) -> None:
        node = template.get(ref.node)
        if not isinstance(node, dict):
            raise WorkflowValidationError(
                t("comfyui.workflow.bindingNodeMissing", label=label, node=ref.node)
            )
        inputs = node.get("inputs")
        if not isinstance(inputs, dict) or ref.input not in inputs:
            raise WorkflowValidationError(
                t(
                    "comfyui.workflow.bindingInputMissing",
                    label=label,
                    node=ref.node,
                    input=ref.input,
                )
            )
        key = (ref.node, ref.input)
        if key in used:
            raise WorkflowValidationError(
                t("comfyui.workflow.bindingDuplicate", node=ref.node, input=ref.input)
            )
        used.add(key)

    _check_ref(bindings.prompt, "prompt")
    if bindings.negative_prompt is not None:
        _check_ref(bindings.negative_prompt, "negative_prompt")
    for index, ref in enumerate(bindings.seed):
        _check_ref(ref, f"seed[{index}]")
    if bindings.width is not None:
        _check_ref(bindings.width, "width")
    if bindings.height is not None:
        _check_ref(bindings.height, "height")
    if bindings.batch_size is not None:
        _check_ref(bindings.batch_size, "batch_size")
    for index, ref in enumerate(bindings.images):
        _check_ref(ref, f"images[{index}]")
    if bindings.mask is not None and bindings.mask.mode == "load_image_mask":
        assert bindings.mask.node is not None and bindings.mask.input is not None
        _check_ref(InputRef(node=bindings.mask.node, input=bindings.mask.input), "mask")

    for node_id in bindings.outputs:
        if not isinstance(template.get(node_id), dict):
            raise WorkflowValidationError(t("comfyui.workflow.outputNodeMissing", node=node_id))
    # 最終プロンプトのノードは種類で絞らない。存在だけを確かめる(ADR-0030 1章)。
    if bindings.final_prompt is not None and not isinstance(
        template.get(bindings.final_prompt), dict
    ):
        raise WorkflowValidationError(
            t("comfyui.workflow.finalPromptNodeMissing", node=bindings.final_prompt)
        )

    if operation == "edit" and not bindings.images:
        raise WorkflowValidationError(t("comfyui.workflow.editRequiresImage"))
    # generate は入力画像を取らない(ADR-0009)。差し込み先があると requires_mask などが
    # capabilities に出てしまい、どの入力でも実行できないワークフローになる。
    if operation == "generate" and (bindings.images or bindings.mask is not None):
        raise WorkflowValidationError(t("comfyui.workflow.generateCannotHaveImageOrMask"))

    seen_names: set[str] = set()
    for param in exposed_params:
        if param.name in seen_names:
            raise WorkflowValidationError(
                t("comfyui.workflow.exposedParamDuplicate", name=param.name)
            )
        seen_names.add(param.name)
        _check_ref(
            InputRef(node=param.node, input=param.input),
            t("comfyui.workflow.exposedParamLabel", name=param.name),
        )


# -- capabilities への変換 -----------------------------------------------


def _bindings_from_json(data: dict[str, Any]) -> Bindings:
    return Bindings.model_validate(data)


def _exposed_params_from_json(data: list[Any]) -> list[ExposedParam]:
    return [ExposedParam.model_validate(item) for item in data]


def _template_value(template: dict[str, Any], ref: InputRef) -> Any:
    node = template.get(ref.node)
    if not isinstance(node, dict):
        return None
    inputs = node.get("inputs")
    if not isinstance(inputs, dict):
        return None
    return inputs.get(ref.input)


def workflow_model_capabilities(wf: ComfyWorkflow) -> ModelCapabilities:
    """登録済みワークフロー1件を、`ImageProvider.capabilities()` のモデル1件に変換する。"""
    bindings = _bindings_from_json(wf.bindings)
    exposed = _exposed_params_from_json(wf.exposed_params)
    template = wf.template
    operation = str(wf.operation)

    params: list[ParamDef] = []

    if bindings.negative_prompt is not None:
        default = _template_value(template, bindings.negative_prompt)
        params.append(
            ParamDef(
                name="negative_prompt",
                type="text",
                label=t("comfyui.workflow.negativePromptLabel"),
                default=default,
            )
        )

    if bindings.seed:
        params.append(
            ParamDef(
                name="seed",
                type="int",
                label=t("comfyui.workflow.seedLabel"),
                minimum=0,
                maximum=SEED_MAX,
                required=False,
                default=None,
                widget="seed",
            )
        )

    if bindings.width is not None:
        params.append(
            ParamDef(
                name="width",
                type="int",
                label=t("comfyui.workflow.widthLabel"),
                minimum=64,
                maximum=8192,
                default=_template_value(template, bindings.width),
            )
        )

    if bindings.height is not None:
        params.append(
            ParamDef(
                name="height",
                type="int",
                label=t("comfyui.workflow.heightLabel"),
                minimum=64,
                maximum=8192,
                default=_template_value(template, bindings.height),
            )
        )

    if bindings.batch_size is not None:
        params.append(
            ParamDef(
                name="batch_size",
                type="int",
                label=t("comfyui.workflow.countLabel"),
                minimum=1,
                maximum=8,
                default=_template_value(template, bindings.batch_size),
            )
        )

    for exposed_param in exposed:
        default = exposed_param.default
        if default is None:
            default = _template_value(
                template, InputRef(node=exposed_param.node, input=exposed_param.input)
            )
        params.append(
            ParamDef(
                name=exposed_param.name,
                type=exposed_param.type,
                label=exposed_param.label,
                choices=exposed_param.choices,
                minimum=exposed_param.minimum,
                maximum=exposed_param.maximum,
                step=exposed_param.step,
                max_length=exposed_param.max_length,
                default=default,
            )
        )

    # ComfyUI は画像の枠(スロット)の数がそのまま最小=最大になる(ADR-0013 3節:
    # 枠はすべて必須で、枚数に合わせて枠を増減しない)。
    slot_count = len(bindings.images)
    op_caps = OperationCapabilities(
        operation=operation,
        params=params,
        max_input_images=slot_count,
        min_input_images=slot_count,
        supports_mask=bindings.mask is not None,
        requires_mask=bindings.mask is not None,
    )

    return ModelCapabilities(
        model=str(wf.id),
        label=wf.name,
        quality_choices=[],
        operations=[op_caps],
    )


# -- 値の差し込み ---------------------------------------------------------


def resolve_prompt(
    template: dict[str, Any],
    bindings: Bindings,
    exposed: list[ExposedParam],
    *,
    prompt: str,
    params: dict[str, Any],
    seed: int,
    image_names: list[str] | None,
    mask_name: str | None,
) -> dict[str, Any]:
    """template を書き換えず、値を差し込んだグラフ全体を新しく返す(決定的)。"""
    graph = copy.deepcopy(template)

    def _set(ref: InputRef, value: Any) -> None:
        graph[ref.node]["inputs"][ref.input] = value

    _set(bindings.prompt, prompt)

    if bindings.negative_prompt is not None and "negative_prompt" in params:
        _set(bindings.negative_prompt, params["negative_prompt"])

    for ref in bindings.seed:
        _set(ref, seed)

    if bindings.width is not None and "width" in params:
        _set(bindings.width, params["width"])
    if bindings.height is not None and "height" in params:
        _set(bindings.height, params["height"])
    if bindings.batch_size is not None and "batch_size" in params:
        _set(bindings.batch_size, params["batch_size"])

    for ref, name in zip(bindings.images, image_names or [], strict=True):
        _set(ref, name)

    mask_load_image = bindings.mask is not None and bindings.mask.mode == "load_image_mask"
    if mask_load_image and mask_name is not None:
        assert bindings.mask.node is not None and bindings.mask.input is not None
        graph[bindings.mask.node]["inputs"][bindings.mask.input] = mask_name
    # image_alpha は枠1(images[0])の名前自体を合成済みの画像にすることで表現する
    # (呼び出し側の責務。ここでは追加の差し込みは不要)。

    for exposed_param in exposed:
        ref = InputRef(node=exposed_param.node, input=exposed_param.input)
        if exposed_param.name in params:
            _set(ref, params[exposed_param.name])
        elif exposed_param.default is not None:
            _set(ref, exposed_param.default)
        # デフォルトも無ければ template の値のまま(何もしない)。

    return graph


# -- analyze(保存しない提案) ----------------------------------------------


@dataclass
class AnalyzeResult:
    nodes: list[dict[str, Any]]
    suggested_bindings: SuggestedBindings
    suggested_operation: Literal["generate", "edit"]
    candidate_params: list[ExposedParam]
    warnings: list[str] = field(default_factory=list)


_TITLE_SLOTS = {
    "gakei:prompt",
    "gakei:negative",
    "gakei:seed",
    "gakei:mask",
    "gakei:output",
    "gakei:final_prompt",
}

# 画像の枠のタイトル規約: `gakei:image`(単数、枠1つだけの場合)、または
# `gakei:image1`、`gakei:image2`… (複数、番号順)。番号なしは 0 番として扱い、
# 番号付きより先に来る(ADR-0013 3節)。
_IMAGE_TITLE_RE = re.compile(r"^gakei:image(\d*)$")

# 可変長の画像入力(`images.image_1`、`images.image_2` など)を示す入力名。ドット区切りの
# 最後が `image_<番号>` で終わるものを対象にする(ComfyUI の可変長入力の慣習)。
_VARIABLE_IMAGE_INPUT_RE = re.compile(r"\.image_(\d+)$")


def _node_title(node: dict[str, Any]) -> str | None:
    meta = node.get("_meta")
    if isinstance(meta, dict):
        title = meta.get("title")
        if isinstance(title, str):
            return title
    return None


def _first_text_input(node: dict[str, Any]) -> str | None:
    inputs = node.get("inputs") or {}
    if "text" in inputs and isinstance(inputs["text"], str) and not _is_wire(inputs["text"]):
        return "text"
    for name, value in inputs.items():
        if isinstance(value, str) and not _is_wire(value):
            return name
    return None


def _first_int_input(node: dict[str, Any], preferred: list[str]) -> str | None:
    inputs = node.get("inputs") or {}
    for name in preferred:
        value = inputs.get(name)
        if isinstance(value, int) and not isinstance(value, bool) and not _is_wire(value):
            return name
    for name, value in inputs.items():
        is_plain_int = isinstance(value, int) and not isinstance(value, bool)
        if is_plain_int and not _is_wire(value) and "seed" in name:
            return name
    return None


def _is_plain_str(value: Any) -> bool:
    return isinstance(value, str) and not _is_wire(value)


def _is_plain_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and not _is_wire(value)


# -- 「値をノードとして切り出す」流儀への対応 ---------------------------------------
#
# ComfyUI の汎用プリミティブノード(class_type が `Primitive` で始まる。`PrimitiveInt`、
# `PrimitiveStringMultiline` など)は、widget の値をそのノード1つの入力(たいてい
# `value`)に持つだけで、他の意味のある値は持たない。seed・width/height/batch_size・
# プロンプトが直値ではなくこの種のノードへの配線になっているワークフロー向けに、
# 配線元を1段だけ辿って値を探す(ADR-0013 フォローアップ)。


def _is_primitive_class_type(class_type: Any) -> bool:
    return isinstance(class_type, str) and class_type.startswith("Primitive")


def _first_plain_int_input(node: dict[str, Any]) -> str | None:
    """整数の値を直接持つ入力を1つ選ぶ。`value`(Primitive ノードの標準の入力名)が
    あればそれを優先し、無ければ該当する入力がちょうど1つのときだけそれを使う
    (複数あって紛らわしい場合は諦める)。
    """
    inputs = node.get("inputs") or {}
    if _is_plain_int(inputs.get("value")):
        return "value"
    candidates = [name for name, value in inputs.items() if _is_plain_int(value)]
    return candidates[0] if len(candidates) == 1 else None


def _first_plain_str_input(node: dict[str, Any]) -> str | None:
    inputs = node.get("inputs") or {}
    if _is_plain_str(inputs.get("value")):
        return "value"
    candidates = [name for name, value in inputs.items() if _is_plain_str(value)]
    return candidates[0] if len(candidates) == 1 else None


def _follow_primitive_int(template: dict[str, Any], wire: Any) -> InputRef | None:
    if not _is_wire(wire):
        return None
    source = template.get(wire[0])
    if not isinstance(source, dict) or not _is_primitive_class_type(source.get("class_type")):
        return None
    name = _first_plain_int_input(source)
    return InputRef(node=wire[0], input=name) if name is not None else None


def _follow_primitive_str(template: dict[str, Any], wire: Any) -> InputRef | None:
    if not _is_wire(wire):
        return None
    source = template.get(wire[0])
    if not isinstance(source, dict) or not _is_primitive_class_type(source.get("class_type")):
        return None
    name = _first_plain_str_input(source)
    return InputRef(node=wire[0], input=name) if name is not None else None


def _resolve_int_ref(
    template: dict[str, Any], node_id: str, value: Any, input_name: str
) -> InputRef | None:
    """整数の入力を差し込み先として使えるかを判定する。直値ならそのまま、配線なら
    配線元の Primitive ノードを1段だけ辿る(ADR-0013 フォローアップ)。
    """
    if _is_plain_int(value):
        return InputRef(node=node_id, input=input_name)
    return _follow_primitive_int(template, value)


# -- プロンプト・ネガティブの文字列入力を選ぶ規則 -----------------------------------


def _string_input_names(
    node: dict[str, Any], class_type: Any, object_info: dict[str, Any] | None
) -> list[str]:
    """プロンプト候補として見てよい入力名を選ぶ。`/object_info` があれば型が `STRING`
    の入力だけ(`COMBO` などの選択肢は除く)、無ければ名前が `text` / `prompt`、または
    `negative` を含むものだけに絞る(ADR-0013 フォローアップ。`reference_latents_method`
    のような選択肢を誤ってプロンプトとして拾わないため)。
    """
    inputs = node.get("inputs") or {}
    if object_info is not None and isinstance(class_type, str):
        node_info = object_info.get(class_type)
        if isinstance(node_info, dict):
            names: list[str] = []
            input_groups = node_info.get("input")
            if isinstance(input_groups, dict):
                for group_name in ("required", "optional"):
                    group = input_groups.get(group_name)
                    if not isinstance(group, dict):
                        continue
                    for name, spec in group.items():
                        if (
                            name in inputs
                            and isinstance(spec, list)
                            and spec
                            and spec[0] == "STRING"
                        ):
                            names.append(name)
            return names
    return [name for name in inputs if name in ("text", "prompt") or "negative" in name]


def _select_text_input(
    node: dict[str, Any],
    class_type: Any,
    object_info: dict[str, Any] | None,
    *,
    prefer_negative: bool = False,
) -> str | None:
    """ノードの文字列入力から、プロンプト用に使う入力名を1つ選ぶ。

    `prefer_negative` は、positive と negative が同じノードを指しているとき
    (例: `TextEncodeQwenImage21` の `prompt`/`negative_prompt`)に使う。その場合は
    名前に `negative` を含む入力を選ぶ。そうでなければ `text`、`prompt`、その他の
    (`negative` を含まない)候補の順に選ぶ(ADR-0013 フォローアップ)。
    """
    names = _string_input_names(node, class_type, object_info)
    if prefer_negative:
        for name in names:
            if "negative" in name:
                return name
        return None
    for preferred in ("text", "prompt"):
        if preferred in names:
            return preferred
    for name in names:
        if "negative" not in name:
            return name
    return None


# -- サンプラー系ノードの探索(class_type を決め打ちしない) --------------------------

_PASS_THROUGH_MAX_DEPTH = 4


def _find_sampler_node_id(template: dict[str, Any]) -> tuple[str, str, str | None] | None:
    """positive 相当・negative 相当の入力を持つノードを探し、
    `(node_id, positive の入力名, negative の入力名)` を返す(ADR-0013 フォローアップ)。

    - `positive` と `negative` を持つノード(KSampler、KSamplerAdvanced など)。
    - `cond1` と `negative` を持つノード(`DualCFGGuider`。`cond1` を positive として扱う)。
    - `guider` を持つノード(`SamplerCustomAdvanced`)。配線先の `CFGGuider`
      (positive/negative を持つ)、`BasicGuider`(conditioning のみ。negative は無い)を
      1段辿る。
    """
    for node_id, node in template.items():
        inputs = node.get("inputs") or {}
        if "positive" in inputs and "negative" in inputs:
            return node_id, "positive", "negative"

    for node_id, node in template.items():
        inputs = node.get("inputs") or {}
        if "cond1" in inputs and "negative" in inputs:
            return node_id, "cond1", "negative"

    for node in template.values():
        inputs = node.get("inputs") or {}
        guider_wire = inputs.get("guider")
        if not _is_wire(guider_wire):
            continue
        guider_node = template.get(guider_wire[0])
        if not isinstance(guider_node, dict):
            continue
        guider_inputs = guider_node.get("inputs") or {}
        if "positive" in guider_inputs and "negative" in guider_inputs:
            return guider_wire[0], "positive", "negative"
        if "conditioning" in guider_inputs:
            return guider_wire[0], "conditioning", None

    return None


def _find_conditioning_wire(
    node: dict[str, Any], class_type: Any, object_info: dict[str, Any] | None
) -> Any | None:
    """素通りノード(`FluxGuidance`、`ConditioningZeroOut`、`ReferenceLatent`、
    `FluxKontextMultiReferenceLatentMethod` など)の先へ進むための配線を探す。
    `/object_info` があれば型が `CONDITIONING` の入力、無ければ名前に `conditioning`
    を含む入力を対象にする(ADR-0013 フォローアップ)。
    """
    inputs = node.get("inputs") or {}
    names: list[str] = []
    if object_info is not None and isinstance(class_type, str):
        node_info = object_info.get(class_type)
        if isinstance(node_info, dict):
            input_groups = node_info.get("input")
            if isinstance(input_groups, dict):
                for group_name in ("required", "optional"):
                    group = input_groups.get(group_name)
                    if not isinstance(group, dict):
                        continue
                    for name, spec in group.items():
                        if isinstance(spec, list) and spec and spec[0] == "CONDITIONING":
                            names.append(name)
    if not names:
        names = [name for name in inputs if "conditioning" in name]
    for name in names:
        value = inputs.get(name)
        if _is_wire(value):
            return value
    return None


def _resolve_prompt_ref(
    template: dict[str, Any],
    object_info: dict[str, Any] | None,
    node_id: str,
    *,
    prefer_negative: bool,
    is_negative: bool,
    depth: int = 0,
) -> tuple[InputRef | None, InputRef | None]:
    """サンプラーの positive/negative の配線元から、実際のプロンプト文字列の差し込み先を
    探す。文字列入力を持たない素通りノード(`FluxGuidance`、`ReferenceLatent` など)は、
    conditioning 系の入力を辿って先へ進む(深さの上限 4。ADR-0013 フォローアップ)。

    `ConditioningZeroOut` は、辿ってきたのが negative 側であっても「空にしている」
    ノードなので、そこで探索を打ち切り None を返す(ネガティブの差し込み先にはしない)。

    戻り値は `(解決できた差し込み先, 経路上で最後に見つかった文字列入力)` のタプル。
    後者は解決に失敗したとき(1つ目が None)だけ意味を持ち、手動選択の候補を BFS で
    探す起点(プロンプト強化ワークフローなどのエンコーダー)に使う(ADR-0013 フォローアップ)。
    """
    if depth > _PASS_THROUGH_MAX_DEPTH:
        return None, None
    node = template.get(node_id)
    if not isinstance(node, dict):
        return None, None
    class_type = node.get("class_type")
    if is_negative and class_type == "ConditioningZeroOut":
        return None, None

    text_name = _select_text_input(node, class_type, object_info, prefer_negative=prefer_negative)
    last_text_ref = InputRef(node=node_id, input=text_name) if text_name is not None else None
    if text_name is not None:
        value = (node.get("inputs") or {}).get(text_name)
        if _is_plain_str(value):
            return InputRef(node=node_id, input=text_name), None
        if _is_wire(value):
            followed = _follow_primitive_str(template, value)
            if followed is not None:
                return followed, None

    next_wire = _find_conditioning_wire(node, class_type, object_info)
    if next_wire is not None:
        resolved, deeper_last = _resolve_prompt_ref(
            template,
            object_info,
            next_wire[0],
            prefer_negative=prefer_negative,
            is_negative=is_negative,
            depth=depth + 1,
        )
        return resolved, (deeper_last or last_text_ref)
    return None, last_text_ref


# -- プロンプトの差し込み先が特定できないときの候補列挙 -------------------------------
#
# 「プロンプト強化(prompt enhancer)」型のワークフローは、エンコーダーの prompt が直値
# でも Primitive ノード1段でもなく、正規表現置換や文字列連結を挟んで複数の固定文字列
# ノードに行き着く。`_resolve_prompt_ref` では特定できないので、探索が止まった地点
# (エンコーダーの文字列入力)から配線を辿り、手動選択の材料になりそうな直値の文字列
# 入力を候補として提案する(ADR-0013 フォローアップ)。

_PROMPT_CANDIDATE_MAX_DEPTH = 12
_PROMPT_CANDIDATE_MAX_COUNT = 5

# 自由文らしい入力名。`value` は Primitive 系ノードの標準の入力名、`text`/`prompt`/
# `string*` はそれ以外のノードでよく使われる名前。
_PROMPT_CANDIDATE_NAME_RE = re.compile(r"^(value|text|prompt|string.*)$")
# 名前だけでは判断がつかない、明らかにプロンプトではない設定値(正規表現の置換設定、
# 連結の区切り文字など)。
_PROMPT_CANDIDATE_NAME_EXCLUDE = frozenset({"regex_pattern", "replace", "delimiter"})


def _is_prompt_candidate_name(name: str) -> bool:
    if name in _PROMPT_CANDIDATE_NAME_EXCLUDE:
        return False
    return bool(_PROMPT_CANDIDATE_NAME_RE.match(name))


def _find_prompt_candidates(
    template: dict[str, Any], start: InputRef
) -> list[tuple[int, str, str]]:
    """`start`(探索が止まったノードの文字列入力)から配線を幅優先で辿り、直値の文字列
    入力で名前がプロンプトらしいものを候補として集める。深さの上限と訪問済み集合で
    循環・際限ない探索を防ぐ。戻り値は `(深さ, ノードID, 入力名)` のタプルのリストで、
    深さ→ノードID の順に並べて返す(呼び出し側はそのまま整形すればよい)。
    """
    candidates: list[tuple[int, str, str]] = []
    visited: set[str] = {start.node}
    queue: list[tuple[str, int]] = [(start.node, 0)]
    while queue:
        node_id, depth = queue.pop(0)
        node = template.get(node_id)
        if not isinstance(node, dict) or depth > _PROMPT_CANDIDATE_MAX_DEPTH:
            continue
        for name, value in (node.get("inputs") or {}).items():
            if _is_wire(value):
                target_id = value[0]
                if target_id not in visited:
                    visited.add(target_id)
                    queue.append((target_id, depth + 1))
            elif _is_plain_str(value) and _is_prompt_candidate_name(name):
                candidates.append((depth, node_id, name))
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates


def _format_prompt_candidate(node: dict[str, Any], node_id: str, input_name: str) -> str:
    title = _node_title(node) or node.get("class_type") or node_id
    if len(title) > 30:
        title = title[:30] + "…"
    return f"{node_id}「{title}」.{input_name}"


def _prompt_candidates_warning(template: dict[str, Any], start: InputRef | None) -> str | None:
    """プロンプトの差し込み先が特定できなかったときの警告文。候補が見つかれば手動選択
    の材料として列挙し、見つからなければ None を返す(呼び出し側が従来の文言を使う)。
    """
    if start is None:
        return None
    candidates = _find_prompt_candidates(template, start)
    if not candidates:
        return None
    shown = candidates[:_PROMPT_CANDIDATE_MAX_COUNT]
    joiner = t("comfyui.workflow.listSeparator")
    formatted = joiner.join(
        _format_prompt_candidate(template[node_id], node_id, input_name)
        for _, node_id, input_name in shown
    )
    remainder = len(candidates) - len(shown)
    if remainder > 0:
        formatted += t("comfyui.workflow.moreCandidatesSuffix", count=remainder)
    return t("comfyui.workflow.promptBindingCandidates", candidates=formatted)


def _find_seed_refs_from_object_info(
    template: dict[str, Any], object_info: dict[str, Any]
) -> list[InputRef]:
    """`/object_info` の `control_after_generate` が真偽値の `True` である INT 入力を
    seed の候補にする。ComfyUI の汎用 `PrimitiveInt` は `control_after_generate` に
    列挙値の文字列(`"fixed"` など)を返すため、`is True` で真偽値だけに絞る必要がある
    (そうしないと文字列 `"fixed"` が truthy になり、Width/Height/Steps 用に切り出した
    PrimitiveInt まで seed と誤認する)。配線なら Primitive ノードを1段辿る。
    """
    refs: list[InputRef] = []
    for node_id, node in template.items():
        class_type = node.get("class_type")
        if not isinstance(class_type, str):
            continue
        node_info = object_info.get(class_type)
        if not isinstance(node_info, dict):
            continue
        input_groups = node_info.get("input")
        if not isinstance(input_groups, dict):
            continue
        node_inputs = node.get("inputs") or {}
        for group_name in ("required", "optional"):
            group = input_groups.get(group_name)
            if not isinstance(group, dict):
                continue
            for name, spec in group.items():
                if not isinstance(spec, list) or not spec:
                    continue
                type_spec = spec[0]
                config = spec[1] if len(spec) > 1 else None
                if type_spec != "INT" or not isinstance(config, dict):
                    continue
                if config.get("control_after_generate") is not True:
                    continue
                ref = _resolve_int_ref(template, node_id, node_inputs.get(name), name)
                if ref is not None:
                    refs.append(ref)
    return refs


def _find_seed_refs_fallback(template: dict[str, Any]) -> list[InputRef]:
    """`/object_info` が無いときの seed の推定: 入力名が `seed` か `noise_seed` の整数値
    (配線なら Primitive ノードを1段辿る)。
    """
    refs: list[InputRef] = []
    for node_id, node in template.items():
        inputs = node.get("inputs") or {}
        for name in ("seed", "noise_seed"):
            if name not in inputs:
                continue
            ref = _resolve_int_ref(template, node_id, inputs[name], name)
            if ref is not None:
                refs.append(ref)
    return refs


def _filter_output_candidates(candidates: list[tuple[str, str]]) -> list[str]:
    """出力ノードの候補から、比較・プレビュー用の UI ノードを除く。`Save` を含む
    class_type が1つでもあれば、`Save` を含まないものは候補から外す(`ImageCompare` の
    ような UI 専用ノードが `output_node: true` を返すケースへの対策。ADR-0013 フォローアップ)。
    """
    filtered = [
        (node_id, class_type)
        for node_id, class_type in candidates
        if "Preview" not in class_type and "Compare" not in class_type
    ]
    if any("Save" in class_type for _, class_type in filtered):
        filtered = [
            (node_id, class_type) for node_id, class_type in filtered if "Save" in class_type
        ]
    return [node_id for node_id, _ in filtered]


def _find_output_ids_from_object_info(
    template: dict[str, Any], object_info: dict[str, Any]
) -> list[str]:
    """`/object_info` の `output_node: true` を出力ノードの候補にする。"""
    candidates: list[tuple[str, str]] = []
    for node_id, node in template.items():
        class_type = node.get("class_type")
        if not isinstance(class_type, str):
            continue
        node_info = object_info.get(class_type)
        if isinstance(node_info, dict) and node_info.get("output_node") is True:
            candidates.append((node_id, class_type))
    return _filter_output_candidates(candidates)


def _find_output_ids_fallback(template: dict[str, Any]) -> list[str]:
    """`/object_info` が無いときの出力ノードの推定: class_type に `SaveImage` を含むもの。"""
    candidates: list[tuple[str, str]] = []
    for node_id, node in template.items():
        class_type = node.get("class_type")
        if isinstance(class_type, str) and "SaveImage" in class_type:
            candidates.append((node_id, class_type))
    return _filter_output_candidates(candidates)


# -- 最終プロンプト(PE の出力)のノードの推定(ADR-0030 1章) ---------------------------

# テキストを表示する出力ノードの class_type(`/object_info` が無いときの推定に使う)。
# ComfyUI 本体の `PreviewAny` と、よく使われるカスタムノードの表示ノード。
_TEXT_DISPLAY_CLASS_TYPES = frozenset({"PreviewAny", "PreviewText", "ShowText|pysssss", "ShowText"})
# `/object_info` の `output_node: true` でも、画像・動画・音声などを扱うものは除く。
_NON_TEXT_OUTPUT_MARKERS = ("Image", "Video", "Audio", "Mesh", "Save", "Compare", "Latent")
_FINAL_PROMPT_MAX_DEPTH = 12


def _is_text_display_node(node: dict[str, Any], object_info: dict[str, Any] | None) -> bool:
    class_type = node.get("class_type")
    if not isinstance(class_type, str):
        return False
    if class_type in _TEXT_DISPLAY_CLASS_TYPES:
        return True
    if object_info is None:
        return False
    node_info = object_info.get(class_type)
    if not isinstance(node_info, dict) or node_info.get("output_node") is not True:
        return False
    return not any(marker in class_type for marker in _NON_TEXT_OUTPUT_MARKERS)


def _find_encoder_text_wires(
    template: dict[str, Any], object_info: dict[str, Any] | None
) -> list[Any]:
    """サンプラーの positive 側のエンコーダーの、プロンプト入力につながる配線を集める。
    素通りノード(`FluxGuidance`、`ReferenceLatent` など)は `_resolve_prompt_ref` と同じく
    conditioning 系の入力を辿って跨ぐ(深さの上限 4)。直値のプロンプトには配線が無いので
    何も返さない。
    """
    sampler_info = _find_sampler_node_id(template)
    if sampler_info is None:
        return []
    sampler_id, positive_input_name, _ = sampler_info
    wire = (template[sampler_id].get("inputs") or {}).get(positive_input_name)
    wires: list[Any] = []
    depth = 0
    while _is_wire(wire) and depth <= _PASS_THROUGH_MAX_DEPTH:
        node = template.get(wire[0])
        if not isinstance(node, dict):
            break
        class_type = node.get("class_type")
        text_name = _select_text_input(node, class_type, object_info)
        if text_name is not None:
            value = (node.get("inputs") or {}).get(text_name)
            if _is_wire(value):
                wires.append(value)
        wire = _find_conditioning_wire(node, class_type, object_info)
        depth += 1
    return wires


def _find_final_prompt_node(
    template: dict[str, Any], object_info: dict[str, Any] | None
) -> str | None:
    """最終プロンプトのノードの初期値を推定する(ADR-0030 1章)。

    1. タイトルが `gakei:final_prompt` のノード
    2. エンコーダーのプロンプト入力から配線を幅優先でさかのぼり、最初に見つかる
       テキストを表示する出力ノード(`PreviewAny` など。`/object_info` があれば
       `output_node: true` のものも)
    見つからなければ None(PE の無いワークフローが普通なので警告は出さない)。
    """
    for node_id, node in template.items():
        if _node_title(node) == "gakei:final_prompt":
            return node_id

    for start in _find_encoder_text_wires(template, object_info):
        visited: set[str] = {start[0]}
        queue: list[tuple[str, int]] = [(start[0], 0)]
        while queue:
            node_id, depth = queue.pop(0)
            node = template.get(node_id)
            if not isinstance(node, dict):
                continue
            if _is_text_display_node(node, object_info):
                return node_id
            if depth >= _FINAL_PROMPT_MAX_DEPTH:
                continue
            for value in (node.get("inputs") or {}).values():
                if _is_wire(value) and value[0] not in visited:
                    visited.add(value[0])
                    queue.append((value[0], depth + 1))
    return None


def _build_analyze_nodes(template: dict[str, Any]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for node_id, node in template.items():
        inputs_info = [
            {"name": name, "value": value, "linked": _is_wire(value)}
            for name, value in (node.get("inputs") or {}).items()
        ]
        nodes.append(
            {
                "id": node_id,
                "class_type": node.get("class_type"),
                "title": _node_title(node),
                "inputs": inputs_info,
            }
        )
    return nodes


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
        for input_name, value in (node.get("inputs") or {}).items():
            if (node_id, input_name) in used:
                continue
            if _is_wire(value):
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


def _title_image_slots(template: dict[str, Any]) -> list[InputRef] | None:
    """タイトルによる画像の枠の指定。`gakei:image`(枠1つ)または `gakei:image1`、
    `gakei:image2`… (番号順)を持つノードを探す。見つからなければ None を返し、
    呼び出し側はヒューリスティックにフォールバックする(ADR-0013 3節)。
    """
    matches: list[tuple[int, str]] = []
    for node_id, node in template.items():
        title = _node_title(node)
        if title is None:
            continue
        match = _IMAGE_TITLE_RE.match(title)
        if match is None:
            continue
        number = int(match.group(1)) if match.group(1) else 0
        matches.append((number, node_id))
    if not matches:
        return None
    matches.sort(key=lambda item: item[0])
    slots = [
        InputRef(node=node_id, input="image")
        for _, node_id in matches
        if "image" in (template[node_id].get("inputs") or {})
    ]
    return slots or None


def _numbered_load_image_slots(template: dict[str, Any]) -> list[InputRef]:
    """可変長の画像入力(`images.image_1`、`images.image_2` など)に直接つながる
    `LoadImage` を、その番号順に並べる。配線を辿るのは直接の配線だけ(ADR-0013 3節:
    「可変長の画像入力につながる LoadImage は、その番号の順に並べる」)。
    """
    numbered: dict[int, str] = {}
    for node in template.values():
        for input_name, value in (node.get("inputs") or {}).items():
            match = _VARIABLE_IMAGE_INPUT_RE.search(input_name)
            if match is None or not _is_wire(value):
                continue
            target = template.get(value[0])
            if not isinstance(target, dict) or target.get("class_type") != "LoadImage":
                continue
            numbered.setdefault(int(match.group(1)), value[0])
    return [InputRef(node=numbered[number], input="image") for number in sorted(numbered)]


def analyze_workflow(
    template: dict[str, Any], object_info: dict[str, Any] | None = None
) -> AnalyzeResult:
    """保存前の提案。テンプレートは変更しない。UI 形式・API 形式でないものは呼び出し側で
    先に弾く想定(`is_ui_format_template` / `is_api_format_template`)。

    `object_info` は ComfyUI の `/object_info`(接続できなければ None。呼び出し側が
    保存や DB に触れずに取得したものを渡す)。あれば seed と出力ノードの推定に使う。
    純粋関数のまま(HTTP は呼ばない)。
    """
    nodes = _build_analyze_nodes(template)
    warnings: list[str] = []

    title_nodes: dict[str, str] = {}
    for node_id, node in template.items():
        title = _node_title(node)
        if title in _TITLE_SLOTS and title not in title_nodes:
            title_nodes[title] = node_id

    prompt_ref: InputRef | None = None
    negative_ref: InputRef | None = None
    seed_refs: list[InputRef] = []
    width_ref: InputRef | None = None
    height_ref: InputRef | None = None
    batch_size_ref: InputRef | None = None
    image_refs: list[InputRef] = []
    mask_binding: MaskBinding | None = None
    output_ids: list[str] = []

    # 1. タイトルによる指定
    if "gakei:prompt" in title_nodes:
        node_id = title_nodes["gakei:prompt"]
        node = template[node_id]
        input_name = "text" if "text" in (node.get("inputs") or {}) else _first_text_input(node)
        if input_name is not None:
            prompt_ref = InputRef(node=node_id, input=input_name)

    if "gakei:negative" in title_nodes:
        node_id = title_nodes["gakei:negative"]
        node = template[node_id]
        input_name = "text" if "text" in (node.get("inputs") or {}) else _first_text_input(node)
        if input_name is not None:
            negative_ref = InputRef(node=node_id, input=input_name)

    if "gakei:seed" in title_nodes:
        node_id = title_nodes["gakei:seed"]
        node = template[node_id]
        input_name = _first_int_input(node, ["seed", "noise_seed"])
        if input_name is not None:
            seed_refs.append(InputRef(node=node_id, input=input_name))

    title_image_slots = _title_image_slots(template)
    if title_image_slots is not None:
        image_refs = title_image_slots

    if "gakei:mask" in title_nodes:
        node_id = title_nodes["gakei:mask"]
        node = template[node_id]
        if node.get("class_type") == "LoadImageMask" and "image" in (node.get("inputs") or {}):
            mask_binding = MaskBinding(mode="load_image_mask", node=node_id, input="image")

    if "gakei:output" in title_nodes:
        for node_id, node in template.items():
            if _node_title(node) == "gakei:output":
                output_ids.append(node_id)

    # 2. サンプラー系ノード(positive/negative 相当の入力を持つ。class_type は決め打ちしない)の
    # 配線を辿った、文字列入力(prompt/negative_prompt など)。文字列入力を持たない素通り
    # ノード(FluxGuidance、ConditioningZeroOut、ReferenceLatent など)は跨いで辿る
    # (深さ上限4。ADR-0013 フォローアップ)。
    sampler_info = _find_sampler_node_id(template)
    prompt_search_start: InputRef | None = None

    if sampler_info is not None:
        sampler_id, positive_input_name, negative_input_name = sampler_info
        sampler_inputs = template[sampler_id].get("inputs") or {}
        positive_wire = sampler_inputs.get(positive_input_name)
        negative_wire = (
            sampler_inputs.get(negative_input_name) if negative_input_name is not None else None
        )
        positive_target_id = positive_wire[0] if _is_wire(positive_wire) else None
        negative_target_id = negative_wire[0] if _is_wire(negative_wire) else None

        if prompt_ref is None and positive_target_id is not None:
            prompt_ref, prompt_search_start = _resolve_prompt_ref(
                template,
                object_info,
                positive_target_id,
                prefer_negative=False,
                is_negative=False,
            )

        if negative_ref is None and negative_target_id is not None:
            prefer_negative = negative_target_id == positive_target_id
            negative_ref, _ = _resolve_prompt_ref(
                template,
                object_info,
                negative_target_id,
                prefer_negative=prefer_negative,
                is_negative=True,
            )

    # 3. seed: /object_info があれば control_after_generate が真偽値 true の INT、無ければ
    # 入力名 seed / noise_seed の整数値(どちらも配線なら Primitive ノードを1段辿る)。
    if not seed_refs:
        if object_info is not None:
            seed_refs = _find_seed_refs_from_object_info(template, object_info)
        else:
            seed_refs = _find_seed_refs_fallback(template)

    # 4. width / height / batch_size: class_type が Empty で始まり Latent を含むノード
    # (EmptyLatentImage、EmptySD3LatentImage、EmptyFlux2LatentImage など。完全一致にしない
    # ことで新しいモデル系のテンプレートにも対応する。ADR-0013 フォローアップ)。配線なら
    # Primitive ノードを1段辿る。
    for node_id, node in template.items():
        class_type = node.get("class_type")
        if not isinstance(class_type, str) or not class_type.startswith("Empty"):
            continue
        if "Latent" not in class_type:
            continue
        inputs = node.get("inputs") or {}
        if "width" not in inputs or "height" not in inputs:
            continue
        if width_ref is None:
            width_ref = _resolve_int_ref(template, node_id, inputs.get("width"), "width")
        if height_ref is None:
            height_ref = _resolve_int_ref(template, node_id, inputs.get("height"), "height")
        if batch_size_ref is None and "batch_size" in inputs:
            batch_size_ref = _resolve_int_ref(
                template, node_id, inputs.get("batch_size"), "batch_size"
            )
        break

    # 5. LoadImage: タイトル(gakei:image[N])が優先。無ければ、可変長の画像入力
    # (images.image_1 など)に直接つながる LoadImage をその番号順に、残りはテンプレート内の
    # 出現順(ノードの id 順)に並べる(ADR-0013 3節)。
    load_image_ids = [
        node_id for node_id, node in template.items() if node.get("class_type") == "LoadImage"
    ]
    has_load_image = bool(load_image_ids)
    if not image_refs:
        numbered_refs = _numbered_load_image_slots(template)
        ordered_ids = {ref.node for ref in numbered_refs}
        remaining_refs = [
            InputRef(node=node_id, input="image")
            for node_id in load_image_ids
            if node_id not in ordered_ids and "image" in (template[node_id].get("inputs") or {})
        ]
        image_refs = numbered_refs + remaining_refs

    # 6. LoadImageMask、無ければ 枠1(images[0])の LoadImage の MASK 出力(index=1)の
    # 配線を探す。マスクは1枚目の画像に対するものだけ(ADR-0013 3節)。
    if mask_binding is None:
        for node_id, node in template.items():
            if node.get("class_type") == "LoadImageMask" and "image" in (node.get("inputs") or {}):
                mask_binding = MaskBinding(mode="load_image_mask", node=node_id, input="image")
                break
    if mask_binding is None and image_refs:
        first_slot_node = image_refs[0].node
        for node in template.values():
            for value in (node.get("inputs") or {}).values():
                if _is_wire(value) and value[0] == first_slot_node and value[1] == 1:
                    mask_binding = MaskBinding(mode="image_alpha")
                    break
            if mask_binding is not None:
                break

    # 7. 出力ノード: /object_info があれば output_node: true、無ければ class_type に
    # SaveImage を含むもの(どちらも Preview・Compare 系の UI 専用ノードは除き、Save を
    # 含むものが1つでもあれば含まないものは外す。ADR-0013 フォローアップ)。
    if not output_ids:
        if object_info is not None:
            output_ids = _find_output_ids_from_object_info(template, object_info)
        else:
            output_ids = _find_output_ids_fallback(template)

    if not output_ids:
        class_types = [
            node.get("class_type")
            for node in template.values()
            if isinstance(node.get("class_type"), str)
        ]
        has_preview_image = "PreviewImage" in class_types
        has_save = any("Save" in class_type for class_type in class_types)
        if has_preview_image and not has_save:
            # PreviewImage だけで終わるワークフロー(ADR-0013「途中経過画像は Asset に
            # しない」の裏返し)。SaveImage が無いので、候補を探す前に案内を出す。
            warnings.append(t("comfyui.workflow.previewImageNotSupported"))
        else:
            referenced: set[str] = set()
            for node in template.values():
                for value in (node.get("inputs") or {}).values():
                    if _is_wire(value):
                        referenced.add(value[0])
            fallback = [
                node_id
                for node_id, node in template.items()
                if node_id not in referenced
                and "Preview" not in (node.get("class_type") or "")
                and "Compare" not in (node.get("class_type") or "")
            ]
            if fallback:
                warnings.append(
                    t("comfyui.workflow.outputNodeNotFoundWithCandidates") + ", ".join(fallback)
                )
            else:
                warnings.append(t("comfyui.workflow.outputNodeNotFound"))

    suggested_operation: Literal["generate", "edit"] = "edit" if has_load_image else "generate"

    if prompt_ref is None:
        # 直接・Primitive 1段では特定できなかった場合、探索が止まった地点(エンコーダーの
        # 文字列入力)から配線を辿って、手動選択の候補になりそうな直値の文字列入力を探す
        # (プロンプト強化ワークフローなど。ADR-0013 フォローアップ)。見つからなければ
        # 従来通りの文言のまま。
        candidates_warning = _prompt_candidates_warning(template, prompt_search_start)
        warnings.append(candidates_warning or t("comfyui.workflow.promptBindingNotFound"))
    if suggested_operation == "edit" and not image_refs:
        warnings.append(t("comfyui.workflow.imageBindingNotFound"))

    if suggested_operation == "edit" and mask_binding is None:
        # マスクの差し込み先が見つからなくても、グラフに専用のマスク作成ノード(キャンバスに
        # 直接描く Painter 系など、LoadImage / LoadImageMask 以外)があれば、GAKEI の2方式
        # (load_image_mask / image_alpha)のどちらにも当てはまらない方式だと案内する。
        for node in template.values():
            class_type = node.get("class_type")
            if not isinstance(class_type, str) or class_type in ("LoadImage", "LoadImageMask"):
                continue
            if "Painter" in class_type or "Mask" in class_type:
                warnings.append(t("comfyui.workflow.maskMethodUnsupported"))
                break

    # 見つからなかった必須項目(prompt、edit の image)は上の warnings に書いてあるが、
    # 見つかった他の提案(seed、出力ノードなど)は捨てずに返す(ADR-0013 フォローアップ)。
    # 画像の枠が複数あること自体は警告しない(ADR-0013 3節: 枠の数だけ入力を要求すればよい)。
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
        final_prompt=_find_final_prompt_node(template, object_info),
    )

    used: set[tuple[str, str]] = set()
    for ref in (prompt_ref, negative_ref, width_ref, height_ref, batch_size_ref):
        if ref is not None:
            used.add((ref.node, ref.input))
    for ref in (*seed_refs, *image_refs):
        used.add((ref.node, ref.input))
    if mask_binding is not None and mask_binding.mode == "load_image_mask":
        assert mask_binding.node is not None and mask_binding.input is not None
        used.add((mask_binding.node, mask_binding.input))

    candidate_params = _build_candidate_params(template, used)

    return AnalyzeResult(
        nodes=nodes,
        suggested_bindings=suggested_bindings,
        suggested_operation=suggested_operation,
        candidate_params=candidate_params,
        warnings=warnings,
    )
