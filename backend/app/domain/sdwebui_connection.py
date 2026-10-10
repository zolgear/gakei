"""SD WebUI(A1111 互換の API)の接続設定と資格情報(ADR-0038 6章)。

接続先の解決は ComfyUI(`app/domain/comfyui_connection.py`)と同じ3段階。

1. DB の `app_setting`(キー `sdwebui.connection`)に保存された設定があれば、常にそれが勝つ
   (接続 URL、または「切り離した」という明示的な状態 `{"url": None}`)。
2. 保存された設定が無ければ、環境変数 `SDWEBUI_URL`(既定値としてだけ起動時に読む)。
3. どちらも無ければ無効。

Basic 認証(WebUI の `--api-auth`)のユーザー名とパスワードは `DATA_DIR/secrets.json` に
置く(`app/domain/api_key.py` の `write_secret_field` など)。値は API の応答・ログ・例外の
メッセージに一部も出さず、「設定済みか」だけを返す。URL に `user:pass@` を含めることは
受け付けない(ADR-0017 と同じ)。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, TypedDict
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.config import Settings
from app.domain import api_key as secrets_store
from app.domain.models import AppSetting
from app.i18n import t
from app.providers.registry import is_loopback_url

CONNECTION_KEY = "sdwebui.connection"
USERNAME_FIELD = "sdwebui_auth_username"
PASSWORD_FIELD = "sdwebui_auth_password"

# ユーザー名とパスワードの長さの上限(画面の入力ミスで巨大な値を保存しないため)。
CREDENTIAL_MAX_LENGTH = 256

Source = Literal["setting", "env", "none"]


class SdWebuiConnectionValidationError(Exception):
    """接続先 URL や資格情報が不正なときに送出する(API 層で 422 にする)。"""


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
    url = value.get("url")
    return {"url": url if isinstance(url, str) and url else None}


def resolve_effective_url(db: Session, settings: Settings) -> tuple[str | None, Source]:
    """有効な接続先 URL と、その出所(setting/env/none)を返す。"""
    saved = get_saved_connection(db)
    if saved is not None:
        return saved["url"], "setting"
    if settings.sdwebui_url:
        return settings.sdwebui_url.rstrip("/"), "env"
    return None, "none"


def save_connection_url(db: Session, url: str) -> None:
    _save(db, {"url": url})


def save_detached(db: Session) -> None:
    """「切り離した」ことを明示的に保存する。環境変数があっても再起動で再び有効にならない。"""
    _save(db, {"url": None})


def _save(db: Session, value: dict) -> None:
    row = db.get(AppSetting, CONNECTION_KEY)
    if row is None:
        db.add(AppSetting(key=CONNECTION_KEY, value=value, updated_at=_utcnow()))
    else:
        row.value = value
        row.updated_at = _utcnow()
    db.commit()


def normalize_connection_url(url: str, *, allow_non_loopback: bool) -> str:
    """`http`/`https` でホストを含み、ユーザー情報・クエリー・フラグメントを含まないことを
    確かめ、末尾の `/` を除いて返す。

    ループバック以外は `allow_non_loopback=True`(画面で確認のチェックを入れたとき)だけ
    許可する。接続テストからは常に True で呼ぶ(確認は保存時だけに要求する)。
    """
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise SdWebuiConnectionValidationError(t("sdwebui.connection.invalidUrl"))
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        # 資格情報は URL に書かず、資格情報の欄で設定する(ADR-0017 と同じ)。
        raise SdWebuiConnectionValidationError(t("sdwebui.connection.urlHasExtras"))
    if not allow_non_loopback and not is_loopback_url(url):
        raise SdWebuiConnectionValidationError(t("sdwebui.connection.nonLoopback"))
    return url.rstrip("/")


# -- 資格情報(Basic 認証) ---------------------------------------------------------


def read_credentials(data_dir: Path) -> tuple[str, str] | None:
    """保存済みの (ユーザー名, パスワード)。どちらかが欠けていれば None。"""
    username = secrets_store.read_secret_field(data_dir, USERNAME_FIELD)
    password = secrets_store.read_secret_field(data_dir, PASSWORD_FIELD)
    if username is None or password is None:
        return None
    return username, password


def credentials_set(data_dir: Path) -> bool:
    return read_credentials(data_dir) is not None


def validate_credentials(username: str, password: str) -> None:
    """空、長すぎる、制御文字を含む、ユーザー名に `:` を含む値を断る(値はメッセージに
    含めない)。"""
    for value in (username, password):
        if not value or len(value) > CREDENTIAL_MAX_LENGTH:
            raise SdWebuiConnectionValidationError(t("sdwebui.credentials.invalid"))
        if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
            raise SdWebuiConnectionValidationError(t("sdwebui.credentials.invalid"))
    if ":" in username:
        # Basic 認証ではユーザー名とパスワードを `:` でつなぐため、ユーザー名には使えない。
        raise SdWebuiConnectionValidationError(t("sdwebui.credentials.usernameColon"))


def save_credentials(data_dir: Path, username: str, password: str) -> None:
    validate_credentials(username, password)
    secrets_store.write_secret_field(data_dir, USERNAME_FIELD, username)
    secrets_store.write_secret_field(data_dir, PASSWORD_FIELD, password)


def delete_credentials(data_dir: Path) -> None:
    secrets_store.delete_secret_field(data_dir, USERNAME_FIELD)
    secrets_store.delete_secret_field(data_dir, PASSWORD_FIELD)
