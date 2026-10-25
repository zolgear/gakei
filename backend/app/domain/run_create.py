"""Run の作成(検証 → Run と run_input の挿入 → runner への投入)。

REST(`POST /api/runs`)と MCP(`generate_image`。ADR-0023)の両方から呼ぶ。HTTP に依存しない
例外 `RunCreateError` を送出し、呼び出し側がそれぞれの形(HTTP のステータス、MCP のツールエラー)
に変換する。メッセージは `t()` で解決済みの文字列。
"""

from __future__ import annotations

import io
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from PIL import Image
from sqlalchemy.orm import Session

from app.auth.identity import CurrentUser
from app.config import Settings
from app.domain import api_key as api_key_domain
from app.domain import assets as assets_domain
from app.domain import content_withholding, prompt_templates, sizes
from app.domain import regions as regions_domain
from app.domain.models import Asset, AssetKind, Run, RunInput, RunInputRole, RunStatus
from app.domain.run_validation import RunInputMeta, RunValidationError, validate_run_request
from app.domain.schemas import RunCreateRequest
from app.domain.storage import AssetStore
from app.domain.visibility import (
    get_visible_asset,
    get_visible_group,
    get_visible_prompt_template,
)
from app.i18n import t
from app.providers.base import (
    OperationCapabilities,
    ProviderCapabilities,
    ProviderUnavailableError,
    RunDraft,
)
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
    # ADR-0051 4章: `deny` の判定を受けた・画像を残さなかった Asset は入力に使えない(外部の
    # AI へ送らない)。見える Asset だけを確かめる(見えないものは下で「無い」と同じ扱い)。
    blocked = content_withholding.blocked_asset_ids(db, [item.asset_id for item in body.inputs])
    for item in body.inputs:
        asset = get_visible_asset(db, viewer, item.asset_id)
        if asset is None or asset.deleted_at is not None:
            raise RunCreateError("invalid", t("runs.assetNotFound", id=item.asset_id))
        if asset.id in blocked:
            raise RunCreateError("conflict", t("mods.inputDenied", id=item.asset_id))
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

    if repeat < 1:
        raise ValueError("repeat must be >= 1")

    # プロンプトテンプレート(実験)。Run ごとに組み立て直す(繰り返し回数 N なら N 回。
    # 意味の通ったガチャになる)。`prompt` と `prompt_template` の排他はスキーマで断っている。
    prompts, builds = _resolve_prompts(db, body, viewer=viewer, repeat=repeat)

    # ADR-0053: 領域の指示の四角を「画像と文」に変える。四角を描いた画像と参照画像は、利用者の
    # 入力の後ろに role=image で足し(role=reference はプロバイダーに送らないため)、文は
    # プロンプトの後ろに足す。四角を描いた画像の保存は、検証がすべて通った後に行う。
    # ADR-0053 6章: ComfyUI(`comfyui_bboxes`)では、四角をそのままプロバイダーに渡し、ワークフローの
    # Create Bounding Boxes に差し込ませる(重ねた画像も足す文も作らない)。
    # ADR-0053 5章: 入力画像の無い Run(generate)の「画像と文」は、白紙に四角を描いた配置図を唯一の
    # 入力にして Edit で送る。送る先が Edit なので、以降の検証・確定・記録は `operation` で行う。
    operation = body.operation
    plan: _RegionsPlan | None = None
    draft_regions: list[Any] = []
    regions_json: dict[str, Any] | None = None
    if body.regions:
        op_caps = _regions_op_caps(caps, body)
        if op_caps.regions_delivery == regions_domain.DELIVERY_COMFYUI_BBOXES:
            frame = _check_comfyui_regions(op_caps, body, input_metas)
            draft_regions = regions_domain.ordered(body.regions)
            regions_json = regions_domain.comfyui_record(draft_regions, frame=frame)
        else:
            plan = _plan_regions(
                db, caps, op_caps, body, input_metas, viewer=viewer, store=runner.store
            )
            input_metas = [*input_metas, *plan.extra_metas]
            prompts = [prompt + plan.appended_text for prompt in prompts]
            operation = plan.operation

    for index in range(repeat):
        try:
            validate_run_request(
                caps,
                operation,
                body.model,
                prompts[index],
                body.params,
                input_metas,
            )
        except RunValidationError as e:
            raise RunCreateError("invalid", str(e)) from e

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
                params = {**params, "seed": seed}
        draft = RunDraft(
            operation=operation,
            model=body.model,
            prompt=prompts[index],
            params=params,
            inputs=input_metas,
            regions=draft_regions,
        )
        try:
            finalized.append(provider.finalize_params(db, draft))
        except RunValidationError as e:
            raise RunCreateError("invalid", str(e)) from e
        except ProviderUnavailableError as e:
            raise RunCreateError("conflict", str(e)) from e

    extra_inputs: list[tuple[uuid.UUID, int]] = []
    if plan is not None:
        overlay = assets_domain.ingest(
            db,
            runner.store,
            plan.overlay.data,
            AssetKind.REGIONS,
            # 白紙の配置図(入力画像の無い Run)は元の画像を持たない。
            source_asset_id=plan.primary_asset_id,
            created_by_user_id=viewer.id,
            prepared=plan.overlay,
        )
        extra_inputs = [(overlay.id, plan.overlay_position), *plan.reference_inputs]
        regions_json = regions_domain.RegionsRecord(
            regions=regions_domain.ordered(body.regions),
            appended_text=plan.appended_text,
            overlay_asset_id=overlay.id,
            frame=plan.frame,
        ).to_json()

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
            operation=operation,
            prompt=prompts[index],
            params=params,
            # プロンプトテンプレート(実験)で組み立てたときの内訳。作成時に一度だけ書く。
            prompt_build=builds[index],
            # 領域の指示の四角(ADR-0053 4章)。作成時に一度だけ書く。
            regions=regions_json,
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
        for asset_id, position in extra_inputs:
            db.add(
                RunInput(
                    run_id=run.id, asset_id=asset_id, role=RunInputRole.IMAGE, position=position
                )
            )
        runs.append(run)

    db.commit()
    runner.notify()
    return runs


