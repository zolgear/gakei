"""ADR-0052 6章: ComfyUI のワークフローのアシスタントでの推定。

送る要約(文字列を切る・秘密を伏せる・テンプレートを丸ごと出さない)、LLM の応答の解釈と検証
(配線された入力・型違い・存在しないノードなどの提案を捨てる)、`ai.before_send`(目的
`workflow_analysis`)、上限、権限、無効時、Fake の応答を確かめる。実 API は呼ばない(LLM は
`FakeStructuredEngine` と、SDK のクライアントの差し替え)。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.assistant.engine import AssistantEngineError
from app.assistant.structured import (
    FakeStructuredEngine,
    OpenAIStructuredEngine,
    StructuredRequest,
)
from app.domain import comfy_suggest
from app.domain.comfy_analyze import analyze_workflow
from app.domain.llm_connections import LlmTarget
from app.i18n import use_locale
from tests.comfyui_synth_graphs import SYNTH
from tests.conftest import login_as
from tests.test_mods_hooks import _Hook, _install

SUGGEST = "/api/comfyui/workflows/suggest-bindings"
ASSISTANT_SETTINGS = "/api/settings/assistant"

LONG_PROMPT = "a very long prompt about a quiet harbor town at dusk, " * 6


def _w(nid: str, port: int = 0) -> list[Any]:
    return [nid, port]


def _n(class_type: str, title: str | None = None, **inputs: Any) -> dict[str, Any]:
    return {"class_type": class_type, "inputs": inputs, "_meta": {"title": title or class_type}}


def _t2i() -> dict[str, Any]:
    return {
        "4": _n("CheckpointLoaderSimple", ckpt_name="model.safetensors"),
        "6": _n("CLIPTextEncode", "Positive", text=LONG_PROMPT, clip=_w("4", 1)),
        "7": _n("CLIPTextEncode", "Negative", text="blurry", clip=_w("4", 1)),
        "5": _n("EmptyLatentImage", width=512, height=512, batch_size=1),
        "3": _n(
            "KSampler",
            seed=1,
            steps=20,
            cfg=7.0,
            sampler_name="euler",
            scheduler="normal",
            denoise=1.0,
            model=_w("4"),
            positive=_w("6"),
            negative=_w("7"),
            latent_image=_w("5"),
        ),
        "8": _n("VAEDecode", samples=_w("3"), vae=_w("4", 2)),
        "9": _n("SaveImage", images=_w("8"), filename_prefix="ComfyUI"),
        "20": _n("ExternalApiNode", api_key="not-a-real-key-value", note="hello"),
    }


def _edit_alpha_keep() -> dict[str, Any]:
    """LoadImage の MASK を透過を保つためだけに使う(部分編集には使わない)構成。"""
    return {
        "1": _n("LoadImage", image="input.png"),
        "2": _n("CheckpointLoaderSimple", ckpt_name="model.safetensors"),
        "3": _n("CLIPTextEncode", text="make it blue", clip=_w("2", 1)),
        "4": _n("VAEEncode", pixels=_w("1"), vae=_w("2", 2)),
        "5": _n(
            "KSampler",
            seed=5,
            steps=20,
            cfg=5.0,
            sampler_name="euler",
            scheduler="normal",
            denoise=0.6,
            model=_w("2"),
            positive=_w("3"),
            negative=_w("3"),
            latent_image=_w("4"),
        ),
        "6": _n("VAEDecode", samples=_w("5"), vae=_w("2", 2)),
        "7": _n("JoinImageWithAlpha", image=_w("6"), alpha=_w("1", 1)),
        "8": _n("SaveImage", images=_w("7"), filename_prefix="out"),
    }


def _response(**overrides: Any) -> dict[str, Any]:
    """`_t2i` に対する正しい応答。項目を上書きして誤りを混ぜる。"""
    data: dict[str, Any] = {
        "operation": "generate",
        "prompt": {"node": "6", "input": "text"},
        "negative_prompt": {"node": "7", "input": "text"},
        "seed": [{"node": "3", "input": "seed"}],
        "width": {"node": "5", "input": "width"},
        "height": {"node": "5", "input": "height"},
        "batch_size": {"node": "5", "input": "batch_size"},
        "images": [],
        "mask": None,
        "outputs": ["9"],
        "final_prompt": None,
        "reasons": [
            {"field": "prompt", "reason": "Positive のエンコーダーの文"},
            {"field": "operation", "reason": "LoadImage が無い"},
        ],
    }
    data.update(overrides)
    return data


def _interpret(template: dict[str, Any], data: dict[str, Any]) -> comfy_suggest.SuggestOutcome:
    analysis = analyze_workflow(template)
    with use_locale("ja"):
        return comfy_suggest.interpret(
            template, data, analysis.suggested_bindings, analysis.suggested_operation
        )


def _discarded_targets(outcome: comfy_suggest.SuggestOutcome) -> list[str]:
    return [d.target for d in outcome.discarded]


# -- 送る要約 ------------------------------------------------------------------------------


def test_summary_truncates_strings_and_hides_secrets() -> None:
    summary = comfy_suggest.summarize_template(_t2i())
    lines = [json.loads(line) for line in summary.splitlines()]
    by_id = {line["id"]: line for line in lines}
    # 長い文字列は先頭だけ。テンプレートの値が丸ごと出ない。
    text = by_id["6"]["in"]["text"]
    assert text.startswith(LONG_PROMPT[: comfy_suggest.STRING_PREVIEW])
    assert f"全{len(LONG_PROMPT)}文字" in text
    assert LONG_PROMPT not in summary
    assert LONG_PROMPT[: comfy_suggest.STRING_PREVIEW + 1] not in summary
    # 秘密に見える値は伏せる。
    assert by_id["20"]["in"]["api_key"] == "***"
    assert "not-a-real-key-value" not in summary
    assert by_id["20"]["in"]["note"] == "hello"
    # 配線と行き先、独自のタイトル。class_type と同じタイトルは送らない。
    assert by_id["3"]["in"]["positive"] == "@6[0]"
    assert "3.positive<-[0]" in by_id["6"]["to"]
    assert by_id["6"]["title"] == "Positive"
    assert "title" not in by_id["5"]
    # テンプレートの JSON そのもの(`class_type` / `inputs` のキー)ではない。
    assert '"class_type"' not in summary
    assert '"inputs"' not in summary


def test_summary_flattens_nested_inputs_and_redacts_key_shaped_values() -> None:
    template = {
        "1": _n(
            "TextEncodeQwenImageEditPlus",
            images={"image_1": ["2", 0], "image_2": ["3", 0]},
            prompt="x" * 100,
        ),
        "2": _n("LoadImage", image="a.png"),
        "3": _n("LoadImage", image="b.png"),
        "4": _n("Note", text="token sk-proj-" + "A1b2C3d4" * 4),
    }
    lines = {
        json.loads(x)["id"]: json.loads(x)
        for x in comfy_suggest.summarize_template(template).splitlines()
    }
    assert lines["1"]["in"]["images.image_1"] == "@2[0]"
    assert "1.images.image_2<-[0]" in lines["3"]["to"]
    assert lines["4"]["in"]["text"] == "***"


def test_instructions_and_schema() -> None:
    ja = comfy_suggest.instructions("ja")
    en = comfy_suggest.instructions("en")
    assert "日本語で書く" in ja and "英語で書く" in en
    assert "{language}" not in ja
    schema = comfy_suggest.RESPONSE_SCHEMA
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    # 確信度は求めない。公開パラメーターも提案させない。
    assert "confidence" not in json.dumps(schema)
    assert "exposed_params" not in schema["properties"]


# -- 応答の解釈と検証 ---------------------------------------------------------------------------


def test_interpret_accepts_valid_suggestion_and_compares_with_reading() -> None:
    outcome = _interpret(_t2i(), _response())
    assert outcome.discarded == []
    assert outcome.operation == "generate"
    assert outcome.operation_reason == "LoadImage が無い"
    fields = {item.field: item for item in outcome.items}
    assert set(fields) == set(comfy_suggest.FIELDS) - {"images", "mask"}
    assert fields["prompt"].reason == "Positive のエンコーダーの文"
    assert fields["prompt"].matches_reading
    assert outcome.bindings.outputs == ["9"]


def test_interpret_discards_wired_mismatched_and_missing() -> None:
    outcome = _interpret(
        _t2i(),
        _response(
            # 配線された入力(エンコーダーからの配線)。
            prompt={"node": "3", "input": "positive"},
            # 型違い(文字列の入力に seed)と、存在しないノード。
            seed=[{"node": "6", "input": "text"}, {"node": "3", "input": "seed"}],
            width={"node": "99", "input": "width"},
            # 存在しない入力。
            height={"node": "5", "input": "nope"},
            outputs=["9", "77"],
            final_prompt="88",
        ),
    )
    targets = _discarded_targets(outcome)
    assert "prompt" in targets
    assert "seed[0]" in targets
    assert "width" in targets
    assert "height" in targets
    assert "outputs.77" in targets
    assert "final_prompt" in targets
    fields = {item.field for item in outcome.items}
    assert "prompt" not in fields and "width" not in fields and "final_prompt" not in fields
    # 通った値は残る(seed は1つずつ、出力も1つずつ捨てる)。
    assert [(r.node, r.input) for r in outcome.bindings.seed] == [("3", "seed")]
    assert outcome.bindings.outputs == ["9"]
    assert outcome.bindings.prompt is None
    codes = {d.message for d in outcome.discarded}
    assert any("配線" in m for m in codes)


def test_interpret_discards_duplicates_and_bad_shapes() -> None:
    outcome = _interpret(
        _t2i(),
        _response(
            negative_prompt={"node": "6", "input": "text"},
            batch_size={"node": "", "input": "batch_size"},
        ),
    )
    targets = _discarded_targets(outcome)
    assert "negative_prompt" in targets  # prompt と同じ入力
    assert "batch_size" in targets  # 形が合わない
    assert outcome.bindings.prompt is not None


def test_interpret_generate_drops_images_and_mask() -> None:
    outcome = _interpret(
        _t2i(),
        _response(
            images=[{"node": "5", "input": "width"}],
            mask={"mode": "image_alpha", "node": None, "input": None},
        ),
    )
    assert {"images", "mask"} <= set(_discarded_targets(outcome))
    assert outcome.bindings.images == [] and outcome.bindings.mask is None


def test_interpret_image_alpha_requires_inpaint_use() -> None:
    template = _edit_alpha_keep()
    data = {
        "operation": "edit",
        "prompt": {"node": "3", "input": "text"},
        "negative_prompt": None,
        "seed": [{"node": "5", "input": "seed"}],
        "width": None,
        "height": None,
        "batch_size": None,
        "images": [{"node": "1", "input": "image"}],
        "mask": {"mode": "image_alpha", "node": None, "input": None},
        "outputs": ["8"],
        "final_prompt": None,
        "reasons": [],
    }
    outcome = _interpret(template, data)
    assert "mask" in _discarded_targets(outcome)
    assert outcome.bindings.mask is None
    assert [r.node for r in outcome.bindings.images] == ["1"]

    # 部分編集に使う構成(VAEEncodeForInpaint の mask)なら残す。
    inpaint = SYNTH["S2_inpaint_alpha"]
    analysis = analyze_workflow(inpaint)
    good = comfy_suggest.fake_response(analysis)
    assert good["mask"] == {"mode": "image_alpha", "node": None, "input": None}
    kept = _interpret(inpaint, good)
    assert kept.discarded == []
    assert kept.bindings.mask is not None and kept.bindings.mask.mode == "image_alpha"


def test_interpret_wired_image_slot_drops_all_slots_and_alpha_mask() -> None:
    inpaint = SYNTH["S2_inpaint_alpha"]
    data = comfy_suggest.fake_response(analyze_workflow(inpaint))
    data["images"] = [{"node": "3", "input": "pixels"}]
    outcome = _interpret(inpaint, data)
    targets = _discarded_targets(outcome)
    assert "images[0]" in targets
    assert "mask" in targets
    assert outcome.bindings.images == [] and outcome.bindings.mask is None


@pytest.mark.parametrize("name", sorted(SYNTH))
def test_fake_response_passes_validation_on_synth_graphs(name: str) -> None:
    template = SYNTH[name]
    analysis = analyze_workflow(template)
    outcome = _interpret(template, comfy_suggest.fake_response(analysis))
    assert outcome.discarded == []
    assert outcome.operation == analysis.suggested_operation
    assert all(item.matches_reading for item in outcome.items if item.field != "prompt")


# -- エンジン(SDK のクライアントを差し替え) ---------------------------------------------------


class _StubResponses:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.response


def _target(api_style: str = "responses") -> LlmTarget:
    return LlmTarget(
        connection_id="openai", model="test-model", api_key="k", base_url=None, api_style=api_style
    )


def _request() -> StructuredRequest:
    return StructuredRequest(
        target=_target(),
        instructions="i",
        input_text="x",
        schema_name="s",
        schema={"type": "object"},
    )


def test_openai_engine_responses_api_parses_json_and_usage() -> None:
    output = SimpleNamespace(
        type="message", content=[SimpleNamespace(type="output_text", text='{"a": 1}')]
    )
    response = SimpleNamespace(
        output=[output],
        error=None,
        model="test-model-2026",
        usage=SimpleNamespace(
            input_tokens=10,
            output_tokens=5,
            total_tokens=15,
            output_tokens_details=SimpleNamespace(reasoning_tokens=3),
        ),
    )
    stub = _StubResponses(response)
    engine = OpenAIStructuredEngine()
    engine._cache.get = lambda target: SimpleNamespace(responses=stub)  # type: ignore[method-assign]
    result = asyncio.run(engine.complete(_request()))
    assert result.data == {"a": 1}
    assert result.model == "test-model-2026"
    assert result.usage == {
        "input_tokens": 10,
        "output_tokens": 5,
        "reasoning_tokens": 3,
        "total_tokens": 15,
    }
    call = stub.calls[0]
    assert call["text"]["format"]["type"] == "json_schema"
    assert call["text"]["format"]["strict"] is True
    assert call["reasoning"] == {"effort": "low"}
    assert call["store"] is False


def test_openai_engine_refusal_is_an_error() -> None:
    output = SimpleNamespace(
        type="message", content=[SimpleNamespace(type="refusal", refusal="no")]
    )
    stub = _StubResponses(SimpleNamespace(output=[output], error=None, model=None, usage=None))
    engine = OpenAIStructuredEngine()
    engine._cache.get = lambda target: SimpleNamespace(responses=stub)  # type: ignore[method-assign]
    with use_locale("ja"), pytest.raises(AssistantEngineError):
        asyncio.run(engine.complete(_request()))


def test_openai_engine_chat_completions() -> None:
    message = SimpleNamespace(content='{"b": 2}', refusal=None)
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        model="m",
        usage=SimpleNamespace(prompt_tokens=7, completion_tokens=2, total_tokens=9),
    )
    completions = _StubResponses(response)
    engine = OpenAIStructuredEngine()
    engine._cache.get = lambda target: SimpleNamespace(  # type: ignore[method-assign]
        chat=SimpleNamespace(completions=completions)
    )
    request = _request()
    request.target = _target("chat")
    result = asyncio.run(engine.complete(request))
    assert result.data == {"b": 2}
    assert result.usage == {"input_tokens": 7, "output_tokens": 2, "total_tokens": 9}
    call = completions.calls[0]
    assert call["response_format"]["json_schema"]["strict"] is True
    assert call["reasoning_effort"] == "low"


# -- API ----------------------------------------------------------------------------------


def _enable(client: TestClient, **extra: Any) -> None:
    response = client.patch(ASSISTANT_SETTINGS, json={"enabled": True, **extra})
    assert response.status_code == 200, response.text


def _engine(client: TestClient) -> FakeStructuredEngine:
    engine = client.app.state.assistant.structured
    assert isinstance(engine, FakeStructuredEngine)
    return engine


def test_settings_report_structured_outputs_in_fake_mode(client: TestClient) -> None:
    body = client.get(ASSISTANT_SETTINGS).json()
    assert body["structured_outputs"] is True


def test_suggest_with_fake_engine(client: TestClient) -> None:
    _enable(client)
    response = client.post(SUGGEST, json={"template": _t2i(), "name": "t2i"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["suggested_operation"] == "generate"
    assert body["operation_matches_reading"] is True
    assert body["discarded"] == []
    assert {item["field"] for item in body["items"]} >= {"prompt", "seed", "outputs"}
    assert all("confidence" not in item for item in body["items"])
    assert body["suggested_bindings"]["prompt"] == {"node": "6", "input": "text"}
    assert body["usage"]["total_tokens"] == 0
    assert body["model"]
    # 送った要約と指示。テンプレートの値は丸ごと送らない。
    request = _engine(client).requests[-1]
    assert LONG_PROMPT not in request.input_text
    assert "not-a-real-key-value" not in request.input_text
    assert request.reasoning_effort == "low"
    assert request.schema == comfy_suggest.RESPONSE_SCHEMA


def test_suggest_reports_discarded_items(client: TestClient) -> None:
    _enable(client)
    _engine(client).responses.append(_response(prompt={"node": "3", "input": "positive"}))
    body = client.post(SUGGEST, json={"template": _t2i()}).json()
    assert [d["target"] for d in body["discarded"]] == ["prompt"]
    assert "prompt" not in {item["field"] for item in body["items"]}


def test_suggest_with_saved_workflow(client: TestClient) -> None:
    _enable(client)
    template = SYNTH["S1_sd15_t2i"]
    created = client.post(
        "/api/comfyui/workflows",
        json={
            "name": "sd15",
            "operation": "generate",
            "template": template,
            "bindings": {
                "prompt": {"node": "6", "input": "text"},
                "seed": [{"node": "3", "input": "seed"}],
                "outputs": ["9"],
            },
            "exposed_params": [],
        },
    )
    assert created.status_code == 201, created.text
    response = client.post(SUGGEST, json={"workflow_id": created.json()["id"]})
    assert response.status_code == 200, response.text
    assert client.post(SUGGEST, json={}).status_code == 422
    assert (
        client.post(
            SUGGEST, json={"workflow_id": "00000000-0000-0000-0000-000000000000"}
        ).status_code
        == 404
    )


def test_suggest_rejected_when_disabled(client: TestClient) -> None:
    response = client.post(SUGGEST, json={"template": _t2i()})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "disabled"


def test_suggest_rejects_ui_format(client: TestClient) -> None:
    _enable(client)
    response = client.post(SUGGEST, json={"template": {"nodes": [], "links": []}})
    assert response.status_code == 422


def test_suggest_hourly_limit(client: TestClient) -> None:
    _enable(client, hourly_request_limit=1)
    assert client.post(SUGGEST, json={"template": _t2i()}).status_code == 200
    response = client.post(SUGGEST, json={"template": _t2i()})
    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "rate_limited"
    # アシスタントの会話と同じ記録に数える。
    assert client.get(ASSISTANT_SETTINGS).json()["requests_last_hour"] == 1


def test_suggest_llm_error(client: TestClient) -> None:
    _enable(client)
    _engine(client).responses.append(AssistantEngineError("boom"))
    response = client.post(SUGGEST, json={"template": _t2i()})
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["code"] == "llm_error" and "boom" in detail["message"]


def test_suggest_unsupported_connection(client: TestClient) -> None:
    _enable(client)
    created = client.post(
        "/api/settings/llm-connections",
        json={"name": "local", "base_url": "http://127.0.0.1:11434/v1", "api_style": "chat"},
    )
    assert created.status_code == 201, created.text
    _enable(client, connection_id=created.json()["connections"][-1]["id"])

    class _RealLike(FakeStructuredEngine):
        requires_target = True

    client.app.state.assistant.structured = _RealLike()
    response = client.post(SUGGEST, json={"template": _t2i()})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "unsupported"
    assert client.get(ASSISTANT_SETTINGS).json()["structured_outputs"] is False

    # 組み込みの接続先(OpenAI の公式)なら対応とみなす。キーが無ければ接続先の不足。
    _enable(client, connection_id="openai")
    assert client.get(ASSISTANT_SETTINGS).json()["structured_outputs"] is True
    response = client.post(SUGGEST, json={"template": _t2i()})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "connection_missing"


def test_suggest_calls_before_send_and_stops_on_deny(client: TestClient) -> None:
    _enable(client)
    hook = _Hook({"decision": "deny", "reason": "構成は外へ送れません"})
    _install(client, hook, filters={"purposes": ["workflow_analysis"]})
    response = client.post(SUGGEST, json={"template": _t2i(), "name": "my-flow"})
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "hook_denied"
    assert "構成は外へ送れません" in detail["message"]
    # LLM は呼ばない。
    assert _engine(client).requests == []
    subject = hook.events[0]["subject"]
    assert subject["purpose"] == "workflow_analysis"
    roles = {part["role"]: part["text"] for part in subject["text"]}
    assert set(roles) == {"system", "workflow"}
    assert '"id":"6"' in roles["workflow"]
    assert LONG_PROMPT not in roles["workflow"]
    assert subject["params"]["workflow_name"] == "my-flow"
    assert subject["params"]["workflow_id"] is None
    assert subject["params"]["reasoning_effort"] == "low"
    assert subject["target"]["connection"]["id"] == "openai"
    assert subject["images"] == []


def test_suggest_allowed_by_hook(client: TestClient) -> None:
    _enable(client)
    hook = _Hook({"decision": "allow"})
    _install(client, hook)
    response = client.post(SUGGEST, json={"template": _t2i()})
    assert response.status_code == 200, response.text
    assert hook.events[0]["subject"]["purpose"] == "workflow_analysis"


def test_suggest_not_called_for_other_purposes(client: TestClient) -> None:
    _enable(client)
    hook = _Hook({"decision": "deny", "reason": "x"})
    _install(client, hook, filters={"purposes": ["assistant"]})
    assert client.post(SUGGEST, json={"template": _t2i()}).status_code == 200
    assert hook.events == []


def test_suggest_requires_admin(client_oidc: TestClient) -> None:
    login_as(client_oidc, "user@example.com")
    assert client_oidc.post(SUGGEST, json={"template": _t2i()}).status_code == 403
    assert client_oidc.get(ASSISTANT_SETTINGS).json()["structured_outputs"] is True
    login_as(client_oidc, "admin@example.com")
    _enable(client_oidc)
    assert client_oidc.post(SUGGEST, json={"template": _t2i()}).status_code == 200
