"""SQLite と PostgreSQL で結果を揃えるための SQL 式(ADR-0027 2章)。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import String
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.sql.functions import FunctionElement


class binary_order(FunctionElement):  # noqa: N801 - SQL 関数風の名前にそろえる
    """文字列をバイト順(コードポイント順)で並べるための ORDER BY 用の式。

    SQLite の既定の照合順(BINARY)はバイト順だが、PostgreSQL の既定はデータベースの
    ロケール(`en_US.utf8` など)で、大文字小文字や記号の扱いが変わる。画面に出る名前の
    並びを揃えるため、PostgreSQL では `COLLATE "C"` を付ける。SQLite では列そのまま。
    """

    type = String()
    inherit_cache = True
    name = "binary_order"


@compiles(binary_order)
def _binary_order_default(element: binary_order, compiler: Any, **kw: Any) -> str:
    return compiler.process(element.clauses, **kw)


@compiles(binary_order, "postgresql")
def _binary_order_postgresql(element: binary_order, compiler: Any, **kw: Any) -> str:
    return f'({compiler.process(element.clauses, **kw)}) COLLATE "C"'


def savepoints_are_safe(db: Session) -> bool:
    """`begin_nested()`(SAVEPOINT)で一部だけを戻す書き方が使えるか。

    SQLite の Python ドライバ(pysqlite)は、最初の書き込みまで BEGIN を出さない。その前に
    SAVEPOINT を張ると、SQLite はそれを外側のトランザクションとして扱い、RELEASE の時点で
    コミットしてしまう(外側の rollback で戻らない)。SQLite ではこれまでどおり SAVEPOINT を
    使わず、PostgreSQL のときだけ使う(ADR-0027 2章)。
    """
    return db.get_bind().dialect.name != "sqlite"
