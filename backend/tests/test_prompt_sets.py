"""プロンプトセットの CRUD、並べ替え、論理削除、検証エラー(ADR-0009)。"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _create_set(
    client: TestClient, name: str = "サンプル集", items: list[dict] | None = None
) -> dict:
    response = client.post(
        "/api/prompt-sets",
        json={"name": name, "items": items or []},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_create_and_get_via_list(client: TestClient) -> None:
    created = _create_set(
        client,
        name="風景",
        items=[{"label": "夕焼け", "text": "sunset over mountains"}, {"text": "no label item"}],
    )
    assert created["name"] == "風景"
    assert len(created["items"]) == 2
    assert created["items"][0]["position"] == 0
    assert created["items"][1]["position"] == 1
    assert created["items"][0]["label"] == "夕焼け"
    assert created["items"][1]["label"] is None

    listed = client.get("/api/prompt-sets").json()
    ids = [ps["id"] for ps in listed["items"]]
    assert created["id"] in ids


def test_list_is_ordered_by_updated_at_desc(client: TestClient) -> None:
    first = _create_set(client, name="先に作った")
    second = _create_set(client, name="後で作った")

    listed = client.get("/api/prompt-sets").json()["items"]
    ids_in_order = [ps["id"] for ps in listed]
    assert ids_in_order.index(second["id"]) < ids_in_order.index(first["id"])

    # 先に作った方を更新すると、一覧の先頭に上がってくる。
    client.patch(f"/api/prompt-sets/{first['id']}", json={"name": "更新した"})
    listed_after = client.get("/api/prompt-sets").json()["items"]
    ids_after = [ps["id"] for ps in listed_after]
    assert ids_after.index(first["id"]) < ids_after.index(second["id"])


def test_update_set_name(client: TestClient) -> None:
    created = _create_set(client, name="旧名")
    response = client.patch(f"/api/prompt-sets/{created['id']}", json={"name": "新名"})
    assert response.status_code == 200
    assert response.json()["name"] == "新名"


def test_delete_set_removes_it_from_list(client: TestClient) -> None:
    created = _create_set(client, name="消す予定")
    response = client.delete(f"/api/prompt-sets/{created['id']}")
    assert response.status_code == 204

    listed = client.get("/api/prompt-sets").json()
    ids = [ps["id"] for ps in listed["items"]]
    assert created["id"] not in ids

    # 削除済みへの操作は404。(単体取得の GET は仕様に無いので検証しない。
    # frontend/dist の有無で SPA フォールバックの応答が変わり、結果が環境依存になる。)
    assert client.patch(f"/api/prompt-sets/{created['id']}", json={"name": "x"}).status_code == 404


def test_append_item_goes_to_end(client: TestClient) -> None:
    created = _create_set(client, name="項目追加", items=[{"text": "first"}])
    response = client.post(
        f"/api/prompt-sets/{created['id']}/items", json={"label": "2つ目", "text": "second"}
    )
    assert response.status_code == 201
    added = response.json()
    assert added["position"] == 1

    detail = client.get("/api/prompt-sets").json()["items"]
    matched = next(ps for ps in detail if ps["id"] == created["id"])
    assert [i["text"] for i in matched["items"]] == ["first", "second"]


def test_update_item_text_and_label(client: TestClient) -> None:
    created = _create_set(client, name="編集対象", items=[{"text": "before"}])
    item_id = created["items"][0]["id"]

    response = client.patch(
        f"/api/prompt-sets/{created['id']}/items/{item_id}",
        json={"text": "after", "label": "ラベル"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["text"] == "after"
    assert body["label"] == "ラベル"


def test_reorder_items_renumbers_positions(client: TestClient) -> None:
    created = _create_set(
        client,
        name="並べ替え",
        items=[{"text": "a"}, {"text": "b"}, {"text": "c"}],
    )
    items = created["items"]
    a_id, b_id, c_id = items[0]["id"], items[1]["id"], items[2]["id"]

    # c を先頭(position=0)に移動する。
    response = client.patch(f"/api/prompt-sets/{created['id']}/items/{c_id}", json={"position": 0})
    assert response.status_code == 200

    detail = client.get("/api/prompt-sets").json()["items"]
    matched = next(ps for ps in detail if ps["id"] == created["id"])
    ordered = sorted(matched["items"], key=lambda i: i["position"])
    assert [i["id"] for i in ordered] == [c_id, a_id, b_id]
    assert [i["position"] for i in ordered] == [0, 1, 2]


def test_delete_item_renumbers_remaining(client: TestClient) -> None:
    created = _create_set(
        client, name="削除して振り直し", items=[{"text": "a"}, {"text": "b"}, {"text": "c"}]
    )
    items = created["items"]
    a_id, b_id, c_id = items[0]["id"], items[1]["id"], items[2]["id"]

    response = client.delete(f"/api/prompt-sets/{created['id']}/items/{b_id}")
    assert response.status_code == 204

    detail = client.get("/api/prompt-sets").json()["items"]
    matched = next(ps for ps in detail if ps["id"] == created["id"])
    ordered = sorted(matched["items"], key=lambda i: i["position"])
    assert [i["id"] for i in ordered] == [a_id, c_id]
    assert [i["position"] for i in ordered] == [0, 1]

    # 削除済み項目への操作は404。
    assert (
        client.patch(
            f"/api/prompt-sets/{created['id']}/items/{b_id}", json={"text": "x"}
        ).status_code
        == 404
    )


def test_validation_errors(client: TestClient) -> None:
    # name が空文字
    assert client.post("/api/prompt-sets", json={"name": "", "items": []}).status_code == 422
    # name が101文字
    assert client.post("/api/prompt-sets", json={"name": "a" * 101, "items": []}).status_code == 422
    # text が空文字
    assert (
        client.post("/api/prompt-sets", json={"name": "ok", "items": [{"text": ""}]}).status_code
        == 422
    )
    # text が32,001文字
    assert (
        client.post(
            "/api/prompt-sets", json={"name": "ok", "items": [{"text": "a" * 32_001}]}
        ).status_code
        == 422
    )
    # label が101文字
    assert (
        client.post(
            "/api/prompt-sets",
            json={"name": "ok", "items": [{"label": "l" * 101, "text": "ok"}]},
        ).status_code
        == 422
    )


def test_operations_on_unknown_set_return_404(client: TestClient) -> None:
    fake_id = "00000000-0000-0000-0000-000000000000"
    assert client.get("/api/prompt-sets").status_code == 200
    assert client.patch(f"/api/prompt-sets/{fake_id}", json={"name": "x"}).status_code == 404
    assert client.delete(f"/api/prompt-sets/{fake_id}").status_code == 404
    assert client.post(f"/api/prompt-sets/{fake_id}/items", json={"text": "x"}).status_code == 404