def _resolve_prompts(
    db: Session,
    body: RunCreateRequest,
    *,
    viewer: CurrentUser,
    repeat: int,
) -> tuple[list[str], list[dict[str, Any] | None]]:
    """Run ごとの(プロンプト、組み立ての内訳)を決める。

    プロンプトテンプレート(実験)を使わないときは、今までどおり `body.prompt` のまま。
    使うときは Run ごとに組み立て直す(`params` には手を触れない)。
    """
    if body.prompt_template is None:
        assert body.prompt is not None  # スキーマでどちらか一方を強制している
        return [body.prompt] * repeat, [None] * repeat

    template = get_visible_prompt_template(db, viewer, body.prompt_template.template_id)
    if template is None:
        raise RunCreateError("not_found", t("promptTemplates.notFound"))
    spec = prompt_templates.load_spec(db, template)
    selections = body.prompt_template.selections.to_domain()

    prompts: list[str] = []
    builds: list[dict[str, Any] | None] = []
    for _ in range(repeat):
        try:
            result = prompt_templates.build(spec, selections)
        except prompt_templates.PromptBuildError as e:
            raise RunCreateError("invalid", str(e)) from e
        prompts.append(result.prompt)
        builds.append(result.build)
    return prompts, builds


@dataclass(frozen=True)
class _RegionsPlan:
    """領域の指示を送るための準備(ADR-0053 5章「画像と文」)。DB と保存先にはまだ書いていない。"""

    # 四角を描いた画像の元の画像(主たる入力)。白紙の配置図(入力画像の無い Run と、白紙の土台を
    # 選んだ Run)では None。
    primary_asset_id: uuid.UUID | None
    # 四角を描いた画像(検証済み。保存は Run の挿入の直前)。
    overlay: assets_domain.PreparedImage
    overlay_position: int
    # 利用者の入力に無い参照画像(asset_id, position)。四角を描いた画像の後ろに足す。
    reference_inputs: list[tuple[uuid.UUID, int]]
    # 検証に使う、足した入力(四角を描いた画像と参照画像)。
    extra_metas: list[RunInputMeta]
    appended_text: str
    # 送る操作(入力画像の無い Run も Edit で送る)と、座標が何に対する割合か。
    operation: Literal["generate", "edit"] = "edit"
    frame: Literal["input", "output"] = "input"


