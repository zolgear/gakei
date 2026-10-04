"""埋め込みの保存(ADR-0033 4章): マイグレーション、pgvector の列と索引、検索の方式。

PostgreSQL のテストは `GAKEI_TEST_DATABASE_URL` が無ければ skip する。拡張 `vector` を入れて
いない PostgreSQL(`postgres:17` など)では、列を足さずに numpy に切り替わることを確かめる。
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

from app.domain import embedding_index
from app.embedding.base import vector_to_blob
from app.embedding.catalog import XENOVA_CLIP_REVISION
from tests.conftest import make_png_bytes, requires_postgresql, using_postgresql

ACTIVE_KEY = f"fake:onnx:clip-vit-b32-u8@{XENOVA_CLIP_REVISION}"


def _vector_extension_available(url: str) -> bool:
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return (
                conn.execute(
                    text("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'")
                ).first()
                is not None
            )
    finally:
        engine.dispose()


def test_migration_0024_up_and_down(empty_database_url: str) -> None:
    from alembic import command

    from app.main import alembic_config

    url = empty_database_url
    cfg = alembic_config(url)
    command.upgrade(cfg, "0023")
    command.upgrade(cfg, "head")

    engine = create_engine(url)
    try:
        inspector = inspect(engine)
        assert "asset_embedding" in inspector.get_table_names()
        columns = {c["name"] for c in inspector.get_columns("asset_embedding")}
        assert {
            "asset_id",
            "model_key",
            "status",
            "dim",
            "vector",
            "error",
            "requested_at",
            "finished_at",
            "updated_at",
        } <= columns
        assert inspector.get_pk_constraint("asset_embedding")["constrained_columns"] == [
            "asset_id",
            "model_key",
        ]
        indexes = {i["name"]: i["column_names"] for i in inspector.get_indexes("asset_embedding")}
        assert indexes["ix_asset_embedding_model_key_status"] == ["model_key", "status"]
        if using_postgresql():
            # 拡張を使えるときだけ `embedding` 列がある(使えなくてもマイグレーションは通る)。
            assert ("embedding" in columns) == _vector_extension_available(url)
        else:
            assert "embedding" not in columns
    finally:
        engine.dispose()

    command.downgrade(cfg, "0023")
    engine = create_engine(url)
    try:
        assert "asset_embedding" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()
    command.upgrade(cfg, "head")


def test_migration_0025_up_and_down(empty_database_url: str) -> None:
    """知覚ハッシュのテーブル(ADR-0033 12章)。"""
    from alembic import command

    from app.main import alembic_config

    url = empty_database_url
    cfg = alembic_config(url)
    command.upgrade(cfg, "0024")
    command.upgrade(cfg, "0025")

    engine = create_engine(url)
    try:
        inspector = inspect(engine)
        assert "asset_perceptual_hash" in inspector.get_table_names()
        columns = {c["name"]: c for c in inspector.get_columns("asset_perceptual_hash")}
        assert set(columns) == {"asset_id", "dhash", "color", "version", "created_at"}
        assert not any(c["nullable"] for c in columns.values())
        assert inspector.get_pk_constraint("asset_perceptual_hash")["constrained_columns"] == [
            "asset_id"
        ]
        fks = inspector.get_foreign_keys("asset_perceptual_hash")
        assert [(fk["referred_table"], fk["referred_columns"]) for fk in fks] == [("asset", ["id"])]
    finally:
        engine.dispose()

    command.downgrade(cfg, "0024")
    engine = create_engine(url)
    try:
        tables = inspect(engine).get_table_names()
        assert "asset_perceptual_hash" not in tables
        assert "asset_embedding" in tables
    finally:
        engine.dispose()
    command.upgrade(cfg, "head")


def test_sqlite_uses_numpy(client: TestClient) -> None:
    if using_postgresql():
        pytest.skip("SQLite に固有")
    assert embedding_index.prepare(client.app.state.engine) == "numpy"
    assert client.get("/api/settings/embeddings").json()["index_backend"] == "numpy"


def test_vector_literal_roundtrips_float32() -> None:
    values = np.array([0.1, -0.25, 1e-8, 0.333333343], dtype=np.float32)
    literal = embedding_index.vector_literal(values)
    assert literal.startswith("[") and literal.endswith("]")
    parsed = np.array([float(v) for v in literal[1:-1].split(",")], dtype=np.float32)
    assert np.array_equal(parsed, values)


def test_hnsw_index_name_is_stable_and_safe() -> None:
    name = embedding_index.hnsw_index_name("remote:abc:org/model'; DROP")
    assert name.startswith("ix_asset_embedding_hnsw_")
    assert name.replace("_", "").isalnum()
    assert len(name) <= 63
    assert name == embedding_index.hnsw_index_name("remote:abc:org/model'; DROP")
    assert embedding_index.quote_literal("a'b") == "'a''b'"


def _wait_succeeded(client: TestClient, asset_id: str) -> None:
    from app.domain.models import AssetEmbedding

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with client.app.state.session_factory() as session:
            row = session.get(AssetEmbedding, (uuid.UUID(asset_id), ACTIVE_KEY))
            if row is not None and row.status == "succeeded":
                return
        time.sleep(0.05)
    raise TimeoutError(asset_id)


@requires_postgresql
def test_postgresql_worker_writes_pgvector_column_and_index(client: TestClient) -> None:
    engine = client.app.state.engine
    url = engine.url.render_as_string(hide_password=False)
    available = _vector_extension_available(url)
    backend = client.get("/api/settings/embeddings").json()["index_backend"]
    assert backend == ("pgvector" if available else "numpy")

    assert client.patch("/api/settings/embeddings", json={"enabled": True}).status_code == 200
    asset = client.post(
        "/api/assets",
        files={"file": ("a.png", make_png_bytes(), "image/png")},
        data={"kind": "upload"},
    ).json()
    _wait_succeeded(client, asset["id"])

    with engine.connect() as conn:
        has_column = embedding_index.has_embedding_column(conn)
        assert has_column == available
        if not available:
            return
        blob, embedding = conn.execute(
            text(
                "SELECT vector, embedding::text FROM asset_embedding "
                "WHERE asset_id = :id AND model_key = :key"
            ),
            {"id": uuid.UUID(asset["id"]), "key": ACTIVE_KEY},
        ).one()
        from app.embedding.base import blob_to_vector

        stored = np.array([float(v) for v in embedding[1:-1].split(",")], dtype=np.float32)
        assert np.array_equal(stored, blob_to_vector(bytes(blob)))
        index = conn.execute(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = :name"),
            {"name": embedding_index.hnsw_index_name(ACTIVE_KEY)},
        ).scalar_one()
        assert "hnsw" in index and "vector(512)" in index and ACTIVE_KEY in index

        # 型の登録(`app/db.py`)が効いていれば、vector の列を pgvector の型で読める(文字列ではない)。
        from pgvector import Vector

        value = conn.execute(text("SELECT embedding FROM asset_embedding LIMIT 1")).scalar_one()
        assert isinstance(value, Vector)
        assert np.array_equal(value.to_numpy().astype(np.float32), stored)


@requires_postgresql
def test_postgresql_prepare_adds_missing_column_and_backfills(client: TestClient) -> None:
    """後から拡張を入れた場合(列が無い状態)に、起動時に列を足して BLOB から埋める。"""
    engine = client.app.state.engine
    url = engine.url.render_as_string(hide_password=False)
    if not _vector_extension_available(url):
        assert embedding_index.prepare(engine) == "numpy"
        return
    asset = client.post(
        "/api/assets",
        files={"file": ("a.png", make_png_bytes(), "image/png")},
        data={"kind": "upload"},
    ).json()
    vector = np.linspace(-1, 1, 512, dtype=np.float32)
    with engine.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE asset_embedding DROP COLUMN embedding")
        conn.execute(
            text(
                "INSERT INTO asset_embedding (asset_id, model_key, status, dim, vector, "
                "updated_at) VALUES (:id, 'k', 'succeeded', 512, :v, :now)"
            ),
            {"id": uuid.UUID(asset["id"]), "v": vector_to_blob(vector), "now": datetime.now(UTC)},
        )
    assert embedding_index.prepare(engine) == "pgvector"
    with engine.connect() as conn:
        text_value = conn.execute(
            text("SELECT embedding::text FROM asset_embedding WHERE model_key = 'k'")
        ).scalar_one()
    stored = np.array([float(v) for v in text_value[1:-1].split(",")], dtype=np.float32)
    assert np.array_equal(stored, vector)
