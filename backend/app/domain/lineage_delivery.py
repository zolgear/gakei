"""納品用の書き出し(ADR-0037 4章)の `index.html` と `README.txt` を作る。

GAKEI を使っていない納品先に、主画像(起点)の証跡として系列を渡すための文書。

- `index.html`: ZIP を展開してブラウザで開く1枚のページ。オフラインで読める(外部の CSS・JS・
  フォントを読まない。画像は同じ ZIP の `assets/` を相対パスで参照する)。系列の図はここで
  SVG として作り、JS が無くても見られる。言語は書き出した利用者の表示言語(ADR-0015)。
- `README.txt`: GAKEI の説明と ZIP の中身の説明。言語にかかわらず日本語と英語を併記する。

どちらも `ExportPlan` の manifest(秘密に見える値はもう伏せてある。1章)と、取り込んだ Run の
記録(`imported_runs`)だけから作る。DB には触れない。

HTML はテンプレートエンジンを使わず、この中の小さな関数で組み立てる。利用者のデータ(プロンプト、
パラメーター、モデル名、表示名、ファイル名、ID など)は、HTML に入れるところで必ず `_e`
(`html.escape`。引用符も置き換える)を通す。固定の文言(ロケールの JSON)も同じく通す。
"""

from __future__ import annotations

import html
import json
import math
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.domain.lineage_export import ExportPlan, ImportedRunInfo
from app.i18n import Locale, t, use_locale

INDEX_HTML_NAME = "index.html"
README_NAME = "README.txt"
REPOSITORY_URL = "https://github.com/zolgear/gakei"


def _e(value: Any) -> str:
    """HTML のテキストと属性値に入れる文字列にする(`&` `<` `>` `"` `'` を置き換える)。"""
    return html.escape("" if value is None else str(value), quote=True)


# -- 日時 ------------------------------------------------------------------------


def resolve_timezone(name: str | None) -> tzinfo:
    """ブラウザが知らせたタイムゾーン(IANA の名前)。分からなければ UTC。"""
    if not name or len(name) > 64:
        return UTC
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return UTC


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _time(value: Any, tz: tzinfo) -> str:
    """`<time>` 要素(機械向けに ISO、人向けに指定のタイムゾーンの日時)。"""
    if isinstance(value, datetime):
        moment: datetime | None = value if value.tzinfo else value.replace(tzinfo=UTC)
    else:
        moment = _parse_iso(value)
    if moment is None:
        return '<span class="muted">—</span>'
    local = moment.astimezone(tz)
    label = local.strftime("%Y-%m-%d %H:%M:%S ") + (local.tzname() or "")
    return f'<time datetime="{_e(moment.astimezone(UTC).isoformat())}">{_e(label.strip())}</time>'


# -- 系列の図の配置 ----------------------------------------------------------------

# 1つの枠の大きさと間隔(SVG の単位)。
_ASSET_W = 132
_ASSET_H = 132
_THUMB = 96
_RUN_W = 168
_RUN_H = 56
_COL_W = 188
_ROW_H = 176
_MARGIN = 24


@dataclass
class GraphNode:
    key: str  # "a:{id}" または "r:{id}"
    kind: str  # "asset" / "run"
    ident: str
    layer: int = 0
    order: float = 0.0
    x: float = 0.0
    y: float = 0.0


@dataclass(frozen=True)
class GraphEdge:
    source: str
    target: str
    # "image" / "mask" / "reference"(入力)、"output"、"sketch_source"
    label: str
    primary: bool = False


@dataclass
class GraphLayout:
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    width: float
    height: float

    def node(self, key: str) -> GraphNode:
        return next(n for n in self.nodes if n.key == key)


