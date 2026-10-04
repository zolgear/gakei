"""DELETE /api/assets/{id}(論理削除)。ファイルは消さず、一覧から消える(ADR-0008)。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import make_png_bytes

pytestmark = pytest.mark.windows


def _upload(client: TestClient) -> str:
    data = make_png_bytes(width=128, height=128)
    response = client.post(
        "/api/assets",
        files={"file": ("f.png", data, "image/png")},
        data={"kind": "upload"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_delete_asset_removes_from_list_but_detail_and_content_still_work(
    client: TestClient,
) -> None:
    asset_id = _upload(client)

    delete_response = client.delete(f"/api/assets/{asset_id}")
    assert delete_response.status_code == 204

    listed = client.get("/api/assets").json()
    ids = [a["id"] for a in listed["items"]]
    assert asset_id not in ids

    detail = client.get(f"/api/assets/{asset_id}")
    assert detail.status_code == 200
    assert detail.json()["deleted_at"] is not None

    content = client.get(f"/api/assets/{asset_id}/content", params={"variant": "thumb"})
    assert content.status_code == 200
    assert content.headers["content-type"] == "image/webp"


def test_deleted_asset_cannot_be_used_as_run_input(client: TestClient) -> None:
    asset_id = _upload(client)
    assert client.delete(f"/api/assets/{asset_id}").status_code == 204

    response = client.post(
        "/api/runs",
        json={
            "operation": "edit",
            "model": "gpt-image-2.5-sunburst",
            "prompt": "use a deleted asset",
            "params": {"n": 1},
            "inputs": [{"asset_id": asset_id, "role": "image", "position": 0}],
        },
    )
    assert response.status_code == 422


def test_double_delete_asset_returns_404(client: TestClient) -> None:
    asset_id = _upload(client)
    assert client.delete(f"/api/assets/{asset_id}").status_code == 204
    assert client.delete(f"/api/assets/{asset_id}").status_code == 404


def test_delete_unknown_asset_returns_404(client: TestClient) -> None:
    response = client.delete("/api/assets/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
