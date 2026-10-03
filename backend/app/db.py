"""DB接続。既定は SQLite(WAL, foreign_keys=ON)。`DATABASE_URL` で PostgreSQL も選べる
(ADR-0027。接続先の URL は `Settings.sqlalchemy_url` の1か所で決める)。

同期 SQLAlchemy セッションを使う方針とし、重い処理(Pillow でのデコード/リサイズ、
ファイルI/O)は呼び出し側で `asyncio.to_thread` に逃がす。API のパスオペレーションは
同期関数(`def`)にして Starlette のスレッドプールに任せる。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.domain.models import Base


def make_engine(url: str | Path) -> Engine:
    """接続 URL(`Settings.sqlalchemy_url`)からエンジンを作る。`Path` は SQLite のファイルとみなす。

    SQLite のときだけ、親ディレクトリを作り、`check_same_thread=False` と PRAGMA(WAL、
    foreign_keys)を付ける。PostgreSQL は `pool_pre_ping` で切れた接続を捨てる。
    """
    if isinstance(url, Path):
        url = f"sqlite:///{url}"
    parsed = make_url(url)
    if parsed.get_backend_name() != "sqlite":
        # 届かないホストで起動が長く止まらないよう、接続の待ち時間に上限を付ける
        # (URL に `?connect_timeout=` があればそちらを使う)。
        connect_args = {} if "connect_timeout" in parsed.query else {"connect_timeout": 10}
        pg_engine = create_engine(url, pool_pre_ping=True, connect_args=connect_args)
        if parsed.get_backend_name() == "postgresql":
            event.listen(pg_engine, "connect", _register_pgvector)
        return pg_engine

    if parsed.database and parsed.database != ":memory:":
        Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def _register_pgvector(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
    """ADR-0033 4章: 拡張 `vector` がある DB への接続でだけ、pgvector の型を psycopg に登録する
    (拡張が無ければ何もしない)。型を調べる問い合わせのトランザクションは閉じておく。"""
    try:
        import psycopg
        from pgvector.psycopg import register_vector
    except ImportError:
        return
    if not isinstance(dbapi_connection, psycopg.Connection):
        return
    try:
        register_vector(dbapi_connection)
    except psycopg.Error:
        pass
    finally:
        dbapi_connection.rollback()


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
