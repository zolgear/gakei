"""DB に書けない文字(NUL と、対になっていないサロゲート)の扱い(ADR-0027 2章の追記)。

PostgreSQL は `TEXT` / `JSONB` に NUL(`\\u0000`)を含む文字列を保存できず、psycopg が
`DataError`(UntranslatableCharacter)を出す。問い合わせの引数に NUL があっても同じく失敗する。
SQLite は通すので、両方の DB で同じ振る舞いになるよう、入口で次のように揃える。

- 利用者からのリクエスト(REST の本文・パス・クエリ・フォーム、MCP のツール引数)は、
  NUL を含んでいれば 422(MCP はツールのエラー)で拒む。黙って消すと、利用者の意図と違う値が
  保存される(ComfyUI のテンプレートなど)。判定は `find_nul`。
- 外部から来るテキスト(アップロードした画像の埋め込みメタ情報、プロバイダーの応答やエラー、
  自動タイトル・タグの推定結果、IdP のクレーム)は、NUL を取り除き、UTF-8 にできない文字
  (対になっていないサロゲート)を置き換えてから保存する。拒むと Run の記録や取り込みが
  失敗するため。`sanitize_external_text` / `sanitize_external`。
"""

from __future__ import annotations

from typing import Any

NUL = "\x00"


def find_nul(value: Any, path: tuple[str | int, ...] = ()) -> tuple[str | int, ...] | None:
    """`value`(JSON 風の入れ子。dict のキーも見る)の中で NUL を含む最初の文字列の位置を返す。

    無ければ None。キーに NUL があった場合は、そのキー自体(の NUL を除いた形)を位置の末尾にする。
    """
    if isinstance(value, str):
        return path if NUL in value else None
    if isinstance(value, dict):
        for key, item in value.items():
            key_part: str | int = key if isinstance(key, (str, int)) else str(key)
            if isinstance(key, str) and NUL in key:
                return (*path, key.replace(NUL, ""))
            found = find_nul(item, (*path, key_part))
            if found is not None:
                return found
        return None
    if isinstance(value, (list, tuple, set, frozenset)):
        for index, item in enumerate(value):
            found = find_nul(item, (*path, index))
            if found is not None:
                return found
        return None
    return None


def sanitize_external_text(text: str) -> str:
    """外部から来た文字列から NUL を除き、UTF-8 にできない文字を `?` に置き換える。"""
    if NUL in text:
        text = text.replace(NUL, "")
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        text = text.encode("utf-8", "replace").decode("utf-8")
    return text


def sanitize_external_text_or_none(text: str | None) -> str | None:
    return None if text is None else sanitize_external_text(text)


def sanitize_external(value: Any) -> Any:
    """JSON 風の入れ子(dict のキーを含む)の文字列すべてに `sanitize_external_text` を掛ける。

    文字列以外の値はそのまま返す。tuple は list にする(JSON の列に入れる前提)。
    """
    if isinstance(value, str):
        return sanitize_external_text(value)
    if isinstance(value, dict):
        return {
            (sanitize_external_text(k) if isinstance(k, str) else k): sanitize_external(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_external(item) for item in value]
    return value
