"""画像から生成の設定を読み込む(ADR-0038 11章)。

画像に埋め込まれた情報から、生成のフォームに入れる値(プロバイダー、モデル、プロンプト、
パラメーター)を作る。判定の順は次のとおり。

1. GAKEI の `gakei` チャンク(ADR-0014): 起点の画像を生んだ Run のレシピをそのまま使う。
2. ComfyUI の `prompt` チャンク: 登録済みワークフローのテンプレートと照合し、束縛と公開
   パラメーターの入力の値を読む。
3. A1111 形式は SD WebUI の一覧が要るので、呼び出し側(API)が9章の対応付けに回す。

DB の行は作らない。画像の内容は自己申告(未検証)なので、型の合わない値は黙って捨てる。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from app.domain.comfy_workflow import Bindings, ExposedParam, InputRef
from app.domain.embedded_meta import normalize_lineage_meta, read_gakei_meta
from app.domain.models import ComfyWorkflow
from app.i18n import t

ImportSource = Literal["gakei", "comfyui", "a1111"]
ParamScalar = str | int | float | bool

# サーバーだけが書く `run.params` のキー(ComfyUI は ADR-0013、SD WebUI は ADR-0038 3章)。
# フォームには入れない。実際に使った seed は `seed` に戻す(「同じ設定で新規作成」と同じ)。
_SERVER_ONLY_PREFIXES = ("comfyui_", "sdwebui_")
_SERVER_SEED_KEYS = ("comfyui_seed", "sdwebui_seed")

# ノードの一致がこの割合に満たないワークフローは選ばない。
_MIN_NODE_MATCH_RATIO = 0.5
# 「読み込めなかった項目」に並べるテンプレートとの違いの上限。
_MAX_DIFF_ITEMS = 50


@dataclass
class ImportedSettings:
    source: ImportSource
    provider: str
    model: str | None
    prompt: str
    params: dict[str, ParamScalar]
    unapplied: list[tuple[str, str]] = field(default_factory=list)
    notes: list[tuple[str, str]] = field(default_factory=list)
    software: str | None = None


class ImageSettingsImportError(Exception):
    """読み込めないとき。`message` は利用者向けの文言。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _is_scalar(value: Any) -> bool:
    return isinstance(value, str | int | float | bool)


# -- GAKEI ------------------------------------------------------------------


@dataclass
class GakeiRecipe:
    provider: str
    model: str
    operation: str | None
    prompt: str
    params: dict[str, Any]


def read_gakei_recipe(data: bytes) -> GakeiRecipe | None:
    """`gakei` チャンクから、起点の画像を生んだ Run のレシピを読む。Run が無ければ None。"""
    graph = normalize_lineage_meta(read_gakei_meta(data))
    if graph is None:
        return None
    root = graph.get("root")
    run_id = next(
        (
            e.get("source")
            for e in graph.get("edges", [])
            if e.get("kind") == "output" and e.get("target") == root
        ),
        None,
    )
    run = next(
        (n for n in graph.get("nodes", []) if n.get("type") == "run" and n.get("id") == run_id),
        None,
    )
    if run is None or not run.get("provider") or not run.get("model"):
        return None
    return GakeiRecipe(
        provider=run["provider"],
        model=run["model"],
        operation=run.get("operation"),
        prompt=run.get("prompt") or "",
        params=run.get("params") or {},
    )


def form_params_from_run_params(params: dict[str, Any]) -> dict[str, ParamScalar]:
    """`run.params` からフォームに入れる値。サーバー専有のキーと、入れ子の値を除く。"""
    result: dict[str, ParamScalar] = {}
    for key, value in params.items():
        if key.startswith(_SERVER_ONLY_PREFIXES) or not _is_scalar(value):
            continue
        result[key] = value
    for key in _SERVER_SEED_KEYS:
        seed = params.get(key)
        if isinstance(seed, int) and not isinstance(seed, bool):
            result["seed"] = seed
            break
    return result


def settings_from_gakei(recipe: GakeiRecipe) -> ImportedSettings:
    notes: list[tuple[str, str]] = []
    if recipe.operation == "edit":
        notes.append(("inputsNotRestored", t("imageSettings.notes.inputsNotRestored")))
    return ImportedSettings(
        source="gakei",
        provider=recipe.provider,
        model=recipe.model,
        prompt=recipe.prompt,
        params=form_params_from_run_params(recipe.params),
        notes=notes,
        software="GAKEI",
    )


def comfy_workflow_name(recipe: GakeiRecipe) -> str:
    """GAKEI の ComfyUI の Run が使ったワークフローの名前(無ければモデル = ID)。"""
    info = recipe.params.get("comfyui_workflow")
    if isinstance(info, dict) and isinstance(info.get("name"), str):
        return info["name"]
    return recipe.model


# -- ComfyUI ----------------------------------------------------------------


def _node(graph: dict[str, Any], node_id: str) -> dict[str, Any] | None:
    node = graph.get(node_id)
    return node if isinstance(node, dict) else None


def _inputs(node: dict[str, Any] | None) -> dict[str, Any]:
    inputs = node.get("inputs") if node else None
    return inputs if isinstance(inputs, dict) else {}


def _bound_refs(bindings: Bindings, exposed: list[ExposedParam]) -> list[InputRef]:
    refs: list[InputRef] = [bindings.prompt, *bindings.seed, *bindings.images]
    for ref in (
        bindings.negative_prompt,
        bindings.width,
        bindings.height,
        bindings.batch_size,
        bindings.bounding_boxes,
    ):
        if ref is not None:
            refs.append(ref)
    if bindings.mask is not None and bindings.mask.node and bindings.mask.input:
        refs.append(InputRef(node=bindings.mask.node, input=bindings.mask.input))
    refs.extend(InputRef(node=p.node, input=p.input) for p in exposed)
    return refs