def layout_graph(manifest: dict[str, Any]) -> GraphLayout:
    """manifest の Asset と Run を二部グラフにし、段に並べる(アプリの系列グラフと同じく、上が
    祖先、下が子孫)。段は入力の側からの最長の道の長さ。段の中は、親の並びの平均 → 作成日時 →
    ID の順。自動レイアウトのライブラリは使わない(ADR-0009)。"""
    assets = [a for a in manifest.get("assets", []) if isinstance(a, dict)]
    runs = [r for r in manifest.get("runs", []) if isinstance(r, dict)]
    nodes: dict[str, GraphNode] = {}
    created: dict[str, str] = {}
    for a in assets:
        key = f"a:{a['id']}"
        nodes[key] = GraphNode(key=key, kind="asset", ident=str(a["id"]))
        created[key] = str(a.get("created_at") or "")
    for r in runs:
        key = f"r:{r['id']}"
        nodes[key] = GraphNode(key=key, kind="run", ident=str(r["id"]))
        created[key] = str(r.get("created_at") or "")

    edges: list[GraphEdge] = []
    for r in runs:
        rkey = f"r:{r['id']}"
        for i in r.get("inputs", []):
            akey = f"a:{i.get('asset_id')}"
            if akey in nodes:
                role = str(i.get("role") or "image")
                primary = role == "image" and i.get("position") == 0
                edges.append(GraphEdge(akey, rkey, role, primary))
    for a in assets:
        akey = f"a:{a['id']}"
        producer = a.get("produced_by_run_id")
        if producer and f"r:{producer}" in nodes:
            edges.append(GraphEdge(f"r:{producer}", akey, "output"))
        source = a.get("source_asset_id")
        if source and f"a:{source}" in nodes:
            edges.append(GraphEdge(f"a:{source}", akey, "sketch_source"))

    parents: dict[str, list[str]] = defaultdict(list)
    children: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        parents[edge.target].append(edge.source)
        children[edge.source].append(edge.target)

    # 段(Kahn 法で最長の道)。循環は取り込みの検証で断るが、念のため残ったものは 0 段にする。
    indegree = {k: len(parents[k]) for k in nodes}
    queue = deque(sorted((k for k, d in indegree.items() if d == 0), key=lambda k: created[k]))
    while queue:
        key = queue.popleft()
        for child in children[key]:
            nodes[child].layer = max(nodes[child].layer, nodes[key].layer + 1)
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)

    by_layer: dict[int, list[GraphNode]] = defaultdict(list)
    for node in nodes.values():
        by_layer[node.layer].append(node)
    layers = sorted(by_layer)
    max_count = max((len(v) for v in by_layer.values()), default=1)
    width = max_count * _COL_W + 2 * _MARGIN
    for layer in layers:
        row = by_layer[layer]

        def sort_key(node: GraphNode, layer: int = layer) -> tuple[float, str, str]:
            placed = [nodes[p].order for p in parents[node.key] if nodes[p].layer < layer]
            mean = sum(placed) / len(placed) if placed else math.inf
            return (mean, created[node.key], node.ident)

        row.sort(key=sort_key)
        offset = (width - len(row) * _COL_W) / 2
        for index, node in enumerate(row):
            node.x = offset + index * _COL_W + _COL_W / 2
            node.order = node.x
            node.y = _MARGIN + layer * _ROW_H + _ASSET_H / 2
    height = (len(layers) * _ROW_H if layers else _ROW_H) + 2 * _MARGIN - (_ROW_H - _ASSET_H)
    ordered = [n for layer in layers for n in by_layer[layer]]
    return GraphLayout(nodes=ordered, edges=edges, width=width, height=height)


# -- 文言 ------------------------------------------------------------------------


def _d(key: str, /, **params: Any) -> str:
    return t(f"lineageExport.delivery.{key}", **params)


def _kind_label(kind: Any) -> str:
    value = str(kind)
    if value in ("upload", "generated", "mask", "sketch"):
        return _d(f"kinds.{value}")
    return value


def _operation_label(operation: Any) -> str:
    value = str(operation)
    if value in ("generate", "edit"):
        return _d(f"operations.{value}")
    return value


def _status_label(status: Any) -> str:
    value = str(status)
    if value in ("queued", "running", "succeeded", "failed", "canceled"):
        return _d(f"statuses.{value}")
    return value


def _edge_label(edge: GraphEdge) -> str:
    if edge.label == "image" and edge.primary:
        return _d("edges.primary")
    if edge.label in ("image", "mask", "reference", "output", "sketch_source"):
        return _d(f"edges.{edge.label}")
    return edge.label


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


# -- SVG ------------------------------------------------------------------------


