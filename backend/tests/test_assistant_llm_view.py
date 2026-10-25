"""アシスタントが LLM に送る情報(ADR-0048 14.3〜14.5)。

- X 以上の画像: 接続先が外部(既定)なら、ID・種類・段階・グループだけを送る(`withheld`)。
  ローカルなら絞らない。判定は `llm_view.withholds` の1か所。
- 結果の大きさ: 一覧は予算に収まる件数で切り、`returned`・`total`・`next_offset`。最後の
  文字数での切り捨ては `truncated: true`。画面用の値は送らない。
- 古い道具の結果: 直近 3 件より前の 2,000 字を超える結果は ID と件数だけにする。
実 API は呼ばない。
"""

from __future__ import annotations

import io
import json
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.assistant import llm_view
from app.assistant.engine import FakeAssistantEngine
from app.assistant.service import _tool_text, build_history
from app.domain import content_rating as content_rating_domain
from app.domain import locality as locality_domain
from app.domain.models import Asset, AssistantMessage
from tests.conftest import make_png_bytes, wait_for_run_terminal

SETTINGS = "/api/settings/assistant"
CONVERSATIONS = "/api/assistant/conversations"
_MODEL = "gpt-image-2.5-sunburst"


# -- 判定と形(純粋関数) ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("level", "external", "expected"),
    [
        ("x", True, True),
        ("xx", True, True),
        ("xxx", True, True),
        ("r", True, False),
        ("g", True, False),
        (None, True, False),
        ("xxx", False, False),
        ("x", False, False),
    ],
)
def test_withholds(level: str | None, external: bool, expected: bool) -> None:
    assert llm_view.withholds(level, external=external) is expected


def test_compact_asset_keeps_user_tags_first_and_limits_count() -> None:
    entry = {
        "asset_id": "a",
        "kind": "upload",
        "width": 10,
        "height": 10,
        "created_at": "2026-01-01",
        "title": "t",
        "tags": [{"name": f"auto{i}", "source": "auto"} for i in range(20)]
        + [{"name": "mine", "source": "user"}],
        "content_rating": "r",
        "group": {"id": "g", "name": "G"},
    }
    out = llm_view.compact_asset(entry, external=True)
    assert out == {
        "asset_id": "a",
        "kind": "upload",
        "title": "t",
        "tags": ["mine", *[f"auto{i}" for i in range(11)]],
        "content_rating": "r",
        "group": "G",
    }
    withheld = llm_view.compact_asset({**entry, "content_rating": "xx"}, external=True)
    assert withheld == {
        "asset_id": "a",
        "kind": "upload",
        "content_rating": "xx",
        "group": "G",
        "withheld": True,
    }
    assert "title" in llm_view.compact_asset({**entry, "content_rating": "xx"}, external=False)


def test_paged_cuts_by_budget() -> None:
    items = [{"asset_id": str(i), "title": "x" * 100} for i in range(50)]
    result = llm_view.paged(items, offset=10, has_more=False, total=60, budget=1_500)
    assert 0 < result["returned"] < 50
    assert result["next_offset"] == 10 + result["returned"]
    assert result["total"] == 60
    assert len(json.dumps(result)) <= 1_500 + 200
    whole = llm_view.paged(items[:3], offset=0, has_more=False, total=3)
    assert whole["returned"] == 3 and whole["next_offset"] is None


def test_cap_text_marks_truncated() -> None:
    text = json.dumps({"items": ["x" * 100] * 500})
    capped = llm_view.cap_text(text, 2_000)
    assert len(capped) <= 2_000
    first = json.loads(capped.splitlines()[0])
    assert first["truncated"] is True
    assert llm_view.cap_text("short", 2_000) == "short"


