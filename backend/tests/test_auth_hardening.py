"""ADR-0019: 2026-09-27 のセキュリティ監査で見つかった項目のテスト
(M-1、L-1〜L-5、I-1、I-3〜I-11。L-6 を除く)。

`test_auth_api.py` / `test_auth_admin_enforcement.py` / `test_auth_created_by.py` /
`test_auth_oidc_client.py` / `test_auth_authz_matrix.py` と役割を分け、こちらは監査の
各項目に直接対応するテストをまとめる。
"""

from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.auth import _sanitize_next
from app.auth.oidc import OidcIdentity
from app.auth.sessions import is_email_allowed
from app.config import Settings
from app.domain.models import AppUser, AuthSession
from app.main import create_app
from tests.conftest import login_as

# -- _sanitize_next(オープンリダイレクト対策)の単体テスト ---------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/\\evil", "/"),
        ("/%09/evil.com", "/%09/evil.com"),  # `/` で始まる相対パスなのでそのまま通す
        ("//evil", "/"),
        ("javascript:alert(1)", "/"),
        ("", "/"),
        (None, "/"),
        ("/settings", "/settings"),
    ],
)
def test_sanitize_next(raw: str | None, expected: str) -> None:
    assert _sanitize_next(raw) == expected


# -- is_email_allowed の境界(監査テスト項目9) ------------------------------------


def test_is_email_allowed_boundaries() -> None:
    admins: set[str] = set()
    # サブドメインは許可ドメインに一致しない(拒否)。
    assert not is_email_allowed("a@sub.example.com", {"example.com"}, admins)
    # 末尾にドットが付いたドメインは別物として扱う(一致しない=拒否)。
    assert not is_email_allowed("a@example.com", {"example.com."}, admins)
    # メールアドレス側の大文字小文字は無視する。
    assert is_email_allowed("A@EXAMPLE.COM", {"example.com"}, admins)


# -- I-10: AUTH_SESSION_HOURS の範囲検証 --------------------------------------------


@pytest.mark.parametrize("hours", [0, -1, 24 * 30 + 1])
def test_auth_session_hours_out_of_range_is_rejected(hours: int) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(_env_file=None, auth_session_hours=hours)


@pytest.mark.parametrize("hours", [1, 12, 24 * 30])
def test_auth_session_hours_in_range_is_accepted(hours: int) -> None:
    assert Settings(_env_file=None, auth_session_hours=hours).auth_session_hours == hours


def test_allowed_email_domain_set_normalizes_at_prefix_case_and_spaces() -> None:
    settings = Settings(
        _env_file=None,
        auth_allowed_email_domains="@example.com, Example.COM",
    )
    assert settings.allowed_email_domain_set() == {"example.com"}


# -- M-1: email_verified ----------------------------------------------------------