def _render_graph_svg(
    layout: GraphLayout,
    assets: dict[str, dict[str, Any]],
    runs: dict[str, dict[str, Any]],
    labels: dict[str, str],
    root_id: str,
    imported: dict[str, ImportedRunInfo],
) -> str:
    parts: list[str] = []
    w, h = layout.width, layout.height
    parts.append(
        f'<svg class="graph" role="img" '
        f'viewBox="0 0 {w:.0f} {h:.0f}" width="{w:.0f}" height="{h:.0f}" '
        f'aria-label="{_e(_d("graphTitle"))}">'
    )
    parts.append(
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="9" '
        'markerHeight="9" markerUnits="userSpaceOnUse" orient="auto-start-reverse">'
        '<path d="M0,0 L10,5 L0,10 z" '
        'class="arrow-head"/></marker></defs>'
    )

    def half_height(node: GraphNode) -> float:
        return (_ASSET_H if node.kind == "asset" else _RUN_H) / 2

    by_key = {n.key: n for n in layout.nodes}
    for edge in layout.edges:
        src, dst = by_key[edge.source], by_key[edge.target]
        x1, y1 = src.x, src.y + half_height(src)
        x2, y2 = dst.x, dst.y - half_height(dst) - 2
        mid = (y1 + y2) / 2
        classes = "edge"
        if edge.label == "output":
            classes += " edge-output"
        elif edge.label == "sketch_source":
            classes += " edge-source"
        elif edge.primary:
            classes += " edge-primary"
        parts.append(
            f'<path class="{classes}" d="M{x1:.1f},{y1:.1f} C{x1:.1f},{mid:.1f} '
            f'{x2:.1f},{mid:.1f} {x2:.1f},{y2:.1f}" marker-end="url(#arrow)"/>'
        )
        lx, ly = (x1 + x2) / 2, mid + 4
        parts.append(
            f'<text class="edge-label" x="{lx:.1f}" y="{ly:.1f}">{_e(_edge_label(edge))}</text>'
        )

    for node in layout.nodes:
        if node.kind == "asset":
            asset = assets[node.ident]
            is_root = node.ident == root_id
            x0, y0 = node.x - _ASSET_W / 2, node.y - _ASSET_H / 2
            cls = "node asset root" if is_root else "node asset"
            parts.append(f'<a href="#asset-{_e(node.ident)}" class="{cls}">')
            parts.append(
                f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{_ASSET_W}" height="{_ASSET_H}" rx="8"/>'
            )
            parts.append(
                f'<image href="{_e(asset.get("file"))}" x="{node.x - _THUMB / 2:.1f}" '
                f'y="{y0 + 8:.1f}" width="{_THUMB}" height="{_THUMB}" '
                'preserveAspectRatio="xMidYMid meet"/>'
            )
            caption = f"{labels[node.key]} · {_kind_label(asset.get('kind'))}"
            parts.append(
                f'<text class="node-label" x="{node.x:.1f}" y="{y0 + _ASSET_H - 12:.1f}">'
                f"{_e(caption)}</text>"
            )
            if is_root:
                # 「主画像」の札は枠の左上の内側に置く(上に置くと入ってくる矢印と重なる)。
                text = _d("mainImage")
                pill_w = sum(12 if ord(c) > 0x2E7F else 7 for c in text) + 12
                parts.append(
                    f'<rect class="root-pill" x="{x0 + 4:.1f}" y="{y0 + 4:.1f}" '
                    f'width="{pill_w}" height="18" rx="9"/>'
                    f'<text class="root-label" x="{x0 + 4 + pill_w / 2:.1f}" y="{y0 + 17:.1f}">'
                    f"{_e(text)}</text>"
                )
            parts.append("</a>")
        else:
            run = runs[node.ident]
            x0, y0 = node.x - _RUN_W / 2, node.y - _RUN_H / 2
            cls = "node run"
            if node.ident in imported:
                cls += " imported"
            if str(run.get("status")) != "succeeded":
                cls += " not-succeeded"
            parts.append(f'<a href="#run-{_e(node.ident)}" class="{cls}">')
            parts.append(
                f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{_RUN_W}" height="{_RUN_H}" '
                f'rx="{_RUN_H / 2:.0f}"/>'
            )
            title = f"{labels[node.key]} · {_operation_label(run.get('operation'))}"
            if node.ident in imported:
                title += f" · {_d('importedShort')}"
            parts.append(
                f'<text class="node-label" x="{node.x:.1f}" y="{node.y - 4:.1f}">{_e(title)}</text>'
            )
            sub = _truncate(str(run.get("model") or run.get("provider") or ""), 24)
            if str(run.get("status")) != "succeeded":
                sub = f"{_status_label(run.get('status'))} · {sub}"
            parts.append(
                f'<text class="node-sub" x="{node.x:.1f}" y="{node.y + 14:.1f}">{_e(sub)}</text>'
            )
            parts.append("</a>")
    parts.append("</svg>")
    return "".join(parts)


