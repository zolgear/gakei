"""アシスタントのストックの捜索(ADR-0048 10章)。Fake のエンジンの `/tool` 行で道具を呼び、
結果の形、見える範囲(ADR-0025)、埋め込みが無効のときのエラーを確かめる。実 API は呼ばない。"""

from __future__ import annotations

import io
import json
import time
import uuid
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import AssetEmbedding
from tests.conftest import login_as, make_png_bytes, wait_for_run_terminal

SETTINGS = "/api/settings/assistant"
CONVERSATIONS = "/api/assistant/conversations"
_MODEL = "gpt-image-2.5-sunburst"


def _enable(client: TestClient) -> None:
    response = client.patch(SETTINGS, json={"enabled": True})
    assert response.status_code == 200, response.text


def _enable_embeddings(client: TestClient) -> None:
    response = client.patch("/api/settings/embeddings", json={"enabled": True})
    assert response.status_code == 200, response.text


def _parse_sse(text: str) -> list[dict]:
    events: list[dict] = []
    for block in text.strip().split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line.removeprefix("data: ")))
    return events


def _call(client: TestClient, name: str, args: dict | None = None) -> dict[str, Any]:
    """新しい会話で道具を1つ呼び、その tool の結果(`ok` と `result`)を返す。"""
    conversation = client.post(CONVERSATIONS, json={"title": ""})
    assert conversation.status_code == 201, conversation.text
    response = client.post(
        f"{CONVERSATIONS}/{conversation.json()['id']}/messages",
        json={"text": f"/tool {name} {json.dumps(args or {})}"},
    )
    assert response.status_code == 200, response.text
    results = [e["message"] for e in _parse_sse(response.text) if e["type"] == "tool_result"]
    assert len(results) == 1, results
    return results[0]


def _ok(client: TestClient, name: str, args: dict | None = None) -> dict[str, Any]:
    message = _call(client, name, args)
    assert message["ok"] is True, message
    return message["result"]


def _error(client: TestClient, name: str, args: dict | None = None) -> str:
    message = _call(client, name, args)
    assert message["ok"] is False, message
    return message["result"]["error"]


