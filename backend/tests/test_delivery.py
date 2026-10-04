"""配信APIの検証。variant ごとの Content-Type、ETag、If-None-Match -> 304。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import make_png_bytes

pytestmark = pytest.mark.windows


def _upload(client: TestClient) -> dict:
    data = make_png_bytes(width=800, height=400)
    response = client.post(
        "/api/assets",
        files={"file": ("test.png", data, "image/png")},
        data={"kind": "upload"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_content_variants_have_expected_content_type(client: TestClient) -> None:
    asset = _upload(client)
    asset_id = asset["id"]

    thumb = client.get(f"/api/assets/{asset_id}/content", params={"variant": "thumb"})
    assert thumb.status_code == 200
    assert thumb.headers["content-type"] == "image/webp"

    preview = client.get(f"/api/assets/{asset_id}/content", params={"variant": "preview"})
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "image/webp"

    original = client.get(f"/api/assets/{asset_id}/content", params={"variant": "original"})
    assert original.status_code == 200
    assert original.headers["content-type"] == "image/png"


def test_etag_and_if_none_match_returns_304(client: TestClient) -> None:
    asset = _upload(client)
    asset_id = asset["id"]

    first = client.get(f"/api/assets/{asset_id}/content", params={"variant": "preview"})
    etag = first.headers["etag"]
    assert etag

    second = client.get(
        f"/api/assets/{asset_id}/content",
        params={"variant": "preview"},
        headers={"If-None-Match": etag},
    )
    assert second.status_code == 304


def test_download_sets_content_disposition(client: TestClient) -> None:
    asset = _upload(client)
    asset_id = asset["id"]

    response = client.get(
        f"/api/assets/{asset_id}/content",
        params={"variant": "original", "download": 1},
    )
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
