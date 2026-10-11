"""画像の原本を手で消した・退避した後に、DB を保存先に合わせる(ADR-0046)。

使い方:
    uv run python -m app.tools.reconcile_storage [--apply] [--yes] [--include-runs-without-outputs]

- **GAKEI を止めてから実行する**(worker が行を書き換えるため)。
- 原本(`asset.blob_key`)が保存先に無い Asset(論理削除済みも含む)を、DB から物理削除する。
  原本の有無は `AssetStore.content_exists` で、キーごとに調べる(同じ内容を共有する Asset も、
  キーが残っていれば残す。ADR-0026)。
- その Asset を指す行(タイトルとタグ、埋め込み、知覚ハッシュ、焦点、グループのメンバー、共有の
  範囲、`run_input` など)を消し、残る Asset の `source_asset_id`(スケッチの下地)と
  `origin_asset_id`(取り込みの元)は null にする。扱う外部キーは `ASSET_REFERENCES` と
  `RUN_REFERENCES` に全部書く(表を足したらここにも足す。テストが漏れを見つける)。
- 出力があった Run で、出力がすべて消えるものは消す。出力が一度も無い Run(失敗・中止)は、
  `--include-runs-without-outputs` を付けたときだけ消す。共有の範囲が空になる共有リンクと、
  起点の Asset が消える共有リンクも消す。
- 設定、利用者、プロンプトセット、パラメーターセット、ComfyUI のワークフロー、タグの辞書、
  LLM の接続先、アバターには触れない。DB に無い保存先のファイルは数えるだけ。
- 既定は dry-run(件数を示すだけで、DB も保存先も変えない)。`--apply` で消す。消す前に件数を
  示して確認を求める(`--yes` で省く)。SQLite なら、消す前に DB を
  `gakei.db.bak-<日時>` に写す。PostgreSQL ならバックアップ(`pg_dump` など)を促す。
- DB の変更は1つのトランザクションで行う。派生画像(すべての版)は、DB を commit した後で
  消す(消せずに残っても害は無い)。
- ADR-0003 の「追記のみ」「論理削除のみ」の例外にあたる保守の手段で、画面や API からは呼べない。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import uuid
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import Table, delete, inspect, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, display_database_url, get_settings
from app.db import make_engine, make_session_factory
from app.domain.models import Asset, Base, Run, Share, ShareAsset
from app.domain.storage import (
    AssetStore,
    LocalFsStore,
    StorageIOError,
    StorageUnavailableError,
    _first_line,
    describe_store,
    open_store,
)
from app.i18n import console_t
from app.main import database_too_new_message, unknown_revisions

# 外部キーの扱い。
NULLIFY = "nullify"  # 残る行の参照を null にする
DELETE = "delete"  # 行を消す
DELETE_SHARE = "delete_share"  # 共有リンクごと消す(共有の起点)
OUTPUT = "output"  # Run の出力。消す Run の出力はすべて消える Asset なので、残る行は無い

# Asset(`asset.id`)を指す外部キー(表、列)と扱い。
ASSET_REFERENCES: dict[tuple[str, str], str] = {
    ("asset", "source_asset_id"): NULLIFY,
    ("asset", "origin_asset_id"): NULLIFY,
    ("upload_ticket", "asset_id"): NULLIFY,
    ("download_ticket", "asset_id"): DELETE,
    ("run_input", "asset_id"): DELETE,
    ("asset_group_member", "asset_id"): DELETE,
    ("asset_annotation", "asset_id"): DELETE,
    ("asset_tag", "asset_id"): DELETE,
    ("asset_embedding", "asset_id"): DELETE,
    ("asset_perceptual_hash", "asset_id"): DELETE,
    ("asset_focal_point", "asset_id"): DELETE,
    ("share_asset", "asset_id"): DELETE,
    ("share", "root_asset_id"): DELETE_SHARE,
}

# Run(`run.id`)を指す外部キー(表、列)と扱い。
RUN_REFERENCES: dict[tuple[str, str], str] = {
    ("asset", "produced_by_run_id"): OUTPUT,
    ("run_input", "run_id"): DELETE,
    ("run_import", "run_id"): DELETE,
    ("run_prompt_embedding", "run_id"): DELETE,
}

# 共有リンク(`share.id`)を指す外部キー。消す共有リンクの範囲の行も消す。
SHARE_REFERENCES: dict[tuple[str, str], str] = {
    ("share_asset", "share_id"): DELETE,
}

# IN 句に一度に渡す数(SQLite と PostgreSQL のパラメーターの上限より十分小さく)。
_CHUNK = 500
# 出力に載せる、原本が無かったキーの例の数。
_EXAMPLES = 5
ORIGINALS_PREFIX = "assets/"


class ReconcileAbortedError(RuntimeError):
    """始める前の検査に通らなかった、または途中で中止した(利用者向けの文言を持つ)。"""


@dataclass
class ReconcilePlan:
    """消すものの一覧(DB はまだ変えていない)。"""

    include_runs_without_outputs: bool
    asset_ids: set[uuid.UUID] = field(default_factory=set)
    # 消す Asset のうち、論理削除済みのもの
    logically_deleted: int = 0
    # 原本が無かったキー(重複を除く。DB の並びの順)
    missing_keys: list[str] = field(default_factory=list)
    run_ids: set[uuid.UUID] = field(default_factory=set)
    # 消す Run の内訳: 出力がすべて消える / 出力が一度も無い
    runs_outputs_gone: int = 0
    runs_without_outputs: int = 0
    # 出力が一度も無い Run の数(`--include-runs-without-outputs` が無いときは消さない)
    runs_without_outputs_total: int = 0
    share_ids: set[uuid.UUID] = field(default_factory=set)
    # 一緒に消す行の数(表ごと。asset / run / share は別に数える)
    rows: dict[str, int] = field(default_factory=dict)
    # null にする参照の数(「表.列」ごと。残る行だけを数える)
    nullified: dict[str, int] = field(default_factory=dict)
    # 派生画像を消す内容(sha256)。残る Asset と共有している内容は含めない
    derived_sha256: set[str] = field(default_factory=set)
    # DB に無い保存先の原本のファイルの数(消さない)
    untracked_files: int = 0

    @property
    def is_empty(self) -> bool:
        return not (self.asset_ids or self.run_ids or self.share_ids)


@dataclass
class ReconcileResult:
    plan: ReconcilePlan
    backup: Path | None = None
    derived_deleted: int = 0
    # 消せなかった派生の sha256(DB は commit 済み)
    derived_failed: list[str] = field(default_factory=list)


def _table(name: str) -> Table:
    return Base.metadata.tables[name]


def _chunks(ids: Iterable[Any]) -> Iterator[list[Any]]:
    items = list(ids)
    for start in range(0, len(items), _CHUNK):
        yield items[start : start + _CHUNK]


def _delete_conditions() -> dict[str, list[tuple[str, str]]]:
    """表ごとの、行を消す条件(列、どの id の集合か)。"""
    conditions: dict[str, list[tuple[str, str]]] = {}
    for kind, references in (
        ("asset", ASSET_REFERENCES),
        ("run", RUN_REFERENCES),
        ("share", SHARE_REFERENCES),
    ):
        for (table, column), action in references.items():
            if action == DELETE:
                conditions.setdefault(table, []).append((column, kind))
    return conditions


def _matching_keys(
    session: Session, table: Table, conditions: list[tuple[str, str]], ids: dict[str, set]
) -> set[tuple]:
    """条件のどれかに当たる行の主キー(読むだけ)。"""
    pk = list(table.primary_key.columns)
    found: set[tuple] = set()
    for column, kind in conditions:
        for chunk in _chunks(ids[kind]):
            rows = session.execute(select(*pk).where(table.c[column].in_(chunk))).all()
            found.update(tuple(row) for row in rows)
    return found


def _count_nullified(session: Session, table: Table, column: str, asset_ids: set[uuid.UUID]) -> int:
    """null にする参照の数。一緒に消える Asset の参照は数えない(消す前に null にはする)。"""
    pk = list(table.primary_key.columns)
    found: set[tuple] = set()
    for chunk in _chunks(asset_ids):
        rows = session.execute(select(*pk).where(table.c[column].in_(chunk))).all()
        found.update(tuple(row) for row in rows)
    if table.name == Asset.__tablename__:
        found = {key for key in found if key[0] not in asset_ids}
    return len(found)


def plan_reconcile(
    session_factory: sessionmaker[Session],
    store: AssetStore,
    *,
    include_runs_without_outputs: bool = False,
) -> ReconcilePlan:
    """原本の有無を調べ、消すものを決める。DB と保存先は変えない。

    保存先との通信の失敗は `ReconcileAbortedError`。
    """
    plan = ReconcilePlan(include_runs_without_outputs=include_runs_without_outputs)
    with session_factory() as session:
        assets = session.execute(
            select(
                Asset.id, Asset.sha256, Asset.blob_key, Asset.deleted_at, Asset.produced_by_run_id
            ).order_by(Asset.created_at, Asset.id)
        ).all()

        # 原本の有無はキーごとに1回だけ調べる(同じキーを共有する Asset がある。ADR-0026)。
        present: dict[str, bool] = {}
        try:
            for row in assets:
                if row.blob_key not in present:
                    present[row.blob_key] = store.content_exists(
                        row.blob_key, row.sha256, "original"
                    )
            stored = [
                key
                for key in store.list_keys(ORIGINALS_PREFIX)
                if not PurePosixPath(key).name.startswith(".")
            ]
        except OSError as exc:
            error = str(exc) if isinstance(exc, StorageIOError) else _first_line(exc)
            raise ReconcileAbortedError(console_t("reconcileStorage.failed", error=error)) from None
        plan.missing_keys = [key for key, ok in present.items() if not ok]
        known_keys = set(present)
        plan.untracked_files = sum(1 for key in stored if key not in known_keys)

        outputs: dict[uuid.UUID, list[uuid.UUID]] = {}
        for row in assets:
            if not present[row.blob_key]:
                plan.asset_ids.add(row.id)
                if row.deleted_at is not None:
                    plan.logically_deleted += 1
            if row.produced_by_run_id is not None:
                outputs.setdefault(row.produced_by_run_id, []).append(row.id)

        # 派生は sha256 ごとに共有するので、残る Asset が使う内容の派生は消さない。
        remaining_sha = {row.sha256 for row in assets if row.id not in plan.asset_ids}
        plan.derived_sha256 = {
            row.sha256
            for row in assets
            if row.id in plan.asset_ids and row.sha256 not in remaining_sha
        }

        # Run: 出力がすべて消えるもの。出力が一度も無いものは指定があるときだけ。
        for run_id, output_ids in outputs.items():
            if all(asset_id in plan.asset_ids for asset_id in output_ids):
                plan.run_ids.add(run_id)
                plan.runs_outputs_gone += 1
        without_outputs = [
            run_id for run_id in session.scalars(select(Run.id)).all() if run_id not in outputs
        ]
        plan.runs_without_outputs_total = len(without_outputs)
        if include_runs_without_outputs:
            plan.run_ids.update(without_outputs)
            plan.runs_without_outputs = len(without_outputs)

        # 共有リンク: 起点が消えるもの、範囲の Asset がすべて消えるもの。
        scope: dict[uuid.UUID, list[uuid.UUID]] = {}
        for share_id, asset_id in session.execute(
            select(ShareAsset.share_id, ShareAsset.asset_id)
        ).all():
            scope.setdefault(share_id, []).append(asset_id)
        for share_id, root_asset_id in session.execute(select(Share.id, Share.root_asset_id)).all():
            members = scope.get(share_id, [])
            if root_asset_id in plan.asset_ids or (
                members and all(asset_id in plan.asset_ids for asset_id in members)
            ):
                plan.share_ids.add(share_id)

        ids = {"asset": plan.asset_ids, "run": plan.run_ids, "share": plan.share_ids}
        for table_name, conditions in _delete_conditions().items():
            count = len(_matching_keys(session, _table(table_name), conditions, ids))
            if count:
                plan.rows[table_name] = count
        for (table_name, column), action in ASSET_REFERENCES.items():
            if action != NULLIFY:
                continue
            count = _count_nullified(session, _table(table_name), column, plan.asset_ids)
            if count:
                plan.nullified[f"{table_name}.{column}"] = count
    return plan


def _apply_db(session: Session, plan: ReconcilePlan) -> None:
    """1つのトランザクションの中で、外部キーの順に消す(呼び出し側が commit する)。"""
    ids = {"asset": plan.asset_ids, "run": plan.run_ids, "share": plan.share_ids}
    # 1. 消す Asset への参照を null にする(一緒に消える行の参照も。自分自身を指す外部キーの
    #    ため)。
    for (table_name, column), action in ASSET_REFERENCES.items():
        if action != NULLIFY:
            continue
        table = _table(table_name)
        for chunk in _chunks(plan.asset_ids):
            session.execute(update(table).where(table.c[column].in_(chunk)).values({column: None}))
    # 2. 消す Asset・Run・共有リンクを指す行。
    for table_name, conditions in _delete_conditions().items():
        table = _table(table_name)
        for column, kind in conditions:
            for chunk in _chunks(ids[kind]):
                session.execute(delete(table).where(table.c[column].in_(chunk)))
    # 3. 共有リンク、Asset、Run の順(Asset が Run を指すため)。
    for model, key in ((Share, "share"), (Asset, "asset"), (Run, "run")):
        table = model.__table__
        for chunk in _chunks(ids[key]):
            session.execute(delete(table).where(table.c.id.in_(chunk)))


def _delete_derived(store: AssetStore, plan: ReconcilePlan, result: ReconcileResult) -> None:
    """消した Asset の派生(すべての版)を消す。失敗しても止めずに数える。"""
    for sha256 in sorted(plan.derived_sha256):
        prefix = f"derived/{sha256}/"
        try:
            # 一覧を取り切ってから消す(一覧の途中で消すと、保存先によっては取りこぼすため)。
            keys = list(store.list_keys(prefix))
            for key in keys:
                store.delete(key)
                result.derived_deleted += 1
        except OSError:
            result.derived_failed.append(sha256)
            continue
        if isinstance(store, LocalFsStore):
            # 空になったフォルダも片付ける(中に何か残っていれば残す)。
            try:
                (store.root / prefix).rmdir()
            except OSError:
                pass


def _backup_sqlite(settings: Settings) -> Path:
    """SQLite の DB を `<名前>.bak-<日時>` に写す(sqlite3 の backup。WAL の中身も含む)。"""
    database = make_url(settings.sqlalchemy_url).database
    source = Path(database) if database else settings.db_path
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = source.with_name(f"{source.name}.bak-{stamp}")
    suffix = 2
    while target.exists():
        target = source.with_name(f"{source.name}.bak-{stamp}-{suffix}")
        suffix += 1
    src = sqlite3.connect(source)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return target


def apply_plan(
    settings: Settings,
    session_factory: sessionmaker[Session],
    store: AssetStore,
    plan: ReconcilePlan,
) -> ReconcileResult:
    """SQLite ならバックアップを取り、DB から消して commit し、その後で派生を消す。"""
    result = ReconcileResult(plan=plan)
    if plan.is_empty:
        return result
    if settings.uses_sqlite:
        try:
            result.backup = _backup_sqlite(settings)
        except (OSError, sqlite3.Error) as exc:
            raise ReconcileAbortedError(
                console_t("reconcileStorage.backupFailed", error=_first_line(exc))
            ) from None
    try:
        with session_factory() as session, session.begin():
            _apply_db(session, plan)
    except SQLAlchemyError as exc:
        raise ReconcileAbortedError(
            console_t(
                "reconcileStorage.applyFailed",
                error=str(getattr(exc, "orig", None) or exc).strip().splitlines()[0],
            )
        ) from None
    _delete_derived(store, plan, result)
    return result


def _check_database(settings: Settings, session_factory: sessionmaker[Session]) -> None:
    url = settings.sqlalchemy_url
    try:
        with session_factory() as session:
            connection = session.connection()
            revisions = unknown_revisions(connection)
            if revisions:
                raise ReconcileAbortedError(
                    database_too_new_message(display_database_url(url), revisions)
                )
            if not inspect(connection).has_table(Asset.__tablename__):
                raise ReconcileAbortedError(console_t("reconcileStorage.tableMissing"))
    except SQLAlchemyError as exc:
        raise ReconcileAbortedError(
            console_t(
                "app.databaseUnavailable",
                url=display_database_url(url),
                error=str(getattr(exc, "orig", None) or exc).strip().splitlines()[0],
            )
        ) from exc


@contextmanager
def open_environment(
    settings: Settings,
) -> Iterator[tuple[sessionmaker[Session], AssetStore]]:
    """DB と保存先を開き、検査する。DB の移行はしない。"""
    if settings.uses_sqlite and not settings.db_path.is_file():
        database = make_url(settings.sqlalchemy_url).database
        if not database or not Path(database).is_file():
            raise ReconcileAbortedError(
                console_t("reconcileStorage.databaseMissing", source=str(database))
            )
    try:
        store = open_store(settings)
    except StorageUnavailableError as exc:
        raise ReconcileAbortedError(str(exc)) from None
    engine = make_engine(settings.sqlalchemy_url)
    try:
        session_factory = make_session_factory(engine)
        _check_database(settings, session_factory)
        yield session_factory, store
    finally:
        engine.dispose()


def reconcile(
    settings: Settings,
    *,
    apply: bool = False,
    include_runs_without_outputs: bool = False,
) -> ReconcileResult:
    """確認を求めずに、決めて(`apply` なら)消す。テストとスクリプトから使う。"""
    with open_environment(settings) as (session_factory, store):
        plan = plan_reconcile(
            session_factory, store, include_runs_without_outputs=include_runs_without_outputs
        )
        if not apply:
            return ReconcileResult(plan=plan)
        return apply_plan(settings, session_factory, store, plan)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.tools.reconcile_storage",
        description=console_t("reconcileStorage.description"),
    )
    parser.add_argument(
        "--apply", action="store_true", help=console_t("reconcileStorage.applyHelp")
    )
    parser.add_argument("--yes", action="store_true", help=console_t("reconcileStorage.yesHelp"))
    parser.add_argument(
        "--include-runs-without-outputs",
        action="store_true",
        help=console_t("reconcileStorage.includeRunsHelp"),
    )
    return parser


def print_plan(settings: Settings, plan: ReconcilePlan) -> None:
    print(console_t("reconcileStorage.storage", storage=describe_store(settings)))
    print(
        console_t(
            "reconcileStorage.database", database=display_database_url(settings.sqlalchemy_url)
        )
    )
    print(
        console_t(
            "reconcileStorage.assets",
            count=len(plan.asset_ids),
            deleted=plan.logically_deleted,
        )
    )
    if plan.missing_keys:
        print(
            console_t(
                "reconcileStorage.missingKeys",
                count=len(plan.missing_keys),
                keys=", ".join(plan.missing_keys[:_EXAMPLES]),
            )
        )
    print(
        console_t(
            "reconcileStorage.runs",
            count=len(plan.run_ids),
            gone=plan.runs_outputs_gone,
            never=plan.runs_without_outputs,
        )
    )
    if not plan.include_runs_without_outputs and plan.runs_without_outputs_total:
        print(console_t("reconcileStorage.runsKept", count=plan.runs_without_outputs_total))
    print(console_t("reconcileStorage.shares", count=len(plan.share_ids)))
    if plan.rows:
        print(console_t("reconcileStorage.rowsHeading"))
        for name, count in sorted(plan.rows.items()):
            print(console_t("reconcileStorage.item", name=name, count=count))
    if plan.nullified:
        print(console_t("reconcileStorage.nullifiedHeading"))
        for name, count in sorted(plan.nullified.items()):
            print(console_t("reconcileStorage.item", name=name, count=count))
    print(console_t("reconcileStorage.derived", count=len(plan.derived_sha256)))
    print(console_t("reconcileStorage.untracked", count=plan.untracked_files))


def print_result(result: ReconcileResult) -> None:
    if result.backup is not None:
        print(console_t("reconcileStorage.backup", path=str(result.backup)))
    print(
        console_t(
            "reconcileStorage.applied",
            assets=len(result.plan.asset_ids),
            runs=len(result.plan.run_ids),
        )
    )
    print(console_t("reconcileStorage.derivedDeleted", count=result.derived_deleted))
    if result.derived_failed:
        print(
            console_t(
                "reconcileStorage.derivedFailed",
                count=len(result.derived_failed),
                keys=", ".join(result.derived_failed[:_EXAMPLES]),
            ),
            file=sys.stderr,
        )


def _ask(prompt: str, read: Callable[[str], str]) -> bool:
    try:
        answer = read(prompt)
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


def main(argv: list[str] | None = None, *, read: Callable[[str], str] = input) -> None:
    from app.__main__ import use_utf8_stdio

    use_utf8_stdio()
    args = build_parser().parse_args(argv)
    settings = get_settings()
    try:
        with open_environment(settings) as (session_factory, store):
            plan = plan_reconcile(
                session_factory,
                store,
                include_runs_without_outputs=args.include_runs_without_outputs,
            )
            print_plan(settings, plan)
            if not args.apply:
                print(console_t("reconcileStorage.dryRunDone"))
                return
            if plan.is_empty:
                print(console_t("reconcileStorage.nothingToDo"))
                return
            if not settings.uses_sqlite:
                print(console_t("reconcileStorage.postgresBackup"))
            if not args.yes and not _ask(console_t("reconcileStorage.confirm"), read):
                print(console_t("reconcileStorage.canceled"))
                return
            result = apply_plan(settings, session_factory, store, plan)
    except ReconcileAbortedError as exc:
        print(exc, file=sys.stderr, flush=True)
        raise SystemExit(1) from None
    print_result(result)


if __name__ == "__main__":
    main()
