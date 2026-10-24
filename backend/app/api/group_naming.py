"""グループ名の候補(ADR-0056「名前の候補」)。

- `GET /api/settings/group-naming`: 設定と、名前の候補を使えるか(`usable`)。全ログイン者が読める
  (提案のページが候補を取りに行くかの判断に使う)。
- `PATCH /api/settings/group-naming`: 設定を変える(管理者だけ)。
- `POST /api/asset-groups/suggestions/names`: 表示中の提案(新しいグループの提案)に、名前の候補を
  3つずつ付ける。提案の計算とは別のリクエストで、ページの表示の後に取る。保存はしない。

候補は、管理者が選んだ LLM の接続先(ADR-0032)に、タグだけを Structured Outputs で1回にまとめて
送って作る。接続先が外部なら、センシティブなタグを外して送る。送る前に Mod の `ai.before_send`
(目的 `group_naming`。ADR-0051)を呼ぶ。
"""

from __future__ import annotations

import anyio.to_thread
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.assistant.engine import AssistantEngineError
from app.assistant.runtime import AssistantRuntime
from app.assistant.structured import StructuredRequest
from app.auth.deps import require_admin, require_user
from app.auth.identity import CurrentUser
from app.deps import get_session
from app.domain import group_naming as naming
from app.domain import group_naming_settings as naming_settings
from app.domain import group_suggestions as suggestions_domain
from app.domain import llm_connections
from app.domain import locality as locality_domain
from app.domain.llm_connections import ConnectionNotFoundError, LlmTarget
from app.domain.schemas import (
    EmbeddingErrorDetail,
    GroupNamingSettingsResponse,
    GroupNamingSettingsUpdateRequest,
    GroupNamingUnusableReason,
    GroupSuggestionNames,
    GroupSuggestionNamesRequest,
    GroupSuggestionNamesResponse,
)
from app.domain.semantic_search import EmbeddingsConflictError
from app.i18n import t
from app.mods import events as mods_events
from app.mods.api import AI_BEFORE_SEND, BeforeSendSubject, TextPart

router = APIRouter(tags=["asset-groups"])


# -- 設定 ------------------------------------------------------------------------------


def _unusable_reason(
    db: Session, request: Request, config: naming_settings.GroupNamingConfig
) -> GroupNamingUnusableReason | None:
    """使えない理由を1つ(使えるなら None)。見る順は候補の API(`_prepare`)と同じ。"""
    if not config.enabled:
        return "disabled"
    runtime: AssistantRuntime = request.app.state.assistant
    if not runtime.structured.requires_target:
        return None
    return llm_connections.connection_problem(
        db, config.connection_id, request.app.state.settings, structured_outputs=True
    )


def _settings_response(db: Session, request: Request) -> GroupNamingSettingsResponse:
    config = naming_settings.load(db)
    reason = _unusable_reason(db, request, config)
    return GroupNamingSettingsResponse(
        **{name: getattr(config, name) for name in naming_settings.FIELDS},
        usable=reason is None,
        unusable_reason=reason,
    )


@router.get(
    "/api/settings/group-naming",
    response_model=GroupNamingSettingsResponse,
    operation_id="get_group_naming_settings",
    tags=["settings"],
)
def get_group_naming_settings(
    request: Request,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> GroupNamingSettingsResponse:
    return _settings_response(db, request)


@router.patch(
    "/api/settings/group-naming",
    response_model=GroupNamingSettingsResponse,
    operation_id="update_group_naming_settings",
    tags=["settings"],
)
def update_group_naming_settings(
    body: GroupNamingSettingsUpdateRequest,
    request: Request,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_admin),
) -> GroupNamingSettingsResponse:
    # null を「変更しない」ではなく不正な値として扱う(検証で 422)。先に全部を検証してから保存する。
    updates = {name: getattr(body, name) for name in body.model_fields_set}
    try:
        naming_settings.save(db, updates)
    except naming_settings.GroupNamingSettingsValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _settings_response(db, request)


# -- 名前の候補 ------------------------------------------------------------------------

_NAMES_DOC = """表示中の提案に、グループ名の候補を3つずつ付ける(ADR-0056「名前の候補」)。
保存はしない。

提案を `scope` と `granularity` で計算し直し(集めた結果はキャッシュが効く)、新しいグループの
提案(`target_group` の無いもの)のうち `suggestion_ids` に入るもの(省けば全部)を、1回にまとめて
LLM へ送る。送るのはタグだけ(特徴的なタグと出現率、作品・キャラクターの印、作品の集計)。
接続先が外部なら、センシティブなタグを外す。送る前に Mod の `ai.before_send`(目的
`group_naming`。ADR-0051)を呼ぶ。候補を返せなかった提案は `items` に入らない。

断るとき(`detail` は `{code, message}`):

- 409 `embeddings_unavailable`: 埋め込みを使えない
- 409 `disabled`: 名前の候補が無効
- 409 `unsupported`: 接続先が Structured Outputs に対応しない
- 409 `connection_missing`: 接続先・キーが無い
- 409 `hook_denied`: Mod が送信を止めた(理由は `message`)
- 502 `llm_error`: LLM の呼び出しに失敗した・応答を読めなかった
"""


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


