"""ADR-0019: `AuthlibOidcClient.complete_login` の単体テスト(セキュリティ監査 M-1、L-1、L-2、
I-4、I-5)。Authlib が内部で使うクライアント(`AuthlibOidcClient._client`)を `AsyncMock` に
差し替え、実 IdP には一切接続しない。
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.auth.oidc import AuthlibOidcClient
from app.config import Settings
from tests.openai_mock import run_async


def _settings(data_dir) -> Settings:  # noqa: ANN001
    return Settings(
        _env_file=None,
        data_dir=data_dir,
        fake_provider=True,
        auth_mode="oidc",
        oidc_issuer="https://idp.test/realms/x",
        oidc_client_id="gakei",
        public_base_url="http://testserver",
    )


def _client_with_inner(  # noqa: ANN001
    monkeypatch: pytest.MonkeyPatch, data_dir
) -> tuple[AuthlibOidcClient, AsyncMock]:
    """`AuthlibOidcClient._client`(プロパティ)を `AsyncMock` に差し替えて返す。"""
    client = AuthlibOidcClient(_settings(data_dir))
    inner = AsyncMock()
    monkeypatch.setattr(AuthlibOidcClient, "_client", property(lambda self: inner))
    return client, inner


# -- M-1: email_verified ---------------------------------------------------------


@run_async
async def test_complete_login_drops_email_when_email_verified_false(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    client, inner = _client_with_inner(monkeypatch, tmp_path / "data")
    inner.load_server_metadata.return_value = {"issuer": "https://idp.test/realms/x"}
    inner.authorize_access_token.return_value = {
        "userinfo": {
            "sub": "sub-1",
            "email": "unverified@example.com",
            "email_verified": False,
            "name": "Unverified",
        }
    }

    identity = await client.complete_login(request=None)

    assert identity.email is None
    assert identity.email_verified is False
    assert identity.subject == "sub-1"


@run_async
async def test_complete_login_keeps_email_when_email_verified_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """クレーム自体が無い IdP は、これまでどおり通す。"""
    client, inner = _client_with_inner(monkeypatch, tmp_path / "data")
    inner.load_server_metadata.return_value = {"issuer": "https://idp.test/realms/x"}
    inner.authorize_access_token.return_value = {
        "userinfo": {"sub": "sub-2", "email": "no-claim@example.com", "name": "No Claim"}
    }

    identity = await client.complete_login(request=None)

    assert identity.email == "no-claim@example.com"
    assert identity.email_verified is None


@run_async
async def test_complete_login_keeps_email_when_email_verified_true(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    client, inner = _client_with_inner(monkeypatch, tmp_path / "data")
    inner.load_server_metadata.return_value = {"issuer": "https://idp.test/realms/x"}
    inner.authorize_access_token.return_value = {
        "userinfo": {
            "sub": "sub-3",
            "email": "verified@example.com",
            "email_verified": True,
            "name": "Verified",
        }
    }

    identity = await client.complete_login(request=None)

    assert identity.email == "verified@example.com"
    assert identity.email_verified is True


# -- L-2: aud / iss を claims_options で渡す --------------------------------------


@run_async
async def test_complete_login_passes_aud_and_iss_claims_options(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    client, inner = _client_with_inner(monkeypatch, tmp_path / "data")
    inner.load_server_metadata.return_value = {"issuer": "https://idp.test/realms/x"}
    inner.authorize_access_token.return_value = {
        "userinfo": {"sub": "sub-4", "email": "a@example.com", "name": "A"}
    }

    await client.complete_login(request=None)

    _, kwargs = inner.authorize_access_token.call_args
    claims_options = kwargs["claims_options"]
    assert claims_options["aud"] == {"essential": True, "values": ["gakei"]}
    assert claims_options["iss"] == {"essential": True, "values": ["https://idp.test/realms/x"]}


# -- I-4: userinfo で補ったときの sub 一致確認 -------------------------------------


@run_async
async def test_complete_login_rejects_userinfo_sub_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    client, inner = _client_with_inner(monkeypatch, tmp_path / "data")
    inner.load_server_metadata.return_value = {"issuer": "https://idp.test/realms/x"}
    # ID トークンには email が無い(userinfo エンドポイントへの補完が発生する)。
    inner.authorize_access_token.return_value = {"userinfo": {"sub": "sub-id-token"}}
    inner.userinfo.return_value = {"sub": "sub-DIFFERENT", "email": "b@example.com"}

    with pytest.raises(HTTPException) as exc_info:
        await client.complete_login(request=None)
    assert exc_info.value.status_code == 400


@run_async
async def test_complete_login_allows_matching_userinfo_sub(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    client, inner = _client_with_inner(monkeypatch, tmp_path / "data")
    inner.load_server_metadata.return_value = {"issuer": "https://idp.test/realms/x"}
    inner.authorize_access_token.return_value = {"userinfo": {"sub": "sub-same"}}
    inner.userinfo.return_value = {"sub": "sub-same", "email": "c@example.com"}

    identity = await client.complete_login(request=None)
    assert identity.email == "c@example.com"
    assert identity.subject == "sub-same"


# -- L-1: OAuthError 以外の例外も 400 に丸める -------------------------------------


@run_async
async def test_complete_login_translates_runtime_error_to_400(
    monkeypatch: pytest.MonkeyPatch, tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    client, inner = _client_with_inner(monkeypatch, tmp_path / "data")
    inner.load_server_metadata.side_effect = RuntimeError("boom, contains secret=xyz")

    with caplog.at_level(logging.WARNING):
        with pytest.raises(HTTPException) as exc_info:
            await client.complete_login(request=None)

    assert exc_info.value.status_code == 400
    # トークン・code・secret を含めない(例外の型名だけをログに残す)。
    assert "secret=xyz" not in caplog.text
    assert "RuntimeError" in caplog.text


@run_async
async def test_complete_login_translates_connect_error_to_400(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    import httpx2

    client, inner = _client_with_inner(monkeypatch, tmp_path / "data")
    inner.load_server_metadata.side_effect = httpx2.ConnectError("connection refused")

    with pytest.raises(HTTPException) as exc_info:
        await client.complete_login(request=None)
    assert exc_info.value.status_code == 400


@run_async
async def test_complete_login_translates_userinfo_fetch_error_to_400(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    client, inner = _client_with_inner(monkeypatch, tmp_path / "data")
    inner.load_server_metadata.return_value = {"issuer": "https://idp.test/realms/x"}
    inner.authorize_access_token.return_value = {"userinfo": {"sub": "sub-x"}}  # email 無し
    inner.userinfo.side_effect = RuntimeError("userinfo endpoint boom")

    with pytest.raises(HTTPException) as exc_info:
        await client.complete_login(request=None)
    assert exc_info.value.status_code == 400


@run_async
async def test_complete_login_rejects_missing_subject(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    client, inner = _client_with_inner(monkeypatch, tmp_path / "data")
    inner.load_server_metadata.return_value = {"issuer": "https://idp.test/realms/x"}
    inner.authorize_access_token.return_value = {"userinfo": {"email": "no-sub@example.com"}}

    with pytest.raises(HTTPException) as exc_info:
        await client.complete_login(request=None)
    assert exc_info.value.status_code == 400


# -- I-5: discovery の issuer と OIDC_ISSUER の食い違い -----------------------------


@run_async
async def test_complete_login_warns_once_on_issuer_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    client, inner = _client_with_inner(monkeypatch, tmp_path / "data")
    inner.load_server_metadata.return_value = {"issuer": "https://different-issuer.test/x"}
    inner.authorize_access_token.return_value = {
        "userinfo": {"sub": "sub-1", "email": "a@example.com"}
    }

    with caplog.at_level(logging.WARNING):
        await client.complete_login(request=None)
        await client.complete_login(request=None)

    warnings = [r for r in caplog.records if "issuer" in r.getMessage()]
    # 起動ごとに一度だけ警告する(2回目は出さない)。
    assert len(warnings) == 1


@run_async
async def test_complete_login_does_not_warn_when_issuer_matches(
    monkeypatch: pytest.MonkeyPatch, tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    client, inner = _client_with_inner(monkeypatch, tmp_path / "data")
    inner.load_server_metadata.return_value = {"issuer": "https://idp.test/realms/x"}
    inner.authorize_access_token.return_value = {
        "userinfo": {"sub": "sub-1", "email": "a@example.com"}
    }

    with caplog.at_level(logging.WARNING):
        await client.complete_login(request=None)

    assert not any("issuer" in r.getMessage() for r in caplog.records)
