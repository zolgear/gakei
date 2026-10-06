"""より新しい GAKEI で移行された DB を、この版では開かないこと(Issue #84、ADR-0027 1章)。

DB の `alembic_version` をこの版が知らないリビジョンに書き換えて、新しい版で移行した DB を
模する。起動とツールが、DB を変えずに分かる文言で止まることを確かめる。SQLite と
PostgreSQL(`GAKEI_TEST_DATABASE_URL` があるとき)の両方で回す。
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from app.config import Settings, sqlite_url
from app.main import (
    DatabaseTooNewError,
    alembic_config,
    check_database_revision,
    run_migrations,
)
from tests.conftest import _fake_settings, using_postgresql

pytestmark = pytest.mark.windows

_FUTURE_REVISION = "9999"


@dataclass
class Db:
    url: str
    data_dir: Path


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, empty_database_url: str) -> Iterator[Db]:
    """空の DB と DATA_DIR。環境変数も起動と同じ形にそろえる(SQLite は DATA_DIR/gakei.db)。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    if using_postgresql():
        monkeypatch.setenv("DATABASE_URL", empty_database_url)
        url = Settings(_env_file=None, data_dir=data_dir).sqlalchemy_url
    else:
        monkeypatch.delenv("DATABASE_URL", raising=False)
        url = sqlite_url(data_dir / "gakei.db")
        assert url == Settings(_env_file=None, data_dir=data_dir).sqlalchemy_url
    yield Db(url=url, data_dir=data_dir)


def _head() -> str:
    head = ScriptDirectory.from_config(alembic_config("sqlite://")).get_current_head()
    assert head is not None
    return head


def _make_too_new(url: str) -> None:
    """head まで移行してから、この版が知らないリビジョンに書き換える。"""
    run_migrations(url)
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE alembic_version SET version_num = :v"), {"v": _FUTURE_REVISION}
            )
            # 新しい版で足されたテーブルを模する(移行で消されたり変えられたりしないこと)。
            connection.execute(text("CREATE TABLE future_table (id INTEGER PRIMARY KEY)"))
            connection.execute(text("INSERT INTO future_table (id) VALUES (1)"))
    finally:
        engine.dispose()


def _snapshot(url: str) -> tuple[list[str], set[str], int]:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            revisions = sorted(
                connection.scalars(text("SELECT version_num FROM alembic_version")).all()
            )
            tables = set(inspect(connection).get_table_names())
            rows = connection.execute(text("SELECT count(*) FROM future_table")).scalar_one()
    finally:
        engine.dispose()
    return revisions, tables, rows


def test_run_migrations_refuses_newer_database(db: Db) -> None:
    _make_too_new(db.url)
    before = _snapshot(db.url)
    with pytest.raises(DatabaseTooNewError) as exc_info:
        run_migrations(db.url)
    message = str(exc_info.value)
    assert "より新しい版の GAKEI" in message
    assert _FUTURE_REVISION in message
    assert _head() in message
    assert "データベースは変更していません" in message
    assert _snapshot(db.url) == before
    assert before[0] == [_FUTURE_REVISION]


def test_message_hides_password(db: Db) -> None:
    if not using_postgresql():
        pytest.skip("パスワードを含む URL は PostgreSQL だけ")
    _make_too_new(db.url)
    from sqlalchemy.engine import make_url

    password = make_url(db.url).password
    with pytest.raises(DatabaseTooNewError) as exc_info:
        check_database_revision(db.url)
    message = str(exc_info.value)
    # DB の名前にパスワードと同じ文字列が入ることがあるので、`:パスワード@` の形で見る。
    if password:
        assert f":{password}@" not in message
        assert ":***@" in message


def test_message_in_english(db: Db, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_too_new(db.url)
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LC_MESSAGES", raising=False)
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    with pytest.raises(DatabaseTooNewError) as exc_info:
        check_database_revision(db.url)
    message = str(exc_info.value)
    assert "newer version of GAKEI" in message
    assert _FUTURE_REVISION in message


def test_fresh_database_is_migrated(db: Db) -> None:
    run_migrations(db.url)
    engine = create_engine(db.url)
    try:
        with engine.connect() as connection:
            assert connection.scalars(text("SELECT version_num FROM alembic_version")).all() == [
                _head()
            ]
    finally:
        engine.dispose()


def test_older_revision_is_upgraded(db: Db) -> None:
    command.upgrade(alembic_config(db.url), "0019")
    check_database_revision(db.url)
    run_migrations(db.url)
    engine = create_engine(db.url)
    try:
        with engine.connect() as connection:
            assert connection.scalars(text("SELECT version_num FROM alembic_version")).all() == [
                _head()
            ]
    finally:
        engine.dispose()


def test_missing_sqlite_file_is_not_created(tmp_path: Path) -> None:
    """初回の起動(ファイルが無い)では検査だけでファイルを作らない。"""
    path = tmp_path / "nope" / "gakei.db"
    check_database_revision(sqlite_url(path))
    assert not path.exists()


def test_server_exits_without_traceback(db: Db, capsys: pytest.CaptureFixture[str]) -> None:
    from app.__main__ import main

    _make_too_new(db.url)
    before = _snapshot(db.url)
    with pytest.raises(SystemExit) as exc_info:
        main([])
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "より新しい版の GAKEI" in captured.err
    assert "Traceback" not in captured.err
    assert _snapshot(db.url) == before


def test_app_lifespan_refuses_newer_database(db: Db) -> None:
    """`python -m app` を通らない起動(uvicorn から直接など)でも、lifespan が止める。"""
    from fastapi.testclient import TestClient

    from app.main import create_app

    _make_too_new(db.url)
    before = _snapshot(db.url)
    settings = _fake_settings(db.data_dir)
    with pytest.raises(DatabaseTooNewError), TestClient(create_app(settings)):
        pass
    assert _snapshot(db.url) == before


def test_backfill_embedded_meta_refuses(
    db: Db, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from app.tools import backfill_embedded_meta

    _make_too_new(db.url)
    before = _snapshot(db.url)
    monkeypatch.setattr(sys, "argv", ["backfill_embedded_meta"])
    with pytest.raises(SystemExit) as exc_info:
        backfill_embedded_meta.main()
    assert exc_info.value.code == 1
    assert "より新しい版の GAKEI" in capsys.readouterr().err
    assert _snapshot(db.url) == before


def test_regenerate_derivatives_refuses(db: Db) -> None:
    from app.tools import regenerate_derivatives

    _make_too_new(db.url)
    with pytest.raises(regenerate_derivatives.RegenerateAbortedError, match="より新しい版"):
        regenerate_derivatives.regenerate(
            Settings(_env_file=None, data_dir=db.data_dir), dry_run=False, prune=True
        )


def test_migrate_to_postgres_refuses_newer_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """移行元が新しい版の DB なら、移行先に接続する前に中止する。"""
    from app.tools.migrate_to_postgres import MigrationAbortedError, migrate

    monkeypatch.delenv("DATABASE_URL", raising=False)
    path = tmp_path / "newer.db"
    _make_too_new(sqlite_url(path))
    with pytest.raises(MigrationAbortedError) as exc_info:
        migrate(path, "postgresql://u:p@127.0.0.1:1/x")
    message = str(exc_info.value)
    assert "より新しい版の GAKEI" in message
    assert str(path) in message