def _regions_op_caps(caps: ProviderCapabilities, body: RunCreateRequest) -> OperationCapabilities:
    """四角を受ける操作の capabilities。受けなければ `RunCreateError`。"""
    model_caps = next((m for m in caps.models if m.model == body.model), None)
    if model_caps is None:
        raise RunCreateError("invalid", t("runValidation.unknownModel", model=body.model))
    op_caps = next((o for o in model_caps.operations if o.operation == body.operation), None)
    if op_caps is None or not op_caps.supports_regions:
        # 「画像と文」(OpenAI)は Edit だけが受ける。Generate にはそのことを伝える。
        if body.operation != "edit" and _any_operation_supports_regions(model_caps.operations):
            raise RunCreateError("invalid", t("runRegions.editOnly"))
        raise RunCreateError("invalid", t("runRegions.notSupported"))
    return op_caps


def _any_operation_supports_regions(operations: list[OperationCapabilities]) -> bool:
    return any(o.supports_regions for o in operations)


def _check_region_options(
    op_caps: OperationCapabilities, body: RunCreateRequest, *, blank: bool = False
) -> None:
    """capabilities が受けない種類・参照画像の四角を断る(ADR-0053 6章)。

    `blank` は白紙の土台。選べる種類は「変える」「足す」だけ(1章「四角の土台を選ぶ」)。"""
    for region in regions_domain.ordered(body.regions):
        if blank and region.kind not in regions_domain.BLANK_REGION_KINDS:
            raise RunCreateError("invalid", t("runRegions.kindNotOnBlank", number=region.number))
        if region.kind not in op_caps.regions_kinds:
            raise RunCreateError(
                "invalid",
                t("runRegions.kindNotSupported", number=region.number),
            )
        if region.reference_asset_id is not None and not op_caps.regions_reference_images:
            raise RunCreateError(
                "invalid", t("runRegions.referenceNotSupported", number=region.number)
            )


def _check_comfyui_regions(
    op_caps: OperationCapabilities, body: RunCreateRequest, input_metas: list[RunInputMeta]
) -> Literal["input", "output"]:
    """ComfyUI に差し込む四角を確かめ、座標が何に対する割合かを返す(ADR-0053 6章)。

    選べる種類は「変える」「足す」だけ、参照画像は使えない(「動かす」の元の四角も持てない。
    `source_box` は「動かす」だけが持つので、種類の確認で断られる)。座標は要求の土台
    (`regions_frame`)に対する割合として受ける。省略時は、主たる入力があればそれ、無ければ出力の
    大きさ。白紙の土台は入力画像があっても選べる(1章「四角の土台を選ぶ」。検証はどちらも同じ 0〜1)。
    """
    has_primary = any(m.role == "image" and m.position == 0 for m in input_metas)
    frame = _resolve_regions_frame(body, has_primary=has_primary)
    _check_region_options(op_caps, body, blank=frame == "output")
    if frame == "output" and not has_primary and op_caps.regions_requires_input_image:
        raise RunCreateError("invalid", t("runRegions.primaryRequired"))
    return frame


def _resolve_regions_frame(
    body: RunCreateRequest, *, has_primary: bool
) -> Literal["input", "output"]:
    """四角の土台(ADR-0053 1章「四角の土台を選ぶ」)。省略時は主たる入力の有無で決める。
    主たる入力の無い Run で `input` を求められたら断る。"""
    frame = body.regions_frame or ("input" if has_primary else "output")
    if frame == "input" and not has_primary:
        raise RunCreateError("invalid", t("runRegions.primaryRequired"))
    return frame


def _load_reference_assets(
    db: Session, viewer: CurrentUser, reference_ids: list[uuid.UUID]
) -> list[Asset]:
    """四角の参照画像を引く。見えない・削除済み・マスク・`deny` の判定を受けたものは断る。"""
    blocked = content_withholding.blocked_asset_ids(db, reference_ids)
    reference_assets: list[Asset] = []
    for ref in reference_ids:
        asset = get_visible_asset(db, viewer, ref)
        if asset is None or asset.deleted_at is not None or asset.kind == AssetKind.MASK:
            raise RunCreateError("invalid", t("runs.assetNotFound", id=ref))
        if asset.id in blocked:
            raise RunCreateError("conflict", t("mods.inputDenied", id=ref))
        reference_assets.append(asset)
    return reference_assets


