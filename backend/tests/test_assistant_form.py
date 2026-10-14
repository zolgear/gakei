"""アシスタントの生成フォームの書き込み(ADR-0048 11章)。Fake のエンジンの `/tool` 行で
`update_form` を呼び、変えた項目(`patch`)と重ねた後の値(`form`)、検証(プロバイダー・モデル・
パラメーターのキー・見える入力画像とグループ)、同じ発言の `get_form_context` と
`generate_image` への反映を確かめる。サーバーにフォームの値を保存しないことも見る。実 API は
呼ばない。"""

from __future__ import annotations

import io
import json
from typing import Any

from fastapi.testclient import TestClient

from app.assistant.tools import TOOLS_BY_NAME, parameters_schema
from tests.conftest import login_as, make_png_bytes, wait_for_run_terminal

SETTINGS = "/api/settings/assistant"
CONVERSATIONS = "/api/assistant/conversations"
_FORM = {
    "provider": "fake",
    "model": "gpt-image-2.5-flare",
    "operation": "generate",
    "prompt": "a lighthouse",
    "params": {"size": "1024x1024", "quality": "low", "output_format": "png"},
}


def _enable(client: TestClient) -> None:
    response = client.patch(SETTINGS, json={"enabled": True})
    assert response.status_code == 200, response.text


def _conversation(client: TestClient) -> str:
    response = client.post(CONVERSATIONS, json={"title": ""})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _parse_sse(text: str) -> list[dict]:
    events: list[dict] = []
    for block in text.strip().split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line.removeprefix("data: ")))
    return events


def _send(client: TestClient, conversation_id: str, text: str, **body: Any) -> list[dict]:
    response = client.post(
        f"{CONVERSATIONS}/{conversation_id}/messages", json={"text": text, **body}
    )
    assert response.status_code == 200, response.text
    return _parse_sse(response.text)


def _tool(name: str, args: dict | None = None) -> str:
    return f"/tool {name} {json.dumps(args or {})}"


def _results(events: list[dict]) -> list[dict]:
    return [e["message"] for e in events if e["type"] == "tool_result"]


def _update(client: TestClient, args: dict, form: dict | None = _FORM) -> dict:
    _enable(client)
    conversation_id = _conversation(client)
    body = {"form": form} if form is not None else {}
    return _results(_send(client, conversation_id, _tool("update_form", args), **body))[0]


