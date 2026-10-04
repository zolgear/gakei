"""LLM・VLM の接続先(ADR-0032。中身は ADR-0024 8章「接続先(一覧)」)。

GET は全ログイン者(今の `GET /api/settings/annotation` と同じ)、追加・変更・削除とキーは
管理者だけ。キーは一部も返さず、設定済みかどうかだけを返す(ADR-0019 5章)。

どの機能が接続先を使っているか(`used_by`)は、使う側が `llm_connections.register_usage` で登録した
関数が答える。登録は使う側のモジュールを読み込んだときに行われるので、ここで読み込んでおく。
"""

from __future__ import annotations

from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.deps import require_admin, require_user
from app.auth.identity import CurrentUser
from app.config import Settings
from app.deps import get_annotator, get_session, get_settings

# annotation_settings と embedding_settings は、読み込むと使っている機能を登録する(上の docstring)。
from app.domain import annotation_settings, embedding_settings, llm_connections
from app.domain.schemas import (
    LlmConnectionApiKeyUpdateRequest,
    LlmConnectionCreateRequest,
    LlmConnectionsResponse,
    LlmConnectionUpdateRequest,
    LlmConnectionView,
)
from app.i18n import t
from app.worker.annotator import Annotator

router = APIRouter(prefix="/api/settings/llm-connections", tags=["settings"])


def _response(db: Session, settings: Settings) -> LlmConnectionsResponse:
    used_by = llm_connections.features_using(db)
    views: list[LlmConnectionView] = []
    for connection in llm_connections.load(db):
        views.append(
            LlmConnectionView(
                id=connection.id,
                name=(
                    t("settings.llmConnections.builtinName")
                    if connection.builtin
                    else connection.name
                ),
                builtin=connection.builtin,
                base_url=llm_connections.display_base_url(connection, settings),
                api_style=connection.api_style,
                api_key_set=llm_connections.connection_key_set(connection, settings),
                used_by=used_by.get(connection.id, []),  # type: ignore[arg-type]
            )
        )
    return LlmConnectionsResponse(connections=views)


def _feature_name(feature: str) -> str:
    # `t()` のキーはリテラルで書く(tests/test_i18n.py)。機能を足したらここにも足す。
    if feature == annotation_settings.FEATURE_ID:
        return t("settings.llmConnections.features.annotation")
    if feature == embedding_settings.FEATURE_ID:
        return t("settings.llmConnections.features.embedding")
    return feature


def _feature_names(features: list[str]) -> str:
    return t("settings.llmConnections.featureSeparator").join(
        _feature_name(feature) for feature in features
    )


def _raise_connection_error(exc: Exception) -> NoReturn:
    if isinstance(exc, llm_connections.LlmConnectionValidationError):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if isinstance(exc, llm_connections.ConnectionNotFoundError):
        raise HTTPException(status_code=404, detail=t("settings.llmConnections.notFound")) from exc
    if isinstance(exc, llm_connections.ConnectionReservedError):
        raise HTTPException(
            status_code=409, detail=t("settings.llmConnections.builtinReadOnly")
        ) from exc
    if isinstance(exc, llm_connections.ConnectionInUseError):
        raise HTTPException(
            status_code=409,
            detail=t("settings.llmConnections.inUse", features=_feature_names(exc.features)),
        ) from exc
    raise exc


_CONNECTION_ERRORS = (
    llm_connections.LlmConnectionValidationError,
    llm_connections.ConnectionNotFoundError,
    llm_connections.ConnectionReservedError,
    llm_connections.ConnectionInUseError,
)


@router.get("", response_model=LlmConnectionsResponse, operation_id="list_llm_connections")
def list_llm_connections(
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    _user: CurrentUser = Depends(require_user),
) -> LlmConnectionsResponse:
    """接続先の一覧。先頭は組み込みの「OpenAI の設定」。"""
    return _response(db, settings)


@router.post(
    "",
    response_model=LlmConnectionsResponse,
    status_code=201,
    operation_id="create_llm_connection",
)
def create_llm_connection(
    body: LlmConnectionCreateRequest,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    _user: CurrentUser = Depends(require_admin),
) -> LlmConnectionsResponse:
    """接続先を足す。足した接続先は一覧の末尾に入る。キーは任意(ローカルのサーバー向けに任意の
    文字列を受け付けるため、有効性の確認はしない)。"""
    try:
        connection_id = llm_connections.add_connection(db, body.name, body.base_url, body.api_style)
    except _CONNECTION_ERRORS as exc:
        _raise_connection_error(exc)
    key = (body.api_key or "").strip()
    if key:
        llm_connections.write_connection_key(settings.data_dir, connection_id, key)
    return _response(db, settings)


@router.patch(
    "/{connection_id}",
    response_model=LlmConnectionsResponse,
    operation_id="update_llm_connection",
)
def update_llm_connection(
    connection_id: str,
    body: LlmConnectionUpdateRequest,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    annotator: Annotator = Depends(get_annotator),
    _user: CurrentUser = Depends(require_admin),
) -> LlmConnectionsResponse:
    updates = {name: getattr(body, name) for name in body.model_fields_set}
    try:
        llm_connections.update_connection(db, connection_id, updates)
    except _CONNECTION_ERRORS as exc:
        _raise_connection_error(exc)
    # 送り先が変わったので、待ち行列を持つ worker を起こす(上限で止まっていた行を見直させる)。
    annotator.notify()
    return _response(db, settings)


@router.delete(
    "/{connection_id}",
    response_model=LlmConnectionsResponse,
    operation_id="delete_llm_connection",
)
def delete_llm_connection(
    connection_id: str,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    _user: CurrentUser = Depends(require_admin),
) -> LlmConnectionsResponse:
    """接続先を消す(キーも消す)。組み込みの接続先と、どこかの機能で使っている接続先は 409。"""
    try:
        llm_connections.delete_connection(db, settings.data_dir, connection_id)
    except _CONNECTION_ERRORS as exc:
        _raise_connection_error(exc)
    return _response(db, settings)


@router.put(
    "/{connection_id}/api-key",
    response_model=LlmConnectionsResponse,
    operation_id="set_llm_connection_api_key",
)
def set_llm_connection_api_key(
    connection_id: str,
    body: LlmConnectionApiKeyUpdateRequest,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    _user: CurrentUser = Depends(require_admin),
) -> LlmConnectionsResponse:
    """接続先のキーを保存する(`secrets.json`。値は返さない)。有効性の確認はしない。組み込みの
    接続先のキーは OpenAI の設定で変える(409)。"""
    try:
        llm_connections.require_user_connection(db, connection_id)
    except _CONNECTION_ERRORS as exc:
        _raise_connection_error(exc)
    value = body.api_key.strip()
    if not value:
        raise HTTPException(status_code=400, detail=t("openai.keyEmpty"))
    llm_connections.write_connection_key(settings.data_dir, connection_id, value)
    return _response(db, settings)


@router.delete(
    "/{connection_id}/api-key",
    response_model=LlmConnectionsResponse,
    operation_id="delete_llm_connection_api_key",
)
def delete_llm_connection_api_key(
    connection_id: str,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    _user: CurrentUser = Depends(require_admin),
) -> LlmConnectionsResponse:
    try:
        llm_connections.require_user_connection(db, connection_id)
    except _CONNECTION_ERRORS as exc:
        _raise_connection_error(exc)
    llm_connections.delete_connection_key(settings.data_dir, connection_id)
    return _response(db, settings)
