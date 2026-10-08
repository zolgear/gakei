"""Run を一覧・検索で見せるときに共通で使う集計クエリ。

`RunSummary` の `outputs` / `primary_parent_asset_id` / `input_count` は
一覧(app/api/runs.py)とグローバル検索(app/domain/search.py)の両方で必要になるため、
ここに1か所にまとめる。複数 run_id をまとめて引く前提(N+1 を避ける)。

ADR-0025: 見る人(`viewer`)に見えない Asset・Run・グループは、集計や参照に含めない
(`app/domain/visibility.py`)。見える Run の出力は同じ実行者のものなので絞らない。
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.identity import CurrentUser
from app.domain.avatars import avatar_url
from app.domain.models import (
    RUN_ORIGIN_IMPORT,
    AppUser,
    Asset,
    AssetAnnotation,
    AssetGroup,
    Run,
    RunInput,
    RunInputRole,
)
from app.domain.pricing import cost_from_usage
from app.domain.schemas import AssetGroupRef, CreatedBy, RunOutputRef, RunTextOutput
from app.domain.visibility import asset_visible, group_visible, run_visible


def bulk_output_refs(db: Session, run_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[RunOutputRef]]:
    """run_id ごとの出力 Asset 参照。複数 run_id を1つの IN 句でまとめて取る。"""
    result: dict[uuid.UUID, list[RunOutputRef]] = {rid: [] for rid in run_ids}
    if not run_ids:
        return result
    rows = db.execute(
        select(Asset.produced_by_run_id, Asset.id, Asset.output_index, AssetAnnotation.title)
        .outerjoin(AssetAnnotation, AssetAnnotation.asset_id == Asset.id)
        .where(Asset.produced_by_run_id.in_(run_ids))
        .order_by(Asset.output_index)
    ).all()
    for produced_by_run_id, asset_id, output_index, title in rows:
        result[produced_by_run_id].append(
            RunOutputRef(asset_id=asset_id, output_index=output_index, title=title)
        )
    return result


def bulk_input_summary(
    db: Session, run_ids: list[uuid.UUID], viewer: CurrentUser
) -> dict[uuid.UUID, tuple[int, uuid.UUID | None]]:
    """run_id ごとの (role=image の入力枚数, 主たる親 asset_id) を1つの IN 句でまとめて取る。

    主たる親が `viewer` に見えない Asset なら null にする(ADR-0025。認証モードでの以前の
    データのように、他人の Asset を入力にしていた Run のため)。枚数は Run 自身の情報なので数える。
    """
    counts: dict[uuid.UUID, int] = dict.fromkeys(run_ids, 0)
    primary: dict[uuid.UUID, uuid.UUID | None] = dict.fromkeys(run_ids)
    if not run_ids:
        return {rid: (0, None) for rid in run_ids}
    rows = db.execute(
        select(RunInput.run_id, RunInput.asset_id, RunInput.position).where(
            RunInput.run_id.in_(run_ids), RunInput.role == RunInputRole.IMAGE
        )
    ).all()
    for run_id, asset_id, position in rows:
        counts[run_id] += 1
        if position == 0:
            primary[run_id] = asset_id
    parent_ids = [aid for aid in primary.values() if aid is not None]
    if parent_ids:
        visible = set(
            db.execute(select(Asset.id).where(Asset.id.in_(parent_ids), asset_visible(viewer)))
            .scalars()
            .all()
        )
        for run_id, asset_id in primary.items():
            if asset_id is not None and asset_id not in visible:
                primary[run_id] = None
    return {rid: (counts[rid], primary[rid]) for rid in run_ids}


def bulk_descendant_run_counts(
    db: Session, run_ids: list[uuid.UUID], viewer: CurrentUser
) -> dict[uuid.UUID, int]:
    """run_id ごとの「子孫 Run 数」: この Run の出力 Asset を入力に使っている、削除されていない
    Run の数(distinct run_id、自分自身は除く)。削除確認ダイアログの警告に使う。
    `viewer` に見える Run だけを数える(ADR-0025)。
    """
    result: dict[uuid.UUID, int] = dict.fromkeys(run_ids, 0)
    if not run_ids:
        return result
    descendants: dict[uuid.UUID, set[uuid.UUID]] = {rid: set() for rid in run_ids}
    rows = db.execute(
        select(Asset.produced_by_run_id, RunInput.run_id)
        .select_from(RunInput)
        .join(Asset, Asset.id == RunInput.asset_id)
        .join(Run, Run.id == RunInput.run_id)
        .where(
            Asset.produced_by_run_id.in_(run_ids),
            Run.deleted_at.is_(None),
            run_visible(viewer),
        )
        .distinct()
    ).all()
    for produced_by_run_id, descendant_run_id in rows:
        if descendant_run_id != produced_by_run_id:
            descendants[produced_by_run_id].add(descendant_run_id)
    return {rid: len(descendants[rid]) for rid in run_ids}


def bulk_users(db: Session, user_ids: list[uuid.UUID | None]) -> dict[uuid.UUID, CreatedBy]:
    """`created_by_user_id` ごとの `CreatedBy` を1つの IN 句でまとめて取る(ADR-0019、N+1 回避)。

    None(none モード、または生成出力以外で実行者を記録しない箇所)は呼び出し側で弾く前提
    だが、渡ってきても無視するだけで安全。
    """
    ids = {uid for uid in user_ids if uid is not None}
    if not ids:
        return {}
    rows = db.execute(select(AppUser).where(AppUser.id.in_(ids))).scalars().all()
    return {
        row.id: CreatedBy(
            id=row.id,
            name=row.name,
            email=row.email,
            avatar_url=avatar_url(row.id, row.avatar_sha256),
        )
        for row in rows
    }


def bulk_asset_groups(
    db: Session, group_ids: list[uuid.UUID | None], viewer: CurrentUser
) -> dict[uuid.UUID, AssetGroupRef]:
    """`run.asset_group_id` ごとの `AssetGroupRef` を1つの IN 句でまとめて取る(ADR-0022、
    N+1 回避)。削除済みのグループと、`viewer` に見えないグループは含めない(呼び出し側では
    null になる。ADR-0025)。
    グループ指定のある Run がページに無ければクエリを発行しない。
    """
    ids = {gid for gid in group_ids if gid is not None}
    if not ids:
        return {}
    rows = db.execute(
        select(AssetGroup.id, AssetGroup.name).where(
            AssetGroup.id.in_(ids), AssetGroup.deleted_at.is_(None), group_visible(viewer)
        )
    ).all()
    return {gid: AssetGroupRef(id=gid, name=name) for gid, name in rows}


def model_label_from_params(params: dict[str, Any] | None) -> str | None:
    """`run.params["comfyui_workflow"]["name"]` があればそれ、無ければ None(ADR-0013)。
    ComfyUI の Run は `model` がワークフローの id なので、画面にはこの名前を出す。
    """
    if not params:
        return None
    workflow = params.get("comfyui_workflow")
    if isinstance(workflow, dict):
        name = workflow.get("name")
        if isinstance(name, str):
            return name
    return None


def run_text_outputs(run: Run) -> list[RunTextOutput] | None:
    """`run.text_outputs` を API の形にする(ADR-0030)。形の崩れた要素は飛ばし、
    1件も残らなければ None。"""
    raw = run.text_outputs
    if not isinstance(raw, list):
        return None
    items: list[RunTextOutput] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            items.append(RunTextOutput.model_validate(item))
        except ValidationError:
            continue
    return items or None


def run_cost_usd(run: Run) -> float | None:
    """Run の実コスト(参考)。取り込んだ Run(ADR-0037)は、この GAKEI で料金が掛かっていない
    ので数えない(usage は記録として残すが、料金にはしない)。"""
    if run.origin == RUN_ORIGIN_IMPORT:
        return None
    return cost_from_usage(run.model, run.usage)


def run_summary_fields(
    run: Run,
    outputs: list[RunOutputRef],
    input_count: int,
    primary_parent_asset_id: uuid.UUID | None,
    descendant_run_count: int = 0,
    created_by: CreatedBy | None = None,
    asset_group: AssetGroupRef | None = None,
) -> dict[str, Any]:
    """`RunSummary(**...)` にそのまま渡せる辞書を作る。`created_by` は呼び出し側が
    `bulk_users()` で、`asset_group` は `bulk_asset_groups()` で引いた値を渡す
    (ここでは DB を引かない。ADR-0019、ADR-0022)。
    """
    return {
        "id": run.id,
        "provider": run.provider,
        "operation": run.operation,
        "model": run.model,
        "model_label": model_label_from_params(run.params),
        "status": run.status,
        "prompt": run.prompt,
        "params": run.params or {},
        "usage": run.usage,
        "error_code": run.error_code,
        "error_message": run.error_message,
        "queued_at": run.queued_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "outputs": outputs,
        "primary_parent_asset_id": primary_parent_asset_id,
        "input_count": input_count,
        "descendant_run_count": descendant_run_count,
        "deleted_at": run.deleted_at,
        # 実コスト(参考)。ADR-0009「参考価格」節。usage 無し・単価不明なら None。
        "cost_usd": run_cost_usd(run),
        "created_by": created_by,
        "asset_group": asset_group,
        "origin": run.origin,
        "text_outputs": run_text_outputs(run),
    }
