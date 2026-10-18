"""ADR-0052 2章(読み取りの規則)と3章(保存時の検証)のテスト。

グラフは標準の class_type と架空の値だけで作る(`tests/comfyui_synth_graphs.py` の S1〜S12 と、
このファイルの Qwen Edit 風・切り替えのある t2i)。DB や HTTP には触れない。
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from app.domain.comfy_analyze import AnalyzeResult, BindingReading, analyze_workflow
from app.domain.comfy_graph import WorkflowGraph
from app.domain.comfy_workflow import (
    Bindings,
    ExposedParam,
    InputRef,
    MaskBinding,
    WorkflowValidationError,
    check_workflow,
    validate_workflow,
)
from tests.comfyui_graphs import (
    IMG2IMG_GRAPH,
    OUTPUT_FILTER_GRAPH,
    PROMPT_ENHANCER_GRAPH,
    QWEN_LIKE_OBJECT_INFO,
    T2I_GRAPH,
    clone,
)
from tests.comfyui_synth_graphs import SYNTH, SYNTH_EXPOSED, SYNTH_GT


def W(nid: str, port: int = 0) -> list[Any]:
    return [nid, port]


def N(class_type: str, title: str | None = None, **inputs: Any) -> dict[str, Any]:
    return {"class_type": class_type, "inputs": inputs, "_meta": {"title": title or class_type}}


def _reading(result: AnalyzeResult, field: str) -> BindingReading:
    return next(r for r in result.readings if r.field == field)


def _ref(ref: InputRef | None) -> tuple[str, str] | None:
    return (ref.node, ref.input) if ref is not None else None


def _summary(result: AnalyzeResult) -> dict[str, Any]:
    b = result.suggested_bindings
    mask = b.mask
    return {
        "operation": result.suggested_operation,
        "prompt": _ref(b.prompt),
        "negative_prompt": _ref(b.negative_prompt),
        "seed": frozenset(_ref(r) for r in b.seed),
        "width": _ref(b.width),
        "height": _ref(b.height),
        "batch_size": _ref(b.batch_size),
        "images": tuple(_ref(r) for r in b.images),
        "mask": (mask.mode, mask.node, mask.input) if mask is not None else None,
        "outputs": frozenset(b.outputs),
        "final_prompt": b.final_prompt,
    }


# -- S1〜S12 ----------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(SYNTH))
def test_synth_graphs_match_ground_truth(name: str) -> None:
    result = analyze_workflow(copy.deepcopy(SYNTH[name]))
    got = _summary(result)
    for key, allowed in SYNTH_GT[name].items():
        assert got[key] in allowed, (name, key, got[key], allowed)
    # 公開パラメーターの候補に、最低限あってほしいものが含まれる。
    exposed = {(p.node, p.input) for p in result.candidate_params}
    assert SYNTH_EXPOSED[name] <= exposed
    # 提案に保存時と同じ検証を掛けても、エラーは出ない。
    assert [v for v in result.validation if v.level == "error"] == []


def test_synth_every_field_has_a_reading_with_reason() -> None:
    result = analyze_workflow(copy.deepcopy(SYNTH["S1_sd15_t2i"]))
    fields = [r.field for r in result.readings]
    assert fields == [
        "prompt",
        "negative_prompt",
        "seed",
        "width",
        "height",
        "batch_size",
        "images",
        "mask",
        "outputs",
        "final_prompt",
    ]
    assert all(r.reason for r in result.readings)


def test_prompt_unique_root_path_reaches_output() -> None:
    result = analyze_workflow(copy.deepcopy(SYNTH["S1_sd15_t2i"]))
    reading = _reading(result, "prompt")
    assert reading.confidence == "unique"
    path = reading.paths[0]
    assert (path[0].node, path[0].input) == ("6", "text")
    assert path[-1].node == "9"  # 保存ノードまで
    assert [step.node for step in path] == ["6", "3", "8", "9"]
    assert path[1].input == "positive"


def test_shared_primitive_seed_is_merged_into_one_reference() -> None:
    """1つの Primitive を2つのサンプラーが共有しても、seed の参照は1つ(重複で保存が落ちない)。"""
    result = analyze_workflow(copy.deepcopy(SYNTH["S5_sdxl_refiner"]))
    assert result.suggested_bindings.seed == [InputRef(node="10", input="value")]
    assert result.validation == []


def test_numbered_image_inputs_order_slots_across_passthrough_nodes() -> None:
    """`image1`・`image2` の形の入力の番号で枠を並べる(画像を素通しするノードを越えて)。"""
    result = analyze_workflow(copy.deepcopy(SYNTH["S6_qwen_edit_plus_3img"]))
    assert [r.node for r in result.suggested_bindings.images] == ["11", "10", "12"]
    assert _reading(result, "images").confidence == "unique"


def test_concat_prefix_does_not_outrank_titled_prompt() -> None:
    result = analyze_workflow(copy.deepcopy(SYNTH["S12_concat_zero_negative"]))
    reading = _reading(result, "prompt")
    assert reading.confidence == "ranked"
    assert [(c.node, c.input) for c in reading.candidates] == [("4", "value"), ("3", "value")]
    assert result.suggested_bindings.negative_prompt is None
    assert "ConditioningZeroOut" in _reading(result, "negative_prompt").reason


def test_real_inpaint_mask_is_image_alpha() -> None:
    for name in ("S2_inpaint_alpha", "S11_inpaint_inverted"):
        result = analyze_workflow(copy.deepcopy(SYNTH[name]))
        assert result.suggested_bindings.mask == MaskBinding(mode="image_alpha")
        assert _reading(result, "mask").confidence == "unique"


# -- Qwen Edit 風: 書式・透過のための MASK・サブグラフの id ------------------------------


def _qwen_edit_alpha_graph() -> dict[str, Any]:
    return {
        "10": N("LoadImage", "Input image", image="input.png"),
        "11": N("LoadImage", image="reference.png"),
        "459:470": N("UNETLoader", unet_name="model.safetensors", weight_dtype="default"),
        "459:471": N("CLIPLoader", clip_name="clip.safetensors", type="qwen_image"),
        "459:472": N("VAELoader", vae_name="vae.safetensors"),
        "459:480": N("PrimitiveStringMultiline", "編集指示", value="make the sky purple"),
        "459:481": N(
            "PrimitiveStringMultiline",
            "Output rules (do not edit)",
            value="Keep the original composition.",
        ),
        "459:482": N(
            "StringFormat",
            f_string="{a}\n{b}",
            **{"values.a": W("459:480"), "values.b": W("459:481")},
        ),
        "459:474": N(
            "TextEncodeQwenImageEditPlus",
            clip=W("459:471"),
            prompt=W("459:482"),
            vae=W("459:472"),
            image1=W("10"),
            image2=W("11"),
        ),
        "459:475": N(
            "TextEncodeQwenImageEditPlus",
            clip=W("459:471"),
            prompt="",
            vae=W("459:472"),
            image1=W("10"),
            image2=W("11"),
        ),
        "459:476": N("VAEEncode", pixels=W("10"), vae=W("459:472")),
        "459:477": N(
            "KSampler",
            seed=7,
            steps=8,
            cfg=1.0,
            sampler_name="euler",
            scheduler="simple",
            denoise=1.0,
            model=W("459:470"),
            positive=W("459:474"),
            negative=W("459:475"),
            latent_image=W("459:476"),
        ),
        "459:478": N("VAEDecode", samples=W("459:477"), vae=W("459:472")),
        "459:479": N("JoinImageWithAlpha", image=W("459:478"), alpha=W("10", 1)),
        "460": N("SaveImage", images=W("459:479"), filename_prefix="edit"),
        "461": N("PreviewAny", source=W("459:482")),
    }


def test_qwen_edit_like_reading() -> None:
    result = analyze_workflow(_qwen_edit_alpha_graph())
    got = _summary(result)
    assert got["operation"] == "edit"
    assert got["prompt"] == ("459:480", "value")
    assert _reading(result, "prompt").confidence == "ranked"
    # 空文字の既定値もネガティブの差し込み先にする。
    assert got["negative_prompt"] == ("459:475", "prompt")
    assert got["seed"] == {("459:477", "seed")}
    assert got["images"] == (("10", "image"), ("11", "image"))
    # 透過を保つためだけの MASK(JoinImageWithAlpha.alpha)はマスクにしない。
    assert got["mask"] is None
    mask_reading = _reading(result, "mask")
    assert mask_reading.confidence == "absent"
    assert "JoinImageWithAlpha" in mask_reading.reason
    assert got["outputs"] == {"460"}
    # 同じ配線を横から表示する PreviewAny が最終プロンプト。
    assert got["final_prompt"] == "461"
    assert _reading(result, "final_prompt").confidence == "unique"
    # 大きさは入力画像に従う。
    assert got["width"] is None
    assert "VAEEncode" in _reading(result, "width").reason
    assert result.validation == []
    assert result.warnings == []


def test_qwen_edit_like_neutral_titles_ask() -> None:
    graph = _qwen_edit_alpha_graph()
    graph["459:480"]["_meta"] = {"title": "PrimitiveStringMultiline"}
    graph["459:481"]["_meta"] = {"title": "PrimitiveStringMultiline"}
    result = analyze_workflow(graph)
    reading = _reading(result, "prompt")
    assert reading.confidence == "ask"
    assert result.suggested_bindings.prompt is None
    assert reading.question
    # 書式の先頭({a})の方が上位。
    assert [(c.node, c.input) for c in reading.candidates] == [
        ("459:480", "value"),
        ("459:481", "value"),
    ]
    assert any("候補" in w for w in result.warnings)
    # 問いと同じことを示す警告は、読み取りの種別(カードの画面では出さない)。
    prompt_items = [w for w in result.warning_items if w.field == "prompt"]
    assert prompt_items and all(w.kind == "reading" for w in prompt_items)
    assert [w.message for w in result.warning_items] == result.warnings


def test_image_alpha_on_transparency_mask_is_warned() -> None:
    graph = _qwen_edit_alpha_graph()
    bindings = Bindings(
        prompt=InputRef(node="459:480", input="value"),
        images=[InputRef(node="10", input="image"), InputRef(node="11", input="image")],
        mask=MaskBinding(mode="image_alpha"),
        outputs=["460"],
    )
    warnings = validate_workflow(graph, "edit", bindings, [])
    assert [w.code for w in warnings] == ["mask_not_inpaint"]


# -- 切り替えのある t2i(プロンプトの強化と透過の切り替え) -----------------------------


def _switch_t2i_graph(*, enhance: bool = True, transparent: bool = False) -> dict[str, Any]:
    return {
        "1": N("PrimitiveStringMultiline", "prompt", value=""),
        "2": N("PrimitiveInt", "seed", value=1),
        "3": N("PrimitiveBoolean", "prompt_enhance", value=enhance),
        "4": N("PrimitiveBoolean", "transparent", value=transparent),
        "10": N("UNETLoader", unet_name="model.safetensors", weight_dtype="default"),
        "11": N("CLIPLoader", clip_name="clip.safetensors", type="qwen_image"),
        "12": N("VAELoader", vae_name="vae.safetensors"),
        "13": N("CLIPLoader", clip_name="pe.safetensors", type="qwen_image"),
        "20": N("TextGenerate", clip=W("13"), prompt=W("1"), **{"sampling_mode.seed": W("2")}),
        "21": N("ComfySwitchNode", switch=W("3"), on_true=W("20"), on_false=W("1")),
        "22": N("PreviewAny", source=W("21")),
        "23": N("TextEncodeQwenImage21", clip=W("11"), prompt=W("21"), negative_prompt=""),
        "30": N("EmptyLatentImage", width=1024, height=1024, batch_size=1),
        "31": N("EmptyLatentImage", width=768, height=768, batch_size=2),
        "32": N("ComfySwitchNode", switch=W("4"), on_true=W("31"), on_false=W("30")),
        "40": N(
            "KSampler",
            seed=W("2"),
            steps=20,
            cfg=4.0,
            sampler_name="euler",
            scheduler="simple",
            denoise=1.0,
            model=W("10"),
            positive=W("23"),
            negative=W("23", 1),
            latent_image=W("32"),
        ),
        "41": N("VAEDecode", samples=W("40"), vae=W("12")),
        "42": N("SaveImage", images=W("41"), filename_prefix="t2i"),
    }


def test_switch_t2i_reading() -> None:
    result = analyze_workflow(_switch_t2i_graph())
    got = _summary(result)
    assert got["prompt"] == ("1", "value")
    assert _reading(result, "prompt").confidence == "unique"
    assert got["negative_prompt"] == ("23", "negative_prompt")
    assert got["seed"] == {("2", "value")}
    # 今の切り替え(transparent=False)で使われる EmptyLatentImage だけ。
    assert got["width"] == ("30", "width")
    assert got["batch_size"] == ("30", "batch_size")
    assert got["final_prompt"] == "22"
    states = {n["id"]: n["state"] for n in result.nodes}
    assert states["31"] == "unused"
    assert states["30"] == "live"
    assert states["22"] == "display"
    assert states["20"] == "live"
    tiers = {(p.node, p.input): p.tier for p in result.param_tiers}
    # 真偽値の切り替えは推奨、使われない枝は公開しない、ファイル名も公開しない。
    assert tiers[("3", "value")] == "recommended"
    assert tiers[("4", "value")] == "recommended"
    assert tiers[("10", "unet_name")] == "hidden"
    assert tiers[("40", "steps")] == "recommended"
    assert result.validation == []


def test_switch_inversion_changes_live_branch() -> None:
    result = analyze_workflow(_switch_t2i_graph(transparent=True))
    got = _summary(result)
    assert got["width"] == ("31", "width")
    assert got["batch_size"] == ("31", "batch_size")
    states = {n["id"]: n["state"] for n in result.nodes}
    assert states["30"] == "unused"

    no_enhance = analyze_workflow(_switch_t2i_graph(enhance=False))
    states = {n["id"]: n["state"] for n in no_enhance.nodes}
    assert states["20"] == "unused"
    assert states["13"] == "unused"
    assert _summary(no_enhance)["prompt"] == ("1", "value")
    tiers = {(p.node, p.input): p.tier for p in no_enhance.param_tiers}
    assert tiers[("13", "type")] == "hidden"  # 使われない枝のノードは公開しない


def test_switch_controlled_by_another_node_keeps_both_branches_live() -> None:
    graph = _switch_t2i_graph()
    graph["5"] = N("SomeLogicNode", a=1)
    graph["32"]["inputs"]["switch"] = W("5")
    result = analyze_workflow(graph)
    states = {n["id"]: n["state"] for n in result.nodes}
    assert states["30"] == "live" and states["31"] == "live"
    # 大きさの元が2つあって決めきれない。
    assert _reading(result, "width").confidence == "ask"
    assert result.suggested_bindings.width is None


def test_unused_branch_binding_is_a_warning() -> None:
    graph = _switch_t2i_graph()
    bindings = Bindings(
        prompt=InputRef(node="1", input="value"),
        batch_size=InputRef(node="31", input="batch_size"),
        outputs=["42"],
    )
    warnings = validate_workflow(graph, "generate", bindings, [])
    assert [(w.code, w.field, w.node) for w in warnings] == [("unused_branch", "batch_size", "31")]


# -- id の付け替えで結果が変わらない ------------------------------------------------------


def _relabel(graph: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    ids = list(graph)
    mapping = {old: f"n{len(ids) - i}" for i, old in enumerate(ids)}
    relabeled: dict[str, Any] = {}
    for old in reversed(ids):
        node = copy.deepcopy(graph[old])
        node["inputs"] = {
            name: ([mapping[v[0]], v[1]] if isinstance(v, list) and len(v) == 2 else v)
            for name, v in node["inputs"].items()
        }
        relabeled[mapping[old]] = node
    return relabeled, mapping


@pytest.mark.parametrize(
    "graph_factory",
    [
        _qwen_edit_alpha_graph,
        _switch_t2i_graph,
        lambda: copy.deepcopy(SYNTH["S6_qwen_edit_plus_3img"]),
        lambda: copy.deepcopy(SYNTH["S5_sdxl_refiner"]),
        lambda: clone(PROMPT_ENHANCER_GRAPH),
    ],
)
def test_relabeling_ids_does_not_change_reading(graph_factory) -> None:
    graph = graph_factory()
    base = _summary(analyze_workflow(copy.deepcopy(graph)))
    relabeled, mapping = _relabel(graph)
    got = _summary(analyze_workflow(relabeled))

    def m(ref):
        return (mapping[ref[0]], ref[1]) if ref is not None else None

    expected = {
        "operation": base["operation"],
        "prompt": m(base["prompt"]),
        "negative_prompt": m(base["negative_prompt"]),
        "seed": frozenset(m(r) for r in base["seed"]),
        "width": m(base["width"]),
        "height": m(base["height"]),
        "batch_size": m(base["batch_size"]),
        "images": tuple(m(r) for r in base["images"]),
        "mask": (
            (base["mask"][0], mapping.get(base["mask"][1]), base["mask"][2])
            if base["mask"] is not None
            else None
        ),
        "outputs": frozenset(mapping[o] for o in base["outputs"]),
        "final_prompt": mapping[base["final_prompt"]] if base["final_prompt"] else None,
    }
    assert got == expected


# -- 既存の外れ方 ----------------------------------------------------------------------


def test_prompt_enhancer_ranks_user_instruction() -> None:
    result = analyze_workflow(clone(PROMPT_ENHANCER_GRAPH))
    assert result.suggested_bindings.prompt == InputRef(node="479", input="value")
    reading = _reading(result, "prompt")
    assert reading.confidence == "ranked"
    assert {c.node for c in reading.candidates} == {"478", "479", "480"}
    # 設定値(正規表現・区切り・モデル名)は根にしない。
    assert all(c.input == "value" for c in reading.candidates)


def test_unwired_load_image_is_not_a_slot() -> None:
    graph = clone(IMG2IMG_GRAPH)
    graph["15"] = {"class_type": "LoadImage", "inputs": {"image": "unused.png"}}
    result = analyze_workflow(graph)
    assert result.suggested_bindings.images == [InputRef(node="10", input="image")]


def test_unnumbered_slots_are_ordered_by_id_and_asked() -> None:
    graph = clone(IMG2IMG_GRAPH)
    graph["15"] = {"class_type": "LoadImage", "inputs": {"image": "reference.png"}}
    graph["16"] = {"class_type": "MergeImages", "inputs": {"first": ["15", 0], "second": ["10", 0]}}
    graph["11"]["inputs"]["pixels"] = ["16", 0]
    result = analyze_workflow(graph)
    assert [r.node for r in result.suggested_bindings.images] == ["10", "15"]
    reading = _reading(result, "images")
    assert reading.confidence == "ask"
    assert reading.question in result.warnings


def test_analyze_never_suggests_wired_inputs() -> None:
    for graph in [*(copy.deepcopy(g) for g in SYNTH.values()), _switch_t2i_graph()]:
        result = analyze_workflow(graph)
        assert not any(v.code == "wired_input" for v in result.validation)


def test_title_convention_wins_over_structure() -> None:
    graph = _qwen_edit_alpha_graph()
    graph["459:481"]["_meta"] = {"title": "gakei:prompt"}
    result = analyze_workflow(graph)
    assert result.suggested_bindings.prompt == InputRef(node="459:481", input="value")
    assert _reading(result, "prompt").confidence == "marked"


def test_object_info_marks_custom_save_node_as_output() -> None:
    graph = clone(T2I_GRAPH)
    graph["9"]["class_type"] = "MyImageWriter"
    assert analyze_workflow(clone(graph)).suggested_bindings.outputs == []
    object_info = {
        "MyImageWriter": {"input": {"required": {"images": ["IMAGE", {}]}}, "output_node": True}
    }
    result = analyze_workflow(graph, object_info)
    assert result.suggested_bindings.outputs == ["9"]


# -- 保存時の検証 ----------------------------------------------------------------------


def _t2i_bindings(**update: Any) -> Bindings:
    base = Bindings(
        prompt=InputRef(node="6", input="text"),
        negative_prompt=InputRef(node="7", input="text"),
        seed=[InputRef(node="3", input="seed")],
        width=InputRef(node="5", input="width"),
        height=InputRef(node="5", input="height"),
        batch_size=InputRef(node="5", input="batch_size"),
        outputs=["9"],
    )
    return base.model_copy(update=update)


def _codes(issues) -> list[tuple[str, str]]:
    return [(i.level, i.code) for i in issues]


def test_check_valid_t2i_has_no_issues() -> None:
    assert check_workflow(clone(T2I_GRAPH), "generate", _t2i_bindings(), []) == []


def test_check_wired_input_is_an_error() -> None:
    bindings = _t2i_bindings(prompt=InputRef(node="3", input="positive"))
    issues = check_workflow(clone(T2I_GRAPH), "generate", bindings, [])
    assert ("error", "wired_input") in _codes(issues)
    exposed = [ExposedParam(name="model_in", node="3", input="model", type="text", label="m")]
    issues = check_workflow(clone(T2I_GRAPH), "generate", _t2i_bindings(), exposed)
    assert _codes(issues) == [("error", "wired_input")]
    with pytest.raises(WorkflowValidationError, match="配線"):
        validate_workflow(clone(T2I_GRAPH), "generate", _t2i_bindings(), exposed)


def test_check_type_mismatch_is_an_error() -> None:
    graph = clone(T2I_GRAPH)
    issues = check_workflow(
        graph, "generate", _t2i_bindings(prompt=InputRef(node="3", input="steps")), []
    )
    assert ("error", "type_mismatch") in _codes(issues)
    issues = check_workflow(
        graph, "generate", _t2i_bindings(seed=[InputRef(node="3", input="sampler_name")]), []
    )
    assert ("error", "type_mismatch") in _codes(issues)
    graph["3"]["inputs"]["add_noise"] = True
    exposed = [ExposedParam(name="noise", node="3", input="add_noise", type="text", label="n")]
    assert _codes(check_workflow(graph, "generate", _t2i_bindings(), exposed)) == [
        ("error", "type_mismatch")
    ]
    # /object_info で選択肢と分かる入力はプロンプトにできない。
    issues = check_workflow(
        clone(T2I_GRAPH),
        "generate",
        _t2i_bindings(prompt=InputRef(node="3", input="sampler_name")),
        [],
        QWEN_LIKE_OBJECT_INFO,
    )
    assert ("error", "type_mismatch") in _codes(issues)


def test_check_duplicate_target_is_an_error() -> None:
    issues = check_workflow(
        clone(T2I_GRAPH),
        "generate",
        _t2i_bindings(negative_prompt=InputRef(node="6", input="text")),
        [],
    )
    assert _codes(issues) == [("error", "duplicate")]


def test_check_output_without_generated_image_is_an_error() -> None:
    issues = check_workflow(clone(T2I_GRAPH), "generate", _t2i_bindings(outputs=["4"]), [])
    assert ("error", "output_no_image") in _codes(issues)
    graph = clone(IMG2IMG_GRAPH)
    graph["20"] = {"class_type": "SaveImage", "inputs": {"images": ["10", 0]}}
    bindings = Bindings(
        prompt=InputRef(node="6", input="text"),
        images=[InputRef(node="10", input="image")],
        outputs=["20"],
    )
    assert ("error", "output_no_image") in _codes(check_workflow(graph, "edit", bindings, []))


def test_check_display_only_output_is_a_warning() -> None:
    issues = check_workflow(
        clone(OUTPUT_FILTER_GRAPH), "generate", _t2i_bindings(outputs=["9", "70"]), []
    )
    assert _codes(issues) == [("warning", "display_only_output")]


def test_graph_node_states() -> None:
    graph = clone(T2I_GRAPH)
    graph["20"] = {"class_type": "PreviewImage", "inputs": {"images": ["21", 0]}}
    graph["21"] = {"class_type": "ImageInvert", "inputs": {"image": ["8", 0]}}
    graph["22"] = {"class_type": "LoadImage", "inputs": {"image": "x.png"}}
    states = WorkflowGraph(graph).node_states()
    assert states["3"] == "live"
    assert states["21"] == "display"
    assert states["20"] == "display"
    assert states["22"] == "unused"


def test_paths_follow_positive_and_negative_outputs_of_shared_encoder() -> None:
    from tests.comfyui_graphs import QWEN_LIKE_GRAPH

    result = analyze_workflow(clone(QWEN_LIKE_GRAPH))
    prompt_path = _reading(result, "prompt").paths[0]
    negative_path = _reading(result, "negative_prompt").paths[0]
    assert prompt_path[1].input == "positive"
    assert negative_path[1].input == "negative"
    # 計算するノード(ResolutionSelector)の入力を公開パラメーターに薦める。
    tiers = {(p.node, p.input): p.tier for p in result.param_tiers}
    assert tiers[("13", "aspect_ratio")] == "recommended"
    assert result.suggested_bindings.width is None


# -- 図に使う情報(配線・錨の種類・根拠。ADR-0052 4章) -------------------------------------


def test_edges_mark_unchosen_switch_branch() -> None:
    result = analyze_workflow(_switch_t2i_graph())
    edges = {(e.source, e.target, e.input): e for e in result.edges}
    # テンプレートの配線がすべて並ぶ。
    wired = sum(
        1
        for node in _switch_t2i_graph().values()
        for value in node["inputs"].values()
        if isinstance(value, list)
    )
    assert len(result.edges) == wired
    # transparent=False: on_false(30)が選ばれ、on_true(31)は辿らない。
    assert edges[("30", "32", "on_false")].followed is True
    assert edges[("31", "32", "on_true")].followed is False
    assert edges[("40", "41", "samples")].output == 0
    assert edges[("23", "40", "negative")].output == 1
    # /object_info が無ければ出力の型は分からない。
    assert all(e.output_type is None for e in result.edges)


def test_nodes_carry_anchor_kind() -> None:
    result = analyze_workflow(_switch_t2i_graph())
    kinds = {n["id"]: n["kind"] for n in result.nodes}
    assert kinds["40"] == "sampler"
    assert kinds["23"] == "encoder"
    assert kinds["42"] == "save"
    assert kinds["22"] == "display"
    assert kinds["32"] == "switch"
    assert kinds["10"] is None
    edit = analyze_workflow(_qwen_edit_alpha_graph())
    edit_kinds = {n["id"]: n["kind"] for n in edit.nodes}
    assert edit_kinds["10"] == "image_source"
    # 正と負のエンコーダーはどちらも錨。
    assert edit_kinds["459:474"] == "encoder"
    assert edit_kinds["459:475"] == "encoder"


def test_absent_mask_names_the_transparency_wire_as_evidence() -> None:
    result = analyze_workflow(_qwen_edit_alpha_graph())
    evidence = [(s.node, s.input) for s in _reading(result, "mask").evidence]
    assert evidence == [("10", None), ("459:479", "alpha")]


def test_absent_size_names_unchosen_latent_branch_as_evidence() -> None:
    graph = _qwen_edit_alpha_graph()
    graph["459:490"] = N("EmptySD3LatentImage", width=1024, height=1024, batch_size=1)
    graph["459:491"] = N(
        "ComfySwitchNode", switch=False, on_true=W("459:490"), on_false=W("459:476")
    )
    graph["459:477"]["inputs"]["latent_image"] = W("459:491")
    result = analyze_workflow(graph)
    assert result.suggested_bindings.width is None
    for field_name in ("width", "height", "batch_size"):
        reading = _reading(result, field_name)
        assert reading.confidence == "absent"
        evidence = [(s.node, s.input) for s in reading.evidence]
        assert evidence == [("459:476", None), ("459:491", "on_true"), ("459:490", None)]
    states = {n["id"]: n["state"] for n in result.nodes}
    assert states["459:490"] == "unused"


def test_found_items_have_no_evidence() -> None:
    result = analyze_workflow(copy.deepcopy(SYNTH["S1_sd15_t2i"]))
    assert all(r.evidence == [] for r in result.readings if r.confidence != "absent")
