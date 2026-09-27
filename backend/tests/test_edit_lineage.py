"""edit の run_input 記録とリネージ(position=0 の親を辿れること)。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import make_png_bytes, wait_for_run_terminal


def _upload(client: TestClient, width: int = 256, height: int = 256) -> str:
    data = make_png_bytes(width=width, height=height)
    response = client.post(
        "/api/assets",
        files={"file": ("base.png", data, "image/png")},
        data={"kind": "upload"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_edit_records_position_zero_parent_and_is_traceable(client: TestClient) -> None:
    base_asset_id = _upload(client)

    response = client.post(
        "/api/runs",
        json={
            "operation": "edit",
            "model": "gpt-image-2.5-sunburst",
            "prompt": "make it look like sunset",
            "params": {"n": 1},
            "inputs": [{"asset_id": base_asset_id, "role": "image", "position": 0}],
        },
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]

    detail = wait_for_run_terminal(client, run_id)
    assert detail["status"] == "succeeded"
    assert len(detail["inputs"]) == 1
    assert detail["inputs"][0]["asset_id"] == base_asset_id
    assert detail["inputs"][0]["role"] == "image"
    assert detail["inputs"][0]["position"] == 0

    output_asset_id = detail["outputs"][0]["asset_id"]
    output_detail = client.get(f"/api/assets/{output_asset_id}").json()
    assert output_detail["produced_by_run"]["id"] == run_id
    assert output_detail["produced_by_run"]["operation"] == "edit"


def test_descendant_run_count_tracks_downstream_edits_and_deletion(client: TestClient) -> None:
    """generate の出力を使って edit すると、generate 側の descendant_run_count が増える。
    edit の Run を削除すると 0 に戻る(削除確認ダイアログの警告に使う値)。
    """
    generate_response = client.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": "gpt-image-2.5-sunburst",
            "prompt": "a base image for descendant count check",
            "params": {"n": 1, "size": "1024x1024"},
        },
    )
    assert generate_response.status_code == 202, generate_response.text
    generate_run_id = generate_response.json()["id"]
    generate_detail = wait_for_run_terminal(client, generate_run_id)
    generate_output_asset_id = generate_detail["outputs"][0]["asset_id"]

    def _run_summary(run_id: str) -> dict:
        listed = client.get("/api/runs").json()
        return next(item for item in listed["items"] if item["id"] == run_id)

    assert _run_summary(generate_run_id)["descendant_run_count"] == 0

    edit_response = client.post(
        "/api/runs",
        json={
            "operation": "edit",
            "model": "gpt-image-2.5-sunburst",
            "prompt": "edit the base image for descendant count check",
            "params": {"n": 1},
            "inputs": [{"asset_id": generate_output_asset_id, "role": "image", "position": 0}],
        },
    )
    assert edit_response.status_code == 202, edit_response.text
    edit_run_id = edit_response.json()["id"]
    wait_for_run_terminal(client, edit_run_id)

    assert _run_summary(generate_run_id)["descendant_run_count"] == 1
    assert _run_summary(edit_run_id)["descendant_run_count"] == 0

    # RunDetail(単体取得)でも同じ値が乗ること。
    generate_detail_again = client.get(f"/api/runs/{generate_run_id}").json()
    assert generate_detail_again["descendant_run_count"] == 1

    delete_response = client.delete(f"/api/runs/{edit_run_id}")
    assert delete_response.status_code == 204, delete_response.text

    assert _run_summary(generate_run_id)["descendant_run_count"] == 0
