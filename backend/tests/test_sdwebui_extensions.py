"""SD WebUI の拡張機能(Dynamic Prompts)への対応のテスト(ADR-0038 7章)。偽の WebUI だけを使う。

スクリプト名は架空の版(`dynamic prompts v9.9.9`)。
"""

from __future__ import annotations

import asyncio
import io
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.orm import sessionmaker

from app.domain.run_validation import RunValidationError
from app.providers.base import RunDraft, RunRequest
from app.providers.sdwebui.client import ScriptArg, ScriptInfo, SdWebuiClient, parse_scripts
from app.providers.sdwebui.extensions import (
    COMBINATORIAL_MAX_OUTPUTS,
    resolve_extensions,
)
from app.providers.sdwebui.provider import SdWebuiProvider
from tests.conftest import wait_for_run_terminal
from tests.sdwebui_fake import (
    DYNAMIC_PROMPTS_ARGS,
    DYNAMIC_PROMPTS_NAME,
    MAX_GENERATIONS_LABEL,
    FakeSdWebui,
    install_fake_factories,
)

URL = "http://127.0.0.1:7860"
DP_PARAMS = {"dynamic_prompts", "dynamic_prompts_combinatorial"}


def _provider(fake: FakeSdWebui, session_factory: sessionmaker) -> SdWebuiProvider:
    return SdWebuiProvider(
        URL,
        session_factory,
        30.0,
        credentials_loader=lambda: None,
        transport=fake.transport(),
        progress_interval=0.01,
    )


def _dp_fake(args: list[tuple[str, Any]] | None = None) -> FakeSdWebui:
    fake = FakeSdWebui()
    fake.enable_dynamic_prompts(args=args)
    return fake


def _draft(prompt: str = "a {red|blue|green} cat", **params: Any) -> RunDraft:
    return RunDraft(operation="generate", model="model-a", prompt=prompt, params=params, inputs=[])


def _request(params: dict[str, Any], prompt: str = "a {red|blue|green} cat") -> RunRequest:
    return RunRequest(
        run_id=uuid.uuid4(), operation="generate", model="model-a", prompt=prompt, params=params
    )


async def _noop(_event: Any) -> None:
    return None


def _finalize(
    provider: SdWebuiProvider, session_factory: sessionmaker, draft: RunDraft
) -> dict[str, Any]:
    with session_factory() as db:
        return provider.finalize_params(db, draft)


def _labelled(args: list[Any], labels: list[tuple[str, Any]] = DYNAMIC_PROMPTS_ARGS) -> dict:
    return {label: value for (label, _default), value in zip(labels, args, strict=True)}


# -- 一覧の読み取りと解決 ----------------------------------------------------------------


def test_parse_scripts_pairs_names_with_script_info() -> None:
    fake = _dp_fake()
    catalog = SdWebuiClient(URL, transport=fake.transport()).fetch_catalog()
    names = [(s.name, s.is_img2img) for s in catalog.scripts]
    assert names == [(DYNAMIC_PROMPTS_NAME, False), (DYNAMIC_PROMPTS_NAME, True)]
    txt2img = catalog.scripts[0]
    assert txt2img.is_alwayson is True
    assert [a.label for a in txt2img.args] == [label for label, _ in DYNAMIC_PROMPTS_ARGS]
    assert txt2img.args[0].value is True


def test_parse_scripts_ignores_entries_not_listed_or_malformed() -> None:
    info = [
        {"name": "dynamic prompts v9.9.9", "is_img2img": False, "args": []},
        {"name": "not listed", "is_img2img": False, "args": []},
        {"name": "no args", "is_img2img": False},
        "garbage",
    ]
    scripts = {"txt2img": ["dynamic prompts v9.9.9", "no args"], "img2img": []}
    assert [s.name for s in parse_scripts(scripts, info)] == ["dynamic prompts v9.9.9"]
    assert parse_scripts(None, info) == []
    assert parse_scripts(scripts, {"detail": "x"}) == []


def test_resolve_requires_all_labels() -> None:
    full = ScriptInfo(
        name="Dynamic Prompts v9.9.9",  # 大文字でも判定する
        is_img2img=False,
        is_alwayson=True,
        args=tuple(ScriptArg(label, value) for label, value in DYNAMIC_PROMPTS_ARGS),
    )
    assert [r.adapter.key for r in resolve_extensions([full])] == ["dynamic_prompts"]
    # img2img 用だけでは txt2img には使わない
    img2img = ScriptInfo(full.name, True, True, full.args)
    assert resolve_extensions([img2img]) == []
    # 名前が違うものは対象外
    other = ScriptInfo("prompts dynamic", False, True, full.args)
    assert resolve_extensions([other]) == []
    # 必要な label(Jinja2)が無い版は対応外
    missing = ScriptInfo(
        full.name,
        False,
        True,
        tuple(a for a in full.args if a.label != "Enable Jinja2 templates"),
    )
    assert resolve_extensions([missing]) == []