def _upload(client: TestClient, color: tuple[int, int, int]) -> str:
    response = client.post(
        "/api/assets",
        files={"file": ("x.png", io.BytesIO(make_png_bytes(64, 48, color)), "image/png")},
        data={"kind": "upload"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _generate(client: TestClient, prompt: str) -> str:
    response = client.post(
        "/api/runs",
        json={"operation": "generate", "model": _MODEL, "prompt": prompt, "params": {"n": 1}},
    )
    assert response.status_code == 202, response.text
    detail = wait_for_run_terminal(client, response.json()["id"])
    assert detail["status"] == "succeeded", detail
    return detail["outputs"][0]["asset_id"]


def _tag(client: TestClient, asset_id: str, name: str) -> None:
    response = client.post(f"/api/assets/{asset_id}/tags", json={"name": name})
    assert response.status_code == 200, response.text


def _group(client: TestClient, name: str, asset_ids: list[str]) -> str:
    response = client.post("/api/asset-groups", json={"name": name})
    assert response.status_code == 201, response.text
    group_id = response.json()["id"]
    moved = client.post(f"/api/asset-groups/{group_id}/assets", json={"asset_ids": asset_ids})
    assert moved.status_code == 200, moved.text
    return group_id


def _wait_embedded(client: TestClient, *asset_ids: str) -> None:
    model_key = client.get("/api/capabilities").json()["embeddings"]["model_key"]
    pending = {uuid.UUID(a) for a in asset_ids}
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        with client.app.state.session_factory() as db:
            done = db.execute(
                select(AssetEmbedding.asset_id).where(
                    AssetEmbedding.model_key == model_key,
                    AssetEmbedding.status == "succeeded",
                    AssetEmbedding.asset_id.in_(pending),
                )
            ).all()
        pending -= {row[0] for row in done}
        if not pending:
            return
        time.sleep(0.05)
    raise TimeoutError(f"埋め込みが終わりませんでした: {pending}")


def _ids(result: dict[str, Any]) -> list[str]:
    return [item["asset_id"] for item in result["items"]]


# -- キーワードの検索、画像の情報、グループ、タグ -------------------------------------------


def test_keyword_search_filters_and_result_shape(client: TestClient) -> None:
    _enable(client)
    zebra = _generate(client, "zebra lighthouse at dusk")
    _generate(client, "mountain cabin")
    upload = _upload(client, (200, 30, 30))
    _tag(client, upload, "redsquare")
    group_id = _group(client, "reds", [upload])

    result = _ok(client, "search_assets", {"query": "zebra"})
    assert result["mode"] == "keyword"
    assert _ids(result) == [zebra]
    item = result["items"][0]
    assert set(item) >= {"asset_id", "kind", "width", "height", "title", "tags", "group"}
    assert item["kind"] == "generated"
    # 画像の中身と URL は入れない。
    dumped = json.dumps(result)
    assert "base64" not in dumped and "/content" not in dumped

    newest = _ok(client, "search_assets", {"limit": 2})
    assert len(newest["items"]) == 2 and newest["truncated"] is True

    assert _ids(_ok(client, "search_assets", {"kind": "upload"})) == [upload]
    assert _ids(_ok(client, "search_assets", {"tag": "redsquare"})) == [upload]
    in_group = _ok(client, "search_assets", {"group_id": group_id})
    assert _ids(in_group) == [upload]
    assert in_group["items"][0]["group"]["name"] == "reds"

    error = _error(client, "search_assets", {"group_id": str(uuid.uuid4())})
    assert "not found" in error
    assert "limit" in _error(client, "search_assets", {"limit": 51})


def test_get_asset_list_groups_and_list_tags(client: TestClient) -> None:
    _enable(client)
    parent = _upload(client, (10, 10, 200))
    response = client.post(
        "/api/runs",
        json={
            "operation": "edit",
            "model": _MODEL,
            "prompt": "make it blue sky",
            "params": {"n": 1},
            "inputs": [{"asset_id": parent, "role": "image", "position": 0}],
        },
    )
    assert response.status_code == 202, response.text
    detail = wait_for_run_terminal(client, response.json()["id"])
    child = detail["outputs"][0]["asset_id"]
    _tag(client, child, "skyline")
    _tag(client, parent, "skyline")
    _tag(client, parent, "sea")
    _group(client, "blues", [parent])

    info = _ok(client, "get_asset", {"asset_id": child})
    assert info["asset_id"] == child
    assert info["produced_by_run"]["prompt"] == "make it blue sky"
    assert info["produced_by_run"]["model"] == _MODEL
    assert info["primary_parent_asset_id"] == parent
    assert [t["name"] for t in info["tags"]] == ["skyline"]
    assert "not found" in _error(client, "get_asset", {"asset_id": str(uuid.uuid4())})

    groups = _ok(client, "list_groups")
    assert [(g["name"], g["member_count"]) for g in groups["items"]] == [("blues", 1)]

    tags = _ok(client, "list_tags")
    assert tags["items"][0] == {"name": "skyline", "count": 2}
    # 前方一致(部分一致ではない)。
    assert [t["name"] for t in _ok(client, "list_tags", {"prefix": "sk"})["items"]] == ["skyline"]
    assert _ok(client, "list_tags", {"prefix": "kyl"})["items"] == []


# -- show_assets ---------------------------------------------------------------------


def test_show_assets_result_shape(client: TestClient) -> None:
    _enable(client)
    first = _upload(client, (1, 2, 3))
    second = _upload(client, (4, 5, 6))
    result = _ok(client, "show_assets", {"asset_ids": [second, first, second], "title": "候補"})
    assert result["shown"] is True
    assert result["title"] == "候補"
    # 並びは指定どおり、重複は1つにまとめる。
    assert [a["asset_id"] for a in result["assets"]] == [second, first]
    entry = result["assets"][0]
    assert entry["width"] == 64 and entry["height"] == 48
    assert entry["kind"] == "upload"
    assert "title" in entry and "focal_point" in entry

    missing = str(uuid.uuid4())
    assert missing in _error(client, "show_assets", {"asset_ids": [first, missing]})
    assert client.delete(f"/api/assets/{first}").status_code in (200, 204)
    assert first in _error(client, "show_assets", {"asset_ids": [first]})


# -- 見える範囲(ADR-0025) ------------------------------------------------------------


def test_other_users_assets_are_not_visible(client_oidc: TestClient) -> None:
    client = client_oidc
    login_as(client, "admin@example.com")
    _enable(client)
    login_as(client, "bob@example.com")
    bob = _upload(client, (20, 40, 230))
    _tag(client, bob, "bobtag")
    bob_group = _group(client, "bob group", [bob])
    login_as(client, "alice@example.com")
    alice = _upload(client, (220, 30, 30))

    assert _ids(_ok(client, "search_assets")) == [alice]
    assert _ok(client, "search_assets", {"tag": "bobtag"})["items"] == []
    assert "not found" in _error(client, "search_assets", {"group_id": bob_group})
    assert "not found" in _error(client, "get_asset", {"asset_id": bob})
    assert bob in _error(client, "show_assets", {"asset_ids": [alice, bob]})
    assert _ok(client, "list_groups")["items"] == []
    assert _ok(client, "list_tags")["items"] == []


# -- 埋め込み ---------------------------------------------------------------------------


def test_semantic_and_similar_fail_when_embeddings_are_off(client: TestClient) -> None:
    _enable(client)
    asset_id = _upload(client, (200, 30, 30))
    error = _error(client, "search_assets", {"mode": "semantic", "query": "red square"})
    assert "keyword" in error
    error = _error(client, "find_similar_assets", {"asset_id": asset_id})
    assert "not available" in error
    # keyword には切り替えられる。
    assert _ids(_ok(client, "search_assets")) == [asset_id]


def test_semantic_search_and_similar(client: TestClient) -> None:
    _enable(client)
    _enable_embeddings(client)
    red = _upload(client, (220, 30, 30))
    red2 = _upload(client, (222, 31, 30))
    blue = _upload(client, (20, 40, 230))
    _wait_embedded(client, red, red2, blue)

    result = _ok(client, "search_assets", {"mode": "semantic", "query": "red square"})
    assert result["mode"] == "semantic"
    assert sorted(_ids(result)) == sorted([red, red2, blue])
    assert all(isinstance(item["score"], float) for item in result["items"])
    assert "query" in _error(client, "search_assets", {"mode": "semantic"})

    similar = _ok(client, "find_similar_assets", {"asset_id": red, "limit": 5})
    assert red not in _ids(similar)
    assert _ids(similar)[0] == red2
    assert "not found" in _error(client, "find_similar_assets", {"asset_id": str(uuid.uuid4())})


def test_semantic_search_hides_other_users_assets(client_oidc: TestClient) -> None:
    client = client_oidc
    login_as(client, "admin@example.com")
    _enable(client)
    _enable_embeddings(client)
    login_as(client, "bob@example.com")
    bob = _upload(client, (221, 30, 31))
    login_as(client, "alice@example.com")
    alice = _upload(client, (220, 30, 30))
    _wait_embedded(client, alice, bob)

    result = _ok(client, "search_assets", {"mode": "semantic", "query": "red"})
    assert _ids(result) == [alice]
    assert _ids(_ok(client, "find_similar_assets", {"asset_id": alice})) == []
    assert "not found" in _error(client, "find_similar_assets", {"asset_id": bob})
