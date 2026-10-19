"""`Host` と `Origin` の確認(DNS リバインディングと CSRF の対策。ADR-0019 2章、2026-10-19 追記)。

`conftest.py` は既定で `ALLOWED_HOSTS=testserver` を入れているが、ここでは `Settings` に
`allowed_hosts` を明示して、本番と同じ判定を確かめる(`testserver` は許さない)。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.domain import auth_settings
from app.http_guard import host_allowed, origin_allowed
from app.main import create_app
from tests.conftest import make_png_bytes, reload_auth_runtime

LOCAL = "http://127.0.0.1:8000"


def _none_settings(data_dir: Path, **extra: object) -> Settings:
    values: dict[str, object] = {"allowed_hosts": ""}
    values.update(extra)
    return Settings(_env_file=None, data_dir=data_dir, fake_provider=True, **values)


@pytest.fixture
def none_client(data_dir: Path) -> Iterator[TestClient]:
    app = create_app(_none_settings(data_dir, allowed_hosts="gakei.lan, Photo-Box"))
    with TestClient(app, base_url=LOCAL) as client:
        yield client


@pytest.fixture
def oidc_client(data_dir: Path) -> Iterator[TestClient]:
    settings = Settings(
        _env_file=None,
        data_dir=data_dir,
        fake_provider=True,
        auth_mode="oidc",
        oidc_issuer="https://idp.test/realms/x",
        oidc_client_id="gakei",
        public_base_url="https://gakei.example.com",
        auth_admin_emails="admin@example.com",
        allowed_hosts="",
    )
    app = create_app(settings)
    with TestClient(app, base_url="https://gakei.example.com") as client:
        yield client


def _upload(client: TestClient, headers: dict[str, str] | None = None):  # noqa: ANN202
    return client.post(
        "/api/assets",
        files={"file": ("a.png", make_png_bytes(16, 16), "image/png")},
        data={"kind": "upload"},
        headers=headers or {},
    )


# -- Host(個人モード) --------------------------------------------------------------


@pytest.mark.parametrize("path", ["/api/health", "/api/about", "/", "/static/x.js", "/s/abc"])
def test_none_mode_rejects_unknown_host_with_421(none_client: TestClient, path: str) -> None:
    response = none_client.get(path, headers={"Host": "evil.example"})
    assert response.status_code == 421
    assert "ALLOWED_HOSTS" in response.json()["detail"]


def test_none_mode_rejects_unknown_host_on_mcp(none_client: TestClient) -> None:
    response = none_client.post("/mcp", json={}, headers={"Host": "evil.example:8000"})
    assert response.status_code == 421


def test_421_detail_follows_accept_language(none_client: TestClient) -> None:
    response = none_client.get(
        "/api/health", headers={"Host": "evil.example", "Accept-Language": "en"}
    )
    assert response.status_code == 421
    assert "ALLOWED_HOSTS" in response.json()["detail"]
    assert "host name" in response.json()["detail"]


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1:8000",
        "127.0.0.1",
        "localhost",
        "localhost:8000",
        "LOCALHOST:8000",
        "[::1]:8000",
        "[::1]",
        "192.168.1.10:8000",
        "gakei.lan:8000",  # ALLOWED_HOSTS
        "GAKEI.LAN",  # 大文字小文字は無視
        "photo-box",
    ],
)
def test_none_mode_allows_ip_localhost_and_allowed_hosts(
    none_client: TestClient, host: str
) -> None:
    response = none_client.get("/api/health", headers={"Host": host})
    assert response.status_code == 200, response.text


@pytest.mark.parametrize(
    "host",
    ["testserver", "evil@127.0.0.1", "127.0.0.1.evil.example", "gakei.lan.evil.example", ""],
)
def test_none_mode_rejects_lookalike_hosts(none_client: TestClient, host: str) -> None:
    assert none_client.get("/api/health", headers={"Host": host}).status_code == 421


def test_none_mode_allows_public_base_url_host(data_dir: Path) -> None:
    app = create_app(_none_settings(data_dir, public_base_url="https://gakei.example.com"))
    with TestClient(app, base_url="https://gakei.example.com") as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/health", headers={"Host": "other.example"}).status_code == 421


def test_host_allowed_rules() -> None:
    assert host_allowed("127.0.0.1:8000", None, ()) is True
    assert host_allowed("[fe80::1]:8000", None, ()) is True
    assert host_allowed(None, None, ()) is False
    assert host_allowed("", None, ()) is False
    assert host_allowed("gakei.example.com", "https://gakei.example.com/", ()) is True
    assert host_allowed("gakei.example.com.", None, ("gakei.example.com",)) is True
    assert host_allowed("evil.example", None, ("gakei.example.com",)) is False


# -- Origin(個人モード) ------------------------------------------------------------


def test_cross_origin_multipart_upload_is_403(none_client: TestClient) -> None:
    response = _upload(none_client, {"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert response.json()["detail"]


def test_cross_origin_bodyless_post_is_403(none_client: TestClient) -> None:
    response = none_client.post(
        "/api/settings/annotation/backfill", headers={"Origin": "https://evil.example"}
    )
    assert response.status_code == 403


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
def test_cross_origin_other_unsafe_methods_are_403(none_client: TestClient, method: str) -> None:
    response = none_client.request(
        method, "/api/settings/openai-base-url", headers={"Origin": "https://evil.example"}
    )
    assert response.status_code == 403


def test_same_origin_upload_is_allowed(none_client: TestClient) -> None:
    assert _upload(none_client, {"Origin": LOCAL}).status_code == 201


def test_same_origin_with_allowed_hostname_is_allowed(none_client: TestClient) -> None:
    headers = {"Host": "gakei.lan:8000", "Origin": "http://gakei.lan:8000"}
    assert _upload(none_client, headers).status_code == 201


def test_upload_without_origin_is_allowed(none_client: TestClient) -> None:
    assert _upload(none_client).status_code == 201


def test_bodyless_post_without_origin_passes_the_guard(none_client: TestClient) -> None:
    # エンジンが無いので 409 だが、ガードには止められない。
    response = none_client.post("/api/settings/annotation/backfill")
    assert response.status_code not in (403, 421)


def test_null_origin_is_403(none_client: TestClient) -> None:
    assert _upload(none_client, {"Origin": "null"}).status_code == 403


def test_get_with_foreign_origin_is_allowed(none_client: TestClient) -> None:
    response = none_client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert response.status_code == 200


def test_origin_allowed_with_allowed_hosts() -> None:
    assert origin_allowed("http://gakei.lan:8000", "gakei.lan:8000", None, ("gakei.lan",))
    assert not origin_allowed("http://gakei.lan:8000", "gakei.lan:8000", None, ())
    assert not origin_allowed("http://gakei.lan:8000", "gakei.lan:9000", None, ("gakei.lan",))


# -- oidc モード -------------------------------------------------------------------


@pytest.mark.parametrize("host", ["gakei.example.com", "internal-proxy:8000", "evil.example"])
def test_oidc_mode_does_not_check_host(oidc_client: TestClient, host: str) -> None:
    assert oidc_client.get("/api/health", headers={"Host": host}).status_code == 200


def test_oidc_mode_rejects_foreign_origin(oidc_client: TestClient) -> None:
    response = oidc_client.post("/api/auth/logout", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403


def test_oidc_mode_allows_public_base_url_origin(oidc_client: TestClient) -> None:
    response = oidc_client.post("/api/auth/logout", headers={"Origin": "https://gakei.example.com"})
    assert response.status_code not in (403, 421)


# -- 再起動なしの切り替え(ADR-0034) ---------------------------------------------------


def test_mode_switch_is_reflected_without_restart(data_dir: Path) -> None:
    settings = _none_settings(
        data_dir,
        oidc_issuer="https://idp.test/realms/x",
        oidc_client_id="gakei",
        public_base_url="https://gakei.example.com",
        auth_admin_emails="admin@example.com",
    )
    app = create_app(settings)
    with TestClient(app, base_url=LOCAL) as client:
        evil = {"Host": "evil.example"}
        assert client.get("/api/health", headers=evil).status_code == 421

        with app.state.session_factory() as db:
            auth_settings.set_value(db, auth_settings.MODE_KEY, "oidc")
            db.commit()
        reload_auth_runtime(client)
        assert app.state.auth_runtime.is_oidc
        assert client.get("/api/health", headers=evil).status_code == 200

        with app.state.session_factory() as db:
            auth_settings.set_value(db, auth_settings.MODE_KEY, "none")
            db.commit()
        reload_auth_runtime(client)
        assert client.get("/api/health", headers=evil).status_code == 421