# -- HTML の部品 ------------------------------------------------------------------


def _row(label: str, value_html: str) -> str:
    """`<dt>`/`<dd>` の1行。`value_html` はエスケープ済みの HTML。"""
    return f'<div class="row"><dt>{_e(label)}</dt><dd>{value_html}</dd></div>'


def _code(value: Any) -> str:
    return f"<code>{_e(value)}</code>"


def _render_params(params: Any) -> str:
    """パラメーターを読みやすく並べる。数・文字列などはそのまま、入れ子の値は折りたたんだ JSON。"""
    if not isinstance(params, dict) or not params:
        return f'<p class="muted">{_e(_d("noParams"))}</p>'
    rows: list[str] = []
    nested: list[str] = []
    for key, value in params.items():
        if isinstance(value, (dict, list)):
            dumped = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False)
            nested.append(
                f'<details><summary>{_e(key)}</summary><pre class="json">{_e(dumped)}</pre>'
                "</details>"
            )
        else:
            shown = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
            rows.append(_row(str(key), f'<span class="param-value">{_e(shown)}</span>'))
    out = ""
    if rows:
        out += f'<dl class="params">{"".join(rows)}</dl>'
    out += "".join(nested)
    return out


def _asset_link(asset_id: str, labels: dict[str, str]) -> str:
    label = labels.get(f"a:{asset_id}", asset_id)
    return f'<a href="#asset-{_e(asset_id)}">{_e(label)}</a>'


def _run_link(run_id: str, labels: dict[str, str]) -> str:
    label = labels.get(f"r:{run_id}", run_id)
    return f'<a href="#run-{_e(run_id)}">{_e(label)}</a>'


def _render_asset_card(
    asset: dict[str, Any],
    labels: dict[str, str],
    root_id: str,
    tz: tzinfo,
) -> str:
    asset_id = str(asset["id"])
    is_root = asset_id == root_id
    file_name = str(asset.get("file") or "")
    width, height = asset.get("width"), asset.get("height")
    dims = f"{width} × {height} px" if width and height else "—"
    rows = [
        _row(_d("fields.fileName"), _code(file_name)),
        _row(_d("fields.sha256"), f'<code class="hash">{_e(asset.get("sha256"))}</code>'),
        _row(_d("fields.dimensions"), _e(dims)),
        _row(_d("fields.format"), _e(asset.get("mime"))),
        _row(_d("fields.bytes"), _e(_d("bytes", count=int(asset.get("bytes") or 0)))),
        _row(_d("fields.createdAt"), _time(asset.get("created_at"), tz)),
        _row(_d("fields.kind"), _e(_kind_label(asset.get("kind")))),
    ]
    producer = asset.get("produced_by_run_id")
    if producer:
        rows.append(_row(_d("fields.producedBy"), _run_link(str(producer), labels)))
    elif asset.get("kind") == "generated":
        # 範囲が `descendants` の起点など、作った実行を ZIP に含めていない生成画像。
        rows.append(_row(_d("fields.producedBy"), _e(_d("producerOutOfScope"))))
    source = asset.get("source_asset_id")
    if source:
        rows.append(_row(_d("fields.sketchSource"), _asset_link(str(source), labels)))
    rows.append(_row(_d("fields.id"), _code(asset_id)))
    badge = f' <span class="badge badge-root">{_e(_d("mainImage"))}</span>' if is_root else ""
    img_attrs = f' width="{_e(width)}" height="{_e(height)}"' if width and height else ""
    label = labels[f"a:{asset_id}"]
    card_class = "card asset-card is-root" if is_root else "card asset-card"
    return (
        f'<article class="{card_class}" id="asset-{_e(asset_id)}">'
        f'<a class="thumb" href="{_e(file_name)}"><img src="{_e(file_name)}" alt="{_e(label)}"'
        f'{img_attrs} loading="lazy"></a>'
        f'<div class="card-body"><h3>{_e(label)}{badge}</h3>'
        f'<dl class="fields">{"".join(rows)}</dl></div></article>'
    )