# -- capabilities ----------------------------------------------------------------------


def _param_names(provider: SdWebuiProvider) -> set[str]:
    caps = provider.capabilities()
    return {p.name for p in caps.models[0].operations[0].params}


def test_capabilities_show_dynamic_prompts_when_available(
    db_session_factory: sessionmaker,
) -> None:
    provider = _provider(_dp_fake(), db_session_factory)
    caps = provider.capabilities()
    params = {p.name: p for p in caps.models[0].operations[0].params}
    assert DP_PARAMS <= set(params)
    assert params["dynamic_prompts"].type == "bool"
    assert params["dynamic_prompts"].default is True
    assert params["dynamic_prompts"].form_default is True
    assert params["dynamic_prompts_combinatorial"].default is False
    assert params["dynamic_prompts"].label == "Dynamic Prompts"
    assert "{red|blue|green}" in params["dynamic_prompts"].description
    assert f"上限 {COMBINATORIAL_MAX_OUTPUTS} 枚" in (
        params["dynamic_prompts_combinatorial"].description
    )


def test_capabilities_hide_dynamic_prompts_when_absent(db_session_factory: sessionmaker) -> None:
    assert not DP_PARAMS & _param_names(_provider(FakeSdWebui(), db_session_factory))


def test_capabilities_hide_dynamic_prompts_for_unsupported_version(
    db_session_factory: sessionmaker,
) -> None:
    args = [(label, v) for label, v in DYNAMIC_PROMPTS_ARGS if label != "Magic prompt"]
    assert not DP_PARAMS & _param_names(_provider(_dp_fake(args), db_session_factory))


def test_script_info_failure_does_not_stop_generation(db_session_factory: sessionmaker) -> None:
    fake = _dp_fake()
    fake.scripts_status = 500
    provider = _provider(fake, db_session_factory)
    caps = provider.capabilities()
    assert [m.model for m in caps.models] == ["model-a", "model-b"]
    assert not DP_PARAMS & _param_names(provider)

    params = _finalize(provider, db_session_factory, _draft())
    assert "alwayson_scripts" not in params["sdwebui_request"]
    result = asyncio.run(provider.execute(_request(params), _noop))
    assert len(result.outputs) == 1


# -- finalize_params -------------------------------------------------------------------


def test_finalize_always_records_alwayson_scripts(db_session_factory: sessionmaker) -> None:
    provider = _provider(_dp_fake(), db_session_factory)
    params = _finalize(provider, db_session_factory, _draft())
    # 利用者の値は変えない(既定を足さない)
    assert not DP_PARAMS & set(params)
    scripts = params["sdwebui_request"]["alwayson_scripts"]
    assert list(scripts) == [DYNAMIC_PROMPTS_NAME]
    args = scripts[DYNAMIC_PROMPTS_NAME]["args"]
    assert len(args) == len(DYNAMIC_PROMPTS_ARGS)
    values = _labelled(args)
    assert values["Dynamic Prompts enabled"] is True
    assert values["Combinatorial generation"] is False
    assert values[MAX_GENERATIONS_LABEL] == 0
    # 固定で無効にするもの
    for label in (
        "Magic prompt",
        "I'm feeling lucky",
        "Attention grabber",
        "Enable Jinja2 templates",
        "Don't generate images",
    ):
        assert values[label] is False, label
    # それ以外は script-info の既定値のまま
    defaults = dict(DYNAMIC_PROMPTS_ARGS)
    for label in (
        "Combinatorial batches",
        "Minimum attention",
        "Max magic prompt length",
        "Don't apply to negative prompts",
        "Magic prompt model",
        "Magic prompt blocklist regex",
    ):
        assert values[label] == defaults[label], label


def test_finalize_overrides_script_info_defaults(db_session_factory: sessionmaker) -> None:
    # WebUI の画面の既定値が「Magic prompt 有効・Dynamic Prompts 無効」でも、GAKEI の値で送る
    args = [
        (label, True if label in ("Magic prompt", "Attention grabber") else value)
        for label, value in DYNAMIC_PROMPTS_ARGS
    ]
    args[0] = ("Dynamic Prompts enabled", False)
    provider = _provider(_dp_fake(args), db_session_factory)
    params = _finalize(provider, db_session_factory, _draft())
    values = _labelled(params["sdwebui_request"]["alwayson_scripts"][DYNAMIC_PROMPTS_NAME]["args"])
    assert values["Dynamic Prompts enabled"] is True
    assert values["Magic prompt"] is False
    assert values["Attention grabber"] is False


