"""FastAPI アプリ本体。lifespan で DB マイグレーションと worker(Runner)を起動する。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from alembic import command
from alembic.config import Config
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.annotation.wd_models import WdModelDownloader
from app.api import about as about_api
from app.api import api_tokens as api_tokens_api
from app.api import asset_groups as asset_groups_api
from app.api import assets as assets_api
from app.api import auth as auth_api
from app.api import auth_settings as auth_settings_api
from app.api import capabilities as capabilities_api
from app.api import comfyui as comfyui_api
from app.api import downloads as downloads_api
from app.api import embeddings as embeddings_api
from app.api import events as events_api
from app.api import health as health_api
from app.api import llm_connections as llm_connections_api
from app.api import pricing as pricing_api
from app.api import prompt_sets as prompt_sets_api
from app.api import runs as runs_api
from app.api import search as search_api
from app.api import settings as settings_api
from app.api import shares as shares_api
from app.api import tags as tags_api
from app.api import uploads as uploads_api
from app.api import users as users_api
from app.api.request_text import reject_nul_in_request
from app.auth.deps import require_user
from app.auth.runtime import AuthRuntime
from app.auth.secret import load_or_create_auth_secret
from app.config import Settings, display_database_url, get_settings
from app.db import make_engine, make_session_factory
from app.domain import annotation_settings, embedding_index
from app.domain.api_key import resolve_base_url, warn_if_insecure_base_url
from app.domain.auth_settings import EffectiveAuthConfig, resolve_auth_config
from app.domain.semantic_search import QueryVectorCache
from app.domain.storage import open_store
from app.domain.vector_index import build_index as build_vector_index
from app.embedding.catalog import ClipModelDownloader
from app.i18n import console_t, parse_accept_language, set_locale, t
from app.mcp.endpoint import McpEndpoint
from app.mcp.server import build_mcp_server, build_session_manager
from app.providers.registry import _is_loopback_url, build_registry
from app.worker.annotator import Annotator
from app.worker.embedder import Embedder
from app.worker.progress import ProgressBus
from app.worker.runner import Runner

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = _BACKEND_DIR / "migrations"
_FRONTEND_DIST = _BACKEND_DIR.parent / "frontend" / "dist"

logger = logging.getLogger(__name__)


class LegacyProviderAbortedError(RuntimeError):
    """ADR-0017 移行の安全策。`PROVIDER` に `openai` 以外の値(`fake` など)が設定されたまま
    起動しようとしたときに送出する。`FAKE_PROVIDER=1` を使うよう案内する。
    """


def check_legacy_provider_env(settings: Settings) -> None:
    """`PROVIDER` はもう使わない。設定されたまま起動した場合の移行安全策(ADR-0017)。

    - `PROVIDER=openai`: 従来と同じ動作なので、無視して警告するだけ。
    - それ以外(`fake` など): `PROVIDER=fake` のつもりで実 API に課金される事故を防ぐため、
      黙って OpenAI で起動せず、起動を中止する。
    """
    if settings.legacy_provider is None:
        return
    if settings.legacy_provider == "openai":
        logger.warning(console_t("app.legacyProviderIgnored"))
        return
    raise LegacyProviderAbortedError(
        console_t("app.legacyProviderAborted", value=settings.legacy_provider)
    )


class AuthConfigError(RuntimeError):
    """ADR-0019・0034: 実効の認証モードが oidc なのに、必須の値が欠けている・不正な場合。"""


def check_auth_env(config: EffectiveAuthConfig | Settings) -> None:
    """実効の設定(ADR-0034 4章。起動時は DB を読んだ後)が oidc なら、必要な値が揃っているか
    検査する。`Settings` を渡したときは `.env` と既定だけから決めた設定で検査する。

    `none` モードは何も要らないので常に通る。IdP が実際に落ちていても discovery は
    初回ログイン時まで行わないため、ここでは値の有無と形だけを見る。
    """
    if isinstance(config, Settings):
        config = resolve_auth_config(None, config)
    if config.mode != "oidc":
        return

    # モードが画面の設定(DB)から来ているときは、`.env` に AUTH_MODE=none を書いて再起動すれば
    # 起動できることを案内する(ADR-0034 4章)。
    from_settings = config.mode_source == "setting"
    missing = [
        name
        for name, valued in (
            ("OIDC_ISSUER", config.issuer),
            ("OIDC_CLIENT_ID", config.client_id),
            ("PUBLIC_BASE_URL", config.public_base_url),
        )
        if not valued.value
    ]
    if missing:
        joined = ", ".join(missing)
        if from_settings:
            raise AuthConfigError(console_t("app.authConfigMissingFromSettings", missing=joined))
        raise AuthConfigError(console_t("app.authConfigMissing", missing=joined))

    # L-4(2026-09-27 追記): 両方とも値はあるので、形式(スキームとホスト)を検査する。
    # スキームが http/https でない、あるいはホストが無いものは起動を中止する
    # (redirect_uri の組み立てや Cookie の Secure 判定が壊れた値のまま起動しないため)。
    # ループバック以外への http は、動作はするが平文になるため警告に留める。
    for name, value in (
        ("PUBLIC_BASE_URL", config.public_base_url.value),
        ("OIDC_ISSUER", config.issuer.value),
    ):
        assert value is not None  # 上の missing チェックを通過済み
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            message = console_t("app.authUrlInvalid", name=name, value=value)
            if from_settings:
                message += " " + console_t("app.authEmergencyDisable")
            raise AuthConfigError(message)
        if parsed.scheme == "http" and not _is_loopback_url(value):
            logger.warning(console_t("app.authUrlInsecure", name=name, value=value))


class DatabaseUnavailableError(RuntimeError):
    """ADR-0027 1章: 起動時に DB(主に `DATABASE_URL` の PostgreSQL)へ接続できない場合。"""


def check_database_connection(settings: Settings) -> None:
    """DB に1回つないでみて、だめなら分かる文言で `DatabaseUnavailableError` を送出する。

    SQLite(`DATABASE_URL` 未指定)はファイルを作るだけなので検査しない。接続 URL を文言に
    含めるときはパスワードを伏せる。
    """
    if settings.uses_sqlite:
        return
    from sqlalchemy import text
    from sqlalchemy.exc import SQLAlchemyError

    url = settings.sqlalchemy_url
    try:
        engine = make_engine(url)
    except Exception as exc:  # noqa: BLE001 - URL の形式やドライバの誤りも同じ案内にする
        # URL を解釈できない場合の例外の文言には URL(パスワードを含む)がそのまま入るので、
        # 例外の種類だけを出す。
        raise DatabaseUnavailableError(
            console_t(
                "app.databaseUnavailable",
                url=display_database_url(url),
                error=type(exc).__name__,
            )
        ) from exc
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise DatabaseUnavailableError(
            console_t(
                "app.databaseUnavailable",
                url=display_database_url(url),
                error=_first_line(getattr(exc, "orig", None) or exc),
            )
        ) from exc
    finally:
        engine.dispose()


def _first_line(exc: BaseException) -> str:
    lines = str(exc).strip().splitlines()
    return lines[0] if lines else type(exc).__name__


class DatabaseTooNewError(RuntimeError):
    """ADR-0027 1章(Issue #84): DB がこの版より新しい GAKEI で移行されている場合。

    DB のリビジョンがこの版のマイグレーションに無いので、`upgrade head` を実行せず、DB にも
    触れずに止める。
    """


def unknown_revisions(connection: Connection) -> list[str]:
    """DB の `alembic_version` にあるリビジョンのうち、この版のマイグレーションに無いもの。

    空の DB(`alembic_version` が無い)と、この版が知っている古いリビジョンなら空のリスト。
    読むだけで、DB には何も書かない。
    """
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    heads = MigrationContext.configure(connection).get_current_heads()
    if not heads:
        return []
    # URL は使わない(スクリプトの置き場所だけを見る)。
    script = ScriptDirectory.from_config(alembic_config("sqlite://"))
    known = {revision.revision for revision in script.walk_revisions()}
    return sorted(head for head in heads if head not in known)


def database_too_new_message(label: str, revisions: list[str]) -> str:
    """`DatabaseTooNewError` などの文言。`label` は DB を示す表示用の文字列(接続 URL なら
    `display_database_url` でパスワードを伏せたもの、SQLite ならファイルのパスでもよい)。"""
    from alembic.script import ScriptDirectory

    from app.version import get_version

    head = ScriptDirectory.from_config(alembic_config("sqlite://")).get_current_head()
    return console_t(
        "app.databaseTooNew",
        url=label,
        revision=", ".join(revisions),
        version=get_version(),
        head=head or "-",
    )


def check_database_revision(url: str) -> None:
    """DB のリビジョンがこの版のマイグレーションに含まれるかを確かめる(読むだけ)。

    含まれなければ `DatabaseTooNewError`。SQLite のファイルがまだ無ければ(初回の起動)何も
    しない(つなぐとファイルができてしまうため)。
    """
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url

    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite":
        database = parsed.database
        if not database or database == ":memory:" or not Path(database).is_file():
            return
    # 読むだけなので、PRAGMA(WAL への切り替えなど)を付けずに開く。
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            revisions = unknown_revisions(connection)
    finally:
        engine.dispose()
    if revisions:
        raise DatabaseTooNewError(database_too_new_message(display_database_url(url), revisions))


def run_migrations(url: str) -> None:
    """Alembic の `upgrade head` を実行する。起動時のほか、`app.tools.backfill_embedded_meta`
    のようにサーバーを立てずに DB だけ用意したいツールからも呼べるよう公開する。

    `url` は `Settings.sqlalchemy_url`(SQLite / PostgreSQL。ADR-0027)。DB がより新しい
    GAKEI で移行されていれば、何も変えずに `DatabaseTooNewError` を送出する(Issue #84)。
    """
    check_database_revision(url)
    command.upgrade(alembic_config(url), "head")


def alembic_config(url: str) -> Config:
    """この URL の DB に対する Alembic の設定(マイグレーションのテストや移行ツールでも使う)。"""
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    # Config は configparser の補間を通すので、パスワードに含まれうる `%` を逃がす。
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def _build_lifespan(settings: Settings):
    """`create_app` で解決済みの `settings` を受け取る(ミドルウェアの構築時にも同じ値が
    要るため、`create_app` の中で1回だけ `get_settings()` する。ADR-0019)。
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        check_legacy_provider_env(settings)

        settings.data_dir.mkdir(parents=True, exist_ok=True)
        # ADR-0027 1章: PostgreSQL に接続できなければ、分かる文言で起動を中止する。
        check_database_connection(settings)
        run_migrations(settings.sqlalchemy_url)

        # ADR-0017 2章: ループバック以外への http の接続先は、保存時だけでなく起動時にも警告する。
        base_url, _source = resolve_base_url(settings)
        if base_url:
            warn_if_insecure_base_url(base_url)

        engine = make_engine(settings.sqlalchemy_url)
        session_factory = make_session_factory(engine)
        # ADR-0034 4章: 実効の認証の設定を DB から読み、それに対して起動時の検査をする。
        auth_runtime = AuthRuntime(settings)
        try:
            with session_factory() as session:
                auth_runtime.reload(session)
            check_auth_env(auth_runtime.config)
        except BaseException:
            engine.dispose()
            raise
        # ADR-0024 8章: 推定の接続先1組の設定を、接続先の一覧と用途ごとの組に移す(冪等)。
        with session_factory() as session:
            annotation_settings.migrate_legacy(session, settings)
        # ADR-0028 2章: 画像の保存先。接続・読み書きできなければ、分かる文言で起動を中止する
        # (`StorageUnavailableError`)。
        store = open_store(settings)
        registry = build_registry(settings, session_factory)
        progress_bus = ProgressBus()
        # ADR-0024: 自動タイトル・タグの推定の worker と、ONNX タガーのモデルのダウンロード。
        annotator = Annotator(session_factory, store, settings)
        wd_downloader = WdModelDownloader(settings.data_dir, fake=settings.fake_provider)
        # ADR-0033: 画像の埋め込み。PostgreSQL では pgvector を使えるかを見て、使うモデルの
        # 索引を作る。
        embedding_index_backend = embedding_index.prepare(engine)
        embedder = Embedder(
            session_factory,
            store,
            settings,
            pgvector=embedding_index_backend == embedding_index.BACKEND_PGVECTOR,
            db_engine=engine,
        )
        if embedder.pgvector:
            embedder.ensure_active_index()
        # ADR-0033 6章: 近傍検索。numpy の方式は、worker がベクトルを書くたびに上げる版で
        # メモリの行列を読み直す。
        vector_index = build_vector_index(embedding_index_backend, embedder.version)
        clip_downloader = ClipModelDownloader(settings.data_dir, fake=settings.fake_provider)
        runner = Runner(
            session_factory,
            store,
            registry,
            progress_bus,
            settings.data_dir,
            annotator=annotator,
            embedder=embedder,
        )

        app.state.settings = settings
        app.state.engine = engine
        app.state.session_factory = session_factory
        app.state.store = store
        app.state.registry = registry
        app.state.progress_bus = progress_bus
        app.state.runner = runner
        app.state.annotator = annotator
        app.state.wd_downloader = wd_downloader
        app.state.embedder = embedder
        app.state.clip_downloader = clip_downloader
        app.state.embedding_index_backend = embedding_index_backend
        app.state.vector_index = vector_index
        app.state.query_vector_cache = QueryVectorCache()
        app.state.auth_runtime = auth_runtime

        # ADR-0023: `/mcp` のセッションマネージャー。`run()` は1インスタンスにつき1回しか
        # 呼べないので、lifespan のたびに作り直す(テストで同じアプリを複数回起動するため)。
        mcp_session_manager = build_session_manager(build_mcp_server())
        app.state.mcp_session_manager = mcp_session_manager

        await runner.start()
        await annotator.start()
        await embedder.start()
        try:
            async with mcp_session_manager.run():
                yield
        finally:
            await runner.stop()
            await annotator.stop()
            await embedder.stop()
            await wd_downloader.stop()
            await clip_downloader.stop()
            engine.dispose()

    return lifespan


class LocaleMiddleware:
    """`Accept-Language` からリクエストの言語(ja/en)を選び、`app.i18n` の ContextVar に
    設定する純粋な ASGI ミドルウェア(ADR-0015)。

    `BaseHTTPMiddleware` は使わない。SSE(`text/event-stream`)のストリーミングを内部で
    バッファしてしまい、進捗配信が遅れる/途切れる原因になるため(ADR-0004 の配信方針とも
    衝突する)。ContextVar は `contextvars.Context` を通じて例外ハンドラや
    `anyio.to_thread.run_sync`(同期エンドポイント)にも引き継がれる。
    """

    def __init__(self, app) -> None:  # noqa: ANN001
        self._app = app

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        header_value: str | None = None
        for raw_name, raw_value in scope.get("headers", ()):
            if raw_name.decode("latin-1").lower() == "accept-language":
                header_value = raw_value.decode("latin-1")
                break

        set_locale(parse_accept_language(header_value))
        await self._app(scope, receive, send)


# ADR-0029 6章: 共有のページ(`/s/{トークン}`)と、その API(`/api/public/`)の応答に付ける
# ヘッダー。検索エンジンに載せない・トークンを含む URL を Referer で外に渡さない。
_PUBLIC_SHARE_PREFIXES = ("/s/", "/api/public/")
_PUBLIC_SHARE_HEADERS = (
    (b"x-robots-tag", b"noindex"),
    (b"referrer-policy", b"no-referrer"),
)


class PublicShareHeadersMiddleware:
    """共有のページと公開の API の応答(404 などのエラーも含む)にヘッダーを足す純粋な ASGI
    ミドルウェア(`LocaleMiddleware` と同じく、`BaseHTTPMiddleware` は使わない)。"""

    def __init__(self, app) -> None:  # noqa: ANN001
        self._app = app

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        if scope["type"] != "http" or not scope.get("path", "").startswith(_PUBLIC_SHARE_PREFIXES):
            await self._app(scope, receive, send)
            return

        async def send_with_headers(message) -> None:  # noqa: ANN001
            if message["type"] == "http.response.start":
                names = {name.lower() for name, _ in message.get("headers", [])}
                extra = [(k, v) for k, v in _PUBLIC_SHARE_HEADERS if k not in names]
                message = {**message, "headers": [*message.get("headers", []), *extra]}
            await send(message)

        await self._app(scope, receive, send_with_headers)


class OidcCookieSecureMiddleware:
    """`gakei_oidc`(SessionMiddleware)の Set-Cookie に、必要なら `Secure` を足す純粋な ASGI
    ミドルウェア(ADR-0034 4章)。

    以前は `SessionMiddleware(https_only=PUBLIC_BASE_URL が https か)` で起動時に決めていた。
    `PUBLIC_BASE_URL` は画面から変えられるようになったので、レスポンスのたびに実効の値で
    決める。実効の `PUBLIC_BASE_URL` が https(TLS を終端するプロキシの後ろでもブラウザから
    見て https)、またはこのリクエスト自体が https なら付ける。仮登録の値では決めない(テスト中に
    通常のログインの Cookie が http で送られなくなるのを避けるため)。
    """

    def __init__(self, app) -> None:  # noqa: ANN001
        self._app = app

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        async def send_with_secure(message) -> None:  # noqa: ANN001
            if message["type"] == "http.response.start":
                headers = message.get("headers", [])
                if any(
                    name.lower() == b"set-cookie" and value.startswith(b"gakei_oidc=")
                    for name, value in headers
                ) and _wants_secure(scope):
                    message = {
                        **message,
                        "headers": [
                            (name, value + b"; secure")
                            if name.lower() == b"set-cookie"
                            and value.startswith(b"gakei_oidc=")
                            and b"secure" not in value.lower()
                            else (name, value)
                            for name, value in headers
                        ],
                    }
            await send(message)

        await self._app(scope, receive, send_with_secure)


def _wants_secure(scope) -> bool:  # noqa: ANN001
    if scope.get("scheme") == "https":
        return True
    app = scope.get("app")
    runtime = getattr(getattr(app, "state", None), "auth_runtime", None)
    return bool(runtime is not None and runtime.public_base_is_https)


def create_app(settings: Settings | None = None) -> FastAPI:
    """`settings` を渡すと、環境変数を経由せずその設定でアプリを組み立てる(ADR-0017)。

    ミドルウェア(`SessionMiddleware`)の構築時にも `settings` が要るため、ここで1回だけ
    `get_settings()` を解決して lifespan にも同じ値を渡す(ADR-0019)。
    """
    resolved_settings = settings or get_settings()
    app = FastAPI(
        title="GAKEI ローカルMVP",
        lifespan=_build_lifespan(resolved_settings),
        # ADR-0027 2章の追記: リクエストの文字列に NUL があれば、全ルートの入口で 422 にする
        # (PostgreSQL は NUL を保存も検索もできないため。`app/api/request_text.py`)。
        dependencies=[Depends(reject_nul_in_request)],
        # /docs・/redoc・/openapi.json は下で自前のルートとして置く(モードで出し分けるため)。
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    app.add_middleware(LocaleMiddleware)
    app.add_middleware(PublicShareHeadersMiddleware)

    # JS / CSS / JSON を圧縮する。画像、フォント、SSE(text/event-stream)は
    # ミドルウェアの既定で対象外なので、進捗の配信が遅れることはない。
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    # state/nonce/PKCE の一時保存用(ADR-0019)。ログイン済みセッションの Cookie
    # (`gakei_session`)とは別物。ADR-0034 4章: モードを再起動なしで切り替えるので常に入れる
    # (手続き中でなければ中身が空なので Cookie は出ない)。`Secure` は
    # `OidcCookieSecureMiddleware` がレスポンスのたびに決める(外側に置く)。
    from starlette.middleware.sessions import SessionMiddleware

    app.add_middleware(
        SessionMiddleware,
        secret_key=load_or_create_auth_secret(resolved_settings),
        session_cookie="gakei_oidc",
        max_age=600,
        same_site="lax",
        https_only=False,
    )
    app.add_middleware(OidcCookieSecureMiddleware)

    # I-1(2026-09-27 追記)・ADR-0034 4章: oidc モードでは /docs・/redoc・/openapi.json も
    # 未ログインで読めてしまうため 404 にする(API の形はレスポンス自体からも推測できるが、
    # わざわざ一覧を公開しない)。モードは実行中に変わるので、ルートは常に置いてリクエストの
    # たびに見る(`app.tools.export_openapi` は `app.openapi()` を直接呼ぶので影響しない)。
    def _docs_available(request: Request) -> None:
        runtime = getattr(request.app.state, "auth_runtime", None)
        if runtime is None or runtime.is_oidc:
            raise HTTPException(status_code=404)

    @app.get("/openapi.json", include_in_schema=False)
    def openapi_json(request: Request) -> JSONResponse:
        _docs_available(request)
        return JSONResponse(app.openapi())

    @app.get("/docs", include_in_schema=False)
    def swagger_ui(request: Request) -> HTMLResponse:
        _docs_available(request)
        return get_swagger_ui_html(openapi_url="/openapi.json", title=f"{app.title} - Swagger UI")

    @app.get("/redoc", include_in_schema=False)
    def redoc(request: Request) -> HTMLResponse:
        _docs_available(request)
        return get_redoc_html(openapi_url="/openapi.json", title=f"{app.title} - ReDoc")

    # `/api/auth` だけは認可を付けない(未ログインでも /me・/login・/callback は呼べる必要が
    # あるため)。他のルーターは `require_user` を通す(ADR-0019。`none` モードは常に
    # `LOCAL_ADMIN` を返すので実質無効化される)。`users`(ADR-0020)は oidc モード専用だが、
    # `none` モードでの 404 化はルーター内で行うので、ここでの扱いは他と同じでよい。
    # `about`(ADR-0021)もログインが要る他の API と同じ扱いにする。
    app.include_router(auth_api.router)
    # 生存確認(Issue #43)。コンテナの HEALTHCHECK が oidc モードでも通るよう、ログイン不要。
    # 応答は `{"status": "ok"}` だけで、情報は出さない(ADR-0019 の例外)。
    app.include_router(health_api.router)

    auth_dep = [Depends(require_user)]
    app.include_router(about_api.router, dependencies=auth_dep)
    app.include_router(capabilities_api.router, dependencies=auth_dep)
    app.include_router(comfyui_api.router, dependencies=auth_dep)
    app.include_router(assets_api.router, dependencies=auth_dep)
    app.include_router(asset_groups_api.router, dependencies=auth_dep)
    app.include_router(runs_api.router, dependencies=auth_dep)
    app.include_router(events_api.router, dependencies=auth_dep)
    app.include_router(prompt_sets_api.router, dependencies=auth_dep)
    app.include_router(search_api.router, dependencies=auth_dep)
    app.include_router(embeddings_api.router, dependencies=auth_dep)
    app.include_router(tags_api.router, dependencies=auth_dep)
    app.include_router(pricing_api.router, dependencies=auth_dep)
    app.include_router(settings_api.router, dependencies=auth_dep)
    # ADR-0034: 認証の設定(各ルートで `require_admin`)。
    app.include_router(auth_settings_api.router, dependencies=auth_dep)
    app.include_router(llm_connections_api.router, dependencies=auth_dep)
    app.include_router(users_api.router, dependencies=auth_dep)
    app.include_router(api_tokens_api.router, dependencies=auth_dep)
    app.include_router(shares_api.router, dependencies=auth_dep)

    # ADR-0023 7章: 画像の本体の配信(Cookie かアクセストークン。ルーター自身が認可を掛ける)と、
    # 1回限りのアップロード URL の受け口(URL のトークン自体が認可)。いずれも `require_user`
    # の括りに入れない。
    app.include_router(assets_api.content_router)
    app.include_router(uploads_api.router)
    # ADR-0023 8章 3: 原本の1回限りのダウンロード URL(URL のトークン自体が認可)。
    app.include_router(downloads_api.router)

    # ADR-0029 6章: ログイン不要の共有リンク。`require_user` を掛けず、見せてよいかは
    # `app/domain/shares.resolve_public_share` だけで確かめる。
    app.include_router(shares_api.public_router)

    # ADR-0023: MCP サーバー(Streamable HTTP、stateless)。認証は Cookie ではなく
    # アクセストークンなので `require_user` は掛けず、`McpEndpoint` の中で行う。
    # SPA のフォールバック(`/{full_path:path}`)より先に登録する。
    app.router.add_route(
        "/mcp", McpEndpoint(), methods=["GET", "POST", "DELETE"], include_in_schema=False
    )

    # フロントの配信は、dist の有無を起動時ではなくリクエスト時に見る。
    # `npm run build` は dist を一度空にするので、サーバー起動中の再ビルドや、
    # 起動後の初回ビルドでもそのまま配信できるようにするため。
    #
    # Vite の出力先は `static/`(frontend/vite.config.ts の build.assetsDir)。
    # 既定の `assets/` だと SPA のビューア `/assets/:id` と URL が衝突する。
    dist_root = _FRONTEND_DIST.resolve()
    # `app/api/about.py`(第三者ライセンス表記)も同じ dist を読む。ここから import すると
    # 循環する(about → main → about)ので、`app.state` 経由で渡す(`app/deps.py` 参照)。
    # `tests/conftest.py` の `_isolate_from_frontend_dist` が `_FRONTEND_DIST` を差し替える
    # のは `create_app()` 呼び出しより前なので、そのままここに反映される。
    app.state.frontend_dist = dist_root
    app.mount(
        "/static",
        StaticFiles(directory=str(dist_root / "static"), check_dir=False),
        name="frontend-static",
    )

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str) -> FileResponse:
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404)
        candidate = (dist_root / full_path).resolve()
        # `..` を含むパスで dist の外のファイルを返さない。
        if full_path and candidate.is_relative_to(dist_root) and candidate.is_file():
            return FileResponse(candidate)
        index_html = dist_root / "index.html"
        if not index_html.is_file():
            raise HTTPException(
                status_code=503,
                detail=t("app.frontendNotBuilt"),
            )
        # index.html は毎回検証させる(再ビルド後に古い版が残らないように)。
        # ハッシュ付きの /static/* は内容が変われば URL も変わるのでキャッシュしてよい。
        return FileResponse(index_html, headers={"Cache-Control": "no-cache"})

    return app
