"""領域の四角の検出(ADR-0053 7章)。

- `GET /api/settings/region-detection`: 設定と、検出を使えるか(`usable`)。全ログイン者が読める
  (画面が検出のボタンを出すかの判断に使う)。
- `PATCH /api/settings/region-detection`: 設定を変える(管理者だけ)。
- `POST /api/assets/{asset_id}/regions/detect`: 見える Asset の画像で四角を検出する。結果は保存
  しない。

検出は、管理者が選んだ LLM の接続先(ADR-0032)の VLM に、画像(`detail: high`)と指示を
Structured Outputs で送る(生成のアシスタントの構造化出力のエンジンを使う)。送る前に Mod の
`ai.before_send`(目的 `region_detection`。ADR-0051)を呼ぶ。
"""

from __future__ import annotations

import uuid

import anyio.to_thread
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.assistant.engine import AssistantEngineError
from app.assistant.runtime import AssistantRuntime, user_key
from app.assistant.structured import StructuredImage, StructuredRequest
from app.auth.deps import require_admin, require_user
from app.auth.identity import CurrentUser
from app.deps import get_session
from app.domain import content_withholding, derivatives, llm_connections
from app.domain import region_detection as detection
from app.domain import region_detection_settings as detection_settings
from app.domain.llm_connections import ConnectionNotFoundError, LlmTarget
from app.domain.models import Asset, AssetKind
from app.domain.schemas import (
    DetectedRegion,
    RegionDetectionSettingsResponse,
    RegionDetectionSettingsUpdateRequest,
    RegionDetectRequest,
    RegionDetectResponse,
    RegionDetectUsage,
)
from app.domain.visibility import get_visible_asset
from app.i18n import current_locale, t
from app.mods import events as mods_events
from app.mods.api import AI_BEFORE_SEND, BeforeSendSubject, TextPart

router = APIRouter(tags=["region-detection"])


# -- 設定 ------------------------------------------------------------------------------


def _usable(
    db: Session, request: Request, config: detection_settings.RegionDetectionConfig
) -> bool:
    if not config.enabled:
        return False
    runtime: AssistantRuntime = request.app.state.assistant
    if not runtime.structured.requires_target:
        return True
    settings = request.app.state.settings
    try:
        target = detection_settings.resolve_target(db, config, settings)
    except ConnectionNotFoundError:
        return False
    return bool(target.api_key) and detection_settings.structured_outputs_supported(
        db, config, settings
    )


def _settings_response(
    db: Session, request: Request, user: CurrentUser
) -> RegionDetectionSettingsResponse:
    config = detection_settings.load(db)
    runtime: AssistantRuntime = request.app.state.assistant
    return RegionDetectionSettingsResponse(
        **{name: getattr(config, name) for name in detection_settings.FIELDS},
        hourly_request_limit_default=detection_settings.DEFAULT_HOURLY_REQUEST_LIMIT,
        hourly_request_limit_max=detection_settings.HOURLY_REQUEST_LIMIT_MAX,
        usable=_usable(db, request, config),
        requests_last_hour=runtime.detection_requests.count(user_key(user.id)),
    )


@router.get(
    "/api/settings/region-detection",
    response_model=RegionDetectionSettingsResponse,
    operation_id="get_region_detection_settings",
)
def get_region_detection_settings(
    request: Request,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> RegionDetectionSettingsResponse:
    return _settings_response(db, request, user)


@router.patch(
    "/api/settings/region-detection",
    response_model=RegionDetectionSettingsResponse,
    operation_id="update_region_detection_settings",
)
def update_region_detection_settings(
    body: RegionDetectionSettingsUpdateRequest,
    request: Request,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_admin),
) -> RegionDetectionSettingsResponse:
    # null を「変更しない」ではなく不正な値として扱う(検証で 422)。先に全部を検証してから
    # 保存する(一部だけ保存される事態を避ける)。
    updates = {name: getattr(body, name) for name in body.model_fields_set}
    try:
        detection_settings.save(db, updates)
    except detection_settings.RegionDetectionSettingsValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _settings_response(db, request, user)


# -- 検出 ------------------------------------------------------------------------------

_DETECT_DOC = """見える Asset の画像で、四角の候補を検出する(ADR-0053 7章)。保存はしない。

`query` があれば、それに当てはまる物だけ(複数なら複数)を、無ければ主な物を一通り挙げる。
四角は画像に対する割合(0〜1)で、左上を原点にした `[x0, y0, x1, y1]`。送る前に Mod の
`ai.before_send`(目的 `region_detection`。ADR-0051)を呼ぶ。検出の1時間の上限に数える。
10 秒を超えることがある。

断るとき(404 以外の `detail` は `{code, message}`):

- 404: Asset が無い・見えない
- 409 `mask_not_supported` / `asset_deleted` / `image_withheld` / `image_missing`: その画像では
  検出できない
- 409 `disabled`: 検出が無効
- 409 `unsupported`: 接続先が Structured Outputs に対応しない
- 409 `connection_missing`: 接続先・キーが無い
- 429 `rate_limited`: 1時間の上限
- 409 `hook_denied`: Mod が送信を止めた(理由は `message`)
- 502 `llm_error`: VLM の呼び出しに失敗した・応答を読めなかった
"""


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