def _upload(client: TestClient) -> str:
    response = client.post(
        "/api/assets",
        files={"file": ("x.png", io.BytesIO(make_png_bytes(64, 48)), "image/png")},
        data={"kind": "upload"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _group(client: TestClient, name: str = "g") -> str:
    response = client.post("/api/asset-groups", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_update_prompt_and_params(client: TestClient) -> None:
    message = _update(client, {"prompt": "a cat", "params": {"quality": "high"}})
    assert message["ok"] is True, message
    result = message["result"]
    assert result["patch"] == {"prompt": "a cat", "params": {"quality": "high"}}
    form = result["form"]
    assert form["prompt"] == "a cat"
    assert form["model"] == "gpt-image-2.5-flare"
    assert form["params"] == {"size": "1024x1024", "quality": "high", "output_format": "png"}
    assert form["operation"] == "generate"
    assert form["inputs"] == []
    # 書き込みの「元に戻す」(サーバー)の対象ではない。変更前はブラウザが持つ。
    assert message["undoable"] is False


def test_null_param_resets_to_form_default(client: TestClient) -> None:
    message = _update(
        client,
        {"params": {"quality": None, "background": None, "size": None}},
        form={**_FORM, "params": {"size": "1536x1024", "quality": "high", "background": "opaque"}},
    )
    assert message["ok"] is True, message
    result = message["result"]
    # 結果の patch には null のまま、form には既定(form_default、size は既定のサイズ)を入れる。
    assert result["patch"]["params"] == {"quality": None, "background": None, "size": None}
    # background は form_default が無いので未指定(キーを落とす)。
    assert result["form"]["params"] == {"quality": "low", "size": "1024x1024"}


def test_switch_model_and_provider(client: TestClient) -> None:
    message = _update(client, {"model": "gpt-image-2"})
    assert message["ok"] is True, message
    assert message["result"]["patch"] == {"model": "gpt-image-2"}
    assert message["result"]["form"]["model"] == "gpt-image-2"
    assert message["result"]["form"]["params"]["quality"] == "low"

    # quality=xhigh は gpt-image-2 に無いので、引き継がずに落とす。
    message = _update(
        client,
        {"model": "gpt-image-2"},
        form={**_FORM, "params": {"quality": "xhigh", "size": "1024x1024"}},
    )
    assert message["ok"] is True, message
    assert message["result"]["form"]["params"] == {"size": "1024x1024"}
    assert message["result"]["dropped_params"] == ["quality"]

    # 今と同じプロバイダーを指定しても、モデルはそのまま(結果にはモデルも入れる)。
    message = _update(client, {"provider": "fake"}, form={**_FORM, "model": "gpt-image-2"})
    assert message["ok"] is True, message
    assert message["result"]["patch"] == {"provider": "fake", "model": "gpt-image-2"}

    # フォームのプロバイダーが空(未解決)なら、主プロバイダーの既定のモデル。
    message = _update(client, {"provider": "fake"}, form={**_FORM, "provider": None, "model": None})
    assert message["ok"] is True, message
    assert message["result"]["patch"] == {"provider": "fake", "model": "gpt-image-2.5-flare"}


def test_inputs_set_edit_and_clear(client: TestClient) -> None:
    first = _upload(client)
    second = _upload(client)
    message = _update(
        client,
        {"inputs": [{"asset_id": first}, {"asset_id": second, "role": "mask"}]},
    )
    assert message["ok"] is True, message
    result = message["result"]
    assert result["patch"]["operation"] == "edit"
    assert result["patch"]["inputs"] == [
        {"asset_id": first, "role": "image"},
        {"asset_id": second, "role": "mask"},
    ]
    assert result["form"]["operation"] == "edit"

    edit_form = {**_FORM, "operation": "edit", "inputs": [{"asset_id": first, "role": "image"}]}
    message = _update(client, {"inputs": []}, form=edit_form)
    assert message["ok"] is True, message
    assert message["result"]["patch"] == {"inputs": [], "operation": "generate"}

    # operation=generate だけでも入力画像を外す。
    message = _update(client, {"operation": "generate"}, form=edit_form)
    assert message["ok"] is True, message
    assert message["result"]["patch"] == {"inputs": [], "operation": "generate"}


def test_group_set_and_clear(client: TestClient) -> None:
    group_id = _group(client)
    message = _update(client, {"asset_group_id": group_id})
    assert message["ok"] is True, message
    assert message["result"]["patch"] == {"asset_group_id": group_id}
    assert message["result"]["form"]["asset_group_id"] == group_id

    message = _update(client, {"asset_group_id": None}, form={**_FORM, "asset_group_id": group_id})
    assert message["ok"] is True, message
    assert message["result"]["patch"] == {"asset_group_id": None}
    assert message["result"]["form"]["asset_group_id"] is None


def test_validation_errors_change_nothing(client: TestClient) -> None:
    _enable(client)
    missing = "00000000-0000-0000-0000-000000000001"
    cases: list[tuple[dict, str]] = [
        ({}, "Nothing to change"),
        ({"provider": "nope"}, "Unknown provider"),
        ({"model": "nope"}, "Unknown model"),
        ({"params": {"steps": 20}}, "Unknown parameter"),
        ({"params": {"quality": {"a": 1}}}, "must be a string"),
        ({"inputs": [{"asset_id": missing}]}, "not found"),
        ({"operation": "edit"}, "edit needs"),
        ({"inputs": [{"asset_id": missing, "role": "reference"}]}, "Invalid arguments"),
        ({"inputs": [{"asset_id": missing, "role": "mask"}]}, "A mask needs"),
        ({"asset_group_id": missing}, "not found"),
    ]
    for args, expected in cases:
        conversation_id = _conversation(client)
        update_args = {"prompt": "changed", **args} if args else {}
        text = "\n".join([_tool("update_form", update_args), _tool("get_form_context")])
        failed, context = _results(_send(client, conversation_id, text, form=_FORM))
        assert failed["ok"] is False, args
        assert expected in failed["result"]["error"], (args, failed["result"])
        # 通らなければ何も変えない(プロンプトもそのまま)。
        assert context["result"]["prompt"] == "a lighthouse"


def test_other_users_assets_and_groups_are_not_found(client_oidc: TestClient) -> None:
    client = client_oidc
    login_as(client, "admin@example.com")
    _enable(client)
    login_as(client, "alice@example.com")
    asset_id = _upload(client)
    group_id = _group(client, "alice")
    login_as(client, "bob@example.com")
    conversation_id = _conversation(client)
    for args in ({"inputs": [{"asset_id": asset_id}]}, {"asset_group_id": group_id}):
        events = _send(client, conversation_id, _tool("update_form", args), form=_FORM)
        message = _results(events)[0]
        assert message["ok"] is False
        assert "not found" in message["result"]["error"]


def test_changes_apply_to_form_context_and_generate(client: TestClient) -> None:
    _enable(client)
    conversation_id = _conversation(client)
    text = "\n".join(
        [
            _tool("update_form", {"prompt": "a red fox", "params": {"quality": "medium"}}),
            _tool("get_form_context"),
            _tool("generate_image"),
        ]
    )
    results = _results(_send(client, conversation_id, text, form=_FORM))
    assert [r["ok"] for r in results] == [True, True, True], results
    context = results[1]["result"]
    assert context["prompt"] == "a red fox"
    assert context["params"]["quality"] == "medium"
    generated = results[2]["result"]
    assert generated["prompt"] == "a red fox"
    assert generated["params"]["quality"] == "medium"
    wait_for_run_terminal(client, generated["run_ids"][0])

    # サーバーはフォームの値を持たない。次の発言は、ブラウザが送った値から始まる。
    events = _send(client, conversation_id, _tool("get_form_context"), form=_FORM)
    assert _results(events)[0]["result"]["prompt"] == "a lighthouse"


def test_works_without_form_state(client: TestClient) -> None:
    message = _update(client, {"prompt": "only a prompt"}, form=None)
    assert message["ok"] is True, message
    assert message["result"]["patch"] == {"prompt": "only a prompt"}


def test_schema_marks_group_nullable() -> None:
    schema = parameters_schema(TOOLS_BY_NAME["update_form"].args)
    assert set(schema["properties"]) == {
        "provider",
        "model",
        "operation",
        "prompt",
        "params",
        "inputs",
        "asset_group_id",
    }
    assert schema.get("additionalProperties") is False
