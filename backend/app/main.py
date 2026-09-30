"""FastAPI アプリ本体。lifespan で DB マイグレーションと worker(Runner)を起動する。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from alembic import command
from alembic.config import Config
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.annotation.wd_models import WdModelDownloader
from app.api import about as about_api
from app.api import api_tokens as api_tokens_api
from app.api import asset_groups as asset_groups_api
from app.api import assets as assets_api
from app.api import auth as auth_api
from app.api import capabilities as capabilities_api
from app.api import comfyui as comfyui_api
from app.api import downloads as downloads_api
from app.api import events as events_api
from app.api import pricing as pricing_api
from app.api import prompt_sets as prompt_sets_api
from app.api import runs as runs_api
from app.api import search as search_api
from app.api import settings as settings_api
from app.api import shares as shares_api
from app.api import tags as tags_api
from app.api import uploads as uploads_api
from app.api import users as users_api
from app.auth.deps import require_user
from app.auth.oidc import AuthlibOidcClient
from app.auth.secret import load_or_create_auth_secret
from app.config import Settings, display_database_url, get_settings
from app.db import make_engine, make_session_factory
from app.domain.api_key import resolve_base_url, warn_if_insecure_base_url
from app.domain.storage import open_store
from app.i18n import console_t, parse_accept_language, set_locale, t
from app.mcp.endpoint import McpEndpoint
from app.mcp.server import build_mcp_server, build_session_manager
from app.providers.registry import _is_loopback_url, build_registry
from app.worker.annotator import Annotator
from app.worker.progress import ProgressBus
from app.worker.runner import Runner

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = _BACKEND_DIR / "migrations"
_FRONTEND_DIST = _BACKEND_DIR.parent / "frontend" / "dist"

# I-1(2026-09-27 追記): oidc モードで無効にした3つのパス(`spa_fallback` 側の扱いに使う)。
_DISABLED_DOCS_PATHS = {"docs", "redoc", "openapi.json"}

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
    """ADR-0019: `AUTH_MODE=oidc` なのに必須の環境変数が欠けている場合。"""


def check_auth_env(settings: Settings) -> None:
    """oidc モードに必要な環境変数が揃っているか起動時に検査する。

    `none`(既定)モードは何も要らないので常に通る。IdP が実際に落ちていても discovery は
    初回ログイン時まで行わないため、ここでは値の有無だけを見る。
    """
    if settings.auth_mode != "oidc":
        return

    missing = [
        name
        for name, value in (
            ("OIDC_ISSUER", settings.oidc_issuer),
            ("OIDC_CLIENT_ID", settings.oidc_client_id),
            ("PUBLIC_BASE_URL", settings.public_base_url),
        )
        if not value
    ]
    if missing:
        raise AuthConfigError(console_t("app.authConfigMissing", missing=", ".join(missing)))

    # L-4(2026-09-27 追記): 両方とも値はあるので、形式(スキームとホスト)を検査する。
    # スキームが http/https でない、あるいはホストが無いものは起動を中止する
    # (redirect_uri の組み立てや Cookie の Secure 判定が壊れた値のまま起動しないため)。
    # ループバック以外への http は、動作はするが平文になるため警告に留める。
    for name, value in (
        ("PUBLIC_BASE_URL", settings.public_base_url),
        ("OIDC_ISSUER", settings.oidc_issuer),
    ):
        assert value is not None  # 上の missing チェックを通過済み
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise AuthConfigError(console_t("app.authUrlInvalid", name=name, value=value))
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


def run_migrations(url: str) -> None:
    """Alembic の `upgrade head` を実行する。起動時のほか、`app.tools.backfill_embedded_meta`
    のようにサーバーを立てずに DB だけ用意したいツールからも呼べるよう公開する。

    `url` は `Settings.sqlalchemy_url`(SQLite / PostgreSQL。ADR-0027)。
    """
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
        check_auth_env(settings)

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
        # ADR-0028 2章: 画像の保存先。接続・読み書きできなければ、分かる文言で起動を中止する
        # (`StorageUnavailableError`)。
        store = open_store(settings)
        registry = build_registry(settings, session_factory)
        progress_bus = ProgressBus()
        # ADR-0024: 自動タイトル・タグの推定の worker と、ONNX タガーのモデルのダウンロード。
        annotator = Annotator(session_factory, store, settings)
        wd_downloader = WdModelDownloader(settings.data_dir, fake=settings.fake_provider)
        runner = Runner(
            session_factory, store, registry, progress_bus, settings.data_dir, annotator=annotator
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
        if settings.auth_mode == "oidc":
            app.state.oidc_client = AuthlibOidcClient(settings)

        # ADR-0023: `/mcp` のセッションマネージャー。`run()` は1インスタンスにつき1回しか
        # 呼べないので、lifespan のたびに作り直す(テストで同じアプリを複数回起動するため)。
        mcp_session_manager = build_session_manager(build_mcp_server())
        app.state.mcp_session_manager = mcp_session_manager

        await runner.start()
        await annotator.start()
        try:
            async with mcp_session_manager.run():
                yield
        finally:
            await runner.stop()
            await annotator.stop()
            await wd_downloader.stop()
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


def create_app(settings: Settings | None = None) -> FastAPI:
    """`settings` を渡すと、環境変数を経由せずその設定でアプリを組み立てる(ADR-0017)。

    ミドルウェア(`SessionMiddleware`)の構築時にも `settings` が要るため、ここで1回だけ
    `get_settings()` を解決して lifespan にも同じ値を渡す(ADR-0019)。
    """
    resolved_settings = settings or get_settings()
    # I-1(2026-09-27 追記): oidc モードでは /docs・/redoc・/openapi.json も未ログインで
    # 読めてしまうため無効にする(API の形はレスポンス自体からも推測できるが、わざわざ
    # 一覧を公開しない)。none モードはこれまでどおり(`app.tools.export_openapi` は
    # none モードのまま `app.openapi()` を直接呼ぶので、ここでは影響しない)。
    docs_kwargs: dict[str, str | None] = (
        {"docs_url": None, "redoc_url": None, "openapi_url": None}
        if resolved_settings.auth_mode == "oidc"
        else {}
    )
    app = FastAPI(
        title="GAKEI ローカルMVP", lifespan=_build_lifespan(resolved_settings), **docs_kwargs
    )

    app.add_middleware(LocaleMiddleware)
    app.add_middleware(PublicShareHeadersMiddleware)

    # JS / CSS / JSON を圧縮する。画像、フォント、SSE(text/event-stream)は
    # ミドルウェアの既定で対象外なので、進捗の配信が遅れることはない。
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    if resolved_settings.auth_mode == "oidc":
        # state/nonce/PKCE の一時保存用(ADR-0019)。ログイン済みセッションの Cookie
        # (`gakei_session`)とは別物で、oidc モードのときだけ追加する。
        from starlette.middleware.sessions import SessionMiddleware

        app.add_middleware(
            SessionMiddleware,
            secret_key=load_or_create_auth_secret(resolved_settings),
            session_cookie="gakei_oidc",
            max_age=600,
            same_site="lax",
            https_only=resolved_settings.public_base_is_https,
        )

    # `/api/auth` だけは認可を付けない(未ログインでも /me・/login・/callback は呼べる必要が
    # あるため)。他のルーターは `require_user` を通す(ADR-0019。`none` モードは常に
    # `LOCAL_ADMIN` を返すので実質無効化される)。`users`(ADR-0020)は oidc モード専用だが、
    # `none` モードでの 404 化はルーター内で行うので、ここでの扱いは他と同じでよい。
    # `about`(ADR-0021)もログインが要る他の API と同じ扱いにする。
    app.include_router(auth_api.router)

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
    app.include_router(tags_api.router, dependencies=auth_dep)
    app.include_router(pricing_api.router, dependencies=auth_dep)
    app.include_router(settings_api.router, dependencies=auth_dep)
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
        # I-1: oidc モードで `docs_url` 等を None にして無効化した3つのパスは、この
        # catch-all が拾って index.html を返してしまわないよう明示的に 404 にする
        # (none モードでは FastAPI 自身のルートが先に処理するのでここには来ない)。
        if full_path in _DISABLED_DOCS_PATHS:
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


app = create_app()