def _plan_regions(
    db: Session,
    caps: ProviderCapabilities,
    op_caps: OperationCapabilities,
    body: RunCreateRequest,
    input_metas: list[RunInputMeta],
    *,
    viewer: CurrentUser,
    store: AssetStore,
) -> _RegionsPlan:
    """四角を検証し、四角を描いた画像と足す文を作る。断るときは `RunCreateError`。

    土台(ADR-0053 1章「四角の土台を選ぶ」)が主たる入力なら、主たる入力に四角を重ねた画像を、
    白紙なら出力の大きさの白紙に四角を描いた配置図を、利用者の入力の後ろ(参照画像の前)に足す。
    主たる入力(`position = 0`)はどちらも利用者の1枚目のまま。"""
    if body.operation != "edit":
        if op_caps.regions_requires_input_image:
            raise RunCreateError("invalid", t("runRegions.editOnly"))
        return _plan_blank_regions(db, caps, op_caps, body, input_metas, viewer=viewer)

    image_metas = sorted((m for m in input_metas if m.role == "image"), key=lambda m: m.position)
    primary_meta = next((m for m in image_metas if m.position == 0), None)
    if primary_meta is None:
        raise RunCreateError("invalid", t("runRegions.primaryRequired"))
    frame = _resolve_regions_frame(body, has_primary=True)
    blank = frame == "output"
    _check_region_options(op_caps, body, blank=blank)

    # 入力の何枚目か(1 から。プロバイダーに送る順 = role=image の position の順)。
    image_numbers = {m.asset_id: index + 1 for index, m in enumerate(image_metas)}
    next_position = image_metas[-1].position + 1
    overlay_position = next_position
    overlay_number = len(image_metas) + 1

    # 参照画像。利用者の入力にあればその画像を指し、無ければ四角を描いた画像の後ろに足す。
    reference_ids: list[uuid.UUID] = []
    for region in regions_domain.ordered(body.regions):
        ref = region.reference_asset_id
        if ref is not None and ref not in image_numbers and ref not in reference_ids:
            reference_ids.append(ref)
    reference_assets = _load_reference_assets(db, viewer, reference_ids)

    # ADR-0053 5章: 足す画像も Edit の入力の枚数の上限に数える。超えるなら Run を作らない。
    total = len(image_metas) + 1 + len(reference_assets)
    if total > op_caps.max_input_images:
        raise RunCreateError(
            "invalid",
            t("runRegions.tooManyInputs", max=op_caps.max_input_images, count=total),
        )

    primary = db.get(Asset, primary_meta.asset_id)
    assert primary is not None  # 入力の検証で見える・削除されていないことを確かめている
    try:
        if blank:
            # 白紙の大きさは入力画像の無い Run と同じ(出力サイズ。`auto` は 1024×1024)。
            width, height = _blank_canvas_size(body, caps)
            data = regions_domain.render_layout(width, height, body.regions)
        else:
            with Image.open(io.BytesIO(store.read(primary.blob_key))) as image:
                data = regions_domain.render_overlay(image, body.regions)
        overlay = assets_domain.prepare_image(data, AssetKind.REGIONS)
    except (OSError, ValueError, assets_domain.IngestError) as e:
        raise RunCreateError("invalid", t("runRegions.overlayFailed")) from e

    extra_metas = [
        RunInputMeta(
            asset_id=overlay.asset_id,
            role="image",
            position=overlay_position,
            width=overlay.width,
            height=overlay.height,
            sha256=overlay.sha256,
            mime=overlay.mime,
        )
    ]
    reference_inputs: list[tuple[uuid.UUID, int]] = []
    reference_numbers = dict(image_numbers)
    for offset, asset in enumerate(reference_assets, start=1):
        position = overlay_position + offset
        reference_inputs.append((asset.id, position))
        reference_numbers[asset.id] = overlay_number + offset
        extra_metas.append(
            RunInputMeta(
                asset_id=asset.id,
                role="image",
                position=position,
                width=asset.width,
                height=asset.height,
                sha256=asset.sha256,
                mime=asset.mime,
            )
        )

    appended = regions_domain.build_prompt_text(
        body.regions,
        overlay_image_number=overlay_number,
        reference_image_numbers=reference_numbers,
        blank=blank,
    )
    return _RegionsPlan(
        # 白紙の配置図は主たる入力から作った画像ではないので、元の画像を持たない。
        primary_asset_id=None if blank else primary.id,
        overlay=overlay,
        overlay_position=overlay_position,
        reference_inputs=reference_inputs,
        extra_metas=extra_metas,
        appended_text=appended,
        frame=frame,
    )


