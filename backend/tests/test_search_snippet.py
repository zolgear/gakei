"""app.domain.search の純粋関数(snippet 生成、LIKE エスケープ、クエリ解析)の単体テスト。

DB を使わない部分だけをここで検証する。HTTP 経由の統合的な挙動は
tests/test_search_api.py で確認する。
"""

from __future__ import annotations

import pytest

from app.domain.search import (
    InvalidSearchQueryError,
    build_snippet,
    escape_like,
    parse_query_terms,
    parse_types,
)


def test_build_snippet_centers_around_first_match() -> None:
    text = "a" * 100 + "TARGET" + "b" * 100
    snippet = build_snippet(text, ["target"], context=10)
    assert "TARGET" in snippet
    assert snippet.startswith("…")
    assert snippet.endswith("…")
    assert len(snippet) < len(text)


def test_build_snippet_is_case_insensitive() -> None:
    snippet = build_snippet("Hello World", ["world"], context=5)
    assert "World" in snippet


def test_build_snippet_picks_earliest_of_multiple_terms() -> None:
    text = "zzz apple zzz banana zzz"
    snippet = build_snippet(text, ["banana", "apple"], context=3)
    assert "apple" in snippet
    assert "banana" not in snippet


def test_build_snippet_no_ellipsis_when_whole_text_fits() -> None:
    text = "short target text"
    snippet = build_snippet(text, ["target"], context=60)
    assert snippet == text
    assert "…" not in snippet


def test_escape_like_escapes_percent_underscore_and_backslash() -> None:
    assert escape_like("50%_off") == "50\\%\\_off"
    assert escape_like("a\\b") == "a\\\\b"


def test_parse_query_terms_strips_and_splits_on_whitespace() -> None:
    assert parse_query_terms("  hello   world  ") == ["hello", "world"]


def test_parse_query_terms_rejects_empty_or_blank() -> None:
    with pytest.raises(InvalidSearchQueryError):
        parse_query_terms("")
    with pytest.raises(InvalidSearchQueryError):
        parse_query_terms("   ")


def test_parse_types_default_is_all_types() -> None:
    assert parse_types(None) == {"run", "asset", "prompt_set"}
    assert parse_types("") == {"run", "asset", "prompt_set"}
    assert parse_types("  ") == {"run", "asset", "prompt_set"}


def test_parse_types_parses_comma_separated_list() -> None:
    assert parse_types("run,asset") == {"run", "asset"}
    assert parse_types(" run , prompt_set ") == {"run", "prompt_set"}


def test_parse_types_rejects_unknown_value() -> None:
    with pytest.raises(InvalidSearchQueryError):
        parse_types("run,bogus")