def test_strip_display_drops_screen_only_values() -> None:
    stock = {"operation": "add_tags", "count": 2, "preview": [{"asset_id": "a"}]}
    assert "preview" not in llm_view.strip_display("add_tags", stock)
    assert "preview" in stock  # 保存した値は変えない
    groups = {
        "items": [
            {
                "id": "g",
                "name": "G",
                "position": 0,
                "member_count": 3,
                "cover_asset_id": "c",
                "created_at": "t",
                "updated_at": "t",
            }
        ]
    }
    assert llm_view.strip_display("list_groups", groups)["items"] == [
        {"id": "g", "name": "G", "member_count": 3}
    ]
    shown = {"assets": [{"asset_id": "a", "title": "t", "focal_point": {"x": 0}, "width": 1}]}
    assert llm_view.strip_display("show_assets", shown)["assets"] == [
        {"asset_id": "a", "title": "t"}
    ]


def test_compact_payload_keeps_ids_and_counts() -> None:
    ids = [str(uuid.uuid4()) for _ in range(3)]
    payload = {
        "mode": "keyword",
        "returned": 3,
        "next_offset": None,
        "items": [{"asset_id": i, "title": "secret", "tags": ["a"]} for i in ids],
        "note": "long note",
    }
    out = llm_view.compact_payload(payload, reason="r")
    assert out["asset_ids"] == ids and out["asset_count"] == 3
    assert out["mode"] == "keyword" and out["returned"] == 3
    assert "secret" not in json.dumps(out)
    assert "items" not in out and "note" not in out


# -- 履歴の古い結果(14.5) -------------------------------------------------------------


def _row(position: int, role: str, content: dict[str, Any]) -> AssistantMessage:
    return AssistantMessage(
        id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        position=position,
        role=role,
        content=content,
        created_at=datetime.now(UTC),
    )


def _big_result(asset_id: str) -> dict[str, Any]:
    return {"items": [{"asset_id": asset_id, "title": "t" * 3_000}]}


def test_old_long_tool_results_are_compacted() -> None:
    user = _row(0, "user", {"text": "go", "attachments": []})
    rows = [user]
    ids = [str(uuid.uuid4()) for _ in range(5)]
    for i, asset_id in enumerate(ids):
        call_id = f"c{i}"
        rows.append(
            _row(
                len(rows),
                "assistant",
                {"text": "", "tool_calls": [{"id": call_id, "name": "search_assets"}]},
            )
        )
        rows.append(
            _row(
                len(rows),
                "tool",
                {
                    "tool_call_id": call_id,
                    "name": "search_assets",
                    "ok": True,
                    "result": _big_result(asset_id),
                },
            )
        )
    history = build_history(rows, user.id, {}, max_chars=1_000_000)
    tools = [m for m in history if m.role == "tool"]
    assert len(tools) == 5
    for message, asset_id in zip(tools[:2], ids[:2], strict=True):
        data = json.loads(message.text)
        assert data["compacted"] is True and data["asset_ids"] == [asset_id]
        assert len(message.text) < 1_000
    for message in tools[2:]:
        assert "compacted" not in message.text and len(message.text) > 3_000
    # 保存した値は変えない。
    assert len(rows[2 * 1 + 2].content["result"]["items"][0]["title"]) == 3_000


def test_results_made_for_a_local_connection_are_not_sent_to_external() -> None:
    content = {
        "name": "get_asset",
        "ok": True,
        "result": {"asset_id": str(uuid.uuid4()), "title": "secret title"},
        "external": False,
    }
    assert "secret" in _tool_text(content, external=False)
    sent = _tool_text(content, external=True)
    assert "secret" not in sent and json.loads(sent)["compacted"] is True


def test_llm_result_is_preferred_over_the_screen_result() -> None:
    content = {
        "name": "show_assets",
        "ok": True,
        "result": {"assets": [{"asset_id": "a", "title": "secret"}]},
        "llm_result": {"assets": [{"asset_id": "a", "withheld": True}]},
    }
    assert "secret" not in _tool_text(content, external=True)


# -- 道具(HTTP。外部の接続先が既定) --------------------------------------------------


def _enable(client: TestClient) -> None:
    assert client.patch(SETTINGS, json={"enabled": True}).status_code == 200


