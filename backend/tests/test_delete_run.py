"""DELETE /api/runs/{id}(論理削除)。終了状態のみ削除でき、出力Assetも同時に削除される(ADR-0008)。"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.domain.models import Run, RunStatus
from tests.conftest import make_png_bytes, wait_for_run_terminal


def _upload(client: TestClient) -> str:
    data = make_png_bytes(width=128, height=128)
    response = client.post(
        "/api/assets",
        files={"file": ("f.png", data, "image/png")},
        data={"kind": "upload"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _generate(client: TestClient, prompt: str = "delete test") -> dict:
    response = client.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": "gpt-image-2.5-sunburst",
            "prompt": prompt,
            "params": {"n": 1},
        },
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]
    return wait_for_run_terminal(client, run_id)


def test_delete_succeeded_run_removes_from_history_and_cascades_to_outputs(
    client: TestClient,
) -> None:
    detail = _generate(client)
    run_id = detail["id"]
    output_asset_id = detail["outputs"][0]["asset_id"]

    delete_response = client.delete(f"/api/runs/{run_id}")
    assert delete_response.status_code == 204

    listed = client.get("/api/runs").json()
    ids = [r["id"] for r in listed["items"]]
    assert run_id not in ids

    run_detail = client.get(f"/api/runs/{run_id}")
    assert run_detail.status_code == 200
    assert run_detail.json()["deleted_at"] is not None
    # outputs は RunDetail に残ったまま(削除済み Asset も含めて出す)。
    assert run_detail.json()["outputs"][0]["asset_id"] == output_asset_id

    asset_detail = client.get(f"/api/assets/{output_asset_id}")
    assert asset_detail.status_code == 200
    assert asset_detail.json()["deleted_at"] is not None

    asset_list = client.get("/api/assets").json()
    asset_ids = [a["id"] for a in asset_list["items"]]
    assert output_asset_id not in asset_ids


def test_delete_queued_run_returns_409(client_no_runner: TestClient) -> None:
    response = client_no_runner.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": "gpt-image-2.5-sunburst",
            "prompt": "queued run cannot be deleted",
            "params": {"n": 1},
        },
    )
    run_id = response.json()["id"]

    delete_response = client_no_runner.delete(f"/api/runs/{run_id}")
    assert delete_response.status_code == 409


def test_delete_running_run_returns_409(client_no_runner: TestClient) -> None:
    response = client_no_runner.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": "gpt-image-2.5-sunburst",
            "prompt": "running run cannot be deleted",
            "params": {"n": 1},
        },
    )
    run_id = response.json()["id"]

    # runner を止めているので、直接 DB を触って running 状態を作る。
    session_factory = client_no_runner.app.state.session_factory
    with session_factory() as session:
        run = session.get(Run, uuid.UUID(run_id))
        run.status = RunStatus.RUNNING
        session.commit()

    delete_response = client_no_runner.delete(f"/api/runs/{run_id}")
    assert delete_response.status_code == 409


def test_double_delete_run_returns_404(client: TestClient) -> None:
    detail = _generate(client)
    run_id = detail["id"]
    assert client.delete(f"/api/runs/{run_id}").status_code == 204
    assert client.delete(f"/api/runs/{run_id}").status_code == 404


def test_delete_unknown_run_returns_404(client_no_runner: TestClient) -> None:
    response = client_no_runner.delete("/api/runs/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


def test_deleting_one_asset_does_not_affect_run_still_referencing_it(
    client: TestClient,
) -> None:
    """削除済み Asset を入力に使っている別の Run は影響を受けない。"""
    base_asset_id = _upload(client)

    edit_response = client.post(
        "/api/runs",
        json={
            "operation": "edit",
            "model": "gpt-image-2.5-sunburst",
            "prompt": "edit using base asset",
            "params": {"n": 1},
            "inputs": [{"asset_id": base_asset_id, "role": "image", "position": 0}],
        },
    )
    assert edit_response.status_code == 202, edit_response.text
    edit_run_id = edit_response.json()["id"]
    edit_detail = wait_for_run_terminal(client, edit_run_id)
    assert edit_detail["status"] == "succeeded"

    # base_asset_id を直接削除する(edit_run 自体は削除しない)。
    assert client.delete(f"/api/assets/{base_asset_id}").status_code == 204

    # edit_run はまだ生きているので、履歴からも引き続き見え、入力として base_asset_id を参照できる。
    listed = client.get("/api/runs").json()
    ids = [r["id"] for r in listed["items"]]
    assert edit_run_id in ids

    run_detail = client.get(f"/api/runs/{edit_run_id}").json()
    assert run_detail["inputs"][0]["asset_id"] == base_asset_id
    assert run_detail["primary_parent_asset_id"] == base_asset_id

    # 削除済みだが、参照(詳細取得)はできる。
    asset_detail = client.get(f"/api/assets/{base_asset_id}").json()
    assert asset_detail["deleted_at"] is not None