@router.post(
    "/api/assets/{asset_id}/regions/detect",
    response_model=RegionDetectResponse,
    operation_id="detect_asset_regions",
    tags=["assets"],
    description=_DETECT_DOC,
)
async def detect_regions(
    asset_id: uuid.UUID,
    body: RegionDetectRequest,
    request: Request,
    user: CurrentUser = Depends(require_user),
) -> RegionDetectResponse:
    state = request.app.state
    runtime: AssistantRuntime = state.assistant
    engine = runtime.structured
    query = (body.query or "").strip() or None
    max_items = min(body.max_items, detection.MAX_ITEMS)

    def _prepare() -> tuple[
        detection_settings.RegionDetectionConfig,
        LlmTarget | None,
        llm_connections.ConnectionConfig | None,
        Asset,
        bytes,
    ]:
        with state.session_factory() as db:
            asset = get_visible_asset(db, user, asset_id)
            if asset is None:
                raise HTTPException(status_code=404, detail=t("assets.notFound"))
            if asset.kind == AssetKind.MASK:
                raise _error(409, "mask_not_supported", t("regionDetection.maskNotSupported"))
            if asset.deleted_at is not None:
                raise _error(409, "asset_deleted", t("regionDetection.assetDeleted"))
            # ADR-0051 4章: 画像を残さなかった・`deny` の判定を受けた画像は外部の AI へ送らない。
            if content_withholding.blocked_asset_ids(db, [asset.id]):
                raise _error(409, "image_withheld", t("regionDetection.imageWithheld"))
            config = detection_settings.load(db)
            if not config.enabled:
                raise _error(409, "disabled", t("regionDetection.disabled"))
            target: LlmTarget | None = None
            if engine.requires_target:
                try:
                    target = detection_settings.resolve_target(db, config, state.settings)
                except ConnectionNotFoundError as e:
                    raise _error(409, "connection_missing", t("assistant.connectionMissing")) from e
                if not detection_settings.structured_outputs_supported(db, config, state.settings):
                    raise _error(409, "unsupported", t("regionDetection.unsupported"))
                if not target.api_key:
                    raise _error(409, "connection_missing", t("assistant.apiKeyMissing"))
            connection = llm_connections.find_connection(
                llm_connections.load(db), config.connection_id
            )
            db.expunge(asset)
        # 派生のプレビュー(2048px)から縮める(原本は大きいことがある。派生は ensure_derived を
        # 通す。ADR-0036)。割合で答えるので、縦横比が同じなら座標は変わらない。
        content = derivatives.ensure_derived(state.store, asset.blob_key, asset.sha256, "preview")
        if content is None:
            raise _error(409, "image_missing", t("regionDetection.imageMissing"))
        try:
            image = detection.prepare_image(content.read_all())
        except (OSError, ValueError) as e:
            raise _error(409, "image_missing", t("regionDetection.imageUnreadable")) from e
        return config, target, connection, asset, image

    config, target, connection, asset, image = await anyio.to_thread.run_sync(_prepare)
    locale = current_locale()
    instructions = detection.instructions(locale, query=query, max_items=max_items)
    input_text = detection.build_input(query)

    if not runtime.detection_requests.try_acquire(user_key(user.id), config.hourly_request_limit):
        raise _error(
            429,
            "rate_limited",
            t("regionDetection.hourlyLimitReached", limit=config.hourly_request_limit),
        )

    # ADR-0051: 外部の AI へ送る直前のチェック(目的 `region_detection`)。接続先がローカルでも
    # 同じ入口を通す。
    mods = getattr(state, "mods", None)
    if mods is not None and mods.wants(AI_BEFORE_SEND, purpose="region_detection"):

        def _subject() -> BeforeSendSubject:
            with state.session_factory() as db:
                conn = connection or llm_connections.ConnectionConfig(
                    id=config.connection_id, name="", base_url=None
                )
                base_url = (
                    target.base_url
                    if target is not None
                    else llm_connections.display_base_url(conn, state.settings)
                )
                text = [TextPart(role="system", text=instructions)]
                text.append(TextPart(role="query" if query else "user", text=input_text))
                return BeforeSendSubject(
                    purpose="region_detection",
                    target=mods_events.llm_target(
                        db, state.settings, conn, model=config.model, base_url=base_url
                    ),
                    text=text,
                    params={
                        "model": config.model,
                        "query": query,
                        "max_items": max_items,
                        "image_max_side": detection.IMAGE_MAX_SIDE,
                        "image_detail": detection.IMAGE_DETAIL,
                        "reasoning_effort": detection.REASONING_EFFORT,
                    },
                    images=[mods_events.image_ref(asset, "primary")],
                    asset_id=str(asset.id),
                )

        subject = await anyio.to_thread.run_sync(_subject)
        denial = await mods.before_send(subject, actor=mods_events.actor_from_user(user))
        if denial is not None:
            raise _error(409, "hook_denied", denial.message)

    try:
        result = await engine.complete(
            StructuredRequest(
                target=target,
                instructions=instructions,
                input_text=input_text,
                schema_name=detection.SCHEMA_NAME,
                schema=detection.RESPONSE_SCHEMA,
                reasoning_effort=detection.REASONING_EFFORT,
                fake_response=detection.fake_response(locale, query=query, max_items=max_items),
                images=(StructuredImage(data=image, detail=detection.IMAGE_DETAIL),),
            )
        )
    except AssistantEngineError as e:
        raise _error(502, "llm_error", t("regionDetection.llmError", error=str(e))) from e

    boxes = detection.interpret(result.data, max_items)
    return RegionDetectResponse(
        regions=[DetectedRegion(label=b.label, box=list(b.box)) for b in boxes],
        model=result.model or config.model,
        usage=RegionDetectUsage(**result.usage),
    )
