"""`app.tools.backfill_embedded_meta` の単体テスト(ADR-0018)。

HTTP 経由でアップロードした Asset の `embedded_meta` を一旦 null に戻し、
`run_backfill` が実際に埋め戻すこと、既に埋まっている行は再走査してもそのままなこと、
`dry_run` と `--limit`、原本が読めない行の扱いを確認する。
"""

from __future__ import annotations

import io
import uuid

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from PIL.PngImagePlugin import PngInfo
from sqlalchemy import text

from app.domain.models import Asset
from app.tools.backfill_embedded_meta import run_backfill
from tests.conftest import make_png_bytes, wait_for_background_reads

pytestmark = pytest.mark.windows

_A1111_TEXT = "masterpiece, 1girl\nNegative prompt: lowres\nSteps: 20, Sampler: Euler, CFG scale: 7"


def _a1111_png_bytes() -> bytes:
    image = Image.new("RGB", (64, 64), (200, 30, 30))
    info = PngInfo()
    info.add_text("parameters", _A1111_TEXT)
    buffer = io.BytesIO()
    image.save(buffer, "PNG", pnginfo=info)
    return buffer.getvalue()


def _upload(client: TestClient, data: bytes) -> dict:
    response = client.post(
        "/api/assets",
        files={"file": ("f.png", data, "image/png")},
        data={"kind": "upload"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _reset_embedded_meta(client: TestClient, asset_id: str) -> None:
    """既存(この機能より前)の行を模して、`embedded_meta` を SQL の NULL に戻す。

    SQLAlchemy の `JSON` 型は既定で Python の `None` を(SQL の NULL ではなく)JSON の
    `null` として書き込む(`none_as_null` が既定 False のため)。`run_backfill` の対象は
    `Asset.embedded_meta.is_(None)`(= SQL の NULL)なので、生の SQL で更新する。
    """
    session_factory = client.app.state.session_factory
    with session_factory() as session:
        session.execute(
            text("UPDATE asset SET embedded_meta = NULL WHERE id = :id"),
            {"id": uuid.UUID(asset_id).hex},
        )
        session.commit()


def test_backfill_fills_missing_and_leaves_no_meta_rows_null(client: TestClient) -> None:
    with_meta = _upload(client, _a1111_png_bytes())
    without_meta = _upload(client, make_png_bytes())
    _reset_embedded_meta(client, with_meta["id"])
    _reset_embedded_meta(client, without_meta["id"])

    stats = run_backfill(client.app.state.session_factory, client.app.state.store)
    assert stats.scanned == 2
    assert stats.updated == 1
    assert stats.no_meta == 1
    assert stats.missing_blob == 0

    updated = client.get(f"/api/assets/{with_meta['id']}").json()
    assert updated["embedded_meta"] is not None
    assert updated["embedded_meta"]["tool"] == "a1111"

    untouched = client.get(f"/api/assets/{without_meta['id']}").json()
    assert untouched["embedded_meta"] is None

    # 2回目の実行では、既に埋まっている行は対象にならない。
    second = run_backfill(client.app.state.session_factory, client.app.state.store)
    assert second.updated == 0
    # 見つからなかった行は null のままなので、再走査の対象になり続ける。
    assert second.scanned == 1
    assert second.no_meta == 1


def test_backfill_dry_run_does_not_change_the_database(client: TestClient) -> None:
    uploaded = _upload(client, _a1111_png_bytes())
    _reset_embedded_meta(client, uploaded["id"])

    stats = run_backfill(client.app.state.session_factory, client.app.state.store, dry_run=True)
    assert stats.updated == 1

    unchanged = client.get(f"/api/assets/{uploaded['id']}").json()
    assert unchanged["embedded_meta"] is None


def test_backfill_reports_missing_blob(client: TestClient) -> None:
    uploaded = _upload(client, _a1111_png_bytes())
    _reset_embedded_meta(client, uploaded["id"])

    session_factory = client.app.state.session_factory
    store = client.app.state.store
    with session_factory() as session:
        asset = session.get(Asset, uuid.UUID(uploaded["id"]))
        assert asset is not None
        blob_path = store.local_path(asset.blob_key, asset.sha256, "original")
    wait_for_background_reads(client)
    blob_path.unlink()

    stats = run_backfill(session_factory, store)
    assert stats.missing_blob == 1
    assert stats.updated == 0

    still_null = client.get(f"/api/assets/{uploaded['id']}").json()
    assert still_null["embedded_meta"] is None


def test_backfill_respects_limit(client: TestClient) -> None:
    first = _upload(client, _a1111_png_bytes())
    second = _upload(client, _a1111_png_bytes())
    _reset_embedded_meta(client, first["id"])
    _reset_embedded_meta(client, second["id"])

    stats = run_backfill(client.app.state.session_factory, client.app.state.store, limit=1)
    assert stats.scanned == 1
    assert stats.updated == 1
