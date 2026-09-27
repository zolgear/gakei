"""`app.domain.api_key` の単体テスト(ADR-0012 Decision 4)。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from app.config import Settings
from app.domain.api_key import delete_file_key, hint, read_file_key, resolve_key, write_file_key


def test_read_file_key_missing_file_returns_none(tmp_path: Path) -> None:
    assert read_file_key(tmp_path) is None


def test_write_then_read_file_key_roundtrip(tmp_path: Path) -> None:
    write_file_key(tmp_path, "sk-abcd1234")
    assert read_file_key(tmp_path) == "sk-abcd1234"


def test_write_file_key_overwrites_previous_value(tmp_path: Path) -> None:
    write_file_key(tmp_path, "sk-first")
    write_file_key(tmp_path, "sk-second")
    assert read_file_key(tmp_path) == "sk-second"


def test_write_file_key_is_atomic_no_temp_file_left_behind(tmp_path: Path) -> None:
    write_file_key(tmp_path, "sk-abcd1234")
    names = {p.name for p in tmp_path.iterdir()}
    assert names == {"secrets.json"}


@pytest.mark.skipif(os.name == "nt", reason="POSIX のファイル権限のテスト")
def test_write_file_key_sets_0600_permissions(tmp_path: Path) -> None:
    write_file_key(tmp_path, "sk-abcd1234")
    mode = (tmp_path / "secrets.json").stat().st_mode & 0o777
    assert mode == 0o600


def test_delete_file_key_removes_file_when_nothing_else_remains(tmp_path: Path) -> None:
    write_file_key(tmp_path, "sk-abcd1234")
    delete_file_key(tmp_path)
    assert read_file_key(tmp_path) is None
    assert not (tmp_path / "secrets.json").exists()


def test_delete_file_key_keeps_other_fields(tmp_path: Path) -> None:
    """将来 secrets.json に他のフィールドが増えても、キーの削除で巻き込まない。"""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "secrets.json").write_text(
        json.dumps({"openai_api_key": "sk-abcd1234", "other_field": "keep-me"}),
        encoding="utf-8",
    )
    delete_file_key(tmp_path)
    assert read_file_key(tmp_path) is None
    remaining = json.loads((tmp_path / "secrets.json").read_text(encoding="utf-8"))
    assert remaining == {"other_field": "keep-me"}


def test_delete_file_key_on_missing_file_is_a_noop(tmp_path: Path) -> None:
    delete_file_key(tmp_path)  # 例外を送出しないことの確認。
    assert not (tmp_path / "secrets.json").exists()


def test_read_file_key_ignores_corrupted_json(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "secrets.json").write_text("not-json{{{", encoding="utf-8")
    assert read_file_key(tmp_path) is None


def test_hint_shows_only_last_four_chars() -> None:
    assert hint("sk-abcdefgh1234") == "…1234"


def test_hint_on_short_key_returns_whole_key() -> None:
    assert hint("ab") == "…ab"


def _settings(*, data_dir: Path, openai_api_key: str | None) -> Settings:
    # env_file を無効化し、明示した値だけで Settings を組み立てる(実物の .env を読まない)。
    return Settings(_env_file=None, data_dir=data_dir, openai_api_key=openai_api_key)


def test_resolve_key_prefers_env_over_file(tmp_path: Path) -> None:
    write_file_key(tmp_path, "sk-file")
    settings = _settings(data_dir=tmp_path, openai_api_key="sk-env")

    api_key, source = resolve_key(settings)
    assert api_key == "sk-env"
    assert source == "env"


def test_resolve_key_falls_back_to_file(tmp_path: Path) -> None:
    write_file_key(tmp_path, "sk-file")
    settings = _settings(data_dir=tmp_path, openai_api_key=None)

    api_key, source = resolve_key(settings)
    assert api_key == "sk-file"
    assert source == "file"


def test_resolve_key_none_when_neither_configured(tmp_path: Path) -> None:
    settings = _settings(data_dir=tmp_path, openai_api_key=None)

    api_key, source = resolve_key(settings)
    assert api_key is None
    assert source is None


if sys.platform != "win32":

    def test_write_file_key_never_leaves_world_readable_window(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`mkstemp` は既定で 0600 を使うため、fchmod 前に緩い権限の一時ファイルが
        存在する窓は無い。ここでは実際に作られたファイルの権限だけを確認する
        (競合状態そのものを検出するのは難しいため、既定の安全性に依存している点を
        コメントで明記しておく)。
        """
        write_file_key(tmp_path, "sk-abcd1234")
        for entry in tmp_path.iterdir():
            mode = entry.stat().st_mode & 0o777
            assert mode == 0o600, f"{entry} の権限が 0600 ではありません: {oct(mode)}"
