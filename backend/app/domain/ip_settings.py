"""IP 素材の管理者設定(ADR-0059 1章)。

- `ip.enabled`: IP 素材の機能を使えるか。既定は無効(false)。認証モードのときだけ意味を持つ。
  無効のあいだは IP の画面と API を出さない。無効に戻しても、取り込んだ素材の記録と、素材と
  派生物の制限は効き続ける(ADR-0059 1章)。

`app_setting` に保存する(`share_settings` と同じ形式)。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.domain.general_settings import GeneralSettingsValidationError, _get_raw_value, _save
from app.i18n import t

ENABLED_KEY = "ip.enabled"

DEFAULT_ENABLED = False


class IpSettingsValidationError(GeneralSettingsValidationError):
    """保存しようとした値が不正(API 層で 422 にする)。"""


def is_enabled(db: Session) -> bool:
    value = _get_raw_value(db, ENABLED_KEY)
    return value if isinstance(value, bool) else DEFAULT_ENABLED


def save_enabled(db: Session, value: bool) -> None:
    if not isinstance(value, bool):
        raise IpSettingsValidationError(t("ip.invalidEnabled"))
    _save(db, ENABLED_KEY, value)
