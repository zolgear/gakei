"""アシスタントのストックの整理(ADR-0048 13章)。Fake のエンジンの `/tool` 行で道具を呼び、
書き込み(人の編集と同じ扱い)、見える範囲(ADR-0025)、1回の上限、画像ごとの「元に戻す」、
`selected_asset_ids`、`suggest_groups` を確かめる。実 API は呼ばない。"""

from __future__ import annotations

import io
import json
import uuid
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain import annotations as annotations_domain
from app.domain.models import (
    Asset,
    AssetAnnotation,
    AssetGroup,
    AssetGroupMember,
    AssetTag,
    Tag,
)
from tests.conftest import login_as, make_png_bytes
from tests.test_group_suggestions import _setup as _setup_clusters

SETTINGS = "/api/settings/assistant"
CONVERSATIONS = "/api/assistant/conversations"
UNDO = "/api/assistant/undo"


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


def _call(
    client: TestClient, name: str, args: dict | None = None, form: dict | None = None
) -> dict[str, Any]:
    """新しい会話で道具を1つ呼び、その tool のメッセージを返す。"""
    conversation = client.post(CONVERSATIONS, json={"title": ""})
    assert conversation.status_code == 201, conversation.text
    body: dict[str, Any] = {"text": f"/tool {name} {json.dumps(args or {})}"}
    if form is not None:
        body["form"] = form
    response = client.post(f"{CONVERSATIONS}/{conversation.json()['id']}/messages", json=body)
    assert response.status_code == 200, response.text
    results = [e["message"] for e in _parse_sse(response.text) if e["type"] == "tool_result"]
    assert len(results) == 1, results
    return results[0]


def _ok(client: TestClient, name: str, args: dict | None = None) -> dict[str, Any]:
    message = _call(client, name, args)
    assert message["ok"] is True, message
    return message


def _error(client: TestClient, name: str, args: dict | None = None) -> str:
    message = _call(client, name, args)
    assert message["ok"] is False, message
    return message["result"]["error"]


_COLOR = iter(range(1, 10_000))