@router.post(
    "/api/asset-groups/suggestions/names",
    response_model=GroupSuggestionNamesResponse,
    operation_id="suggest_group_names",
    description=_NAMES_DOC,
    responses={409: {"model": EmbeddingErrorDetail, "description": "候補を出せない"}},
)
async def suggest_group_names(
    body: GroupSuggestionNamesRequest,
    request: Request,
    user: CurrentUser = Depends(require_user),
) -> GroupSuggestionNamesResponse:
    state = request.app.state
    runtime: AssistantRuntime = state.assistant
    engine = runtime.structured

    def _prepare() -> tuple[
        naming_settings.GroupNamingConfig,
        LlmTarget | None,
        llm_connections.ConnectionConfig,
        bool,
        suggestions_domain.NamingInput,
    ]:
        with state.session_factory() as db:
            config = naming_settings.load(db)
            if not config.enabled:
                raise _error(409, "disabled", t("groupNaming.disabled"))
            target: LlmTarget | None = None
            if engine.requires_target:
                try:
                    target = naming_settings.resolve_target(db, config, state.settings)
                except ConnectionNotFoundError as e:
                    raise _error(409, "connection_missing", t("assistant.connectionMissing")) from e
                if not target.api_key:
                    raise _error(409, "connection_missing", t("assistant.apiKeyMissing"))
                if not naming_settings.structured_outputs_supported(db, config, state.settings):
                    raise _error(409, "unsupported", t("groupNaming.unsupported"))
            connection = llm_connections.find_connection(
                llm_connections.load(db), config.connection_id
            ) or llm_connections.ConnectionConfig(id=config.connection_id, name="", base_url=None)
            locality, _source = locality_domain.connection_locality(
                db, state.settings, builtin=connection.builtin, value=connection.locality
            )
            try:
                data = suggestions_domain.naming_input(
                    db,
                    settings=state.settings,
                    index=state.vector_index,
                    version_of=state.embedder.version,
                    cache=state.group_suggestion_cache,
                    viewer=user,
                    scope=body.scope,
                    granularity=body.granularity,
                    suggestion_ids=body.suggestion_ids,
                )
            except EmbeddingsConflictError as exc:
                raise _error(409, exc.code, exc.message) from exc
        return config, target, connection, locality == "external", data

    config, target, connection, external, data = await anyio.to_thread.run_sync(_prepare)
    clusters = data.clusters[: naming.MAX_CLUSTERS]
    if not clusters:
        return GroupSuggestionNamesResponse(model=None, items=[])
    data = suggestions_domain.NamingInput(
        clusters=clusters,
        global_counts=data.global_counts,
        total=data.total,
        categories=data.categories,
    )
    input_text = naming.build_input(data, external=external)

    # ADR-0051: 外部の AI へ送る直前のチェック(目的 `group_naming`)。接続先がローカルでも
    # 同じ入口を通す。本文はセンシティブなタグを外した後の、実際に送る文面。
    mods = getattr(state, "mods", None)
    if mods is not None and mods.wants(AI_BEFORE_SEND, purpose="group_naming"):

        def _subject() -> BeforeSendSubject:
            with state.session_factory() as db:
                base_url = (
                    target.base_url
                    if target is not None
                    else llm_connections.display_base_url(connection, state.settings)
                )
                return BeforeSendSubject(
                    purpose="group_naming",
                    target=mods_events.llm_target(
                        db, state.settings, connection, model=config.model, base_url=base_url
                    ),
                    text=[
                        TextPart(role="system", text=naming.INSTRUCTIONS),
                        TextPart(role="user", text=input_text),
                    ],
                    params={
                        "model": config.model,
                        "clusters": len(clusters),
                        "sensitive_tags_removed": external,
                        "reasoning_effort": naming.REASONING_EFFORT,
                    },
                )

        subject = await anyio.to_thread.run_sync(_subject)
        denial = await mods.before_send(subject, actor=mods_events.actor_from_user(user))
        if denial is not None:
            raise _error(409, "hook_denied", denial.message)

    try:
        result = await engine.complete(
            StructuredRequest(
                target=target,
                instructions=naming.INSTRUCTIONS,
                input_text=input_text,
                schema_name=naming.SCHEMA_NAME,
                schema=naming.RESPONSE_SCHEMA,
                reasoning_effort=naming.REASONING_EFFORT,
                fake_response=naming.fake_response(len(clusters)),
            )
        )
    except AssistantEngineError as e:
        raise _error(502, "llm_error", t("groupNaming.llmError", error=str(e))) from e

    by_number = naming.interpret(result.data, len(clusters))
    return GroupSuggestionNamesResponse(
        model=result.model or config.model,
        items=[
            GroupSuggestionNames(suggestion_id=cluster.suggestion_id, candidates=by_number[i])
            for i, cluster in enumerate(clusters, start=1)
            if i in by_number
        ],
    )
