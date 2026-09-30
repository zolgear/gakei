"""`python -m app` で config の HOST/PORT を使って uvicorn を起動する。

`--host` / `--port` / `--data-dir` は環境変数より優先する。`get_settings()` が
そのつど環境変数(と `.env`)を読み直す仕組みを使っているので、`uvicorn.run()` の
前に `os.environ` へ反映してから `get_settings()` を呼び直すだけでよい。
"""

from __future__ import annotations

import argparse
import contextlib
import os
import socket
import sys
import threading
import time
import webbrowser

import uvicorn

from app.config import get_settings
from app.i18n import console_t


def use_utf8_stdio() -> None:
    """標準出力と標準エラーを UTF-8 にする(ADR-0012)。

    Windows で出力をファイルやパイプにリダイレクトすると、Python はロケールの文字コード
    (cp1252 や cp932)で書き出し、日本語のメッセージで UnicodeEncodeError になる。
    コンソールへの出力は元から UTF-8 なので、変えても表示は変わらない。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        with contextlib.suppress(ValueError, OSError):
            reconfigure(encoding="utf-8", errors="replace")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app",
        description=console_t("launcher.serverDescription"),
    )
    parser.add_argument(
        "--host",
        help=console_t("launcher.hostHelp"),
    )
    parser.add_argument(
        "--port",
        type=int,
        help=console_t("launcher.portHelp"),
    )
    parser.add_argument(
        "--data-dir",
        dest="data_dir",
        help=console_t("launcher.dataDirHelp"),
    )
    parser.add_argument(
        "--open-browser",
        action="store_true",
        help=console_t("launcher.openBrowserHelp"),
    )
    return parser


def browser_url(host: str, port: int) -> str:
    """ブラウザに開かせる URL。`0.0.0.0` は待ち受け用の特殊アドレスで、そのままでは
    開けないので `127.0.0.1` に読み替える。"""
    display_host = "127.0.0.1" if host == "0.0.0.0" else host
    return f"http://{display_host}:{port}/"


def wait_for_server_and_open_browser(host: str, port: int, timeout: float = 30.0) -> None:
    """サーバーがポートで接続を受け付けるまで待ってからブラウザを開く。

    バックグラウンドスレッドから呼ぶ想定。タイムアウトしたら黙って諦める(サーバー起動
    自体の失敗はメインスレッド側のログで分かる)。
    """
    connect_host = "127.0.0.1" if host == "0.0.0.0" else host
    url = browser_url(host, port)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with contextlib.suppress(OSError):
            with socket.create_connection((connect_host, port), timeout=0.5):
                webbrowser.open(url)
                return
        time.sleep(0.2)


def port_in_use(host: str, port: int) -> bool:
    """待ち受けようとしているポートが、すでに他のプロセスに使われているか。

    bind できなければ使用中とみなす。Windows ではワイルドカード(0.0.0.0)で待ち受けている
    プロセスがあっても 127.0.0.1 への bind が通ることがあるので、接続できるかも確かめる。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        # uvicorn と同じく SO_REUSEADDR を付ける。付けないと、再起動の直後に残る TIME_WAIT の
        # 接続で bind が失敗し、使用中と誤判定する(Linux)。Windows の SO_REUSEADDR は
        # 使用中のポートも奪えてしまう別物なので付けない。
        if os.name != "nt":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return True
    connect_host = "127.0.0.1" if host == "0.0.0.0" else host
    with contextlib.suppress(OSError), socket.create_connection((connect_host, port), timeout=0.5):
        return True
    return False


def apply_cli_overrides(args: argparse.Namespace) -> None:
    """CLI で指定された分だけ環境変数を上書きする(指定なしは既存の値のまま)。"""
    if args.host is not None:
        os.environ["HOST"] = args.host
    if args.port is not None:
        os.environ["PORT"] = str(args.port)
    if args.data_dir is not None:
        os.environ["DATA_DIR"] = args.data_dir


def main(argv: list[str] | None = None) -> None:
    use_utf8_stdio()
    args = build_parser().parse_args(argv)
    apply_cli_overrides(args)

    settings = get_settings()

    # ADR-0017: 廃止した PROVIDER が残っていたら、スタックトレースを出さずに案内だけして止める。
    # lifespan 側にも同じ検査があるが、そちらは uvicorn のトレースバックに埋もれるため。
    from app.main import (
        AuthConfigError,
        DatabaseUnavailableError,
        LegacyProviderAbortedError,
        check_auth_env,
        check_database_connection,
        check_legacy_provider_env,
    )

    # PROVIDER=openai の警告は lifespan 側で1回だけ出す。
    if settings.legacy_provider not in (None, "openai"):
        try:
            check_legacy_provider_env(settings)
        except LegacyProviderAbortedError as exc:
            print(exc, file=sys.stderr, flush=True)
            raise SystemExit(1) from None

    # ADR-0019: AUTH_MODE=oidc なのに必須の環境変数が欠けていたら、同じくトレースバックを
    # 出さずに案内だけして止める(lifespan 側の検査は uvicorn のトレースバックに埋もれる)。
    try:
        check_auth_env(settings)
    except AuthConfigError as exc:
        print(exc, file=sys.stderr, flush=True)
        raise SystemExit(1) from None

    # ADR-0027 1章: DATABASE_URL の PostgreSQL に接続できなければ、同じくトレースバックを
    # 出さずに案内だけして止める。
    try:
        check_database_connection(settings)
    except DatabaseUnavailableError as exc:
        print(exc, file=sys.stderr, flush=True)
        raise SystemExit(1) from None

    # ADR-0028 2章: 画像の保存先(Azure Blob / S3)に接続・読み書きできなければ、同じく
    # トレースバックを出さずに案内だけして止める。ローカルFS(既定)は何もしない。
    if settings.storage_backend != "local":
        from app.domain.storage import StorageUnavailableError, open_store

        try:
            open_store(settings)
        except StorageUnavailableError as exc:
            print(exc, file=sys.stderr, flush=True)
            raise SystemExit(1) from None

    if port_in_use(settings.host, settings.port):
        print(
            console_t(
                "launcher.portInUse",
                port=settings.port,
                nextPort=settings.port + 1,
            ),
            file=sys.stderr,
            flush=True,
        )
        raise SystemExit(1)

    url = browser_url(settings.host, settings.port)
    # ログファイルへリダイレクトされていてもすぐ見えるよう、明示的に flush する。
    print(console_t("launcher.startingAt", url=url), flush=True)

    if args.open_browser:
        threading.Thread(
            target=wait_for_server_and_open_browser,
            args=(settings.host, settings.port),
            daemon=True,
        ).start()

    uvicorn.run("app.main:app", host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
