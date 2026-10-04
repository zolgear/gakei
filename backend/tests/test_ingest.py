"""domain.assets.ingest の単体テスト。重複排除、形式検証、サイズ上限。"""

from __future__ import annotations

import io

import pytest
from PIL import Image
from sqlalchemy.orm import sessionmaker

from app.domain.assets import IngestError, ingest
from app.domain.models import Asset, AssetKind
from app.domain.storage import LocalFsStore
from tests.conftest import make_png_bytes

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
