"""ADR-0013: `validate_run_request` に足した検証(float/text/size=None/requires_mask)。

capabilities は provider ごとに形が変わりうる(特に ComfyUI)ため、実在の provider の
capabilities に頼らず、検証したい形だけを持つ最小限の `ProviderCapabilities` を都度組み立てる。
"""

from __future__ import annotations

import uuid

import pytest

from app.domain.run_validation import RunValidationError, validate_run_request
from app.providers.base import (
    ModelCapabilities,
    OperationCapabilities,
    ParamDef,
    ProviderCapabilities,
    RunInputMeta,
)

FLOAT_PARAM = ParamDef(name="strength", type="float", label="強さ", minimum=0.0, maximum=1.0)
TEXT_PARAM = ParamDef(name="note", type="text", label="メモ", max_length=5)


def _caps_for_generate(params: list[ParamDef], *, size: object = None) -> ProviderCapabilities:
    operation = OperationCapabilities(operation="generate", params=params, max_input_images=0)
    model = ModelCapabilities(
        model="stub-model", label="Stub", quality_choices=[], operations=[operation]
    )
    return ProviderCapabilities(
        provider="stub",
        label="Stub",
        models=[model],
        default_model="stub-model",
        default_size=None,
        size=size,
    )


# -- float ---------------------------------------------------------------


@pytest.mark.parametrize("value", [0.0, 0.5, 1.0, 1])  # 整数値も float パラメータとして許容する
def test_float_param_accepts_int_and_float_within_range(value: float) -> None:
    caps = _caps_for_generate([FLOAT_PARAM])
    validate_run_request(caps, "generate", "stub-model", "p", {"strength": value}, [])


@pytest.mark.parametrize("value", [-0.1, 1.1])
def test_float_param_rejects_out_of_range(value: float) -> None:
    caps = _caps_for_generate([FLOAT_PARAM])
    with pytest.raises(RunValidationError):
        validate_run_request(caps, "generate", "stub-model", "p", {"strength": value}, [])


def test_float_param_rejects_bool() -> None:
    """bool は int のサブクラスなので、明示的に弾く必要がある。"""
    caps = _caps_for_generate([FLOAT_PARAM])
    with pytest.raises(RunValidationError):
        validate_run_request(caps, "generate", "stub-model", "p", {"strength": True}, [])


def test_float_param_rejects_non_numeric_type() -> None:
    caps = _caps_for_generate([FLOAT_PARAM])
    with pytest.raises(RunValidationError):
        validate_run_request(caps, "generate", "stub-model", "p", {"strength": "0.5"}, [])


# -- text ------------------------------------------------------------------


def test_text_param_accepts_within_max_length() -> None:
    caps = _caps_for_generate([TEXT_PARAM])
    validate_run_request(caps, "generate", "stub-model", "p", {"note": "abcde"}, [])


def test_text_param_rejects_over_max_length() -> None:
    caps = _caps_for_generate([TEXT_PARAM])
    with pytest.raises(RunValidationError):
        validate_run_request(caps, "generate", "stub-model", "p", {"note": "abcdef"}, [])


def test_text_param_rejects_non_string_type() -> None:
    caps = _caps_for_generate([TEXT_PARAM])
    with pytest.raises(RunValidationError):
        validate_run_request(caps, "generate", "stub-model", "p", {"note": 123}, [])


# -- size = None -------------------------------------------------------------


def test_size_param_rejected_when_capabilities_size_is_none() -> None:
    caps = _caps_for_generate([], size=None)
    with pytest.raises(RunValidationError):
        validate_run_request(caps, "generate", "stub-model", "p", {"size": "1024x1024"}, [])


# -- requires_mask -----------------------------------------------------------


def _caps_for_edit_requiring_mask() -> ProviderCapabilities:
    operation = OperationCapabilities(
        operation="edit", params=[], max_input_images=1, supports_mask=True, requires_mask=True
    )
    model = ModelCapabilities(
        model="stub-model", label="Stub", quality_choices=[], operations=[operation]
    )
    return ProviderCapabilities(
        provider="stub",
        label="Stub",
        models=[model],
        default_model="stub-model",
        default_size=None,
        size=None,
    )


def test_requires_mask_without_mask_input_is_rejected() -> None:
    caps = _caps_for_edit_requiring_mask()
    image_input = RunInputMeta(
        asset_id=uuid.uuid4(),
        role="image",
        position=0,
        width=64,
        height=64,
        sha256="a" * 64,
        mime="image/png",
    )
    with pytest.raises(RunValidationError):
        validate_run_request(caps, "edit", "stub-model", "p", {}, [image_input])


