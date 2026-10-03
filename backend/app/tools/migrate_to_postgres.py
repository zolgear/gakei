"""SQLite(`DATA_DIR/gakei.db`)のメタデータを PostgreSQL に移す(ADR-0027 4章)。

使い方:
    uv run python -m app.tools.migrate_to_postgres --to postgresql://user:pass@host/gakei \\
        [--from DATA_DIR/gakei.db] [--dry-run]

- GAKEI を止めてから使う。読み込み元の SQLite ファイルは変更も削除もしない。
- 移行先は空の DB に限る(GAKEI のテーブルが1つでもあれば中止する)。
- 読み込み元のスキーマが最新(Alembic の head)でなければ中止する(先に今の版の GAKEI を
  1回起動すれば最新になる)。
- 移行先には Alembic で最新のスキーマを作り、全テーブルを外部キーの順にコピーして、最後に
  行数を突き合わせる。スキーマの作成からコピー、突き合わせまでを1つのトランザクションで
  行い、途中で失敗したら全体を戻す(PostgreSQL は DDL もトランザクションに入る)。
- 埋め込みのベクトル(`asset_embedding`)も移す。移行先で拡張 `vector`(pgvector)を使えるときは、
  `embedding` 列を BLOB から作る(ADR-0033 4章)。
- 画像などのファイル(`DATA_DIR`)は移さない。移行後も同じ `DATA_DIR` を使う。
- `--dry-run` は両方に接続して、テーブルごとの行数を表示するだけ。
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from alembic import command
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import (
    JSON,
    Connection,
    Table,
    Text,
    bindparam,
    cast,
    create_engine,
    func,
    inspect,
    select,
    type_coerce,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError

from app.config import display_database_url, get_settings, normalize_database_url, sqlite_url
from app.domain import embedding_index
from app.domain.models import Base
from app.i18n import console_t
from app.main import alembic_config

_BATCH_SIZE = 500


class MigrationAbortedError(RuntimeError):
    """移行を始める前の検査に通らなかった、または途中で失敗した(利用者向けの文言を持つ)。"""


@dataclass
class TableCount:
    name: str
    source_rows: int
    target_rows: int | None = None


@dataclass
class MigrationReport:
    source: str
    target: str
    dry_run: bool
    tables: list[TableCount] = field(default_factory=list)

    @property
    def total_rows(self) -> int:
        return sum(t.source_rows for t in self.tables)


def _gakei_table_names() -> set[str]:
    return set(Base.metadata.tables) | {"alembic_version"}


def _head_revision() -> str | None:
    # URL は使わない(スクリプトの置き場所だけを見る)。
    return ScriptDirectory.from_config(alembic_config("sqlite://")).get_current_head()


def _check_source(connection: Connection, source_label: str) -> None:
    current = MigrationContext.configure(connection).get_current_revision()
    head = _head_revision()
    if current != head:
        raise MigrationAbortedError(
            console_t(
                "migrateToPostgres.sourceNotHead",
                source=source_label,
                current=current or "-",
                head=head or "-",
            )
        )


def _check_target_empty(connection: Connection, target_label: str) -> None:
    existing = sorted(set(inspect(connection).get_table_names()) & _gakei_table_names())
    if existing:
        raise MigrationAbortedError(
            console_t(
                "migrateToPostgres.targetNotEmpty",
                target=target_label,
                tables=", ".join(existing),
            )
        )


def _count(connection: Connection, table: Table) -> int:
    return connection.execute(select(func.count()).select_from(table)).scalar_one()


def _self_referencing_columns(table: Table) -> list[str]:
    """同じテーブルを指す外部キーの列(例 `asset.source_asset_id`、`asset.origin_asset_id`)。

    行の順序によっては、指す先の行がまだ入っていないことがある。いったん NULL で入れてから、
    全行を入れ終わった後で埋める。
    """
    return sorted(
        {fk.parent.key for fk in table.foreign_keys if fk.column.table is table},
    )


def _json_columns(table: Table) -> set[str]:
    return {c.key for c in table.columns if isinstance(c.type, JSON)}


def _batches(connection: Connection, table: Table) -> Iterator[list[dict[str, Any]]]:
    """元のテーブルの行を、主キーの順にまとめて読む(毎回同じ順になるように)。

    JSON の列は、Python の値に直さず文字列のまま読む。SQL の NULL(値なし)と JSON の
    `null` をどちらも None にしてしまうと、区別が失われるため。
    """
    json_columns = _json_columns(table)
    columns = [
        type_coerce(c, Text).label(c.key) if c.key in json_columns else c for c in table.columns
    ]
    result = connection.execute(select(*columns).order_by(*table.primary_key.columns))
    while True:
        rows = result.mappings().fetchmany(_BATCH_SIZE)
        if not rows:
            return
        yield [dict(row) for row in rows]


def _insert_statement(table: Table) -> Any:
    """`_batches` の行をそのまま入れる INSERT。JSON の列は文字列を JSONB に変換して入れる
    (None は SQL の NULL、`"null"` は JSON の null のまま)。"""
    json_columns = _json_columns(table)
    values = {
        c.key: (
            cast(bindparam(f"p_{c.key}", type_=Text), JSONB)
            if c.key in json_columns
            else bindparam(f"p_{c.key}", type_=c.type)
        )
        for c in table.columns
    }
    return table.insert().values(values)


def _copy_table(source: Connection, target: Connection, table: Table) -> None:
    self_refs = _self_referencing_columns(table)
    deferred: list[dict[str, Any]] = []
    pk_columns = [c.key for c in table.primary_key.columns]
    insert = _insert_statement(table)
    for batch in _batches(source, table):
        if self_refs:
            for row in batch:
                values = {col: row[col] for col in self_refs if row[col] is not None}
                if values:
                    deferred.append({**{f"_pk_{k}": row[k] for k in pk_columns}, **values})
                    for col in values:
                        row[col] = None
        target.execute(insert, [{f"p_{k}": v for k, v in row.items()} for row in batch])

    # 後で埋める自己参照の列を、列ごとにまとめて UPDATE する。
    for col in self_refs:
        params = [
            {**{f"_pk_{k}": row[f"_pk_{k}"] for k in pk_columns}, f"_v_{col}": row[col]}
            for row in deferred
            if col in row
        ]
        if not params:
            continue
        statement = (
            table.update()
            .where(*[table.c[k] == bindparam(f"_pk_{k}") for k in pk_columns])
            .values({col: bindparam(f"_v_{col}")})
        )
        target.execute(statement, params)


def migrate(source_path: Path, target_url: str, *, dry_run: bool = False) -> MigrationReport:
    """`source_path`(SQLite)の全テーブルを `target_url`(PostgreSQL)にコピーする。

    検査に通らない場合や途中で失敗した場合は `MigrationAbortedError`(移行先は元のまま)。
    """
    source_label = str(source_path)
    try:
        normalized = normalize_database_url(target_url.strip())
        backend = make_url(normalized).get_backend_name()
    except (ArgumentError, ValueError) as exc:
        raise MigrationAbortedError(console_t("migrateToPostgres.targetNotPostgres")) from exc
    if backend != "postgresql":
        raise MigrationAbortedError(console_t("migrateToPostgres.targetNotPostgres"))
    target_label = display_database_url(normalized)

    if not source_path.is_file():
        raise MigrationAbortedError(
            console_t("migrateToPostgres.sourceMissing", source=source_label)
        )

    report = MigrationReport(source=source_label, target=target_label, dry_run=dry_run)
    tables = list(Base.metadata.sorted_tables)

    # 読み込み元は PRAGMA を付けずに開く(WAL への切り替えなどの書き込みもしない)。
    source_engine = create_engine(sqlite_url(source_path))
    target_engine = create_engine(normalized)
    try:
        with source_engine.connect() as source:
            _check_source(source, source_label)
            report.tables = [TableCount(t.name, _count(source, t)) for t in tables]

            try:
                target_connection = target_engine.connect()
            except SQLAlchemyError as exc:
                raise MigrationAbortedError(
                    console_t(
                        "app.databaseUnavailable",
                        url=target_label,
                        error=str(getattr(exc, "orig", None) or exc).strip().splitlines()[0],
                    )
                ) from exc

            # 空かどうかの確認からスキーマの作成、コピー、突き合わせまでを1つのトランザクション
            # で行う。例外で抜けると `begin()` がロールバックし、移行先は元の空のまま残る。
            with target_connection as target, target.begin():
                _check_target_empty(target, target_label)
                if dry_run:
                    return report

                cfg = alembic_config(normalized)
                cfg.attributes["connection"] = target
                command.upgrade(cfg, "head")

                for table in tables:
                    _copy_table(source, target, table)
                # ADR-0033 4章: 移行先で pgvector を使えるなら、`embedding` 列を BLOB から作る。
                if embedding_index.has_embedding_column(target):
                    embedding_index.fill_embedding_column(target)

                mismatched: list[str] = []
                for entry, table in zip(report.tables, tables, strict=True):
                    entry.target_rows = _count(target, table)
                    if entry.target_rows != entry.source_rows:
                        mismatched.append(
                            f"{entry.name} ({entry.source_rows} -> {entry.target_rows})"
                        )
                if mismatched:
                    raise MigrationAbortedError(
                        console_t("migrateToPostgres.countMismatch", tables=", ".join(mismatched))
                    )
    except MigrationAbortedError:
        raise
    except SQLAlchemyError as exc:
        raise MigrationAbortedError(
            console_t(
                "migrateToPostgres.failed",
                error=str(getattr(exc, "orig", None) or exc).strip().splitlines()[0],
            )
        ) from exc
    finally:
        source_engine.dispose()
        target_engine.dispose()
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.tools.migrate_to_postgres",
        description=console_t("migrateToPostgres.description"),
    )
    parser.add_argument(
        "--to", dest="to_url", required=True, help=console_t("migrateToPostgres.toHelp")
    )
    parser.add_argument(
        "--from", dest="from_path", default=None, help=console_t("migrateToPostgres.fromHelp")
    )
    parser.add_argument(
        "--dry-run", action="store_true", help=console_t("migrateToPostgres.dryRunHelp")
    )
    return parser


def print_report(report: MigrationReport) -> None:
    print(console_t("migrateToPostgres.source", source=report.source))
    print(console_t("migrateToPostgres.target", target=report.target))
    for entry in report.tables:
        if entry.target_rows is None:
            print(
                console_t("migrateToPostgres.tableRows", table=entry.name, count=entry.source_rows)
            )
        else:
            print(
                console_t(
                    "migrateToPostgres.tableRowsCopied",
                    table=entry.name,
                    count=entry.source_rows,
                    copied=entry.target_rows,
                )
            )
    if report.dry_run:
        print(console_t("migrateToPostgres.dryRunDone", count=report.total_rows))
    else:
        print(
            console_t("migrateToPostgres.done", tables=len(report.tables), count=report.total_rows)
        )


def main(argv: list[str] | None = None) -> None:
    from app.__main__ import use_utf8_stdio

    use_utf8_stdio()
    args = build_parser().parse_args(argv)
    source_path = Path(args.from_path) if args.from_path else get_settings().db_path
    try:
        report = migrate(source_path, args.to_url, dry_run=args.dry_run)
    except MigrationAbortedError as exc:
        print(exc, file=sys.stderr, flush=True)
        raise SystemExit(1) from None
    print_report(report)


if __name__ == "__main__":
    main()
