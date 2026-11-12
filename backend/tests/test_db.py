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


def test_sqlite_pool_is_larger_than_the_worker_threads(tmp_path: Path) -> None:
    """接続プールが同時に動くスレッド(同期エンドポイントの 40 と worker)より小さいと、処理が
    遅いときに接続待ちが起き、待ち時間の後に 500 になる。"""
    engine = make_engine(tmp_path / "gakei.db")
    try:
        capacity = engine.pool.size() + engine.pool._max_overflow  # noqa: SLF001
    finally:
        engine.dispose()
    assert capacity >= 72
