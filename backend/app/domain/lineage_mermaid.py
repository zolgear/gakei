"""系列グラフ(Asset と Run の二部グラフ。ADR-0003)を Mermaid の `flowchart` の文字列にする
(ADR-0023 9章)。

DB・Session・MCP の型を受け取らない純粋関数だけを置く。入力は `LineageNode` / `LineageEdge`
の列(`AssetLineageResponse` / `RunLineageResponse` から `LineageGraph` を作れる)で、表示の
選び方は `MermaidOptions` で渡す。MCP 以外(画面の書き出しなど)からも使えるようにしてある。

プロンプトやモデル名は利用者のデータなので、ラベルに入れる文字はすべて Mermaid の
エンティティ(`#quot;` / `#91;` など)に置き換え、引用符で囲んだラベルから抜け出せない
ようにする。辺のラベルやクラス名など、固定の文字列だけを構文の側に置く。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from app.domain.schemas import (
    AssetLineageResponse,
    LineageEdge,
    LineageNode,
    RunLineageResponse,
)

Direction = Literal["LR", "RL", "TB", "BT"]


@dataclass(frozen=True)
class MermaidOptions:
    """表示の選択。既定値は汎用のもので、MCP 用の値は MCP 側で決める。"""

    # グラフの向き(`flowchart LR` など)。
    direction: Direction = "LR"
    # 起点(`LineageGraph.focus_ids`)を太枠で強調する。
    highlight_focus: bool = True
    # 先頭に `%% a1 = asset <完全な UUID>` の対応を書く。
    id_comments: bool = True
    # 先頭に形と矢印の読み方を1行のコメントで書く。
    legend_comment: bool = True
    # Asset のラベルに種類(generated / upload / mask / sketch)と大きさを入れる。
    show_asset_kind: bool = True
    show_asset_size: bool = True
    # Run のラベルにモデル(表示名があればそれ)と、成功以外の状態を入れる。
    show_run_model: bool = True
    show_run_status: bool = True
    # Run のラベルに入れるプロンプトの冒頭の文字数(0 で入れない)。
    prompt_chars: int = 40
    # 辺に入力の役割(primary / reference / mask)や output を書く。
    edge_labels: bool = True
    # classDef で起点・削除済み・埋め込み(未検証)・注記を描き分ける。
    styles: bool = True
    # 描くノード数の上限(None で上限なし)。超えたら起点から遠いノードを落とし、打ち切りと
    # して注記する。探索側の上限とは別の、描画側の安全弁。
    max_nodes: int | None = None


@dataclass(frozen=True)
class LineageGraph:
    """描画の入力。`nodes` / `edges` は系列グラフのノードと辺そのもの。

    - `focus_ids`: 強調するノード(起点)。
    - `truncated`: 探索がノード数の上限で打ち切られた。
    - `more_ancestors` / `more_descendants`: 世代の上限などで、その先の祖先 / 子孫を
      描いていないノード。グラフ内に「…」の注記ノードを付ける。
    """

    nodes: Sequence[LineageNode]
    edges: Sequence[LineageEdge]
    focus_ids: frozenset[uuid.UUID] = field(default_factory=frozenset)
    truncated: bool = False
    more_ancestors: frozenset[uuid.UUID] = field(default_factory=frozenset)
    more_descendants: frozenset[uuid.UUID] = field(default_factory=frozenset)

    @classmethod
    def from_asset_lineage(
        cls,
        lineage: AssetLineageResponse,
        *,
        more_ancestors: Iterable[uuid.UUID] = (),
        more_descendants: Iterable[uuid.UUID] = (),
    ) -> LineageGraph:
        return cls(
            nodes=lineage.nodes,
            edges=lineage.edges,
            focus_ids=frozenset({lineage.root_asset_id}),
            truncated=lineage.truncated,
            more_ancestors=frozenset(more_ancestors),
            more_descendants=frozenset(more_descendants),
        )

    @classmethod
    def from_run_lineage(
        cls,
        lineage: RunLineageResponse,
        *,
        more_ancestors: Iterable[uuid.UUID] = (),
        more_descendants: Iterable[uuid.UUID] = (),
    ) -> LineageGraph:
        return cls(
            nodes=lineage.nodes,
            edges=lineage.edges,
            focus_ids=frozenset({lineage.root_run_id}),
            truncated=lineage.truncated,
            more_ancestors=frozenset(more_ancestors),
            more_descendants=frozenset(more_descendants),
        )


# ラベルの中で意味を持つ文字を Mermaid のエンティティにする。`#` は他のエンティティの
# 先頭になるので最初に置き換える(辞書の順で処理する)。引用符で囲んだラベルの中では
# `"` 以外は構文を壊さないが、利用者のデータに現れる括弧類・`|`・`<>` はまとめて逃がす。
_ESCAPES: dict[str, str] = {
    "#": "#35;",
    '"': "#quot;",
    "&": "#amp;",
    "<": "#lt;",
    ">": "#gt;",
    "[": "#91;",
    "]": "#93;",
    "{": "#123;",
    "}": "#125;",
    "|": "#124;",
    "`": "#96;",
}

_CLASS_DEFS: dict[str, str] = {
    "focus": "stroke-width:3px,stroke:#d9480f",
    "deleted": "stroke-dasharray:4 3,color:#868e96",
    "embedded": "stroke-dasharray:2 2,fill:#fff8e1",
    "note": "fill:#f1f3f5,stroke:#adb5bd,color:#495057",
}

_LEGEND = (
    "%% GAKEI lineage: (rounded box) = asset (image), {{hexagon}} = run (one API call); "
    "arrows go input asset -> run -> output asset. Full IDs are listed below."
)


def escape_label(text: str) -> str:
    """利用者のデータを、引用符で囲んだ Mermaid のラベルに安全に入れられる形にする。

    改行・タブなどの制御文字は空白1つにまとめる。
    """
    out: list[str] = []
    prev_space = False
    for ch in text:
        if ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F:
            if not prev_space:
                out.append(" ")
            prev_space = True
            continue
        prev_space = False
        out.append(_ESCAPES.get(ch, ch))
    return "".join(out).strip()


def _short(value: uuid.UUID) -> str:
    return str(value)[:8]


def _preview(text: str, limit: int) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[:limit].rstrip() + "…"


def _asset_label(node: LineageNode, options: MermaidOptions) -> str:
    # 固定の文字列はそのまま、利用者やファイル由来の値だけを `escape_label` に通す。
    parts = [f"asset {_short(node.id)}"]
    info = node.asset
    detail: list[str] = []
    if info is not None:
        if options.show_asset_kind and info.kind:
            detail.append(escape_label(info.kind))
        if options.show_asset_size and info.width and info.height:
            detail.append(f"{int(info.width)}x{int(info.height)}")
    if detail:
        parts.append(" ".join(detail))
    parts.extend(_flags(node))
    return "<br/>".join(parts)


def _run_label(node: LineageNode, options: MermaidOptions) -> str:
    parts = [f"run {_short(node.id)}"]
    info = node.run
    if info is not None:
        line = [escape_label(info.operation)]
        if options.show_run_model:
            line.append(escape_label(info.model_label or info.model))
        if node.embedded and info.provider:
            line.append(f"via {escape_label(info.provider)}")
        if options.show_run_status and info.status != "succeeded":
            line.append(f"({escape_label(info.status)})")
        parts.append(" ".join(p for p in line if p))
        if options.prompt_chars > 0 and info.prompt.strip():
            parts.append(f"“{escape_label(_preview(info.prompt, options.prompt_chars))}”")
    parts.extend(_flags(node))
    return "<br/>".join(parts)


def _flags(node: LineageNode) -> list[str]:
    flags: list[str] = []
    if node.deleted:
        flags.append("(deleted)")
    if node.embedded:
        flags.append("(embedded, unverified)")
    if node.local_hidden:
        flags.append("(not visible)")
    return flags


def _edge_label(edge: LineageEdge, output_counts: dict[uuid.UUID, int]) -> str:
    if edge.kind == "input":
        if edge.role == "mask":
            return "mask"
        if edge.role == "image" and edge.primary:
            return "primary"
        return "reference"
    if edge.kind == "output":
        if output_counts.get(edge.source, 0) > 1 and edge.output_index is not None:
            return f"output #{edge.output_index}"
        return "output"
    if edge.kind == "sketch_source":
        return "sketch base"
    return "origin, unverified"


def _select_nodes(graph: LineageGraph, max_nodes: int | None) -> tuple[list[LineageNode], bool]:
    nodes = list(graph.nodes)
    if max_nodes is None or len(nodes) <= max_nodes:
        return nodes, False
    # 起点を残し、起点から近い(|depth| が小さい)ノードから順に残す。
    order = sorted(
        range(len(nodes)),
        key=lambda i: (nodes[i].id not in graph.focus_ids, abs(nodes[i].depth), i),
    )
    keep = set(order[: max(max_nodes, 0)])
    return [n for i, n in enumerate(nodes) if i in keep], True


def render_lineage_mermaid(graph: LineageGraph, options: MermaidOptions | None = None) -> str:
    """系列グラフを Mermaid の `flowchart` の文字列にする。

    ノード ID は Asset が `a1, a2, …`、Run が `r1, r2, …`(祖先側から順に)。完全な ID は
    `%% a1 = asset <UUID>` のコメント行で示す(`options.id_comments`)。
    """
    opts = options or MermaidOptions()
    nodes, cut_by_render = _select_nodes(graph, opts.max_nodes)
    truncated = graph.truncated or cut_by_render

    # 祖先側から番号を振る(同じ深さは与えられた順)。
    ordered = sorted(enumerate(nodes), key=lambda p: (p[1].depth, p[0]))
    ids: dict[uuid.UUID, str] = {}
    counters = {"asset": 0, "run": 0}
    for _, node in ordered:
        if node.id in ids:
            continue
        counters[node.type] += 1
        ids[node.id] = f"{'a' if node.type == 'asset' else 'r'}{counters[node.type]}"
    by_id = {n.id: n for _, n in ordered}

    lines: list[str] = [f"flowchart {opts.direction}"]
    if opts.legend_comment:
        lines.append(_LEGEND)
    if opts.id_comments:
        for node_id, short in ids.items():
            node = by_id[node_id]
            comment = f"%% {short} = {node.type} {node_id}"
            notes: list[str] = []
            if node.id in graph.focus_ids:
                notes.append("focus")
            if node.deleted:
                notes.append("deleted")
            if node.embedded:
                notes.append("embedded, unverified")
            if node.resolved_asset_id is not None:
                notes.append(f"imported here as asset {node.resolved_asset_id}")
            if notes:
                comment += f" ({'; '.join(notes)})"
            lines.append(comment)
    if truncated:
        lines.append(
            "%% truncated: the node limit was reached; some ancestors or descendants are not shown"
        )

    classes: dict[str, list[str]] = {name: [] for name in _CLASS_DEFS}

    for node_id, short in ids.items():
        node = by_id[node_id]
        if node.type == "asset":
            lines.append(f'    {short}("{_asset_label(node, opts)}")')
        else:
            lines.append(f'    {short}{{{{"{_run_label(node, opts)}"}}}}')
        if opts.highlight_focus and node.id in graph.focus_ids:
            classes["focus"].append(short)
        if node.deleted:
            classes["deleted"].append(short)
        if node.embedded:
            classes["embedded"].append(short)

    edges = [e for e in graph.edges if e.source in ids and e.target in ids]
    output_counts: dict[uuid.UUID, int] = {}
    for e in edges:
        if e.kind == "output":
            output_counts[e.source] = output_counts.get(e.source, 0) + 1
    seen: set[tuple[str, str, str]] = set()
    for e in edges:
        label = _edge_label(e, output_counts)
        key = (ids[e.source], ids[e.target], label)
        if key in seen:
            continue
        seen.add(key)
        arrow = "-.->" if e.kind == "origin" else "-->"
        if opts.edge_labels:
            lines.append(f"    {ids[e.source]} {arrow}|{label}| {ids[e.target]}")
        else:
            lines.append(f"    {ids[e.source]} {arrow} {ids[e.target]}")

    # 描いていない先があることを、グラフの中の注記ノードで示す。
    note_count = 0
    for node_id, short in ids.items():
        if node_id in graph.more_ancestors:
            note_count += 1
            note = f"m{note_count}"
            lines.append(f'    {note}["… older ancestors"]')
            lines.append(f"    {note} -.-> {short}")
            classes["note"].append(note)
        if node_id in graph.more_descendants:
            note_count += 1
            note = f"m{note_count}"
            lines.append(f'    {note}["… more descendants"]')
            lines.append(f"    {short} -.-> {note}")
            classes["note"].append(note)
    if truncated:
        lines.append('    truncated["… truncated: node limit reached"]')
        classes["note"].append("truncated")

    if opts.styles:
        for name, members in classes.items():
            if members:
                lines.append(f"    classDef {name} {_CLASS_DEFS[name]}")
                lines.append(f"    class {','.join(members)} {name}")

    return "\n".join(lines) + "\n"
