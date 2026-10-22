"""ComfyUI のワークフロー登録(ADR-0013)。差し込み先(`Bindings`)と公開パラメータ
(`ExposedParam`)の定義、保存時の検証(エラーと警告。ADR-0052 3章)、`capabilities()`
への変換、値の差し込み(`resolve_prompt`)をまとめる。登録画面向けの読み取り(analyze)は
`comfy_analyze` にある。

いずれも DB やプロバイダーの実行(`app/providers/comfyui/`)には依存しない純粋な関数群。
グラフの構造そのものは変えない(ADR-0013: ノードの追加・削除・配線の変更はしない)。
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.domain import comfy_bounding_boxes, comfy_output_size
from app.domain.comfy_graph import WorkflowGraph, is_wire
from app.domain.models import ComfyWorkflow
from app.i18n import t
from app.providers.base import (
    ModelCapabilities,
    OperationCapabilities,
    OutputDimension,
    OutputSizeOperand,
    OutputSizeRule,
    ParamDef,
    ParamType,
)

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
    # 領域の指示の四角を差し込む先(ADR-0053 6章)。`CreateBoundingBoxes` の `bboxes` 入力。
    # 配線されていても差し込める(実行時に定数で上書きする)。旧形式(キー無し)は None。
    bounding_boxes: InputRef | None = None

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
    bounding_boxes: InputRef | None = None

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


# -- sha256 -------------------------------------------------------------


def compute_template_sha256(template: dict[str, Any]) -> str:
    encoded = json.dumps(template, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


# -- 検証(保存時。ADR-0052 3章) ------------------------------------------------

ValidationLevel = Literal["error", "warning"]
ValidationCode = Literal[
    "node_missing",
    "input_missing",
    "duplicate",
    "wired_input",
    "type_mismatch",
    "output_missing",
    "output_no_image",
    "final_prompt_missing",
    "edit_requires_image",
    "generate_has_image",
    "exposed_param_duplicate",
    "unused_branch",
    "mask_not_inpaint",
    "display_only_output",
    "bounding_boxes_not_node",
]


class ValidationIssue(BaseModel):
    """保存時の検証の結果1件。`error` は保存しない(422)、`warning` は保存して画面に残す。"""

    level: ValidationLevel
    code: ValidationCode
    message: str
    # どの項目か(`prompt`、`seed[0]`、`outputs`、`exposed_params.steps` など)。
    field: str | None = None
    node: str | None = None
    input: str | None = None


class WorkflowValidationError(ValueError):
    """ワークフロー登録の検証失敗。API 層で 422 に変換する。`issues` はエラーの一覧。"""

    def __init__(self, message: str, issues: list[ValidationIssue] | None = None) -> None:
        super().__init__(message)
        self.issues = issues or []


# 差し込み先の値の種類。型の検証に使う。
_ValueKind = Literal["string", "image", "int", "float", "bool", "enum"]

_KIND_TYPES: dict[str, tuple[str, ...]] = {
    "string": ("STRING",),
    "int": ("INT",),
    "float": ("FLOAT", "INT"),
    "bool": ("BOOLEAN",),
}


def _kind_label(kind: _ValueKind) -> str:
    labels = {
        "string": lambda: t("comfyui.validation.kind.string"),
        "image": lambda: t("comfyui.validation.kind.string"),
        "int": lambda: t("comfyui.validation.kind.int"),
        "float": lambda: t("comfyui.validation.kind.float"),
        "bool": lambda: t("comfyui.validation.kind.bool"),
        "enum": lambda: t("comfyui.validation.kind.enum"),
    }
    return labels[kind]()


def _value_matches_kind(value: Any, kind: _ValueKind) -> bool:
    if kind in ("string", "image"):
        return isinstance(value, str)
    if kind == "int":
        if isinstance(value, float) and not isinstance(value, bool):
            return value.is_integer()
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "float":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if kind == "bool":
        return isinstance(value, bool)
    # enum: 選択肢は文字列のことが多いが、数の選択肢もある。
    return isinstance(value, str | int | float) and not isinstance(value, bool)


def _param_kind(param_type: ParamType) -> _ValueKind:
    return {"text": "string", "int": "int", "float": "float", "bool": "bool"}.get(
        param_type, "enum"
    )  # type: ignore[return-value]


def check_workflow(
    template: dict[str, Any],
    operation: Literal["generate", "edit"],
    bindings: Bindings | SuggestedBindings,
    exposed_params: list[ExposedParam],
    object_info: dict[str, Any] | None = None,
) -> list[ValidationIssue]:
    """差し込み先と公開パラメーターの役割を検証し、エラーと警告を返す(ADR-0052 3章)。

    `SuggestedBindings`(analyze の提案)も受け付ける。その場合、見つからなかった必須項目
    (prompt、outputs)は問わない。テンプレートの形式(UI 形式など)は呼び出し側で先に確かめる。
    「生きている部分」の判定は読み取り(`comfy_analyze`)と同じ `WorkflowGraph` を使う。
    """
    graph = WorkflowGraph(template, object_info)
    states = graph.node_states(extra_anchors=bindings.outputs)
    live = {nid for nid, state in states.items() if state == "live"}
    issues: list[ValidationIssue] = []
    used: set[tuple[str, str]] = set()

    def _error(
        code: ValidationCode,
        message: str,
        field: str,
        node: str | None = None,
        input: str | None = None,
    ) -> None:
        issues.append(
            ValidationIssue(
                level="error", code=code, message=message, field=field, node=node, input=input
            )
        )

    def _warn(
        code: ValidationCode,
        message: str,
        field: str,
        node: str | None = None,
        input: str | None = None,
    ) -> None:
        issues.append(
            ValidationIssue(
                level="warning", code=code, message=message, field=field, node=node, input=input
            )
        )

    def _check_ref(ref: InputRef, label: str, field: str, kind: _ValueKind | None) -> None:
        if not graph.has(ref.node):
            _error(
                "node_missing",
                t("comfyui.workflow.bindingNodeMissing", label=label, node=ref.node),
                field,
                ref.node,
                ref.input,
            )
            return
        inputs = graph.inputs(ref.node)
        if ref.input not in inputs:
            _error(
                "input_missing",
                t(
                    "comfyui.workflow.bindingInputMissing",
                    label=label,
                    node=ref.node,
                    input=ref.input,
                ),
                field,
                ref.node,
                ref.input,
            )
            return
        key = (ref.node, ref.input)
        if key in used:
            _error(
                "duplicate",
                t("comfyui.workflow.bindingDuplicate", node=ref.node, input=ref.input),
                field,
                ref.node,
                ref.input,
            )
            return
        used.add(key)
        value = inputs[ref.input]
        if is_wire(value):
            # 実行時に配線を定数で上書きしてしまうので、差し込み先にも公開にもできない。
            _error(
                "wired_input",
                t("comfyui.validation.wiredInput", label=label, node=ref.node, input=ref.input),
                field,
                ref.node,
                ref.input,
            )
            return
        if kind is not None and not _type_ok(graph, ref, value, kind):
            _error(
                "type_mismatch",
                t(
                    "comfyui.validation.typeMismatch",
                    label=label,
                    node=ref.node,
                    input=ref.input,
                    expected=_kind_label(kind),
                ),
                field,
                ref.node,
                ref.input,
            )
            return
        if live and states.get(ref.node) != "live":
            # 錨(保存ノード)が1つも無いときは、出力が無いこと自体を別に示すので言わない。
            _warn(
                "unused_branch",
                t("comfyui.validation.unusedBranch", label=label, node=ref.node, input=ref.input),
                field,
                ref.node,
                ref.input,
            )

    def _check_bounding_boxes(ref: InputRef) -> None:
        """四角の差し込み先(ADR-0053 6章)。`CreateBoundingBoxes` の `bboxes` だけを受ける。
        `bboxes` は任意の入力なので、テンプレートに無くても(配線されていても)差し込める。"""
        field = "bounding_boxes"
        if not graph.has(ref.node):
            _error(
                "node_missing",
                t("comfyui.workflow.bindingNodeMissing", label=field, node=ref.node),
                field,
                ref.node,
                ref.input,
            )
            return
        if (
            graph.class_type(ref.node) != comfy_bounding_boxes.CLASS_TYPE
            or ref.input != comfy_bounding_boxes.BBOXES_INPUT
        ):
            _error(
                "bounding_boxes_not_node",
                t(
                    "comfyui.validation.boundingBoxesNotNode",
                    node=ref.node,
                    input=ref.input,
                    class_type=comfy_bounding_boxes.CLASS_TYPE,
                ),
                field,
                ref.node,
                ref.input,
            )
            return
        key = (ref.node, ref.input)
        if key in used:
            _error(
                "duplicate",
                t("comfyui.workflow.bindingDuplicate", node=ref.node, input=ref.input),
                field,
                ref.node,
                ref.input,
            )
            return
        used.add(key)
        if live and states.get(ref.node) != "live":
            _warn(
                "unused_branch",
                t("comfyui.validation.unusedBranch", label=field, node=ref.node, input=ref.input),
                field,
                ref.node,
                ref.input,
            )

    if bindings.prompt is not None:
        _check_ref(bindings.prompt, "prompt", "prompt", "string")
    if bindings.negative_prompt is not None:
        _check_ref(bindings.negative_prompt, "negative_prompt", "negative_prompt", "string")
    for index, ref in enumerate(bindings.seed):
        _check_ref(ref, f"seed[{index}]", f"seed[{index}]", "int")
    if bindings.width is not None:
        _check_ref(bindings.width, "width", "width", "int")
    if bindings.height is not None:
        _check_ref(bindings.height, "height", "height", "int")
    if bindings.batch_size is not None:
        _check_ref(bindings.batch_size, "batch_size", "batch_size", "int")
    for index, ref in enumerate(bindings.images):
        _check_ref(ref, f"images[{index}]", f"images[{index}]", "image")
    if bindings.mask is not None and bindings.mask.mode == "load_image_mask":
        assert bindings.mask.node is not None and bindings.mask.input is not None
        _check_ref(
            InputRef(node=bindings.mask.node, input=bindings.mask.input), "mask", "mask", "image"
        )

    if bindings.bounding_boxes is not None:
        _check_bounding_boxes(bindings.bounding_boxes)

    for node_id in bindings.outputs:
        if not graph.has(node_id):
            _error(
                "output_missing",
                t("comfyui.workflow.outputNodeMissing", node=node_id),
                "outputs",
                node_id,
            )
            continue
        if not graph.generated_image_reaches(node_id):
            _error(
                "output_no_image",
                t("comfyui.validation.outputNoGeneratedImage", node=node_id),
                "outputs",
                node_id,
            )
            continue
        if graph.is_display_node(node_id):
            _warn(
                "display_only_output",
                t(
                    "comfyui.validation.displayOnlyOutput",
                    node=node_id,
                    class_type=graph.class_type(node_id) or "?",
                ),
                "outputs",
                node_id,
            )
    # 最終プロンプトのノードは種類で絞らない。存在だけを確かめる(ADR-0030 1章)。
    if bindings.final_prompt is not None and not graph.has(bindings.final_prompt):
        _error(
            "final_prompt_missing",
            t("comfyui.workflow.finalPromptNodeMissing", node=bindings.final_prompt),
            "final_prompt",
            bindings.final_prompt,
        )

    if operation == "edit" and not bindings.images:
        _error("edit_requires_image", t("comfyui.workflow.editRequiresImage"), "images")
    # generate は入力画像を取らない(ADR-0009)。差し込み先があると requires_mask などが
    # capabilities に出てしまい、どの入力でも実行できないワークフローになる。
    if operation == "generate" and (bindings.images or bindings.mask is not None):
        _error("generate_has_image", t("comfyui.workflow.generateCannotHaveImageOrMask"), "images")

    # image_alpha は枠1の LoadImage の MASK を部分編集に使う形。透過を保つためだけに使う
    # MASK(`JoinImageWithAlpha.alpha` など)なら、塗った範囲が透明な画像として渡ってしまう。
    if bindings.mask is not None and bindings.mask.mode == "image_alpha" and bindings.images:
        first = bindings.images[0].node
        if graph.has(first):
            inpaint, _other = graph.mask_uses(first, 1, live)
            if not inpaint:
                _warn(
                    "mask_not_inpaint",
                    t("comfyui.validation.maskNotInpaint", node=first),
                    "mask",
                    first,
                )

    seen_names: set[str] = set()
    for param in exposed_params:
        field = f"exposed_params.{param.name}"
        if param.name in seen_names:
            _error(
                "exposed_param_duplicate",
                t("comfyui.workflow.exposedParamDuplicate", name=param.name),
                field,
            )
            continue
        seen_names.add(param.name)
        _check_ref(
            InputRef(node=param.node, input=param.input),
            t("comfyui.workflow.exposedParamLabel", name=param.name),
            field,
            _param_kind(param.type),
        )

    return issues


def _type_ok(graph: WorkflowGraph, ref: InputRef, value: Any, kind: _ValueKind) -> bool:
    """値の種類が合うか。`/object_info` で入力の型が分かれば、それも合わせて見る。"""
    if not _value_matches_kind(value, kind):
        return False
    declared = graph.input_type(ref.node, ref.input)
    if declared is None or kind == "enum":
        return True
    if kind == "string":
        return declared in ("STRING", "*")
    if kind == "image":
        # 画像の枠(LoadImage.image)は選択肢(COMBO)で宣言される。
        return declared in ("STRING", "COMBO", "*")
    return declared in _KIND_TYPES[kind] or declared == "*"


def validate_workflow(
    template: dict[str, Any],
    operation: Literal["generate", "edit"],
    bindings: Bindings,
    exposed_params: list[ExposedParam],
    object_info: dict[str, Any] | None = None,
) -> list[ValidationIssue]:
    """保存(POST/PATCH)前の検証。エラーがあれば `WorkflowValidationError` を投げ(422)、
    無ければ警告の一覧を返す(保存は通す。ADR-0052 3章)。

    実行時(`ComfyUIProvider`)は呼ばない。登録済みのワークフローを新しい検証で止めないため。
    """
    if is_ui_format_template(template):
        raise WorkflowValidationError(ui_format_message())
    if not is_api_format_template(template):
        raise WorkflowValidationError(not_api_format_message())
    issues = check_workflow(template, operation, bindings, exposed_params, object_info)
    errors = [issue for issue in issues if issue.level == "error"]
    if errors:
        raise WorkflowValidationError("\n".join(issue.message for issue in errors), errors)
    return [issue for issue in issues if issue.level == "warning"]


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
        **_regions_capabilities(bindings),
        output_size=_output_size_rule(template, bindings, exposed),
    )

    return ModelCapabilities(
        model=str(wf.id),
        label=wf.name,
        quality_choices=[],
        operations=[op_caps],
    )


def _regions_capabilities(bindings: Bindings) -> dict[str, Any]:
    """四角の差し込み先を持つワークフローの、領域の指示の capabilities(ADR-0053 6章)。

    選べる種類は「変える」「足す」だけ、四角ごとの参照画像は使えない(ノードに入口が無い)。入力画像の
    無い Run でも使える(出力の大きさの白紙の上に描く)。差し込み先が無ければ何も足さない(既定の
    「受けない」のまま)。
    """
    if bindings.bounding_boxes is None:
        return {}
    return {
        "supports_regions": True,
        "regions_delivery": "comfyui_bboxes",
        "regions_kinds": list(comfy_bounding_boxes.REGION_KINDS),
        "regions_reference_images": False,
        "regions_requires_input_image": False,
    }


def _output_size_rule(
    template: dict[str, Any], bindings: Bindings, exposed: list[ExposedParam]
) -> OutputSizeRule | None:
    """四角の差し込み先のノードの幅・高さの決まり方(白紙のキャンバスの大きさ。ADR-0053 6章)。

    幅・高さの束縛が両方あれば、その項目の値(出力の大きさそのもの)。無ければ差し込み先のノードの
    `width` / `height` を辿る。フォームの項目(公開パラメーター)が差し込む入力は、その項目の値として
    表す。差し込み先が無いか、決められなければ None(フォームは今までどおり出力サイズの設定から
    決める)。
    """
    if bindings.bounding_boxes is None:
        return None
    if bindings.width is not None and bindings.height is not None:
        return OutputSizeRule(
            width=OutputDimension(source="int", value=OutputSizeOperand(param="width")),
            height=OutputDimension(source="int", value=OutputSizeOperand(param="height")),
        )
    targets: dict[tuple[str, str], str] = {}
    if bindings.width is not None:
        targets[(bindings.width.node, bindings.width.input)] = "width"
    if bindings.height is not None:
        targets[(bindings.height.node, bindings.height.input)] = "height"
    for exposed_param in exposed:
        targets[(exposed_param.node, exposed_param.input)] = exposed_param.name
    return comfy_output_size.describe_size(template, bindings.bounding_boxes.node, targets)


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
    bounding_boxes: str | None = None,
) -> dict[str, Any]:
    """template を書き換えず、値を差し込んだグラフ全体を新しく返す(決定的)。

    `bounding_boxes` は四角の要素の一覧の JSON の文字列(`comfy_bounding_boxes.encode`)。None
    (四角の無い Run)なら差し込まず、テンプレートのキャンバスの値をそのまま使う(ADR-0053 6章)。
    """
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

    # 四角は最後に差し込む。キャンバスの値(`editor_state`)のピクセルは差し込み先のノードの幅・高さ
    # で決まり、それは公開パラメーター(幅・高さや Resolution Selector の aspect_ratio など)で
    # 変わりうるため。差し込み先の `bboxes` などを公開パラメーターにしていても、四角が勝つ。
    if bindings.bounding_boxes is not None and bounding_boxes is not None:
        comfy_bounding_boxes.inject(graph, bindings.bounding_boxes.node, bounding_boxes)

    return graph
