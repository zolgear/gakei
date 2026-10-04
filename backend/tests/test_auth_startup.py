"""ADR-0019: `AUTH_MODE=oidc` に必須の環境変数が欠けていたら起動を中止する(`check_auth_env`)。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.main import AuthConfigError, check_auth_env


def _settings(data_dir: Path, **overrides: object) -> Settings:
    base = dict(
        _env_file=None,
        data_dir=data_dir,
        fake_provider=True,
        auth_mode="oidc",
        oidc_issuer="https://idp.test/realms/x",
        oidc_client_id="gakei",
        public_base_url="http://testserver",
    )
    base.update(overrides)
    return Settings(**base)


def test_none_mode_never_raises(data_dir: Path) -> None:
    settings = Settings(_env_file=None, data_dir=data_dir, fake_provider=True)
    check_auth_env(settings)  # 例外が出なければ良い


def test_oidc_mode_with_all_required_vars_passes(data_dir: Path) -> None:
    settings = _settings(data_dir)
    check_auth_env(settings)  # 例外が出なければ良い


@pytest.mark.parametrize(
    "missing_field",
    ["oidc_issuer", "oidc_client_id", "public_base_url"],
)
def test_oidc_mode_missing_required_var_raises(data_dir: Path, missing_field: str) -> None:
    settings = _settings(data_dir, **{missing_field: None})
    with pytest.raises(AuthConfigError):
        check_auth_env(settings)


def test_oidc_mode_missing_var_message_names_it(data_dir: Path) -> None:
    settings = _settings(data_dir, oidc_issuer=None, public_base_url=None)
    with pytest.raises(AuthConfigError) as exc_info:
        check_auth_env(settings)
    message = str(exc_info.value)
    assert "OIDC_ISSUER" in message
    assert "PUBLIC_BASE_URL" in message
    assert "OIDC_CLIENT_ID" not in message


# -- L-4(2026-09-27 追記): PUBLIC_BASE_URL / OIDC_ISSUER の形式検査 -------------------


@pytest.mark.parametrize("field", ["public_base_url", "oidc_issuer"])
def test_oidc_mode_url_without_scheme_aborts_startup(data_dir: Path, field: str) -> None:
    settings = _settings(data_dir, **{field: "testserver"})
    with pytest.raises(AuthConfigError):
        check_auth_env(settings)


def test_oidc_mode_url_without_host_aborts_startup(data_dir: Path) -> None:
    settings = _settings(data_dir, public_base_url="http://")
    with pytest.raises(AuthConfigError):
        check_auth_env(settings)


def test_oidc_mode_invalid_url_message_names_the_field_and_value(data_dir: Path) -> None:
    settings = _settings(data_dir, public_base_url="not-a-url")
    with pytest.raises(AuthConfigError) as exc_info:
        check_auth_env(settings)
    message = str(exc_info.value)
    assert "PUBLIC_BASE_URL" in message
    assert "not-a-url" in message


def test_oidc_mode_non_loopback_http_warns_but_does_not_abort(
    data_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    settings = _settings(data_dir, public_base_url="http://192.168.1.10:8000")
    with caplog.at_level("WARNING"):
        check_auth_env(settings)  # 例外は出ない
    assert any("192.168.1.10" in record.message for record in caplog.records)


def test_oidc_mode_loopback_http_does_not_warn(
    data_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    settings = _settings(data_dir, public_base_url="http://127.0.0.1:8000")
    with caplog.at_level("WARNING"):
        check_auth_env(settings)
    assert not any("127.0.0.1" in record.message for record in caplog.records)


def test_oidc_mode_https_url_does_not_warn(
    data_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    settings = _settings(data_dir, oidc_issuer="https://idp.example.com/realms/x")
    with caplog.at_level("WARNING"):
        check_auth_env(settings)
    assert not any("idp.example.com" in record.message for record in caplog.records)


# -- ADR-0034: 画面の設定(DB)で oidc にしたときの検査 ---------------------------------


def test_db_mode_missing_values_message_guides_emergency_disable(data_dir: Path) -> None:
    from dataclasses import replace

    from app.domain.auth_settings import resolve_auth_config

    config = replace(
        resolve_auth_config(None, Settings(_env_file=None, data_dir=data_dir)),
        mode="oidc",
        mode_source="setting",
    )
    with pytest.raises(AuthConfigError) as exc_info:
        check_auth_env(config)
    message = str(exc_info.value)
    assert "OIDC_ISSUER" in message
    assert "AUTH_MODE=none" in message


def test_env_mode_missing_values_message_is_unchanged(data_dir: Path) -> None:
    settings = _settings(data_dir, oidc_issuer=None)
    with pytest.raises(AuthConfigError) as exc_info:
        check_auth_env(settings)
    assert "AUTH_MODE=none" not in str(exc_info.value)
