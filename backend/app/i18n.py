"""画面 / API の多言語化(ADR-0015)。日本語と英語の2言語のみ対応する。

サーバーの文言は `backend/app/locales/{ja,en}.json` の1ファイル(言語ごと)にまとめて
置き、`t("area.key", **params)` で引く。差し込みは `str.format` 形式(`{name}`)。
数で文言が変わるもの(英語の単数・複数)は JSON 側で `{"one": ..., "other": ...}` の
オブジェクトにし、`t()` に渡した `count` で選ぶ(日本語も同じ形にそろえる。同じ文でよい)。

キーが両方の JSON に無い場合は `KeyError` を送出してバグを早期に発見できるようにする
(呼び出し側で握りつぶさない)。現在の言語(ja/en)に無くても `ja` にあればそちらを使う
(将来 ja/en 以外の言語を受け付けたときの保険。今は `current_locale()` が ja/en しか
返さないので実質発生しない)。

既定(ヘッダーなし、curl やテスト)は日本語で、これまでの挙動と同じ。

ランチャー(コンソール)の言語選択は `console_t` を使う。こちらはリクエストと無関係に、
OS のロケール環境変数で決める。
"""

from __future__ import annotations

import json
import locale
import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import cache
from pathlib import Path
from typing import Any, Literal

Locale = Literal["ja", "en"]

_DEFAULT_LOCALE: Locale = "ja"

_current_locale: ContextVar[Locale] = ContextVar("current_locale", default=_DEFAULT_LOCALE)

_LOCALES_DIR = Path(__file__).resolve().parent / "locales"


def current_locale() -> Locale:
    """現在のリクエスト(または呼び出しコンテキスト)の言語。"""
    return _current_locale.get()


def set_locale(loc: Locale) -> None:
    _current_locale.set(loc)


@contextmanager
def use_locale(loc: Locale) -> Iterator[None]:
    """指定した言語をこのブロックの間だけ使う(主にテスト用)。"""
    token = _current_locale.set(loc)
    try:
        yield
    finally:
        _current_locale.reset(token)


@cache
def _load_locale(loc: Locale) -> dict[str, Any]:
    """`locales/{loc}.json` を読み込む(プロセス内で1回だけ、モジュールレベルのキャッシュ)。"""
    path = _LOCALES_DIR / f"{loc}.json"
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _resolve(dotted_key: str, loc: Locale) -> Any:
    node: Any = _load_locale(loc)
    for part in dotted_key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _select_and_format(key: str, value: Any, params: dict[str, Any]) -> str:
    if isinstance(value, dict):
        # {"one": ..., "other": ...} の形(数で文言が変わるもの)。
        if "count" not in params:
            raise KeyError(f"i18n key {key!r} requires a 'count' param for pluralization")
        shape = "one" if params["count"] == 1 else "other"
        value = value.get(shape, value.get("other"))
    if not isinstance(value, str):
        raise KeyError(f"i18n key {key!r} did not resolve to a string")
    return value.format(**params)


def t(key: str, /, **params: Any) -> str:
    """現在のリクエストの言語で `key` を引き、`params` を差し込んで返す。

    `key` は JSON の入れ子を `.` でつないだもの(例: `"runs.notFound"`)。
    キーが見つからない場合は `KeyError`(呼び出し側の記述ミスを早期に発見するため)。
    `key` は位置専用引数にしてある(差し込みに `key` という名前のパラメータを使う
    呼び出し側があるため。例: `t("runValidation.unknownParam", key=key)`)。
    """
    value = _resolve(key, current_locale())
    if value is None:
        value = _resolve(key, _DEFAULT_LOCALE)
    if value is None:
        raise KeyError(f"i18n key not found: {key!r}")
    return _select_and_format(key, value, params)


def parse_accept_language(header: str | None) -> Locale:
    """`Accept-Language` ヘッダーから対応言語(ja/en)を選ぶ。

    q 値をおおまかに尊重し、q が最大のタグから順に見て、最初に `ja`/`en` に一致した
    ものを使う。どれにも一致しなければ(ヘッダーなし含め)日本語を既定にする(これまでの
    挙動、curl やテストとの互換のため)。
    """
    if not header:
        return _DEFAULT_LOCALE

    entries: list[tuple[float, str]] = []
    for raw_part in header.split(","):
        part = raw_part.strip()
        if not part:
            continue
        tag = part
        quality = 1.0
        if ";" in part:
            tag, _, params = part.partition(";")
            tag = tag.strip()
            for param in params.split(";"):
                param = param.strip()
                if param.startswith("q="):
                    try:
                        quality = float(param[2:])
                    except ValueError:
                        quality = 1.0
        entries.append((quality, tag.lower()))

    # q 値で降順、同点はヘッダーに現れた順を保つ(安定ソート)。
    entries.sort(key=lambda item: item[0], reverse=True)

    for _quality, tag in entries:
        primary = tag.split("-", 1)[0]
        if primary == "ja":
            return "ja"
        if primary == "en":
            return "en"

    return _DEFAULT_LOCALE


# --- コンソール(ランチャー)の言語選択 -----------------------------------------
#
# リクエストの言語とは別に、OS のロケールだけを見て決める(ADR-0015 Decision 5)。


def _console_locale_is_japanese() -> bool:
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var)
        if value:
            return value.lower().startswith("ja")

    try:
        lang, _encoding = locale.getlocale()
    except (ValueError, TypeError):
        lang = None
    if lang:
        return lang.lower().startswith("ja") or lang.lower().startswith("japanese")

    return False


def console_t(key: str, /, **params: Any) -> str:
    """ランチャー / CLI が端末に出す文言を、OS ロケールに応じて選んで返す。"""
    loc: Locale = "ja" if _console_locale_is_japanese() else "en"
    value = _resolve(key, loc)
    if value is None:
        value = _resolve(key, _DEFAULT_LOCALE)
    if value is None:
        raise KeyError(f"i18n key not found: {key!r}")
    return _select_and_format(key, value, params)
