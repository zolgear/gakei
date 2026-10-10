"""`POST /api/runs` のサーバー側検証。capabilities に定義したパラメータ・サイズ・
入力画像の制約をここでまとめて確認する。"""

from __future__ import annotations

from typing import Any

from app.domain import sizes
from app.i18n import t
from app.providers.base import OperationName, ParamDef, ProviderCapabilities, RunInputMeta

# ADR-0013 で `RunInputMeta` は `app/providers/base.py` に移した(provider 層からも
# 参照できるようにするため)。ここでは既存の import 元を壊さないよう再エクスポートする。
__all__ = ["RunInputMeta", "RunValidationError", "validate_run_request"]


class RunValidationError(ValueError):
    """検証失敗。API 層で 422 に変換する。"""


def _validate_param_value(pdef: ParamDef, value: Any) -> None:
    if pdef.type == "enum":
        if not isinstance(value, str) or (pdef.choices is not None and value not in pdef.choices):
            raise RunValidationError(
                t("runValidation.enumInvalid", name=pdef.name, choices=pdef.choices)
            )
    elif pdef.type == "int":
        if not isinstance(value, int) or isinstance(value, bool):
            raise RunValidationError(t("runValidation.intRequired", name=pdef.name))
        if pdef.minimum is not None and value < pdef.minimum:
            raise RunValidationError(
                t("runValidation.minRequired", name=pdef.name, minimum=pdef.minimum)
            )
        if pdef.maximum is not None and value > pdef.maximum:
            raise RunValidationError(
                t("runValidation.maxRequired", name=pdef.name, maximum=pdef.maximum)
            )
    elif pdef.type == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RunValidationError(t("runValidation.floatRequired", name=pdef.name))
        if pdef.minimum is not None and value < pdef.minimum:
            raise RunValidationError(
                t("runValidation.minRequired", name=pdef.name, minimum=pdef.minimum)
            )
        if pdef.maximum is not None and value > pdef.maximum:
            raise RunValidationError(
                t("runValidation.maxRequired", name=pdef.name, maximum=pdef.maximum)
            )
    elif pdef.type == "bool":
        if not isinstance(value, bool):
            raise RunValidationError(t("runValidation.boolRequired", name=pdef.name))
    elif pdef.type == "text":
        if not isinstance(value, str):
            raise RunValidationError(t("runValidation.textRequired", name=pdef.name))
        if pdef.max_length is not None and len(value) > pdef.max_length:
            raise RunValidationError(
                t("runValidation.maxLength", name=pdef.name, maxLength=pdef.max_length)
            )


def validate_run_request(
    caps: ProviderCapabilities,
    operation: OperationName,
    model: str,
    prompt: str,
    params: dict[str, Any],
    inputs: list[RunInputMeta],
) -> None:
    if not prompt or len(prompt) > caps.prompt_max_length:
        raise RunValidationError(t("runValidation.promptLength", max=caps.prompt_max_length))

    model_caps = next((m for m in caps.models if m.model == model), None)
    if model_caps is None:
        raise RunValidationError(t("runValidation.unknownModel", model=model))

    op_caps = next((o for o in model_caps.operations if o.operation == operation), None)
    if op_caps is None:
        raise RunValidationError(
            t("runValidation.operationNotSupported", model=model, operation=operation)
        )

    param_defs = {p.name: p for p in op_caps.params}

    for key, value in params.items():
        if key == "size":
            # size パラメータを取らないプロバイダー(capabilities().size が None)には
            # 未知のパラメータとして扱う(ADR-0013)。
            if caps.size is None:
                raise RunValidationError(t("runValidation.unknownParamSize"))
            continue  # サイズの妥当性は別途 sizes.parse_size で検証する
        pdef = param_defs.get(key)
        if pdef is None:
            raise RunValidationError(t("runValidation.unknownParam", key=key))
        _validate_param_value(pdef, value)

    if "size" in params:
        # 制約はプロバイダーの capabilities のもの(OpenAI は sizes の既定と同じ値。
        # SD WebUI は 8 の倍数・長辺 2048px。ADR-0038 2章)。
        try:
            parsed_size = sizes.parse_size(str(params["size"]), caps.size)
        except sizes.InvalidSizeError as e:
            raise RunValidationError(str(e)) from e
        if parsed_size is None and caps.size is not None and not caps.size.allow_auto:
            raise RunValidationError(t("sizes.autoNotAllowed"))

    for pair in caps.incompatible_pairs:
        if params.get(pair.field_a) == pair.value_a and params.get(pair.field_b) == pair.value_b:
            raise RunValidationError(
                t(
                    "runValidation.incompatiblePair",
                    fieldA=pair.field_a,
                    valueA=pair.value_a,
                    fieldB=pair.field_b,
                    valueB=pair.value_b,
                )
            )

    for cond in caps.conditional_params:
        if cond.field in params:
            current = params.get(cond.depends_on_field)
            if current is None:
                pdef = param_defs.get(cond.depends_on_field)
                current = pdef.default if pdef else None
            if current not in cond.depends_on_values:
                raise RunValidationError(
                    t(
                        "runValidation.conditionalParam",
                        field=cond.field,
                        dependsOnField=cond.depends_on_field,
                        dependsOnValues=cond.depends_on_values,
                    )
                )

    image_inputs = [i for i in inputs if i.role == "image"]
    mask_inputs = [i for i in inputs if i.role == "mask"]

    if operation == "generate":
        if inputs:
            raise RunValidationError(t("runValidation.generateNoInputImages"))
        return

    # edit
    count = len(image_inputs)
    if not (op_caps.min_input_images <= count <= op_caps.max_input_images):
        if op_caps.min_input_images == op_caps.max_input_images:
            raise RunValidationError(
                t(
                    "runValidation.exactImageCount",
                    count=op_caps.min_input_images,
                    current=count,
                )
            )
        raise RunValidationError(
            t(
                "runValidation.imageCountRange",
                min=op_caps.min_input_images,
                max=op_caps.max_input_images,
            )
        )
    if len(mask_inputs) > 1:
        raise RunValidationError(t("runValidation.maskAtMostOne"))
    # マスクの差し込み先が無いワークフロー(ComfyUI)で黙って捨てないよう、受け付けない。
    if mask_inputs and not op_caps.supports_mask:
        raise RunValidationError(t("runValidation.maskNotSupported"))
    if op_caps.requires_mask and not mask_inputs:
        raise RunValidationError(t("runValidation.maskRequired"))

    if mask_inputs:
        position0 = next((i for i in image_inputs if i.position == 0), None)
        if position0 is None:
            raise RunValidationError(t("runValidation.maskRequiresPosition0"))
        mask = mask_inputs[0]
        if (mask.width, mask.height) != (position0.width, position0.height):
            raise RunValidationError(t("runValidation.maskSizeMismatch"))
    else:
        # マスクの無い Run では、マスクがあるときだけ意味を持つ項目を受け付けない(黙って捨てない)。
        for key in params:
            pdef = param_defs.get(key)
            if pdef is not None and pdef.mask_only:
                raise RunValidationError(t("runValidation.maskOnlyParam", key=key))
