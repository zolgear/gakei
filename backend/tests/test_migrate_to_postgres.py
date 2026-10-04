"""SQLite → PostgreSQL の移行ツール(ADR-0027 4章)。

PostgreSQL を使うテストは `GAKEI_TEST_DATABASE_URL` が無ければ skip する。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import create_engine, inspect, select, text

from app.config import sqlite_url
from app.db import make_engine, make_session_factory
from app.domain import embedding_index
from app.domain.models import (
    AppSetting,
    AppUser,
    Asset,
    AssetEmbedding,
    AssetKind,
    AssetPerceptualHash,
    AssetTag,
    PromptSet,
    PromptSetItem,
    Run,
    RunInput,
    RunInputRole,
    RunOperation,
    RunStatus,
    Tag,
)
from app.embedding.base import blob_to_vector, vector_to_blob
from app.main import alembic_config, run_migrations
from app.tools.migrate_to_postgres import MigrationAbortedError, main, migrate
from tests.conftest import requires_postgresql

# 自己参照の外部キーの順序を試すため、指す側の id を指される側より小さくしておく
# (主キー順に読むと、指す側が先に来る)。
_BASE_ID = uuid.UUID("ffffffff-0000-4000-8000-000000000001")
_SKETCH_ID = uuid.UUID("00000000-0000-4000-8000-000000000002")
_RUN_ID = uuid.uuid4()
_USER_ID = uuid.uuid4()
_CREATED = datetime(2026, 9, 1, 12, 34, 56, 789000, tzinfo=UTC)
_VECTOR = np.array([0.5, -0.5, 0.5, 0.5], dtype=np.float32)
_DHASH = bytes.fromhex("ff00a5c3e7180f81")


def _asset(asset_id: uuid.UUID, kind: AssetKind, **kwargs: object) -> Asset:
    return Asset(
        id=asset_id,
        kind=kind,
        sha256=asset_id.hex * 2,
        blob_key=f"assets/{asset_id.hex}.png",
        mime="image/png",
        width=1,
        height=1,
        bytes=1,
        created_at=_CREATED,
        **kwargs,
    )


@pytest.fixture
def source_db(tmp_path: Path) -> Path:
    """最新のスキーマまで上げ、主なテーブルに行を入れた SQLite。"""
    path = tmp_path / "data" / "gakei.db"
    path.parent.mkdir(parents=True)
    run_migrations(sqlite_url(path))
    engine = make_engine(path)
    factory = make_session_factory(engine)
    with factory() as db:
        db.add(
            AppUser(
                id=_USER_ID,
                issuer="https://idp.example/" + "x" * 300,  # 0019 までの VARCHAR(255) を超える
                subject="sub",
                email="user@example.com",
                name="ユーザー",
                role="user",
            )
        )
        db.add(
            Run(
                id=_RUN_ID,
                provider="fake",
                model="fake-model",
                operation=RunOperation.GENERATE,
                prompt="夕焼けの猫 " * 10,
                params={"n": 1, "size": "1024x1024", "nested": {"a": [1, 2]}},
                status=RunStatus.SUCCEEDED,
                usage={"total_tokens": 10},
                created_by_user_id=_USER_ID,
                queued_at=_CREATED,
            )
        )
        db.flush()
        db.add(
            _asset(
                _BASE_ID,
                AssetKind.GENERATED,
                produced_by_run_id=_RUN_ID,
                output_index=0,
            )
        )
        db.flush()
        db.add(
            _asset(
                _SKETCH_ID,
                AssetKind.SKETCH,
                source_asset_id=_BASE_ID,
                embedded_meta={"schema": "gakei.embedded/1", "prompt": "埋め込み"},
            )
        )
        db.flush()
        db.add(RunInput(run_id=_RUN_ID, asset_id=_BASE_ID, role=RunInputRole.IMAGE, position=0))
        tag = Tag(name="猫")
        db.add(tag)
        db.flush()
        db.add(AssetTag(asset_id=_BASE_ID, tag_id=tag.id, source="user", removed=True))
        # ADR-0033: 埋め込みのベクトル(BLOB)。
        db.add(
            AssetEmbedding(
                asset_id=_BASE_ID,
                model_key="onnx:clip-vit-b32-u8@rev",
                status="succeeded",
                dim=4,
                vector=vector_to_blob(_VECTOR),
                updated_at=_CREATED,
            )
        )
        # ADR-0033 12章: 知覚ハッシュ。
        db.add(
            AssetPerceptualHash(
                asset_id=_BASE_ID,
                dhash=_DHASH,
                color=bytes(range(64)),
                version=1,
                created_at=_CREATED,
            )
        )
        prompt_set = PromptSet(name="セット")
        db.add(prompt_set)
        db.flush()
        db.add(PromptSetItem(prompt_set_id=prompt_set.id, label=None, text="本文", position=0))
        db.add(AppSetting(key="instance.id", value={"id": "abc"}))
        db.commit()
    engine.dispose()
    return path


def test_rejects_non_postgresql_target(source_db: Path, tmp_path: Path) -> None:
    with pytest.raises(MigrationAbortedError, match="PostgreSQL"):
        migrate(source_db, sqlite_url(tmp_path / "other.db"))


def test_rejects_missing_source(tmp_path: Path) -> None:
    with pytest.raises(MigrationAbortedError, match="見つかりません"):
        migrate(tmp_path / "nope.db", "postgresql://u:p@127.0.0.1:1/x")


def test_rejects_source_not_at_head(tmp_path: Path) -> None:
    """元が head でなければ、移行先に接続する前に中止する。"""
    from alembic import command

    path = tmp_path / "old.db"
    command.upgrade(alembic_config(sqlite_url(path)), "0019")
    with pytest.raises(MigrationAbortedError, match="最新ではありません"):
        # 移行先には接続しない(接続できないポートでも、この検査で先に止まる)。
        migrate(path, "postgresql://u:p@127.0.0.1:1/x")


@requires_postgresql
def test_copies_all_tables_and_row_counts_match(
    source_db: Path, pg_empty_database_url: str
) -> None:
    report = migrate(source_db, pg_empty_database_url)
    assert not report.dry_run
    assert ":gakei@" not in report.target  # パスワードは伏せる
    counts = {t.name: (t.source_rows, t.target_rows) for t in report.tables}
    assert all(source == target for source, target in counts.values())
    assert counts["asset"] == (2, 2)
    assert counts["run"] == (1, 1)
    assert counts["tag"] == (1, 1)

    engine = make_engine(pg_empty_database_url)
    factory = make_session_factory(engine)
    with factory() as db:
        run = db.get(Run, _RUN_ID)
        assert run is not None
        assert run.params == {"n": 1, "size": "1024x1024", "nested": {"a": [1, 2]}}
        assert run.queued_at == _CREATED
        assert run.created_by_user_id == _USER_ID
        sketch = db.get(Asset, _SKETCH_ID)
        assert sketch is not None
        assert sketch.source_asset_id == _BASE_ID  # 自己参照は後から埋めている
        assert sketch.embedded_meta == {"schema": "gakei.embedded/1", "prompt": "埋め込み"}
        tag_row = db.execute(select(AssetTag)).scalar_one()
        assert tag_row.removed is True
        user = db.get(AppUser, _USER_ID)
        assert user is not None and len(user.issuer) > 255
        # SQL の NULL(値なし)は NULL のまま(JSON の null にしない)。
        null_meta = db.execute(select(Asset.id).where(Asset.embedded_meta.is_(None))).scalar_one()
        assert null_meta == _BASE_ID
        # 検索の JSON の取り出しが PostgreSQL でも動くこと(ADR-0027 2章)。
        hit = db.execute(
            select(Asset.id).where(Asset.embedded_meta["prompt"].as_string() == "埋め込み")
        ).scalar_one()
        assert hit == _SKETCH_ID
        embedding = db.execute(select(AssetEmbedding)).scalar_one()
        assert np.array_equal(blob_to_vector(embedding.vector), _VECTOR)
        # 64 ビット全部を使う値(最上位ビットが立つ)もそのまま移る。
        phash = db.execute(select(AssetPerceptualHash)).scalar_one()
        assert phash.dhash == _DHASH
        assert phash.color == bytes(range(64))
        assert phash.version == 1
    with engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        # pgvector を使えるなら、`embedding` 列も BLOB から作っている(ADR-0033 4章)。
        if embedding_index.has_embedding_column(conn):
            text_value = conn.execute(
                text("SELECT embedding::text FROM asset_embedding")
            ).scalar_one()
            assert text_value == embedding_index.vector_literal(_VECTOR).replace(" ", "")
    engine.dispose()
    assert counts["asset_embedding"] == (1, 1)
    assert counts["asset_perceptual_hash"] == (1, 1)
    assert version == "0025"

    # 元の SQLite は消さない(戻したい場合は DATABASE_URL を外せば元の状態で動く)。
    assert source_db.is_file()


@requires_postgresql
def test_dry_run_only_counts_and_leaves_target_empty(
    source_db: Path, pg_empty_database_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["--to", pg_empty_database_url, "--from", str(source_db), "--dry-run"])
    out = capsys.readouterr().out
    assert "--dry-run" in out
    assert "asset: 2 行" in out
    # パスワードは伏せる。
    assert ":gakei@" not in out

    engine = create_engine(pg_empty_database_url)
    assert inspect(engine).get_table_names() == []
    engine.dispose()


@requires_postgresql
def test_aborts_when_target_is_not_empty(source_db: Path, pg_empty_database_url: str) -> None:
    engine = create_engine(pg_empty_database_url)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE tag (id integer)"))
    engine.dispose()

    with pytest.raises(MigrationAbortedError, match="空ではありません"):
        migrate(source_db, pg_empty_database_url)

    engine = create_engine(pg_empty_database_url)
    assert inspect(engine).get_table_names() == ["tag"]
    engine.dispose()


@requires_postgresql
def test_cli_aborts_with_message_and_exit_code(
    source_db: Path, pg_empty_database_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["--to", pg_empty_database_url, "--from", str(source_db)])
    capsys.readouterr()
    # 2回目は移行先が空ではないので中止する。
    with pytest.raises(SystemExit) as exc_info:
        main(["--to", pg_empty_database_url, "--from", str(source_db)])
    assert exc_info.value.code == 1
    assert "空ではありません" in capsys.readouterr().err


@requires_postgresql
def test_failure_during_copy_rolls_back_everything(
    source_db: Path, pg_empty_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """コピーの途中で失敗したら、スキーマの作成も含めて全体を戻す(移行先は空のまま)。"""
    import app.tools.migrate_to_postgres as tool

    original = tool._copy_table
    calls = {"n": 0}

    def _failing_copy(source, target, table) -> None:  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] == 3:
            target.execute(text("SELECT 1/0"))
        original(source, target, table)

    monkeypatch.setattr(tool, "_copy_table", _failing_copy)
    with pytest.raises(MigrationAbortedError, match="取り消しました"):
        migrate(source_db, pg_empty_database_url)

    engine = create_engine(pg_empty_database_url)
    assert inspect(engine).get_table_names() == []
    engine.dispose()
