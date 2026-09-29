"""系列グラフの Mermaid 化(ADR-0023 9章)の単体テスト。DB を使わず、ノードと辺を直接組む。"""

from __future__ import annotations

import re
import uuid

from app.domain.lineage_mermaid import (
    LineageGraph,
    MermaidOptions,
    escape_label,
    render_lineage_mermaid,
)
from app.domain.schemas import (
    AssetLineageResponse,
    LineageAssetInfo,
    LineageEdge,
    LineageNode,
    LineageRunInfo,
    RunLineageResponse,
)

# ノードの行は「引用符で囲んだラベル1つ」だけを持つ。ラベルの中に `"` が残っていれば
# この形に一致しない。
_ASSET_LINE = re.compile(r'^    a\d+\("[^"]*"\)$')
_RUN_LINE = re.compile(r'^    r\d+\{\{"[^"]*"\}\}$')
_NOTE_LINE = re.compile(r'^    (m\d+|truncated)\["[^"]*"\]$')
_EDGE_LINE = re.compile(r"^    ([arm]\d+) (-->|-\.->)(\|[a-z #0-9,]+\|)? ([arm]\d+)$")
_CLASS_LINE = re.compile(r"^    (classDef \w+ [^\n]+|class [\w,]+ \w+)$")


def _asset(
    depth: int, kind: str = "generated", w: int = 1024, h: int = 1024, **extra: object
) -> LineageNode:
    return LineageNode(
        id=uuid.uuid4(),
        type="asset",
        depth=depth,
        asset=LineageAssetInfo(kind=kind, width=w, height=h, mime="image/png"),
        **extra,
    )


def _run(depth: int, prompt: str = "a cat", **extra: object) -> LineageNode:
    status = extra.pop("status", "succeeded")
    return LineageNode(
        id=uuid.uuid4(),
        type="run",
        depth=depth,
        run=LineageRunInfo(
            operation=extra.pop("operation", "edit"),
            model=extra.pop("model", "gpt-image-2"),
            model_label=extra.pop("model_label", None),
            status=status,
            prompt=prompt,
        ),
        **extra,
    )


def _in(src: LineageNode, run: LineageNode, role: str = "image", position: int = 0) -> LineageEdge:
    return LineageEdge(
        source=src.id,
        target=run.id,
        kind="input",
        role=role,
        position=position,
        primary=role == "image" and position == 0,
    )


def _out(run: LineageNode, dst: LineageNode, index: int = 0) -> LineageEdge:
    return LineageEdge(source=run.id, target=dst.id, kind="output", output_index=index)


def _assert_well_formed(text: str) -> None:
    lines = text.rstrip("\n").split("\n")
    assert lines[0].startswith("flowchart ")
    for line in lines[1:]:
        if line.startswith("%%"):
            continue
        assert any(
            p.match(line) for p in (_ASSET_LINE, _RUN_LINE, _NOTE_LINE, _EDGE_LINE, _CLASS_LINE)
        ), line


def _two_step() -> tuple[LineageGraph, dict[str, LineageNode]]:
    upload = _asset(-2, kind="upload", w=64, h=48)
    mask = _asset(-2, kind="mask", w=64, h=48)
    ref = _asset(-2, kind="upload", w=32, h=32)
    run = _run(-1, prompt="make it sunset", model="gpt-image-2", model_label="GPT Image 2")
    out = _asset(0)
    child_run = _run(1, prompt="add a moon", status="running")
    lineage = AssetLineageResponse(
        root_asset_id=out.id,
        nodes=[out, run, upload, mask, ref, child_run],
        edges=[
            _out(run, out),
            _in(upload, run),
            _in(mask, run, role="mask"),
            _in(ref, run, position=1),
            _in(out, child_run),
        ],
    )
    nodes = {
        "upload": upload,
        "mask": mask,
        "ref": ref,
        "run": run,
        "out": out,
        "child_run": child_run,
    }
    return LineageGraph.from_asset_lineage(lineage), nodes


def test_renders_nodes_edges_and_id_comments() -> None:
    graph, n = _two_step()
    text = render_lineage_mermaid(graph)
    _assert_well_formed(text)

    # 祖先側から番号を振る。depth -2 の Asset が a1〜a3、Run は r1, r2。
    assert f"%% a1 = asset {n['upload'].id}" in text
    assert f"%% a2 = asset {n['mask'].id}" in text
    assert f"%% a3 = asset {n['ref'].id}" in text
    assert f"%% r1 = run {n['run'].id}" in text
    assert f"%% a4 = asset {n['out'].id} (focus)" in text
    assert f"%% r2 = run {n['child_run'].id}" in text

    assert f'    a1("asset {str(n["upload"].id)[:8]}<br/>upload 64x48")' in text
    assert "a4(" in text
    # Run は六角形。表示名・プロンプトの冒頭、成功以外の状態。
    assert (
        f'    r1{{{{"run {str(n["run"].id)[:8]}<br/>edit GPT Image 2<br/>“make it sunset”"}}}}'
        in text
    )
    assert "edit gpt-image-2 (running)" in text

    assert "    a1 -->|primary| r1" in text
    assert "    a2 -->|mask| r1" in text
    assert "    a3 -->|reference| r1" in text
    assert "    r1 -->|output| a4" in text
    assert "    a4 -->|primary| r2" in text
    assert "    class a4 focus" in text


