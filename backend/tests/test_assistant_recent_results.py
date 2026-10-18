"""アシスタントの履歴の参照(ADR-0048 12章「履歴の参照」)。Fake のエンジンの `/tool` 行で
`list_recent_results` と `view_result` の `asset_ids` を呼び、新しい順、件数、見える範囲、
論理削除済みの出力の除外、グループでの絞り込み、複数の画像の順序と枚数、上限と併用のエラーを
確かめる。実 API は呼ばない。"""

from __future__ import annotations

import io
import json
import uuid
from typing import Any

from fastapi.testclient import TestClient

from app.assistant.engine import FakeAssistantEngine
from app.assistant.tools import RECENT_RESULTS_PROMPT_MAX, TOOLS_BY_NAME, parameters_schema
from app.domain.schemas import ASSISTANT_ATTACHMENTS_MAX
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
    response = client.post(CONVERSATIONS, json={"title": ""})
    assert response.status_code == 201, response.text
    body: dict[str, Any] = {"text": text}
    if form is not None:
        body["form"] = form
    response = client.post(f"{CONVERSATIONS}/{response.json()['id']}/messages", json=body)
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


def _run(
    client: TestClient,
    prompt: str,
    *,
    n: int = 1,
    parent: str | None = None,
    group_id: str | None = None,
) -> dict:
    body: dict[str, Any] = {
        "operation": "edit" if parent else "generate",
        "model": _MODEL,
        "prompt": prompt,
        "params": {"n": n},
    }
    if parent:
        body["inputs"] = [{"asset_id": parent, "role": "image", "position": 0}]
    if group_id:
        body["asset_group_id"] = group_id
    response = client.post("/api/runs", json=body)
    assert response.status_code == 202, response.text
    detail = wait_for_run_terminal(client, response.json()["id"])
    assert detail["status"] == "succeeded", detail
    return detail


def _outputs(detail: dict) -> list[str]:
    return [o["asset_id"] for o in detail["outputs"]]


def _recent(client: TestClient, args: dict | None = None, form: dict | None = None) -> dict:
    message = _send(client, _tool("list_recent_results", args), form=form)[0]
    assert message["ok"] is True, message
    return message["result"]


def test_recent_results_newest_first_with_limit(client: TestClient) -> None:
    _enable(client)
    parent = _upload(client)
    first = _run(client, "first")
    second = _run(client, "second", n=2)
    third = _run(client, "third", parent=parent)

    result = _recent(client)
    assert [i["run_id"] for i in result["items"]] == [third["id"], second["id"], first["id"]]
    assert result["truncated"] is False
    assert "view_result" in result["note"]

    newest = result["items"][0]
    assert newest["status"] == "succeeded"
    assert newest["operation"] == "edit"
    assert newest["model"] == _MODEL
    assert newest["prompt"] == "third"
    assert newest["output_asset_ids"] == _outputs(third)
    assert newest["inputs"] == [{"asset_id": parent, "role": "image"}]
    # 出力の並び順(output_index)のまま。
    assert result["items"][1]["output_asset_ids"] == _outputs(second)
    assert len(result["items"][1]["output_asset_ids"]) == 2
    # 画像の中身は入れない。
    assert all(not m.images for m in _engine(client).requests[-1].messages)

    limited = _recent(client, {"limit": 2})
    assert [i["run_id"] for i in limited["items"]] == [third["id"], second["id"]]
    assert limited["truncated"] is True


def test_recent_results_limit_bounds(client: TestClient) -> None:
    _enable(client)
    for limit in (0, 51):
        message = _send(client, _tool("list_recent_results", {"limit": limit}))[0]
        assert message["ok"] is False


def test_recent_results_clips_long_prompt(client: TestClient) -> None:
    _enable(client)
    _run(client, "x" * (RECENT_RESULTS_PROMPT_MAX + 50))
    prompt = _recent(client)["items"][0]["prompt"]
    assert prompt.startswith("x" * RECENT_RESULTS_PROMPT_MAX)
    assert len(prompt) == RECENT_RESULTS_PROMPT_MAX + 1


def test_recent_results_excludes_deleted_outputs(client: TestClient) -> None:
    _enable(client)
    detail = _run(client, "two images", n=2)
    kept, deleted = _outputs(detail)
    assert client.delete(f"/api/assets/{deleted}").status_code in (200, 204)
    item = _recent(client)["items"][0]
    assert item["run_id"] == detail["id"]
    assert item["output_asset_ids"] == [kept]