def _render_run_card(
    run: dict[str, Any],
    labels: dict[str, str],
    outputs: list[dict[str, Any]],
    imported: ImportedRunInfo | None,
    include_creator_names: bool,
    tz: tzinfo,
) -> str:
    run_id = str(run["id"])
    status = str(run.get("status"))
    badges = [
        f'<span class="badge status-{_e(status)}">{_e(_status_label(status))}</span>',
    ]
    if imported is not None:
        badges.append(f'<span class="badge badge-imported">{_e(_d("importedBadge"))}</span>')
    rows = [
        _row(_d("fields.provider"), _e(run.get("provider"))),
        _row(_d("fields.model"), _code(run.get("model"))),
        _row(_d("fields.operation"), _e(_operation_label(run.get("operation")))),
        _row(_d("fields.status"), _e(_status_label(status))),
        _row(_d("fields.runCreatedAt"), _time(run.get("created_at"), tz)),
        _row(_d("fields.runFinishedAt"), _time(run.get("finished_at"), tz)),
    ]
    if include_creator_names and run.get("created_by_name"):
        rows.append(_row(_d("fields.creator"), _e(run.get("created_by_name"))))
    inputs = run.get("inputs") or []
    if inputs:
        items = "".join(
            f"<li>{_asset_link(str(i.get('asset_id')), labels)} "
            f'<span class="muted">({_e(_role_label(i))})</span></li>'
            for i in inputs
        )
        rows.append(_row(_d("fields.inputs"), f'<ul class="links">{items}</ul>'))
    else:
        rows.append(_row(_d("fields.inputs"), f'<span class="muted">{_e(_d("none"))}</span>'))
    omitted = int(run.get("omitted_input_count") or 0)
    if omitted:
        rows.append(_row(_d("fields.omittedInputs"), _e(_d("omittedInputs", count=omitted))))
    if outputs:
        items = "".join(
            f"<li>{_asset_link(str(a['id']), labels)}</li>"
            for a in sorted(outputs, key=lambda a: a.get("output_index") or 0)
        )
        rows.append(_row(_d("fields.outputs"), f'<ul class="links">{items}</ul>'))
    if run.get("error_code") or run.get("error_message"):
        error = " — ".join(str(v) for v in (run.get("error_code"), run.get("error_message")) if v)
        rows.append(_row(_d("fields.error"), _e(error)))
    rows.append(_row(_d("fields.id"), _code(run_id)))

    imported_html = ""
    if imported is not None:
        irows = [
            _row(_d("fields.sourceCreatedAt"), _time(imported.source_created_at, tz)),
            _row(_d("fields.sourceFinishedAt"), _time(imported.source_finished_at, tz)),
        ]
        if include_creator_names and imported.source_creator_name:
            irows.append(_row(_d("fields.sourceCreator"), _e(imported.source_creator_name)))
        if imported.source_gakei_version:
            irows.append(_row(_d("fields.sourceVersion"), _e(imported.source_gakei_version)))
        imported_html = (
            f'<div class="notice notice-imported"><p>{_e(_d("importedNote"))}</p>'
            f'<dl class="fields">{"".join(irows)}</dl></div>'
        )

    prompt = run.get("prompt")
    prompt_html = (
        f'<pre class="prompt">{_e(prompt)}</pre>'
        if isinstance(prompt, str) and prompt != ""
        else f'<p class="muted">{_e(_d("none"))}</p>'
    )
    title = f"{labels[f'r:{run_id}']} · {_operation_label(run.get('operation'))}"
    return (
        f'<article class="card run-card{" is-imported" if imported else ""}" id="run-{_e(run_id)}">'
        f'<div class="card-body"><h3>{_e(title)} {"".join(badges)}</h3>'
        f"{imported_html}"
        f'<dl class="fields">{"".join(rows)}</dl>'
        f"<h4>{_e(_d('fields.prompt'))}</h4>{prompt_html}"
        f"<h4>{_e(_d('fields.params'))}</h4>{_render_params(run.get('params'))}"
        "</div></article>"
    )


def _role_label(run_input: dict[str, Any]) -> str:
    role = str(run_input.get("role") or "image")
    edge = GraphEdge("", "", role, role == "image" and run_input.get("position") == 0)
    return _edge_label(edge)


