"""OpenAI API キーと接続先(Base URL)の解決・保存(ADR-0012 Decision 4、ADR-0017)。

優先順位はキー・Base URL とも同じ: 環境変数 / `.env`(`Settings.openai_api_key` /
`Settings.openai_base_url`) > 画面から保存した値(`DATA_DIR/secrets.json`)。環境変数が
設定されている間は、画面から変更・削除できない(呼び出し側の API 層でその判定に使う)。

`secrets.json` は原子的に書き込む(同じディレクトリの一時ファイル + `os.replace`)。POSIX では
0600 で作成する(`tempfile.mkstemp` が既定でその権限を使うため、緩い権限になる時間帯は無い)。
Windows ではユーザープロファイルのアクセス制御に任せ、chmod は行わない。Base URL は秘密では
ないが、社内のホスト名を含みうるためキーと同じファイル・同じ権限で守る(ADR-0017 2章)。

キーの全文はここでもログに出さない・例外メッセージに含めない。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlparse

from app.i18n import t
from app.providers.registry import _is_loopback_url

if TYPE_CHECKING:
    from app.config import Settings

logger = logging.getLogger(__name__)

Source = Literal["env", "file"]

_SECRETS_FILENAME = "secrets.json"


def _secrets_path(data_dir: Path) -> Path:
    return data_dir / _SECRETS_FILENAME


def _read_payload(data_dir: Path) -> dict:
    path = _secrets_path(data_dir)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # 壊れたファイルは「未設定」として扱う(起動やAPIを壊さないため)。
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_payload_atomic(data_dir: Path, payload: dict) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = _secrets_path(data_dir)

    if not payload:
        # 何も残らないなら secrets.json ごと削除する。
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return

    fd, tmp_name = tempfile.mkstemp(dir=data_dir, prefix=".secrets-", suffix=".tmp")
    try:
        # POSIX では mkstemp が既定で 0600 を使う。念のため明示しておく(Windows は非対応)。
        if os.name != "nt":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def read_file_key(data_dir: Path) -> str | None:
    """ファイル保存のキーを読む(未設定・壊れたファイルは None)。"""
    key = _read_payload(data_dir).get("openai_api_key")
    return key if isinstance(key, str) and key else None


def write_file_key(data_dir: Path, api_key: str) -> None:
    """ファイルにキーを保存する(既存の他フィールドは保持する)。"""
    payload = _read_payload(data_dir)
    payload["openai_api_key"] = api_key
    _write_payload_atomic(data_dir, payload)


def delete_file_key(data_dir: Path) -> None:
    """ファイル保存のキーを削除する。他に残るものが無ければファイル自体を消す。"""
    payload = _read_payload(data_dir)
    if "openai_api_key" not in payload:
        return
    del payload["openai_api_key"]
    _write_payload_atomic(data_dir, payload)


def read_secret_field(data_dir: Path, key: str) -> str | None:
    """`secrets.json` の任意フィールドを読む(ADR-0019。`auth/secret.py` の `auth_secret` 用)。

    キーと Base URL 専用の `read_file_key` / `read_file_base_url` と同じファイル・同じ
    アトミック書き込みを共有するための、汎用の公開ラッパー(`_read_payload` を外から
    直接呼ばせないため)。
    """
    value = _read_payload(data_dir).get(key)
    return value if isinstance(value, str) and value else None


def write_secret_field(data_dir: Path, key: str, value: str) -> None:
    """`secrets.json` の任意フィールドを保存する(既存の他フィールドは保持する)。"""
    payload = _read_payload(data_dir)
    payload[key] = value
    _write_payload_atomic(data_dir, payload)


def delete_secret_field(data_dir: Path, key: str) -> None:
    """`secrets.json` の任意フィールドを削除する。他に残るものが無ければファイル自体を消す。"""
    payload = _read_payload(data_dir)
    if key not in payload:
        return
    del payload[key]
    _write_payload_atomic(data_dir, payload)


def resolve_key(settings: Settings) -> tuple[str | None, Source | None]:
    """有効なキーとその出所を返す。環境変数 / `.env` が常に優先。"""
    if settings.openai_api_key:
        return settings.openai_api_key, "env"
    file_key = read_file_key(settings.data_dir)
    if file_key:
        return file_key, "file"
    return None, None


def hint(api_key: str) -> str:
    """画面に見せてよい表示用の断片(末尾4文字のみ)。"""
    tail = api_key[-4:] if len(api_key) >= 4 else api_key
    return f"…{tail}"


# -- Base URL(ADR-0017) ---------------------------------------------------
# 値は秘密ではないので、画面には全文を返してよい(`hint` に相当する処理は無い)。


class BaseUrlValidationError(Exception):
    """Base URL の形式が不正なときに送出する(呼び出し側の API 層で 422 にする)。"""


def read_file_base_url(data_dir: Path) -> str | None:
    """ファイル保存の Base URL を読む(未設定・壊れたファイルは None)。"""
    value = _read_payload(data_dir).get("openai_base_url")
    return value if isinstance(value, str) and value else None


def write_file_base_url(data_dir: Path, base_url: str) -> None:
    """ファイルに Base URL を保存する(既存の他フィールドは保持する)。"""
    payload = _read_payload(data_dir)
    payload["openai_base_url"] = base_url
    _write_payload_atomic(data_dir, payload)


def delete_file_base_url(data_dir: Path) -> None:
    """ファイル保存の Base URL を削除する。他に残るものが無ければファイル自体を消す。"""
    payload = _read_payload(data_dir)
    if "openai_base_url" not in payload:
        return
    del payload["openai_base_url"]
    _write_payload_atomic(data_dir, payload)


def resolve_base_url(settings: Settings) -> tuple[str | None, Source | None]:
    """有効な Base URL とその出所を返す。環境変数 / `.env` が常に優先。

    どちらも未設定なら `(None, None)`(OpenAI 本体を使う)。
    """
    if settings.openai_base_url:
        return settings.openai_base_url.rstrip("/"), "env"
    file_url = read_file_base_url(settings.data_dir)
    if file_url:
        return file_url, "file"
    return None, None


def normalize_base_url(url: str) -> str:
    """`http` / `https` のみ許可し、ユーザー情報・クエリー・フラグメントを含むものは拒否する。

    末尾の `/` は取り除く(OpenAI SDK の `base_url` にそのまま渡す。ADR-0017 2章)。
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise BaseUrlValidationError(t("openai.baseUrl.invalid"))
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise BaseUrlValidationError(t("openai.baseUrl.invalid"))
    return url.rstrip("/")


def is_insecure_base_url(url: str) -> bool:
    """ループバック以外への `http`(TLS なし)かどうか。"""
    return urlparse(url).scheme == "http" and not _is_loopback_url(url)


def warn_if_insecure_base_url(url: str) -> None:
    """ループバック以外への `http` なら警告をログに出す(ADR-0017 2章。ComfyUI の警告と同じ
    扱い。ADR-0013)。起動時と画面での保存時の両方から呼ぶ。
    """
    if is_insecure_base_url(url):
        logger.warning(
            "OpenAI の接続先(Base URL)がループバック以外への http を指しています(%s)。"
            "API キーと画像が平文で送信されます。",
            url,
        )
