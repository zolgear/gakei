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
from dataclasses import dataclass
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
    # ADR-0038: 実物の SD WebUI にも接続しない。
    monkeypatch.setenv("SDWEBUI_URL", "")


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


def reload_auth_runtime(client: TestClient) -> None:
    """`app.state.settings` の認証の値を書き換えたテストで、実効の設定(ADR-0034 の
    `AuthRuntime`)を読み直す(本番では設定 API の保存が読み直す)。"""
    app = client.app
    with app.state.session_factory() as db:
        app.state.auth_runtime.reload(db)


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


def wait_for_background_reads(client: TestClient, timeout: float = 10.0) -> None:
    """裏の worker が原本を読み終えるまで待つ。テストで原本・派生のファイルを動かす・消す・
    上書きする前に呼ぶ。

    取り込みの後、焦点の worker(ADR-0043)が原本を `open_content` で開いて顔を探す。
    Windows では開いているファイルを rename・unlink できず、`PermissionError`(WinError 32)
    になる。アップロードは応答を返す前に worker へ渡すので、worker が空(`is_idle`)になれば
    読み終えている。生成の出力は、runner が commit してから worker へ渡すまでの間に Run の
    完了が見えることがあるので、マスク以外の Asset に焦点の行がそろうまでも待つ(行の無い
    Asset が残っても、worker が空のまま `grace` 秒たてば、渡されないものとみなして戻る)。

    自動タイトル・タグと埋め込みの worker は、設定でオンにしない限り原本を読まない。それを
    オンにするテストは、それぞれの状態(`auto_status` など)が終わるのを待つこと。
    """
    import time

    from sqlalchemy import exists, select

    from app.domain.models import Asset, AssetFocalPoint, AssetKind

    grace = 0.3
    worker = client.app.state.focal_worker
    session_factory = client.app.state.session_factory
    deadline = time.monotonic() + timeout
    idle_since: float | None = None
    while time.monotonic() < deadline:
        if worker.is_idle():
            with session_factory() as session:
                missing = session.scalar(
                    select(
                        exists().where(
                            Asset.kind != AssetKind.MASK,
                            ~exists().where(AssetFocalPoint.asset_id == Asset.id),
                        )
                    )
                )
            if not missing:
                return
            now = time.monotonic()
            if idle_since is None:
                idle_since = now
            elif now - idle_since >= grace:
                return
        else:
            idle_since = None
        time.sleep(0.02)
    raise TimeoutError("焦点の worker が時間内に終わりませんでした")


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


# --- オブジェクトストレージ(ADR-0028 7章) ------------------------------------------
#
# S3: 既定は moto のサーバーをテストの中で立てる(worker ごとに1つ。空きポートを取る)。
# `GAKEI_TEST_S3_ENDPOINT_URL` ほかがあれば、moto の代わりにそこ(Cloudflare R2 など)へつなぐ。
# Azure Blob: `GAKEI_TEST_AZURE_BLOB_CONNECTION_STRING`(Azurite など)があるときだけ回す。
# どちらも、テストごとにランダムな接頭辞の下を使い、終わったらその下を消す。

_S3_ENV = {
    "endpoint_url": os.environ.get("GAKEI_TEST_S3_ENDPOINT_URL", "").strip(),
    "bucket": os.environ.get("GAKEI_TEST_S3_BUCKET", "").strip(),
    "access_key_id": os.environ.get("GAKEI_TEST_S3_ACCESS_KEY_ID", "").strip(),
    "secret_access_key": os.environ.get("GAKEI_TEST_S3_SECRET_ACCESS_KEY", "").strip(),
    "region": os.environ.get("GAKEI_TEST_S3_REGION", "").strip(),
}
_AZURE_BLOB_CONNECTION_STRING = (
    os.environ.get("GAKEI_TEST_AZURE_BLOB_CONNECTION_STRING", "").strip() or None
)
# Azurite で使うコンテナ(無ければテストが作る。本番の GAKEI はコンテナを作らない)
AZURE_TEST_CONTAINER = "gakei-test"
MOTO_TEST_BUCKET = "gakei-test"

