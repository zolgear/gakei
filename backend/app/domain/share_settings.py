"""共有リンクの管理者設定(ADR-0029 5章)。

- `share.enabled`: 共有リンクを使えるか。既定は無効(false)。無効のあいだは共有を作れず、
  既存の共有リンクもすべて 404 にする(記録は消さない。有効に戻せば、取り消していないリンクは
  また使える)。

`app_setting` に保存する(画面で保存した値だけ。環境変数の既定値は持たない)。値の読み書きは
`mcp_settings` と同じ形式(`{"value": ...}`)。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.domain.general_settings import GeneralSettingsValidationError, _get_raw_value, _save
from app.i18n import t

ENABLED_KEY = "share.enabled"

DEFAULT_ENABLED = False


class ShareSettingsValidationError(GeneralSettingsValidationError):
    """保存しようとした値が不正(API 層で 422 にする)。"""


def is_enabled(db: Session) -> bool:
    value = _get_raw_value(db, ENABLED_KEY)
    return value if isinstance(value, bool) else DEFAULT_ENABLED


def validate_enabled(value: object) -> None:
    if not isinstance(value, bool):
        raise ShareSettingsValidationError(t("settings.share.invalidEnabled"))


def save_enabled(db: Session, value: bool) -> None:
    validate_enabled(value)
    _save(db, ENABLED_KEY, value)
