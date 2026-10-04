"""ローカルFS(`DATA_DIR`)の画像を Azure Blob Storage / S3 互換ストレージへ移す(ADR-0028 6章)。

使い方:
    uv run python -m app.tools.migrate_storage --to azure_blob|s3 [--dry-run]

- 移行先の接続は、サーバーと同じ環境変数(`AZURE_STORAGE_*` / `S3_*`。ADR-0028 2章)で
  指定する。`STORAGE_BACKEND` の値は見ず、`--to` の種類を使う。GAKEI を止めてから使う。
- DB の全 Asset(論理削除済みを含む)の `blob_key` の原本と、`DATA_DIR/derived/` の派生を、
  同じキーでコピーする。DB は読むだけで書き換えない。ローカルのファイルは消さない。
- 移行先に同じキーがあれば、大きさが同じなら飛ばし、違えば中止する(上書きしない)。ただし
  派生(`derived/...`)は原本から作り直せるので、大きさが違えば警告して上書きする。途中で
  止まっても、もう一度実行すれば続きからコピーする。
- 保存先との通信の失敗などは、トレースバックではなく文言を出して中止する(鍵は出さない)。
- DB にあってローカルに無い原本は、数えて知らせるだけで中止しない(手で消されたものなど)。
- `--dry-run` は、コピーする対象の件数と合計サイズを表示するだけ(移行先には接続しない)。
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings, display_database_url, get_settings
from app.domain.models import Asset
from app.domain.storage import (
    StorageIOError,
    StorageUnavailableError,
    _first_line,
    describe_store,
    open_store,
)
from app.i18n import console_t

TARGETS = ("azure_blob", "s3")


class MigrationAbortedError(RuntimeError):
    """移行を始める前の検査に通らなかった、または途中で中止した(利用者向けの文言を持つ)。"""


@dataclass
class MigrationReport:
    source: str
    target: str
    dry_run: bool
    originals: int = 0
    originals_bytes: int = 0
    derived: int = 0
    derived_bytes: int = 0
    copied: int = 0
    skipped: int = 0
    missing: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.originals + self.derived

    @property
    def total_bytes(self) -> int:
        return self.originals_bytes + self.derived_bytes


def _original_keys(settings: Settings) -> list[str]:
    """DB の全 Asset(論理削除済みを含む)の `blob_key`(重複を除き、順序を保つ)。"""
    url = settings.sqlalchemy_url
    if settings.uses_sqlite and not settings.db_path.is_file():
        raise MigrationAbortedError(
            console_t("migrateStorage.databaseMissing", source=str(settings.db_path))
        )
    # 読むだけなので、PRAGMA(WAL への切り替えなど)を付けずに開く。
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            if not inspect(connection).has_table(Asset.__tablename__):
                return []
            keys = connection.scalars(
                select(Asset.blob_key).order_by(Asset.created_at, Asset.id)
            ).all()
    except SQLAlchemyError as exc:
        raise MigrationAbortedError(
            console_t(
                "app.databaseUnavailable",
                url=display_database_url(url),
                error=str(getattr(exc, "orig", None) or exc).strip().splitlines()[0],
            )
        ) from exc
    finally:
        engine.dispose()
    return list(dict.fromkeys(keys))


def _derived_keys(data_dir: Path) -> Iterator[str]:
    root = data_dir / "derived"
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*")):
        if path.is_file():
            yield path.relative_to(data_dir).as_posix()


def _copy(target: Any, key: str, path: Path, report: MigrationReport) -> None:
    """1件をコピーする。保存先の失敗(`OSError`。SDK の例外は `StorageIOError` に包まれて
    いる)は、トレースバックではなく文言で止めるため `MigrationAbortedError` にする。"""
    try:
        _copy_one(target, key, path, report)
    except MigrationAbortedError:
        raise
    except OSError as exc:
        # StorageIOError の文言は伏せ字済み(権限不足の案内が長いので切り詰めない)。
        # 元の例外はつながない。
        error = str(exc) if isinstance(exc, StorageIOError) else _first_line(exc)
        raise MigrationAbortedError(
            console_t("migrateStorage.copyFailed", key=key, error=error)
        ) from None


def _copy_one(target: Any, key: str, path: Path, report: MigrationReport) -> None:
    size = path.stat().st_size
    derived = key.startswith("derived/")
    existing = target.size_of(key)
    if existing is not None:
        if existing == size:
            report.skipped += 1
            return
        if not derived:
            raise MigrationAbortedError(
                console_t("migrateStorage.sizeMismatch", key=key, local=size, remote=existing)
            )
        # 派生は原本から作り直せるので、止めずに警告して上書きする(原本は上書きしない)。
        print(
            console_t("migrateStorage.derivedSizeMismatch", key=key, local=size, remote=existing),
            file=sys.stderr,
            flush=True,
        )
        target.overwrite(key, path.read_bytes())
        report.copied += 1
        return
    try:
        target.write_new(key, path.read_bytes())
    except FileExistsError as exc:
        # 調べた後に誰かが書いた。上書きはしない。
        raise MigrationAbortedError(
            console_t("migrateStorage.sizeMismatch", key=key, local=size, remote="?")
        ) from exc
    report.copied += 1


def migrate(settings: Settings, to: str, *, dry_run: bool = False) -> MigrationReport:
    """`settings.data_dir` の画像を `to` の保存先にコピーする。

    検査に通らない場合や、移行先に大きさの違う同じキーがある場合は `MigrationAbortedError`。
    """
    if to not in TARGETS:
        raise MigrationAbortedError(console_t("migrateStorage.invalidTarget", target=to))
    target_settings = settings.model_copy(update={"storage_backend": to})
    report = MigrationReport(
        source=str(settings.data_dir), target=describe_store(target_settings), dry_run=dry_run
    )

    originals: list[tuple[str, Path]] = []
    for key in _original_keys(settings):
        path = settings.data_dir / key
        if path.is_file():
            originals.append((key, path))
            report.originals += 1
            report.originals_bytes += path.stat().st_size
        else:
            report.missing.append(key)
    derived = [(key, settings.data_dir / key) for key in _derived_keys(settings.data_dir)]
    report.derived = len(derived)
    report.derived_bytes = sum(path.stat().st_size for _key, path in derived)

    if dry_run:
        return report

    try:
        target = open_store(target_settings)
    except StorageUnavailableError as exc:
        raise MigrationAbortedError(str(exc)) from None
    for key, path in [*originals, *derived]:
        _copy(target, key, path, report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.tools.migrate_storage",
        description=console_t("migrateStorage.description"),
    )
    parser.add_argument(
        "--to", dest="to", required=True, choices=TARGETS, help=console_t("migrateStorage.toHelp")
    )
    parser.add_argument(
        "--dry-run", action="store_true", help=console_t("migrateStorage.dryRunHelp")
    )
    return parser


def _mb(size: int) -> str:
    return f"{size / (1024 * 1024):.1f}"


def print_report(report: MigrationReport) -> None:
    print(console_t("migrateStorage.source", source=report.source))
    print(console_t("migrateStorage.target", target=report.target))
    print(
        console_t(
            "migrateStorage.originals", count=report.originals, mb=_mb(report.originals_bytes)
        )
    )
    print(console_t("migrateStorage.derived", count=report.derived, mb=_mb(report.derived_bytes)))
    if report.missing:
        print(
            console_t(
                "migrateStorage.missing",
                count=len(report.missing),
                keys=", ".join(report.missing[:5]),
            ),
            file=sys.stderr,
        )
    if report.dry_run:
        print(
            console_t("migrateStorage.dryRunDone", count=report.total, mb=_mb(report.total_bytes))
        )
    else:
        print(
            console_t(
                "migrateStorage.done",
                count=report.total,
                copied=report.copied,
                skipped=report.skipped,
            )
        )


def main(argv: list[str] | None = None) -> None:
    from app.__main__ import use_utf8_stdio

    use_utf8_stdio()
    args = build_parser().parse_args(argv)
    try:
        report = migrate(get_settings(), args.to, dry_run=args.dry_run)
    except MigrationAbortedError as exc:
        print(exc, file=sys.stderr, flush=True)
        raise SystemExit(1) from None
    print_report(report)


if __name__ == "__main__":
    main()
