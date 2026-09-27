"""生成時のグループ指定(ADR-0022 1章 (a)、2章 `run.asset_group_id`、3章)。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.domain.models import AssetGroupMember, Run
from app.worker.runner import _finish_run_succeeded
from tests.conftest import make_png_bytes, wait_for_run_terminal


def _create_group(client: TestClient, name: str = "案件A") -> dict:
    response = client.post("/api/asset-groups", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def _post_run(client: TestClient, asset_group_id: str | None = None, n: int = 2):
    body: dict = {
        "operation": "generate",
        "model": "gpt-image-2.5-sunburst",
        "prompt": "x",
        "params": {"n": n},
    }
    if asset_group_id is not None:
        body["asset_group_id"] = asset_group_id
    return client.post("/api/runs", json=body)


def _run_row(client: TestClient, run_id: str) -> Run:
    with client.app.state.session_factory() as session:
        run = session.get(Run, uuid.UUID(run_id))
        assert run is not None
        return run


def test_outputs_are_added_to_specified_group(client: TestClient) -> None:
    group = _create_group(client, name="生成先")
    response = _post_run(client, asset_group_id=group["id"], n=2)
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]

    detail = wait_for_run_terminal(client, run_id)
    assert detail["status"] == "succeeded"
    output_ids = [o["asset_id"] for o in detail["outputs"]]
    assert len(output_ids) == 2

    listed = client.get("/api/assets", params={"group_id": group["id"]}).json()["items"]
    listed_ids = {a["id"] for a in listed}
    assert set(output_ids) <= listed_ids

    for asset_id in output_ids:
        asset_detail = client.get(f"/api/assets/{asset_id}").json()
        assert {"id": group["id"], "name": "生成先"} in asset_detail["groups"]

    groups = {g["id"]: g for g in client.get("/api/asset-groups").json()["items"]}
    assert groups[group["id"]]["member_count"] == 2

    assert detail["asset_group"] == {"id": group["id"], "name": "生成先"}
    runs = client.get("/api/runs").json()["items"]
    matched = next(r for r in runs if r["id"] == run_id)
    assert matched["asset_group"] == {"id": group["id"], "name": "生成先"}


def test_run_without_group_has_null_asset_group(client: TestClient) -> None:
    response = _post_run(client, n=1)
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]

    detail = wait_for_run_terminal(client, run_id)
    assert detail["status"] == "succeeded"
    assert detail["asset_group"] is None
    runs = client.get("/api/runs").json()["items"]
    assert next(r for r in runs if r["id"] == run_id)["asset_group"] is None
    assert _run_row(client, run_id).asset_group_id is None


def test_unknown_group_on_create_is_404(client: TestClient) -> None:
    response = _post_run(client, asset_group_id=str(uuid.uuid4()))
    assert response.status_code == 404
    assert response.json()["detail"] == "グループが見つかりません"


def test_deleted_group_on_create_is_404_and_no_run_is_created(client: TestClient) -> None:
    group = _create_group(client)
    assert client.delete(f"/api/asset-groups/{group['id']}").status_code == 204

    before = len(client.get("/api/runs").json()["items"])
    response = _post_run(client, asset_group_id=group["id"])
    assert response.status_code == 404
    assert len(client.get("/api/runs").json()["items"]) == before


def test_group_deleted_before_completion_run_still_succeeds(
    client_no_runner: TestClient,
) -> None:
    """Run を作った後、実行完了までにグループが削除された場合: Run は成功し、出力は
    グループに入らず、`asset_group` は null になる。runner を止めたアプリで、完了処理
    (`_finish_run_succeeded`)を直接呼んでタイミングを固定する。"""
    client = client_no_runner
    group = _create_group(client, name="途中で消す")
    response = _post_run(client, asset_group_id=group["id"], n=1)
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]
    assert client.get(f"/api/runs/{run_id}").json()["asset_group"] == {
        "id": group["id"],
        "name": "途中で消す",
    }

    assert client.delete(f"/api/asset-groups/{group['id']}").status_code == 204

    state = client.app.state
    result = SimpleNamespace(
        outputs=[SimpleNamespace(data=make_png_bytes(width=64, height=64))],
        usage=None,
        provider_request_id=None,
    )
    output_ids = _finish_run_succeeded(
        state.session_factory, state.store, uuid.UUID(run_id), result
    )
    assert len(output_ids) == 1

    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["status"] == "succeeded"
    assert detail["asset_group"] is None
    runs = client.get("/api/runs").json()["items"]
    assert next(r for r in runs if r["id"] == run_id)["asset_group"] is None

    # 列そのものは作成時の値のまま残る(追記のみ)。メンバー行は作られない。
    assert _run_row(client, run_id).asset_group_id == uuid.UUID(group["id"])
    with state.session_factory() as session:
        members = session.query(AssetGroupMember).filter_by(asset_id=output_ids[0]).all()
        assert members == []
    assert client.get(f"/api/assets/{output_ids[0]}").json()["groups"] == []


def test_finish_adds_member_once_and_readding_is_ignored(client_no_runner: TestClient) -> None:
    """完了処理で入った出力は1件のメンバーになり、手で再追加しても重複しない。"""
    client = client_no_runner
    group = _create_group(client, name="重複なし")
    run_id = _post_run(client, asset_group_id=group["id"], n=1).json()["id"]

    state = client.app.state
    result = SimpleNamespace(
        outputs=[SimpleNamespace(data=make_png_bytes(width=64, height=64))],
        usage=None,
        provider_request_id=None,
    )
    output_ids = _finish_run_succeeded(
        state.session_factory, state.store, uuid.UUID(run_id), result
    )
    add_again = client.post(
        f"/api/asset-groups/{group['id']}/assets", json={"asset_ids": [str(output_ids[0])]}
    )
    assert add_again.status_code == 200, add_again.text
    assert add_again.json()["member_count"] == 1
