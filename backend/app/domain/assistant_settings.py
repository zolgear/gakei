"""生成のアシスタントの管理者設定(ADR-0048 7章・9章)。

値は項目ごとに `app_setting` の `assistant.<項目名>` に保存する(`annotation_settings` と同じ
形式 `{"value": ...}`。画面で保存した値だけ。環境変数の既定値は持たない)。壊れた値・範囲外の
値は既定値として扱う。

- `enabled`: 有効・無効(既定は無効)。
- `connection_id` / `model`: LLM の接続先(ADR-0032)とモデル名。使っている接続先は
  `llm_connections.register_usage` で答える(使っている接続先は削除できない)。
- `hourly_request_limit`: LLM のリクエストの1時間の上限(利用者ごと。プロセス内で数え、
  再起動で 0 に戻る。ADR-0024 8章と同じ)。
- `runs_per_message_limit`: 1回の発言で作れる Run の数(9章。既定 20)。
- `hourly_run_limit`: 1時間あたりにアシスタントが作れる Run の数(利用者ごと。既定 60)。
  DB で `run.origin = 'assistant'` の Run を数える(MCP の上限と同じ数え方。ADR-0023 5章)。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain import llm_connections
from app.domain.general_settings import GeneralSettingsValidationError, _get_raw_value, _save
from app.domain.llm_connections import BUILTIN_CONNECTION_ID, LlmTarget
from app.domain.locality import host_of
from app.domain.models import RUN_ORIGIN_ASSISTANT, Run
from app.i18n import t

if TYPE_CHECKING:
    from app.config import Settings

_KEY_PREFIX = "assistant."

FEATURE_ID = "assistant"

DEFAULT_MODEL = "gpt-5.6-luna"
MODEL_NAME_MAX = 200

DEFAULT_HOURLY_REQUEST_LIMIT = 200
HOURLY_REQUEST_LIMIT_MIN = 1
HOURLY_REQUEST_LIMIT_MAX = 10000

DEFAULT_RUNS_PER_MESSAGE_LIMIT = 20
RUNS_PER_MESSAGE_LIMIT_MIN = 0
RUNS_PER_MESSAGE_LIMIT_MAX = 200

DEFAULT_HOURLY_RUN_LIMIT = 60
HOURLY_RUN_LIMIT_MIN = 0
HOURLY_RUN_LIMIT_MAX = 1000


class AssistantSettingsValidationError(GeneralSettingsValidationError):
    """保存しようとした値が不正(API 層で 422 にする)。"""


@dataclass(frozen=True)
class AssistantConfig:
    enabled: bool = False
    connection_id: str = BUILTIN_CONNECTION_ID
    model: str = DEFAULT_MODEL
    hourly_request_limit: int = DEFAULT_HOURLY_REQUEST_LIMIT
    runs_per_message_limit: int = DEFAULT_RUNS_PER_MESSAGE_LIMIT
    hourly_run_limit: int = DEFAULT_HOURLY_RUN_LIMIT


FIELDS: tuple[str, ...] = (
    "enabled",
    "connection_id",
    "model",
    "hourly_request_limit",
    "runs_per_message_limit",
    "hourly_run_limit",
)
_DEFAULTS = AssistantConfig()

_INT_RANGES: dict[str, tuple[int, int]] = {
    "hourly_request_limit": (HOURLY_REQUEST_LIMIT_MIN, HOURLY_REQUEST_LIMIT_MAX),
    "runs_per_message_limit": (RUNS_PER_MESSAGE_LIMIT_MIN, RUNS_PER_MESSAGE_LIMIT_MAX),
    "hourly_run_limit": (HOURLY_RUN_LIMIT_MIN, HOURLY_RUN_LIMIT_MAX),
}


# -- 検証 --------------------------------------------------------------------


def _is_int_in(value: object, low: int, high: int) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and low <= value <= high


def normalize_value(db: Session, name: str, value: Any, *, check_connection: bool = True) -> Any:
    """1項目を検証して保存する形に整える。不正なら AssistantSettingsValidationError。
    `check_connection` が真なら、接続先が登録されているかも確かめる。"""
    if name == "enabled":
        if not isinstance(value, bool):
            raise AssistantSettingsValidationError(t("settings.assistant.invalidBool", name=name))
        return value
    if name == "connection_id":
        if not isinstance(value, str) or not value:
            raise AssistantSettingsValidationError(t("settings.assistant.unknownConnection"))
        if (
            check_connection
            and llm_connections.find_connection(llm_connections.load(db), value) is None
        ):
            raise AssistantSettingsValidationError(t("settings.assistant.unknownConnection"))
        return value
    if name == "model":
        if not isinstance(value, str) or not 0 < len(value.strip()) <= MODEL_NAME_MAX:
            raise AssistantSettingsValidationError(
                t("settings.assistant.invalidModel", max=MODEL_NAME_MAX)
            )
        return value.strip()
    if name in _INT_RANGES:
        low, high = _INT_RANGES[name]
        if not _is_int_in(value, low, high):
            raise AssistantSettingsValidationError(
                t("settings.assistant.invalidLimit", name=name, min=low, max=high)
            )
        return value
    raise KeyError(name)


# -- 読み書き ------------------------------------------------------------------


def load(db: Session) -> AssistantConfig:
    """保存された設定を読む。無い・壊れている項目は既定値。"""
    values: dict[str, Any] = {}
    for name in FIELDS:
        raw = _get_raw_value(db, _KEY_PREFIX + name)
        if raw is None:
            continue
        try:
            # 接続先は保存時に確かめている(使っている接続先は削除できない)。
            values[name] = normalize_value(db, name, raw, check_connection=False)
        except AssistantSettingsValidationError:
            continue
    return replace(_DEFAULTS, **values)


def validate(db: Session, updates: dict[str, Any]) -> dict[str, Any]:
    """全項目を検証して、保存する形の値を返す(保存はしない)。"""
    return {name: normalize_value(db, name, value) for name, value in updates.items()}


def save(db: Session, updates: dict[str, Any]) -> None:
    """検証してからまとめて保存する(1項目だけ保存される事態を避ける)。"""
    for name, value in validate(db, updates).items():
        _save(db, _KEY_PREFIX + name, value)


def used_connection_ids(db: Session) -> set[str]:
    """アシスタントが使う接続先(ADR-0032 3章)。選んで保存した接続先は、有効かどうかに関わらず
    数える(削除できなくなる)。選んでいなければ、有効なときだけ既定(組み込みの接続先)を数える。"""
    raw = _get_raw_value(db, _KEY_PREFIX + "connection_id")
    if isinstance(raw, str) and raw:
        return {raw}
    if _get_raw_value(db, _KEY_PREFIX + "enabled") is True:
        return {BUILTIN_CONNECTION_ID}
    return set()


llm_connections.register_usage(FEATURE_ID, used_connection_ids)


def resolve_target(db: Session, config: AssistantConfig, settings: Settings) -> LlmTarget:
    """送り先を決める。接続先が無ければ ConnectionNotFoundError。"""
    return llm_connections.resolve_target(
        llm_connections.load(db), config.connection_id, config.model, settings
    )


# -- 生成の上限(9章) ------------------------------------------------------------


def count_recent_runs(db: Session, user_id: uuid.UUID | None, now: datetime | None = None) -> int:
    """直近1時間にアシスタントが `user_id` のために作った Run の件数(削除済みも数える。上限は
    課金を抑えるためのもの)。個人モード(`user_id` が None)は作成者が null の Run を数える。"""
    now = now or datetime.now(UTC)
    since = now - timedelta(hours=1)
    owner = (
        Run.created_by_user_id.is_(None) if user_id is None else Run.created_by_user_id == user_id
    )
    return db.execute(
        select(func.count())
        .select_from(Run)
        .where(Run.origin == RUN_ORIGIN_ASSISTANT, owner, Run.queued_at >= since)
    ).scalar_one()


# -- Structured Outputs(ADR-0052 6章) ---------------------------------------------------

# Structured Outputs(JSON Schema の strict)に対応するとみなす接続先のホスト。GAKEI は接続先に
# 対応を問い合わせないので、確かな OpenAI の公式だけにする。
STRUCTURED_OUTPUTS_HOSTS = frozenset({"api.openai.com"})


def structured_outputs_supported(db: Session, config: AssistantConfig, settings: Settings) -> bool:
    """アシスタントの接続先が Structured Outputs に対応するとみなせるか(ComfyUI のワークフローの
    推定に使えるか)。"""
    return connection_supports_structured_outputs(db, config.connection_id, settings)


def connection_supports_structured_outputs(
    db: Session, connection_id: str, settings: Settings
) -> bool:
    """接続先が Structured Outputs に対応するとみなせるか。組み込みの接続先で Base URL を
    変えていなければ OpenAI の公式。領域の四角の検出(ADR-0053 7章)も使う。"""
    connection = llm_connections.find_connection(llm_connections.load(db), connection_id)
    if connection is None:
        return False
    base_url = llm_connections.display_base_url(connection, settings)
    if base_url is None:
        return connection.builtin
    return host_of(base_url) in STRUCTURED_OUTPUTS_HOSTS
