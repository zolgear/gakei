"""ADR-0017: `PROVIDER` の廃止(`FAKE_PROVIDER` への切り替え、移行の安全策)と、
OpenAI の接続先(Base URL)。実 API・実プロキシには一切接続しない。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import settings as settings_api
from app.config import Settings
from app.domain.api_key import (
    BaseUrlValidationError,
    is_insecure_base_url,
    normalize_base_url,
    read_file_base_url,
    resolve_base_url,
    write_file_base_url,
)
from app.main import LegacyProviderAbortedError, check_legacy_provider_env, create_app

pytestmark = pytest.mark.windows

# -- 主プロバイダーの選択(FAKE_PROVIDER) -------------------------------------


def test_fake_provider_env_selects_fake_as_primary(
    monkeypatch: pytest.MonkeyPatch, data_dir: Path
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = Settings(_env_file=None, data_dir=data_dir, fake_provider=True)
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.app.state.registry.primary == "fake"
        response = client.get("/api/capabilities")
        assert response.json()["default_provider"] == "fake"


def test_default_settings_without_fake_provider_selects_openai(
    monkeypatch: pytest.MonkeyPatch, data_dir: Path
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = Settings(_env_file=None, data_dir=data_dir)
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.app.state.registry.primary == "openai"


# -- 移行の安全策(PROVIDER 環境変数) ------------------------------------------


def test_legacy_provider_none_is_a_noop() -> None:
    settings = Settings(_env_file=None, data_dir=Path("/tmp"))
    check_legacy_provider_env(settings)  # 例外を送出しないことの確認。


def test_legacy_provider_openai_warns_and_is_ignored(caplog: pytest.LogCaptureFixture) -> None:
    settings = Settings(_env_file=None, data_dir=Path("/tmp"), PROVIDER="openai")
    with caplog.at_level(logging.WARNING, logger="app.main"):
        check_legacy_provider_env(settings)
    assert any("PROVIDER" in record.message for record in caplog.records)


def test_legacy_provider_fake_aborts_startup() -> None:
    settings = Settings(_env_file=None, data_dir=Path("/tmp"), PROVIDER="fake")
    with pytest.raises(LegacyProviderAbortedError) as exc:
        check_legacy_provider_env(settings)
    assert "FAKE_PROVIDER" in str(exc.value)


def test_legacy_provider_other_value_also_aborts_startup() -> None:
    settings = Settings(_env_file=None, data_dir=Path("/tmp"), PROVIDER="azure")
    with pytest.raises(LegacyProviderAbortedError):
        check_legacy_provider_env(settings)


def test_app_startup_aborts_when_legacy_provider_is_not_openai(data_dir: Path) -> None:
    """`main.py` の lifespan に実際に配線されていることの確認(TestClient 経由)。"""
    settings = Settings(_env_file=None, data_dir=data_dir, PROVIDER="fake")
    app = create_app(settings)
    with pytest.raises(LegacyProviderAbortedError):
        with TestClient(app):
            pass


def test_app_startup_succeeds_when_legacy_provider_is_openai(data_dir: Path) -> None:
    settings = Settings(_env_file=None, data_dir=data_dir, PROVIDER="openai", fake_provider=True)
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/api/capabilities").status_code == 200


# -- Base URL の正規化・検証 ---------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://127.0.0.1:4000/v1", "http://127.0.0.1:4000/v1"),
        ("http://127.0.0.1:4000/v1/", "http://127.0.0.1:4000/v1"),
        ("https://proxy.example.com/v1", "https://proxy.example.com/v1"),
    ],
)
def test_normalize_base_url_strips_trailing_slash(raw: str, expected: str) -> None:
    assert normalize_base_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "ftp://127.0.0.1:4000/v1",
        "127.0.0.1:4000/v1",  # scheme が無い
        "http://user:pass@127.0.0.1:4000/v1",  # ユーザー情報
        "http://127.0.0.1:4000/v1?foo=bar",  # クエリー
        "http://127.0.0.1:4000/v1#frag",  # フラグメント
        "not a url",
    ],
)
def test_normalize_base_url_rejects_invalid_forms(raw: str) -> None:
    with pytest.raises(BaseUrlValidationError):
        normalize_base_url(raw)


def test_is_insecure_base_url_true_for_non_loopback_http() -> None:
    assert is_insecure_base_url("http://example.com/v1") is True


def test_is_insecure_base_url_false_for_loopback_http() -> None:
    assert is_insecure_base_url("http://127.0.0.1:4000/v1") is False


def test_is_insecure_base_url_false_for_https() -> None:
    assert is_insecure_base_url("https://example.com/v1") is False


# -- ファイル保存・優先順位(resolve_base_url) ---------------------------------


def test_read_file_base_url_missing_file_returns_none(tmp_path: Path) -> None:
    assert read_file_base_url(tmp_path) is None


def test_write_then_read_file_base_url_roundtrip(tmp_path: Path) -> None:
    write_file_base_url(tmp_path, "http://127.0.0.1:4000/v1")
    assert read_file_base_url(tmp_path) == "http://127.0.0.1:4000/v1"


def _settings(*, data_dir: Path, openai_base_url: str | None) -> Settings:
    return Settings(_env_file=None, data_dir=data_dir, openai_base_url=openai_base_url)


def test_resolve_base_url_prefers_env_over_file(tmp_path: Path) -> None:
    write_file_base_url(tmp_path, "http://127.0.0.1:4000/v1")
    settings = _settings(data_dir=tmp_path, openai_base_url="http://127.0.0.1:5000/v1")

    base_url, source = resolve_base_url(settings)
    assert base_url == "http://127.0.0.1:5000/v1"
    assert source == "env"


def test_resolve_base_url_falls_back_to_file(tmp_path: Path) -> None:
    write_file_base_url(tmp_path, "http://127.0.0.1:4000/v1")
    settings = _settings(data_dir=tmp_path, openai_base_url=None)

    base_url, source = resolve_base_url(settings)
    assert base_url == "http://127.0.0.1:4000/v1"
    assert source == "file"


def test_resolve_base_url_none_when_neither_configured(tmp_path: Path) -> None:
    settings = _settings(data_dir=tmp_path, openai_base_url=None)

    base_url, source = resolve_base_url(settings)
    assert base_url is None
    assert source is None


# -- 設定 API(/api/settings/openai-base-url) ---------------------------------


@pytest.fixture
def client_no_key(monkeypatch: pytest.MonkeyPatch, data_dir: Path) -> Iterator[TestClient]:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    settings = Settings(_env_file=None, data_dir=data_dir)
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def client_env_base_url(monkeypatch: pytest.MonkeyPatch, data_dir: Path) -> Iterator[TestClient]:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = Settings(
        _env_file=None, data_dir=data_dir, openai_base_url="http://127.0.0.1:9000/v1"
    )
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


def test_get_base_url_status_when_unset(client_no_key: TestClient) -> None:
    response = client_no_key.get("/api/settings/openai-base-url")
    assert response.status_code == 200
    assert response.json() == {"value": None, "source": None}


def test_get_base_url_status_env_locked(client_env_base_url: TestClient) -> None:
    response = client_env_base_url.get("/api/settings/openai-base-url")
    assert response.status_code == 200
    assert response.json() == {"value": "http://127.0.0.1:9000/v1", "source": "env"}


async def _validator_ok(_api_key: str, _base_url: str | None) -> None:
    return None


def test_put_base_url_without_key_saves_after_format_check_only(
    client_no_key: TestClient,
) -> None:
    calls: list[tuple[str, str | None]] = []

    async def _tracking_validator(api_key: str, base_url: str | None) -> None:
        calls.append((api_key, base_url))

    client_no_key.app.dependency_overrides[settings_api.get_key_validator] = lambda: (
        _tracking_validator
    )
    try:
        response = client_no_key.put(
            "/api/settings/openai-base-url", json={"base_url": "http://127.0.0.1:4000/v1/"}
        )
    finally:
        client_no_key.app.dependency_overrides.pop(settings_api.get_key_validator, None)

    assert response.status_code == 200, response.text
    assert response.json() == {"value": "http://127.0.0.1:4000/v1", "source": "file"}
    assert calls == []  # キーが無いので確認は呼ばれない。


def test_put_base_url_with_key_calls_validator_with_key_and_url(
    client_no_key: TestClient,
) -> None:
    from app.domain.api_key import write_file_key

    write_file_key(client_no_key.app.state.settings.data_dir, "sk-existing-key")

    calls: list[tuple[str, str | None]] = []

    async def _tracking_validator(api_key: str, base_url: str | None) -> None:
        calls.append((api_key, base_url))

    client_no_key.app.dependency_overrides[settings_api.get_key_validator] = lambda: (
        _tracking_validator
    )
    try:
        response = client_no_key.put(
            "/api/settings/openai-base-url", json={"base_url": "http://127.0.0.1:4000/v1"}
        )
    finally:
        client_no_key.app.dependency_overrides.pop(settings_api.get_key_validator, None)

    assert response.status_code == 200, response.text
    assert calls == [("sk-existing-key", "http://127.0.0.1:4000/v1")]


def test_put_base_url_rejects_ftp_scheme(client_no_key: TestClient) -> None:
    client_no_key.app.dependency_overrides[settings_api.get_key_validator] = lambda: _validator_ok
    try:
        response = client_no_key.put(
            "/api/settings/openai-base-url", json={"base_url": "ftp://x/v1"}
        )
    finally:
        client_no_key.app.dependency_overrides.pop(settings_api.get_key_validator, None)
    assert 400 <= response.status_code < 500
    assert read_file_base_url(client_no_key.app.state.settings.data_dir) is None


def test_put_base_url_rejects_empty(client_no_key: TestClient) -> None:
    response = client_no_key.put("/api/settings/openai-base-url", json={"base_url": "   "})
    assert response.status_code == 400


def test_put_base_url_rejected_when_env_locked(client_env_base_url: TestClient) -> None:
    calls: list[tuple[str, str | None]] = []

    async def _tracking_validator(api_key: str, base_url: str | None) -> None:
        calls.append((api_key, base_url))

    client_env_base_url.app.dependency_overrides[settings_api.get_key_validator] = lambda: (
        _tracking_validator
    )
    try:
        response = client_env_base_url.put(
            "/api/settings/openai-base-url", json={"base_url": "http://127.0.0.1:5000/v1"}
        )
    finally:
        client_env_base_url.app.dependency_overrides.pop(settings_api.get_key_validator, None)

    assert response.status_code == 409
    assert calls == []


def test_delete_base_url_removes_file_value(client_no_key: TestClient) -> None:
    write_file_base_url(client_no_key.app.state.settings.data_dir, "http://127.0.0.1:4000/v1")

    response = client_no_key.delete("/api/settings/openai-base-url")
    assert response.status_code == 200
    assert response.json() == {"value": None, "source": None}
    assert read_file_base_url(client_no_key.app.state.settings.data_dir) is None


def test_delete_base_url_rejected_when_env_locked(client_env_base_url: TestClient) -> None:
    response = client_env_base_url.delete("/api/settings/openai-base-url")
    assert response.status_code == 409


def test_key_put_validates_against_effective_base_url(client_env_base_url: TestClient) -> None:
    """ADR-0017: キーを保存するときの確認は、その時点で有効な Base URL に対して行う。"""
    calls: list[tuple[str, str | None]] = []

    async def _tracking_validator(api_key: str, base_url: str | None) -> None:
        calls.append((api_key, base_url))

    client_env_base_url.app.dependency_overrides[settings_api.get_key_validator] = lambda: (
        _tracking_validator
    )
    try:
        response = client_env_base_url.put(
            "/api/settings/openai-key", json={"api_key": "sk-new-key"}
        )
    finally:
        client_env_base_url.app.dependency_overrides.pop(settings_api.get_key_validator, None)

    assert response.status_code == 200, response.text
    assert calls == [("sk-new-key", "http://127.0.0.1:9000/v1")]


# -- OpenAI プロバイダーのクライアント組み立て(base_url) ------------------------


def test_resolve_client_uses_configured_base_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.providers.openai_images import OpenAIImagesProvider

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app.domain.api_key import write_file_base_url, write_file_key

    write_file_key(tmp_path, "sk-file-key")
    write_file_base_url(tmp_path, "http://127.0.0.1:4000/v1")

    provider = OpenAIImagesProvider()
    client = provider._resolve_client()
    assert str(client.base_url) == "http://127.0.0.1:4000/v1/"


def test_resolve_client_defaults_to_openai_when_base_url_unset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.providers.openai_images import OpenAIImagesProvider

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app.domain.api_key import write_file_key

    write_file_key(tmp_path, "sk-file-key")

    provider = OpenAIImagesProvider()
    client = provider._resolve_client()
    assert str(client.base_url) == "https://api.openai.com/v1/"


def test_resolve_client_recreates_when_base_url_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.domain.api_key import write_file_base_url, write_file_key
    from app.providers.openai_images import OpenAIImagesProvider

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    write_file_key(tmp_path, "sk-file-key")

    provider = OpenAIImagesProvider()
    client_1 = provider._resolve_client()

    write_file_base_url(tmp_path, "http://127.0.0.1:4000/v1")
    client_2 = provider._resolve_client()

    assert client_2 is not client_1
    assert str(client_2.base_url) == "http://127.0.0.1:4000/v1/"