def _same_class(template: dict[str, Any], graph: dict[str, Any], node_id: str) -> bool:
    a = _node(template, node_id)
    b = _node(graph, node_id)
    return a is not None and b is not None and a.get("class_type") == b.get("class_type")


def _match_score(
    template: dict[str, Any], graph: dict[str, Any], refs: list[InputRef]
) -> float | None:
    """ノードの一致の割合。束縛のノードが1つでも合わなければ None。"""
    if not all(_same_class(template, graph, ref.node) for ref in refs):
        return None
    matched = sum(1 for node_id in template if _same_class(template, graph, node_id))
    total = max(len(template), len(graph))
    return matched / total if total else None


@dataclass
class _Candidate:
    workflow: ComfyWorkflow
    bindings: Bindings
    exposed: list[ExposedParam]
    score: float


def match_comfy_workflow(
    graph: dict[str, Any], workflows: Iterable[ComfyWorkflow]
) -> _Candidate | None:
    """画像のグラフに最も合う登録済みワークフロー。合うものが無ければ None。"""
    best: _Candidate | None = None
    for wf in workflows:
        if wf.deleted_at is not None or not isinstance(wf.template, dict):
            continue
        try:
            bindings = Bindings.model_validate(wf.bindings)
            exposed = [ExposedParam.model_validate(p) for p in wf.exposed_params]
        except ValueError:
            continue
        score = _match_score(wf.template, graph, _bound_refs(bindings, exposed))
        if score is None or score < _MIN_NODE_MATCH_RATIO:
            continue
        if best is None or score > best.score:
            best = _Candidate(wf, bindings, exposed, score)
    return best


def _literal(graph: dict[str, Any], ref: InputRef) -> Any:
    """入力の値。他のノードからの配線(`[node_id, index]`)なら None。"""
    value = _inputs(_node(graph, ref.node)).get(ref.input)
    return value if _is_scalar(value) else None


def _int_value(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _diff_label(template: dict[str, Any], node_id: str, input_name: str) -> str:
    node = _node(template, node_id) or {}
    meta = node.get("_meta")
    title = meta.get("title") if isinstance(meta, dict) else None
    name = title if isinstance(title, str) and title else node.get("class_type") or node_id
    return f"{name} › {input_name}"


def settings_from_comfy_graph(graph: dict[str, Any], candidate: _Candidate) -> ImportedSettings:
    """照合したワークフローの束縛と公開パラメーターの値をフォームの値にする。"""
    bindings = candidate.bindings
    template: dict[str, Any] = candidate.workflow.template
    params: dict[str, ParamScalar] = {}
    notes: list[tuple[str, str]] = []

    prompt_value = _literal(graph, bindings.prompt)
    prompt = prompt_value if isinstance(prompt_value, str) else ""
    if not isinstance(prompt_value, str):
        notes.append(("promptLinked", t("imageSettings.notes.promptLinked")))

    if bindings.negative_prompt is not None:
        negative = _literal(graph, bindings.negative_prompt)
        if isinstance(negative, str):
            params["negative_prompt"] = negative
    for ref in bindings.seed:
        seed = _int_value(_literal(graph, ref))
        if seed is not None:
            params["seed"] = seed
            break
    for name, ref in (
        ("width", bindings.width),
        ("height", bindings.height),
        ("batch_size", bindings.batch_size),
    ):
        if ref is None:
            continue
        number = _int_value(_literal(graph, ref))
        if number is not None:
            params[name] = number
    for exposed_param in candidate.exposed:
        value = _literal(graph, InputRef(node=exposed_param.node, input=exposed_param.input))
        if value is not None:
            params[exposed_param.name] = value

    # 束縛していない入力で、テンプレートと値が違うもの(チェックポイント名など)。
    bound = {(ref.node, ref.input) for ref in _bound_refs(bindings, candidate.exposed)}
    unapplied: list[tuple[str, str]] = []
    for node_id, tpl_node in template.items():
        graph_inputs = _inputs(_node(graph, node_id))
        for input_name, tpl_value in _inputs(
            tpl_node if isinstance(tpl_node, dict) else None
        ).items():
            if (node_id, input_name) in bound or not _is_scalar(tpl_value):
                continue
            value = graph_inputs.get(input_name)
            if _is_scalar(value) and value != tpl_value:
                unapplied.append((_diff_label(template, node_id, input_name), str(value)))
    if len(unapplied) > _MAX_DIFF_ITEMS:
        unapplied = unapplied[:_MAX_DIFF_ITEMS]
    if candidate.score < 1.0:
        notes.append(
            (
                "workflowPartialMatch",
                t("imageSettings.notes.workflowPartialMatch", name=candidate.workflow.name),
            )
        )

    return ImportedSettings(
        source="comfyui",
        provider="comfyui",
        model=str(candidate.workflow.id),
        prompt=prompt,
        params=params,
        unapplied=unapplied,
        notes=notes,
        software="ComfyUI",
    )


def is_registered_workflow(model: str, workflows: Iterable[ComfyWorkflow]) -> bool:
    try:
        workflow_id = uuid.UUID(model)
    except ValueError:
        return False
    return any(wf.id == workflow_id and wf.deleted_at is None for wf in workflows)