def test_recent_results_filter_by_group(client: TestClient) -> None:
    _enable(client)
    response = client.post("/api/asset-groups", json={"name": "views"})
    assert response.status_code == 201, response.text
    group = response.json()["id"]

    sent = _run(client, "sent to the group", group_id=group)
    _run(client, "outside")
    moved = _run(client, "moved later")
    response = client.post(f"/api/asset-groups/{group}/assets", json={"asset_ids": _outputs(moved)})
    assert response.status_code in (200, 204), response.text

    result = _recent(client, {"group_id": group})
    assert [i["run_id"] for i in result["items"]] == [moved["id"], sent["id"]]

    message = _send(client, _tool("list_recent_results", {"group_id": str(uuid.uuid4())}))[0]
    assert message["ok"] is False
    assert "not found" in message["result"]["error"]


def test_recent_results_hide_other_users_runs(client_oidc: TestClient) -> None:
    client = client_oidc
    login_as(client, "admin@example.com")
    _enable(client)
    login_as(client, "bob@example.com")
    _run(client, "bob's run")
    login_as(client, "alice@example.com")
    mine = _run(client, "alice's run")
    result = _recent(client)
    assert [i["run_id"] for i in result["items"]] == [mine["id"]]


def test_recent_results_include_assistant_runs(client: TestClient) -> None:
    _enable(client)
    web = _run(client, "from the form")
    created = _send(client, _tool("generate_image", {"prompt": "from the assistant"}), _FORM)[0]
    assert created["ok"] is True, created
    run_id = created["result"]["run_ids"][0]
    wait_for_run_terminal(client, run_id)
    items = _recent(client)["items"]
    assert [i["run_id"] for i in items] == [run_id, web["id"]]
    assert items[0]["origin"] == "assistant"
    assert items[1]["origin"] == "web"


def test_view_result_several_images_in_order(client: TestClient) -> None:
    _enable(client)
    older = _outputs(_run(client, "older"))[0]
    newer_run = _run(client, "newer")
    newer = _outputs(newer_run)[0]
    upload = _upload(client)
    ids = [newer, upload, older]

    message = _send(
        client, _tool("view_result", {"asset_ids": ids}), form={**_FORM, "result_asset_id": newer}
    )[0]
    assert message["ok"] is True, message
    assets = message["result"]["assets"]
    assert [a["asset_id"] for a in assets] == ids
    assert [a["in_result_area"] for a in assets] == [True, False, False]
    # その画像を出力した Run(履歴の位置を数える起点)。
    assert assets[0]["produced_by_run"]["run_id"] == newer_run["id"]
    assert assets[1]["produced_by_run"] is None
    images = _engine(client).requests[-1].messages[-1].images
    assert [image.label for image in images] == ids

    message = _send(client, _tool("view_result", {"asset_ids": ids, "include_image": False}))[0]
    assert message["ok"] is True, message
    assert len(message["result"]["assets"]) == 3
    assert all(not m.images for m in _engine(client).requests[-1].messages)


def test_view_result_several_images_errors(client: TestClient) -> None:
    _enable(client)
    upload = _upload(client)
    too_many = [upload] * (ASSISTANT_ATTACHMENTS_MAX + 1)
    for args in (
        {"asset_ids": too_many},
        {"asset_ids": []},
        {"asset_id": upload, "asset_ids": [upload]},
        {"asset_ids": [upload, str(uuid.uuid4())]},
    ):
        message = _send(client, _tool("view_result", args))[0]
        assert message["ok"] is False, args


def test_view_result_without_arguments_still_uses_result_area(client: TestClient) -> None:
    _enable(client)
    shown = _upload(client)
    message = _send(
        client,
        _tool("view_result", {"include_image": False}),
        form={**_FORM, "result_asset_id": shown},
    )[0]
    assert message["ok"] is True, message
    assert message["result"]["asset_id"] == shown
    assert message["result"]["from_result_area"] is True
    assert "assets" not in message["result"]


def test_list_recent_results_schema() -> None:
    schema = parameters_schema(TOOLS_BY_NAME["list_recent_results"].args)
    assert set(schema["properties"]) == {"limit", "group_id"}
    assert schema.get("required", []) == []
    assert schema.get("additionalProperties") is False