def _upload(client: TestClient, kind: str = "upload") -> str:
    n = next(_COLOR)
    response = client.post(
        "/api/assets",
        files={
            "file": (
                "x.png",
                io.BytesIO(make_png_bytes(32, 24, (n % 256, n // 256, 7))),
                "image/png",
            )
        },
        data={"kind": kind},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _undo(client: TestClient, message_id: str) -> dict[str, Any]:
    response = client.post(f"{UNDO}/{message_id}")
    assert response.status_code == 200, response.text
    return response.json()


def _tag_state(client: TestClient, asset_id: str, name: str) -> dict[str, Any] | None:
    with client.app.state.session_factory() as db:
        row = db.execute(
            select(AssetTag)
            .join(Tag, Tag.id == AssetTag.tag_id)
            .where(AssetTag.asset_id == uuid.UUID(asset_id), Tag.name == name)
        ).scalar_one_or_none()
        if row is None:
            return None
        return {"source": row.source, "score": row.score, "removed": row.removed}


def _apply_auto(client: TestClient, asset_id: str, tags: list[tuple[str, float]]) -> None:
    with client.app.state.session_factory() as db:
        annotations_domain.apply_auto_result(db, uuid.UUID(asset_id), title="auto", tags=tags)
        db.commit()


def _membership(client: TestClient, asset_id: str) -> str | None:
    with client.app.state.session_factory() as db:
        group_id = db.execute(
            select(AssetGroupMember.asset_group_id)
            .join(AssetGroup, AssetGroup.id == AssetGroupMember.asset_group_id)
            .where(
                AssetGroupMember.asset_id == uuid.UUID(asset_id), AssetGroup.deleted_at.is_(None)
            )
        ).scalar_one_or_none()
    return str(group_id) if group_id else None


def _group(client: TestClient, name: str, asset_ids: list[str]) -> str:
    response = client.post("/api/asset-groups", json={"name": name})
    assert response.status_code == 201, response.text
    group_id = response.json()["id"]
    if asset_ids:
        moved = client.post(f"/api/asset-groups/{group_id}/assets", json={"asset_ids": asset_ids})
        assert moved.status_code == 200, moved.text
    return group_id


# -- タグ ---------------------------------------------------------------------------


def test_add_tags_is_a_user_edit_and_undo_restores_each_row(client: TestClient) -> None:
    _enable(client)
    fresh, auto, user, removed = (_upload(client) for _ in range(4))
    _apply_auto(client, auto, [("sky", 0.75)])
    assert client.post(f"/api/assets/{user}/tags", json={"name": "sky"}).status_code == 200
    assert client.post(f"/api/assets/{removed}/tags", json={"name": "sky"}).status_code == 200
    assert client.delete(f"/api/assets/{removed}/tags/sky").status_code == 200

    message = _ok(client, "add_tags", {"asset_ids": [fresh, auto, user, removed], "tags": ["Sky"]})
    result = message["result"]
    assert result["operation"] == "add_tags" and result["tags"] == ["sky"]
    assert result["count"] == 4 and result["changed"] == 3 and result["unchanged"] == 1
    assert {p["asset_id"] for p in result["preview"]} == {fresh, auto, removed}
    assert message["undoable"] is True
    for asset_id in (fresh, auto, user, removed):
        assert _tag_state(client, asset_id, "sky") == {
            "source": "user",
            "score": None,
            "removed": False,
        }

    undone = _undo(client, message["id"])
    assert undone["undo_result"] == {
        "restored": 3,
        "skipped": 0,
        "group_kept": False,
        "group_deleted": False,
    }
    assert undone["message"]["undoable"] is False
    assert undone["message"]["undo_result"]["restored"] == 3
    assert _tag_state(client, fresh, "sky") is None
    assert _tag_state(client, auto, "sky") == {"source": "auto", "score": 0.75, "removed": False}
    assert _tag_state(client, user, "sky") == {"source": "user", "score": None, "removed": False}
    assert _tag_state(client, removed, "sky") == {"source": "user", "score": None, "removed": True}
    # 二度は戻せない。
    assert client.post(f"{UNDO}/{message['id']}").status_code == 409


def test_remove_auto_tag_then_undo_returns_to_auto(client: TestClient) -> None:
    _enable(client)
    asset = _upload(client)
    other = _upload(client)
    _apply_auto(client, asset, [("cat", 0.5)])
    message = _ok(client, "remove_tags", {"asset_ids": [asset, other], "tags": ["cat"]})
    assert message["result"]["changed"] == 1 and message["result"]["unchanged"] == 1
    state = _tag_state(client, asset, "cat")
    assert state is not None and state["removed"] is True and state["source"] == "user"
    # 外したタグは画面にも出ない。
    detail = client.get(f"/api/assets/{asset}").json()
    assert "cat" not in [t["name"] for t in detail["tags"]]

    _undo(client, message["id"])
    assert _tag_state(client, asset, "cat") == {"source": "auto", "score": 0.5, "removed": False}
    # 自動のタグに戻ったので、再推定で入れ替わる。
    with client.app.state.session_factory() as db:
        annotations_domain.apply_auto_result(db, uuid.UUID(asset), title=None, tags=[("dog", 0.9)])
        db.commit()
    assert _tag_state(client, asset, "cat") is None


def test_undo_skips_images_changed_afterwards(client: TestClient) -> None:
    _enable(client)
    a, b, c = (_upload(client) for _ in range(3))
    message = _ok(client, "add_tags", {"asset_ids": [a, b, c], "tags": ["sea"]})
    # b はその後に人がタグを外した。
    assert client.delete(f"/api/assets/{b}/tags/sea").status_code == 200
    undone = _undo(client, message["id"])
    assert undone["undo_result"]["restored"] == 2 and undone["undo_result"]["skipped"] == 1
    assert _tag_state(client, a, "sea") is None
    assert _tag_state(client, b, "sea") == {"source": "user", "score": None, "removed": True}


def test_undo_conflicts_when_every_image_changed(client: TestClient) -> None:
    _enable(client)
    a = _upload(client)
    message = _ok(client, "add_tags", {"asset_ids": [a], "tags": ["sea"]})
    assert client.delete(f"/api/assets/{a}/tags/sea").status_code == 200
    response = client.post(f"{UNDO}/{message['id']}")
    assert response.status_code == 409
    # 戻していないので、まだ戻せる扱い(後で状態が戻れば戻せる)。
    conversation_messages = client.get(f"{CONVERSATIONS}/{message['conversation_id']}").json()[
        "messages"
    ]
    tool = next(m for m in conversation_messages if m["id"] == message["id"])
    assert tool["undoable"] is True and tool["undo_result"] is None


def test_tags_reject_invisible_masks_and_too_many(client: TestClient) -> None:
    _enable(client)
    asset = _upload(client)
    mask = _upload(client, kind="mask")
    missing = str(uuid.uuid4())
    error = _error(client, "add_tags", {"asset_ids": [asset, missing], "tags": ["x"]})
    assert missing in error
    assert _tag_state(client, asset, "x") is None
    assert "Mask" in _error(client, "add_tags", {"asset_ids": [mask], "tags": ["x"]})
    too_many = [str(uuid.uuid4()) for _ in range(201)]
    assert "Invalid arguments" in _error(client, "add_tags", {"asset_ids": too_many, "tags": ["x"]})
    assert "Invalid tag" in _error(client, "add_tags", {"asset_ids": [asset], "tags": ["  "]})


def test_nothing_changed_is_not_undoable(client: TestClient) -> None:
    _enable(client)
    asset = _upload(client)
    message = _ok(client, "remove_tags", {"asset_ids": [asset], "tags": ["none"]})
    assert message["result"]["changed"] == 0
    assert message["undoable"] is False


# -- タイトル -------------------------------------------------------------------------


def test_set_title_is_user_edit_and_undo(client: TestClient) -> None:
    _enable(client)
    asset = _upload(client)
    _apply_auto(client, asset, [])
    message = _ok(client, "set_title", {"asset_id": asset, "title": "  夕方の海  "})
    assert message["result"]["before"] == {"title": "auto"}
    assert message["result"]["after"] == {"title": "夕方の海"}
    with client.app.state.session_factory() as db:
        row = db.get(AssetAnnotation, uuid.UUID(asset))
        assert row is not None and row.title == "夕方の海" and row.title_source == "user"
    # 再推定で上書きしない。
    with client.app.state.session_factory() as db:
        annotations_domain.apply_auto_result(db, uuid.UUID(asset), title="other", tags=None)
        db.commit()
    assert client.get(f"/api/assets/{asset}").json()["title"] == "夕方の海"

    _undo(client, message["id"])
    with client.app.state.session_factory() as db:
        row = db.get(AssetAnnotation, uuid.UUID(asset))
        assert row is not None and row.title == "auto" and row.title_source == "auto"


def test_set_title_clear_and_conflict(client: TestClient) -> None:
    _enable(client)
    asset = _upload(client)
    message = _ok(client, "set_title", {"asset_id": asset, "title": None})
    # 注釈の行が無かった → タイトル無し・人の決定(空)として残る。
    with client.app.state.session_factory() as db:
        row = db.get(AssetAnnotation, uuid.UUID(asset))
        assert row is not None and row.title is None and row.title_source == "user"
    assert message["undoable"] is True
    # その後に人が編集したら戻さない。
    assert client.patch(f"/api/assets/{asset}/title", json={"title": "人"}).status_code == 200
    assert client.post(f"{UNDO}/{message['id']}").status_code == 409


# -- グループ ---------------------------------------------------------------------------


def test_create_group_with_assets_and_undo_restores_memberships(client: TestClient) -> None:
    _enable(client)
    a, b, c = (_upload(client) for _ in range(3))
    old = _group(client, "Old", [a])
    message = _ok(client, "create_group", {"name": "  New  ", "asset_ids": [a, b, c]})
    result = message["result"]
    new = result["group"]["id"]
    assert result["group"]["name"] == "New"
    assert result["changed"] == 3 and result["moved_from_other_groups"] == 1
    assert {_membership(client, x) for x in (a, b, c)} == {new}

    undone = _undo(client, message["id"])
    assert undone["undo_result"]["restored"] == 3 and undone["undo_result"]["group_deleted"]
    assert _membership(client, a) == old
    assert _membership(client, b) is None and _membership(client, c) is None
    with client.app.state.session_factory() as db:
        group = db.get(AssetGroup, uuid.UUID(new))
        assert group is not None and group.deleted_at is not None


def test_undo_created_group_kept_when_others_were_added(client: TestClient) -> None:
    _enable(client)
    a, b = _upload(client), _upload(client)
    message = _ok(client, "create_group", {"name": "G", "asset_ids": [a]})
    new = message["result"]["group"]["id"]
    added = client.post(f"/api/asset-groups/{new}/assets", json={"asset_ids": [b]})
    assert added.status_code == 200
    undone = _undo(client, message["id"])
    assert undone["undo_result"]["restored"] == 1 and undone["undo_result"]["group_kept"]
    assert _membership(client, a) is None and _membership(client, b) == new


def test_move_and_remove_from_group_and_partial_undo(client: TestClient) -> None:
    _enable(client)
    a, b, c = (_upload(client) for _ in range(3))
    first = _group(client, "First", [a])
    second = _group(client, "Second", [c])
    message = _ok(client, "move_to_group", {"group_id": second, "asset_ids": [a, b, c]})
    result = message["result"]
    assert result["changed"] == 2 and result["already_in_group"] == 1
    assert result["moved_from_other_groups"] == 1
    # b はその後に人が別のグループへ移した。
    assert (
        client.post(f"/api/asset-groups/{first}/assets", json={"asset_ids": [b]}).status_code == 200
    )
    undone = _undo(client, message["id"])
    assert undone["undo_result"]["restored"] == 1 and undone["undo_result"]["skipped"] == 1
    assert _membership(client, a) == first
    assert _membership(client, b) == first
    assert _membership(client, c) == second

    removed = _ok(client, "remove_from_group", {"group_id": second, "asset_ids": [c, a]})
    assert removed["result"]["changed"] == 1 and removed["result"]["not_in_group"] == 1
    assert _membership(client, c) is None
    _undo(client, removed["id"])
    assert _membership(client, c) == second


def test_rename_group_and_undo(client: TestClient) -> None:
    _enable(client)
    group = _group(client, "Before", [])
    message = _ok(client, "rename_group", {"group_id": group, "name": "After"})
    assert message["result"]["before"] == {"name": "Before"}
    _undo(client, message["id"])
    names = {g["id"]: g["name"] for g in client.get("/api/asset-groups").json()["items"]}
    assert names[group] == "Before"


def test_group_tools_reject_unknown_group_and_assets(client: TestClient) -> None:
    _enable(client)
    asset = _upload(client)
    unknown = str(uuid.uuid4())
    assert "not found" in _error(
        client, "move_to_group", {"group_id": unknown, "asset_ids": [asset]}
    )
    group = _group(client, "G", [])
    assert unknown in _error(client, "move_to_group", {"group_id": group, "asset_ids": [unknown]})
    assert _membership(client, asset) is None
    too_many = [str(uuid.uuid4()) for _ in range(201)]
    assert "Invalid arguments" in _error(
        client, "create_group", {"name": "x", "asset_ids": too_many}
    )


def test_other_users_assets_and_groups_cannot_be_changed(client_oidc: TestClient) -> None:
    client = client_oidc
    login_as(client, "admin@example.com")
    _enable(client)
    login_as(client, "bob@example.com")
    bob_asset = _upload(client)
    bob_group = _group(client, "Bob", [])
    login_as(client, "alice@example.com")
    alice_asset = _upload(client)
    assert bob_asset in _error(client, "add_tags", {"asset_ids": [bob_asset], "tags": ["x"]})
    assert bob_asset in _error(client, "set_title", {"asset_id": bob_asset, "title": "x"})
    assert "not found" in _error(
        client, "move_to_group", {"group_id": bob_group, "asset_ids": [alice_asset]}
    )
    assert "not found" in _error(client, "rename_group", {"group_id": bob_group, "name": "x"})
    with client.app.state.session_factory() as db:
        assert (
            db.execute(select(AssetTag).where(AssetTag.asset_id == uuid.UUID(bob_asset))).first()
            is None
        )
        assert db.get(Asset, uuid.UUID(bob_asset)) is not None


def test_undo_by_other_user_is_not_found(client_oidc: TestClient) -> None:
    client = client_oidc
    login_as(client, "admin@example.com")
    _enable(client)
    login_as(client, "alice@example.com")
    asset = _upload(client)
    message = _ok(client, "add_tags", {"asset_ids": [asset], "tags": ["x"]})
    login_as(client, "bob@example.com")
    assert client.post(f"{UNDO}/{message['id']}").status_code == 404


# -- 選んだ画像 -------------------------------------------------------------------------


def test_selected_asset_ids_in_form_context(client: TestClient) -> None:
    _enable(client)
    a, b = _upload(client), _upload(client)
    message = _call(
        client, "get_form_context", {}, form={"prompt": "", "selected_asset_ids": [a, b]}
    )
    assert message["ok"] is True
    assert message["result"]["selected_asset_ids"] == [a, b]
    assert "2 images" in message["result"]["selected_note"]

    plain = _call(client, "get_form_context", {}, form={"prompt": ""})
    assert plain["result"]["selected_asset_ids"] == []
    assert "selected_note" not in plain["result"]

    # update_form は選択を変えず、フォームの値にも入れない。
    written = _call(client, "update_form", {"prompt": "x"}, form={"selected_asset_ids": [a]})
    assert written["ok"] is True
    assert "selected_asset_ids" not in written["result"]["form"]


def test_selected_asset_ids_limit(client: TestClient) -> None:
    _enable(client)
    conversation = client.post(CONVERSATIONS, json={"title": ""}).json()
    response = client.post(
        f"{CONVERSATIONS}/{conversation['id']}/messages",
        json={
            "text": "hi",
            "form": {"selected_asset_ids": [str(uuid.uuid4()) for _ in range(201)]},
        },
    )
    assert response.status_code == 422


# -- グループの提案 ---------------------------------------------------------------------


def test_suggest_groups_requires_embeddings(client: TestClient) -> None:
    _enable(client)
    assert "not available" in _error(client, "suggest_groups", {})


def test_suggest_groups_returns_suggestions(client: TestClient) -> None:
    _enable(client)
    first, second, _lonely = _setup_clusters(client, [14, 8], outliers=1)
    existing = _group(client, "Existing", second[:5])
    message = _ok(client, "suggest_groups", {})
    result = message["result"]
    assert message["undoable"] is False
    assert result["total_suggestions"] == 2
    by_size = {s["size"]: s for s in result["suggestions"]}
    big = by_size[14]
    assert len(big["asset_ids"]) == 12 and big["asset_ids_truncated"] is True
    assert set(big["asset_ids"]) <= set(first)
    assert big["existing_group"] is None
    small = by_size[3]
    assert small["existing_group"]["id"] == existing
    assert small["existing_group"]["name"] == "Existing"

    detail = _ok(client, "suggest_groups", {"suggestion_id": big["suggestion_id"]})
    full = detail["result"]["suggestion"]
    assert set(full["asset_ids"]) == set(first) and full["asset_ids_truncated"] is False
    assert "not found" in _error(client, "suggest_groups", {"suggestion_id": "nope"})
    limited = _ok(client, "suggest_groups", {"limit": 1})["result"]
    assert len(limited["suggestions"]) == 1
