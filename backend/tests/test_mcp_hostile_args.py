"""MCP の全ツールに異常な引数を与える(Issue #82 の候補 1)。

どのツールも、どんな引数でも「扱われた結果」を返すこと: JSON-RPC の `result` で、
`isError: true` なら読めるメッセージ(トレースバックや内部の例外を出さない)、または正常な
結果。JSON-RPC の内部エラー(-32603)、HTTP 500、固まる呼び出しにはしない。

ツールの一覧と引数のスキーマはコードに書かず、`build_mcp_server()` の `list_tools()` から
収集時に取り出す(新しいツールも自動で対象になる)。HTTP の `tools/list` と同じ一覧かは
`test_tool_list_matches_runtime` で確かめる。

速さのため、アプリはモジュールで1つにする(数百件の呼び出しを1件ずつ作り直すと遅い)。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

import app.mcp.server as mcp_server
from app.config import Settings
from app.domain.models import Run
from tests.conftest import (
    _PG_SERVER_URL,
    _create_pg_database,
    _drop_pg_database,
    _unique_db_name,
    make_png_bytes,
)
from tests.test_ingest import png_with_declared_size
from tests.test_mcp import _HEADERS, _call, _enable, _error_text, _ok, _rpc

# -- ツールの一覧(収集時) -----------------------------------------------------------


def _runtime_tools() -> list[dict[str, Any]]:
    tools = asyncio.run(mcp_server.build_mcp_server().list_tools())
    return [{"name": tool.name, "inputSchema": tool.input_schema} for tool in tools]


_TOOLS = _runtime_tools()

# 1回の呼び出しに許す時間。待ちの引数(`wait_seconds` など)は上限で切られる前提で、
# 不明な ID ならすぐ返る。これを超えたら固まりかけている。
_CALL_BUDGET_SECONDS = 10.0

# よく知られた形だが存在しない UUID。
_UNKNOWN_UUID = "00000000-0000-4000-8000-000000000000"

_LONG = "A" * 100_000

# 埋め込んだ base64 が DB に残っていないかを探す目印(DB の検査でこの文字列を探す)。
_BASE64_MARKER = base64.b64encode(b"gakei-hostile-marker-" * 40).decode("ascii")


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


# -- 異常な値の生成 ------------------------------------------------------------------


@dataclass(frozen=True)
class Case:
    """1つの呼び出し。`must_error` は、スキーマ上どう見ても通らない引数(エラーを期待する)。"""

    tool: str
    label: str
    arguments: dict[str, Any]
    must_error: bool

    @property
    def id(self) -> str:
        return f"{self.tool}-{self.label}"


def _types_of(schema: dict[str, Any]) -> set[str]:
    """プロパティが受ける JSON の型(`anyOf` の null を含む)。"""
    if "anyOf" in schema:
        found: set[str] = set()
        for option in schema["anyOf"]:
            found |= _types_of(option)
        return found
    kind = schema.get("type")
    if isinstance(kind, list):
        return set(kind)
    if kind is not None:
        return {kind}
    if "enum" in schema:
        return {"string"}
    return set()


def _main_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """`anyOf` の null でない側(無ければそのもの)。"""
    for option in schema.get("anyOf", []):
        if option.get("type") != "null":
            return option
    return schema


def _deep(depth: int) -> dict[str, Any]:
    nested: dict[str, Any] = {"leaf": 1}
    for _ in range(depth):
        nested = {"n": nested}
    return nested


# 型ごとの「違う型」の見本。値は、lax なパース(例: "1" → 1)でも通らないものにする。
_WRONG_TYPE_SAMPLES: dict[str, Any] = {
    "string": "not-a-valid-value",
    "integer": 7,
    "number": 7.5,
    "boolean": True,
    "array": ["x", 1, None],
    "object": {"unexpected": {"nested": [1, 2]}},
    "null": None,
}


def _wrong_type_cases(accepted: set[str], main: dict[str, Any]) -> list[tuple[str, Any, bool]]:
    cases: list[tuple[str, Any, bool]] = []
    for kind, value in _WRONG_TYPE_SAMPLES.items():
        if kind in accepted or (kind == "integer" and "number" in accepted):
            continue
        # pydantic の lax なパースで通るものは、弾かれるとは決めつけない
        # (例: number に整数、整数に真偽、日時に UNIX 時刻の数値)。
        lax_ok = (kind in ("integer", "number") and accepted & {"integer", "number"}) or (
            kind == "boolean" and accepted & {"integer", "number"}
        )
        lax_ok = lax_ok or (kind in ("integer", "number") and main.get("format") == "date-time")
        cases.append((f"type-{kind}", value, not lax_ok))
    return cases


def _string_cases(name: str, schema: dict[str, Any]) -> list[tuple[str, Any, bool]]:
    fmt = schema.get("format")
    enum = schema.get("enum")
    cases: list[tuple[str, Any, bool]] = [
        ("empty", "", fmt in ("uuid", "date-time") or enum is not None),
        ("long-100k", _LONG, fmt in ("uuid", "date-time") or enum is not None),
        ("control-chars", "a\x01\x07\x1b[31m\r\n\tb", fmt is not None or enum is not None),
        ("nul", "bad\x00value", True),
        ("unicode", "画像🎨‮﻿\U0010ffff", fmt is not None or enum is not None),
        ("many-words", "w " * 20_000, fmt is not None or enum is not None),
        ("sql-ish", "'; DROP TABLE run; --", fmt is not None or enum is not None),
        ("percent-wildcards", "%_%\\", fmt is not None or enum is not None),
    ]
    if fmt == "uuid":
        cases += [
            ("uuid-malformed", "not-a-uuid", True),
            ("uuid-truncated", _UNKNOWN_UUID[:-3], True),
            ("uuid-unknown", _UNKNOWN_UUID, False),
            ("uuid-nil", "00000000-0000-0000-0000-000000000000", False),
            ("uuid-braced", "{" + _UNKNOWN_UUID + "}", False),
        ]
    if fmt == "date-time":
        cases += [
            ("datetime-garbage", "2026-13-45T99:99:99Z", True),
            ("datetime-year-1", "0001-01-01T00:00:00Z", False),
            ("datetime-year-9999", "9999-12-31T23:59:59+14:00", False),
            ("datetime-naive", "2026-09-28T10:00:00", False),
        ]
    if enum is not None:
        cases.append(("enum-unknown", "definitely-not-in-enum", True))
        cases.append(("enum-wrong-case", str(enum[0]).upper() + "X", True))
    if "base64" in name:
        cases += [
            ("b64-invalid", "!!!not base64!!!", True),
            ("b64-padding-only", "====", True),
            ("b64-whitespace", "   ", True),
            ("b64-data-url-no-comma", "data:image/png;base64", True),
            ("b64-data-url-empty", "data:image/png;base64,", True),
            ("b64-not-image", _b64(b"hello, this is not an image"), True),
            ("b64-truncated-png", _b64(make_png_bytes(8, 8, (9, 9, 9))[:40]), True),
            ("b64-png-bomb", _b64(png_with_declared_size(60_000, 60_000)), True),
            ("b64-png-zero-size", _b64(png_with_declared_size(0, 0)), True),
            ("b64-marker", _BASE64_MARKER, True),
        ]
    return cases


def _number_cases(schema: dict[str, Any], integer: bool) -> list[tuple[str, Any, bool]]:
    lo = schema.get("minimum", schema.get("exclusiveMinimum"))
    hi = schema.get("maximum", schema.get("exclusiveMaximum"))
    cases: list[tuple[str, Any, bool]] = [
        ("negative", -1, lo is not None and lo >= 0),
        ("zero", 0, lo is not None and lo > 0),
        ("huge", 10**18, hi is not None),
        ("huge-int-beyond-int64", 10**40, hi is not None),
        ("negative-huge", -(10**18), lo is not None),
        ("float-tiny", 1e-300, integer),
        ("float-huge", 1e308, integer or hi is not None),
        ("string-number", "5", False),
        ("bool-for-number", True, False),
    ]
    if integer:
        cases.append(("float-fraction", 1.5, True))
    if lo is not None:
        cases.append(("below-minimum", lo - 1, True))
    if hi is not None:
        cases.append(("above-maximum", hi + 1, True))
    return cases


def _array_cases(schema: dict[str, Any]) -> list[tuple[str, Any, bool]]:
    items = schema.get("items", {})
    max_items = schema.get("maxItems")
    min_items = schema.get("minItems")
    item_is_uuid = items.get("format") == "uuid"
    item_enum = items.get("enum")
    sample = _UNKNOWN_UUID if item_is_uuid else (item_enum[0] if item_enum else "x")
    cases: list[tuple[str, Any, bool]] = [
        ("array-empty", [], bool(min_items)),
        ("array-1000", [sample] * 1000, max_items is not None and max_items < 1000),
        ("array-of-null", [None], True),
        ("array-of-objects", [{"a": 1}], True),
        ("array-nested", [[sample]], True),
        ("array-mixed", [sample, 1, True], True),
    ]
    if item_is_uuid:
        cases += [
            ("array-malformed-uuid", ["not-a-uuid"], True),
            ("array-duplicate-unknown-uuid", [_UNKNOWN_UUID, _UNKNOWN_UUID], False),
            ("array-random-unknown-uuids", [str(uuid.uuid4()) for _ in range(5)], False),
        ]
    if item_enum is not None:
        cases.append(("array-unknown-enum", ["nope"], True))
    if max_items is not None:
        cases.append(("array-above-max", [sample] * (max_items + 1), True))
    return cases


# 自由な形の object(`params`)に入れる、よくあるキーへの異常な値。
_FREEFORM_OBJECT_CASES: list[tuple[str, Any]] = [
    ("obj-empty", {}),
    ("obj-deep-50", _deep(50)),
    ("obj-deep-150", _deep(150)),
    ("obj-many-keys", {f"k{i}": i for i in range(1000)}),
    ("obj-size-int", {"size": 1024}),
    ("obj-size-garbage", {"size": "big"}),
    ("obj-size-negative", {"size": "-1024x-1024"}),
    ("obj-size-huge", {"size": "99999999x99999999"}),
    ("obj-size-zero", {"size": "0x0"}),
    ("obj-size-list", {"size": [1024, 1024]}),
    ("obj-quality-list", {"quality": ["low"]}),
    ("obj-quality-unknown", {"quality": "ultra"}),
    ("obj-n-string", {"n": "many"}),
    ("obj-n-huge", {"n": 10**18}),
    ("obj-n-negative", {"n": -5}),
    ("obj-n-float", {"n": 2.5}),
    ("obj-n-bool", {"n": True}),
    ("obj-n-null", {"n": None}),
    ("obj-n-object", {"n": {"v": 1}}),
    ("obj-output-format-garbage", {"output_format": "exe"}),
    ("obj-background-object", {"background": {"x": 1}}),
    ("obj-long-value", {"note": _LONG}),
    ("obj-unicode-key", {"画像‮": "x"}),
    ("obj-image-base64", {"image": _BASE64_MARKER}),
]


def _object_cases(schema: dict[str, Any]) -> list[tuple[str, Any, bool]]:
    if schema.get("additionalProperties") is True or "properties" not in schema:
        return [(label, value, False) for label, value in _FREEFORM_OBJECT_CASES]
    return [("obj-deep-50", _deep(50), True)]


def _property_cases(name: str, schema: dict[str, Any]) -> list[tuple[str, Any, bool]]:
    accepted = _types_of(schema)
    main = _main_schema(schema)
    cases = _wrong_type_cases(accepted, main)
    if "string" in accepted:
        cases += _string_cases(name, main)
    if "integer" in accepted:
        cases += _number_cases(main, integer=True)
    elif "number" in accepted:
        cases += _number_cases(main, integer=False)
    if "array" in accepted:
        cases += _array_cases(main)
    if "object" in accepted:
        cases += _object_cases(main)
    return cases


def _baseline_value(schema: dict[str, Any]) -> Any:
    """必須の引数を埋める、妥当な形の値(ほかの引数を壊したときに必須の欠けで止まらないよう)。"""
    main = _main_schema(schema)
    kind = main.get("type")
    if main.get("enum"):
        return main["enum"][0]
    if kind == "string":
        return _UNKNOWN_UUID if main.get("format") == "uuid" else "hostile test"
    if kind == "array":
        items = main.get("items", {})
        return [_UNKNOWN_UUID if items.get("format") == "uuid" else "x"]
    if kind in ("integer", "number"):
        return main.get("minimum", 1)
    if kind == "boolean":
        return False
    return {}


def _cases_for(tool: dict[str, Any]) -> list[Case]:
    name = tool["name"]
    schema = tool["inputSchema"]
    properties: dict[str, Any] = schema.get("properties", {})
    required: list[str] = schema.get("required", [])
    baseline = {prop: _baseline_value(properties[prop]) for prop in required}

    cases: list[Case] = [
        Case(name, "empty-args", {}, must_error=bool(required)),
        Case(name, "unknown-extra-prop", {**baseline, "zz_unknown": {"x": [1]}}, False),
        Case(name, "unknown-extra-prop-long", {**baseline, "zz_unknown": _LONG}, False),
    ]
    for prop in required:
        missing = {k: v for k, v in baseline.items() if k != prop}
        cases.append(Case(name, f"{prop}-missing", missing, must_error=True))
    for prop, prop_schema in properties.items():
        for label, value, must_error in _property_cases(prop, prop_schema):
            arguments = {**baseline, prop: value}
            cases.append(Case(name, f"{prop}-{label}", arguments, must_error))
    return cases


_CASES = [case for tool in _TOOLS for case in _cases_for(tool)]


# -- アプリ(モジュールで1つ) ---------------------------------------------------------


@pytest.fixture(scope="module")
def shared_client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
    """FAKE プロバイダーで runner が動くアプリ。MCP を有効にし、時間あたりの上限は最大にする
    (上限に当たって検証のエラーが見えなくならないように)。"""
    from app.main import create_app

    data_dir: Path = tmp_path_factory.mktemp("mcp-hostile") / "data"
    pg_name = _unique_db_name("gakei_hostile") if _PG_SERVER_URL else None
    database_url = _create_pg_database(pg_name) if pg_name else None
    settings = Settings(
        _env_file=None,
        data_dir=data_dir,
        fake_provider=True,
        database_url=database_url,
        comfyui_url="",
    )
    try:
        with TestClient(create_app(settings)) as client:
            _enable(client, hourly_run_limit=1000)
            yield client
    finally:
        if pg_name:
            _drop_pg_database(pg_name)


# -- 判定 ----------------------------------------------------------------------------

# 想定外の例外が SDK まで抜けたときの文言(`mcp.server.mcpserver.tools.base`)。中身は伏せられ、
# 利用者には直し方がわからない。
_CRASH_TEXT = re.compile(r"^Error executing tool \w+$")
_LEAK_PATTERNS = (
    "Traceback",
    'File "',
    "sqlalchemy.",
    "psycopg",
    "IntegrityError",
    "OperationalError",
    "DataError",
    "RecursionError",
    "KeyError",
    "AttributeError",
    "TypeError(",
    "<class '",
)


def _run_count(client: TestClient) -> int:
    with client.app.state.session_factory() as db:
        return db.execute(select(func.count()).select_from(Run)).scalar_one()


def _check_handled(
    client: TestClient,
    case: Case,
    caplog: pytest.LogCaptureFixture,
) -> dict[str, Any]:
    started = time.monotonic()
    with caplog.at_level(logging.ERROR):
        response = _rpc(client, "tools/call", {"name": case.tool, "arguments": case.arguments})
    elapsed = time.monotonic() - started

    assert elapsed < _CALL_BUDGET_SECONDS, f"{case.id}: {elapsed:.1f}s かかった"
    assert response.status_code == 200, f"{case.id}: HTTP {response.status_code} {response.text}"
    body = response.json()
    assert "error" not in body, f"{case.id}: JSON-RPC のエラー {body['error']}"
    assert "result" in body, body
    unexpected = [r for r in caplog.records if "unexpected exception" in r.getMessage()]
    assert not unexpected, f"{case.id}: 想定外の例外 {unexpected[0].exc_info!r}"

    result = body["result"]
    if result.get("isError"):
        text = _error_text(result)
        assert text.strip(), f"{case.id}: エラーの文言が空"
        assert not _CRASH_TEXT.match(text), f"{case.id}: 想定外の例外になった"
        for pattern in _LEAK_PATTERNS:
            assert pattern not in text, f"{case.id}: 内部の情報が出ている ({pattern}): {text}"
        # 長い入力をそのまま返して文脈を埋めない。
        assert len(text) < 20_000, f"{case.id}: エラーの文言が長すぎる ({len(text)})"
    else:
        assert not case.must_error, f"{case.id}: エラーになるべき引数が通った: {result}"
    return result


# -- テスト --------------------------------------------------------------------------


def test_tool_list_matches_runtime(shared_client: TestClient) -> None:
    """収集時に取り出した一覧と、HTTP の `tools/list` が同じ。どのツールにもスキーマがある。"""
    response = _rpc(shared_client, "tools/list")
    assert response.status_code == 200
    tools = response.json()["result"]["tools"]
    assert tools, "ツールが1つもない"
    for tool in tools:
        schema = tool.get("inputSchema")
        assert isinstance(schema, dict), tool["name"]
        assert schema.get("type") == "object", tool["name"]
    assert sorted(t["name"] for t in tools) == sorted(t["name"] for t in _TOOLS)
    assert _CASES


def test_unknown_tool_is_handled(shared_client: TestClient) -> None:
    response = _rpc(shared_client, "tools/call", {"name": "no_such_tool", "arguments": {}})
    assert response.status_code == 200
    body = response.json()
    if "result" in body:
        assert "Unknown tool" in _error_text(body["result"])
    else:
        assert body["error"]["code"] != -32603, body


@pytest.mark.parametrize("arguments", [None, [], "x", 1], ids=["null", "list", "string", "int"])
def test_non_object_arguments_are_rejected(shared_client: TestClient, arguments: Any) -> None:
    """`arguments` 自体が object でない。プロトコルの検証で弾かれる(内部エラーにはしない)。"""
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "list_groups", "arguments": arguments},
    }
    response = shared_client.post("/mcp", json=body, headers=_HEADERS)
    assert response.status_code in (200, 400), response.text
    payload = response.json()
    if "error" in payload:
        assert payload["error"]["code"] != -32603, payload
    elif arguments is not None:
        assert payload["result"].get("isError") is True, payload


def test_nan_and_infinity_literals(shared_client: TestClient) -> None:
    """JSON の外の `NaN` / `Infinity` を本文に書いて送る(固まらず、内部エラーにしない)。"""
    for literal in ("NaN", "Infinity", "-Infinity"):
        raw = (
            '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_run",'
            f'"arguments":{{"run_id":"{_UNKNOWN_UUID}","wait_seconds":{literal}}}}}}}'
        )
        started = time.monotonic()
        response = shared_client.post("/mcp", content=raw, headers=_HEADERS)
        assert time.monotonic() - started < _CALL_BUDGET_SECONDS
        assert response.status_code in (200, 400), response.text
        payload = response.json()
        if "error" in payload:
            assert payload["error"]["code"] != -32603, payload
        else:
            assert payload["result"].get("isError") is True, payload


@pytest.mark.parametrize("case", _CASES, ids=[case.id for case in _CASES])
def test_hostile_arguments_are_handled(
    shared_client: TestClient, case: Case, caplog: pytest.LogCaptureFixture
) -> None:
    before = _run_count(shared_client)
    result = _check_handled(shared_client, case, caplog)
    if case.tool == "generate_image":
        after = _run_count(shared_client)
        if result.get("isError"):
            # エラーを返したのに Run ができていたら、利用者の知らない課金になる。
            assert after == before, f"{case.id}: エラーなのに Run ができた"
        else:
            assert "run_id" in result["structuredContent"], result
            assert after == before + 1, f"{case.id}: Run の数 {before} → {after}"


def test_no_base64_reaches_the_db(shared_client: TestClient) -> None:
    """異常な引数のどれも、base64 の目印を Run の params やプロンプトに残していない
    (ADR-0004: base64 は DB に入れない)。ほかのテストと同じワーカーで最後に走るとは限らない
    ので、ここで目印入りの呼び出しを自分でもしておく。"""
    for arguments in (
        {"prompt": "x", "params": {"image": _BASE64_MARKER}},
        {"prompt": "x", "params": {"input_fidelity": _BASE64_MARKER}},
    ):
        _call(shared_client, "generate_image", arguments)
    _call(shared_client, "upload_image", {"data_base64": _BASE64_MARKER})
    with shared_client.app.state.session_factory() as db:
        for run in db.execute(select(Run)).scalars():
            dumped = json.dumps(run.params, ensure_ascii=False) + (run.prompt or "")
            assert _BASE64_MARKER not in dumped, run.id


def test_hostile_arguments_with_generation_disabled(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """時間あたりの上限 0(MCP での生成を止めた状態)でも、異常な引数は扱われたエラーになり、
    Run はできない。読み取りのツールは動く。"""
    _enable(client, hourly_run_limit=0)
    generate_cases = [case for case in _CASES if case.tool == "generate_image"]
    assert generate_cases
    for case in generate_cases:
        result = _check_handled(client, case, caplog)
        assert result.get("isError") is True, f"{case.id}: 生成を止めているのに通った"
    assert _run_count(client) == 0
    _ok(_call(client, "list_groups"))
    _ok(_call(client, "list_runs", {"limit": 50}))


def test_get_run_wait_is_clamped(
    client_no_runner: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """待ちの引数がどれほど大きくても、上限(WAIT_MAX_SECONDS)で切られて返る。上限を縮めて
    確かめる(本来の 25 秒を待たない)。"""
    client = client_no_runner
    _enable(client)
    monkeypatch.setattr(mcp_server, "WAIT_MAX_SECONDS", 0.3)
    run_id = _ok(_call(client, "generate_image", {"prompt": "queued"}))["run_id"]
    for value in (10**18, 1e308, 25.0001):
        started = time.monotonic()
        payload = _ok(_call(client, "get_run", {"run_id": run_id, "wait_seconds": value}))
        assert time.monotonic() - started < 5, value
        assert payload["status"] == "queued"


def test_hostile_ids_never_leak_other_errors(shared_client: TestClient) -> None:
    """有効な画像を1つ作り、その ID を run_id として(型は合うが種類が違う ID)渡す。"""
    asset_id = _ok(
        _call(shared_client, "upload_image", {"data_base64": _b64(make_png_bytes(8, 8, (1, 1, 1)))})
    )["asset_id"]
    for tool in ("get_run", "cancel_run"):
        assert "not found" in _error_text(_call(shared_client, tool, {"run_id": asset_id}))
    group_text = _error_text(
        _call(shared_client, "move_to_group", {"group_id": asset_id, "asset_ids": [asset_id]})
    )
    assert "not found" in group_text


@pytest.mark.parametrize(
    "query",
    ["A" * 100_000, "w " * 20_000, "a\x01\x1b[31m b", "画像🎨\u202e"],
    ids=["long", "many-words", "control-chars", "unicode"],
)
def test_semantic_search_with_hostile_query(
    shared_client: TestClient, query: str, caplog: pytest.LogCaptureFixture
) -> None:
    """引数の組み合わせ(mode=semantic と異常な query)。埋め込みが無効でも有効でも扱われる。"""
    case = Case("search_assets", "semantic", {"mode": "semantic", "query": query}, False)
    _check_handled(shared_client, case, caplog)


def test_long_error_messages_are_clipped() -> None:
    message = "Unknown provider: " + "A" * 100_000 + " Use get_capabilities."
    clipped = mcp_server.clip_error_message(message)
    assert len(clipped) < mcp_server.ERROR_MESSAGE_MAX_CHARS + 100
    assert clipped.startswith("Unknown provider: ")
    assert clipped.endswith(" Use get_capabilities.")
    assert mcp_server.clip_error_message("short") == "short"
