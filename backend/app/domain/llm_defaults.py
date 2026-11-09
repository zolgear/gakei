"""LLM・VLM の既定の送り先(ADR-0063)。

外部(`external`)と内部(`local`)の2組の「接続先 + モデル名」を1か所で決める。各機能は、
機能ごとの上書きが無ければ、対象の画像の区分(ADR-0051 3章)に合う組を使う。

- 保存先は `app_setting` の `llm_defaults.external` / `llm_defaults.local`
  (`{"connection_id": ..., "model": ...}`)。
- 外部の組を保存していなければ、組み込みの接続先(OpenAI の設定)と `BUILTIN_DEFAULT_MODEL`。
- 内部の組を保存していなければ、外部の組を使う。
- 画像の区分は、その Asset を作った Run の区分(`ip_usage.run_locality`)。Run の無い Asset
  (アップロード、スケッチ、合成)は外部。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import ColumnElement, and_, false, or_, select
from sqlalchemy.orm import Session

from app.domain import llm_connections
from app.domain import locality as locality_domain
from app.domain.general_settings import (
    GeneralSettingsValidationError,
    _delete,
    _get_raw_value,
    _save,
)
from app.domain.llm_connections import BUILTIN_CONNECTION_ID
from app.domain.models import Asset, ComfyWorkflow, Run
from app.i18n import t

if TYPE_CHECKING:
    from app.config import Settings

_KEY_PREFIX = "llm_defaults."

FEATURE_ID = "llm_defaults"

# LLM・VLM のモデル名の組み込みの既定は、この1つだけに置く(ADR-0063)。
BUILTIN_DEFAULT_MODEL = "gpt-6-luna"
MODEL_NAME_MAX = 200

EXTERNAL = "external"
LOCAL = "local"
LOCALITIES: tuple[str, ...] = (EXTERNAL, LOCAL)


class LlmDefaultsValidationError(GeneralSettingsValidationError):
    """保存しようとした値が不正(API 層で 422 にする)。"""


@dataclass(frozen=True)
class TargetChoice:
    """「接続先 + モデル名」の組。"""

    connection_id: str
    model: str


BUILTIN_CHOICE = TargetChoice(BUILTIN_CONNECTION_ID, BUILTIN_DEFAULT_MODEL)


@dataclass(frozen=True)
class LlmDefaults:
    """既定の送り先。`local` が None なら外部の組を使う。"""

    external: TargetChoice = BUILTIN_CHOICE
    local: TargetChoice | None = None
    # 外部の組を画面で保存したか(画面に「組み込みの既定」と出すため)。
    external_saved: bool = False

    def choice_for(self, locality: str) -> TargetChoice:
        if locality == LOCAL and self.local is not None:
            return self.local
        return self.external


# -- 検証 --------------------------------------------------------------------


def is_model_name(value: object) -> bool:
    return isinstance(value, str) and 0 < len(value.strip()) <= MODEL_NAME_MAX


def normalize_model(value: object) -> str:
    if not is_model_name(value):
        raise LlmDefaultsValidationError(t("settings.llmDefaults.invalidModel", max=MODEL_NAME_MAX))
    assert isinstance(value, str)
    return value.strip()


def parse_choice(value: object) -> TargetChoice:
    """`{"connection_id": ..., "model": ...}` を読む(接続先があるかは確かめない)。"""
    if not isinstance(value, dict):
        raise LlmDefaultsValidationError(t("settings.llmDefaults.invalidTarget"))
    connection_id = value.get("connection_id")
    if not isinstance(connection_id, str) or not connection_id:
        raise LlmDefaultsValidationError(t("settings.llmDefaults.invalidTarget"))
    return TargetChoice(connection_id=connection_id, model=normalize_model(value.get("model")))


def choice_to_json(value: TargetChoice) -> dict[str, str]:
    return {"connection_id": value.connection_id, "model": value.model}


def require_connection(db: Session, connection_id: str) -> None:
    if llm_connections.find_connection(llm_connections.load(db), connection_id) is None:
        raise LlmDefaultsValidationError(t("settings.llmDefaults.unknownConnection"))


# -- 読み書き ------------------------------------------------------------------


def _read(db: Session, locality: str) -> TargetChoice | None:
    raw = _get_raw_value(db, _KEY_PREFIX + locality)
    if raw is None:
        return None
    try:
        return parse_choice(raw)
    except LlmDefaultsValidationError:
        return None


def load(db: Session) -> LlmDefaults:
    external = _read(db, EXTERNAL)
    return LlmDefaults(
        external=external or BUILTIN_CHOICE,
        local=_read(db, LOCAL),
        external_saved=external is not None,
    )


def save(db: Session, updates: dict[str, Any]) -> None:
    """`external` / `local` を保存する(書いた項目だけ)。None は保存した値を消す(外部は組み込みの
    既定、内部は外部と同じに戻す)。先に全部を検証する。"""
    parsed: dict[str, TargetChoice | None] = {}
    for locality, value in updates.items():
        if locality not in LOCALITIES:
            raise KeyError(locality)
        if value is None:
            parsed[locality] = None
            continue
        choice = parse_choice(value)
        require_connection(db, choice.connection_id)
        parsed[locality] = choice
    for locality, choice in parsed.items():
        if choice is None:
            _delete(db, _KEY_PREFIX + locality)
        else:
            _save(db, _KEY_PREFIX + locality, choice_to_json(choice))


def used_connection_ids(db: Session) -> set[str]:
    """既定の送り先の接続先(削除させない。ADR-0032 3章)。外部は保存していなければ組み込みの
    接続先、内部は保存したときだけ数える。"""
    defaults = load(db)
    ids = {defaults.external.connection_id}
    if defaults.local is not None:
        ids.add(defaults.local.connection_id)
    return ids


llm_connections.register_usage(FEATURE_ID, used_connection_ids)


def svg_author_default(db: Session) -> str:
    """SVG を書くモデルの既定(ADR-0062 2章、ADR-0063 3章)。書き手は OpenAI の画像の API が
    呼ぶので、外部の組の接続先が組み込みの接続先のときだけ、そのモデル名を使う。"""
    external = load(db).external
    if external.connection_id == BUILTIN_CONNECTION_ID:
        return external.model
    return BUILTIN_DEFAULT_MODEL


# -- 画像の区分 ----------------------------------------------------------------


def run_locality(db: Session, settings: Settings | None, run: Run | None) -> str:
    """Run の区分(ADR-0051 3章)。Run が無ければ外部。"""
    if run is None:
        return EXTERNAL
    # ip_usage は llm_defaults を使う機能から読み込まれるので、ここで読み込む。
    from app.domain import ip_usage

    return ip_usage.run_locality(db, settings, run.provider, run.model)


def asset_locality(db: Session, settings: Settings | None, asset: Asset | None) -> str:
    """Asset の区分。その Asset を作った Run の区分で、Run の無い Asset は外部。"""
    if asset is None or asset.produced_by_run_id is None:
        return EXTERNAL
    return run_locality(db, settings, db.get(Run, asset.produced_by_run_id))


def assets_locality(db: Session, settings: Settings | None, asset_ids: Iterable[uuid.UUID]) -> str:
    """複数の Asset の区分。内部の画像が1枚でもあれば内部。"""
    ids = list(asset_ids)
    if not ids:
        return EXTERNAL
    runs = db.execute(
        select(Run)
        .join(Asset, Asset.produced_by_run_id == Run.id)
        .where(Asset.id.in_(ids))
        .distinct()
    ).scalars()
    for run in runs:
        if run_locality(db, settings, run) == LOCAL:
            return LOCAL
    return EXTERNAL


def local_run_condition(db: Session, settings: Settings | None) -> ColumnElement[bool]:
    """`Run` が内部の区分である、という SQL の条件(`run_locality` と同じ決め方)。待ち行列から
    区分ごとに行を選ぶのに使う。"""
    conditions: list[ColumnElement[bool]] = []
    for provider in locality_domain.PROVIDERS:
        connection = locality_domain.provider_locality(db, settings, provider)[0]
        if provider != "comfyui":
            if connection == LOCAL:
                conditions.append(Run.provider == provider)
            continue
        # ComfyUI はワークフローの値を接続の値より優先する。
        overriding = [
            str(row.id)
            for row in db.execute(select(ComfyWorkflow)).scalars()
            if locality_domain.read_workflow(row.locality)
            not in (locality_domain.UNSET, connection)
        ]
        is_comfyui = Run.provider == provider
        if connection == LOCAL:
            conditions.append(and_(is_comfyui, Run.model.not_in(overriding)))
        elif overriding:
            conditions.append(and_(is_comfyui, Run.model.in_(overriding)))
    return or_(*conditions) if conditions else false()


# -- 機能ごとの上書き ----------------------------------------------------------------
# 生成のアシスタント・領域の検出・グループの名前の提案は、`<接頭辞>connection_id` と
# `<接頭辞>model` に上書きを保存する(どちらかが null なら上書き無し。ADR-0063 3章)。


def load_override(db: Session, key_prefix: str) -> TargetChoice | None:
    """機能ごとの上書き。片方だけ保存されていれば(ADR-0063 より前に保存した値)、もう片方は
    組み込みの既定で埋める(以前の既定と同じ)。"""
    connection_id = _get_raw_value(db, key_prefix + "connection_id")
    model = _get_raw_value(db, key_prefix + "model")
    has_connection = isinstance(connection_id, str) and bool(connection_id)
    has_model = is_model_name(model)
    if not has_connection and not has_model:
        return None
    return TargetChoice(
        connection_id=connection_id if has_connection else BUILTIN_CONNECTION_ID,  # type: ignore[arg-type]
        model=str(model).strip() if has_model else BUILTIN_DEFAULT_MODEL,
    )


def save_override(db: Session, key_prefix: str, updates: dict[str, Any]) -> None:
    """`connection_id` / `model` の更新を保存する。どちらかが None なら上書きを消す(既定の送り先に
    戻す)。値は呼び出し側で検証しておく。"""
    if "connection_id" not in updates and "model" not in updates:
        return
    if updates.get("connection_id", "") is None or updates.get("model", "") is None:
        _delete(db, key_prefix + "connection_id")
        _delete(db, key_prefix + "model")
        return
    for name in ("connection_id", "model"):
        if name in updates:
            _save(db, key_prefix + name, updates[name])


def effective_choice(db: Session, override: TargetChoice | None, locality: str) -> TargetChoice:
    """実際に使う送り先。上書きが無ければ、区分に合う既定の送り先。"""
    return override or load(db).choice_for(locality)