def _blank_canvas_size(body: RunCreateRequest, caps: ProviderCapabilities) -> tuple[int, int]:
    """入力画像の無い Run の配置図の大きさ。

    出力サイズの設定の幅×高さ。`auto`・未指定は 1024×1024。"""
    raw = body.params.get("size")
    if raw is None:
        return regions_domain.BLANK_AUTO_SIZE
    try:
        parsed = sizes.parse_size(str(raw), caps.size)
    except sizes.InvalidSizeError as e:
        raise RunCreateError("invalid", str(e)) from e
    return parsed if parsed is not None else regions_domain.BLANK_AUTO_SIZE


def _plan_blank_regions(
    db: Session,
    caps: ProviderCapabilities,
    op_caps: OperationCapabilities,
    body: RunCreateRequest,
    input_metas: list[RunInputMeta],
    *,
    viewer: CurrentUser,
) -> _RegionsPlan:
    """入力画像の無い Run の四角を、白紙の配置図と足す文にする(ADR-0053 5章)。

    OpenAI の生成の API は画像を受けないので、出力の大きさの白紙に四角と番号だけを描いた配置図を
    唯一の主たる入力(`position = 0`)にし、参照画像をその後ろに足して Edit で送る。種類と参照画像は
    生成の capabilities で確かめ、入力の枚数の上限とパラメーターは Edit の capabilities で確かめる
    (パラメーターは呼び出し側の `validate_run_request`)。
    """
    if input_metas:
        raise RunCreateError("invalid", t("runValidation.generateNoInputImages"))
    _resolve_regions_frame(body, has_primary=False)
    _check_region_options(op_caps, body, blank=True)

    model_caps = next(m for m in caps.models if m.model == body.model)
    edit_caps = next((o for o in model_caps.operations if o.operation == "edit"), None)
    if edit_caps is None:
        raise RunCreateError("invalid", t("runRegions.notSupported"))

    reference_ids: list[uuid.UUID] = []
    for region in regions_domain.ordered(body.regions):
        ref = region.reference_asset_id
        if ref is not None and ref not in reference_ids:
            reference_ids.append(ref)
    reference_assets = _load_reference_assets(db, viewer, reference_ids)

    total = 1 + len(reference_assets)
    if total > edit_caps.max_input_images:
        raise RunCreateError(
            "invalid",
            t("runRegions.tooManyInputs", max=edit_caps.max_input_images, count=total),
        )

    width, height = _blank_canvas_size(body, caps)
    try:
        data = regions_domain.render_layout(width, height, body.regions)
        overlay = assets_domain.prepare_image(data, AssetKind.REGIONS)
    except (OSError, ValueError, assets_domain.IngestError) as e:
        raise RunCreateError("invalid", t("runRegions.overlayFailed")) from e

    extra_metas = [
        RunInputMeta(
            asset_id=overlay.asset_id,
            role="image",
            position=0,
            width=overlay.width,
            height=overlay.height,
            sha256=overlay.sha256,
            mime=overlay.mime,
        )
    ]
    reference_inputs: list[tuple[uuid.UUID, int]] = []
    reference_numbers: dict[Any, int] = {}
    for position, asset in enumerate(reference_assets, start=1):
        reference_inputs.append((asset.id, position))
        reference_numbers[asset.id] = position + 1
        extra_metas.append(
            RunInputMeta(
                asset_id=asset.id,
                role="image",
                position=position,
                width=asset.width,
                height=asset.height,
                sha256=asset.sha256,
                mime=asset.mime,
            )
        )

    appended = regions_domain.build_prompt_text(
        body.regions,
        overlay_image_number=1,
        reference_image_numbers=reference_numbers,
        blank=True,
    )
    return _RegionsPlan(
        primary_asset_id=None,
        overlay=overlay,
        overlay_position=0,
        reference_inputs=reference_inputs,
        extra_metas=extra_metas,
        appended_text=appended,
        operation="edit",
        frame="output",
    )
