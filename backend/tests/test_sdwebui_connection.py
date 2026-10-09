"""SD WebUI の接続設定の優先順位と URL の検証(ADR-0038 6章)。"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.domain import sdwebui_connection as connection
from app.providers.registry import build_registry
from app.providers.sdwebui.provider import SdWebuiProvider


def _settings(data_dir: Path, **env: str) -> Settings:
    return Settings(DATA_DIR=str(data_dir), FAKE_PROVIDER=True, **env)  # type: ignore[call-arg]


def test_resolve_none_by_default(db_session_factory: sessionmaker, data_dir: Path) -> None:
    with db_session_factory() as db:
        assert connection.resolve_effective_url(db, _settings(data_dir)) == (None, "none")


def test_env_is_used_until_saved(db_session_factory: sessionmaker, data_dir: Path) -> None:
    settings = _settings(data_dir, SDWEBUI_URL="http://127.0.0.1:7860/")
    with db_session_factory() as db:
        assert connection.resolve_effective_url(db, settings) == (
            "http://127.0.0.1:7860",
            "env",
        )
        connection.save_connection_url(db, "http://127.0.0.1:7861")
        assert connection.resolve_effective_url(db, settings) == (
            "http://127.0.0.1:7861",
            "setting",
        )
        connection.save_detached(db)
        # 切り離しを保存したら、環境変数があっても無効のまま
        assert connection.resolve_effective_url(db, settings) == (None, "setting")


@pytest.mark.parametrize(
    "url",
    [
        "ftp://127.0.0.1:7860",
        "127.0.0.1:7860",
        "http://user:pass@127.0.0.1:7860",
        "http://127.0.0.1:7860/?a=1",
        "http://127.0.0.1:7860/#x",
    ],
)
def test_normalize_rejects_bad_urls(url: str) -> None:
    with pytest.raises(connection.SdWebuiConnectionValidationError) as excinfo:
        connection.normalize_connection_url(url, allow_non_loopback=True)
    # 資格情報らしき値をメッセージに含めない
    assert "pass" not in str(excinfo.value)


def test_normalize_non_loopback_requires_confirmation() -> None:
    with pytest.raises(connection.SdWebuiConnectionValidationError):
        connection.normalize_connection_url("http://192.0.2.10:7860", allow_non_loopback=False)
    assert (
        connection.normalize_connection_url("http://192.0.2.10:7860/", allow_non_loopback=True)
        == "http://192.0.2.10:7860"
    )
    assert (
        connection.normalize_connection_url("http://localhost:7860", allow_non_loopback=False)
        == "http://localhost:7860"
    )


def test_credentials_roundtrip(data_dir: Path) -> None:
    assert connection.read_credentials(data_dir) is None
    connection.save_credentials(data_dir, "user-x", "secret-pass-123")
    assert connection.read_credentials(data_dir) == ("user-x", "secret-pass-123")
    assert connection.credentials_set(data_dir) is True
    connection.delete_credentials(data_dir)
    assert connection.read_credentials(data_dir) is None


@pytest.mark.parametrize(
    ("username", "password"), [("", "p"), ("u", ""), ("a:b", "p"), ("u", "p\n")]
)
def test_credentials_validation(data_dir: Path, username: str, password: str) -> None:
    with pytest.raises(connection.SdWebuiConnectionValidationError):
        connection.save_credentials(data_dir, username, password)


def test_build_registry_registers_sdwebui_from_env(
    db_session_factory: sessionmaker, data_dir: Path
) -> None:
    registry = build_registry(
        _settings(data_dir, SDWEBUI_URL="http://127.0.0.1:7860"), db_session_factory
    )
    provider = registry.get("sdwebui")
    assert isinstance(provider, SdWebuiProvider)
    assert provider.base_url == "http://127.0.0.1:7860"

    registry.set_provider("sdwebui", None)
    assert registry.get("sdwebui") is None


def test_build_registry_without_url(db_session_factory: sessionmaker, data_dir: Path) -> None:
    registry = build_registry(_settings(data_dir), db_session_factory)
    assert registry.get("sdwebui") is None
