"""`DATABASE_URL` の解釈と、起動時の接続確認(ADR-0027 1章)。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings, display_database_url, normalize_database_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("postgresql://u:p@h:5432/db", "postgresql+psycopg://u:p@h:5432/db"),
        ("postgres://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
        ("postgresql+psycopg://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
        ("sqlite:///x.db", "sqlite:///x.db"),
    ],
)
def test_normalize_database_url(raw: str, expected: str) -> None:
    assert normalize_database_url(raw) == expected


def test_sqlalchemy_url_defaults_to_sqlite_in_data_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    settings = Settings(_env_file=None, data_dir=tmp_path)
    assert settings.sqlalchemy_url == f"sqlite:///{tmp_path / 'gakei.db'}"
    assert settings.uses_sqlite


def test_sqlalchemy_url_uses_database_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "  postgres://gakei:pw@db:5432/gakei  ")
    settings = Settings(_env_file=None, data_dir=tmp_path)
    assert settings.sqlalchemy_url == "postgresql+psycopg://gakei:pw@db:5432/gakei"
    assert not settings.uses_sqlite


def test_empty_database_url_falls_back_to_sqlite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", "")
    settings = Settings(_env_file=None, data_dir=tmp_path)
    assert settings.uses_sqlite


def test_display_database_url_hides_password() -> None:
    shown = display_database_url("postgresql+psycopg://gakei:s3cret@db:5432/gakei")
    assert "s3cret" not in shown
    assert "db:5432/gakei" in shown


def test_check_database_connection_fails_with_clear_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.main import DatabaseUnavailableError, check_database_connection

    # ポート 1 には何も待ち受けていない。
    monkeypatch.setenv("DATABASE_URL", "postgresql://gakei:s3cret@127.0.0.1:1/gakei")
    settings = Settings(_env_file=None, data_dir=tmp_path)
    with pytest.raises(DatabaseUnavailableError) as exc_info:
        check_database_connection(settings)
    message = str(exc_info.value)
    assert "データベースに接続できません" in message
    assert "DATABASE_URL" in message
    assert "s3cret" not in message


def test_launcher_exits_without_traceback_when_database_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from app.__main__ import main

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", "postgresql://gakei:s3cret@127.0.0.1:1/gakei")
    with pytest.raises(SystemExit) as exc_info:
        main([])
    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "データベースに接続できません" in err
    assert "s3cret" not in err


def test_sqlite_is_not_checked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.main import check_database_connection

    monkeypatch.delenv("DATABASE_URL", raising=False)
    check_database_connection(Settings(_env_file=None, data_dir=tmp_path))


def test_malformed_database_url_does_not_leak_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.main import DatabaseUnavailableError, check_database_connection

    monkeypatch.setenv("DATABASE_URL", "postgresql://gakei:s3cret@host:notaport/gakei")
    settings = Settings(_env_file=None, data_dir=tmp_path)
    with pytest.raises(DatabaseUnavailableError) as exc_info:
        check_database_connection(settings)
    assert "s3cret" not in str(exc_info.value)
