"""系列の取り込み(ADR-0037 2章)で入った Asset の見分け。

取り込んだ記録は ZIP を信じるしかない「未検証」のもので、一覧のタイルの印(種別のリボン)で
ほかの Asset と見分けられるようにする。見分けるのは、取り込んだ Run(`run.origin = 'import'`)が
作った生成画像だけ。取り込みで入ったアップロード・スケッチなどは Asset の行に印が無く、
中身は通常のアップロードと同じなので、種別のとおりに見せる。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import RUN_ORIGIN_IMPORT, Asset, AssetKind, Run

# `IN` に並べる id の上限(SQLite の変数の上限より十分小さく)。
_IN_CHUNK = 500


def bulk_imported_ids(db: Session, assets: Iterable[Asset]) -> set[uuid.UUID]:
    """`assets` のうち、取り込んだ Run が作った生成画像の id の集合。"""
    by_run: dict[uuid.UUID, list[uuid.UUID]] = {}
    for asset in assets:
        if asset.kind == AssetKind.GENERATED and asset.produced_by_run_id is not None:
            by_run.setdefault(asset.produced_by_run_id, []).append(asset.id)
    if not by_run:
        return set()
    run_ids = list(by_run)
    imported_runs: set[uuid.UUID] = set()
    for start in range(0, len(run_ids), _IN_CHUNK):
        chunk = run_ids[start : start + _IN_CHUNK]
        imported_runs.update(
            db.execute(
                select(Run.id).where(Run.id.in_(chunk), Run.origin == RUN_ORIGIN_IMPORT)
            ).scalars()
        )
    return {asset_id for run_id in imported_runs for asset_id in by_run[run_id]}


def is_imported(asset: Asset, produced_by_run: Run | None) -> bool:
    """1件の Asset が取り込んだ Run の生成画像か(詳細の応答向け。Run は呼び出し側で引き済み)。"""
    return (
        asset.kind == AssetKind.GENERATED
        and produced_by_run is not None
        and produced_by_run.origin == RUN_ORIGIN_IMPORT
    )