def test_finalize_combinatorial_sets_max_generations(db_session_factory: sessionmaker) -> None:
    provider = _provider(_dp_fake(), db_session_factory)
    params = _finalize(
        provider,
        db_session_factory,
        _draft(dynamic_prompts_combinatorial=True),
    )
    assert params["dynamic_prompts_combinatorial"] is True
    values = _labelled(params["sdwebui_request"]["alwayson_scripts"][DYNAMIC_PROMPTS_NAME]["args"])
    assert values["Combinatorial generation"] is True
    assert values[MAX_GENERATIONS_LABEL] == COMBINATORIAL_MAX_OUTPUTS


def test_finalize_disabled(db_session_factory: sessionmaker) -> None:
    provider = _provider(_dp_fake(), db_session_factory)
    params = _finalize(provider, db_session_factory, _draft(dynamic_prompts=False))
    values = _labelled(params["sdwebui_request"]["alwayson_scripts"][DYNAMIC_PROMPTS_NAME]["args"])
    assert values["Dynamic Prompts enabled"] is False


def test_finalize_uses_labels_not_positions(db_session_factory: sessionmaker) -> None:
    # 版が変わって並びと数が変わっても、label で差し替える
    shuffled = list(reversed(DYNAMIC_PROMPTS_ARGS))
    shuffled.insert(3, ("Some new option", "keep-me"))
    shuffled = [
        (MAX_GENERATIONS_LABEL.replace("(0 = all", "(zero = all"), v)
        if label == MAX_GENERATIONS_LABEL
        else (label, v)
        for label, v in shuffled
    ]
    provider = _provider(_dp_fake(shuffled), db_session_factory)
    params = _finalize(provider, db_session_factory, _draft(dynamic_prompts_combinatorial=True))
    args = params["sdwebui_request"]["alwayson_scripts"][DYNAMIC_PROMPTS_NAME]["args"]
    values = _labelled(args, shuffled)
    assert values["Some new option"] == "keep-me"
    assert values["Dynamic Prompts enabled"] is True
    assert values["Combinatorial generation"] is True
    max_gen_label = next(label for label, _ in shuffled if label.startswith("Max generations"))
    assert values[max_gen_label] == COMBINATORIAL_MAX_OUTPUTS
    assert values["Max magic prompt length"] == 100


def test_finalize_rejects_extension_params_without_extension(
    db_session_factory: sessionmaker,
) -> None:
    provider = _provider(FakeSdWebui(), db_session_factory)
    with pytest.raises(RunValidationError):
        _finalize(provider, db_session_factory, _draft(dynamic_prompts=True))


# -- execute ---------------------------------------------------------------------------


def test_execute_records_expanded_prompts(db_session_factory: sessionmaker) -> None:
    fake = _dp_fake()
    provider = _provider(fake, db_session_factory)
    params = _finalize(provider, db_session_factory, _draft(n=2, negative_prompt="blurry"))
    result = asyncio.run(provider.execute(_request(params), _noop))

    assert len(result.outputs) == 2
    assert fake.txt2img_bodies == [params["sdwebui_request"]]
    usage = result.usage or {}
    assert usage["all_prompts"] == ["a red cat", "a blue cat"]
    assert usage["all_negative_prompts"] == ["blurry", "blurry"]
    assert "output_limit" not in usage
    assert result.text_outputs == [
        {"role": "final_prompt", "output_index": 0, "text": "a red cat"},
        {"role": "final_prompt", "output_index": 1, "text": "a blue cat"},
        {"role": "final_negative_prompt", "output_index": 0, "text": "blurry"},
        {"role": "final_negative_prompt", "output_index": 1, "text": "blurry"},
    ]


def test_execute_combinatorial_ignores_batch_size(db_session_factory: sessionmaker) -> None:
    fake = _dp_fake()
    provider = _provider(fake, db_session_factory)
    params = _finalize(
        provider, db_session_factory, _draft(n=1, dynamic_prompts_combinatorial=True)
    )
    result = asyncio.run(provider.execute(_request(params), _noop))
    assert len(result.outputs) == 3
    usage = result.usage or {}
    assert usage["all_prompts"] == ["a red cat", "a blue cat", "a green cat"]
    assert usage["output_limit"] == COMBINATORIAL_MAX_OUTPUTS
    assert "discarded_outputs" not in usage
    assert len(usage["all_seeds"]) == 3


