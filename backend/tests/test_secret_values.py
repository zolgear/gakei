"""秘密に見える値の判定(ADR-0013 8章、ADR-0029 3章・4章、2026-10-01 追記)。"""

from __future__ import annotations

import copy

import pytest

from app.domain.comfy_workflow import analyze_workflow
from app.domain.secret_values import (
    REDACTED,
    SecretInput,
    comfyui_params_have_secret,
    find_secret_inputs,
    is_secret_input,
    is_secret_name,
    looks_like_secret_value,
    redact_comfyui_params,
    redact_graph,
    split_name_words,
)
from tests.comfyui_graphs import T2I_GRAPH, clone

# テスト用の、形だけそれらしい値(実在のキーではない)。
OPENAI_KEY = "sk-proj-" + "Ab3dEf6hIj9kLm2nOp5qRs8t"
ANTHROPIC_KEY = "sk-ant-api03-" + "Xy7zAb3dEf6hIj9kLm2nOp5q"
HF_TOKEN = "hf_" + "aB3dEf6hIj9kLm2nOp5qRs8tUv1wXy4zAb"
GITHUB_TOKEN = "ghp_" + "aB3dEf6hIj9kLm2nOp5qRs8tUv1wXy4zAb7c"
GITHUB_PAT = "github_pat_" + "11AB3dEf6hIj9kLm2nOp5q_Rs8tUv1wXy4zAb7cDe0fGh"
AWS_KEY = "AKIA" + "IOSFODNN7EXAMPLE"
GOOGLE_KEY = "AIza" + "SyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q"
SLACK_TOKEN = "xoxb-" + "123456789012-abcdefABCDEF"
REPLICATE_TOKEN = "r8_" + "aB3dEf6hIj9kLm2nOp5qRs8tUv1wXy4z"
BEARER = "Bearer " + "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.abc123"


# -- 名前 ----------------------------------------------------------------------


def test_split_name_words() -> None:
    assert split_name_words("api_key") == ["api", "key"]
    assert split_name_words("apiKey") == ["api", "key"]
    assert split_name_words("APIKey") == ["api", "key"]
    assert split_name_words("OPENAI_API_KEY") == ["openai", "api", "key"]
    assert split_name_words("x-api-key") == ["x", "api", "key"]
    assert split_name_words("hfToken2") == ["hf", "token", "2"]


@pytest.mark.parametrize(
    "name",
    [
        "api_key",
        "apikey",
        "apiKey",
        "API_KEY",
        "openai_api_key",
        "x-api-key",
        "token",
        "hf_token",
        "accessToken",
        "secret",
        "client_secret",
        "clientSecret",
        "secret_key",
        "password",
        "passwd",
        "credentials",
        "credential",
        "authorization",
        "auth",
        "auth_header",
        "bearer",
        "access_key",
        "accessKey",
        "aws_access_key_id",
        "private_key",
        "privateKey",
    ],
)
def test_secret_names(name: str) -> None:
    assert is_secret_name(name), name


@pytest.mark.parametrize(
    "name",
    [
        "max_tokens",
        "tokens",
        "tokenizer",
        "token_count",
        "max_token",
        "token_limit",
        "num_tokens",
        "text",
        "prompt",
        "key",
        "seed",
        "ckpt_name",
        "author",
        "keyword",
        "api_url",
        "base_url",
        "monkey",
    ],
)
def test_non_secret_names(name: str) -> None:
    assert not is_secret_name(name), name


# -- 値の形 --------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        OPENAI_KEY,
        "sk-" + "Ab3dEf6hIj9kLm2nOp5qRs8tUv1w",
        ANTHROPIC_KEY,
        HF_TOKEN,
        GITHUB_TOKEN,
        GITHUB_PAT,
        AWS_KEY,
        GOOGLE_KEY,
        SLACK_TOKEN,
        REPLICATE_TOKEN,
        BEARER,
        f"use key {OPENAI_KEY} here",
    ],
)
def test_secret_value_shapes(value: str) -> None:
    assert looks_like_secret_value(value), value


@pytest.mark.parametrize(
    "value",
    [
        "",
        "a cat sitting on a sofa",
        # プロンプトの文章の中に偶然現れる短い語。
        "sk-8 skateboard, hf_ logo, ghp_ text, AKIA, AIza, xoxb-1, r8_ car",
        "a task-based workflow with sk-like prefix",
        "sk-full-body-portrait-of-a-woman-standing-in-the-rain",
        "Bearer of the ring, a hobbit",
        "bearer bonds in a vault",
        "photo of a dask-1234567890abcdefghijklmn job",
        "flux1-dev-fp8.safetensors",
        "gakei_" + "a" * 64 + ".png",
    ],
)
def test_non_secret_values(value: str) -> None:
    assert not looks_like_secret_value(value), value


def test_is_secret_input_value_types() -> None:
    # 名前が当たっても、値が空・文字列でないもの(数値、ノードへのリンク)は当てない。
    assert is_secret_input("api_key", "abc")
    assert not is_secret_input("api_key", "")
    assert not is_secret_input("api_key", "   ")
    assert not is_secret_input("api_key", None)
    assert not is_secret_input("api_key", 123)
    assert not is_secret_input("api_key", ["6", 0])
    assert not is_secret_input("token", True)
    # 名前が当たらなくても、値の形が当たれば当てる。
    assert is_secret_input("text", OPENAI_KEY)
    assert not is_secret_input("max_tokens", "4096")
    assert not is_secret_input("text", "a photo of a cat")


