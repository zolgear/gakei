"""お気に入り(ADR-0055)。付け外し、見える範囲、一覧の状態、絞り込みとカーソル。"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app.auth.identity import LOCAL_ADMIN
from app.domain import history_sort
from app.domain.models import AssetEmbedding, AssetMark
from app.domain.run_views import run_has_favorite_output, summarize_runs, visible_runs_query
from app.embedding.catalog import XENOVA_CLIP_REVISION
from tests.conftest import login_as, wait_for_run_terminal

ACTIVE_KEY = f"fake:onnx:clip-vit-b32-u8@{XENOVA_CLIP_REVISION}"


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


def _output_ids(run: dict) -> list[str]:
    return [o["asset_id"] for o in sorted(run["outputs"], key=lambda o: o["output_index"])]


def _favorite(client: TestClient, asset_id: str, on: bool = True) -> Any:
    method = client.put if on else client.delete
    return method(f"/api/assets/{asset_id}/favorite")


def _favorites_in(run: dict) -> dict[str, bool]:
    return {o["asset_id"]: o["favorite"] for o in run["outputs"]}


def _mark_count(client: TestClient) -> int:
    with client.app.state.session_factory() as db:
        return db.execute(select(func.count()).select_from(AssetMark)).scalar_one()


def _runs_pages(client: TestClient, limit: int, **params: Any) -> list[dict]:
    items: list[dict] = []
    cursor: str | None = None
    while True:
        query: dict[str, Any] = {"limit": limit, **params}
        if cursor:
            query["cursor"] = cursor
        response = client.get("/api/runs", params=query)
        assert response.status_code == 200, response.text
        body = response.json()
        items.extend(body["items"])
        cursor = body["next_cursor"]
        if not cursor:
            return items


def _history_pages(client: TestClient, sort: str, limit: int, **params: Any) -> tuple[list, dict]:
    items: list[dict] = []
    cursor: str | None = None
    first: dict = {}
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


# -- 付け外し ---------------------------------------------------------------------


def test_toggle_is_idempotent_and_shows_in_lists(client: TestClient) -> None:
    run = _run(client, "two images", n=2)
    first, second = _output_ids(run)
    assert _favorites_in(run) == {first: False, second: False}

    for _ in range(2):  # 2回付けても1行(冪等)
        response = _favorite(client, first)
        assert response.status_code == 200, response.text
        assert response.json() == {"asset_id": first, "favorite": True}
    assert _mark_count(client) == 1

    detail = client.get(f"/api/runs/{run['id']}").json()
    assert _favorites_in(detail) == {first: True, second: False}
    listed = client.get("/api/runs").json()["items"][0]
    assert _favorites_in(listed) == {first: True, second: False}
    items, _ = _history_pages(client, "group", 50)
    assert {i["asset_id"]: _favorites_in(i["run"])[i["asset_id"]] for i in items} == {
        first: True,
        second: False,
    }

    for _ in range(2):  # 外すのも冪等
        response = _favorite(client, first, on=False)
        assert response.status_code == 200, response.text
        assert response.json() == {"asset_id": first, "favorite": False}
    assert _mark_count(client) == 0
    assert _favorites_in(client.get(f"/api/runs/{run['id']}").json()) == {
        first: False,
        second: False,
    }


def test_missing_and_deleted_assets_are_404(client: TestClient) -> None:
    missing = str(uuid.uuid4())
    assert _favorite(client, missing).status_code == 404
    assert _favorite(client, missing, on=False).status_code == 404

    run = _run(client, "to delete")
    (asset_id,) = _output_ids(run)
    assert _favorite(client, asset_id).status_code == 200
    assert client.delete(f"/api/assets/{asset_id}").status_code == 204
    # 削除済みには付け外しできず、絞り込みにも出ない。
    assert _favorite(client, asset_id).status_code == 404
    assert _favorite(client, asset_id, on=False).status_code == 404
    assert _runs_pages(client, 50, favorite=True) == []
    assert _history_pages(client, "group", 50, favorite=True)[0] == []

    # 行は残しているので、復元すればお気に入りも戻る(ADR-0055 2章)。
    assert client.post(f"/api/assets/{asset_id}/restore").status_code == 200
    assert [r["id"] for r in _runs_pages(client, 50, favorite=True)] == [run["id"]]


def test_others_assets_are_404_and_marks_are_per_user(client_oidc: TestClient) -> None:
    login_as(client_oidc, "alice@example.com")
    alice = _run(client_oidc, "alice")
    (alice_asset,) = _output_ids(alice)
    assert _favorite(client_oidc, alice_asset).status_code == 200
    client_oidc.cookies.clear()

    login_as(client_oidc, "bob@example.com")
    assert _favorite(client_oidc, alice_asset).status_code == 404
    assert _favorite(client_oidc, alice_asset, on=False).status_code == 404
    bob = _run(client_oidc, "bob")
    assert _favorites_in(bob) == {_output_ids(bob)[0]: False}
    assert _runs_pages(client_oidc, 50, favorite=True) == []
    client_oidc.cookies.clear()

    # 他人の操作で A の印は変わっていない。
    login_as(client_oidc, "alice@example.com")
    assert [r["id"] for r in _runs_pages(client_oidc, 50, favorite=True)] == [alice["id"]]
    with client_oidc.app.state.session_factory() as db:
        rows = db.execute(select(AssetMark.user_id)).scalars().all()
    assert len(rows) == 1 and rows[0] is not None


# -- 絞り込み ---------------------------------------------------------------------


def test_runs_favorite_filter_pages_with_cursor(client: TestClient) -> None:
    runs = [_run(client, f"image {i}", n=2 if i % 2 else 1) for i in range(6)]
    failed = _run(client, "x [[fail:contentFilter]]")
    assert failed["status"] == "failed"
    chosen = [runs[0], runs[1], runs[3], runs[4]]
    for run in chosen:
        assert _favorite(client, _output_ids(run)[-1]).status_code == 200
    expected = [r["id"] for r in reversed(chosen)]  # 新しい順
    for limit in (1, 2, 50):
        assert [r["id"] for r in _runs_pages(client, limit, favorite=True)] == expected
    # 省略と false は絞り込まない。
    assert len(_runs_pages(client, 50)) == 7
    assert len(_runs_pages(client, 50, favorite=False)) == 7
    # 出力が2つの Run は、お気に入りでない出力も要約に載る(どれがお気に入りかは favorite で分かる)。
    listed = {r["id"]: r for r in _runs_pages(client, 50, favorite=True)}
    assert _favorites_in(listed[runs[1]["id"]]) == {
        _output_ids(runs[1])[0]: False,
        _output_ids(runs[1])[1]: True,
    }


def test_history_group_favorite_filter(client: TestClient) -> None:
    group = client.post("/api/asset-groups", json={"name": "G"}).json()["id"]
    g1 = _run(client, "g1", n=2, group=group)
    g2 = _run(client, "g2", group=group)
    plain1 = _run(client, "p1")
    plain2 = _run(client, "p2", n=2)
    picked = [
        _output_ids(g1)[1],
        _output_ids(g2)[0],
        _output_ids(plain2)[0],
        _output_ids(plain2)[1],
    ]
    for asset_id in picked:
        assert _favorite(client, asset_id).status_code == 200

    for limit in (1, 3, 50):
        items, first = _history_pages(client, "group", limit, favorite=True)
        assert [i["asset_id"] for i in items] == picked
    assert [i["group_id"] for i in items] == [group, group, None, None]
    assert first["groups"] == [
        {"id": group, "name": "G", "count": 2},
        {"id": None, "name": None, "count": 2},
    ]
    assert not {i["asset_id"] for i in items} & set(_output_ids(plain1))
    # 絞り込まない並びは今のまま。
    items, first = _history_pages(client, "group", 50)
    assert len(items) == 6
    assert [g["count"] for g in first["groups"]] == [3, 3]


def _wait_embedded(client: TestClient, asset_ids: list[str]) -> None:
    deadline = time.monotonic() + 15
    pending = {uuid.UUID(a) for a in asset_ids}
    while time.monotonic() < deadline:
        with client.app.state.session_factory() as db:
            rows = db.execute(
                select(AssetEmbedding.asset_id).where(
                    AssetEmbedding.model_key == ACTIVE_KEY,
                    AssetEmbedding.status == "succeeded",
                    AssetEmbedding.asset_id.in_(pending),
                )
            ).all()
        pending -= {r[0] for r in rows}
        if not pending:
            return
        time.sleep(0.05)
    raise TimeoutError(f"埋め込みが終わりませんでした: {pending}")


def test_history_embedding_favorite_filter(client: TestClient) -> None:
    response = client.patch("/api/settings/embeddings", json={"enabled": True})
    assert response.status_code == 200, response.text
    runs = [_run(client, f"image {i}", n=2 if i % 2 else 1) for i in range(5)]
    all_ids = [a for r in runs for a in _output_ids(r)]
    _wait_embedded(client, all_ids)
    picked = all_ids[::2]
    for asset_id in picked:
        assert _favorite(client, asset_id).status_code == 200

    # 全体の並びを先に作ってキャッシュに載せても、絞り込みは別の並び(鍵が別)。
    everything, _ = _history_pages(client, "embedding", 50)
    assert sorted(i["asset_id"] for i in everything) == sorted(all_ids)
    items, body = _history_pages(client, "embedding", 50, favorite=True)
    ids = [i["asset_id"] for i in items]
    assert sorted(ids) == sorted(picked)
    assert body["groups"] is None
    # ページを分けても同じ並び。
    paged, _ = _history_pages(client, "embedding", 1, favorite=True)
    assert [i["asset_id"] for i in paged] == ids
    # 絞り込みの後で全体の並びを読んでも、キャッシュが混ざらない。
    again, _ = _history_pages(client, "embedding", 50)
    assert [i["asset_id"] for i in again] == [i["asset_id"] for i in everything]

    # 外すと絞り込みの並びから消える(作り直す)。
    assert _favorite(client, picked[0], on=False).status_code == 200
    items, _ = _history_pages(client, "embedding", 50, favorite=True)
    assert sorted(i["asset_id"] for i in items) == sorted(picked[1:])


# -- 問い合わせの数(N+1 にしない) --------------------------------------------------


def _count_queries(client: TestClient, call: Callable[[Session], object]) -> int:
    """この Session が出した問い合わせの数(裏の worker の問い合わせは数えない)。"""
    statements: list[object] = []
    with client.app.state.session_factory() as db:
        event.listen(db, "do_orm_execute", lambda state: statements.append(state.statement))
        call(db)
    return len(statements)


def test_lists_do_not_query_per_item(client: TestClient) -> None:
    viewer = LOCAL_ADMIN

    def measure() -> dict[str, int]:
        def runs(favorite: bool) -> Callable[[Session], object]:
            def call(db: Session) -> object:
                query = visible_runs_query(viewer)
                if favorite:
                    query = query.where(run_has_favorite_output(viewer))
                rows = list(db.execute(query.limit(50)).scalars().all())
                return summarize_runs(db, rows, viewer)

            return call

        def group(favorite: bool) -> Callable[[Session], object]:
            return lambda db: history_sort.list_by_group(
                db, viewer, limit=50, cursor=None, favorite=favorite
            )

        return {
            "runs": _count_queries(client, runs(False)),
            "runs_fav": _count_queries(client, runs(True)),
            "group": _count_queries(client, group(False)),
            "group_fav": _count_queries(client, group(True)),
        }

    for i in range(2):
        run = _run(client, f"first {i}", n=2)
        assert _favorite(client, _output_ids(run)[0]).status_code == 200
    small = measure()
    for i in range(6):
        run = _run(client, f"more {i}", n=2)
        assert _favorite(client, _output_ids(run)[1]).status_code == 200
    large = measure()
    assert large == small