def test_callback_rejects_unverified_email_with_dedicated_error_code(
    client_oidc: TestClient,
) -> None:
    fake_client = client_oidc.app.state.test_oidc_client
    fake_client.queue_identity(
        OidcIdentity(
            issuer="https://idp.test/realms/x",
            subject="sub-unverified",
            # AuthlibOidcClient.complete_login はここで email を None にしてから返す
            # (`test_auth_oidc_client.py` で確認済み)。ここでは callback() 側の
            # 分岐(email_missing と email_unverified の出し分け)を検証する。
            email=None,
            name="Unverified",
            email_verified=False,
        )
    )
    client_oidc.get("/api/auth/login", follow_redirects=False)
    response = client_oidc.get("/api/auth/callback", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/?login_error=email_unverified"

    with client_oidc.app.state.session_factory() as db:
        assert db.execute(select(AppUser)).scalars().all() == []


def test_callback_missing_email_without_verified_claim_uses_email_missing(
    client_oidc: TestClient,
) -> None:
    fake_client = client_oidc.app.state.test_oidc_client
    fake_client.queue_identity(
        OidcIdentity(
            issuer="https://idp.test/realms/x",
            subject="sub-no-email",
            email=None,
            name="No Email",
            email_verified=None,
        )
    )
    client_oidc.get("/api/auth/login", follow_redirects=False)
    response = client_oidc.get("/api/auth/callback", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/?login_error=email_missing"


# -- 監査テスト項目2/3: callback の例外を丸める(実 AuthlibOidcClient) -----------------


def _oidc_settings_for(data_dir) -> Settings:  # noqa: ANN001
    return Settings(
        _env_file=None,
        data_dir=data_dir,
        fake_provider=True,
        auth_mode="oidc",
        oidc_issuer="https://idp.test/realms/x",
        oidc_client_id="gakei",
        public_base_url="http://testserver",
    )


def test_callback_translates_unexpected_exception_via_real_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """`complete_login` が(discovery の失敗などで)`RuntimeError` を送出しても、500 では
    なく `callback_failed` の 302 になり、セッションは作られない(L-1)。
    """
    settings = _oidc_settings_for(tmp_path / "data")
    app = create_app(settings)
    with TestClient(app) as client:
        real_client = app.state.oidc_client
        monkeypatch.setattr(
            real_client._client,
            "load_server_metadata",
            AsyncMock(side_effect=RuntimeError("discovery boom")),
        )
        response = client.get("/api/auth/callback?code=x&state=y", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"] == "/?login_error=callback_failed"

        with app.state.session_factory() as db:
            assert db.execute(select(AuthSession)).scalars().all() == []


def test_callback_with_real_client_rejects_forged_state_and_denied_consent(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """実 `AuthlibOidcClient`(discovery だけスタブ化し、Authlib 自身の state/error
    チェックは実物を通す)で、次の3パターンがいずれも `callback_failed` になり、
    `app_user`/`auth_session` が作られないことを確認する(監査テスト項目3)。

    - state/code はあるが `gakei_oidc`(state を保存した一時セッション)が無い
    - `gakei_oidc` Cookie を偽造している
    - IdP が `error=access_denied` を付けて戻してきた
    """
    settings = _oidc_settings_for(tmp_path / "data")
    app = create_app(settings)
    with TestClient(app) as client:
        real_client = app.state.oidc_client
        monkeypatch.setattr(
            real_client._client,
            "load_server_metadata",
            AsyncMock(return_value={"issuer": settings.oidc_issuer}),
        )

        response = client.get("/api/auth/callback?code=x&state=y", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"] == "/?login_error=callback_failed"

        client.cookies.set("gakei_oidc", "forged-not-a-signed-session-cookie")
        response = client.get("/api/auth/callback?code=x&state=y", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"] == "/?login_error=callback_failed"
        client.cookies.delete("gakei_oidc")

        response = client.get("/api/auth/callback?error=access_denied", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"] == "/?login_error=callback_failed"

        with app.state.session_factory() as db:
            assert db.execute(select(AuthSession)).scalars().all() == []
            assert db.execute(select(AppUser)).scalars().all() == []


# -- Cookie の属性(監査テスト項目5) -----------------------------------------------


def test_login_sets_gakei_oidc_cookie_with_expected_attributes(client_oidc: TestClient) -> None:
    response = client_oidc.get("/api/auth/login", follow_redirects=False)
    set_cookie = response.headers.get("set-cookie", "")
    assert "gakei_oidc=" in set_cookie
    assert "httponly" in set_cookie.lower()
    assert "samesite=lax" in set_cookie.lower()
    assert "Max-Age=600" in set_cookie


def test_callback_session_cookie_max_age_matches_auth_session_hours(
    client_oidc: TestClient, oidc_settings: Settings
) -> None:
    fake_client = client_oidc.app.state.test_oidc_client
    fake_client.queue_identity(
        OidcIdentity(
            issuer="https://idp.test/realms/x",
            subject="sub-maxage",
            email="maxage@example.com",
            name="Max Age",
        )
    )
    client_oidc.get("/api/auth/login", follow_redirects=False)
    callback = client_oidc.get("/api/auth/callback", follow_redirects=False)
    set_cookie = callback.headers.get("set-cookie", "")
    assert f"Max-Age={oidc_settings.auth_session_hours * 3600}" in set_cookie


# -- token_hash と生トークンの扱い(監査テスト項目6) --------------------------------


def test_session_token_hash_is_sha256_and_raw_token_absent_from_db_file(
    client_oidc: TestClient,
) -> None:
    login_as(client_oidc, "dbcheck@example.com", "DB Check")
    raw_token = client_oidc.cookies.get("gakei_session")
    assert raw_token

    session_factory = client_oidc.app.state.session_factory
    with session_factory() as db:
        row = db.execute(select(AuthSession)).scalars().one()
        assert row.token_hash == hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    db_path = client_oidc.app.state.settings.db_path
    db_bytes = db_path.read_bytes()
    assert raw_token.encode("utf-8") not in db_bytes


# -- L-3: 管理者一覧・許可ドメインをリクエストごとに評価する ---------------------------


def test_admin_role_changes_take_effect_without_relogin(client_oidc: TestClient) -> None:
    login_as(client_oidc, "same-cookie@example.com", "Same Cookie")
    assert client_oidc.get("/api/auth/me").json()["user"]["role"] == "user"

    # 同じ Cookie のまま、設定(admin 一覧)だけを書き換える(再ログインしない)。
    client_oidc.app.state.settings.auth_admin_emails = "same-cookie@example.com"
    assert client_oidc.get("/api/auth/me").json()["user"]["role"] == "admin"

    client_oidc.app.state.settings.auth_admin_emails = "admin@example.com"
    assert client_oidc.get("/api/auth/me").json()["user"]["role"] == "user"


def test_session_becomes_unauthenticated_when_email_leaves_allowed_domains(
    client_oidc: TestClient,
) -> None:
    login_as(client_oidc, "outsider@corp.example.jp", "Outsider")
    assert client_oidc.get("/api/capabilities").status_code == 200

    client_oidc.app.state.settings.auth_allowed_email_domains = "other.example.jp"
    response = client_oidc.get("/api/capabilities")
    assert response.status_code == 401

    client_oidc.app.state.settings.auth_allowed_email_domains = ""


# -- I-3: 再ログインで古いセッションが無効になる -----------------------------------


def test_relogin_invalidates_previous_session_token(client_oidc: TestClient) -> None:
    login_as(client_oidc, "relogin@example.com", "Relogin One")
    old_token = client_oidc.cookies.get("gakei_session")
    assert client_oidc.get("/api/capabilities").status_code == 200

    login_as(client_oidc, "relogin@example.com", "Relogin Two")
    new_token = client_oidc.cookies.get("gakei_session")
    assert new_token != old_token

    # 古いトークンはもう有効ではない。
    client_oidc.cookies.set("gakei_session", old_token)
    assert client_oidc.get("/api/capabilities").status_code == 401

    # 新しいトークンは有効。
    client_oidc.cookies.set("gakei_session", new_token)
    assert client_oidc.get("/api/capabilities").status_code == 200

    with client_oidc.app.state.session_factory() as db:
        sessions = db.execute(select(AuthSession)).scalars().all()
        assert len(sessions) == 1


# -- I-9: logout の delete_cookie 属性 ----------------------------------------------


def test_logout_delete_cookie_has_httponly_and_samesite(client_oidc: TestClient) -> None:
    login_as(client_oidc, "logout-cookie@example.com", "Logout Cookie")
    response = client_oidc.post("/api/auth/logout")
    set_cookie = response.headers.get("set-cookie", "")
    assert "gakei_session=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "samesite=lax" in set_cookie.lower()


# -- I-7: ログアウトで Clear-Site-Data ----------------------------------------------


def test_logout_sets_clear_site_data_header(client_oidc: TestClient) -> None:
    login_as(client_oidc, "clearcache@example.com", "Clear Cache")
    response = client_oidc.post("/api/auth/logout")
    assert response.headers.get("Clear-Site-Data") == '"cache"'


def test_anonymous_logout_returns_200_and_leaves_db_unchanged(client_oidc: TestClient) -> None:
    """未ログインでの logout(I-2、変更不要と判断した項目)は 200 のまま、DB も変えない
    (`gakei_session` が無いので `revoke()` は呼ばれない。IdP のログアウト URL 自体は、
    未ログインでも組み立てて返す既存の挙動のまま)。
    """
    with client_oidc.app.state.session_factory() as db:
        before = list(db.execute(select(AuthSession)).scalars().all())
    response = client_oidc.post("/api/auth/logout")
    assert response.status_code == 200
    assert "redirect_url" in response.json()
    with client_oidc.app.state.session_factory() as db:
        after = list(db.execute(select(AuthSession)).scalars().all())
    assert before == after == []


# -- none モードの /api/auth/* (監査テスト項目10) ----------------------------------


def test_none_mode_callback_is_404(client: TestClient) -> None:
    response = client.get("/api/auth/callback")
    assert response.status_code == 404


def test_none_mode_logout_returns_200_without_gakei_oidc_cookie(client: TestClient) -> None:
    response = client.post("/api/auth/logout")
    assert response.status_code == 200
    assert response.json() == {"redirect_url": "/"}
    assert "gakei_oidc" not in response.headers.get("set-cookie", "")


# -- Cache-Control(監査テスト項目11) ------------------------------------------------


def test_asset_content_cache_control_is_private(client: TestClient) -> None:
    import io

    from tests.conftest import make_png_bytes

    upload = client.post(
        "/api/assets",
        files={"file": ("input.png", io.BytesIO(make_png_bytes()), "image/png")},
        data={"kind": "upload"},
    )
    assert upload.status_code == 201, upload.text
    asset_id = upload.json()["id"]

    response = client.get(f"/api/assets/{asset_id}/content")
    assert response.status_code == 200
    assert "private" in response.headers.get("Cache-Control", "")


def test_run_partial_cache_control_is_no_store(client_no_runner: TestClient) -> None:
    import uuid

    response = client_no_runner.get(f"/api/runs/{uuid.uuid4()}/partials/0")
    # 404(見つからない)でも Cache-Control の付け方自体はレスポンスのヘッダーとして
    # 検証できないため、ここでは実在するファイルを用意してから確認する。
    assert response.status_code == 404


def test_run_partial_cache_control_header_on_existing_file(
    client_no_runner: TestClient, tmp_path
) -> None:
    import uuid

    from PIL import Image

    data_dir = client_no_runner.app.state.settings.data_dir
    run_id = uuid.uuid4()
    partial_dir = data_dir / "tmp" / "partial" / str(run_id)
    partial_dir.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (4, 4)).save(partial_dir / "0.png")

    response = client_no_runner.get(f"/api/runs/{run_id}/partials/0")
    assert response.status_code == 200
    assert response.headers.get("Cache-Control") == "private, no-store"


# -- I-1: oidc モードで /docs 等が 404 ------------------------------------------------


def test_docs_and_openapi_are_404_in_oidc_mode(client_oidc: TestClient) -> None:
    for path in ("/docs", "/redoc", "/openapi.json"):
        response = client_oidc.get(path)
        assert response.status_code == 404, path


def test_docs_and_openapi_are_available_in_none_mode(client: TestClient) -> None:
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/docs").status_code == 200
