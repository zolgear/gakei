"""イベントの中身(`subject`)を DB の行から組み立てる(ADR-0051 3章)。

契約の型は `app/mods/api.py`。ここは GAKEI の内部の行(Asset、Run、接続先)を契約の形に移す
だけで、送り先や呼び出しは知らない。API キーと秘密に見える値は載せない(ComfyUI の Run の
`params` は共有と同じ規則で伏せる。`app/domain/secret_values.py`)。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import comfyui_connection, sdwebui_connection
from app.domain import locality as locality_domain
from app.domain.models import (
    AppUser,
    Asset,
    AssetContentRating,
    ComfyWorkflow,
    Run,
    RunInput,
    RunInputRole,
)
from app.domain.secret_values import redact_comfyui_params
from app.mods import api as contract

if TYPE_CHECKING:
    from app.auth.identity import CurrentUser
    from app.config import Settings
    from app.domain.llm_connections import ConnectionConfig


def image_ref(asset: Asset, role: str | None = None) -> contract.ImageRef:
    return contract.ImageRef(
        asset_id=str(asset.id),
        sha256=asset.sha256,
        width=asset.width,
        height=asset.height,
        bytes=asset.bytes,
        mime=asset.mime,
        role=role,
    )


def actor_for_user_id(db: Session, user_id: uuid.UUID | None) -> contract.Actor | None:
    """実行したユーザー。個人モード(ユーザーの行が無い)では None。"""
    if user_id is None:
        return None
    user = db.get(AppUser, user_id)
    if user is None:
        return contract.Actor(id=str(user_id))
    return contract.Actor(id=str(user.id), email=user.email, name=user.name)


def actor_from_user(user: CurrentUser | None) -> contract.Actor | None:
    if user is None or user.id is None:
        return None
    return contract.Actor(id=str(user.id), email=user.email, name=user.name)


def run_params(run: Run) -> dict[str, Any]:
    """送るパラメーター。ComfyUI の送ったグラフの秘密に見える値は伏せる。"""
    params = dict(run.params or {})
    if run.provider == "comfyui":
        return redact_comfyui_params(params)
    return params


def _workflow(db: Session, run: Run) -> tuple[contract.WorkflowRef | None, str | None]:
    """ComfyUI の Run のワークフローと、その接続先(`locality`)。"""
    if run.provider != "comfyui":
        return None, None
    row: ComfyWorkflow | None = None
    try:
        row = db.get(ComfyWorkflow, uuid.UUID(run.model))
    except ValueError:
        row = None
    recorded = (run.params or {}).get("comfyui_workflow")
    name = row.name if row is not None else None
    if name is None and isinstance(recorded, dict) and isinstance(recorded.get("name"), str):
        name = recorded["name"]
    locality = locality_domain.read_workflow(row.locality) if row is not None else None
    return contract.WorkflowRef(id=run.model, name=name), locality


def _provider_host(db: Session, settings: Settings, provider: str) -> str | None:
    if provider == "openai":
        return locality_domain.openai_host(settings)
    if provider == "comfyui":
        return locality_domain.host_of(comfyui_connection.resolve_active_url(db, settings))
    if provider == "sdwebui":
        return locality_domain.host_of(sdwebui_connection.resolve_active_url(db, settings))
    return None


def run_target(db: Session, settings: Settings, run: Run) -> contract.Target:
    """画像の生成の Run の送り先。ワークフローの接続先は、`unset`(接続の設定に従う)でなければ
    接続の値より優先する。"""
    workflow, workflow_locality = _workflow(db, run)
    if run.provider in locality_domain.PROVIDERS:
        connection = locality_domain.provider_locality(db, settings, run.provider)
    else:
        connection = (locality_domain.DEFAULT, "default")
    locality, source = locality_domain.resolve(connection, workflow_locality)  # type: ignore[arg-type]
    return contract.Target(
        provider=run.provider,
        model=run.model,
        host=_provider_host(db, settings, run.provider),
        locality=locality,
        locality_source=source,
        workflow=workflow,
    )


def llm_target(
    db: Session,
    settings: Settings,
    connection: ConnectionConfig,
    *,
    model: str | None,
    base_url: str | None,
    provider: str | None = None,
) -> contract.Target:
    """LLM・VLM・リモートの埋め込みの接続先(ADR-0032)の送り先。プロバイダーは、組み込みの
    接続先なら `openai`、それ以外は `openai_compatible`(埋め込みは呼び出し側が渡す)。"""
    locality, source = locality_domain.connection_locality(
        db, settings, builtin=connection.builtin, value=connection.locality
    )
    host = locality_domain.host_of(base_url)
    if host is None and connection.builtin:
        host = locality_domain.OPENAI_OFFICIAL_HOST
    return contract.Target(
        provider=provider or ("openai" if connection.builtin else "openai_compatible"),
        model=model,
        host=host,
        locality=locality,
        locality_source=source,
        connection=contract.ConnectionRef(
            id=connection.id,
            name=None if connection.builtin else connection.name,
            builtin=connection.builtin,
        ),
    )


def _input_role(run_input: RunInput) -> str:
    if run_input.role == RunInputRole.MASK:
        return "mask"
    if run_input.role == RunInputRole.IMAGE and run_input.position == 0:
        return "primary"
    return "reference"


def run_inputs(db: Session, run_id: uuid.UUID, *, sent_only: bool) -> list[contract.ImageRef]:
    """Run の入力。`sent_only` ならプロバイダーに送るもの(image / mask)だけ。"""
    rows = db.execute(
        select(RunInput, Asset)
        .join(Asset, Asset.id == RunInput.asset_id)
        .where(RunInput.run_id == run_id)
        .order_by(RunInput.position)
    ).all()
    result: list[contract.ImageRef] = []
    for run_input, asset in rows:
        if sent_only and run_input.role not in (RunInputRole.IMAGE, RunInputRole.MASK):
            continue
        result.append(image_ref(asset, _input_role(run_input)))
    return result


def run_texts(run: Run) -> list[contract.TextPart]:
    parts = [contract.TextPart(role="prompt", text=run.prompt)] if run.prompt else []
    negative = (run.params or {}).get("negative_prompt")
    if isinstance(negative, str) and negative:
        parts.append(contract.TextPart(role="negative_prompt", text=negative))
    return parts


def image_run_subject(db: Session, settings: Settings, run: Run) -> contract.BeforeSendSubject:
    return contract.BeforeSendSubject(
        purpose="image_run",
        target=run_target(db, settings, run),
        text=run_texts(run),
        params=run_params(run),
        images=run_inputs(db, run.id, sent_only=True),
        run_id=str(run.id),
    )


def asset_ingested_subject(db: Session, asset: Asset, source: str) -> contract.AssetIngestedSubject:
    rating = db.get(AssetContentRating, asset.id)
    return contract.AssetIngestedSubject(
        asset=image_ref(asset),
        kind=str(asset.kind),
        source=source,
        run_id=str(asset.produced_by_run_id) if asset.produced_by_run_id else None,
        rating=rating.level if rating is not None else None,
    )


def run_finished_subject(
    db: Session,
    run: Run,
    *,
    status: str | None = None,
    outputs: list[contract.ImageRef] | None = None,
    usage: dict[str, Any] | None = None,
) -> contract.RunFinishedSubject:
    """`status`・`outputs`・`usage` は、「判定を待つ」購読に、出力を取り込む前(Run の状態を
    書く前)の値を渡すときだけ指定する(ADR-0051 2章)。"""
    if outputs is None:
        rows = (
            db.execute(
                select(Asset).where(Asset.produced_by_run_id == run.id).order_by(Asset.output_index)
            )
            .scalars()
            .all()
        )
        outputs = [image_ref(asset) for asset in rows]
    if usage is None:
        usage = dict(run.usage) if isinstance(run.usage, dict) else None
    error = None
    if run.error_code or run.error_message:
        error = contract.RunError(code=run.error_code, message=run.error_message)
    return contract.RunFinishedSubject(
        run_id=str(run.id),
        status=status or str(run.status),
        provider=run.provider,
        model=run.model,
        operation=str(run.operation),
        prompt=run.prompt,
        params=run_params(run),
        inputs=run_inputs(db, run.id, sent_only=False),
        outputs=outputs,
        usage=usage,
        error=error,
    )