_CSS = """
:root{--bg:#f7f7f5;--card:#fff;--text:#1d1e21;--muted:#62656c;--border:#d9dade;
--accent:#b8860b;--accent-soft:#fdf3d8;--edge:#8a8d94;--danger:#b42318;--ok:#1f7a3a;
--imported:#6941c6;--imported-soft:#f1ecfb;--code:#f0f0ee}
@media (prefers-color-scheme:dark){:root{--bg:#17181b;--card:#202226;--text:#e8e9ec;
--muted:#a2a5ad;--border:#3a3d44;--accent:#e2b13c;--accent-soft:#3a3118;--edge:#7d818a;
--danger:#f97066;--ok:#5fc27e;--imported:#b69cf5;--imported-soft:#2c2540;--code:#2a2c31}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.6 system-ui,-apple-system,
"Segoe UI","Hiragino Sans","Noto Sans JP","Yu Gothic UI",Meiryo,sans-serif}
main{max-width:1080px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:24px;margin:0 0 4px}
h2{font-size:19px;margin:36px 0 12px;padding-bottom:6px;border-bottom:1px solid var(--border)}
h3{font-size:16px;margin:0 0 8px;display:flex;flex-wrap:wrap;gap:6px;align-items:center}
h4{font-size:13px;margin:14px 0 6px;color:var(--muted)}
a{color:inherit}
.muted{color:var(--muted)}
.lead{margin:0 0 20px;color:var(--muted)}
.lead:has(+.purpose){margin-bottom:6px}
.purpose{color:var(--text);font-weight:600}
code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:13px;
background:var(--code);padding:1px 4px;border-radius:4px;overflow-wrap:anywhere}
.summary{display:grid;grid-template-columns:minmax(0,320px) minmax(0,1fr);gap:20px;
background:var(--card);border:1px solid var(--border);border-radius:10px;padding:16px}
.summary img{width:100%;height:auto;display:block;border-radius:6px;background:var(--code)}
.fields{margin:0;display:grid;gap:4px}
.row{display:grid;grid-template-columns:minmax(96px,30%) minmax(0,1fr);gap:8px}
dt{color:var(--muted);font-size:13px}
dd{margin:0;overflow-wrap:anywhere}
.notice{border:1px solid var(--border);border-left:4px solid var(--accent);
background:var(--accent-soft);border-radius:8px;padding:12px 14px;margin:16px 0}
.notice p{margin:0 0 6px}.notice p:last-child{margin-bottom:0}
.notice-imported{border-left-color:var(--imported);background:var(--imported-soft);margin:0 0 12px}
.graph-wrap{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:8px;
overflow-x:auto}
svg.graph{display:block;margin:0 auto;max-width:100%;height:auto;min-width:min(100%,480px)}
.graph .edge{fill:none;stroke:var(--edge);stroke-width:1.5}
.graph .edge-primary{stroke-width:2.5}
.graph .edge-source{stroke-dasharray:4 3}
.graph .arrow-head{fill:var(--edge)}
.graph .edge-label{font-size:11px;fill:var(--muted);text-anchor:middle;paint-order:stroke;
stroke:var(--card);stroke-width:4px;stroke-linejoin:round}
.graph .node rect{fill:var(--card);stroke:var(--border);stroke-width:1.5}
.graph .node.root rect{stroke:var(--accent);stroke-width:4}
.graph .node.run rect{fill:var(--code)}
.graph .node.run.imported rect{stroke:var(--imported);stroke-dasharray:5 3}
.graph .node.run.not-succeeded rect{stroke:var(--danger)}
.graph .node-label{font-size:12px;font-weight:600;fill:var(--text);text-anchor:middle}
.graph .node-sub{font-size:11px;fill:var(--muted);text-anchor:middle}
.graph .node.root rect.root-pill{fill:var(--accent);stroke:none}
.graph .root-label{font-size:11px;font-weight:700;fill:#fff;text-anchor:middle}
.legend{font-size:13px;color:var(--muted);margin:8px 0 0}
.cards{display:grid;gap:12px}
.card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px;
display:grid;grid-template-columns:200px minmax(0,1fr);gap:16px;break-inside:avoid}
.run-card{grid-template-columns:minmax(0,1fr)}
.card.is-root{border-color:var(--accent);border-width:2px}
.card.is-imported{border-style:dashed;border-color:var(--imported)}
.thumb img{width:100%;height:auto;max-height:240px;object-fit:contain;display:block;
border-radius:6px;background:var(--code)}
.badge{font-size:12px;font-weight:600;padding:1px 8px;border-radius:999px;
border:1px solid var(--border)}
.badge-root{background:var(--accent-soft);border-color:var(--accent)}
.badge-imported{background:var(--imported-soft);border-color:var(--imported);color:var(--imported)}
.status-succeeded{color:var(--ok)}.status-failed,.status-canceled{color:var(--danger)}
pre{margin:0;white-space:pre-wrap;overflow-wrap:anywhere;background:var(--code);border-radius:6px;
padding:10px;font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
pre.prompt{font-family:inherit;font-size:14px}
.params{margin:0;display:grid;gap:2px}
.param-value{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:13px}
details{margin-top:6px}summary{cursor:pointer;font-family:ui-monospace,monospace;font-size:13px}
ul.links{margin:0;padding-left:18px}
footer{margin-top:40px;font-size:13px;color:var(--muted)}
@media (max-width:767px){
body{font-size:14px}
.summary,.card{grid-template-columns:minmax(0,1fr)}
.row{grid-template-columns:minmax(0,1fr);gap:0}
.thumb img{max-height:320px}
}
@media print{
:root{--bg:#fff;--card:#fff;--text:#000;--muted:#444;--border:#bbb;--code:#f4f4f4}
body{font-size:11pt}
main{max-width:none;padding:0}
.graph-wrap{overflow:visible}
.card,.summary,.graph-wrap,.notice{break-inside:avoid}
a{text-decoration:none}
}
"""


