"""Run の作成(検証 → Run と run_input の挿入 → runner への投入)。

REST(`POST /api/runs`)と MCP(`generate_image`。ADR-0023)の両方から呼ぶ。HTTP に依存しない
例外 `RunCreateError` を送出し、呼び出し側がそれぞれの形(HTTP のステータス、MCP のツールエラー)
に変換する。メッセージは `t()` で解決済みの文字列。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.auth.identity import CurrentUser
from app.config import Settings
from app.domain import api_key as api_key_domain
from app.domain.models import Run, RunInput, RunStatus
from app.domain.run_validation import RunInputMeta, RunValidationError, validate_run_request
from app.domain.schemas import RunCreateRequest
from app.domain.visibility import get_visible_asset, get_visible_group
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
    viewer: CurrentUser,
    origin: RunOrigin | None = None,
) -> Run:
    """Run を1つ作る(MCP 用。繰り返し回数は見ない。ADR-0042 3章)。"""
    runs = create_runs(db, registry, runner, settings, body, viewer=viewer, origin=origin)
    return runs[0]


def create_runs(
    db: Session,
    registry: ProviderRegistry,
    runner: Runner,
    settings: Settings,
    body: RunCreateRequest,
    *,
    viewer: CurrentUser,
    origin: RunOrigin | None = None,
    repeat: int = 1,
) -> list[Run]:
    """検証してから同じ設定の Run を `repeat` 個挿入してコミットし、runner を起こす。

    作った Run を積んだ順に返す。検証は1回だけ行い、Run ごとの確定(`finalize_params`)の
    どれかが断れば1つも作らない(1つのトランザクション。ADR-0042 3章)。runner への通知は
    最後に1回。

    実行者は `viewer`(`created_by_user_id = viewer.id`。個人モードは null)。入力画像・マスク・
    出力先のグループは `viewer` に見えるものだけを指定でき、見えないものは存在しない場合と
    同じエラーにする(ADR-0025 3章)。

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

    # ADR-0022: 出力を入れるグループ。存在しない・削除済み・見えない(ADR-0025)なら
    # Run を作らない。
    if (
        body.asset_group_id is not None
        and get_visible_group(db, viewer, body.asset_group_id) is None
    ):
        raise RunCreateError("not_found", t("assetGroups.notFound"))

    caps = provider.capabilities()

    input_metas: list[RunInputMeta] = []
    for item in body.inputs:
        asset = get_visible_asset(db, viewer, item.asset_id)
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

    if repeat < 1:
        raise ValueError("repeat must be >= 1")

    # 利用者が seed を指定したときだけ、2つ目以降の seed を進める(ADR-0042 2章)。指定が
    # 無ければ、各 Run でプロバイダーがそれぞれ決める(今と同じ)。
    user_seed = body.params.get("seed")
    repeat_seed = getattr(provider, "repeat_seed", None)

    finalized: list[dict[str, Any]] = []
    for index in range(repeat):
        params = body.params
        if index > 0 and user_seed is not None and repeat_seed is not None:
            seed = repeat_seed(finalized[0], index)
            if seed is not None:
                params = {**body.params, "seed": seed}
        draft = RunDraft(
            operation=body.operation,
            model=body.model,
            prompt=body.prompt,
            params=params,
            inputs=input_metas,
        )
        try:
            finalized.append(provider.finalize_params(db, draft))
        except RunValidationError as e:
            raise RunCreateError("invalid", str(e)) from e
        except ProviderUnavailableError as e:
            raise RunCreateError("conflict", str(e)) from e

    # 積んだ順を queued_at で保つ(runner も履歴も queued_at 順。同じ時刻だと id の順になり、
    # 順番が崩れる)。時計の分解能が粗い環境でも並ぶよう、1マイクロ秒ずつずらす。
    queued_base = datetime.now(UTC)
    runs: list[Run] = []
    for index, params in enumerate(finalized):
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
            queued_at=queued_base + timedelta(microseconds=index),
            created_by_user_id=viewer.id,
            asset_group_id=body.asset_group_id,
            origin=origin.origin,
            api_token_id=origin.api_token_id,
        )
        db.add(run)
        db.flush()
        for item in body.inputs:
            db.add(
                RunInput(
                    run_id=run.id, asset_id=item.asset_id, role=item.role, position=item.position
                )
            )
        runs.append(run)

    db.commit()
    runner.notify()
    return runs
