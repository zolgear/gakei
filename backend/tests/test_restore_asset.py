"""POST /api/assets/{id}/restore(論理削除した Asset の復元。ADR-0008)。"""

from __future__ import annotations

from fastapi.testclient import TestClient

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


def _generate(client: TestClient, prompt: str = "restore test") -> dict:
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


def test_standalone_deleted_upload_is_restorable(client: TestClient) -> None:
    asset_id = _upload(client)
    assert client.delete(f"/api/assets/{asset_id}").status_code == 204

    detail = client.get(f"/api/assets/{asset_id}").json()
    assert detail["restorable"] is True


def test_restore_returns_to_list_and_clears_deleted_at(client: TestClient) -> None:
    asset_id = _upload(client)
    assert client.delete(f"/api/assets/{asset_id}").status_code == 204

    listed_after_delete = client.get("/api/assets").json()
    assert asset_id not in [a["id"] for a in listed_after_delete["items"]]

    restore_response = client.post(f"/api/assets/{asset_id}/restore")
    assert restore_response.status_code == 200
    body = restore_response.json()
    assert body["deleted_at"] is None
    assert body["restorable"] is False

    listed_after_restore = client.get("/api/assets").json()
    assert asset_id in [a["id"] for a in listed_after_restore["items"]]

    detail = client.get(f"/api/assets/{asset_id}").json()
    assert detail["deleted_at"] is None
    assert detail["restorable"] is False


def test_restore_not_deleted_asset_returns_409(client: TestClient) -> None:
    asset_id = _upload(client)
    response = client.post(f"/api/assets/{asset_id}/restore")
    assert response.status_code == 409


def test_restore_unknown_asset_returns_404(client: TestClient) -> None:
    response = client.post("/api/assets/00000000-0000-0000-0000-000000000000/restore")
    assert response.status_code == 404


def test_output_of_deleted_run_is_not_restorable_and_returns_409(client: TestClient) -> None:
    detail = _generate(client)
    output_asset_id = detail["outputs"][0]["asset_id"]

    assert client.delete(f"/api/runs/{detail['id']}").status_code == 204

    asset_detail = client.get(f"/api/assets/{output_asset_id}").json()
    assert asset_detail["deleted_at"] is not None
    assert asset_detail["restorable"] is False

    restore_response = client.post(f"/api/assets/{output_asset_id}/restore")
    assert restore_response.status_code == 409

    # 出力は一覧にも戻ってこない。
    listed = client.get("/api/assets").json()
    assert output_asset_id not in [a["id"] for a in listed["items"]]


def test_directly_deleted_output_asset_is_restorable_when_run_is_not_deleted(
    client: TestClient,
) -> None:
    """Run自体は削除せず、出力Assetだけを個別に削除した場合は復元できる。"""
    detail = _generate(client)
    output_asset_id = detail["outputs"][0]["asset_id"]

    assert client.delete(f"/api/assets/{output_asset_id}").status_code == 204

    asset_detail = client.get(f"/api/assets/{output_asset_id}").json()
    assert asset_detail["restorable"] is True

    restore_response = client.post(f"/api/assets/{output_asset_id}/restore")
    assert restore_response.status_code == 200
    assert restore_response.json()["deleted_at"] is None
