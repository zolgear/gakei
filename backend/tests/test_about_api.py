"""`GET /api/about`、`GET /api/about/third-party-notices`(ADR-0021 3・4章)。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.version import get_version
from tests.conftest import login_as


def test_get_about_returns_pyproject_version(client: TestClient) -> None:
    response = client.get("/api/about")
    assert response.status_code == 200
    body = response.json()
    assert body["version"] == get_version()


def test_get_about_commit_is_null_when_unset(client: TestClient) -> None:
    """`client` フィクスチャの設定は `gakei_commit` を指定していないので null。"""
    response = client.get("/api/about")
    assert response.status_code == 200
    assert response.json()["commit"] is None


def test_get_about_commit_reflects_setting(monkeypatch: pytest.MonkeyPatch, data_dir: Path) -> None:
    """`GAKEI_COMMIT`(Settings 経由)があればそのまま返す。フロント側での7桁への切り詰めは
    ここでは行わない(ADR-0021 3章: `GET /api/about` は commit をそのまま返す)。
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from app.main import create_app

    settings = Settings(
        _env_file=None, data_dir=data_dir, fake_provider=True, gakei_commit=" abc1234deadbeef \n"
    )
    app = create_app(settings)
    with TestClient(app) as test_client:
        response = test_client.get("/api/about")
    assert response.status_code == 200
    assert response.json()["commit"] == "abc1234deadbeef"


def test_third_party_notices_is_plain_text_and_lists_python_packages(
    client: TestClient,
) -> None:
    response = client.get("/api/about/third-party-notices")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert "GAKEI third-party notices" in response.text
    assert "Python packages" in response.text
    assert "fastapi" in response.text


def test_third_party_notices_includes_frontend_txt_when_present(
    monkeypatch: pytest.MonkeyPatch, data_dir: Path, tmp_path: Path
) -> None:
    import app.main as app_main
    from app.main import create_app

    dist_dir = tmp_path / "frontend-dist"
    dist_dir.mkdir()
    (dist_dir / "third-party-notices.txt").write_text(
        "GAKEI frontend third-party notices\n\nreact@19.3.0 — MIT — https://react.dev/\n",
        encoding="utf-8",
    )
    # `_isolate_from_frontend_dist`(autouse)が既に差し替えた値を、このテスト専用の実在する
    # ディレクトリに上書きする(このテストの monkeypatch は autouse フィクスチャより後に
    # 評価されるので、こちらが有効になる)。
    monkeypatch.setattr(app_main, "_FRONTEND_DIST", dist_dir)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    settings = Settings(_env_file=None, data_dir=data_dir, fake_provider=True)
    app = create_app(settings)
    with TestClient(app) as test_client:
        response = test_client.get("/api/about/third-party-notices")

    assert response.status_code == 200
    assert "Frontend packages" in response.text
    assert "react@19.3.0 — MIT — https://react.dev/" in response.text


def test_third_party_notices_reports_missing_frontend_dist(client: TestClient) -> None:
    """`client` フィクスチャは存在しない dist を指す(`_isolate_from_frontend_dist`)ので、
    frontend 未ビルドの案内文になる。
    """
    response = client.get("/api/about/third-party-notices")
    assert "third-party-notices.txt was not found" in response.text


def test_third_party_notices_tool_writes_same_content_to_stdout(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from app.domain.third_party import render_notices
    from app.tools import third_party_notices

    expected = render_notices(third_party_notices._FRONTEND_DIST.resolve())

    monkeypatch.setattr(sys, "argv", ["third_party_notices", "-"])
    third_party_notices.main()

    captured = capsys.readouterr()
    assert captured.out == expected


def test_about_requires_login_in_oidc_mode(client_oidc: TestClient) -> None:
    """`/api/about` も他の API と同じくログインが要る(ADR-0019)。"""
    response = client_oidc.get("/api/about")
    assert response.status_code == 401

    login_as(client_oidc, "about-user@example.com")
    response = client_oidc.get("/api/about")
    assert response.status_code == 200