requires_azure_blob = pytest.mark.skipif(
    _AZURE_BLOB_CONNECTION_STRING is None,
    reason="GAKEI_TEST_AZURE_BLOB_CONNECTION_STRING(Azurite など)が無い",
)


@dataclass(frozen=True)
class S3TestTarget:
    """テストでつなぐ S3 互換ストレージ(moto か実機)。"""

    endpoint_url: str
    bucket: str
    access_key_id: str
    secret_access_key: str
    region: str
    is_moto: bool

    def store(self, prefix: str = "") -> Any:
        from app.domain.object_storage import S3Store

        return S3Store.create(
            self.bucket,
            region=self.region,
            endpoint_url=self.endpoint_url,
            force_path_style=self.is_moto,
            access_key_id=self.access_key_id,
            secret_access_key=self.secret_access_key,
            prefix=prefix,
        )


@pytest.fixture(scope="session")
def s3_target() -> Iterator[S3TestTarget]:
    if _S3_ENV["endpoint_url"]:
        missing = [k for k in ("bucket", "access_key_id", "secret_access_key") if not _S3_ENV[k]]
        if missing:
            pytest.fail(f"GAKEI_TEST_S3_* が足りない: {missing}")
        yield S3TestTarget(
            endpoint_url=_S3_ENV["endpoint_url"],
            bucket=_S3_ENV["bucket"],
            access_key_id=_S3_ENV["access_key_id"],
            secret_access_key=_S3_ENV["secret_access_key"],
            region=_S3_ENV["region"] or "auto",
            is_moto=False,
        )
        return

    from moto.server import ThreadedMotoServer

    server = ThreadedMotoServer(ip_address="127.0.0.1", port=0, verbose=False)
    server.start()
    try:
        host, port = server.get_host_and_port()
        target = S3TestTarget(
            endpoint_url=f"http://{host}:{port}",
            bucket=MOTO_TEST_BUCKET,
            access_key_id="testing",
            secret_access_key="testing",
            region="us-east-1",
            is_moto=True,
        )
        target.store()._client.create_bucket(Bucket=target.bucket)
        yield target
    finally:
        server.stop()


def _test_prefix() -> str:
    return f"gakei-pytest/{uuid.uuid4().hex}/"


def _cleanup_s3_prefix(store: Any) -> None:
    paginator = store._client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=store.bucket, Prefix=store.prefix):
        keys = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
        if keys:
            store._client.delete_objects(Bucket=store.bucket, Delete={"Objects": keys})


@pytest.fixture
def s3_store(s3_target: S3TestTarget) -> Iterator[Any]:
    """テストごとにランダムな接頭辞の下を使う S3Store。終わったら接頭辞の下を消す。"""
    store = s3_target.store(prefix=_test_prefix())
    try:
        yield store
    finally:
        _cleanup_s3_prefix(store)


@pytest.fixture
def azure_blob_store() -> Iterator[Any]:
    """テストごとにランダムな接頭辞の下を使う AzureBlobStore。接続先が無ければ skip。"""
    if _AZURE_BLOB_CONNECTION_STRING is None:
        pytest.skip("GAKEI_TEST_AZURE_BLOB_CONNECTION_STRING(Azurite など)が無い")
    from azure.core.exceptions import ResourceExistsError

    from app.domain.object_storage import AzureBlobStore

    store = AzureBlobStore.from_connection_string(
        _AZURE_BLOB_CONNECTION_STRING, AZURE_TEST_CONTAINER, prefix=_test_prefix()
    )
    try:
        store._container.create_container()
    except ResourceExistsError:
        pass
    try:
        yield store
    finally:
        for blob in store._container.list_blobs(name_starts_with=store.prefix):
            store._container.delete_blob(blob.name)


@pytest.fixture(params=["local", "s3", "azure_blob"])
def any_store(request: pytest.FixtureRequest, tmp_path: Path) -> Any:
    """ストアの共通の振る舞いのテスト用(ローカルFS / S3 / Azure Blob)。"""
    if request.param == "local":
        return LocalFsStore(tmp_path / "store")
    if request.param == "s3":
        return request.getfixturevalue("s3_store")
    return request.getfixturevalue("azure_blob_store")
