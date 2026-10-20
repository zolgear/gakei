"""応答のセキュリティのヘッダー(Issue #166。`app/security_headers.py`)。"""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import Settings
from app.security_headers import DOCS_CSP, SPA_CSP

_COMMON = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
}


def _assert_common(response, *, referrer: str = "same-origin") -> None:  # noqa: ANN001
    for name, value in _COMMON.items():
        assert response.headers.get(name) == value, (response.request.url, name)
    assert response.headers.get("referrer-policy") == referrer, response.request.url


@pytest.fixture
def spa_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, data_dir: Path
) -> Iterator[TestClient]:
    """ビルド済みの SPA(index.html と /static の JS)があるアプリ。"""
    import app.main as app_main
    from app.main import create_app

    dist_dir = tmp_path / "frontend-dist"
    (dist_dir / "static").mkdir(parents=True)
    (dist_dir / "index.html").write_text("<html>gakei spa</html>", encoding="utf-8")
    (dist_dir / "static" / "index-abc.js").write_text("console.log(1)", encoding="utf-8")
    monkeypatch.setattr(app_main, "_FRONTEND_DIST", dist_dir)
    app = create_app(Settings(_env_file=None, data_dir=data_dir, fake_provider=True))
    with TestClient(app) as test_client:
        yield test_client


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


def test_spa_html_has_csp_and_common_headers(spa_client: TestClient) -> None:
    for path in ("/", "/stock", f"/assets/{'0' * 8}"):
        response = spa_client.get(path)
        assert response.status_code == 200, path
        assert response.headers["content-security-policy"] == SPA_CSP
        _assert_common(response)


def test_spa_csp_forbids_framing_and_inline_scripts() -> None:
    directives = dict(
        (part.split(" ", 1) + [""])[:2] for part in (p.strip() for p in SPA_CSP.split(";"))
    )
    assert directives["frame-ancestors"] == "'none'"
    assert directives["script-src"] == "'self'"
    assert directives["object-src"] == "'none'"


def test_static_and_api_responses_have_common_headers_without_csp(
    spa_client: TestClient,
) -> None:
    js = spa_client.get("/static/index-abc.js")
    assert js.status_code == 200
    _assert_common(js)
    assert "content-security-policy" not in js.headers

    api = spa_client.get("/api/health")
    assert api.status_code == 200
    _assert_common(api)
    assert "content-security-policy" not in api.headers

    missing = spa_client.get("/api/no-such-route")
    assert missing.status_code == 404
    _assert_common(missing)


def test_asset_content_has_nosniff(client: TestClient) -> None:
    upload = client.post(
        "/api/assets", files={"file": ("a.png", _png(), "image/png")}, data={"kind": "upload"}
    )
    assert upload.status_code == 201, upload.text
    response = client.get(f"/api/assets/{upload.json()['id']}/content?variant=original")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    _assert_common(response)


def test_share_pages_keep_no_referrer(spa_client: TestClient) -> None:
    """ADR-0029 6章: 共有のページと公開の API は `no-referrer` のまま(上書きしない)。"""
    page = spa_client.get("/s/abc")
    assert page.status_code == 200
    _assert_common(page, referrer="no-referrer")
    assert page.headers["content-security-policy"] == SPA_CSP

    api = spa_client.get("/api/public/shares/abc")
    _assert_common(api, referrer="no-referrer")


def test_rejected_by_host_guard_still_has_headers(client: TestClient) -> None:
    response = client.get("/api/health", headers={"host": "evil.example"})
    assert response.status_code == 421
    _assert_common(response)


def test_docs_use_their_own_csp(client: TestClient) -> None:
    for path in ("/docs", "/redoc"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.headers["content-security-policy"] == DOCS_CSP
        _assert_common(response)


def test_security_headers_can_be_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, data_dir: Path
) -> None:
    """`SECURITY_HEADERS=false`: リバースプロキシで付ける人向けに、GAKEI 側では付けない。"""
    import app.main as app_main
    from app.main import create_app

    dist_dir = tmp_path / "frontend-dist"
    dist_dir.mkdir()
    (dist_dir / "index.html").write_text("<html>gakei spa</html>", encoding="utf-8")
    monkeypatch.setattr(app_main, "_FRONTEND_DIST", dist_dir)
    settings = Settings(
        _env_file=None, data_dir=data_dir, fake_provider=True, SECURITY_HEADERS=False
    )
    app = create_app(settings)
    with TestClient(app) as test_client:
        for path in ("/", "/api/health", "/docs"):
            response = test_client.get(path)
            assert response.status_code == 200, path
            for name in (
                "x-content-type-options",
                "x-frame-options",
                "referrer-policy",
                "content-security-policy",
            ):
                assert name not in response.headers, (path, name)
        # 共有のページの `no-referrer` は ADR-0029 の決めごとなので残る。
        share = test_client.get("/s/abc")
        assert share.headers["referrer-policy"] == "no-referrer"
