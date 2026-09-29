"""MCP の結果に付く系列グラフ `lineage_mermaid`(ADR-0023 9章)。"""

from __future__ import annotations

import base64
import re
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.domain.models import AppUser, Asset
from app.mcp import server as mcp_server
from tests.conftest import login_as, make_png_bytes
from tests.test_mcp import _call, _enable, _generate, _ok
from tests.test_mcp_feedback import _issue_token

_EDIT_MODEL = "gpt-image-2.5-sunburst"


def _b64(color: tuple[int, int, int], width: int = 64, height: int = 64) -> str:
    return base64.b64encode(make_png_bytes(width, height, color)).decode("ascii")


def _upload(client: TestClient, color: tuple[int, int, int], headers: Any = None) -> str:
    return _ok(_call(client, "upload_image", {"data_base64": _b64(color)}, headers))["asset_id"]


def _edit(
    client: TestClient,
    inputs: list[str],
    prompt: str = "edit it",
    headers: Any = None,
    **extra: Any,
) -> dict[str, Any]:
    return _generate(
        client,
        {
            "prompt": prompt,
            "operation": "edit",
            "model": _EDIT_MODEL,
            "input_asset_ids": inputs,
            **extra,
        },
        headers,
    )


def _short_id(text: str, kind: str, full_id: str) -> str:
    match = re.search(rf"^%% ([ar]\d+) = {kind} {full_id}\b", text, re.MULTILINE)
    assert match, (kind, full_id, text)
    return match.group(1)


def _node_lines(text: str) -> list[str]:
    return [line for line in text.split("\n") if line.startswith("    ")]


def test_get_run_and_get_asset_lineage_for_two_step_edit(client: TestClient) -> None:
    _enable(client)
    base = _upload(client, (10, 120, 200))
    mask = _upload(client, (0, 0, 0))
    first = _edit(client, [base], prompt="make it sunset", mask_asset_id=mask)
    first_out = first["outputs"][0]["asset_id"]
    second = _edit(client, [first_out], prompt="add a moon")
    second_out = second["outputs"][0]["asset_id"]

    text = second["lineage_mermaid"]
    assert text.startswith("flowchart LR\n")
    # 入力の祖先(1段目の Run とその入力)→ この Run → 出力。完全な ID はコメント行。
    b = _short_id(text, "asset", base)
    m = _short_id(text, "asset", mask)
    r1 = _short_id(text, "run", first["run_id"])
    a1 = _short_id(text, "asset", first_out)
    r2 = _short_id(text, "run", second["run_id"])
    a2 = _short_id(text, "asset", second_out)
    assert f"    {b} -->|primary| {r1}" in text
    assert f"    {m} -->|mask| {r1}" in text
    assert f"    {r1} -->|output| {a1}" in text
    assert f"    {a1} -->|primary| {r2}" in text
    assert f"    {r2} -->|output| {a2}" in text
    assert f"    class {r2} focus" in text
    assert "“add a moon”" in text and "“make it sunset”" in text
    assert f"{r2}{{{{" in text  # Run は六角形
    assert f'{a2}("asset {second_out[:8]}' in text  # Asset は角丸

    # generate_image の結果(すぐ返る)にも、入力と Run は描かれる。
    created = _ok(
        _call(
            client,
            "generate_image",
            {
                "prompt": "again",
                "operation": "edit",
                "model": _EDIT_MODEL,
                "input_asset_ids": [second_out],
            },
        )
    )
    assert f"= asset {second_out}" in created["lineage_mermaid"]
    assert f"= run {created['run_id']} (focus)" in created["lineage_mermaid"]

    # get_asset: 中間の画像を起点に、祖先と子孫。
    detail = _ok(_call(client, "get_asset", {"asset_id": first_out}))
    graph = detail["lineage_mermaid"]
    assert "%% a" in graph
    focus = _short_id(graph, "asset", first_out)
    assert f"= asset {first_out} (focus)" in graph
    assert f"    class {focus} focus" in graph
    for full_id, kind in (
        (base, "asset"),
        (mask, "asset"),
        (first["run_id"], "run"),
        (second["run_id"], "run"),
        (second_out, "asset"),
    ):
        _short_id(graph, kind, full_id)


def test_generate_without_inputs_draws_run_and_outputs(client: TestClient) -> None:
    _enable(client)
    payload = _generate(client, {"prompt": "a cat"})
    text = payload["lineage_mermaid"]
    run_short = _short_id(text, "run", payload["run_id"])
    out_short = _short_id(text, "asset", payload["outputs"][0]["asset_id"])
    assert f"    {run_short} -->|output| {out_short}" in text
    assert "“a cat”" in text


