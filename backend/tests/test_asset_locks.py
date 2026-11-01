"""ロック(ADR-0060)。ロックした画像と、その祖先は消せない。"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.auth.identity import CurrentUser
from app.domain import asset_locks
from app.domain.models import AssetLock
from tests.conftest import make_png_bytes, wait_for_run_terminal


def _upload(client: TestClient, color: tuple[int, int, int] = (10, 20, 30)) -> str:
    response = client.post(
        "/api/assets",
        files={"file": ("f.png", make_png_bytes(64, 64, color), "image/png")},
        data={"kind": "upload"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _edit(client: TestClient, source: str, prompt: str) -> tuple[str, str]:
    response = client.post(
        "/api/runs",
        json={
            "operation": "edit",
            "model": "gpt-image-2.5-sunburst",
            "prompt": prompt,
            "params": {"n": 1},
            "inputs": [{"asset_id": source, "role": "image", "position": 0}],
        },
    )
    assert response.status_code == 202, response.text
    detail = wait_for_run_terminal(client, response.json()["id"])
    assert detail["status"] == "succeeded", detail
    return detail["id"], detail["outputs"][0]["asset_id"]


def _lock(client: TestClient, asset_id: str, **body: Any) -> Any:
    return client.put(f"/api/assets/{asset_id}/lock", json={"kind": "delivered", **body})


def test_lock_and_unlock(client: TestClient) -> None:
    asset_id = _upload(client)
    response = _lock(client, asset_id, note="  納品 A-12  ")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kind"] == "delivered"
    assert body["note"] == "納品 A-12"
    assert body["can_unlock"] is True

    assert _lock(client, asset_id).status_code == 409
    detail = client.get(f"/api/assets/{asset_id}").json()
    assert detail["locked"] is True
    assert detail["lock_protected"] is True
    assert detail["lock"]["note"] == "納品 A-12"
    stock = client.get("/api/assets").json()["items"]
    assert [(a["locked"], a["lock_protected"]) for a in stock if a["id"] == asset_id] == [
        (True, True)
    ]

    assert client.delete(f"/api/assets/{asset_id}/lock").status_code == 204
    assert client.delete(f"/api/assets/{asset_id}/lock").status_code == 404
    detail = client.get(f"/api/assets/{asset_id}").json()
    assert detail["lock"] is None
    assert detail["lock_protected"] is False
    # 付け直せる(外したロックは記録として残る)。
    assert _lock(client, asset_id, kind="shared").status_code == 200


def test_lock_rejects_unknown_kind_and_missing_asset(client: TestClient) -> None:
    asset_id = _upload(client)
    assert _lock(client, asset_id, kind="approved").status_code == 422
    assert _lock(client, "00000000-0000-0000-0000-000000000000").status_code == 404


def test_lock_protects_the_image_and_its_ancestors(client: TestClient) -> None:
    source = _upload(client)
    first_run, first = _edit(client, source, "first")
    second_run, second = _edit(client, first, "second")
    assert _lock(client, first).status_code == 200

    # ロックした画像、祖先(入力)、それを作った Run は消せない。
    assert client.delete(f"/api/assets/{first}").status_code == 409
    assert client.delete(f"/api/assets/{source}").status_code == 409
    assert client.delete(f"/api/runs/{first_run}").status_code == 409
    source_detail = client.get(f"/api/assets/{source}").json()
    assert (source_detail["locked"], source_detail["lock_protected"]) == (False, True)

    # 履歴の出力にも載る。子孫は守らない。
    run = client.get(f"/api/runs/{first_run}").json()
    assert [(o["locked"], o["lock_protected"]) for o in run["outputs"]] == [(True, True)]
    assert client.delete(f"/api/runs/{second_run}").status_code == 204
    assert client.get(f"/api/assets/{second}").json()["deleted_at"] is not None

    # 外せば消せる。
    assert client.delete(f"/api/assets/{first}/lock").status_code == 204
    assert client.delete(f"/api/assets/{source}").status_code == 204
    assert client.delete(f"/api/runs/{first_run}").status_code == 204


def test_history_filters_locked(client: TestClient) -> None:
    source = _upload(client)
    locked_run, locked = _edit(client, source, "locked")
    _other_run, _other = _edit(client, source, "other")
    assert _lock(client, locked).status_code == 200

    runs = client.get("/api/runs", params={"locked": True}).json()["items"]
    assert [r["id"] for r in runs] == [locked_run]
    assets = client.get("/api/history/assets", params={"sort": "group", "locked": True}).json()[
        "items"
    ]
    assert [a["asset_id"] for a in assets] == [locked]


def test_can_unlock_only_locker_or_admin() -> None:
    import uuid

    locker = uuid.uuid4()
    lock = AssetLock(asset_id=uuid.uuid4(), kind="other", locked_by_user_id=locker)

    def user(role: str, user_id: uuid.UUID) -> CurrentUser:
        return CurrentUser(id=user_id, name=None, email=None, role=role)  # type: ignore[arg-type]

    assert asset_locks.can_unlock(user("user", locker), lock)
    assert asset_locks.can_unlock(user("admin", uuid.uuid4()), lock)
    assert not asset_locks.can_unlock(user("user", uuid.uuid4()), lock)
