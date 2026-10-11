"""既存の Asset のサムネイルの焦点(顔の位置)を求める(ADR-0043 3章)。

使い方:
    uv run python -m app.tools.backfill_focal_points [--dry-run] [--limit N] [--recompute]

- 焦点が無い、または検出の版(`detect.ALGORITHM_VERSION`)が古い Asset(マスク以外。論理削除
  済みも、復元に備えて含める)について、原本から焦点を求めて `asset_focal_point` に書く。
  顔が見つからなかった Asset も「見つからない」と記録し、次からは数えない。
- `--recompute` は、今の版の焦点がある Asset も作り直す。`--limit` は処理する件数の上限。
- `--dry-run` は、対象の件数を数えるだけで、何も書かない。
- 保存先はサーバーと同じ(`STORAGE_BACKEND`)。原本は `open_content` で読むだけ。
- サーバーを止めずに動かせる。1件ごとに短いトランザクションで commit する(SQLite の書き込みの
  鍵を長く持たない)。サーバーの焦点の worker と同じ Asset を同時に書いたときは、後の方を捨てる。
- DB の移行はしない。テーブルが無ければ(この版の GAKEI を一度も起動していなければ)、何もせずに
  中止する。DB がより新しい GAKEI で移行されていても中止する(Issue #84)。
"""

from __future__ import annotations

import argparse
import sys
import time
import uuid
from dataclasses import dataclass

from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, display_database_url, get_settings
from app.db import make_engine, make_session_factory
from app.domain import focal_points
from app.domain.models import Asset, AssetFocalPoint, AssetKind
from app.domain.storage import (
    AssetStore,
    StorageIOError,
    StorageUnavailableError,
    _first_line,
    open_store,
)
from app.i18n import console_t
from app.main import database_too_new_message, unknown_revisions


class BackfillAbortedError(RuntimeError):
    """始める前の検査に通らなかった、または途中で中止した(利用者向けの文言を持つ)。"""


@dataclass
class BackfillReport:
    dry_run: bool
    # 対象の件数(`--limit` を当てた後)
    targets: int = 0
    found: int = 0
    not_found: int = 0
    # 原本が無い・読めない(「見つからない」として記録する)
    unreadable: int = 0
    # 処理中にほかが書いた・消えたなどで飛ばした
    skipped: int = 0
    seconds: float = 0.0


def _pending_ids(
    session_factory: sessionmaker[Session], *, recompute: bool, limit: int | None
) -> list[uuid.UUID]:
    with session_factory() as session:
        query = focal_points.pending_query(recompute=recompute)
        if limit is not None:
            query = query.limit(limit)
        return list(session.execute(query).scalars().all())


def _process_one(
    session_factory: sessionmaker[Session],
    store: AssetStore,
    asset_id: uuid.UUID,
    report: BackfillReport,
    *,
    recompute: bool,
) -> None:
    with session_factory() as session:
        asset = session.get(Asset, asset_id)
        if asset is None or asset.kind == AssetKind.MASK:
            report.skipped += 1
            return
        if not recompute and not focal_points.needs_compute(session, asset_id):
            # 一覧を取ったあとに、サーバーの worker が書いた。
            report.skipped += 1
            return
        content = store.open_content(asset.blob_key, asset.sha256, "original")
        if content is None:
            report.unreadable += 1
            point = None
        else:
            point = focal_points.detect_from_bytes(content.read_all())
            if point is None:
                report.not_found += 1
            else:
                report.found += 1
        # 読み込みのトランザクションを終えてから、短く書く。
        session.rollback()
        focal_points.save(session, asset_id, point)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            report.skipped += 1


