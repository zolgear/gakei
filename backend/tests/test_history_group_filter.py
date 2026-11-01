"""履歴のグループで含める・除く、日付の古い順、グループごとの先頭だけ(ADR-0054 6章)。"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import login_as, wait_for_run_terminal


def _run(client: TestClient, prompt: str, *, n: int = 1, group: str | None = None) -> dict:
    body: dict[str, Any] = {
        "operation": "generate",
        "model": "gpt-image-2.5-sunburst",
        "prompt": prompt,
        "params": {"n": n},
    }
    if group is not None:
        body["asset_group_id"] = group
    response = client.post("/api/runs", json=body)
    assert response.status_code == 202, response.text
    return wait_for_run_terminal(client, response.json()["id"])


def _group(client: TestClient, name: str) -> str:
    response = client.post("/api/asset-groups", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _runs(client: TestClient, **params: Any) -> list[str]:
    """`GET /api/runs` の全ページの Run の id(1件ずつ読んでカーソルも確かめる)。"""
    ids: list[str] = []
    cursor: str | None = None
    while True:
        query: dict[str, Any] = {"limit": 1, **params}
        if cursor:
            query["cursor"] = cursor
        response = client.get("/api/runs", params=query)
        assert response.status_code == 200, response.text
        body = response.json()
        ids.extend(item["id"] for item in body["items"])
        cursor = body["next_cursor"]
        if not cursor:
            return ids


def _assets(
    client: TestClient, sort: str, *, limit: int = 2, **params: Any
) -> tuple[list[dict], dict]:
    items: list[dict] = []
    first: dict = {}
    cursor: str | None = None
    while True:
        query: dict[str, Any] = {"sort": sort, "limit": limit, **params}
        if cursor:
            query["cursor"] = cursor
        response = client.get("/api/history/assets", params=query)
        assert response.status_code == 200, response.text
        body = response.json()
        first = first or body
        items.extend(body["items"])
        cursor = body["next_cursor"]
        if not cursor:
            return items, first


def _output_ids(run: dict) -> list[str]:
    return [o["asset_id"] for o in sorted(run["outputs"], key=lambda o: o["output_index"])]


def _move(client: TestClient, group: str, asset_ids: list[str]) -> None:
    response = client.post(f"/api/asset-groups/{group}/assets", json={"asset_ids": asset_ids})
    assert response.status_code in (200, 204), response.text


# -- 日付の古い順 ---------------------------------------------------------------------


def test_runs_ascending_order_pages_oldest_first(client: TestClient) -> None:
    first = _run(client, "one")
    second = _run(client, "two")
    third = _run(client, "three [[fail:contentFilter]]")
    expected = [first["id"], second["id"], third["id"]]
    assert _runs(client, order="asc") == expected
    assert _runs(client) == list(reversed(expected))
    assert client.get("/api/runs", params={"order": "sideways"}).status_code == 422


# -- グループで含める・除く(Run 単位) ------------------------------------------------


def test_runs_group_include_and_exclude(client: TestClient) -> None:
    poses = _group(client, "Poses")
    other = _group(client, "Other")
    plain = _run(client, "plain")
    pose = _run(client, "pose", group=poses)
    side = _run(client, "side", group=other)
    # 出力の無い Run は、出力を入れる先に選んだグループで見る。
    failed_pose = _run(client, "x [[fail:contentFilter]]", group=poses)
    failed_plain = _run(client, "y [[fail:contentFilter]]")

    all_ids = [plain["id"], pose["id"], side["id"], failed_pose["id"], failed_plain["id"]]
    assert _runs(client, order="asc") == all_ids
    assert _runs(client, order="asc", exclude_group=[poses]) == [
        plain["id"],
        side["id"],
        failed_plain["id"],
    ]
    assert _runs(client, order="asc", group=[poses]) == [pose["id"], failed_pose["id"]]
    assert _runs(client, order="asc", group=[poses, "none"]) == [
        plain["id"],
        pose["id"],
        failed_pose["id"],
        failed_plain["id"],
    ]
    assert _runs(client, order="asc", exclude_group=["none"]) == [
        pose["id"],
        side["id"],
        failed_pose["id"],
    ]
    # 含めると除くは両方を満たすもの。
    assert _runs(client, order="asc", group=[poses, other], exclude_group=[poses]) == [side["id"]]


def test_runs_group_uses_current_membership_of_outputs(client: TestClient) -> None:
    """出力を後から別のグループへ移したら、移した先のグループとして扱う。"""
    poses = _group(client, "Poses")
    other = _group(client, "Other")
    moved = _run(client, "moved", group=poses)
    _move(client, other, _output_ids(moved))
    assert _runs(client, group=[poses]) == []
    assert _runs(client, group=[other]) == [moved["id"]]
    assert _runs(client, exclude_group=[poses]) == [moved["id"]]


def test_runs_deleted_group_counts_as_no_group(client: TestClient) -> None:
    gone = _group(client, "Gone")
    run = _run(client, "in gone", group=gone)
    assert client.delete(f"/api/asset-groups/{gone}").status_code == 204
    assert _runs(client, group=["none"]) == [run["id"]]
    assert _runs(client, group=[gone]) == []


def test_group_filter_rejects_unreadable_values(client: TestClient) -> None:
    assert client.get("/api/runs", params={"group": "nope"}).status_code == 422
    response = client.get("/api/history/assets", params={"sort": "group", "exclude_group": "x"})
    assert response.status_code == 422


def test_runs_group_filter_hides_other_users_groups(client_oidc: TestClient) -> None:
    """他人のグループは見えないので、その id で含めても何も当たらない(ADR-0025)。"""
    login_as(client_oidc, "owner@example.com")
    theirs = _group(client_oidc, "Theirs")
    _run(client_oidc, "owner run", group=theirs)
    client_oidc.cookies.clear()
    login_as(client_oidc, "other@example.com")
    mine = _run(client_oidc, "other run")
    assert _runs(client_oidc, group=[theirs]) == []
    assert _runs(client_oidc, exclude_group=[theirs]) == [mine["id"]]


# -- グループで含める・除く(画像単位) ------------------------------------------------


def test_history_assets_group_filter(client: TestClient) -> None:
    poses = _group(client, "Poses")
    other = _group(client, "Other")
    plain = _run(client, "plain")
    pose = _run(client, "pose", n=2, group=poses)
    side = _run(client, "side", group=other)

    items, first = _assets(client, "group", exclude_group=[poses])
    assert [i["asset_id"] for i in items] == _output_ids(side) + _output_ids(plain)
    assert [(g["id"], g["count"]) for g in first["groups"]] == [(other, 1), (None, 1)]

    items, _first = _assets(client, "group", group=[poses])
    assert [i["asset_id"] for i in items] == _output_ids(pose)


# -- グループごとの先頭だけ ------------------------------------------------------------


def test_group_sort_per_group_shows_heads_with_full_counts(client: TestClient) -> None:
    big = _group(client, "Big")
    small = _group(client, "Small")  # 新しいグループが先頭
    big_runs = [_run(client, f"big {i}", n=2, group=big) for i in range(3)]
    small_run = _run(client, "small", group=small)
    plain = [_run(client, f"plain {i}") for i in range(4)]

    big_ids = [a for r in big_runs for a in _output_ids(r)]
    plain_ids = [a for r in plain for a in _output_ids(r)]
    expected = _output_ids(small_run) + big_ids[:3] + plain_ids[:3]
    for limit in (1, 2, 50):
        items, first = _assets(client, "group", limit=limit, per_group=3)
        assert [i["asset_id"] for i in items] == expected
        # 節の件数は先頭だけでなく全件。
        assert [(g["id"], g["count"]) for g in first["groups"]] == [
            (small, 1),
            (big, 6),
            (None, 4),
        ]
    bad = client.get("/api/history/assets", params={"sort": "group", "per_group": 0})
    assert bad.status_code == 422