def test_execute_combinatorial_caps_outputs(db_session_factory: sessionmaker) -> None:
    fake = _dp_fake()
    fake.extra_images = 1  # 補助の画像も混ざる
    provider = _provider(fake, db_session_factory)
    prompt = "{a|b|c|d|e|f} {1|2|3|4|5|6}"  # 36 通り
    params = _finalize(
        provider, db_session_factory, _draft(prompt, dynamic_prompts_combinatorial=True)
    )
    # WebUI が上限を守らなかった場合(Max generations を 0 にして送る)でも、32 枚までにする
    request = params["sdwebui_request"]
    args = request["alwayson_scripts"][DYNAMIC_PROMPTS_NAME]["args"]
    args[[label for label, _ in DYNAMIC_PROMPTS_ARGS].index(MAX_GENERATIONS_LABEL)] = 0

    result = asyncio.run(provider.execute(_request(params, prompt), _noop))
    assert len(result.outputs) == COMBINATORIAL_MAX_OUTPUTS
    usage = result.usage or {}
    assert usage["discarded_outputs"] == 36 - COMBINATORIAL_MAX_OUTPUTS
    assert len(usage["all_prompts"]) == COMBINATORIAL_MAX_OUTPUTS
    assert len(usage["all_seeds"]) == COMBINATORIAL_MAX_OUTPUTS


def test_execute_skips_leading_grid(db_session_factory: sessionmaker) -> None:
    fake = _dp_fake()
    fake.return_grid = True
    provider = _provider(fake, db_session_factory)
    params = _finalize(provider, db_session_factory, _draft(n=2))
    result = asyncio.run(provider.execute(_request(params), _noop))
    assert len(result.outputs) == 2
    # グリッド(16x16)は取り込まない
    sizes = {Image.open(io.BytesIO(o.data)).size for o in result.outputs}
    assert sizes == {(8, 8)}
    assert (result.usage or {})["infotext"].startswith("a red cat")


def test_execute_without_extension_records_plain_prompts(
    db_session_factory: sessionmaker,
) -> None:
    provider = _provider(FakeSdWebui(), db_session_factory)
    params = _finalize(provider, db_session_factory, _draft("a cat", n=2))
    result = asyncio.run(provider.execute(_request(params, "a cat"), _noop))
    usage = result.usage or {}
    assert usage["all_prompts"] == ["a cat", "a cat"]
    # 空のネガティブプロンプトは text_outputs に入れない
    assert result.text_outputs == [
        {"role": "final_prompt", "output_index": 0, "text": "a cat"},
        {"role": "final_prompt", "output_index": 1, "text": "a cat"},
    ]


# -- 通し(API) ------------------------------------------------------------------------


def test_run_detail_exposes_per_output_prompts(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _dp_fake()
    install_fake_factories(monkeypatch, fake)
    assert client.put("/api/sdwebui/connection", json={"url": URL}).status_code == 200

    response = client.post(
        "/api/runs",
        json={
            "operation": "generate",
            "provider": "sdwebui",
            "model": "model-a",
            "prompt": "a {red|blue|green} cat",
            "params": {"n": 1, "dynamic_prompts_combinatorial": True},
        },
    )
    assert response.status_code == 202, response.text
    detail = wait_for_run_terminal(client, response.json()["id"])
    assert detail["status"] == "succeeded", detail
    assert len(detail["outputs"]) == 3
    finals = {
        item["output_index"]: item["text"]
        for item in detail["text_outputs"]
        if item["role"] == "final_prompt"
    }
    assert finals == {0: "a red cat", 1: "a blue cat", 2: "a green cat"}
    assert detail["usage"]["all_prompts"] == ["a red cat", "a blue cat", "a green cat"]

    # Asset の詳細でも、output_index で対応づけられる
    output = next(o for o in detail["outputs"] if o["output_index"] == 2)
    asset = client.get(f"/api/assets/{output['asset_id']}").json()
    assert asset["output_index"] == 2
    produced = asset["produced_by_run"]["text_outputs"]
    assert {"role": "final_prompt", "output_index": 2, "text": "a green cat"}.items() <= next(
        item for item in produced if item["output_index"] == 2 and item["role"] == "final_prompt"
    ).items()


# -- 共有ページと PNG 埋め込み --------------------------------------------------------------


def test_alwayson_scripts_are_not_public() -> None:
    from app.domain.embedded_meta import _EXCLUDED_PARAM_KEYS
    from app.domain.shares import public_params

    params = {
        "steps": 20,
        "dynamic_prompts_combinatorial": True,
        "sdwebui_seed": 1,
        "sdwebui_task_id": "gakei-0123",
        "sdwebui_request": {
            "prompt": "a {red|blue} cat",
            "alwayson_scripts": {DYNAMIC_PROMPTS_NAME: {"args": [True, False]}},
        },
    }
    assert public_params(params, "sdwebui") == {
        "steps": 20,
        "dynamic_prompts_combinatorial": True,
        "sdwebui_seed": 1,
    }
    embedded = {k: v for k, v in params.items() if k not in _EXCLUDED_PARAM_KEYS}
    assert "sdwebui_request" not in embedded
    assert "alwayson_scripts" not in str(embedded)