def render_index_html(
    plan: ExportPlan,
    *,
    tz: tzinfo = UTC,
    locale: Locale,
) -> str:
    """`index.html` の中身。文言は現在のリクエストの言語(呼び出し側が `use_locale` で決める)。"""
    manifest = plan.manifest
    assets = {str(a["id"]): a for a in manifest.get("assets", [])}
    runs = {str(r["id"]): r for r in manifest.get("runs", [])}
    root_id = str(manifest.get("root_asset_id"))
    layout = layout_graph(manifest)

    # 図とカードで共通の呼び名(「画像 1」「実行 1」)。図の並び(上から、左から)の順に振る。
    labels: dict[str, str] = {}
    asset_no = run_no = 0
    for node in layout.nodes:
        if node.kind == "asset":
            asset_no += 1
            labels[node.key] = _d("assetLabel", n=asset_no)
        else:
            run_no += 1
            labels[node.key] = _d("runLabel", n=run_no)

    outputs_by_run: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for a in assets.values():
        if a.get("produced_by_run_id"):
            outputs_by_run[str(a["produced_by_run_id"])].append(a)

    root = assets.get(root_id)
    scope = str(manifest.get("scope"))
    known_scope = scope in ("ancestors", "descendants", "lineage")
    scope_label = _d(f"scopes.{scope}") if known_scope else scope
    # 範囲ごとの目的(ADR-0037 4章): 祖先は「主画像がどう作られたか」、子孫は「主画像(素材)が
    # 何に使われたか」、系列全体はその両方を示す。
    purpose = f'<p class="lead purpose">{_e(_d(f"purpose.{scope}"))}</p>' if known_scope else ""
    main_image_label = _d(f"mainImageRole.{scope}") if known_scope else _d("mainImage")

    summary_rows = []
    if root is not None:
        summary_rows.append(_row(main_image_label, _asset_link(root_id, labels)))
        summary_rows.append(_row(_d("fields.fileName"), _code(root.get("file"))))
        summary_rows.append(
            _row(_d("fields.sha256"), f'<code class="hash">{_e(root.get("sha256"))}</code>')
        )
    summary_rows += [
        _row(_d("exportedAt"), _time(manifest.get("exported_at"), tz)),
        _row(_d("gakeiVersion"), _e(manifest.get("gakei_version"))),
        _row(_d("scope"), _e(scope_label)),
        _row(
            _d("contents"),
            _e(_d("counts", assets=len(assets), runs=len(runs))),
        ),
    ]
    root_img = ""
    if root is not None:
        root_img = (
            f'<a href="{_e(root.get("file"))}"><img src="{_e(root.get("file"))}" '
            f'alt="{_e(_d("mainImage"))}"></a>'
        )

    truncated = ""
    if manifest.get("truncated"):
        truncated = f'<div class="notice"><p>{_e(_d("truncated"))}</p></div>'
    imported_notice = (
        f'<div class="notice notice-imported"><p>{_e(_d("importedSummary"))}</p></div>'
        if plan.imported_runs
        else ""
    )

    asset_cards = "".join(
        _render_asset_card(assets[n.ident], labels, root_id, tz)
        for n in layout.nodes
        if n.kind == "asset"
    )
    run_cards = "".join(
        _render_run_card(
            runs[n.ident],
            labels,
            outputs_by_run.get(n.ident, []),
            plan.imported_runs.get(n.ident),
            plan.include_creator_names,
            tz,
        )
        for n in layout.nodes
        if n.kind == "run"
    )
    runs_section = (
        f'<h2>{_e(_d("runsTitle"))}</h2><div class="cards">{run_cards}</div>'
        if run_cards
        else f'<h2>{_e(_d("runsTitle"))}</h2><p class="muted">{_e(_d("noRuns"))}</p>'
    )
    graph_svg = _render_graph_svg(layout, assets, runs, labels, root_id, plan.imported_runs)

    return (
        "<!doctype html>\n"
        f'<html lang="{_e(locale)}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="robots" content="noindex">'
        f"<title>{_e(_d('title'))}</title>"
        f"<style>{_CSS}</style></head><body><main>"
        f"<header><h1>{_e(_d('title'))}</h1>"
        f'<p class="lead">{_e(_d("lead"))}</p>{purpose}</header>'
        f'<section class="summary" aria-label="{_e(_d("summaryTitle"))}">'
        f"<div>{root_img}</div>"
        f'<dl class="fields">{"".join(summary_rows)}</dl></section>'
        f"{truncated}"
        f'<div class="notice"><p><strong>{_e(_d("integrityTitle"))}</strong></p>'
        f"<p>{_e(_d('integrityBody'))}</p></div>"
        f"{imported_notice}"
        f"<h2>{_e(_d('graphTitle'))}</h2>"
        f'<div class="graph-wrap">{graph_svg}</div>'
        f'<p class="legend">{_e(_d("graphLegend"))}</p>'
        f'<h2>{_e(_d("assetsTitle"))}</h2><div class="cards">{asset_cards}</div>'
        f"{runs_section}"
        f"<footer><p>{_e(_d('footerFiles'))}</p>"
        f"<p>{_e(_d('footerMadeWith'))} "
        f'<a href="{_e(REPOSITORY_URL)}">{_e(REPOSITORY_URL)}</a></p></footer>'
        "</main></body></html>\n"
    )


