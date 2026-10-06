"""オブジェクトストレージ(S3。既定は moto)を保存先にしたアプリの配信(ADR-0028 4章)。

生成 → `/api/assets/{id}/content` の各 variant、`download=1` の PNG への系列情報の埋め込み、
404、304、MCP の `get_image`・`get_asset`(透過の情報)・`create_download_url` を、S3 の
ストアで通す。Azurite があれば Azure Blob でも同じテストを回す。
"""

from __future__ import annotations

import base64
import io
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import app.main as app_main
from app.domain import agent_images
from app.domain.embedded_meta import read_gakei_meta
from app.domain.models import Asset
from tests.conftest import _fake_settings, make_png_bytes, wait_for_run_terminal
from tests.test_mcp import _call, _enable, _ok
from tests.test_mcp_feedback import _path_of, _rgba_png, _upload_via_url


@pytest.fixture(params=["s3", "azure_blob"])
def object_client(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, data_dir: Path
) -> Iterator[TestClient]:
    """画像の保存先をオブジェクトストレージ(テストごとの接頭辞の下)にしたアプリ。"""
    store = request.getfixturevalue(f"{request.param}_store")
    monkeypatch.setattr(app_main, "open_store", lambda _settings: store)
    agent_images.clear_cache()
    app = app_main.create_app(_fake_settings(data_dir))
    with TestClient(app) as test_client:
        assert test_client.app.state.store is store
        yield test_client


def _generate_png(client: TestClient) -> str:
    response = client.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": "gpt-image-2.5-sunburst",
            "prompt": "ADR-0028 オブジェクトストレージの配信",
            "params": {"n": 1, "size": "1024x1024", "output_format": "png"},
        },
    )
    assert response.status_code == 202, response.text
    detail = wait_for_run_terminal(client, response.json()["id"])
    assert detail["status"] == "succeeded", detail
    return detail["outputs"][0]["asset_id"]


def _asset(client: TestClient, asset_id: str) -> Asset:
    with client.app.state.session_factory() as db:
        asset = db.get(Asset, uuid.UUID(asset_id))
        assert asset is not None
        db.expunge(asset)
        return asset


def test_content_variants_are_served_from_object_storage(
    object_client: TestClient, data_dir: Path
) -> None:
    asset_id = _generate_png(object_client)
    asset = _asset(object_client, asset_id)
    store: Any = object_client.app.state.store

    for variant, media_type in (
        ("thumb", "image/webp"),
        ("preview", "image/webp"),
        ("original", "image/png"),
    ):
        response = object_client.get(f"/api/assets/{asset_id}/content?variant={variant}")
        assert response.status_code == 200, (variant, response.text)
        assert response.headers["content-type"] == media_type
        suffix = "" if variant == "original" else "-d1"  # 派生は版を含む(ADR-0036)
        assert response.headers["etag"] == f'"{asset.sha256}-{variant}{suffix}"'
        assert response.headers["content-length"] == str(len(response.content))
        key = asset.blob_key if variant == "original" else f"derived/{asset.sha256}/{variant}.webp"
        assert response.content == store.read(key)

        # 同じ ETag なら 304(本文なし)
        cached = object_client.get(
            f"/api/assets/{asset_id}/content?variant={variant}",
            headers={"If-None-Match": response.headers["etag"]},
        )
        assert cached.status_code == 304
        assert cached.content == b""

    with Image.open(io.BytesIO(store.read(asset.blob_key))) as image:
        assert image.size == (1024, 1024)
    # ローカルFSには書いていない(アバターなどの `DATA_DIR` は別)。
    assert not (data_dir / "assets").exists()
    assert not (data_dir / "derived").exists()


def test_download_original_png_embeds_lineage(object_client: TestClient) -> None:
    asset_id = _generate_png(object_client)
    asset = _asset(object_client, asset_id)
    response = object_client.get(f"/api/assets/{asset_id}/content?variant=original&download=1")
    assert response.status_code == 200
    assert (
        response.headers["content-disposition"] == f'attachment; filename="{asset_id}-original.png"'
    )
    assert response.headers["etag"] == f'"{asset.sha256}-original-gakei1"'
    meta = read_gakei_meta(response.content)
    assert meta is not None
    assert meta["root"] == asset_id
    # 保存している原本は変えない
    assert read_gakei_meta(object_client.app.state.store.read(asset.blob_key)) is None


def test_missing_content_is_404(object_client: TestClient) -> None:
    unknown = object_client.get(f"/api/assets/{uuid.uuid4()}/content?variant=original")
    assert unknown.status_code == 404

    uploaded = object_client.post(
        "/api/assets",
        files={"file": ("f.png", make_png_bytes(), "image/png")},
        data={"kind": "upload"},
    )
    assert uploaded.status_code == 201, uploaded.text
    asset_id = uploaded.json()["id"]
    asset = _asset(object_client, asset_id)
    store = object_client.app.state.store
    thumb_key = f"derived/{asset.sha256}/thumb.webp"
    expected = store.read(thumb_key)
    _delete_object(store, thumb_key)

    # ADR-0036: 派生が無くても、原本があれば作り直して返す(保存もする)。
    regenerated = object_client.get(f"/api/assets/{asset_id}/content?variant=thumb")
    assert regenerated.status_code == 200
    assert regenerated.content == expected
    assert store.read(thumb_key) == expected

    # 原本も派生も無ければ 404。If-None-Match が合っていても 304 ではなく 404。
    _delete_object(store, thumb_key)
    _delete_object(store, asset.blob_key)
    missing = object_client.get(f"/api/assets/{asset_id}/content?variant=thumb")
    assert missing.status_code == 404
    missing_cached = object_client.get(
        f"/api/assets/{asset_id}/content?variant=thumb",
        headers={"If-None-Match": f'"{asset.sha256}-thumb-d1"'},
    )
    assert missing_cached.status_code == 404
    assert object_client.get(f"/api/assets/{asset_id}/content?variant=preview").status_code == 200


def _delete_object(store: Any, key: str) -> None:
    if store.kind == "s3":
        store._client.delete_object(Bucket=store.bucket, Key=store.prefix + key)
    else:
        store._container.delete_blob(store.prefix + key)


def test_mcp_image_access_and_download_url(object_client: TestClient) -> None:
    _enable(object_client)
    asset_id = _upload_via_url(object_client, _rgba_png(40, 20, transparent_columns=10))

    detail = _ok(_call(object_client, "get_asset", {"asset_id": asset_id}))
    assert detail["has_alpha"] is True
    assert detail["transparent_ratio"] == pytest.approx(0.25)

    result = _call(object_client, "get_image", {"asset_id": asset_id})
    images = [c for c in result["content"] if c["type"] == "image"]
    assert len(images) == 1
    assert images[0]["mimeType"] == "image/png"
    with Image.open(io.BytesIO(base64.b64decode(images[0]["data"]))) as image:
        assert image.size == (40, 20)

    issued = _ok(_call(object_client, "create_download_url", {"asset_id": asset_id}))
    path = _path_of(issued["download_url"])
    response = object_client.get(path)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/png"
    assert "no-store" in response.headers["cache-control"]
    assert read_gakei_meta(response.content) is not None
    assert object_client.get(path).status_code == 404
