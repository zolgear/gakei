"""Generate の moderation と ComfyUI のタイムアウトを設定画面から変える(ADR-0009、ADR-0013 7章)。

優先順位は ComfyUI の接続先(`app/domain/comfyui_connection.py`)と同じ3段階。

1. DB の `app_setting` に画面で保存した値があれば、常にそれが勝つ。
2. 保存された値が無ければ、環境変数(`MODERATION` / `COMFYUI_TIMEOUT_SECONDS`。既定値としてだけ
   起動時に読む)。
3. どちらも無ければ組み込みの既定値(`low` / 1800秒)。

環境変数の値は DB に書き込まない。変更は再起動なしで反映する。moderation は Run 作成時
(`finalize_params`)に、タイムアウトは ComfyUI の実行開始時(`execute`)に、その都度 DB を
引いて解決する(いずれも Run 1回につき高々1回の問い合わせで、SSE のイベントなど高頻度な
経路では引かない)。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, get_args

from sqlalchemy.orm import Session

from app.config import Settings
from app.domain.models import AppSetting
from app.i18n import t

MODERATION_KEY = "generation.moderation"
TIMEOUT_KEY = "comfyui.timeout_seconds"

Moderation = Literal["auto", "low"]
_MODERATION_VALUES: tuple[Moderation, ...] = get_args(Moderation)

TIMEOUT_MIN_SECONDS = 60
TIMEOUT_MAX_SECONDS = 10800

Source = Literal["setting", "env", "default"]


class GeneralSettingsValidationError(Exception):
    """保存しようとした値が不正なときに送出する(呼び出し側の API 層で 422 にする)。"""


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _get_raw_value(db: Session, key: str) -> object | None:
    row = db.get(AppSetting, key)
    if row is None:
        return None
    value = row.value
    if not isinstance(value, dict) or "value" not in value:
        return None
    return value["value"]


def _save(db: Session, key: str, value: object) -> None:
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value={"value": value}, updated_at=_utcnow()))
    else:
        row.value = {"value": value}
        row.updated_at = _utcnow()
    db.commit()


def _delete(db: Session, key: str) -> None:
    row = db.get(AppSetting, key)
    if row is not None:
        db.delete(row)
        db.commit()


# -- moderation(Generate 専用) -----------------------------------------------


def default_moderation(settings: Settings) -> Moderation:
    """画面で一度も保存していないときの既定値(環境変数 MODERATION、無ければ組み込みの low)。"""
    return settings.moderation


def get_saved_moderation(db: Session) -> Moderation | None:
    """DB に保存された値を返す。無い、または壊れている(不正な値)なら None
    (未設定のときと同じに扱う)。"""
    value = _get_raw_value(db, MODERATION_KEY)
    if value not in _MODERATION_VALUES:
        return None
    return value  # type: ignore[return-value]


def resolve_moderation(db: Session, settings: Settings) -> tuple[Moderation, Source]:
    """有効な moderation と、その出所(setting/env/default)を返す。"""
    saved = get_saved_moderation(db)
    if saved is not None:
        return saved, "setting"
    source: Source = "env" if "moderation" in settings.model_fields_set else "default"
    return default_moderation(settings), source


def validate_moderation(value: str | None) -> None:
    """`value=None`(リセット)は常に有効。それ以外は auto/low のみ。"""
    if value is not None and value not in _MODERATION_VALUES:
        raise GeneralSettingsValidationError(t("settings.general.invalidModeration"))


def save_moderation(db: Session, value: str | None) -> None:
    """保存する。`value=None` は保存済みの行を削除する(環境変数・既定値に戻す)。

    呼び出し側(API 層)は、複数項目を含む1リクエストの一部だけが保存される事態を避けるため、
    全項目を `validate_*` で検証してからまとめて `save_*` を呼ぶ。
    """
    validate_moderation(value)
    if value is None:
        _delete(db, MODERATION_KEY)
        return
    _save(db, MODERATION_KEY, value)


# -- ComfyUI のタイムアウト(秒) ------------------------------------------------


def default_timeout_seconds(settings: Settings) -> int:
    """画面で一度も保存していないときの既定値(環境変数 COMFYUI_TIMEOUT_SECONDS、無ければ
    組み込みの1800秒)。"""
    return int(settings.comfyui_timeout_seconds)


def get_saved_timeout_seconds(db: Session) -> int | None:
    """DB に保存された値を返す。無い、または壊れている(整数でない・範囲外)なら None
    (未設定のときと同じに扱う)。"""
    value = _get_raw_value(db, TIMEOUT_KEY)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if not (TIMEOUT_MIN_SECONDS <= value <= TIMEOUT_MAX_SECONDS):
        return None
    return value


def resolve_timeout_seconds(db: Session, settings: Settings) -> tuple[int, Source]:
    """有効なタイムアウト(秒)と、その出所(setting/env/default)を返す。"""
    saved = get_saved_timeout_seconds(db)
    if saved is not None:
        return saved, "setting"
    source: Source = "env" if "comfyui_timeout_seconds" in settings.model_fields_set else "default"
    return default_timeout_seconds(settings), source


def validate_timeout_seconds(value: int | None) -> None:
    """`value=None`(リセット)は常に有効。それ以外は60〜10800の整数のみ。"""
    if value is None:
        return
    if isinstance(value, bool) or not (TIMEOUT_MIN_SECONDS <= value <= TIMEOUT_MAX_SECONDS):
        raise GeneralSettingsValidationError(
            t(
                "settings.general.invalidTimeoutSeconds",
                min=TIMEOUT_MIN_SECONDS,
                max=TIMEOUT_MAX_SECONDS,
            )
        )


def save_timeout_seconds(db: Session, value: int | None) -> None:
    """保存する。`value=None` は保存済みの行を削除する(環境変数・既定値に戻す)。

    呼び出し側(API 層)は、複数項目を含む1リクエストの一部だけが保存される事態を避けるため、
    全項目を `validate_*` で検証してからまとめて `save_*` を呼ぶ。
    """
    validate_timeout_seconds(value)
    if value is None:
        _delete(db, TIMEOUT_KEY)
        return
    _save(db, TIMEOUT_KEY, value)
