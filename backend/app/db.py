"""DB接続。SQLite(WAL, foreign_keys=ON)。

同期 SQLAlchemy セッションを使う方針とし、重い処理(Pillow でのデコード/リサイズ、
ファイルI/O)は呼び出し側で `asyncio.to_thread` に逃がす。API のパスオペレーションは
同期関数(`def`)にして Starlette のスレッドプールに任せる。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.domain.models import Base


def make_engine(db_path: Path) -> Engine:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def create_all(engine: Engine) -> None:
    """テスト用: Alembic を介さずスキーマを直接作る。"""
    Base.metadata.create_all(engine)


def session_scope_factory(session_factory: sessionmaker[Session]):
    """FastAPI の Depends 用ジェネレータを作る。"""

    def _get_db() -> Iterator[Session]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    return _get_db
