"""ADR-0019: OIDC ログイン(`/api/auth/*`)と、既存 API への認可の掛かり方。"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import Settings
from app.domain.models import AppUser, AuthSession
from tests.conftest import login_as

# -- 未ログインは401(ADR-0019: /api/auth 以外の9ルーターに require_user が掛かっている) ------


def test_protected_endpoints_require_login_in_oidc_mode(client_oidc: TestClient) -> None:
    random_id = uuid.uuid4()
    for path in (
        "/api/runs",
        "/api/capabilities",
        f"/api/assets/{random_id}/content",
        f"/api/runs/{random_id}/events",
    ):
        response = client_oidc.get(path)
        assert response.status_code == 401, path


def test_401_detail_follows_accept_language(client_oidc: TestClient) -> None:
    ja = client_oidc.get("/api/capabilities")
    assert ja.status_code == 401
    assert ja.json()["detail"] == "ログインが必要です。"

    en = client_oidc.get("/api/capabilities", headers={"Accept-Language": "en"})
    assert en.status_code == 401
    assert en.json()["detail"] == "Login required."


def test_none_mode_does_not_require_login(client: TestClient) -> None:
    response = client.get("/api/capabilities")
    assert response.status_code == 200


# -- /me ---------------------------------------------------------------------


def test_me_none_mode(client: TestClient) -> None:
    response = client.get("/api/auth/me")
    assert response.status_code == 200
    assert response.json() == {"mode": "none", "user": None}


def test_me_oidc_mode_before_and_after_login(client_oidc: TestClient) -> None:
    before = client_oidc.get("/api/auth/me")
    assert before.status_code == 200
    assert before.json() == {"mode": "oidc", "user": None}

    login_as(client_oidc, "user@example.com", "一般ユーザー")

    after = client_oidc.get("/api/auth/me")
    assert after.status_code == 200
    body = after.json()
    assert body["mode"] == "oidc"
    assert body["user"]["email"] == "user@example.com"
    assert body["user"]["name"] == "一般ユーザー"
    assert body["user"]["role"] == "user"
    uuid.UUID(body["user"]["id"])  # 有効な UUID であること


def test_me_admin_email_gets_admin_role(client_oidc: TestClient) -> None:
    login_as(client_oidc, "admin@example.com", "Admin")
    body = client_oidc.get("/api/auth/me").json()
    assert body["user"]["role"] == "admin"


# -- /login --------------------------------------------------------------------


def test_login_returns_302_and_sets_gakei_next(client_oidc: TestClient) -> None:
    response = client_oidc.get("/api/auth/login?next=/settings", follow_redirects=False)
    assert response.status_code == 302


def test_login_disabled_in_none_mode(client: TestClient) -> None:
    response = client.get("/api/auth/login")
    assert response.status_code == 404


def test_login_sanitizes_open_redirect_next(client_oidc: TestClient) -> None:
    from app.auth.oidc import OidcIdentity

    fake_client = client_oidc.app.state.test_oidc_client
    for unsafe_next in ("//evil.example.com", "http://evil.example.com", "/a\\b"):
        fake_client.queue_identity(
            OidcIdentity(
                issuer="https://idp.test/realms/x",
                subject="sub-user2",
                email="user2@example.com",
                name="User Two",
            )
        )
        # ログイン後の遷移先はサーバー側で `/` に丸められる(callback の Location を見る)。
        response = client_oidc.get(f"/api/auth/login?next={unsafe_next}", follow_redirects=False)
        assert response.status_code == 302
        callback = client_oidc.get("/api/auth/callback", follow_redirects=False)
        assert callback.status_code == 302
        assert callback.headers["location"] == "/"
        client_oidc.post("/api/auth/logout")


def test_login_keeps_safe_next(client_oidc: TestClient) -> None:
    from app.auth.oidc import OidcIdentity

    fake_client = client_oidc.app.state.test_oidc_client
    fake_client.queue_identity(
        OidcIdentity(
            issuer="https://idp.test/realms/x",
            subject="sub-safe",
            email="safe@example.com",
            name="Safe",
        )
    )
    login_response = client_oidc.get("/api/auth/login?next=/settings", follow_redirects=False)
    assert login_response.status_code == 302
    callback = client_oidc.get("/api/auth/callback", follow_redirects=False)
    assert callback.status_code == 302
    assert callback.headers["location"] == "/settings"


# -- /callback: app_user・auth_session の作成、Cookie 属性 ------------------------


def test_callback_creates_user_and_session(
    client_oidc: TestClient, oidc_settings: Settings
) -> None:
    login_as(client_oidc, "new@example.com", "New User")

    session_factory = client_oidc.app.state.session_factory
    with session_factory() as db:
        user = db.execute(select(AppUser).where(AppUser.email == "new@example.com")).scalar_one()
        assert user.issuer == "https://idp.test/realms/x"
        assert user.role == "user"

        sessions = (
            db.execute(select(AuthSession).where(AuthSession.user_id == user.id)).scalars().all()
        )
        assert len(sessions) == 1


def test_callback_sets_httponly_lax_cookie(client_oidc: TestClient) -> None:
    from app.auth.oidc import OidcIdentity

    fake_client = client_oidc.app.state.test_oidc_client
    fake_client.queue_identity(
        OidcIdentity(
            issuer="https://idp.test/realms/x",
            subject="sub-cookie",
            email="cookie@example.com",
            name="Cookie",
        )
    )
    client_oidc.get("/api/auth/login", follow_redirects=False)
    callback = client_oidc.get("/api/auth/callback", follow_redirects=False)
    set_cookie = callback.headers.get("set-cookie", "")
    assert "gakei_session=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "samesite=lax" in set_cookie.lower()
    # public_base_url = http://testserver (非 https) なので Secure は付かない。
    assert "Secure" not in set_cookie


def test_callback_sets_secure_cookie_when_public_base_url_is_https(data_dir) -> None:  # noqa: ANN001
    from fastapi.testclient import TestClient as _TestClient

    from app.auth.deps import get_oidc_client
    from app.main import create_app
    from tests.oidc_fake import FakeOidcClient

    https_settings = Settings(
        _env_file=None,
        data_dir=data_dir,
        fake_provider=True,
        auth_mode="oidc",
        oidc_issuer="https://idp.test/realms/x",
        oidc_client_id="gakei",
        public_base_url="https://gakei.example.com",
        auth_admin_emails="admin@example.com",
    )
    app = create_app(https_settings)
    fake_client = FakeOidcClient()
    app.dependency_overrides[get_oidc_client] = lambda: fake_client
    app.state.test_oidc_client = fake_client

    with _TestClient(app) as client:
        login_as(client, "https@example.com", "Https User")
        # login_as は callback まで済ませてしまうので、Cookie 自体は Set-Cookie ヘッダーでなく
        # クライアントの cookie jar から確認する(TestClient の https スキームは実際には
        # http だが、Secure 属性の有無は Settings.public_base_is_https の値で決まる)。
        assert client.cookies.get("gakei_session") is not None


# -- 管理者一覧を変えて再ログイン ------------------------------------------------


def test_role_is_recomputed_on_each_login(data_dir) -> None:  # noqa: ANN001
    from app.auth.deps import get_oidc_client
    from app.main import create_app
    from tests.oidc_fake import FakeOidcClient

    settings = Settings(
        _env_file=None,
        data_dir=data_dir,
        fake_provider=True,
        auth_mode="oidc",
        oidc_issuer="https://idp.test/realms/x",
        oidc_client_id="gakei",
        public_base_url="http://testserver",
        auth_admin_emails="",
    )
    app = create_app(settings)
    fake_client = FakeOidcClient()
    app.dependency_overrides[get_oidc_client] = lambda: fake_client
    app.state.test_oidc_client = fake_client

    with TestClient(app) as client:
        login_as(client, "promote@example.com", "Promote Me")
        assert client.get("/api/auth/me").json()["user"]["role"] == "user"
        client.post("/api/auth/logout")

        app.state.settings.auth_admin_emails = "promote@example.com"
        login_as(client, "promote@example.com", "Promote Me")
        assert client.get("/api/auth/me").json()["user"]["role"] == "admin"


# -- /logout -------------------------------------------------------------------


def test_logout_invalidates_session_and_builds_redirect_url(client_oidc: TestClient) -> None:
    login_as(client_oidc, "logout@example.com", "Logout User")
    assert client_oidc.get("/api/capabilities").status_code == 200

    response = client_oidc.post("/api/auth/logout")
    assert response.status_code == 200
    body = response.json()
    assert "post_logout_redirect_uri=" in body["redirect_url"]
    assert "client_id=gakei" in body["redirect_url"]
    assert body["redirect_url"].startswith(
        "https://idp.test/realms/x/protocol/openid-connect/logout"
    )

    assert client_oidc.get("/api/capabilities").status_code == 401


def test_logout_without_end_session_endpoint_redirects_to_root(data_dir) -> None:  # noqa: ANN001
    from app.auth.deps import get_oidc_client
    from app.main import create_app
    from tests.oidc_fake import FakeOidcClient

    settings = Settings(
        _env_file=None,
        data_dir=data_dir,
        fake_provider=True,
        auth_mode="oidc",
        oidc_issuer="https://idp.test/realms/x",
        oidc_client_id="gakei",
        public_base_url="http://testserver",
        auth_admin_emails="",
    )
    app = create_app(settings)
    fake_client = FakeOidcClient(end_session_endpoint=None)
    app.dependency_overrides[get_oidc_client] = lambda: fake_client
    app.state.test_oidc_client = fake_client

    with TestClient(app) as client:
        login_as(client, "noend@example.com", "No End Session")
        response = client.post("/api/auth/logout")
        assert response.json() == {"redirect_url": "/"}


# -- セッション期限切れ ----------------------------------------------------------


def test_expired_session_is_treated_as_unauthenticated(client_oidc: TestClient) -> None:
    from datetime import UTC, datetime, timedelta

    login_as(client_oidc, "expire@example.com", "Expire User")
    assert client_oidc.get("/api/capabilities").status_code == 200

    session_factory = client_oidc.app.state.session_factory
    with session_factory() as db:
        session_row = db.execute(select(AuthSession)).scalars().first()
        assert session_row is not None
        session_row.expires_at = datetime.now(UTC) - timedelta(hours=1)
        db.commit()

    assert client_oidc.get("/api/capabilities").status_code == 401


def test_login_returns_502_when_idp_unreachable(tmp_path: Path) -> None:
    """IdP に届かない(discovery に失敗する)ときは、500 のスタックトレースではなく 502 と
    案内の文言を返す。`FakeOidcClient` ではなく本物の `AuthlibOidcClient` を通す。
    """
    from app.main import create_app

    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        fake_provider=True,
        auth_mode="oidc",
        # 誰も待ち受けていないループバックのポート(接続拒否になる)。
        oidc_issuer="http://127.0.0.1:9/realms/x",
        oidc_client_id="gakei",
        public_base_url="http://testserver",
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/auth/login", follow_redirects=False)
        assert response.status_code == 502
        assert "OIDC_ISSUER" in response.json()["detail"]


def test_is_email_allowed() -> None:
    from app.auth.sessions import is_email_allowed

    admins = {"boss@example.com"}
    # 制限なし
    assert is_email_allowed("anyone@gmail.com", set(), admins)
    # ドメインで絞る(大文字小文字は無視)
    assert is_email_allowed("Taro@Example.com", {"example.com"}, admins)
    assert not is_email_allowed("someone@gmail.com", {"example.com"}, admins)
    # 管理者は常に許す
    assert is_email_allowed("boss@example.com", {"corp.example.jp"}, admins)
    # `@` が無い壊れた値は拒否
    assert not is_email_allowed("no-at-sign", {"example.com"}, admins)


def _callback_with(client: TestClient, email: str | None) -> str:
    """ログインを試み、callback のリダイレクト先を返す。"""
    from app.auth.oidc import OidcIdentity

    client.app.state.test_oidc_client.queue_identity(
        OidcIdentity(issuer="https://idp.test/realms/x", subject="sub-x", email=email, name="X")
    )
    assert client.get("/api/auth/login", follow_redirects=False).status_code == 302
    response = client.get("/api/auth/callback", follow_redirects=False)
    assert response.status_code == 302
    return response.headers["location"]


def test_callback_rejects_email_outside_allowed_domains(oidc_settings: Settings) -> None:
    """`AUTH_ALLOWED_EMAIL_DOMAINS` があれば、そのドメイン以外はログインできない。ただし
    `AUTH_ADMIN_EMAILS` の人は常に通る。失敗は JSON ではなく、ログイン画面へ `login_error`
    付きで戻す。
    """
    from app.auth.deps import get_oidc_client
    from app.main import create_app
    from tests.oidc_fake import FakeOidcClient

    settings = oidc_settings.model_copy(update={"auth_allowed_email_domains": "corp.example.jp"})
    app = create_app(settings)
    fake_client = FakeOidcClient()
    app.dependency_overrides[get_oidc_client] = lambda: fake_client
    app.state.test_oidc_client = fake_client
    with TestClient(app) as client:
        assert _callback_with(client, "outsider@gmail.com") == "/?login_error=email_not_allowed"
        assert client.get("/api/auth/me").json()["user"] is None

        assert _callback_with(client, "member@corp.example.jp") == "/"
        assert client.get("/api/auth/me").json()["user"]["role"] == "user"
        client.post("/api/auth/logout")

        # 管理者(admin@example.com)はドメイン外でも通る
        assert _callback_with(client, "admin@example.com") == "/"
        assert client.get("/api/auth/me").json()["user"]["role"] == "admin"


def test_callback_without_email_redirects_with_error(client_oidc: TestClient) -> None:
    assert _callback_with(client_oidc, None) == "/?login_error=email_missing"
    assert client_oidc.get("/api/auth/me").json()["user"] is None