# -- グラフ --------------------------------------------------------------------


def _graph_with_secrets() -> dict:
    graph = clone(T2I_GRAPH)
    graph["90"] = {
        "class_type": "SomeApiNode",
        "inputs": {
            "api_key": "my-plain-secret",
            "prompt": "a cat",
            "max_tokens": 512,
            "token_count": "100",
            "model": ["4", 0],
            "endpoint": "https://example.com",
            "password": "",
        },
        "_meta": {"title": "API"},
    }
    graph["91"] = {
        "class_type": "OtherNode",
        "inputs": {"header": BEARER, "text": "hello"},
    }
    return graph


def test_find_secret_inputs() -> None:
    assert find_secret_inputs(clone(T2I_GRAPH)) == []
    assert find_secret_inputs(_graph_with_secrets()) == [
        SecretInput(node_id="90", class_type="SomeApiNode", input="api_key"),
        SecretInput(node_id="91", class_type="OtherNode", input="header"),
    ]
    assert find_secret_inputs(None) == []
    assert find_secret_inputs({"1": "not a node", "2": {"inputs": None}}) == []


def test_redact_graph_keeps_keys_and_original() -> None:
    graph = _graph_with_secrets()
    before = copy.deepcopy(graph)
    redacted = redact_graph(graph)
    assert graph == before
    assert redacted["90"]["inputs"]["api_key"] == REDACTED
    assert redacted["91"]["inputs"]["header"] == REDACTED
    assert redacted["90"]["inputs"]["prompt"] == "a cat"
    assert redacted["90"]["inputs"]["model"] == ["4", 0]
    assert redacted["90"]["inputs"]["password"] == ""
    assert redacted["91"]["inputs"]["text"] == "hello"
    # 秘密の無いノードはそのまま。
    for node_id in T2I_GRAPH:
        assert redacted[node_id] == graph[node_id]


def test_nested_inputs_are_checked_and_redacted() -> None:
    # 可変長の入力などで、入力の値が入れ子の dict になるものの中も見る。
    graph = {
        "95": {
            "class_type": "NestedApiNode",
            "inputs": {
                "images": {"image_1": ["10", 0]},
                "config": {"api_key": "plain-secret", "model": "x", "deep": {"token": "t0ken"}},
            },
        }
    }
    before = copy.deepcopy(graph)
    assert find_secret_inputs(graph) == [
        SecretInput(node_id="95", class_type="NestedApiNode", input="config.api_key"),
        SecretInput(node_id="95", class_type="NestedApiNode", input="config.deep.token"),
    ]
    redacted = redact_graph(graph)
    assert graph == before
    assert redacted["95"]["inputs"]["config"]["api_key"] == REDACTED
    assert redacted["95"]["inputs"]["config"]["deep"]["token"] == REDACTED
    assert redacted["95"]["inputs"]["config"]["model"] == "x"
    assert redacted["95"]["inputs"]["images"] == {"image_1": ["10", 0]}
    assert comfyui_params_have_secret({"comfyui_prompt": graph})


def test_redact_comfyui_params() -> None:
    params = {
        "prompt_api_key": "abc",
        "negative_prompt": "blurry",
        "seed": 1,
        "comfyui_prompt": _graph_with_secrets(),
        "comfyui_workflow": {"id": "x", "name": "wf", "token": "not a public param"},
        "comfyui_seed": 1,
    }
    before = copy.deepcopy(params)
    redacted = redact_comfyui_params(params)
    assert params == before
    assert redacted["prompt_api_key"] == REDACTED
    assert redacted["negative_prompt"] == "blurry"
    assert redacted["seed"] == 1
    assert redacted["comfyui_prompt"]["90"]["inputs"]["api_key"] == REDACTED
    # comfyui_prompt 以外の comfyui_* は公開パラメーターではないので触らない。
    assert redacted["comfyui_workflow"] == params["comfyui_workflow"]
    assert set(redacted) == set(params)


def test_comfyui_params_have_secret() -> None:
    clean = {"seed": 1, "comfyui_prompt": clone(T2I_GRAPH), "comfyui_seed": 1}
    assert not comfyui_params_have_secret(clean)
    assert not comfyui_params_have_secret(None)
    assert comfyui_params_have_secret({**clean, "comfyui_prompt": _graph_with_secrets()})
    assert comfyui_params_have_secret({**clean, "my_token": "xyz"})
    assert comfyui_params_have_secret({**clean, "style": OPENAI_KEY})
    # 公開パラメーターでない comfyui_* は見ない。
    assert not comfyui_params_have_secret({**clean, "comfyui_token": "xyz"})


# -- 登録時の警告 ----------------------------------------------------------------


def test_analyze_warns_secret_inputs_without_values() -> None:
    graph = _graph_with_secrets()
    result = analyze_workflow(graph)
    secret_warnings = [w for w in result.warnings if "秘密" in w]
    assert len(secret_warnings) == 2
    assert "90" in secret_warnings[0]
    assert "SomeApiNode" in secret_warnings[0]
    assert "api_key" in secret_warnings[0]
    assert "OtherNode" in secret_warnings[1]
    assert "header" in secret_warnings[1]
    text = "\n".join(result.warnings)
    assert "my-plain-secret" not in text
    assert BEARER not in text


def test_analyze_has_no_secret_warning_for_plain_graph() -> None:
    result = analyze_workflow(clone(T2I_GRAPH))
    assert not any("秘密" in w for w in result.warnings)
