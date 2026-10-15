"""アシスタントの生成結果の読み取り(ADR-0048 12章)。Fake のエンジンの `/tool` 行で
`view_result` を呼び、結果エリアの画像(`form.result_asset_id`)を使うこと、`get_asset` と同じ
情報と画像を返すこと、`include_image=false` で画像を付けないこと、見えない・削除済み・対象が
無いときのエラー、`get_form_context` と `update_form` での `result_asset_id` の扱いを確かめる。
実 API は呼ばない。"""

from __future__ import annotations

import io
import json
import uuid
from typing import Any

from fastapi.testclient import TestClient

from app.assistant.engine import FakeAssistantEngine
from app.assistant.tools import TOOLS_BY_NAME, parameters_schema
from tests.conftest import login_as, make_png_bytes, wait_for_run_terminal

SETTINGS = "/api/settings/assistant"
CONVERSATIONS = "/api/assistant/conversations"
_MODEL = "gpt-image-2.5-sunburst"
_FORM = {
    "provider": "fake",
    "model": "gpt-image-2.5-flare",
    "operation": "generate",
    "prompt": "a lighthouse",
    "params": {"size": "1024x1024"},
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


def _tool(name: str, args: dict | None = None) -> str:
    return f"/tool {name} {json.dumps(args or {})}"


def _send(client: TestClient, text: str, form: dict | None = None) -> list[dict]:
    body: dict[str, Any] = {"text": text}
    if form is not None:
        body["form"] = form
    response = client.post(f"{CONVERSATIONS}/{_conversation(client)}/messages", json=body)
    assert response.status_code == 200, response.text
    return [e["message"] for e in _parse_sse(response.text) if e["type"] == "tool_result"]


def _engine(client: TestClient) -> FakeAssistantEngine:
    engine = client.app.state.assistant.engine
    assert isinstance(engine, FakeAssistantEngine)
    return engine


def _upload(client: TestClient) -> str:
    response = client.post(
        "/api/assets",
        files={"file": ("x.png", io.BytesIO(make_png_bytes(64, 48)), "image/png")},
        data={"kind": "upload"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _edit_output(client: TestClient, parent: str, prompt: str) -> str:
    response = client.post(
        "/api/runs",
        json={
            "operation": "edit",
            "model": _MODEL,
            "prompt": prompt,
            "params": {"n": 1},
            "inputs": [{"asset_id": parent, "role": "image", "position": 0}],
        },
    )
    assert response.status_code == 202, response.text
    detail = wait_for_run_terminal(client, response.json()["id"])
    assert detail["status"] == "succeeded", detail
    return detail["outputs"][0]["asset_id"]


def _form(result_asset_id: str | None) -> dict:
    return {**_FORM, "result_asset_id": result_asset_id}


def test_view_result_uses_result_area_asset(client: TestClient) -> None:
    _enable(client)
    parent = _upload(client)
    result_id = _edit_output(client, parent, "make the sky orange")
    response = client.post(f"/api/assets/{result_id}/tags", json={"name": "sunset"})
    assert response.status_code == 200, response.text

    message = _send(client, _tool("view_result"), form=_form(result_id))[0]
    assert message["ok"] is True, message
    result = message["result"]
    assert result["asset_id"] == result_id
    assert result["from_result_area"] is True
    # `get_asset` と同じ情報(タグ、生成したときのプロンプト・モデル・パラメーター、主たる親)。
    assert [t["name"] for t in result["tags"]] == ["sunset"]
    assert result["produced_by_run"]["prompt"] == "make the sky orange"
    assert result["produced_by_run"]["model"] == _MODEL
    assert result["primary_parent_asset_id"] == parent
    # 画像は DB に保存しない。LLM にだけ送る。
    assert "base64" not in json.dumps(message)
    tool_message = _engine(client).requests[-1].messages[-1]
    assert tool_message.role == "tool"
    assert len(tool_message.images) == 1
    assert tool_message.images[0].data[:2] == b"\xff\xd8"  # JPEG
    assert tool_message.images[0].label == result_id


def test_view_result_same_details_as_get_asset(client: TestClient) -> None:
    _enable(client)
    result_id = _upload(client)
    view, details = _send(
        client,
        "\n".join(
            [
                _tool("view_result", {"include_image": False}),
                _tool("get_asset", {"asset_id": result_id}),
            ]
        ),
        form=_form(result_id),
    )
    assert view["ok"] is True and details["ok"] is True
    extra = {"from_result_area"}
    assert {k: v for k, v in view["result"].items() if k not in extra} == details["result"]


def test_view_result_without_image(client: TestClient) -> None:
    _enable(client)
    result_id = _upload(client)
    message = _send(client, _tool("view_result", {"include_image": False}), form=_form(result_id))[
        0
    ]
    assert message["ok"] is True, message
    assert message["result"]["asset_id"] == result_id
    assert all(not m.images for m in _engine(client).requests[-1].messages)


def test_view_result_with_explicit_asset_id(client: TestClient) -> None:
    _enable(client)
    shown = _upload(client)
    other = _upload(client)
    message = _send(client, _tool("view_result", {"asset_id": other}), form=_form(shown))[0]
    assert message["ok"] is True, message
    assert message["result"]["asset_id"] == other
    assert message["result"]["from_result_area"] is False
    # 結果エリアに画像が無くても、指定すれば見られる。
    message = _send(client, _tool("view_result", {"asset_id": other}))[0]
    assert message["ok"] is True, message


def test_view_result_errors_without_target(client: TestClient) -> None:
    _enable(client)
    for form in (None, _form(None)):
        message = _send(client, _tool("view_result"), form=form)[0]
        assert message["ok"] is False
        assert "result area" in message["result"]["error"]


def test_view_result_errors_for_missing_and_deleted(client: TestClient) -> None:
    _enable(client)
    missing = str(uuid.uuid4())
    message = _send(client, _tool("view_result"), form=_form(missing))[0]
    assert message["ok"] is False
    assert "not found" in message["result"]["error"]

    deleted = _upload(client)
    assert client.delete(f"/api/assets/{deleted}").status_code in (200, 204)
    for args in ({}, {"asset_id": deleted}):
        message = _send(client, _tool("view_result", args), form=_form(deleted))[0]
        assert message["ok"] is False
        assert "not found" in message["result"]["error"]


def test_view_result_hides_other_users_assets(client_oidc: TestClient) -> None:
    client = client_oidc
    login_as(client, "admin@example.com")
    _enable(client)
    login_as(client, "bob@example.com")
    bob = _upload(client)
    login_as(client, "alice@example.com")
    for args, form in (({}, _form(bob)), ({"asset_id": bob}, None)):
        message = _send(client, _tool("view_result", args), form=form)[0]
        assert message["ok"] is False
        assert "not found" in message["result"]["error"]


def test_form_context_and_update_form_keep_result_asset_id(client: TestClient) -> None:
    _enable(client)
    result_id = _upload(client)
    context, updated, context_after, view = _send(
        client,
        "\n".join(
            [
                _tool("get_form_context"),
                _tool("update_form", {"prompt": "a red fox"}),
                _tool("get_form_context"),
                _tool("view_result", {"include_image": False}),
            ]
        ),
        form=_form(result_id),
    )
    assert context["result"]["result_asset_id"] == result_id
    assert updated["ok"] is True, updated
    # ブラウザがフォームに反映する値には、結果エリアの画像を入れない。
    assert "result_asset_id" not in updated["result"]["form"]
    # 変更を重ねた後も、結果エリアの画像は残る。
    assert context_after["result"]["prompt"] == "a red fox"
    assert context_after["result"]["result_asset_id"] == result_id
    assert view["ok"] is True and view["result"]["asset_id"] == result_id


def test_view_result_schema() -> None:
    schema = parameters_schema(TOOLS_BY_NAME["view_result"].args)
    assert set(schema["properties"]) == {"asset_id", "include_image"}
    assert schema.get("required", []) == []
    assert schema.get("additionalProperties") is False
