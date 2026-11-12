"""DB 接続の設定(SQLite の PRAGMA と書き込みの待ち時間)。"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from app.db import SQLITE_BUSY_TIMEOUT_SECONDS, make_engine

pytestmark = pytest.mark.windows


def test_sqlite_engine_waits_for_a_busy_writer_longer_than_the_default(tmp_path: Path) -> None:
    """書き込みが混んだとき、既定の 5 秒で「database is locked」にせず、長めに待つ。"""
    engine = make_engine(tmp_path / "gakei.db")
    try:
        with engine.connect() as conn:
            busy_timeout_ms = conn.execute(text("PRAGMA busy_timeout")).scalar_one()
            journal_mode = conn.execute(text("PRAGMA journal_mode")).scalar_one()
            foreign_keys = conn.execute(text("PRAGMA foreign_keys")).scalar_one()
    finally:
        engine.dispose()
    assert SQLITE_BUSY_TIMEOUT_SECONDS >= 30
    assert busy_timeout_ms == int(SQLITE_BUSY_TIMEOUT_SECONDS * 1000)
    assert journal_mode == "wal"
    assert foreign_keys == 1
