"""capabilities の表示名(label/choice_labels)と初期値(form_default/default_model/
default_size)の整合性。フォームを一般的な日本語の見出しで組み立てるための前提を検証する。
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi.testclient import TestClient

from app.domain.sizes import validate_size
from app.providers.base import ParamDef, ProviderCapabilities
from app.providers.openai_spec import MODELS, build_capabilities


def _all_param_defs(caps: ProviderCapabilities) -> Iterator[ParamDef]:
    for model in caps.models:
        for operation in model.operations:
            yield from operation.params


def test_default_model_is_a_known_model() -> None:
    caps = build_capabilities("fake")
    assert caps.default_model in MODELS
    assert caps.default_model in [m.model for m in caps.models]


def test_default_size_is_not_auto_and_passes_validate_size() -> None:
    caps = build_capabilities("fake")
    assert caps.default_size != "auto"
    width_str, height_str = caps.default_size.lower().split("x")
    validate_size(int(width_str), int(height_str))  # 例外が出なければOK


def test_all_models_have_a_label() -> None:
    caps = build_capabilities("fake")
    for model in caps.models:
        assert model.label, f"{model.model} に label がありません"


def test_all_param_defs_have_a_label() -> None:
    caps = build_capabilities("fake")
    for pdef in _all_param_defs(caps):
        assert pdef.label, f"{pdef.name} に label がありません"


def test_enum_choice_labels_keys_match_choices() -> None:
    caps = build_capabilities("fake")
    for pdef in _all_param_defs(caps):
        if pdef.type != "enum":
            continue
        assert pdef.choice_labels is not None, f"{pdef.name} に choice_labels がありません"
        assert pdef.choices is not None
        assert set(pdef.choice_labels.keys()) == set(pdef.choices), pdef.name


def test_form_default_is_within_choices_or_min_max() -> None:
    caps = build_capabilities("fake")
    for pdef in _all_param_defs(caps):
        if pdef.form_default is None:
            continue
        if pdef.type == "enum":
            assert pdef.choices is not None
            assert pdef.form_default in pdef.choices, pdef.name
        elif pdef.type == "int":
            assert isinstance(pdef.form_default, int) and not isinstance(pdef.form_default, bool)
            if pdef.minimum is not None:
                assert pdef.form_default >= pdef.minimum, pdef.name
            if pdef.maximum is not None:
                assert pdef.form_default <= pdef.maximum, pdef.name
        elif pdef.type == "bool":
            assert isinstance(pdef.form_default, bool)


def test_quality_form_default_is_the_cheap_choice() -> None:
    """「安価な設定」の初期値: quality=low(モデル/サイズは capabilities 側で default_*)。"""
    caps = build_capabilities("fake")
    quality_defs = [p for p in _all_param_defs(caps) if p.name == "quality"]
    assert quality_defs
    for pdef in quality_defs:
        assert pdef.form_default == "low"


def test_generate_params_do_not_include_moderation() -> None:
    """moderation はフォームに出さず、設定 MODERATION の値を Run 作成時にサーバーが
    params へ入れる(api/runs.py)。capabilities には出てはいけない。"""
    caps = build_capabilities("fake")
    for model in caps.models:
        for operation in model.operations:
            if operation.operation != "generate":
                continue
            param_names = {p.name for p in operation.params}
            assert "moderation" not in param_names


def test_capabilities_endpoint_exposes_new_fields(client: TestClient) -> None:
    """ADR-0013: `GET /api/capabilities` は provider の一覧を返す形になった。"""
    response = client.get("/api/capabilities")
    assert response.status_code == 200
    body = response.json()

    assert body["default_provider"] == "fake"
    assert len(body["providers"]) == 1
    entry = body["providers"][0]

    assert entry["provider"] == "fake"
    assert entry["label"] == "Fake"
    assert entry["available"] is True
    assert entry["unavailable_reason"] is None
    assert entry["requires_api_key"] is False
    assert entry["supports_pricing"] is True

    assert "default_model" in entry
    assert "default_size" in entry
    assert entry["default_model"] in [m["model"] for m in entry["models"]]

    quality = next(
        p
        for m in entry["models"]
        for op in m["operations"]
        for p in op["params"]
        if p["name"] == "quality"
    )
    assert quality["label"] == "画質"
    assert quality["choice_labels"]["low"] == "低"
    assert quality["form_default"] == "low"
