"""`app.launch` のユニットテスト。ADR-0012 Decision 2, 3。

実際の npm/node は一切呼ばない。subprocess はモックし、tmp_path 配下に最小限の
frontend/ もどきを組み立ててハッシュ計算やビルド要否の判定を確かめる。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import launch

pytestmark = pytest.mark.windows

# --- compute_source_hash ------------------------------------------------------


def _make_minimal_frontend(root: Path) -> Path:
    frontend = root / "frontend"
    (frontend / "src").mkdir(parents=True)
    (frontend / "public").mkdir(parents=True)
    (frontend / "package-lock.json").write_text('{"lock": true}', encoding="utf-8")
    (frontend / "package.json").write_text('{"name": "frontend"}', encoding="utf-8")
    (frontend / "index.html").write_text("<html></html>", encoding="utf-8")
    (frontend / "vite.config.ts").write_text("export default {}", encoding="utf-8")
    (frontend / "tsconfig.json").write_text("{}", encoding="utf-8")
    (frontend / "src" / "main.ts").write_text("console.log('hi')", encoding="utf-8")
    (frontend / "public" / "favicon.ico").write_bytes(b"\x00\x01")
    return frontend


def test_compute_source_hash_is_deterministic(tmp_path: Path) -> None:
    frontend = _make_minimal_frontend(tmp_path)
    assert launch.compute_source_hash(frontend) == launch.compute_source_hash(frontend)


def test_compute_source_hash_changes_when_source_changes(tmp_path: Path) -> None:
    frontend = _make_minimal_frontend(tmp_path)
    before = launch.compute_source_hash(frontend)
    (frontend / "src" / "main.ts").write_text("console.log('changed')", encoding="utf-8")
    after = launch.compute_source_hash(frontend)
    assert before != after


def test_compute_source_hash_changes_when_file_added(tmp_path: Path) -> None:
    frontend = _make_minimal_frontend(tmp_path)
    before = launch.compute_source_hash(frontend)
    (frontend / "src" / "extra.ts").write_text("export {}", encoding="utf-8")
    after = launch.compute_source_hash(frontend)
    assert before != after


def test_compute_source_hash_ignores_files_outside_hashed_set(tmp_path: Path) -> None:
    frontend = _make_minimal_frontend(tmp_path)
    before = launch.compute_source_hash(frontend)
    # dist/ やロックファイル以外の直下ファイルはハッシュ対象外。
    (frontend / "README.md").write_text("not hashed", encoding="utf-8")
    (frontend / "dist").mkdir()
    (frontend / "dist" / "index.html").write_text("built", encoding="utf-8")
    after = launch.compute_source_hash(frontend)
    assert before == after


def test_compute_source_hash_independent_of_path_separator_style(tmp_path: Path) -> None:
    """相対パスは `/` 区切りに正規化するので、Windows でも Linux でも同じ値になるはず。"""
    frontend = _make_minimal_frontend(tmp_path)
    nested = frontend / "src" / "components"
    nested.mkdir()
    (nested / "widget.ts").write_text("export const widget = 1", encoding="utf-8")

    files = launch.iter_hashed_files(frontend)
    rel_paths = [p.relative_to(frontend).as_posix() for p in files]
    assert "src/components/widget.ts" in rel_paths
    assert all("\\" not in rel for rel in rel_paths)


# --- parse_node_version / meets_min_version ------------------------------------


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("v24.21.0\n", (24, 21, 0)),
        ("v22.12.0", (22, 12, 0)),
        ("22.12.0", (22, 12, 0)),
        ("v18.0.0", (18, 0, 0)),
    ],
)
def test_parse_node_version_ok(output: str, expected: tuple[int, int, int]) -> None:
    assert launch.parse_node_version(output) == expected


@pytest.mark.parametrize("output", ["", "not a version", "node/unknown"])
def test_parse_node_version_unparsable(output: str) -> None:
    assert launch.parse_node_version(output) is None


def test_meets_min_version() -> None:
    assert launch.meets_min_version((22, 12, 0), launch.MIN_NODE_VERSION)
    assert launch.meets_min_version((24, 0, 0), launch.MIN_NODE_VERSION)
    assert not launch.meets_min_version((22, 11, 9), launch.MIN_NODE_VERSION)
    assert not launch.meets_min_version((18, 20, 0), launch.MIN_NODE_VERSION)


# --- decide_frontend_action -----------------------------------------------------


def test_decide_frontend_action_skip_build_when_hash_matches() -> None:
    action = launch.decide_frontend_action(
        hash_matches=True, dist_index_exists=True, node_available=False
    )
    assert action is launch.FrontendAction.SKIP_BUILD


def test_decide_frontend_action_build_when_node_available() -> None:
    action = launch.decide_frontend_action(
        hash_matches=False, dist_index_exists=True, node_available=True
    )
    assert action is launch.FrontendAction.BUILD


def test_decide_frontend_action_use_stale_dist_when_no_node_but_dist_exists() -> None:
    action = launch.decide_frontend_action(
        hash_matches=False, dist_index_exists=True, node_available=False
    )
    assert action is launch.FrontendAction.USE_STALE_DIST


def test_decide_frontend_action_error_when_no_node_and_no_dist() -> None:
    action = launch.decide_frontend_action(
        hash_matches=False, dist_index_exists=False, node_available=False
    )
    assert action is launch.FrontendAction.ERROR_NO_NODE


# --- needs_npm_ci ---------------------------------------------------------------


def test_needs_npm_ci_true_when_node_modules_missing(tmp_path: Path) -> None:
    assert launch.needs_npm_ci(tmp_path / "node_modules", "abc") is True


def test_needs_npm_ci_true_when_stamp_differs(tmp_path: Path) -> None:
    node_modules = tmp_path / "node_modules"
    node_modules.mkdir()
    launch.write_stamp(node_modules / launch.LOCK_STAMP_NAME, "old-hash")
    assert launch.needs_npm_ci(node_modules, "new-hash") is True


def test_needs_npm_ci_false_when_stamp_matches(tmp_path: Path) -> None:
    node_modules = tmp_path / "node_modules"
    node_modules.mkdir()
    launch.write_stamp(node_modules / launch.LOCK_STAMP_NAME, "same-hash")
    assert launch.needs_npm_ci(node_modules, "same-hash") is False


# --- get_node_version(subprocess をモック) --------------------------------------


def test_get_node_version_parses_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeResult:
        returncode = 0
        stdout = "v24.21.0\n"

    monkeypatch.setattr(launch.subprocess, "run", lambda *a, **k: _FakeResult())
    assert launch.get_node_version("node") == (24, 21, 0)


def test_get_node_version_returns_none_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeResult:
        returncode = 1
        stdout = ""

    monkeypatch.setattr(launch.subprocess, "run", lambda *a, **k: _FakeResult())
    assert launch.get_node_version("node") is None


def test_get_node_version_returns_none_when_executable_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(*_a: object, **_k: object) -> None:
        raise OSError("not found")

    monkeypatch.setattr(launch.subprocess, "run", _raise)
    assert launch.get_node_version("node") is None


# --- ensure_frontend_built(結合。subprocess は絶対に呼ばない) ---------------------


def test_ensure_frontend_built_skips_when_stamp_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frontend = _make_minimal_frontend(tmp_path)
    dist = frontend / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("built", encoding="utf-8")
    launch.write_stamp(dist / launch.BUILD_STAMP_NAME, launch.compute_source_hash(frontend))

    calls: list[list[str]] = []
    # Node が使えない状態でも、ハッシュが一致していればビルドは走らないはず。
    monkeypatch.setattr(launch, "find_node_and_npm", lambda: (None, None))

    launch.ensure_frontend_built(
        frontend,
        run_command=lambda cmd, cwd: calls.append(cmd),  # noqa: ARG005
    )

    assert calls == []


def test_ensure_frontend_built_builds_when_hash_differs_and_node_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frontend = _make_minimal_frontend(tmp_path)

    monkeypatch.setattr(launch, "find_node_and_npm", lambda: ("/usr/bin/node", "/usr/bin/npm"))
    monkeypatch.setattr(launch, "get_node_version", lambda _path: (24, 0, 0))

    calls: list[list[str]] = []

    def _fake_run(cmd: list[str], cwd: Path) -> None:
        calls.append(cmd)
        # npm run build を模して、実際に dist/index.html を作る(本物の npm はビルド成果物を作る)。
        if cmd[-2:] == ["run", "build"]:
            dist = frontend / "dist"
            dist.mkdir(exist_ok=True)
            (dist / "index.html").write_text("built", encoding="utf-8")

    launch.ensure_frontend_built(frontend, run_command=_fake_run)

    assert calls == [["/usr/bin/npm", "ci"], ["/usr/bin/npm", "run", "build"]]
    dist = frontend / "dist"
    assert (dist / launch.BUILD_STAMP_NAME).is_file()
    assert launch.read_stamp(dist / launch.BUILD_STAMP_NAME) == launch.compute_source_hash(frontend)
    node_modules = frontend / "node_modules"
    assert (node_modules / launch.LOCK_STAMP_NAME).is_file()


def test_ensure_frontend_built_skips_npm_ci_when_lock_stamp_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frontend = _make_minimal_frontend(tmp_path)
    node_modules = frontend / "node_modules"
    node_modules.mkdir()
    lock_hash = launch.compute_file_hash(frontend / "package-lock.json")
    launch.write_stamp(node_modules / launch.LOCK_STAMP_NAME, lock_hash)

    monkeypatch.setattr(launch, "find_node_and_npm", lambda: ("/usr/bin/node", "/usr/bin/npm"))
    monkeypatch.setattr(launch, "get_node_version", lambda _path: (24, 0, 0))

    calls: list[list[str]] = []

    def _fake_run(cmd: list[str], cwd: Path) -> None:
        calls.append(cmd)
        if cmd[-2:] == ["run", "build"]:
            dist = frontend / "dist"
            dist.mkdir(exist_ok=True)
            (dist / "index.html").write_text("built", encoding="utf-8")

    launch.ensure_frontend_built(frontend, run_command=_fake_run)

    assert calls == [["/usr/bin/npm", "run", "build"]]


def test_ensure_frontend_built_uses_stale_dist_when_node_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frontend = _make_minimal_frontend(tmp_path)
    dist = frontend / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("old build", encoding="utf-8")
    # スタンプは書かない(=古い/ないので本来ならビルド要)。

    monkeypatch.setattr(launch, "find_node_and_npm", lambda: (None, None))

    calls: list[list[str]] = []
    launch.ensure_frontend_built(
        frontend,
        run_command=lambda cmd, cwd: calls.append(cmd),  # noqa: ARG005
    )

    assert calls == []
    assert (dist / "index.html").read_text(encoding="utf-8") == "old build"


def test_ensure_frontend_built_raises_when_no_node_and_no_dist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frontend = _make_minimal_frontend(tmp_path)
    monkeypatch.setattr(launch, "find_node_and_npm", lambda: (None, None))

    with pytest.raises(launch.LauncherError):
        launch.ensure_frontend_built(
            frontend,
            run_command=lambda cmd, cwd: None,  # noqa: ARG005
        )


def test_ensure_frontend_built_raises_when_node_too_old_and_no_dist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frontend = _make_minimal_frontend(tmp_path)
    monkeypatch.setattr(launch, "find_node_and_npm", lambda: ("/usr/bin/node", "/usr/bin/npm"))
    monkeypatch.setattr(launch, "get_node_version", lambda _path: (18, 20, 0))

    with pytest.raises(launch.LauncherError):
        launch.ensure_frontend_built(
            frontend,
            run_command=lambda cmd, cwd: None,  # noqa: ARG005
        )


def test_ensure_frontend_built_raises_on_npm_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frontend = _make_minimal_frontend(tmp_path)
    monkeypatch.setattr(launch, "find_node_and_npm", lambda: ("/usr/bin/node", "/usr/bin/npm"))
    monkeypatch.setattr(launch, "get_node_version", lambda _path: (24, 0, 0))

    def _fail(cmd: list[str], cwd: Path) -> None:
        raise launch.LauncherError("npm ci に失敗しました")

    with pytest.raises(launch.LauncherError):
        launch.ensure_frontend_built(frontend, run_command=_fail)


def test_run_streaming_raises_on_nonzero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _FakeResult:
        returncode = 1

    monkeypatch.setattr(launch.subprocess, "run", lambda *a, **k: _FakeResult())
    with pytest.raises(launch.LauncherError):
        launch.run_streaming(["npm", "ci"], tmp_path)


def test_run_streaming_ok_on_zero_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeResult:
        returncode = 0

    monkeypatch.setattr(launch.subprocess, "run", lambda *a, **k: _FakeResult())
    launch.run_streaming(["npm", "ci"], tmp_path)  # 例外を送出しなければ OK


# --- main(): 引数の受け渡し ------------------------------------------------------


def test_main_passes_unknown_args_through_and_adds_open_browser_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(launch, "ensure_frontend_built", lambda _frontend_dir: None)
    captured: dict[str, list[str] | None] = {}

    def _fake_run_server(argv: list[str] | None) -> None:
        captured["argv"] = argv

    monkeypatch.setattr(launch, "run_server", _fake_run_server)

    launch.main(["--host", "0.0.0.0", "--port", "9000"])

    assert captured["argv"] is not None
    assert "--host" in captured["argv"]
    assert "0.0.0.0" in captured["argv"]
    assert "--port" in captured["argv"]
    assert "9000" in captured["argv"]
    assert "--open-browser" in captured["argv"]


def test_main_no_browser_suppresses_open_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launch, "ensure_frontend_built", lambda _frontend_dir: None)
    captured: dict[str, list[str] | None] = {}

    def _fake_run_server(argv: list[str] | None) -> None:
        captured["argv"] = argv

    monkeypatch.setattr(launch, "run_server", _fake_run_server)

    launch.main(["--no-browser"])

    assert captured["argv"] == []


def test_main_exits_nonzero_when_frontend_build_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(_frontend_dir: Path) -> None:
        raise launch.LauncherError("Node が見つかりません")

    monkeypatch.setattr(launch, "ensure_frontend_built", _raise)
    monkeypatch.setattr(
        launch, "run_server", lambda _argv: pytest.fail("run_server は呼ばれないはず")
    )

    with pytest.raises(SystemExit) as exc_info:
        launch.main([])

    assert exc_info.value.code == 1


def test_use_utf8_stdio_lets_japanese_through_a_cp1252_stream(monkeypatch):
    """Windows で出力をリダイレクトしたときの文字コード(cp1252)でも、日本語で落ちない。"""
    import io
    import sys

    from app.__main__ import use_utf8_stdio

    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", stream)
    use_utf8_stdio()
    print("フロントをビルドしています")
    stream.flush()
    assert buffer.getvalue().decode("utf-8").strip() == "フロントをビルドしています"


def test_port_in_use_detects_a_listening_port():
    import socket

    from app.__main__ import port_in_use

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        assert port_in_use("127.0.0.1", port) is True
    assert port_in_use("127.0.0.1", port) is False


def test_main_exits_with_a_message_when_the_port_is_taken(monkeypatch, capsys):
    import pytest

    import app.__main__ as server_main

    # main() は CLI の値を環境変数に書き込むので、テスト後に元へ戻るよう先に登録しておく。
    monkeypatch.setenv("PORT", "8000")
    monkeypatch.setattr(server_main, "port_in_use", lambda host, port: True)

    def fail_if_started(*args, **kwargs):
        pytest.fail("ポートが使用中なのにサーバーを起動した")

    monkeypatch.setattr(server_main.uvicorn, "run", fail_if_started)
    with pytest.raises(SystemExit) as exc:
        server_main.main(["--port", "8123"])
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "ポート 8123 はすでに使われています" in err
    assert "--port 8124" in err


def test_port_in_use_ignores_time_wait_after_a_restart():
    """再起動の直後は、サーバー側が閉じた接続が TIME_WAIT で残る。uvicorn は SO_REUSEADDR 付きで
    bind できるので、使用中と判定してはいけない(Linux で誤検知した)。"""
    import socket

    from app.__main__ import port_in_use

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen()
    port = server.getsockname()[1]
    client = socket.create_connection(("127.0.0.1", port))
    conn, _ = server.accept()
    # サーバー側から先に閉じると、サーバー側のソケットが TIME_WAIT になる。
    conn.close()
    server.close()
    client.close()
    assert port_in_use("127.0.0.1", port) is False
