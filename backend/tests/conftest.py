"""pytest 共通フィクスチャ。DATA_DIR を一時ディレクトリに切り替え、FakeProvider で完結させる。

ADR-0027 6章: 環境変数 `GAKEI_TEST_DATABASE_URL`(PostgreSQL のサーバーへの接続。例
`postgresql://postgres:gakei@127.0.0.1:55433/postgres`)があれば、テストごとに一意な名前の
一時 DB を作り、`DATABASE_URL` に入れて PostgreSQL で走らせる(終わったら DROP する)。
無ければこれまでどおり SQLite(`DATA_DIR/gakei.db`)。
"""

from __future__ import annotations

import io
import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.config import Settings, normalize_database_url
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


# --- DB の切り替え(ADR-0027 6章) ------------------------------------------------

_PG_SERVER_URL = os.environ.get("GAKEI_TEST_DATABASE_URL", "").strip() or None

requires_postgresql = pytest.mark.skipif(
    _PG_SERVER_URL is None, reason="GAKEI_TEST_DATABASE_URL(PostgreSQL)が無い"
)
requires_sqlite = pytest.mark.skipif(
    _PG_SERVER_URL is not None, reason="SQLite(ファイル)に固有のテスト"
)


def using_postgresql() -> bool:
    return _PG_SERVER_URL is not None


def _pg_admin_engine():  # noqa: ANN202
    assert _PG_SERVER_URL is not None
    return create_engine(
        normalize_database_url(_PG_SERVER_URL), isolation_level="AUTOCOMMIT", pool_pre_ping=True
    )


def _pg_database_url(name: str) -> str:
    assert _PG_SERVER_URL is not None
    url = make_url(normalize_database_url(_PG_SERVER_URL)).set(database=name)
    return url.render_as_string(hide_password=False)


def _unique_db_name(prefix: str) -> str:
    # pytest-xdist の並列でも衝突しないよう、worker 名と uuid を入れる(63 バイト以内)。
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    return f"{prefix}_{worker}_{uuid.uuid4().hex[:16]}"


def _create_pg_database(name: str, template: str | None = None) -> str:
    engine = _pg_admin_engine()
    try:
        with engine.connect() as conn:
            clause = f' TEMPLATE "{template}"' if template else ""
            conn.execute(text(f'CREATE DATABASE "{name}"{clause}'))
    finally:
        engine.dispose()
    return _pg_database_url(name)


def _drop_pg_database(name: str) -> None:
    engine = _pg_admin_engine()
    try:
        with engine.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def _pg_template_database() -> Iterator[str | None]:
    """PostgreSQL のとき、最新のスキーマまで上げた雛形の DB(worker ごとに1つ)。

    テストごとの DB はこれを TEMPLATE にして作る(毎回 0001 から upgrade するより速い)。
    """
    if _PG_SERVER_URL is None:
        yield None
        return
    from app.main import run_migrations

    name = _unique_db_name("gakei_tmpl")
    url = _create_pg_database(name)
    try:
        run_migrations(url)
        yield name
    finally:
        _drop_pg_database(name)


@pytest.fixture
def pg_empty_database_url() -> Iterator[str]:
    """PostgreSQL の空の一時 DB(マイグレーションや移行ツールのテスト用)。PG が無ければ skip。"""
    if _PG_SERVER_URL is None:
        pytest.skip("GAKEI_TEST_DATABASE_URL(PostgreSQL)が無い")
    name = _unique_db_name("gakei_test_empty")
    url = _create_pg_database(name)
    try:
        yield url
    finally:
        _drop_pg_database(name)


@pytest.fixture
def empty_database_url(tmp_path: Path) -> Iterator[str]:
    """テーブルが1つも無い DB の URL。PostgreSQL なら一時 DB、SQLite なら一時ファイル。"""
    if _PG_SERVER_URL is None:
        yield f"sqlite:///{tmp_path / 'existing.db'}"
        return
    name = _unique_db_name("gakei_test_empty")
    url = _create_pg_database(name)
    try:
        yield url
    finally:
        _drop_pg_database(name)


# このテストの間に `extra_database_url()` で作った DB(テストの終わりにまとめて DROP する)。
_extra_databases: list[str] = []
_current_template: list[str] = []


def extra_database_url() -> str | None:
    """別の GAKEI インスタンス用に、もう1つの DB を用意する(PostgreSQL のとき)。

    SQLite のときは None(インスタンスごとの `DATA_DIR/gakei.db` を使うので要らない)。
    PostgreSQL のときは、作った DB の URL を返す。呼び出し側で `DATABASE_URL` に入れる。
    """
    if not _current_template:
        return None
    name = _unique_db_name("gakei_test_extra")
    url = _create_pg_database(name, template=_current_template[0])
    _extra_databases.append(name)
    return url


@pytest.fixture(autouse=True)
def _test_database(
    monkeypatch: pytest.MonkeyPatch, _pg_template_database: str | None
) -> Iterator[str | None]:
    """テストごとの DB を `DATABASE_URL` に入れる(PostgreSQL のとき)。SQLite のときは
    `DATABASE_URL` を消す(開発者の環境変数を持ち込まない)。値は PostgreSQL の URL か None。
    """
    if _pg_template_database is None:
        monkeypatch.delenv("DATABASE_URL", raising=False)
        yield None
        return
    name = _unique_db_name("gakei_test")
    url = _create_pg_database(name, template=_pg_template_database)
    monkeypatch.setenv("DATABASE_URL", url)
    _current_template[:] = [_pg_template_database]
    try:
        yield url
    finally:
        _drop_pg_database(name)
        while _extra_databases:
            _drop_pg_database(_extra_databases.pop())


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
def db_session_factory(tmp_path: Path, _test_database: str | None) -> Iterator[sessionmaker]:
    """HTTP を介さずドメイン層を直接テストしたい場合の DB。PostgreSQL のときはテストごとの
    一時 DB(雛形からの複製で、スキーマは作成済み)。"""
    engine = make_engine(_test_database or (tmp_path / "domain_data" / "gakei.db"))
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
