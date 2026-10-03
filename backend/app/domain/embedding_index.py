"""PostgreSQL の pgvector(ADR-0033 4章)。

- 正本は `asset_embedding.vector`(BLOB)。PostgreSQL で拡張 `vector` を使えるときだけ、同じ値を
  次元を決めない `embedding vector` 列にも入れる。ORM には載せず、ここの SQL で読み書きする。
- 起動時に列の有無を見て、検索の方式(`pgvector` / `numpy`)を決める(`prepare`)。列が無く、
  拡張を作れるなら(後から拡張を入れた場合)、列を足して BLOB から埋める。
- 索引は使うモデルごとの部分 HNSW 索引(`ensure_hnsw_index`)。モデルを選んだとき(起動時と
  設定の保存時)と、そのモデルの最初のベクトルを書いたとき(次元が分かったとき)に
  `IF NOT EXISTS` で作る。名前は `model_key` のハッシュから作る。
- 値は `'[0.1,0.2,...]'` の文字列を `CAST(... AS vector)` して渡す(psycopg に型を登録して
  いなくても書ける)。型の登録(`pgvector.psycopg.register_vector`)は、拡張がある DB への
  接続でだけ行う(`app/db.py`)。
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import Sequence
from typing import Literal

import numpy as np
from sqlalchemy import Connection, Engine, inspect, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from app.embedding.base import blob_to_vector

logger = logging.getLogger(__name__)

IndexBackend = Literal["pgvector", "numpy"]
BACKEND_PGVECTOR: IndexBackend = "pgvector"
BACKEND_NUMPY: IndexBackend = "numpy"

TABLE = "asset_embedding"
COLUMN = "embedding"
_FILL_BATCH = 500


def has_embedding_column(connection: Connection) -> bool:
    if connection.dialect.name != "postgresql":
        return False
    columns = {c["name"] for c in inspect(connection).get_columns(TABLE)}
    return COLUMN in columns


def vector_literal(vector: Sequence[float] | np.ndarray) -> str:
    """pgvector の文字列表現(float32 の値をそのまま往復できる桁で書く)。"""
    values = np.asarray(vector, dtype=np.float32).reshape(-1)
    return "[" + ",".join(repr(float(v)) for v in values) + "]"


def prepare(engine: Engine) -> IndexBackend:
    """起動時に呼ぶ。検索の方式を決め、必要なら列を足して BLOB から埋める。"""
    if engine.dialect.name != "postgresql":
        return BACKEND_NUMPY
    with engine.connect() as connection:
        if has_embedding_column(connection):
            return BACKEND_PGVECTOR
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
            connection.exec_driver_sql(
                f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS {COLUMN} vector"
            )
    except DBAPIError as e:
        logger.info(
            "pgvector を使えないので、埋め込みの検索は numpy で行います: %s",
            str(getattr(e, "orig", None) or e).strip().splitlines()[0],
        )
        return BACKEND_NUMPY
    with engine.begin() as connection:
        filled = fill_embedding_column(connection)
    if filled:
        logger.info("pgvector の列を BLOB から埋めました(%d 件)", filled)
    # 型の登録(`app/db.py`)は接続を作るときに行うので、拡張を作る前の接続を捨てる。
    engine.dispose()
    return BACKEND_PGVECTOR


def fill_embedding_column(connection: Connection) -> int:
    """`embedding` が空で `vector`(BLOB)がある行を埋める。埋めた件数を返す。
    `migrate_to_postgres` の移行先でも使う。"""
    rows = connection.execute(
        text(
            f"SELECT asset_id, model_key, vector FROM {TABLE} "
            f"WHERE vector IS NOT NULL AND {COLUMN} IS NULL"
        )
    ).all()
    statement = text(
        f"UPDATE {TABLE} SET {COLUMN} = CAST(:v AS vector) "
        "WHERE asset_id = :asset_id AND model_key = :model_key"
    )
    for start in range(0, len(rows), _FILL_BATCH):
        params = [
            {
                "v": vector_literal(blob_to_vector(bytes(vector))),
                "asset_id": asset_id,
                "model_key": model_key,
            }
            for asset_id, model_key, vector in rows[start : start + _FILL_BATCH]
        ]
        connection.execute(statement, params)
    return len(rows)


def write_embedding_column(
    connection: Connection, asset_id: uuid.UUID, model_key: str, vector: np.ndarray | None
) -> None:
    """1行の `embedding` を書く(`vector` が None なら空にする)。呼び出し側が BLOB と同じ
    トランザクションで呼ぶ。"""
    connection.execute(
        text(
            f"UPDATE {TABLE} SET {COLUMN} = CAST(:v AS vector) "
            "WHERE asset_id = :asset_id AND model_key = :model_key"
        ),
        {
            "v": vector_literal(vector) if vector is not None else None,
            "asset_id": asset_id,
            "model_key": model_key,
        },
    )


def hnsw_index_name(model_key: str) -> str:
    digest = hashlib.sha256(model_key.encode("utf-8")).hexdigest()[:16]
    return f"ix_asset_embedding_hnsw_{digest}"


def quote_literal(value: str) -> str:
    """DDL に値を埋め込むための文字列リテラル(DDL ではバインド変数が使えない)。"""
    return "'" + value.replace("'", "''") + "'"


def ensure_hnsw_index(engine: Engine, model_key: str, dim: int) -> bool:
    """そのモデルの部分 HNSW 索引を作る(既にあれば何もしない)。作れなければ警告して False。"""
    if engine.dialect.name != "postgresql" or dim <= 0:
        return False
    statement = (
        f"CREATE INDEX IF NOT EXISTS {hnsw_index_name(model_key)} ON {TABLE} "
        f"USING hnsw (({COLUMN}::vector({int(dim)})) vector_cosine_ops) "
        f"WHERE model_key = {quote_literal(model_key)}"
    )
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(statement)
    except SQLAlchemyError as e:
        logger.warning(
            "埋め込みの索引(%s)を作れませんでした: %s",
            model_key,
            str(getattr(e, "orig", None) or e).strip().splitlines()[0],
        )
        return False
    return True