def test_prompt_cannot_inject_mermaid_syntax() -> None:
    evil = 'x"]; click a1 call x\nr9{{"pwn"}} %% <b>|a|</b> #quot; `md` [z] {y}'
    run = _run(0, prompt=evil, model='m"]\nclick a1 call y')
    out = _asset(1)
    graph = LineageGraph.from_run_lineage(
        RunLineageResponse(root_run_id=run.id, nodes=[run, out], edges=[_out(run, out)])
    )
    text = render_lineage_mermaid(graph, MermaidOptions(prompt_chars=200))
    _assert_well_formed(text)
    lines = text.split("\n")
    # 利用者のデータから行や文が増えない。
    assert not any(line.lstrip().startswith("click") for line in lines)
    assert sum(1 for line in lines if line.startswith("    r")) == 2  # ノード1行 + 辺1行
    run_line = next(line for line in lines if line.startswith("    r1{{"))
    for ch in ("[", "]", "<b>", "|", "`", "\n"):
        assert ch not in run_line.split('"', 1)[1].rsplit('"', 1)[0].replace("<br/>", "")
    assert "#quot;" in run_line
    # `#quot;` と書かれた文字列も、そのまま entity として解釈されないよう `#` を逃がす。
    assert "#35;quot;" in run_line


def test_escape_label() -> None:
    assert escape_label('a"b') == "a#quot;b"
    assert escape_label("[x]{y}|<z>") == "#91;x#93;#123;y#125;#124;#lt;z#gt;"
    assert escape_label("a\nb\r\n\tc") == "a b c"
    assert escape_label("#1") == "#35;1"
    assert escape_label("a&b`c") == "a#amp;b#96;c"


def test_deleted_and_embedded_nodes_are_marked() -> None:
    root = _asset(0, kind="upload")
    emb_run = _run(-1, embedded=True, instance="other")
    emb_asset = _asset(-2, embedded=True, instance="other", resolved_asset_id=uuid.uuid4())
    gone = _asset(1, deleted=True)
    child = _run(1)
    graph = LineageGraph.from_asset_lineage(
        AssetLineageResponse(
            root_asset_id=root.id,
            nodes=[root, emb_run, emb_asset, gone, child],
            edges=[_in(emb_asset, emb_run), _out(emb_run, root), _in(root, child)],
        )
    )
    text = render_lineage_mermaid(graph)
    _assert_well_formed(text)
    assert "(embedded, unverified)" in text
    assert "(deleted)" in text
    assert "embedded, unverified; imported here as asset" in text
    assert re.search(r"class [ar]\d+,[ar]\d+ embedded", text)
    assert re.search(r"class a\d+ deleted", text)


def test_truncated_and_frontier_notes() -> None:
    graph, n = _two_step()
    graph = LineageGraph(
        nodes=graph.nodes,
        edges=graph.edges,
        focus_ids=graph.focus_ids,
        truncated=True,
        more_ancestors=frozenset({n["upload"].id}),
        more_descendants=frozenset({n["child_run"].id}),
    )
    text = render_lineage_mermaid(graph)
    _assert_well_formed(text)
    assert "%% truncated:" in text
    assert '    truncated["… truncated: node limit reached"]' in text
    assert '    m1["… older ancestors"]' in text
    assert "    m1 -.-> a1" in text
    assert '    m2["… more descendants"]' in text
    assert "    r2 -.-> m2" in text


def test_options() -> None:
    graph, n = _two_step()
    text = render_lineage_mermaid(
        graph,
        MermaidOptions(
            direction="TB",
            id_comments=False,
            legend_comment=False,
            prompt_chars=0,
            edge_labels=False,
            styles=False,
            show_run_model=False,
            show_asset_size=False,
        ),
    )
    _assert_well_formed(text)
    assert text.startswith("flowchart TB\n")
    assert "%%" not in text
    assert "sunset" not in text
    assert "GPT Image 2" not in text
    assert "64x48" not in text
    assert "|primary|" not in text
    assert "    a1 --> r1" in text
    assert "classDef" not in text


def test_max_nodes_keeps_focus_and_nearest() -> None:
    graph, n = _two_step()
    text = render_lineage_mermaid(graph, MermaidOptions(max_nodes=3))
    _assert_well_formed(text)
    assert "%% truncated:" in text
    assert str(n["out"].id) in text
    assert str(n["run"].id) in text
    assert str(n["child_run"].id) in text
    assert str(n["upload"].id) not in text


def test_multiple_outputs_are_numbered() -> None:
    run = _run(0, operation="generate")
    a = _asset(1)
    b = _asset(1)
    graph = LineageGraph.from_run_lineage(
        RunLineageResponse(
            root_run_id=run.id, nodes=[run, a, b], edges=[_out(run, a), _out(run, b, 1)]
        )
    )
    text = render_lineage_mermaid(graph)
    assert "    r1 -->|output #0| a1" in text
    assert "    r1 -->|output #1| a2" in text
    assert "    class r1 focus" in text
