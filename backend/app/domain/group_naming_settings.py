"""グループ名の候補の管理者設定(ADR-0056「名前の候補」)。

値は項目ごとに `app_setting` の `group_naming.<項目名>` に保存する(`region_detection_settings` と
同じ形式 `{"value": ...}`。画面で保存した値だけ。環境変数の既定値は持たない)。壊れた値は既定値と
して扱う。

- `enabled`: 有効・無効(既定は無効。無効の間は提案のページに名前の候補を出さない)。
- `connection_id` / `model`: LLM の接続先(ADR-0032)とモデル名。使っている接続先は
  `llm_connections.register_usage` で答える(使っている接続先は削除できない)。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from app.domain import llm_connections, llm_defaults
from app.domain.general_settings import GeneralSettingsValidationError, _get_raw_value, _save
from app.domain.llm_connections import BUILTIN_CONNECTION_ID, LlmTarget
from app.i18n import t

if TYPE_CHECKING:
    from app.config import Settings

_KEY_PREFIX = "group_naming."

FEATURE_ID = "group_naming"

MODEL_NAME_MAX = llm_defaults.MODEL_NAME_MAX


class GroupNamingSettingsValidationError(GeneralSettingsValidationError):
    """保存しようとした値が不正(API 層で 422 にする)。"""


@dataclass(frozen=True)
class GroupNamingConfig:
    enabled: bool = False
    # 実際に使う接続先とモデル名(上書き、無ければ区分に合う既定の送り先。ADR-0063)。
    connection_id: str = BUILTIN_CONNECTION_ID
    model: str = llm_defaults.BUILTIN_DEFAULT_MODEL
    # 機能ごとの上書き(None は既定の送り先を使う)。画面にはこちらを返す。
    override: llm_defaults.TargetChoice | None = None


FIELDS: tuple[str, ...] = ("enabled", "connection_id", "model")
_DEFAULTS = GroupNamingConfig()


def normalize_value(db: Session, name: str, value: Any, *, check_connection: bool = True) -> Any:
    """1項目を検証して保存する形に整える。不正なら GroupNamingSettingsValidationError。
    `check_connection` が真なら、接続先が登録されているかも確かめる。"""
    if name == "enabled":
        if not isinstance(value, bool):
            raise GroupNamingSettingsValidationError(
                t("settings.groupNaming.invalidBool", name=name)
            )
        return value
    if name in ("connection_id", "model") and value is None:
        # 上書きを消す(既定の送り先を使う。ADR-0063)。
        return None
    if name == "connection_id":
        if not isinstance(value, str) or not value:
            raise GroupNamingSettingsValidationError(t("settings.groupNaming.unknownConnection"))
        if (
            check_connection
            and llm_connections.find_connection(llm_connections.load(db), value) is None
        ):
            raise GroupNamingSettingsValidationError(t("settings.groupNaming.unknownConnection"))
        return value
    if name == "model":
        if not isinstance(value, str) or not 0 < len(value.strip()) <= MODEL_NAME_MAX:
            raise GroupNamingSettingsValidationError(
                t("settings.groupNaming.invalidModel", max=MODEL_NAME_MAX)
            )
        return value.strip()
    raise KeyError(name)


def load(db: Session, locality: str = llm_defaults.EXTERNAL) -> GroupNamingConfig:
    """保存された設定を読む。無い・壊れている項目は既定値。接続先とモデル名は、上書きが無ければ
    `locality`(対象の画像の区分)に合う既定の送り先(ADR-0063)。"""
    values: dict[str, Any] = {}
    for name in FIELDS:
        if name in ("connection_id", "model"):
            continue
        raw = _get_raw_value(db, _KEY_PREFIX + name)
        if raw is None:
            continue
        try:
            # 接続先は保存時に確かめている(使っている接続先は削除できない)。
            values[name] = normalize_value(db, name, raw, check_connection=False)
        except GroupNamingSettingsValidationError:
            continue
    override = llm_defaults.load_override(db, _KEY_PREFIX)
    choice = llm_defaults.effective_choice(db, override, locality)
    return replace(
        _DEFAULTS,
        **values,
        connection_id=choice.connection_id,
        model=choice.model,
        override=override,
    )


def save(db: Session, updates: dict[str, Any]) -> None:
    """全項目を検証してからまとめて保存する(1項目だけ保存される事態を避ける)。"""
    validated = {name: normalize_value(db, name, value) for name, value in updates.items()}
    for name, value in validated.items():
        if name not in ("connection_id", "model"):
            _save(db, _KEY_PREFIX + name, value)
    llm_defaults.save_override(db, _KEY_PREFIX, validated)


def used_connection_ids(db: Session) -> set[str]:
    """名前の候補が使う接続先(ADR-0032 3章)。上書きに選んで保存した接続先は、
    有効かどうかに関わらず数える(削除できなくなる)。既定の送り先の接続先は、既定の送り先の側が数える。"""
    override = llm_defaults.load_override(db, _KEY_PREFIX)
    return {override.connection_id} if override is not None else set()


llm_connections.register_usage(FEATURE_ID, used_connection_ids)


def resolve_target(db: Session, config: GroupNamingConfig, settings: Settings) -> LlmTarget:
    """送り先を決める。接続先が無ければ ConnectionNotFoundError。"""
    return llm_connections.resolve_target(
        llm_connections.load(db), config.connection_id, config.model, settings
    )


def structured_outputs_supported(
    db: Session, config: GroupNamingConfig, settings: Settings
) -> bool:
    """接続先が Structured Outputs に対応するとみなせるか(LLM の接続先の設定に従う)。"""
    return llm_connections.connection_supports_structured_outputs(
        db, config.connection_id, settings
    )