def test_requires_mask_with_matching_mask_input_is_accepted() -> None:
    caps = _caps_for_edit_requiring_mask()
    image_input = RunInputMeta(
        asset_id=uuid.uuid4(),
        role="image",
        position=0,
        width=64,
        height=64,
        sha256="a" * 64,
        mime="image/png",
    )
    mask_input = RunInputMeta(
        asset_id=uuid.uuid4(),
        role="mask",
        position=0,
        width=64,
        height=64,
        sha256="b" * 64,
        mime="image/png",
    )
    validate_run_request(caps, "edit", "stub-model", "p", {}, [image_input, mask_input])


def test_mask_rejected_when_model_does_not_support_mask() -> None:
    """マスクの差し込み先が無いワークフローに mask を渡しても、黙って捨てずに断る。"""
    operation = OperationCapabilities(
        operation="edit", params=[], max_input_images=1, supports_mask=False
    )
    model = ModelCapabilities(
        model="stub-model", label="Stub", quality_choices=[], operations=[operation]
    )
    caps = ProviderCapabilities(
        provider="stub",
        label="Stub",
        models=[model],
        default_model="stub-model",
        default_size=None,
        size=None,
    )
    image_input = RunInputMeta(
        asset_id=uuid.uuid4(),
        role="image",
        position=0,
        width=64,
        height=64,
        sha256="a" * 64,
        mime="image/png",
    )
    mask_input = RunInputMeta(
        asset_id=uuid.uuid4(),
        role="mask",
        position=0,
        width=64,
        height=64,
        sha256="b" * 64,
        mime="image/png",
    )
    with pytest.raises(RunValidationError, match="マスクに対応していません"):
        validate_run_request(caps, "edit", "stub-model", "p", {}, [image_input, mask_input])


# -- min_input_images / max_input_images (ADR-0013: ComfyUI の画像の枠) ----------------


def _image_input(position: int) -> RunInputMeta:
    return RunInputMeta(
        asset_id=uuid.uuid4(),
        role="image",
        position=position,
        width=64,
        height=64,
        sha256=f"{position}" * 64,
        mime="image/png",
    )


def _caps_requiring_exact_slots(slot_count: int) -> ProviderCapabilities:
    """ComfyUI ワークフローの「枠はすべて必須」(min == max)を模した capabilities。"""
    operation = OperationCapabilities(
        operation="edit",
        params=[],
        max_input_images=slot_count,
        min_input_images=slot_count,
    )
    model = ModelCapabilities(
        model="stub-model", label="Stub", quality_choices=[], operations=[operation]
    )
    return ProviderCapabilities(
        provider="stub",
        label="Stub",
        models=[model],
        default_model="stub-model",
        default_size=None,
        size=None,
    )


def test_exact_slot_count_accepts_matching_input_count() -> None:
    caps = _caps_requiring_exact_slots(2)
    inputs = [_image_input(0), _image_input(1)]
    validate_run_request(caps, "edit", "stub-model", "p", {}, inputs)


def test_exact_slot_count_rejects_too_few_with_specific_message() -> None:
    caps = _caps_requiring_exact_slots(2)
    with pytest.raises(RunValidationError, match="ちょうど2枚必要です.*現在1枚"):
        validate_run_request(caps, "edit", "stub-model", "p", {}, [_image_input(0)])


def test_exact_slot_count_rejects_too_many_with_specific_message() -> None:
    caps = _caps_requiring_exact_slots(1)
    inputs = [_image_input(0), _image_input(1)]
    with pytest.raises(RunValidationError, match="ちょうど1枚必要です.*現在2枚"):
        validate_run_request(caps, "edit", "stub-model", "p", {}, inputs)


def test_min_max_range_message_when_min_differs_from_max() -> None:
    operation = OperationCapabilities(
        operation="edit", params=[], max_input_images=16, min_input_images=1
    )
    model = ModelCapabilities(
        model="stub-model", label="Stub", quality_choices=[], operations=[operation]
    )
    caps = ProviderCapabilities(
        provider="stub",
        label="Stub",
        models=[model],
        default_model="stub-model",
        default_size=None,
        size=None,
    )
    with pytest.raises(RunValidationError, match="1〜16枚"):
        validate_run_request(caps, "edit", "stub-model", "p", {}, [])
