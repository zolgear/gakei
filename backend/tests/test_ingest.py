"""domain.assets.ingest の単体テスト。重複排除、形式検証、サイズ上限。"""

from __future__ import annotations

import io
import struct
import zlib

import pytest
from PIL import Image
from sqlalchemy.orm import sessionmaker

from app.domain.assets import IngestError, ingest
from app.domain.image_limits import MAX_IMAGE_PIXELS
from app.domain.models import Asset, AssetKind
from app.domain.storage import LocalFsStore
from tests.conftest import make_png_bytes
from tests.png_bombs import large_bilevel_png

pytestmark = pytest.mark.windows


def test_same_content_shares_blob_but_creates_separate_assets(
    db_session_factory: sessionmaker, local_store: LocalFsStore
) -> None:
    data = make_png_bytes()
    with db_session_factory() as session:
        asset1 = ingest(session, local_store, data, AssetKind.UPLOAD)
        asset2 = ingest(session, local_store, data, AssetKind.UPLOAD)
        session.commit()

        assert asset1.id != asset2.id
        assert asset1.blob_key == asset2.blob_key
        assert asset1.sha256 == asset2.sha256

        rows = session.query(Asset).all()
        assert len(rows) == 2


def png_with_declared_size(width: int, height: int) -> bytes:
    """IHDR の寸法だけを書き換えた PNG(展開爆弾の形。中身は 1x1)。"""
    data = bytearray(make_png_bytes(1, 1, (1, 2, 3)))
    # シグネチャ 8 バイト + 長さ 4 + "IHDR" 4 の後ろが幅・高さ。CRC は "IHDR" から数える。
    data[16:24] = struct.pack(">II", width, height)
    data[29:33] = struct.pack(">I", zlib.crc32(bytes(data[12:29])) & 0xFFFFFFFF)
    return bytes(data)


def test_ingest_rejects_decompression_bomb(
    db_session_factory: sessionmaker, local_store: LocalFsStore
) -> None:
    """Pillow の上限を超える寸法は、想定外の例外ではなく IngestError にする(Issue #82)。"""
    with db_session_factory() as session, pytest.raises(IngestError):
        ingest(session, local_store, png_with_declared_size(60_000, 60_000), AssetKind.UPLOAD)


def test_ingest_rejects_image_over_pixel_limit_before_decoding(
    db_session_factory: sessionmaker, local_store: LocalFsStore
) -> None:
    """Pillow の既定の上限より小さくても、取り込みの上限(64M 画素)を超える寸法は断る。"""
    side = 8193  # 8193 x 8193 = 67,125,249 > 67,108,864
    assert side * side > MAX_IMAGE_PIXELS
    data = large_bilevel_png(side, side)
    assert len(data) < 300 * 1024
    with db_session_factory() as session, pytest.raises(IngestError):
        ingest(session, local_store, data, AssetKind.UPLOAD)


def test_ingest_pixel_limit_boundary(
    db_session_factory: sessionmaker, local_store: LocalFsStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """上限ちょうどの寸法は通し、1 画素でも超えたら断る。"""
    from app.domain import assets, image_limits

    data = make_png_bytes(64, 32, (1, 2, 3))
    monkeypatch.setattr(
        assets, "exceeds_pixel_limit", lambda img: image_limits.exceeds_pixel_limit(img, 64 * 32)
    )
    with db_session_factory() as session:
        asset = ingest(session, local_store, data, AssetKind.UPLOAD)
        assert (asset.width, asset.height) == (64, 32)
    monkeypatch.setattr(
        assets,
        "exceeds_pixel_limit",
        lambda img: image_limits.exceeds_pixel_limit(img, 64 * 32 - 1),
    )
    with db_session_factory() as session, pytest.raises(IngestError):
        ingest(session, local_store, data, AssetKind.UPLOAD)


def test_upload_api_rejects_image_over_pixel_limit(client) -> None:
    data = large_bilevel_png(8193, 8193)
    response = client.post(
        "/api/assets",
        files={"file": ("big.png", data, "image/png")},
        data={"kind": "upload"},
    )
    assert response.status_code == 422, response.text
    assert "67,108,864" in response.json()["detail"]


def test_ingest_rejects_non_image_bytes(
    db_session_factory: sessionmaker, local_store: LocalFsStore
) -> None:
    with db_session_factory() as session, pytest.raises(IngestError):
        ingest(session, local_store, b"not an image", AssetKind.UPLOAD)


def test_ingest_rejects_mask_that_is_not_png(
    db_session_factory: sessionmaker, local_store: LocalFsStore
) -> None:
    image = Image.new("RGB", (32, 32), (0, 0, 0))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")

    with db_session_factory() as session, pytest.raises(IngestError):
        ingest(session, local_store, buffer.getvalue(), AssetKind.MASK)


def test_ingest_rejects_oversized_mask(
    db_session_factory: sessionmaker, local_store: LocalFsStore
) -> None:
    with db_session_factory() as session, pytest.raises(IngestError):
        # 4MB制限をダミーの大きいバイト列で擬似的に超えさせる(先頭検証で弾かれる)
        oversized = b"x" * (4 * 1024 * 1024)
        ingest(session, local_store, oversized, AssetKind.MASK)


def test_ingest_generates_derivatives(
    db_session_factory: sessionmaker, local_store: LocalFsStore
) -> None:
    data = make_png_bytes(width=1000, height=500)
    with db_session_factory() as session:
        asset = ingest(session, local_store, data, AssetKind.UPLOAD)
        session.commit()

        thumb_path = local_store.local_path(asset.blob_key, asset.sha256, "thumb")
        preview_path = local_store.local_path(asset.blob_key, asset.sha256, "preview")
        assert thumb_path.exists()
        assert preview_path.exists()

        with Image.open(thumb_path) as thumb:
            assert max(thumb.size) == 512
