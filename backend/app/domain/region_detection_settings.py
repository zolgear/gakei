"""領域の四角の検出の管理者設定(ADR-0053 7章)。

値は項目ごとに `app_setting` の `region_detection.<項目名>` に保存する(`assistant_settings` と
同じ形式 `{"value": ...}`。画面で保存した値だけ。環境変数の既定値は持たない)。壊れた値・範囲外の
値は既定値として扱う。

- `enabled`: 有効・無効(既定は無効。無効の間は検出のボタンを出さない)。
- `connection_id` / `model`: LLM の接続先(ADR-0032)とモデル名。画像を受けられるモデルを選ぶ。
  使っている接続先は `llm_connections.register_usage` で答える(使っている接続先は削除できない)。
- `hourly_request_limit`: 検出の1時間の上限(利用者ごと。プロセス内で数え、再起動で 0 に戻る。
  ADR-0024 8章と同じ)。アシスタントの会話とは別に数える。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from app.domain import llm_connections
from app.domain.general_settings import GeneralSettingsValidationError, _get_raw_value, _save
from app.domain.llm_connections import BUILTIN_CONNECTION_ID, LlmTarget
from app.i18n import t

if TYPE_CHECKING:
    from app.config import Settings

_KEY_PREFIX = "region_detection."

FEATURE_ID = "region_detection"

# 2026-10-20 に検出を確かめたモデル(ADR-0053 Context)。
DEFAULT_MODEL = "gpt-5.6-luna"
MODEL_NAME_MAX = 200

DEFAULT_HOURLY_REQUEST_LIMIT = 100
HOURLY_REQUEST_LIMIT_MIN = 1
HOURLY_REQUEST_LIMIT_MAX = 10000


class RegionDetectionSettingsValidationError(GeneralSettingsValidationError):
    """保存しようとした値が不正(API 層で 422 にする)。"""


@dataclass(frozen=True)
class RegionDetectionConfig:
    enabled: bool = False
    connection_id: str = BUILTIN_CONNECTION_ID
    model: str = DEFAULT_MODEL
    hourly_request_limit: int = DEFAULT_HOURLY_REQUEST_LIMIT


FIELDS: tuple[str, ...] = ("enabled", "connection_id", "model", "hourly_request_limit")
_DEFAULTS = RegionDetectionConfig()


def normalize_value(db: Session, name: str, value: Any, *, check_connection: bool = True) -> Any:
    """1項目を検証して保存する形に整える。不正なら RegionDetectionSettingsValidationError。
    `check_connection` が真なら、接続先が登録されているかも確かめる。"""
    if name == "enabled":
        if not isinstance(value, bool):
            raise RegionDetectionSettingsValidationError(
                t("settings.regionDetection.invalidBool", name=name)
            )
        return value
    if name == "connection_id":
        if not isinstance(value, str) or not value:
            raise RegionDetectionSettingsValidationError(
                t("settings.regionDetection.unknownConnection")
            )
        if (
            check_connection
            and llm_connections.find_connection(llm_connections.load(db), value) is None
        ):
            raise RegionDetectionSettingsValidationError(
                t("settings.regionDetection.unknownConnection")
            )
        return value
    if name == "model":
        if not isinstance(value, str) or not 0 < len(value.strip()) <= MODEL_NAME_MAX:
            raise RegionDetectionSettingsValidationError(
                t("settings.regionDetection.invalidModel", max=MODEL_NAME_MAX)
            )
        return value.strip()
    if name == "hourly_request_limit":
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not HOURLY_REQUEST_LIMIT_MIN <= value <= HOURLY_REQUEST_LIMIT_MAX
        ):
            raise RegionDetectionSettingsValidationError(
                t(
                    "settings.regionDetection.invalidLimit",
                    name=name,
                    min=HOURLY_REQUEST_LIMIT_MIN,
                    max=HOURLY_REQUEST_LIMIT_MAX,
                )
            )
        return value
    raise KeyError(name)


def load(db: Session) -> RegionDetectionConfig:
    """保存された設定を読む。無い・壊れている項目は既定値。"""
    values: dict[str, Any] = {}
    for name in FIELDS:
        raw = _get_raw_value(db, _KEY_PREFIX + name)
        if raw is None:
            continue
        try:
            # 接続先は保存時に確かめている(使っている接続先は削除できない)。
            values[name] = normalize_value(db, name, raw, check_connection=False)
        except RegionDetectionSettingsValidationError:
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
    """検出が使う接続先(ADR-0032 3章)。選んで保存した接続先は、有効かどうかに関わらず数える
    (削除できなくなる)。選んでいなければ、有効なときだけ既定(組み込みの接続先)を数える。"""
    raw = _get_raw_value(db, _KEY_PREFIX + "connection_id")
    if isinstance(raw, str) and raw:
        return {raw}
    if _get_raw_value(db, _KEY_PREFIX + "enabled") is True:
        return {BUILTIN_CONNECTION_ID}
    return set()


llm_connections.register_usage(FEATURE_ID, used_connection_ids)


def resolve_target(db: Session, config: RegionDetectionConfig, settings: Settings) -> LlmTarget:
    """送り先を決める。接続先が無ければ ConnectionNotFoundError。"""
    return llm_connections.resolve_target(
        llm_connections.load(db), config.connection_id, config.model, settings
    )


def structured_outputs_supported(
    db: Session, config: RegionDetectionConfig, settings: Settings
) -> bool:
    """接続先が Structured Outputs に対応するとみなせるか(ADR-0053 7章。判定は LLM の接続先の
    設定。`auto` なら OpenAI の公式だけ。手元の VLM の緩い読み取りは後の段)。"""
    return llm_connections.connection_supports_structured_outputs(
        db, config.connection_id, settings
    )