def _upload(client: TestClient, color: tuple[int, int, int] = (10, 120, 200)) -> str:
    response = client.post(
        "/api/assets",
        files={"file": ("x.png", io.BytesIO(make_png_bytes(32, 32, color)), "image/png")},
        data={"kind": "upload"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _tag(client: TestClient, asset_id: str, name: str) -> None:
    assert client.post(f"/api/assets/{asset_id}/tags", json={"name": name}).status_code == 200


def _title(client: TestClient, asset_id: str, title: str) -> None:
    response = client.patch(f"/api/assets/{asset_id}/title", json={"title": title})
    assert response.status_code == 200, response.text


def _rate(client: TestClient, asset_id: str, level: str) -> None:
    with client.app.state.session_factory() as db:
        asset = db.get(Asset, uuid.UUID(asset_id))
        content_rating_domain.set_user_level(db, asset, level)
        db.commit()


def _set_local(client: TestClient) -> None:
    with client.app.state.session_factory() as db:
        locality_domain.save_provider_localities(db, {"openai": "local"})
        db.commit()


def _parse_sse(text: str) -> list[dict]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in text.splitlines()
        if line.startswith("data: ")
    ]


def _send(client: TestClient, text: str, **body: Any) -> list[dict]:
    conversation = client.post(CONVERSATIONS, json={"title": ""}).json()["id"]
    response = client.post(f"{CONVERSATIONS}/{conversation}/messages", json={"text": text, **body})
    assert response.status_code == 200, response.text
    return _parse_sse(response.text)


def _tool(client: TestClient, name: str, args: dict | None = None) -> dict[str, Any]:
    events = _send(client, f"/tool {name} {json.dumps(args or {})}")
    results = [e["message"] for e in events if e["type"] == "tool_result"]
    assert len(results) == 1 and results[0]["ok"] is True, results
    return results[0]["result"]


def _engine(client: TestClient) -> FakeAssistantEngine:
    return client.app.state.assistant.engine


def _last_tool_text(client: TestClient) -> str:
    return next(m.text for m in reversed(_engine(client).requests[-1].messages) if m.role == "tool")


def test_x_rated_images_are_withheld_for_external_connections(client: TestClient) -> None:
    _enable(client)
    safe = _upload(client, (10, 10, 10))
    adult = _upload(client, (200, 10, 10))
    for asset_id in (safe, adult):
        _tag(client, asset_id, "secrettag" if asset_id == adult else "safetag")
        _title(client, asset_id, "secret title" if asset_id == adult else "safe title")
    _rate(client, adult, "xx")

    found = _tool(client, "search_assets", {"mode": "keyword"})
    items = {i["asset_id"]: i for i in found["items"]}
    assert items[adult] == {
        "asset_id": adult,
        "kind": "upload",
        "content_rating": "xx",
        "group": None,
        "withheld": True,
    }
    assert items[safe]["title"] == "safe title"
    assert "secret" not in _last_tool_text(client)

    info = _tool(client, "get_asset", {"asset_id": adult})
    assert info["withheld"] is True and "title" not in info

    # 画像は送らず、許可も聞かない。
    events = _send(client, f'/tool view_assets {{"asset_ids": ["{adult}"]}}')
    assert not any(e["type"] == "permission_request" for e in events)
    viewed = next(e["message"] for e in events if e["type"] == "tool_result")
    assert viewed["result"]["assets"][0]["withheld"] is True
    assert "withheld_note" in viewed["result"]
    assert _engine(client).requests[-1].messages[-1].images == []

    # show_assets: 画面にはタイトルを返し、LLM には送らない。
    shown = _tool(client, "show_assets", {"asset_ids": [adult, safe]})
    assert shown["assets"][0]["title"] == "secret title"
    assert "secret" not in _last_tool_text(client)

    # list_tags は X 以上を数えない。
    tags = {t["name"] for t in _tool(client, "list_tags")["items"]}
    assert "secrettag" not in tags and "safetag" in tags

    # 書き込みは ID で働くが、前後のタイトルは送らない。
    written = _tool(client, "set_title", {"asset_id": adult, "title": "new secret"})
    assert written["after"]["title"] == "new secret"
    assert "secret" not in _last_tool_text(client)

    # 添付も送らず、送らなかったことを記録する。
    events = _send(client, "見て", attachments=[adult, safe])
    user = events[0]["message"]
    assert user["withheld_attachments"] == [adult]
    first = _engine(client).requests[-1].messages[0]
    assert [image.label for image in first.images] == [safe]


def test_local_connections_are_not_filtered(client: TestClient) -> None:
    _enable(client)
    _set_local(client)
    adult = _upload(client)
    _tag(client, adult, "secrettag")
    _rate(client, adult, "xxx")
    found = _tool(client, "search_assets", {"mode": "keyword"})
    assert "withheld" not in found["items"][0]
    assert "secrettag" in found["items"][0]["tags"]
    assert "secrettag" in {t["name"] for t in _tool(client, "list_tags")["items"]}


def test_unrated_images_are_not_filtered(client: TestClient) -> None:
    _enable(client)
    asset_id = _upload(client)
    _title(client, asset_id, "plain")
    found = _tool(client, "search_assets", {"mode": "keyword"})
    assert found["items"][0]["title"] == "plain"


def test_recent_results_withhold_prompts_of_x_rated_runs(client: TestClient) -> None:
    _enable(client)
    response = client.post(
        "/api/runs",
        json={"operation": "generate", "model": _MODEL, "prompt": "hidden words", "params": {}},
    )
    detail = wait_for_run_terminal(client, response.json()["id"])
    output = detail["outputs"][0]["asset_id"]
    _rate(client, output, "x")
    result = _tool(client, "list_recent_results")
    assert result["items"][0]["prompt"] is None and result["items"][0]["withheld"] is True
    assert result["items"][0]["output_asset_ids"] == [output]
    run = _tool(client, "get_run", {"run_id": detail["id"]})
    assert run["prompt"] is None and run["withheld"] is True


# -- 件数での打ち切りと list_group_assets(14.4) ------------------------------------------


def test_list_group_assets_and_budget(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(client)
    ids = [_upload(client, (i * 40, 20, 20)) for i in range(5)]
    group = client.post("/api/asset-groups", json={"name": "box"}).json()["id"]
    moved = client.post(f"/api/asset-groups/{group}/assets", json={"asset_ids": ids})
    assert moved.status_code == 200

    whole = _tool(client, "list_group_assets", {"group_id": group})
    assert whole["group"] == {"id": group, "name": "box"}
    assert whole["returned"] == 5 and whole["total"] == 5 and whole["next_offset"] is None
    assert {i["asset_id"] for i in whole["items"]} == set(ids)
    assert all(i["group"] == "box" for i in whole["items"])

    page = _tool(client, "list_group_assets", {"group_id": group, "limit": 2, "offset": 2})
    assert page["returned"] == 2 and page["next_offset"] == 4

    # 予算を小さくすると、件数で切って next_offset を返す(JSON の途中では切らない)。
    monkeypatch.setattr(llm_view, "LIST_BUDGET_CHARS", 700)
    cut = _tool(client, "list_group_assets", {"group_id": group})
    assert 0 < cut["returned"] < 5
    assert cut["next_offset"] == cut["returned"]
    assert "more_note" in cut

    events = _send(client, f'/tool list_group_assets {{"group_id": "{uuid.uuid4()}"}}')
    result = next(e["message"] for e in events if e["type"] == "tool_result")
    assert result["ok"] is False and "not found" in result["result"]["error"]


def test_list_groups_sends_no_screen_values(client: TestClient) -> None:
    _enable(client)
    asset_id = _upload(client)
    group = client.post("/api/asset-groups", json={"name": "g1"}).json()["id"]
    client.post(f"/api/asset-groups/{group}/assets", json={"asset_ids": [asset_id]})
    result = _tool(client, "list_groups")
    # 保存した結果(画面)には残す。
    assert "cover_asset_id" in result["items"][0]
    sent = json.loads(_last_tool_text(client))
    assert sent["items"] == [{"id": group, "name": "g1", "member_count": 1}]
