"""ComfyUI の接続設定(ADR-0013 7章)。

接続先の解決は3段階。

1. DB の `app_setting`(キー `comfyui.connection`)に保存された設定があれば、常にそれが勝つ
   (接続 URL、または「切り離した」という明示的な状態 `{"url": None}`)。再起動をまたいでも
   画面の設定を優先し、環境変数には戻らない。
2. 保存された設定が無ければ、環境変数 `COMFYUI_URL`(既定値としてだけ起動時に読む)。
3. どちらも無ければ無効。

環境変数の値は DB に書き込まない。

これとは別に、「無効にした」ことを別のキー(`comfyui.disabled`)に保存する(ADR-0013 7章の
「無効にする / 有効にする」)。無効の間も URL は残し、プロバイダーを登録しない(モデルの選択肢から
外し、接続先に問い合わせない)。環境変数の URL があっても、無効のままにする。切り離すと、無効の
印も消す(切り離した状態に「無効」は重ねない)。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Literal, TypedDict
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.config import Settings
from app.domain.models import AppSetting
from app.i18n import t
from app.providers.registry import is_loopback_url

logger = logging.getLogger(__name__)

CONNECTION_KEY = "comfyui.connection"
DISABLED_KEY = "comfyui.disabled"

Source = Literal["setting", "env", "none"]


class ComfyUIConnectionValidationError(Exception):
    """接続先 URL が不正なときに送出する(呼び出し側の API 層で 422 にする)。"""


class SavedConnection(TypedDict):
    url: str | None


def _utcnow() -> datetime:
    return datetime.now(UTC)


def get_saved_connection(db: Session) -> SavedConnection | None:
    """DB に保存された接続設定を返す。一度も設定していなければ None。"""
    row = db.get(AppSetting, CONNECTION_KEY)
    if row is None:
        return None
    value = row.value
    if not isinstance(value, dict):
        return None
    return {"url": value.get("url")}


def resolve_effective_url(db: Session, settings: Settings) -> tuple[str | None, Source]:
    """有効な接続先 URL と、その出所(setting/env/none)を返す。"""
    saved = get_saved_connection(db)
    if saved is not None:
        return saved["url"], "setting"
    if settings.comfyui_url:
        return settings.comfyui_url, "env"
    return None, "none"


def is_disabled(db: Session) -> bool:
    """「無効にした」が保存されているか。"""
    row = db.get(AppSetting, DISABLED_KEY)
    return row is not None and isinstance(row.value, dict) and row.value.get("disabled") is True


def resolve_active_url(db: Session, settings: Settings) -> str | None:
    """プロバイダーを登録してよい接続先 URL。切り離し・未設定・無効のときは None。"""
    if is_disabled(db):
        return None
    url, _source = resolve_effective_url(db, settings)
    return url


def save_connection_url(db: Session, url: str) -> None:
    """接続 URL を保存する(接続・変更)。無効の印はそのまま(無効の間に URL を変えても無効のまま)。"""
    _put(db, CONNECTION_KEY, {"url": url})
    db.commit()


def save_detached(db: Session) -> None:
    """「切り離した」ことを明示的に保存する。環境変数があっても再起動で再び有効にならない。"""
    _put(db, CONNECTION_KEY, {"url": None})
    _put(db, DISABLED_KEY, {"disabled": False})
    db.commit()


def save_disabled(db: Session, disabled: bool) -> None:
    """無効にする・有効にする。URL には触れない。"""
    _put(db, DISABLED_KEY, {"disabled": disabled})
    db.commit()


def _put(db: Session, key: str, value: dict) -> None:
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=value, updated_at=_utcnow()))
    else:
        row.value = value
        row.updated_at = _utcnow()


def validate_connection_url(url: str, *, allow_non_loopback: bool) -> None:
    """`http`/`https` かつホストを含む形式かを確かめる。

    ループバック以外のアドレスは、`allow_non_loopback=True` のとき(画面で確認の
    チェックを入れたとき)だけ許可する。接続テストからは常に `allow_non_loopback=True`
    で呼ぶ(確認は保存時だけに要求する)。
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ComfyUIConnectionValidationError(t("comfyui.connection.invalidUrl"))
    if not allow_non_loopback and not is_loopback_url(url):
        raise ComfyUIConnectionValidationError(t("comfyui.connection.nonLoopback"))
