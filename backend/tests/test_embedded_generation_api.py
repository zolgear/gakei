"""ADR-0018 の API 経路: `asset.embedded_meta` の応答への反映。

前半(取り込みへの組み込み前)は DB の `embedded_meta` 列に直接値を書き込んで
`GET /api/assets/{id}` が期待どおりの `EmbeddedGenerationMeta` を返すことを確認する。
後半は `ingest_upload`(`domain/generation_meta.py`)が実際に PNG を解析して列に保存する
経路を、`POST /api/assets` を通して確認する。
"""

from __future__ import annotations

import io
import uuid

from fastapi.testclient import TestClient
from PIL.PngImagePlugin import PngInfo

from app.domain.models import Asset
from tests.conftest import make_png_bytes, wait_for_run_terminal

_A1111_TEXT = "masterpiece, 1girl\nNegative prompt: lowres\nSteps: 20, Sampler: Euler, CFG scale: 7"


def _upload(client: TestClient) -> dict:
    data = make_png_bytes()
    response = client.post(
        "/api/assets",
        files={"file": ("f.png", data, "image/png")},
        data={"kind": "upload"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _upload_bytes(client: TestClient, data: bytes, *, kind: str = "upload") -> dict:
    response = client.post(
        "/api/assets",
        files={"file": ("f.png", data, "image/png")},
        data={"kind": kind},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _a1111_png_bytes() -> bytes:
    from PIL import Image

    image = Image.new("RGB", (64, 64), (200, 30, 30))
    info = PngInfo()
    info.add_text("parameters", _A1111_TEXT)
    buffer = io.BytesIO()
    image.save(buffer, "PNG", pnginfo=info)
    return buffer.getvalue()


def _set_embedded_meta(client: TestClient, asset_id: str, value: dict | None) -> None:
    session_factory = client.app.state.session_factory
    with session_factory() as session:
        asset = session.get(Asset, uuid.UUID(asset_id))
        assert asset is not None
        asset.embedded_meta = value
        session.commit()


def test_embedded_meta_is_returned_without_schema_key(client: TestClient) -> None:
    uploaded = _upload(client)
    _set_embedded_meta(
        client,
        uploaded["id"],
        {
            "schema": "gakei.embedded/1",
            "tool": "a1111",
            "prompt": "cat",
            "params": {"Steps": "20"},
            "raw": {"parameters": "cat\nSteps: 20"},
            "truncated": False,
        },
    )

    response = client.get(f"/api/assets/{uploaded['id']}")
    assert response.status_code == 200, response.text
    body = response.json()

    embedded = body["embedded_meta"]
    assert embedded is not None
    assert embedded["tool"] == "a1111"
    assert embedded["prompt"] == "cat"
    assert embedded["verified"] is False
    assert "schema" not in embedded


def test_broken_embedded_meta_is_hidden_instead_of_500(client: TestClient) -> None:
    uploaded = _upload(client)
    _set_embedded_meta(client, uploaded["id"], {"tool": "unknown"})

    response = client.get(f"/api/assets/{uploaded['id']}")
    assert response.status_code == 200, response.text
    assert response.json()["embedded_meta"] is None


def test_openapi_exposes_embedded_generation_meta_schema(client: TestClient) -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200, response.text
    schemas = response.json()["components"]["schemas"]
    assert "EmbeddedGenerationMeta" in schemas


# -- 取り込み(ingest_upload)への組み込み(2026-09-26 追記) ---------------------


def test_upload_of_a1111_png_returns_embedded_meta_on_create(client: TestClient) -> None:
    uploaded = _upload_bytes(client, _a1111_png_bytes())
    embedded = uploaded["embedded_meta"]
    assert embedded is not None
    assert embedded["tool"] == "a1111"
    assert embedded["prompt"] == "masterpiece, 1girl"
    assert embedded["verified"] is False

    response = client.get(f"/api/assets/{uploaded['id']}")
    assert response.status_code == 200, response.text
    assert response.json()["embedded_meta"]["tool"] == "a1111"


def test_upload_without_embedded_meta_is_null(client: TestClient) -> None:
    uploaded = _upload_bytes(client, make_png_bytes())
    assert uploaded["embedded_meta"] is None


def test_upload_with_mask_kind_does_not_analyze_embedded_meta(client: TestClient) -> None:
    uploaded = _upload_bytes(client, _a1111_png_bytes(), kind="mask")
    assert uploaded["kind"] == "mask"
    assert uploaded["embedded_meta"] is None


def test_reupload_matching_existing_asset_does_not_change_its_embedded_meta(
    client: TestClient,
) -> None:
    """`GET .../content?download=1` で取得した PNG(gakei の系列情報つき)を
    再アップロードして `matched_existing` になっても、既存行(generated、embedded_meta は
    元々 null)は変更されない(ADR-0018: 追記のみ・upload のみが対象)。
    """
    response = client.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": "gpt-image-2.5-sunburst",
            "prompt": "ADR-0018 再アップロードテスト",
            "params": {"n": 1, "size": "1024x1024"},
        },
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]
    detail = wait_for_run_terminal(client, run_id)
    assert detail["status"] == "succeeded", detail
    asset_id = detail["outputs"][0]["asset_id"]

    before = client.get(f"/api/assets/{asset_id}").json()
    assert before["embedded_meta"] is None

    downloaded = client.get(
        f"/api/assets/{asset_id}/content", params={"variant": "original", "download": 1}
    )
    assert downloaded.status_code == 200, downloaded.text

    result = _upload_bytes(client, downloaded.content)
    assert result["ingest_outcome"] == "matched_existing"
    assert result["id"] == asset_id
    assert result["embedded_meta"] is None

    after = client.get(f"/api/assets/{asset_id}").json()
    assert after["embedded_meta"] is None
