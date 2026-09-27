"""グループ(ストックの手動整理。ADR-0022)。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import make_png_bytes


def _upload(client: TestClient) -> str:
    data = make_png_bytes(width=64, height=64)
    response = client.post(
        "/api/assets",
        files={"file": ("f.png", data, "image/png")},
        data={"kind": "upload"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _create_group(client: TestClient, name: str = "案件A") -> dict:
    response = client.post("/api/asset-groups", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def test_create_and_list(client: TestClient) -> None:
    created = _create_group(client, name="風景")
    assert created["name"] == "風景"
    assert created["member_count"] == 0
    assert created["cover_asset_id"] is None

    listed = client.get("/api/asset-groups").json()["items"]
    ids = [g["id"] for g in listed]
    assert created["id"] in ids


def _list(client: TestClient) -> list[dict]:
    response = client.get("/api/asset-groups")
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _ids(client: TestClient) -> list[str]:
    return [g["id"] for g in _list(client)]


def test_list_puts_newest_group_first(client: TestClient) -> None:
    first = _create_group(client, name="1番目")
    second = _create_group(client, name="2番目")
    third = _create_group(client, name="3番目")

    listed = _list(client)
    assert [g["id"] for g in listed] == [third["id"], second["id"], first["id"]]
    positions = [g["position"] for g in listed]
    assert positions == sorted(positions)
    assert len(set(positions)) == 3
    assert third["position"] < second["position"] < first["position"]


def test_reorder_follows_given_order_and_renumbers_from_zero(client: TestClient) -> None:
    groups = [_create_group(client, name=f"G{i}") for i in range(3)]
    reversed_ids = list(reversed(_ids(client)))

    response = client.put("/api/asset-groups/order", json={"group_ids": reversed_ids})
    assert response.status_code == 200, response.text
    body = response.json()["items"]
    assert [g["id"] for g in body] == reversed_ids
    assert [g["position"] for g in body] == [0, 1, 2]

    listed = _list(client)
    assert [g["id"] for g in listed] == reversed_ids
    assert [g["position"] for g in listed] == [0, 1, 2]
    assert reversed_ids == [g["id"] for g in groups]


def test_reorder_does_not_bump_updated_at(client: TestClient) -> None:
    _create_group(client, name="A")
    _create_group(client, name="B")
    before = {g["id"]: g["updated_at"] for g in _list(client)}

    response = client.put("/api/asset-groups/order", json={"group_ids": list(reversed(before))})
    assert response.status_code == 200
    after = {g["id"]: g["updated_at"] for g in _list(client)}
    assert after == before


def test_reorder_with_mismatched_ids_returns_422_and_keeps_order(client: TestClient) -> None:
    for i in range(3):
        _create_group(client, name=f"G{i}")
    original = _list(client)
    ids = [g["id"] for g in original]
    unknown = "00000000-0000-0000-0000-000000000000"

    for bad in (
        ids[:2],  # 不足
        [*ids, unknown],  # 未知の id が余分
        [ids[0], ids[0], ids[1], ids[2]],  # 重複
        [ids[0], ids[1], ids[1]],  # 重複で1件不足(件数は同じ)
        [],
    ):
        response = client.put("/api/asset-groups/order", json={"group_ids": bad})
        assert response.status_code == 422, bad
        assert response.json()["detail"] == "並べ替えの一覧が現在のグループと一致しません"

    assert _list(client) == original


def test_reorder_excludes_deleted_groups(client: TestClient) -> None:
    first = _create_group(client, name="残す1")
    doomed = _create_group(client, name="消す")
    third = _create_group(client, name="残す2")
    assert client.delete(f"/api/asset-groups/{doomed['id']}").status_code == 204

    # 削除済みを含めると 422。
    response = client.put(
        "/api/asset-groups/order",
        json={"group_ids": [first["id"], doomed["id"], third["id"]]},
    )
    assert response.status_code == 422

    response = client.put("/api/asset-groups/order", json={"group_ids": [first["id"], third["id"]]})
    assert response.status_code == 200, response.text
    assert _ids(client) == [first["id"], third["id"]]


def test_new_group_goes_first_after_reorder(client: TestClient) -> None:
    a = _create_group(client, name="A")
    b = _create_group(client, name="B")
    client.put("/api/asset-groups/order", json={"group_ids": [a["id"], b["id"]]})

    c = _create_group(client, name="C")
    assert c["position"] == -1
    assert _ids(client) == [c["id"], a["id"], b["id"]]


def test_membership_and_rename_do_not_change_order(client: TestClient) -> None:
    first = _create_group(client, name="先に作った")
    second = _create_group(client, name="後で作った")
    before = _ids(client)
    assert before == [second["id"], first["id"]]

    asset_id = _upload(client)
    client.post(f"/api/asset-groups/{first['id']}/assets", json={"asset_ids": [asset_id]})
    assert _ids(client) == before
    client.post(f"/api/asset-groups/{first['id']}/assets/remove", json={"asset_ids": [asset_id]})
    assert _ids(client) == before
    client.patch(f"/api/asset-groups/{first['id']}", json={"name": "更新した"})
    assert _ids(client) == before


def test_order_path_is_not_parsed_as_group_id(client: TestClient) -> None:
    # `order` は PUT だけの固定パス。他のメソッドは `/{group_id}` に当たり UUID として不正(422)。
    assert client.put("/api/asset-groups/order", json={"group_ids": []}).status_code == 200
    assert client.patch("/api/asset-groups/order", json={"name": "x"}).status_code == 422
    assert client.delete("/api/asset-groups/order").status_code == 422


def test_rename_group(client: TestClient) -> None:
    created = _create_group(client, name="旧名")
    response = client.patch(f"/api/asset-groups/{created['id']}", json={"name": "新名"})
    assert response.status_code == 200
    assert response.json()["name"] == "新名"


def test_delete_group_removes_from_list_and_404s_afterward(client: TestClient) -> None:
    created = _create_group(client, name="消す予定")
    response = client.delete(f"/api/asset-groups/{created['id']}")
    assert response.status_code == 204

    listed = client.get("/api/asset-groups").json()["items"]
    assert created["id"] not in [g["id"] for g in listed]

    assert client.patch(f"/api/asset-groups/{created['id']}", json={"name": "x"}).status_code == 404
    assert (
        client.post(
            f"/api/asset-groups/{created['id']}/assets", json={"asset_ids": [_upload(client)]}
        ).status_code
        == 404
    )


def test_add_members_is_idempotent_and_tracks_cover(client: TestClient) -> None:
    group = _create_group(client)
    asset_1 = _upload(client)
    asset_2 = _upload(client)

    response = client.post(f"/api/asset-groups/{group['id']}/assets", json={"asset_ids": [asset_1]})
    assert response.status_code == 200
    body = response.json()
    assert body["member_count"] == 1
    assert body["cover_asset_id"] == asset_1

    # 同じ asset_id をもう一度追加しても無視され、新しい asset_2 が最新のカバーになる。
    response = client.post(
        f"/api/asset-groups/{group['id']}/assets", json={"asset_ids": [asset_1, asset_2]}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["member_count"] == 2
    assert body["cover_asset_id"] == asset_2


def test_add_members_excludes_deleted_asset_from_count(client: TestClient) -> None:
    group = _create_group(client)
    asset_1 = _upload(client)
    asset_2 = _upload(client)
    client.post(f"/api/asset-groups/{group['id']}/assets", json={"asset_ids": [asset_1, asset_2]})

    assert client.delete(f"/api/assets/{asset_2}").status_code == 204

    listed = client.get("/api/asset-groups").json()["items"]
    matched = next(g for g in listed if g["id"] == group["id"])
    assert matched["member_count"] == 1
    assert matched["cover_asset_id"] == asset_1


def test_add_members_with_unknown_id_returns_404_and_adds_nothing(client: TestClient) -> None:
    group = _create_group(client)
    asset_1 = _upload(client)
    fake_id = "00000000-0000-0000-0000-000000000000"

    response = client.post(
        f"/api/asset-groups/{group['id']}/assets", json={"asset_ids": [asset_1, fake_id]}
    )
    assert response.status_code == 404
    assert fake_id in response.json()["detail"]

    listed = client.get("/api/asset-groups").json()["items"]
    matched = next(g for g in listed if g["id"] == group["id"])
    assert matched["member_count"] == 0


def test_add_members_with_deleted_asset_returns_404(client: TestClient) -> None:
    group = _create_group(client)
    asset_1 = _upload(client)
    assert client.delete(f"/api/assets/{asset_1}").status_code == 204

    response = client.post(f"/api/asset-groups/{group['id']}/assets", json={"asset_ids": [asset_1]})
    assert response.status_code == 404


def test_remove_members_ignores_absent(client: TestClient) -> None:
    group = _create_group(client)
    asset_1 = _upload(client)
    asset_2 = _upload(client)
    client.post(f"/api/asset-groups/{group['id']}/assets", json={"asset_ids": [asset_1]})

    # asset_2 は入っていないが、無視されてエラーにならない。
    response = client.post(
        f"/api/asset-groups/{group['id']}/assets/remove",
        json={"asset_ids": [asset_1, asset_2]},
    )
    assert response.status_code == 200
    assert response.json()["member_count"] == 0


def test_list_assets_filtered_by_group_id(client: TestClient) -> None:
    group = _create_group(client)
    asset_1 = _upload(client)
    asset_2 = _upload(client)
    client.post(f"/api/asset-groups/{group['id']}/assets", json={"asset_ids": [asset_1]})

    response = client.get("/api/assets", params={"group_id": group["id"]})
    assert response.status_code == 200
    ids = [a["id"] for a in response.json()["items"]]
    assert ids == [asset_1]
    assert asset_2 not in ids


def test_list_assets_combines_group_id_and_kind(client: TestClient) -> None:
    group = _create_group(client)
    upload_asset = _upload(client)
    client.post(f"/api/asset-groups/{group['id']}/assets", json={"asset_ids": [upload_asset]})

    response = client.get("/api/assets", params={"group_id": group["id"], "kind": "generated"})
    assert response.status_code == 200
    assert response.json()["items"] == []


def test_list_assets_ungrouped_excludes_members_of_active_groups(client: TestClient) -> None:
    group = _create_group(client)
    deleted_group = _create_group(client, "消すグループ")
    in_group = _upload(client)
    only_in_deleted = _upload(client)
    free = _upload(client)
    client.post(f"/api/asset-groups/{group['id']}/assets", json={"asset_ids": [in_group]})
    client.post(
        f"/api/asset-groups/{deleted_group['id']}/assets", json={"asset_ids": [only_in_deleted]}
    )
    client.delete(f"/api/asset-groups/{deleted_group['id']}")

    response = client.get("/api/assets", params={"ungrouped": "true"})
    assert response.status_code == 200
    ids = {a["id"] for a in response.json()["items"]}
    assert free in ids
    assert only_in_deleted in ids  # 削除済みグループにだけ入っていたものは「グループなし」
    assert in_group not in ids

    both = client.get("/api/assets", params={"ungrouped": "true", "group_id": group["id"]})
    assert both.status_code == 422


def test_list_assets_with_deleted_group_id_returns_404(client: TestClient) -> None:
    group = _create_group(client)
    assert client.delete(f"/api/asset-groups/{group['id']}").status_code == 204

    response = client.get("/api/assets", params={"group_id": group["id"]})
    assert response.status_code == 404


def test_list_assets_with_unknown_group_id_returns_404(client: TestClient) -> None:
    fake_id = "00000000-0000-0000-0000-000000000000"
    response = client.get("/api/assets", params={"group_id": fake_id})
    assert response.status_code == 404


def test_asset_detail_groups_sorted_by_name_and_excludes_deleted(client: TestClient) -> None:
    asset_id = _upload(client)
    group_b = _create_group(client, name="B")
    group_a = _create_group(client, name="A")
    group_c = _create_group(client, name="C")
    for group in (group_b, group_a, group_c):
        client.post(f"/api/asset-groups/{group['id']}/assets", json={"asset_ids": [asset_id]})

    assert client.delete(f"/api/asset-groups/{group_c['id']}").status_code == 204

    detail = client.get(f"/api/assets/{asset_id}").json()
    assert [g["name"] for g in detail["groups"]] == ["A", "B"]


def test_name_validation(client: TestClient) -> None:
    assert client.post("/api/asset-groups", json={"name": ""}).status_code == 422
    assert client.post("/api/asset-groups", json={"name": "   "}).status_code == 422
    assert client.post("/api/asset-groups", json={"name": "a" * 101}).status_code == 422

    group = _create_group(client)
    assert client.patch(f"/api/asset-groups/{group['id']}", json={"name": ""}).status_code == 422


def test_asset_ids_count_validation(client: TestClient) -> None:
    group = _create_group(client)
    assert (
        client.post(f"/api/asset-groups/{group['id']}/assets", json={"asset_ids": []}).status_code
        == 422
    )
    fake_ids = [f"00000000-0000-0000-0000-{i:012d}" for i in range(201)]
    assert (
        client.post(
            f"/api/asset-groups/{group['id']}/assets", json={"asset_ids": fake_ids}
        ).status_code
        == 422
    )


def test_operations_on_unknown_group_return_404(client: TestClient) -> None:
    fake_id = "00000000-0000-0000-0000-000000000000"
    assert client.get("/api/asset-groups").status_code == 200
    assert client.patch(f"/api/asset-groups/{fake_id}", json={"name": "x"}).status_code == 404
    assert client.delete(f"/api/asset-groups/{fake_id}").status_code == 404
    assert (
        client.post(
            f"/api/asset-groups/{fake_id}/assets", json={"asset_ids": [_upload(client)]}
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/asset-groups/{fake_id}/assets/remove", json={"asset_ids": [_upload(client)]}
        ).status_code
        == 404
    )


def test_created_by_user_id_is_null_in_none_mode(client: TestClient) -> None:
    import uuid

    from sqlalchemy import select

    from app.domain.models import AssetGroup

    group = _create_group(client)

    session_factory = client.app.state.session_factory
    with session_factory() as db:
        row = db.execute(
            select(AssetGroup).where(AssetGroup.id == uuid.UUID(group["id"]))
        ).scalar_one()
        assert row.created_by_user_id is None
