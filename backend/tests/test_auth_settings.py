"""ADR-0034: 認証の設定を管理者設定の画面から行う。

実 IdP・実ネットワークには出ない。OIDC クライアントは `get_oidc_client_factory` /
`get_oidc_client` を `FakeOidcClient` に、Discovery 文書の取得は `get_discovery_fetcher` を
差し替える。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.auth_settings import DiscoveryError, get_discovery_fetcher
from app.auth.deps import get_oidc_client, get_oidc_client_factory
from app.auth.oidc import OidcIdentity
from app.auth.runtime import AuthRuntime, get_auth_runtime
from app.config import Settings
from app.domain import auth_settings
from app.domain.api_key import read_secret_field
from app.domain.models import AuthSession
from app.main import AuthConfigError, create_app
from tests.conftest import login_as
from tests.oidc_fake import FakeOidcClient

ISSUER = "https://idp.test/realms/x"
ADMIN = "admin@example.com"


def _discovery(issuer: str) -> dict:
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/auth",
        "token_endpoint": f"{issuer}/token",
        "jwks_uri": f"{issuer}/certs",
    }


class _Harness:
    """差し替えた OIDC クライアントと Discovery の取得。"""

    def __init__(self) -> None:
        self.fake = FakeOidcClient()
        self.built_for: list[auth_settings.OidcConnection] = []
        self.discovery_error: DiscoveryError | None = None
        self.discovery_document: dict | None = None

    def factory(self, connection: auth_settings.OidcConnection) -> FakeOidcClient:
        self.built_for.append(connection)
        return self.fake

    async def fetch(self, issuer: str) -> dict:
        if self.discovery_error is not None:
            raise self.discovery_error
        return self.discovery_document or _discovery(issuer)

    def install(self, app) -> None:  # noqa: ANN001
        harness = self

        def _client(runtime: AuthRuntime = Depends(get_auth_runtime)) -> FakeOidcClient | None:
            # 実効のモードが oidc のときだけ(本物の `get_oidc_client` と同じ)。
            return harness.fake if runtime.is_oidc else None

        app.dependency_overrides[get_oidc_client] = _client
        app.dependency_overrides[get_oidc_client_factory] = lambda: harness.factory
        app.dependency_overrides[get_discovery_fetcher] = lambda: harness.fetch
        app.state.test_oidc_client = self.fake


def _app_client(settings: Settings, harness: _Harness) -> TestClient:
    app = create_app(settings)
    harness.install(app)
    return TestClient(app)


def _settings(data_dir: Path, **overrides: object) -> Settings:
    base: dict[str, object] = {"_env_file": None, "data_dir": data_dir, "fake_provider": True}
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def harness() -> _Harness:
    return _Harness()


@pytest.fixture
def client_none(data_dir: Path, harness: _Harness) -> Iterator[TestClient]:
    """個人モード(`.env` に AUTH_MODE なし)。画面から有効にする流れのテスト用。"""
    with _app_client(_settings(data_dir), harness) as client:
        yield client


def _register(client: TestClient, **overrides: object) -> dict:
    body: dict[str, object] = {
        "issuer": ISSUER,
        "client_id": "gakei",
        "scopes": "openid profile email",
        "public_base_url": "http://testserver",
        "client_secret": "s3cr3t-value-123",
    }
    body.update(overrides)
    response = client.put("/api/settings/auth/connection", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def _test_login(client: TestClient, email: str | None, **identity: object) -> dict:
    """テストログイン(ポップアップの往復)。callback が返す postMessage の中身を返す。"""
    client.app.state.test_oidc_client.queue_identity(
        OidcIdentity(
            issuer=identity.get("issuer", ISSUER),  # type: ignore[arg-type]
            subject=identity.get("subject", f"sub-{email}"),  # type: ignore[arg-type]
            email=email,
            name="Tester",
            email_verified=identity.get("email_verified"),  # type: ignore[arg-type]
        )
    )
    start = client.get("/api/auth/test-login", follow_redirects=False)
    assert start.status_code == 302, start.text
    assert start.headers["location"] == "http://testserver/api/auth/callback"
    return _callback_result(client)


def _callback_result(client: TestClient) -> dict:
    response = client.get("/api/auth/callback", follow_redirects=False)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-store"
    text = response.text
    start = text.index("var r=") + len("var r=")
    end = text.index(";if(window.opener)")
    return json.loads(text[start:end])


def _patch(client: TestClient, **body: object):  # noqa: ANN202
    return client.patch("/api/settings/auth", json=body)


def _enable_from_none(client: TestClient) -> None:
    _register(client)
    assert _patch(client, admin_emails=[ADMIN]).status_code == 200
    assert _test_login(client, ADMIN) == {"type": "gakei-auth-test", "ok": True}
    response = _patch(client, mode="oidc")
    assert response.status_code == 200, response.text


# -- 優先順位(ADR-0034 1章) ---------------------------------------------------------


def test_defaults_without_env_or_db(db_session_factory, tmp_path: Path) -> None:  # noqa: ANN001
    with db_session_factory() as db:
        config = auth_settings.resolve_auth_config(db, _settings(tmp_path / "d"))
    assert (config.mode, config.mode_source, config.mode_locked) == ("none", "default", False)
    assert config.scopes.value == auth_settings.DEFAULT_SCOPES
    assert config.scopes.source == "default"
    assert config.session_hours.value == 720
    assert config.connection is None
    assert auth_settings.enable_blockers(config) == ["no_connection", "admin_emails_empty"]


def test_env_values_are_initial_values_and_db_wins(db_session_factory, tmp_path: Path) -> None:  # noqa: ANN001
    settings = _settings(
        tmp_path / "d",
        oidc_issuer="https://env.example/realms/x",
        oidc_client_id="env-client",
        oidc_client_secret="env-secret",
        public_base_url="https://gakei.example",
        auth_admin_emails="Boss@Example.com, boss@example.com",
        auth_session_hours=12,
    )
    with db_session_factory() as db:
        config = auth_settings.resolve_auth_config(db, settings)
        assert config.issuer.value == "https://env.example/realms/x"
        assert config.issuer.source == "env"
        assert config.admin_emails.value == ["boss@example.com"]
        assert config.admin_emails.source == "env"
        assert (config.session_hours.value, config.session_hours.source) == (12, "env")
        assert (config.client_secret, config.client_secret_source) == ("env-secret", "env")
        assert config.public_base_is_https

        auth_settings.set_value(db, auth_settings.ISSUER_KEY, "https://db.example/realms/y")
        auth_settings.set_value(db, auth_settings.ADMIN_EMAILS_KEY, ["db@example.com"])
        auth_settings.set_value(db, auth_settings.MODE_KEY, "oidc")
        db.commit()
        config = auth_settings.resolve_auth_config(db, settings)
    assert (config.issuer.value, config.issuer.source) == ("https://db.example/realms/y", "setting")
    assert config.client_id.source == "env"
    assert config.admin_emails.value == ["db@example.com"]
    assert (config.mode, config.mode_source, config.mode_locked) == ("oidc", "setting", False)
    # 発行者が DB にあれば、シークレットは secrets.json だけから読む(画面の public client を
    # `.env` のシークレットが上書きしないように)。
    assert config.client_secret is None


def test_explicit_env_auth_mode_locks_and_wins_over_db(
    db_session_factory,
    tmp_path: Path,  # noqa: ANN001
) -> None:
    with db_session_factory() as db:
        auth_settings.set_value(db, auth_settings.MODE_KEY, "oidc")
        db.commit()
        config = auth_settings.resolve_auth_config(db, _settings(tmp_path / "d", auth_mode="none"))
    assert (config.mode, config.mode_source, config.mode_locked) == ("none", "env", True)
    assert "env_locked" in auth_settings.enable_blockers(config)


def test_secret_file_wins_over_env(db_session_factory, tmp_path: Path) -> None:  # noqa: ANN001
    data_dir = tmp_path / "d"
    data_dir.mkdir()
    from app.domain.api_key import write_secret_field

    write_secret_field(data_dir, auth_settings.SECRET_FIELD, "file-secret")
    with db_session_factory() as db:
        config = auth_settings.resolve_auth_config(
            db, _settings(data_dir, oidc_client_secret="env-secret")
        )
    assert (config.client_secret, config.client_secret_source) == ("file-secret", "file")


# -- GET と権限 -----------------------------------------------------------------------


def test_get_shape_in_none_mode(client_none: TestClient) -> None:
    body = client_none.get("/api/settings/auth").json()
    assert body["mode"] == {"value": "none", "source": "default", "locked": False}
    assert body["connection"]["configured"] is False
    assert body["connection"]["client_secret"] == {"configured": False, "source": None}
    assert body["connection"]["redirect_uri"] is None
    assert body["pending"] is None
    assert body["verified"] is None
    assert body["admin_emails"] == {"value": [], "source": "default"}
    assert body["session_hours"] == {"value": 720, "source": "default", "min": 1, "max": 720}
    assert body["enable_blockers"] == ["no_connection", "admin_emails_empty"]


def test_secret_never_appears_in_responses(data_dir: Path, harness: _Harness) -> None:
    settings = _settings(
        data_dir,
        oidc_issuer=ISSUER,
        oidc_client_id="gakei",
        oidc_client_secret="env-secret-ABCDEFG",
        public_base_url="http://testserver",
    )
    with _app_client(settings, harness) as client:
        body = _register(client, client_secret="pending-secret-HIJKLMN")
        text = client.get("/api/settings/auth").text
        for secret in ("env-secret-ABCDEFG", "pending-secret-HIJKLMN"):
            assert secret not in text
            assert secret not in json.dumps(body)
        assert body["connection"]["client_secret"] == {"configured": True, "source": "env"}
        assert body["pending"]["client_secret"] == {"configured": True, "source": "file"}
        # DB にも置かない。
        with client.app.state.session_factory() as db:
            from app.domain.models import AppSetting

            for row in db.execute(select(AppSetting)).scalars():
                assert "pending-secret" not in json.dumps(row.value)
        assert (
            read_secret_field(data_dir, auth_settings.PENDING_SECRET_FIELD)
            == "pending-secret-HIJKLMN"
        )


def test_non_admin_gets_403(client_oidc: TestClient) -> None:
    login_as(client_oidc, "user@example.com", "User")
    assert client_oidc.get("/api/settings/auth").status_code == 403
    assert client_oidc.patch("/api/settings/auth", json={"session_hours": 5}).status_code == 403
    assert client_oidc.get("/api/auth/test-login", follow_redirects=False).status_code == 403


def test_unauthenticated_test_login_is_401_in_oidc_mode(client_oidc: TestClient) -> None:
    assert client_oidc.get("/api/auth/test-login", follow_redirects=False).status_code == 401
    assert client_oidc.get("/api/settings/auth").status_code == 401


# -- 仮登録 -------------------------------------------------------------------------------


def test_register_connection_validates_urls_and_scopes(client_none: TestClient) -> None:
    def put(**overrides: object):  # noqa: ANN202
        body: dict[str, object] = {
            "issuer": ISSUER,
            "client_id": "gakei",
            "scopes": "openid email",
            "public_base_url": "http://testserver",
        }
        body.update(overrides)
        return client_none.put("/api/settings/auth/connection", json=body)

    for overrides, field in (
        ({"issuer": "idp.test"}, "issuer"),
        ({"public_base_url": "http://"}, "public_base_url"),
        ({"client_id": "  "}, "client_id"),
        ({"scopes": "profile email"}, "scopes"),
    ):
        response = put(**overrides)
        assert response.status_code == 422, overrides
        assert response.json()["detail"]["code"] == "invalid_value"
        assert response.json()["detail"]["field"] == field
    assert client_none.get("/api/settings/auth").json()["pending"] is None


def test_register_connection_reports_discovery_errors(
    client_none: TestClient, harness: _Harness
) -> None:
    harness.discovery_error = DiscoveryError("failed", "ConnectError")
    response = client_none.put(
        "/api/settings/auth/connection",
        json={
            "issuer": ISSUER,
            "client_id": "gakei",
            "scopes": "openid",
            "public_base_url": "http://testserver",
        },
    )
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "discovery_failed"
    assert "ConnectError" in response.json()["detail"]["message"]

    harness.discovery_error = None
    harness.discovery_document = {"issuer": ISSUER}
    response = client_none.put(
        "/api/settings/auth/connection",
        json={
            "issuer": ISSUER,
            "client_id": "gakei",
            "scopes": "openid",
            "public_base_url": "http://testserver",
        },
        headers={"Accept-Language": "en"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "discovery_invalid"
    assert "discovery document" in response.json()["detail"]["message"]
    assert client_none.get("/api/settings/auth").json()["pending"] is None


def test_register_connection_secret_keep_clear_replace(
    client_none: TestClient, data_dir: Path
) -> None:
    body = _register(client_none, client_secret="first-secret")
    assert body["pending"]["client_secret"]["configured"] is True
    assert body["pending"]["redirect_uri"] == "http://testserver/api/auth/callback"

    # 省略: 今の仮登録のものを引き継ぐ。
    body = _register(client_none, client_id="other", client_secret=None)
    assert body["pending"]["client_id"] == "other"
    assert read_secret_field(data_dir, auth_settings.PENDING_SECRET_FIELD) == "first-secret"

    # 空文字: public client。
    body = _register(client_none, client_secret="")
    assert body["pending"]["client_secret"] == {"configured": False, "source": None}
    assert read_secret_field(data_dir, auth_settings.PENDING_SECRET_FIELD) is None

    # 取り消し。
    response = client_none.delete("/api/settings/auth/connection/pending")
    assert response.status_code == 200
    assert response.json()["pending"] is None


def test_pending_does_not_change_effective_connection(client_none: TestClient) -> None:
    _register(client_none)
    body = client_none.get("/api/settings/auth").json()
    assert body["connection"]["configured"] is False
    assert "no_connection" in body["enable_blockers"]
    response = _patch(client_none, admin_emails=[ADMIN], mode="oidc")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "no_connection"
    # 途中まで保存しない。
    assert client_none.get("/api/settings/auth").json()["admin_emails"]["value"] == []


# -- テストログイン --------------------------------------------------------------------------


def test_test_login_without_connection_reports_no_connection(client_none: TestClient) -> None:
    response = client_none.get("/api/auth/test-login", follow_redirects=False)
    assert response.status_code == 200
    assert '"error": "no_connection"' in response.text


def test_test_login_rejects_non_admin_email_and_keeps_pending(client_none: TestClient) -> None:
    _register(client_none)
    result = _test_login(client_none, "someone@example.com")
    assert result == {"type": "gakei-auth-test", "ok": False, "error": "email_not_admin"}
    body = client_none.get("/api/settings/auth").json()
    assert body["pending"] is not None
    assert body["verified"] is None
    assert body["connection"]["configured"] is False


@pytest.mark.parametrize(
    ("email", "email_verified", "error"),
    [(None, None, "email_missing"), (None, False, "email_unverified")],
)
def test_test_login_email_errors(
    client_none: TestClient, email: str | None, email_verified: bool | None, error: str
) -> None:
    _register(client_none)
    _patch(client_none, admin_emails=[ADMIN])
    result = _test_login(client_none, email, email_verified=email_verified)
    assert result["error"] == error


def test_test_login_fails_when_config_changes_midway(client_none: TestClient) -> None:
    _register(client_none)
    _patch(client_none, admin_emails=[ADMIN])
    client_none.app.state.test_oidc_client.queue_identity(
        OidcIdentity(issuer=ISSUER, subject="s", email=ADMIN, name="A")
    )
    assert client_none.get("/api/auth/test-login", follow_redirects=False).status_code == 302
    _register(client_none, client_id="changed")
    assert _callback_result(client_none)["error"] == "config_changed"
    assert client_none.get("/api/settings/auth").json()["verified"] is None


def test_test_login_callback_failure(client_none: TestClient, harness: _Harness) -> None:
    from fastapi import HTTPException

    _register(client_none)
    _patch(client_none, admin_emails=[ADMIN])

    async def boom(request):  # noqa: ANN001, ANN202
        raise HTTPException(status_code=400, detail="x")

    harness.fake.complete_login = boom  # type: ignore[method-assign]
    assert client_none.get("/api/auth/test-login", follow_redirects=False).status_code == 302
    assert _callback_result(client_none)["error"] == "callback_failed"


def test_successful_test_login_promotes_pending_and_records_verified(
    client_none: TestClient, data_dir: Path, harness: _Harness
) -> None:
    _register(client_none, client_secret="promoted-secret")
    _patch(client_none, admin_emails=[ADMIN])
    assert _test_login(client_none, "Admin@Example.com")["ok"] is True
    # 仮登録の設定でクライアントを作っている。
    assert harness.built_for[-1].client_secret == "promoted-secret"

    body = client_none.get("/api/settings/auth").json()
    assert body["pending"] is None
    assert body["connection"]["configured"] is True
    assert body["connection"]["issuer"] == {"value": ISSUER, "source": "setting"}
    assert body["connection"]["client_secret"] == {"configured": True, "source": "file"}
    assert body["connection"]["redirect_uri"] == "http://testserver/api/auth/callback"
    assert body["verified"]["email"] == ADMIN
    assert body["verified"]["matches_current"] is True
    assert body["enable_blockers"] == []
    assert read_secret_field(data_dir, auth_settings.SECRET_FIELD) == "promoted-secret"
    assert read_secret_field(data_dir, auth_settings.PENDING_SECRET_FIELD) is None
    # 個人モードの間のテストでは、セッションを作って Cookie を渡しておく。
    assert client_none.cookies.get("gakei_session") is not None
    # 個人モードのままなので、まだ誰でも使える。
    assert client_none.get("/api/auth/me").json() == {"mode": "none", "user": None}


def test_enable_requires_matching_fingerprint(client_none: TestClient) -> None:
    _register(client_none)
    _patch(client_none, admin_emails=[ADMIN])
    assert _test_login(client_none, ADMIN)["ok"] is True
    with client_none.app.state.session_factory() as db:
        auth_settings.record_verified(db, "not-the-current-fingerprint", ADMIN)
        db.commit()
    body = client_none.get("/api/settings/auth").json()
    assert body["verified"]["matches_current"] is False
    assert body["enable_blockers"] == ["not_verified"]
    response = _patch(client_none, mode="oidc")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "not_verified"


def test_enable_requires_verified_email_in_saved_admins(client_none: TestClient) -> None:
    _register(client_none)
    _patch(client_none, admin_emails=[ADMIN])
    assert _test_login(client_none, ADMIN)["ok"] is True
    response = _patch(client_none, mode="oidc", admin_emails=["other@example.com"])
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "verified_email_not_admin"
    response = _patch(client_none, mode="oidc", admin_emails=[])
    assert response.json()["detail"]["code"] == "admin_emails_empty"
    body = client_none.get("/api/settings/auth").json()
    assert body["mode"]["value"] == "none"
    assert body["admin_emails"]["value"] == [ADMIN]


# -- 有効化から無効化まで(再起動なし) -------------------------------------------------------


def test_enable_and_disable_without_restart(client_none: TestClient) -> None:
    client = client_none
    assert client.get("/docs").status_code == 200
    _enable_from_none(client)

    body = client.get("/api/settings/auth").json()
    assert body["mode"] == {"value": "oidc", "source": "setting", "locked": False}
    assert client.get("/api/auth/me").json()["user"]["role"] == "admin"

    # 未ログインは 401、/docs などは 404。
    cookie = client.cookies.get("gakei_session")
    client.cookies.clear()
    assert client.get("/api/capabilities").status_code == 401
    assert client.get("/api/auth/me").json() == {"mode": "oidc", "user": None}
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404, path
    client.cookies.set("gakei_session", cookie)
    assert client.get("/api/capabilities").status_code == 200

    # 自分を管理者から外すことはできない。
    response = _patch(client, admin_emails=["other@example.com"])
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "self_not_admin"
    # 他の人を足すのはよい。セッションの長さ・許可ドメインも変えられる。
    response = _patch(
        client,
        admin_emails=[ADMIN, "second@example.com"],
        allowed_email_domains=["@Example.com"],
        session_hours=48,
    )
    assert response.status_code == 200, response.text
    assert response.json()["allowed_email_domains"]["value"] == ["example.com"]
    assert client.app.state.auth_runtime.session_hours == 48

    # 通常のログインも、画面で保存した接続と管理者で動く。
    login_as(client, "second@example.com", "Second")
    assert client.get("/api/auth/me").json()["user"]["role"] == "admin"

    # 無効にする: 誰でも使える。/docs も戻る。
    assert _patch(client, mode="none").status_code == 200
    client.cookies.clear()
    assert client.get("/api/capabilities").status_code == 200
    assert client.get("/api/auth/me").json() == {"mode": "none", "user": None}
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200


def test_disable_keeps_sessions_and_reenable_uses_them(client_none: TestClient) -> None:
    client = client_none
    _enable_from_none(client)
    assert _patch(client, mode="none").status_code == 200
    with client.app.state.session_factory() as db:
        assert db.execute(select(func.count()).select_from(AuthSession)).scalar_one() == 1
    # もう一度有効にすると、同じ Cookie のまま管理者として使える。
    assert _patch(client, mode="oidc").status_code == 200
    assert client.get("/api/auth/me").json()["user"]["role"] == "admin"


def test_null_mode_resets_to_default(client_none: TestClient) -> None:
    _enable_from_none(client_none)
    response = _patch(client_none, mode=None)
    assert response.status_code == 200
    assert response.json()["mode"] == {"value": "none", "source": "default", "locked": False}


def test_test_login_while_oidc_creates_no_session(client_none: TestClient) -> None:
    client = client_none
    _enable_from_none(client)
    with client.app.state.session_factory() as db:
        before = db.execute(select(func.count()).select_from(AuthSession)).scalar_one()
    cookie = client.cookies.get("gakei_session")

    # 接続を差し替える(テストに成功するまで差し替わらない)。
    _register(client, client_id="replaced")
    assert client.get("/api/settings/auth").json()["connection"]["client_id"]["value"] == "gakei"
    assert _test_login(client, ADMIN)["ok"] is True
    body = client.get("/api/settings/auth").json()
    assert body["connection"]["client_id"]["value"] == "replaced"
    assert body["verified"]["matches_current"] is True

    with client.app.state.session_factory() as db:
        after = db.execute(select(func.count()).select_from(AuthSession)).scalar_one()
    assert after == before
    assert client.cookies.get("gakei_session") == cookie
    assert client.get("/api/auth/me").json()["user"]["role"] == "admin"


def test_session_hours_validation(client_none: TestClient) -> None:
    for value in (0, 721, True):
        response = _patch(client_none, session_hours=value)
        assert response.status_code == 422, value
    assert _patch(client_none, session_hours=5).json()["session_hours"]["value"] == 5
    body = _patch(client_none, session_hours=None).json()
    assert body["session_hours"] == {"value": 720, "source": "default", "min": 1, "max": 720}


def test_invalid_admin_email_is_422(client_none: TestClient) -> None:
    response = _patch(client_none, admin_emails=["not-an-email"])
    assert response.status_code == 422
    assert response.json()["detail"]["field"] == "admin_emails"


# -- `.env` との関係 -----------------------------------------------------------------------


def test_env_locked_mode_cannot_be_changed(client_oidc: TestClient) -> None:
    login_as(client_oidc, ADMIN, "Admin")
    body = client_oidc.get("/api/settings/auth").json()
    assert body["mode"] == {"value": "oidc", "source": "env", "locked": True}
    assert "env_locked" in body["enable_blockers"]
    response = _patch(client_oidc, mode="none")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "env_locked"
    # 同じ値なら何もしない。
    assert _patch(client_oidc, mode="oidc").status_code == 200


def test_enable_copies_env_values_to_db(data_dir: Path, harness: _Harness) -> None:
    settings = _settings(
        data_dir,
        oidc_issuer=ISSUER,
        oidc_client_id="gakei",
        oidc_client_secret="env-secret",
        public_base_url="http://testserver",
        auth_admin_emails=ADMIN,
    )
    with _app_client(settings, harness) as client:
        body = client.get("/api/settings/auth").json()
        assert body["connection"]["issuer"]["source"] == "env"
        assert body["enable_blockers"] == ["not_verified"]
        # 仮登録が無ければ、本登録(`.env` の値)でテストする。
        assert _test_login(client, ADMIN)["ok"] is True
        assert harness.built_for[-1].client_secret == "env-secret"
        assert _patch(client, mode="oidc").status_code == 200

        body = client.get("/api/settings/auth").json()
        assert body["connection"]["issuer"] == {"value": ISSUER, "source": "setting"}
        assert body["admin_emails"] == {"value": [ADMIN], "source": "setting"}
        assert body["connection"]["client_secret"] == {"configured": True, "source": "file"}
        assert body["verified"]["matches_current"] is True
        assert read_secret_field(data_dir, auth_settings.SECRET_FIELD) == "env-secret"

    # `.env` から消しても動く。
    with _app_client(_settings(data_dir), harness) as client:
        body = client.get("/api/auth/me").json()
        assert body["mode"] == "oidc"


def test_env_only_oidc_keeps_working(client_oidc: TestClient) -> None:
    """`.env` だけで oidc を使っている既存の利用者は、何もしなくても今までどおり動く。"""
    assert client_oidc.get("/api/capabilities").status_code == 401
    login_as(client_oidc, ADMIN, "Admin")
    assert client_oidc.get("/api/auth/me").json()["user"]["role"] == "admin"


# -- 起動時の検査 ---------------------------------------------------------------------------


def test_startup_aborts_when_db_oidc_is_incomplete(data_dir: Path, harness: _Harness) -> None:
    with _app_client(_settings(data_dir), harness) as client:
        with client.app.state.session_factory() as db:
            auth_settings.set_value(db, auth_settings.MODE_KEY, "oidc")
            db.commit()

    with pytest.raises(AuthConfigError) as exc_info:
        with _app_client(_settings(data_dir), harness):
            pass
    assert "AUTH_MODE=none" in str(exc_info.value)

    # 緊急の無効化: `.env` に AUTH_MODE=none を書けば起動できる(DB の設定は消さない)。
    with _app_client(_settings(data_dir, auth_mode="none"), harness) as client:
        body = client.get("/api/settings/auth").json()
        assert body["mode"] == {"value": "none", "source": "env", "locked": True}
        assert client.get("/api/capabilities").status_code == 200


def test_startup_uses_db_enabled_oidc(data_dir: Path, harness: _Harness) -> None:
    with _app_client(_settings(data_dir), harness) as client:
        _enable_from_none(client)
    with TestClient(create_app(_settings(data_dir))) as client:
        assert client.get("/api/auth/me").json() == {"mode": "oidc", "user": None}
        assert client.get("/api/capabilities").status_code == 401
        assert client.get("/docs").status_code == 404


def test_gakei_oidc_cookie_is_secure_when_public_base_is_https(
    data_dir: Path, harness: _Harness
) -> None:
    settings = _settings(
        data_dir,
        auth_mode="oidc",
        oidc_issuer=ISSUER,
        oidc_client_id="gakei",
        public_base_url="https://gakei.example.com",
    )
    with _app_client(settings, harness) as client:
        response = client.get("/api/auth/login", follow_redirects=False)
        set_cookie = response.headers.get("set-cookie", "")
        assert "gakei_oidc=" in set_cookie
        assert "secure" in set_cookie.lower()


def test_gakei_oidc_cookie_is_not_secure_over_http(client_oidc: TestClient) -> None:
    response = client_oidc.get("/api/auth/login", follow_redirects=False)
    set_cookie = response.headers.get("set-cookie", "")
    assert "gakei_oidc=" in set_cookie
    assert "secure" not in set_cookie.lower()
