"""既存の `upload` Asset に、他のツールや C2PA が埋め込んだ生成メタ情報を埋め戻す(ADR-0018)。

追記のみの原則の例外として、このコマンドだけが `embedded_meta` が null の `upload` 行を
一度だけ更新する(値のある行は上書きしない)。何も見つからなかった行は null のままなので、
次回の実行でも再走査される。原本の読み取りは `AssetStore` を介するだけで、他のマイグレー
ションのように直接ファイルを書き換えたりはしない。

サーバー停止中に実行することを推奨する(SQLite は同時書き込みに弱く、api プロセスと
競合する可能性があるため)。

使い方: `uv run python -m app.tools.backfill_embedded_meta [--dry-run] [--limit N]`
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db import make_engine, make_session_factory
from app.domain.generation_meta import extract_generation_meta
from app.domain.models import Asset, AssetKind
from app.domain.storage import AssetStore, LocalFsStore
from app.main import run_migrations

logger = logging.getLogger(__name__)

_COMMIT_EVERY = 50


@dataclass
class BackfillStats:
    scanned: int = 0
    updated: int = 0
    no_meta: int = 0
    missing_blob: int = 0


def run_backfill(
    session_factory: sessionmaker[Session],
    store: AssetStore,
    *,
    dry_run: bool = False,
    limit: int | None = None,
) -> BackfillStats:
    """`embedded_meta` が null の `upload` Asset(削除済みも含む)を走査して埋める。

    `dry_run=True` のときは何も commit せず(常に rollback)、件数だけを数える。
    """
    stats = BackfillStats()
    with session_factory() as session:
        query = (
            select(Asset)
            .where(Asset.kind == AssetKind.UPLOAD, Asset.embedded_meta.is_(None))
            .order_by(Asset.created_at, Asset.id)
        )
        if limit is not None:
            query = query.limit(limit)

        since_commit = 0
        for asset in session.execute(query).scalars():
            stats.scanned += 1
            try:
                data = store.read(asset.blob_key)
            except (FileNotFoundError, OSError):
                logger.warning("asset %s の原本が読めないためスキップした", asset.id)
                stats.missing_blob += 1
                continue

            meta = extract_generation_meta(data)
            if meta is None:
                stats.no_meta += 1
                continue

            asset.embedded_meta = meta
            stats.updated += 1
            since_commit += 1
            if not dry_run and since_commit >= _COMMIT_EVERY:
                session.commit()
                since_commit = 0

        if dry_run:
            session.rollback()
        else:
            session.commit()

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "既存の upload Asset に埋め込まれた生成メタ情報を埋め戻す(ADR-0018)。"
            "サーバー停止中の実行を推奨する。"
        )
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="DB を変更せず、対象件数だけを表示する"
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="処理する Asset の件数の上限(既定は無制限)"
    )
    args = parser.parse_args()

    settings = get_settings()
    run_migrations(settings.db_path)
    engine = make_engine(settings.db_path)
    try:
        session_factory = make_session_factory(engine)
        store = LocalFsStore(settings.data_dir)
        stats = run_backfill(session_factory, store, dry_run=args.dry_run, limit=args.limit)
    finally:
        engine.dispose()

    mode = "(--dry-run。DB は変更していません)" if args.dry_run else ""
    print(f"埋め戻しが完了しました {mode}".strip())
    print(f"  走査した件数: {stats.scanned}")
    print(f"  更新した件数: {stats.updated}")
    print(f"  何も見つからなかった件数: {stats.no_meta}")
    print(f"  原本が読めなかった件数: {stats.missing_blob}")


if __name__ == "__main__":
    main()