def render_readme(plan: ExportPlan) -> str:
    """`README.txt`。言語にかかわらず日本語と英語を併記する(改行は CRLF)。"""
    sections: list[str] = []
    for loc in ("ja", "en"):
        with use_locale(loc):
            sections.append(
                t(
                    "lineageExport.readme.body",
                    repo_url=REPOSITORY_URL,
                    format=plan.manifest.get("format", ""),
                    root=plan.manifest.get("root_asset_id", ""),
                    exported_at=plan.manifest.get("exported_at", ""),
                    version=plan.manifest.get("gakei_version", ""),
                )
            )
    text = "\n\n----------------------------------------------------------------\n\n".join(sections)
    return text.strip().replace("\r\n", "\n").replace("\n", "\r\n") + "\r\n"


def build_delivery_entries(
    plan: ExportPlan, *, locale: Locale, tz_name: str | None = None
) -> list[tuple[str, bytes]]:
    """納品用に加える文書(`index.html` と `README.txt`)。README は BOM 付きの UTF-8
    (古いメモ帳などでも日本語が化けないように)。"""
    tz = resolve_timezone(tz_name)
    with use_locale(locale):
        index_html = render_index_html(plan, tz=tz, locale=locale)
    readme = render_readme(plan)
    return [
        (INDEX_HTML_NAME, index_html.encode("utf-8")),
        (README_NAME, b"\xef\xbb\xbf" + readme.encode("utf-8")),
    ]
