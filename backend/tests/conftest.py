"""pytest 共通フィクスチャ。DATA_DIR を一時ディレクトリに切り替え、FakeProvider で完結させる。"""

from __future__ import annotations

import io
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db import create_all, make_engine, make_session_factory
from app.domain.storage import LocalFsStore


def make_png_bytes(
    width: int = 64, height: int = 64, color: tuple[int, int, int] = (200, 30, 30)
) -> bytes:
    """テスト用のPNGバイト列を作る。"""
    image = Image.new("RGB", (width, height), color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def _isolate_from_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    """開発者の `.env`(本物の OPENAI_API_KEY など)をテストに持ち込まない。"""
    from app.config import Settings

    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    # ADR-0017: PROVIDER はもう使わない。FAKE_PROVIDER も明示的な指定が無い限り無効にする
    # (`client` / `client_no_runner` は Settings を直接注入するので、この2つが env 経由に
    # なることはない)。
    monkeypatch.delenv("PROVIDER", raising=False)
    monkeypatch.delenv("FAKE_PROVIDER", raising=False)
    # ADR-0013: テストは実物の ComfyUI(既定 127.0.0.1:8188)に接続しない。
    monkeypatch.setenv("COMFYUI_URL", "")


@pytest.fixture(autouse=True)
def _console_locale_is_japanese(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR-0015: ランチャー(コンソール)の言語は OS ロケールで決まる。テスト環境の実際の
    ロケールに関わらず、既存のテスト(日本語のメッセージを前提にしている)が決定的に
    通るよう、ここで明示的に日本語へ固定する。"""
    monkeypatch.setenv("LC_ALL", "ja_JP.UTF-8")


@pytest.fixture(autouse=True)
def _isolate_from_frontend_dist(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`frontend/dist` の有無(ビルド済みかどうか)でテスト結果が変わらないようにする。"""
    import app.main as app_main

    monkeypatch.setattr(app_main, "_FRONTEND_DIST", tmp_path / "no-frontend-dist")


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


def _fake_settings(data_dir: Path) -> Settings:
    """ADR-0017: 自動テストは環境変数を経由せず、`create_app` に設定を渡して fake を使う。"""
    return Settings(_env_file=None, data_dir=data_dir, fake_provider=True)


@pytest.fixture
def oidc_settings(data_dir: Path) -> Settings:
    """ADR-0019: oidc モードの Settings。`admin@example.com` を管理者にしておく。"""
    return Settings(
        _env_file=None,
        data_dir=data_dir,
        fake_provider=True,
        auth_mode="oidc",
        oidc_issuer="https://idp.test/realms/x",
        oidc_client_id="gakei",
        public_base_url="http://testserver",
        auth_admin_emails="admin@example.com",
    )


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, data_dir: Path) -> Iterator[TestClient]:
    """runner が実際に動く、素の状態のアプリ。"""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    from app.main import create_app

    app = create_app(_fake_settings(data_dir))
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def client_no_runner(monkeypatch: pytest.MonkeyPatch, data_dir: Path) -> Iterator[TestClient]:
    """runner のループを止めたアプリ。queued/running の状態を自分で制御したいテスト用。"""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    from app.worker.runner import Runner

    async def _noop_start(self: Runner) -> int:  # noqa: ANN001
        return 0

    monkeypatch.setattr(Runner, "start", _noop_start)

    from app.main import create_app

    app = create_app(_fake_settings(data_dir))
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def client_oidc(oidc_settings: Settings) -> Iterator[TestClient]:
    """ADR-0019: oidc モードのアプリ。`get_oidc_client` を `FakeOidcClient` に差し替えてある
    (`api/settings.py::get_key_validator` と同じ、Depends 差し替えの流儀)。`login_as()` と
    組み合わせて使う。
    """
    from app.auth.deps import get_oidc_client
    from app.main import create_app
    from tests.oidc_fake import FakeOidcClient

    app = create_app(oidc_settings)
    fake_client = FakeOidcClient()
    app.dependency_overrides[get_oidc_client] = lambda: fake_client
    # login_as() がここから取り出して queue_identity() する(テスト専用の置き場所)。
    app.state.test_oidc_client = fake_client
    with TestClient(app) as test_client:
        yield test_client


def login_as(client: TestClient, email: str, name: str = "Test User") -> None:
    """`/api/auth/login` → `/api/auth/callback` の一連の流れを踏んでログイン済みにする
    (`client_oidc` 専用。以後の `client` のリクエストは Cookie `gakei_session` を自動で送る)。
    """
    from app.auth.oidc import OidcIdentity

    fake_client = client.app.state.test_oidc_client
    fake_client.queue_identity(
        OidcIdentity(
            issuer="https://idp.test/realms/x", subject=f"sub-{email}", email=email, name=name
        )
    )
    login_response = client.get("/api/auth/login", follow_redirects=False)
    assert login_response.status_code == 302, login_response.text
    callback_response = client.get("/api/auth/callback", follow_redirects=False)
    assert callback_response.status_code == 302, callback_response.text


@pytest.fixture
def db_session_factory(tmp_path: Path) -> Iterator[sessionmaker]:
    """HTTP を介さずドメイン層を直接テストしたい場合の DB。"""
    db_path = tmp_path / "domain_data" / "gakei.db"
    engine = make_engine(db_path)
    create_all(engine)
    factory = make_session_factory(engine)
    yield factory
    engine.dispose()


@pytest.fixture
def local_store(tmp_path: Path) -> LocalFsStore:
    return LocalFsStore(tmp_path / "domain_data")


def wait_for_run_terminal(
    client: TestClient, run_id: uuid.UUID | str, timeout: float = 5.0
) -> dict:
    """run が終了状態になるまで待って、その時点の GET /api/runs/{id} を返す。"""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/runs/{run_id}")
        response.raise_for_status()
        body = response.json()
        if body["status"] in ("succeeded", "failed", "canceled"):
            return body
        time.sleep(0.02)
    raise TimeoutError(f"run {run_id} が時間内に終了しませんでした")


@contextmanager
def swapped_primary_provider(client: TestClient, provider: Any) -> Iterator[None]:
    """ADR-0013: レジストリの主プロバイダーを一時的にスタブへ差し替える。

    runner は実行のたびに `registry.get(name)` で引く(レーン開始時点の参照をキャッシュ
    しない)ので、この差し替えだけで実行中の Run にも反映される。
    """
    registry = client.app.state.registry
    name = registry.primary
    original = registry.providers[name]
    registry.providers[name] = provider
    try:
        yield
    finally:
        registry.providers[name] = original
