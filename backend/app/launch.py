"""`python -m app.launch`: フロントのビルド確認 → 必要なら Node でビルド → サーバー起動、を
まとめて行うランチャー(`run.sh` / `run.bat` から呼ばれる)。ADR-0012 Decision 2, 3 を参照。

OS に依存しない処理だけをここに置く。uv の用意や Node の有無に応じた案内メッセージの
分岐は起動スクリプト側(run.sh / run.bat)ではなくこちらで行う。
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path

from app.__main__ import build_parser as build_server_parser  # noqa: F401  (テストの参照用)
from app.__main__ import main as run_server
from app.__main__ import use_utf8_stdio
from app.i18n import console_t

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FRONTEND_DIR = _REPO_ROOT / "frontend"

# Decision 3: Node.js 22.12 以上が必要(24 系を推奨)。
MIN_NODE_VERSION = (22, 12, 0)

BUILD_STAMP_NAME = ".gakei-build-stamp"
LOCK_STAMP_NAME = ".gakei-lock-stamp"

# ビルド要否のハッシュに含める frontend/ 直下の固定ファイル。tsconfig*.json は別途 glob で拾う。
_FIXED_FILE_NAMES = ("package-lock.json", "package.json", "index.html", "vite.config.ts")
_HASHED_DIRS = ("src", "public")


class LauncherError(RuntimeError):
    """利用者に伝えるべきランチャーのエラー(スタックトレースは出さずメッセージだけ表示する)。"""


def node_install_message() -> str:
    return console_t("launcher.nodeMissing")


# --- ハッシュ計算(純粋関数) -------------------------------------------------


def iter_hashed_files(frontend_dir: Path) -> list[Path]:
    """ビルド要否のハッシュに含めるファイルを、frontend_dir 相対のソート済みで返す。"""
    files: list[Path] = []
    for name in _FIXED_FILE_NAMES:
        path = frontend_dir / name
        if path.is_file():
            files.append(path)
    files.extend(p for p in frontend_dir.glob("tsconfig*.json") if p.is_file())
    for dirname in _HASHED_DIRS:
        base = frontend_dir / dirname
        if base.is_dir():
            files.extend(p for p in base.rglob("*") if p.is_file())

    def _rel_posix(path: Path) -> str:
        # Windows でも Linux でも同じハッシュになるよう `/` 区切りに揃える。
        return path.relative_to(frontend_dir).as_posix()

    return sorted(files, key=_rel_posix)


def compute_source_hash(frontend_dir: Path) -> str:
    """フロントのソース一式のハッシュ。相対パス(`/` 区切り)とバイト列だけを見るので、
    改行コードの差やタイムスタンプの違いに影響されない。"""
    digest = hashlib.sha256()
    for path in iter_hashed_files(frontend_dir):
        rel = path.relative_to(frontend_dir).as_posix()
        data = path.read_bytes()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(data)).encode("ascii"))
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")
    return digest.hexdigest()


def compute_file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_stamp(stamp_path: Path) -> str | None:
    if not stamp_path.is_file():
        return None
    try:
        return stamp_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def write_stamp(stamp_path: Path, value: str) -> None:
    stamp_path.parent.mkdir(parents=True, exist_ok=True)
    stamp_path.write_text(value, encoding="utf-8")


# --- Node のバージョン確認(純粋関数 + 薄いラッパー) --------------------------

_VERSION_RE = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")


def parse_node_version(version_output: str) -> tuple[int, int, int] | None:
    """`node --version` の出力(例: `v24.21.0`)を `(24, 21, 0)` にする。解釈できなければ None。"""
    match = _VERSION_RE.search(version_output.strip())
    if match is None:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def meets_min_version(version: tuple[int, int, int], minimum: tuple[int, int, int]) -> bool:
    return version >= minimum


def find_node_and_npm() -> tuple[str | None, str | None]:
    """Windows では `npm` は `npm.cmd` だが、`shutil.which` が PATHEXT を見て解決してくれる。"""
    return shutil.which("node"), shutil.which("npm")


def get_node_version(node_path: str) -> tuple[int, int, int] | None:
    try:
        result = subprocess.run(
            [node_path, "--version"], capture_output=True, text=True, check=False
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return parse_node_version(result.stdout)


# --- 「今回どうするか」の判定(純粋関数) --------------------------------------


class FrontendAction(StrEnum):
    SKIP_BUILD = "skip_build"
    BUILD = "build"
    USE_STALE_DIST = "use_stale_dist"
    ERROR_NO_NODE = "error_no_node"


def decide_frontend_action(
    *, hash_matches: bool, dist_index_exists: bool, node_available: bool
) -> FrontendAction:
    """フロントを今回どう扱うかを、副作用なしに決める。

    - ハッシュが一致 → ビルド省略。
    - 一致しないが Node が使える → ビルドする。
    - Node が使えないが、既存の dist がある → 警告して既存の dist のまま起動する。
    - Node が使えず、dist もない → 起動を諦める。
    """
    if hash_matches:
        return FrontendAction.SKIP_BUILD
    if node_available:
        return FrontendAction.BUILD
    if dist_index_exists:
        return FrontendAction.USE_STALE_DIST
    return FrontendAction.ERROR_NO_NODE


def needs_npm_ci(node_modules_dir: Path, current_lock_hash: str) -> bool:
    if not node_modules_dir.is_dir():
        return True
    return read_stamp(node_modules_dir / LOCK_STAMP_NAME) != current_lock_hash


# --- npm の実行(副作用あり。テストでは差し替える) ----------------------------


def run_streaming(cmd: list[str], cwd: Path) -> None:
    """コマンドを実行し、出力をそのままコンソールへ流す。失敗したら LauncherError を送出する。"""
    print(f"$ {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=cwd, check=False)
    if result.returncode != 0:
        joined = " ".join(cmd)
        raise LauncherError(console_t("launcher.commandFailed", code=result.returncode, cmd=joined))


RunCommand = Callable[[list[str], Path], None]


def ensure_frontend_built(frontend_dir: Path, *, run_command: RunCommand = run_streaming) -> None:
    """フロントのビルドが必要か判定し、必要なら Node/npm でビルドする。"""
    dist_dir = frontend_dir / "dist"
    build_stamp_path = dist_dir / BUILD_STAMP_NAME
    dist_index = dist_dir / "index.html"

    current_hash = compute_source_hash(frontend_dir)
    hash_matches = read_stamp(build_stamp_path) == current_hash

    node_path, npm_path = find_node_and_npm()
    node_version = get_node_version(node_path) if node_path else None
    node_available = bool(
        node_path
        and npm_path
        and node_version is not None
        and meets_min_version(node_version, MIN_NODE_VERSION)
    )

    action = decide_frontend_action(
        hash_matches=hash_matches,
        dist_index_exists=dist_index.is_file(),
        node_available=node_available,
    )

    if action is FrontendAction.SKIP_BUILD:
        print(console_t("launcher.frontendUpToDate"))
        return

    if action is FrontendAction.ERROR_NO_NODE:
        raise LauncherError(node_install_message())

    if action is FrontendAction.USE_STALE_DIST:
        print(
            console_t("launcher.warningPrefix")
            + node_install_message()
            + console_t("launcher.staleDistNotice"),
            file=sys.stderr,
        )
        return

    assert action is FrontendAction.BUILD
    assert node_path is not None
    assert npm_path is not None

    lock_hash = compute_file_hash(frontend_dir / "package-lock.json")
    node_modules_dir = frontend_dir / "node_modules"
    if needs_npm_ci(node_modules_dir, lock_hash):
        print(console_t("launcher.npmCi"))
        run_command([npm_path, "ci"], frontend_dir)
        write_stamp(node_modules_dir / LOCK_STAMP_NAME, lock_hash)

    print(console_t("launcher.building"))
    run_command([npm_path, "run", "build"], frontend_dir)
    write_stamp(build_stamp_path, current_hash)
    print(console_t("launcher.buildComplete"))


# --- エントリポイント ---------------------------------------------------------


def build_launcher_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.launch",
        description=console_t("launcher.launcherDescription"),
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help=console_t("launcher.noBrowserHelp"),
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    use_utf8_stdio()
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    launcher_args, remaining = build_launcher_parser().parse_known_args(raw_argv)

    try:
        ensure_frontend_built(_FRONTEND_DIR)
    except LauncherError as exc:
        print(console_t("launcher.startupAborted", error=exc), file=sys.stderr)
        raise SystemExit(1) from exc

    server_argv = list(remaining)
    if not launcher_args.no_browser:
        server_argv.append("--open-browser")

    run_server(server_argv)


if __name__ == "__main__":
    main()
