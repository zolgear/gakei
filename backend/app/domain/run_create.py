"""Run の作成(検証 → Run と run_input の挿入 → runner への投入)。

REST(`POST /api/runs`)と MCP(`generate_image`。ADR-0023)の両方から呼ぶ。HTTP に依存しない
例外 `RunCreateError` を送出し、呼び出し側がそれぞれの形(HTTP のステータス、MCP のツールエラー)
に変換する。メッセージは `t()` で解決済みの文字列。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.orm import Session

from app.config import Settings
from app.domain import api_key as api_key_domain
from app.domain.asset_groups import get_active_group_or_none
from app.domain.models import Asset, Run, RunInput, RunStatus
from app.domain.run_validation import RunInputMeta, RunValidationError, validate_run_request
from app.domain.schemas import RunCreateRequest
from app.i18n import t
from app.providers.base import ProviderUnavailableError, RunDraft
from app.providers.registry import ProviderRegistry
from app.worker.runner import Runner

# REST ではそれぞれ 422 / 409 / 404 に変換する。
RunCreateErrorKind = Literal["invalid", "conflict", "not_found"]


class RunCreateError(Exception):
    """Run を作らずに断る理由。`kind` で種類を、`str(e)` で利用者向けの文言を表す。"""

    def __init__(self, kind: RunCreateErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind: RunCreateErrorKind = kind


@dataclass(frozen=True)
class RunOrigin:
    """Run の実行元(ADR-0023 5章)。画面からの Run は `origin=None`。"""

    origin: str | None = None
    api_token_id: uuid.UUID | None = None


def create_run(
    db: Session,
    registry: ProviderRegistry,
    runner: Runner,
    settings: Settings,
    body: RunCreateRequest,
    *,
    created_by_user_id: uuid.UUID | None,
    origin: RunOrigin | None = None,
) -> Run:
    """検証してから Run を挿入してコミットし、runner を起こす。作った Run を返す。

    検証に通らなければ何も書き込まずに `RunCreateError` を送出する(Run は追記のみの証跡の
    ため、実行して failed にするより作る前に断る方が記録を汚さない。ADR-0012、ADR-0013)。
    """
    origin = origin or RunOrigin()

    # provider を引く(省略時は主プロバイダー)。未知の provider は invalid(ADR-0013)。
    provider_name = body.provider or registry.primary
    provider = registry.get(provider_name)
    if provider is None:
        raise RunCreateError("invalid", t("runs.unknownProvider", provider=provider_name))

    # ADR-0012: キーが無い状態で Run を作らない。
    if getattr(provider, "requires_api_key", False):
        api_key, _source = api_key_domain.resolve_key(settings)
        if not api_key:
            raise RunCreateError("conflict", t("openai.missingApiKey"))

    # ADR-0013: 接続できない等でプロバイダーが使えない場合も、Run を作らずに断る。
    available, unavailable_reason = provider.availability()
    if not available:
        raise RunCreateError(
            "conflict",
            unavailable_reason or t("runs.providerUnavailable", label=provider.label),
        )

    # ADR-0022: 出力を入れるグループ。存在しない・削除済みなら Run を作らない。
    if (
        body.asset_group_id is not None
        and get_active_group_or_none(db, body.asset_group_id) is None
    ):
        raise RunCreateError("not_found", t("assetGroups.notFound"))

    caps = provider.capabilities()

    input_metas: list[RunInputMeta] = []
    for item in body.inputs:
        asset = db.get(Asset, item.asset_id)
        if asset is None or asset.deleted_at is not None:
            raise RunCreateError("invalid", t("runs.assetNotFound", id=item.asset_id))
        input_metas.append(
            RunInputMeta(
                asset_id=item.asset_id,
                role=item.role,
                position=item.position,
                width=asset.width,
                height=asset.height,
                sha256=asset.sha256,
                mime=asset.mime,
            )
        )

    try:
        validate_run_request(
            caps, body.operation, body.model, body.prompt, body.params, input_metas
        )
    except RunValidationError as e:
        raise RunCreateError("invalid", str(e)) from e

    draft = RunDraft(
        operation=body.operation,
        model=body.model,
        prompt=body.prompt,
        params=body.params,
        inputs=input_metas,
    )
    try:
        params = provider.finalize_params(db, draft)
    except RunValidationError as e:
        raise RunCreateError("invalid", str(e)) from e
    except ProviderUnavailableError as e:
        raise RunCreateError("conflict", str(e)) from e

    run = Run(
        # レジストリのキー(provider_name)を記録する。runner はこのキーで実行レーンを選ぶ
        # ため、provider.name(自己申告の属性)ではなくこちらを正とする。
        provider=provider_name,
        model=body.model,
        deployment=None,
        operation=body.operation,
        prompt=body.prompt,
        params=params,
        status=RunStatus.QUEUED,
        created_by_user_id=created_by_user_id,
        asset_group_id=body.asset_group_id,
        origin=origin.origin,
        api_token_id=origin.api_token_id,
    )
    db.add(run)
    db.flush()

    for item in body.inputs:
        db.add(
            RunInput(run_id=run.id, asset_id=item.asset_id, role=item.role, position=item.position)
        )

    db.commit()
    runner.notify()
    return run