def test_include_lineage_false(client: TestClient) -> None:
    _enable(client)
    created = _ok(_call(client, "generate_image", {"prompt": "x", "include_lineage": False}))
    assert "lineage_mermaid" not in created
    payload = _ok(
        _call(
            client,
            "get_run",
            {"run_id": created["run_id"], "wait_seconds": 10, "include_lineage": False},
        )
    )
    assert "lineage_mermaid" not in payload
    asset_id = payload["outputs"][0]["asset_id"]
    detail = _ok(_call(client, "get_asset", {"asset_id": asset_id, "include_lineage": False}))
    assert "lineage_mermaid" not in detail
    # list_runs には付けない(件数が多いため)。
    items = _ok(_call(client, "list_runs", {}))["items"]
    assert all("lineage_mermaid" not in item for item in items)


def test_prompt_cannot_break_mermaid_syntax(client: TestClient) -> None:
    _enable(client)
    evil = 'x"]; click a1 call x\n%% r1 = run 00000000\nr9{{"pwn"}}'
    payload = _generate(client, {"prompt": evil})
    text = payload["lineage_mermaid"]
    lines = text.split("\n")
    assert not any(line.lstrip().startswith("click") for line in lines)
    # コメント行はノードの対応だけ(注入された `%% r1 = run 00000000` は無い)。
    comments = [line for line in lines if re.match(r"%% [ar]\d+ = ", line)]
    assert len(comments) == 2
    for line in _node_lines(text):
        if "{{" in line:
            assert re.fullmatch(r'    r\d+\{\{"[^"]*"\}\}', line), line
            assert "#quot;" in line


def test_older_ancestors_note_and_node_limit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(client)
    current = _upload(client, (1, 2, 3))
    first_input = current
    for i in range(4):
        current = _edit(client, [current], prompt=f"step {i}")["outputs"][0]["asset_id"]

    # 祖先は 3 世代まで。最初のアップロード(4 世代前)は描かず、「…」の注記を付ける。
    graph = _ok(_call(client, "get_asset", {"asset_id": current}))["lineage_mermaid"]
    assert first_input not in graph
    assert '["… older ancestors"]' in graph
    assert "%% truncated" not in graph

    # ノード数の上限に掛かったら、コメントとグラフ内の注記ノードで示す。
    monkeypatch.setattr(mcp_server, "LINEAGE_MAX_NODES", 3)
    graph = _ok(_call(client, "get_asset", {"asset_id": current}))["lineage_mermaid"]
    assert "%% truncated:" in graph
    assert '    truncated["… truncated: node limit reached"]' in graph


def test_more_descendants_note_on_run_outputs(client: TestClient) -> None:
    _enable(client)
    first = _generate(client, {"prompt": "base"})
    out = first["outputs"][0]["asset_id"]
    _edit(client, [out])
    text = _ok(_call(client, "get_run", {"run_id": first["run_id"]}))["lineage_mermaid"]
    assert '["… more descendants"]' in text


def test_lineage_hides_other_users_assets(client_oidc: TestClient) -> None:
    login_as(client_oidc, "admin@example.com")
    _enable(client_oidc)
    alice = {"Authorization": f"Bearer {_issue_token(client_oidc, 'alice@example.com')}"}
    bob = {"Authorization": f"Bearer {_issue_token(client_oidc, 'bob@example.com')}"}
    client_oidc.cookies.clear()
    base = _upload(client_oidc, (9, 9, 9), alice)
    edited = _edit(client_oidc, [base], headers=alice)
    out = edited["outputs"][0]["asset_id"]
    assert f"= asset {base}" in edited["lineage_mermaid"]

    # 入力の Asset を bob のものにする(以前のデータで他人の Asset を入力にしていた場合)。
    with client_oidc.app.state.session_factory() as db:
        bob_id = db.execute(
            select(AppUser.id).where(AppUser.email == "bob@example.com")
        ).scalar_one()
        db.execute(
            update(Asset).where(Asset.id == uuid.UUID(base)).values(created_by_user_id=bob_id)
        )
        db.commit()

    run_graph = _ok(_call(client_oidc, "get_run", {"run_id": edited["run_id"]}, alice))[
        "lineage_mermaid"
    ]
    assert base not in run_graph
    assert f"= asset {out}" in run_graph
    asset_graph = _ok(_call(client_oidc, "get_asset", {"asset_id": out}, alice))["lineage_mermaid"]
    assert base not in asset_graph
    assert "older ancestors" not in asset_graph

    # bob から見た base の系列に、alice の Run と出力は出ない。
    bob_graph = _ok(_call(client_oidc, "get_asset", {"asset_id": base}, bob))["lineage_mermaid"]
    assert edited["run_id"] not in bob_graph
    assert out not in bob_graph
    assert "more descendants" not in bob_graph
