"""今の版の派生画像(サムネイル・プレビュー)を前もって作る(ADR-0036 2章・4章)。

使い方:
    uv run python -m app.tools.regenerate_derivatives [--dry-run] [--prune]

- 派生の作り方の版(`derivatives.DERIVED_VERSION`)を上げた直後は、一覧を開くたびに原本の
  デコードが走って重くなる。前もって作っておくためのツール。版を上げなくても、欠けた派生を
  作り直すのに使える。
- DB の全 Asset(論理削除済みを含む)について、今の版の派生(thumb / preview)が無ければ原本
  から作って保存する。同じ内容(sha256)の Asset は派生を共有するので、1回だけ作る。
- `--prune` は、今の版以外の派生(`derived/` の下の、ほかの版のキー)を消す。原本と、今の版の
  派生には触れない。
- `--dry-run` は、作る件数(と `--prune` なら消す件数)を数えるだけで、何も書かない・消さない。
- 保存先はサーバーと同じ(`STORAGE_BACKEND`。ローカルFS / Azure Blob / S3)。
- サーバーを止めずに動かせる。DB は読むだけで、派生の書き込みは配信の途中で作り直すのと同じ
  内容の上書きにしかならないため(ADR-0036 2章)。
- DB がより新しい GAKEI で移行されていれば、何もせずに中止する(Issue #84。新しい版の派生を
  `--prune` で消さないため)。
- 原本が無い・読めない画像は、数えて知らせるだけで中止しない。保存先との通信の失敗などは、
  トレースバックではなく文言を出して中止する(もう一度実行すれば続きから作る)。
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field

from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings, display_database_url, get_settings
from app.domain import derivatives
from app.domain.models import Asset
from app.domain.storage import (
    AssetStore,
    StorageIOError,
    StorageUnavailableError,
    _first_line,
    derived_key,
    open_store,
    parse_derived_key,
)
from app.i18n import console_t
from app.main import database_too_new_message, unknown_revisions

DERIVED_PREFIX = "derived/"


class RegenerateAbortedError(RuntimeError):
    """始める前の検査に通らなかった、または途中で中止した(利用者向けの文言を持つ)。"""


@dataclass
class RegenerateReport:
    version: int
    dry_run: bool
    prune: bool
    # 対象の画像(sha256 の種類の数)
    images: int = 0
    # 今の版の派生が無かった数(thumb / preview を別に数える)
    missing: int = 0
    generated: int = 0
    # 原本が無い・読めないので作れなかった画像(sha256)
    failed: list[str] = field(default_factory=list)
    # 今の版以外の派生の数と、消した数
    stale: int = 0
    pruned: int = 0


def _asset_sources(settings: Settings) -> dict[str, list[str]]:
    """DB の全 Asset(論理削除済みを含む)の sha256 ごとの原本のキー(順序を保ち重複を除く)。

    同じ内容の Asset は原本のファイルを共有するが(ADR-0026 3章)、古いキーの原本などで
    キーが分かれることがあるので、作るときは順に試す。
    """
    url = settings.sqlalchemy_url
    if settings.uses_sqlite and not settings.db_path.is_file():
        raise RegenerateAbortedError(
            console_t("regenerateDerivatives.databaseMissing", source=str(settings.db_path))
        )
    # 読むだけなので、PRAGMA(WAL への切り替えなど)を付けずに開く。
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            # Issue #84: より新しい GAKEI で移行された DB なら止める。新しい版は派生の版も
            # 新しいことがあり、この版の `--prune` がそれを「今の版以外」として消してしまうため。
            revisions = unknown_revisions(connection)
            if revisions:
                raise RegenerateAbortedError(
                    database_too_new_message(display_database_url(url), revisions)
                )
            if not inspect(connection).has_table(Asset.__tablename__):
                return {}
            rows = connection.execute(
                select(Asset.sha256, Asset.blob_key).order_by(Asset.created_at, Asset.id)
            ).all()
    except SQLAlchemyError as exc:
        raise RegenerateAbortedError(
            console_t(
                "app.databaseUnavailable",
                url=display_database_url(url),
                error=str(getattr(exc, "orig", None) or exc).strip().splitlines()[0],
            )
        ) from exc
    finally:
        engine.dispose()
    sources: dict[str, list[str]] = {}
    for sha256, blob_key in rows:
        keys = sources.setdefault(sha256, [])
        if blob_key not in keys:
            keys.append(blob_key)
    return sources


def _generate(store: AssetStore, sources: dict[str, list[str]], report: RegenerateReport) -> None:
    for sha256, blob_keys in sources.items():
        report.images += 1
        missing = [
            variant
            for variant in derivatives.DERIVED_VARIANTS
            if not store.exists(derived_key(sha256, variant))
        ]
        if not missing:
            continue
        report.missing += len(missing)
        if report.dry_run:
            continue
        for blob_key in blob_keys:
            if derivatives.generate_derived(store, blob_key, sha256, missing) is not None:
                report.generated += len(missing)
                break
        else:
            report.failed.append(sha256)


def _prune(store: AssetStore, report: RegenerateReport) -> None:
    # 一覧を取り切ってから消す(一覧の途中で消すと、保存先によっては取りこぼすため)。
    stale = []
    for key in store.list_keys(DERIVED_PREFIX):
        parsed = parse_derived_key(key)
        # 派生のキーの形でないもの(一時ファイルなど)と、今の版の派生には触れない。
        if parsed is not None and parsed[2] != report.version:
            stale.append(key)
    report.stale = len(stale)
    if report.dry_run:
        return
    for key in stale:
        store.delete(key)
        report.pruned += 1


def regenerate(
    settings: Settings, *, dry_run: bool = False, prune: bool = False
) -> RegenerateReport:
    """今の版の派生を作る(`prune` なら、ほかの版の派生を消す)。

    保存先を使えない、通信に失敗したなどの場合は `RegenerateAbortedError`。
    """
    report = RegenerateReport(version=derivatives.DERIVED_VERSION, dry_run=dry_run, prune=prune)
    sources = _asset_sources(settings)
    try:
        store = open_store(settings)
    except StorageUnavailableError as exc:
        raise RegenerateAbortedError(str(exc)) from None
    try:
        _generate(store, sources, report)
        if prune:
            _prune(store, report)
    except OSError as exc:
        # StorageIOError の文言は伏せ字済み。元の例外はつながない。
        error = str(exc) if isinstance(exc, StorageIOError) else _first_line(exc)
        raise RegenerateAbortedError(
            console_t("regenerateDerivatives.failed", error=error)
        ) from None
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.tools.regenerate_derivatives",
        description=console_t("regenerateDerivatives.description"),
    )
    parser.add_argument(
        "--dry-run", action="store_true", help=console_t("regenerateDerivatives.dryRunHelp")
    )
    parser.add_argument(
        "--prune", action="store_true", help=console_t("regenerateDerivatives.pruneHelp")
    )
    return parser


def print_report(report: RegenerateReport) -> None:
    print(console_t("regenerateDerivatives.version", version=report.version))
    print(console_t("regenerateDerivatives.images", count=report.images))
    if report.dry_run:
        print(console_t("regenerateDerivatives.dryRunMissing", count=report.missing))
        if report.prune:
            print(console_t("regenerateDerivatives.dryRunStale", count=report.stale))
        print(console_t("regenerateDerivatives.dryRunDone"))
        return
    print(console_t("regenerateDerivatives.generated", count=report.generated))
    if report.failed:
        print(
            console_t(
                "regenerateDerivatives.originalUnavailable",
                count=len(report.failed),
                keys=", ".join(report.failed[:5]),
            ),
            file=sys.stderr,
        )
    if report.prune:
        print(console_t("regenerateDerivatives.pruned", count=report.pruned))


def main(argv: list[str] | None = None) -> None:
    from app.__main__ import use_utf8_stdio

    use_utf8_stdio()
    args = build_parser().parse_args(argv)
    try:
        report = regenerate(get_settings(), dry_run=args.dry_run, prune=args.prune)
    except RegenerateAbortedError as exc:
        print(exc, file=sys.stderr, flush=True)
        raise SystemExit(1) from None
    print_report(report)


if __name__ == "__main__":
    main()