def run_backfill(
    session_factory: sessionmaker[Session],
    store: AssetStore,
    *,
    dry_run: bool = False,
    limit: int | None = None,
    recompute: bool = False,
) -> BackfillReport:
    """焦点を求める。`dry_run` なら対象を数えるだけ。保存先との通信の失敗は
    `BackfillAbortedError`(それまでの分は commit 済みなので、もう一度実行すれば続きから)。"""
    report = BackfillReport(dry_run=dry_run)
    ids = _pending_ids(session_factory, recompute=recompute, limit=limit)
    report.targets = len(ids)
    if dry_run:
        return report
    started = time.perf_counter()
    try:
        for asset_id in ids:
            _process_one(session_factory, store, asset_id, report, recompute=recompute)
    except OSError as exc:
        # StorageIOError の文言は伏せ字済み。元の例外はつながない。
        error = str(exc) if isinstance(exc, StorageIOError) else _first_line(exc)
        raise BackfillAbortedError(console_t("backfillFocalPoints.failed", error=error)) from None
    finally:
        report.seconds = time.perf_counter() - started
    return report


def _check_database(settings: Settings, session_factory: sessionmaker[Session]) -> None:
    url = settings.sqlalchemy_url
    if settings.uses_sqlite and not settings.db_path.is_file():
        raise BackfillAbortedError(
            console_t("backfillFocalPoints.databaseMissing", source=str(settings.db_path))
        )
    try:
        with session_factory() as session:
            connection = session.connection()
            revisions = unknown_revisions(connection)
            if revisions:
                raise BackfillAbortedError(
                    database_too_new_message(display_database_url(url), revisions)
                )
            if not inspect(connection).has_table(AssetFocalPoint.__tablename__):
                raise BackfillAbortedError(console_t("backfillFocalPoints.tableMissing"))
    except SQLAlchemyError as exc:
        raise BackfillAbortedError(
            console_t(
                "app.databaseUnavailable",
                url=display_database_url(url),
                error=str(getattr(exc, "orig", None) or exc).strip().splitlines()[0],
            )
        ) from exc


def backfill(
    settings: Settings,
    *,
    dry_run: bool = False,
    limit: int | None = None,
    recompute: bool = False,
) -> BackfillReport:
    try:
        store = open_store(settings)
    except StorageUnavailableError as exc:
        raise BackfillAbortedError(str(exc)) from None
    engine = make_engine(settings.sqlalchemy_url)
    try:
        session_factory = make_session_factory(engine)
        _check_database(settings, session_factory)
        return run_backfill(
            session_factory, store, dry_run=dry_run, limit=limit, recompute=recompute
        )
    finally:
        engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.tools.backfill_focal_points",
        description=console_t("backfillFocalPoints.description"),
    )
    parser.add_argument(
        "--dry-run", action="store_true", help=console_t("backfillFocalPoints.dryRunHelp")
    )
    parser.add_argument(
        "--limit", type=int, default=None, help=console_t("backfillFocalPoints.limitHelp")
    )
    parser.add_argument(
        "--recompute", action="store_true", help=console_t("backfillFocalPoints.recomputeHelp")
    )
    return parser


def print_report(report: BackfillReport) -> None:
    print(console_t("backfillFocalPoints.targets", count=report.targets))
    if report.dry_run:
        print(console_t("backfillFocalPoints.dryRunDone"))
        return
    print(console_t("backfillFocalPoints.found", count=report.found))
    print(console_t("backfillFocalPoints.notFound", count=report.not_found))
    if report.unreadable:
        print(console_t("backfillFocalPoints.unreadable", count=report.unreadable), file=sys.stderr)
    if report.skipped:
        print(console_t("backfillFocalPoints.skipped", count=report.skipped))
    print(console_t("backfillFocalPoints.elapsed", seconds=f"{report.seconds:.1f}"))


def main(argv: list[str] | None = None) -> None:
    from app.__main__ import use_utf8_stdio

    use_utf8_stdio()
    args = build_parser().parse_args(argv)
    if args.limit is not None and args.limit < 1:
        print(console_t("backfillFocalPoints.invalidLimit"), file=sys.stderr, flush=True)
        raise SystemExit(2)
    try:
        report = backfill(
            get_settings(), dry_run=args.dry_run, limit=args.limit, recompute=args.recompute
        )
    except BackfillAbortedError as exc:
        print(exc, file=sys.stderr, flush=True)
        raise SystemExit(1) from None
    print_report(report)


if __name__ == "__main__":
    main()
