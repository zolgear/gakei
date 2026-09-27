"""`app/domain/comfy_workflow.py` の純粋関数のテスト(ADR-0013)。

DB や HTTP には一切触れない。ワークフローのグラフは `tests/comfyui_graphs.py` の
t2i / img2img / inpaint と、優先順位を確かめるための小さなグラフを使う。
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.domain.comfy_workflow import (
    SEED_MAX,
    Bindings,
    ExposedParam,
    InputRef,
    MaskBinding,
    SuggestedBindings,
    WorkflowValidationError,
    analyze_workflow,
    compute_template_sha256,
    is_api_format_template,
    is_ui_format_template,
    resolve_prompt,
    validate_workflow,
    workflow_model_capabilities,
)
from app.domain.models import ComfyWorkflow
from tests.comfyui_graphs import (
    DUAL_CFG_GRAPH,
    FLUX_GUIDANCE_GRAPH,
    GUIDER_BASIC_GRAPH,
    IMG2IMG_GRAPH,
    INPAINT_GRAPH,
    MULTI_HOP_OBJECT_INFO,
    MULTI_HOP_PROMPT_GRAPH,
    NO_PROMPT_CANDIDATES_GRAPH,
    OUTPUT_FILTER_GRAPH,
    OUTPUT_FILTER_OBJECT_INFO,
    PAINTER_MASK_GRAPH,
    PRIMITIVE_LATENT_SIZE_GRAPH,
    PRIMITIVE_SEED_GRAPH,
    PRIMITIVE_SEED_OBJECT_INFO,
    PRIMITIVE_TEXT_GRAPH,
    PROMPT_ENHANCER_GRAPH,
    QWEN_EDIT_GRAPH,
    QWEN_LIKE_GRAPH,
    QWEN_LIKE_OBJECT_INFO,
    T2I_GRAPH,
    TWO_LOAD_IMAGE_GRAPH,
    clone,
)

# -- 形式判定 -----------------------------------------------------------------


def test_is_ui_format_template_detects_nodes_and_links() -> None:
    assert is_ui_format_template({"nodes": [], "links": []}) is True
    assert is_ui_format_template(clone(T2I_GRAPH)) is False


def test_is_api_format_template() -> None:
    assert is_api_format_template(clone(T2I_GRAPH)) is True
    assert is_api_format_template({}) is False
    assert is_api_format_template({"1": {"class_type": "X"}}) is False  # inputs が無い
    assert is_api_format_template({"1": {"inputs": {}}}) is False  # class_type が無い
    assert is_api_format_template("not a dict") is False


# -- analyze: 3つのグラフ -----------------------------------------------------


def test_analyze_t2i_suggests_generate() -> None:
    result = analyze_workflow(clone(T2I_GRAPH))

    assert result.suggested_operation == "generate"
    assert result.suggested_bindings is not None
    bindings = result.suggested_bindings
    assert bindings.prompt == InputRef(node="6", input="text")
    assert bindings.negative_prompt == InputRef(node="7", input="text")
    assert bindings.seed == [InputRef(node="3", input="seed")]
    assert bindings.width == InputRef(node="5", input="width")
    assert bindings.height == InputRef(node="5", input="height")
    assert bindings.batch_size == InputRef(node="5", input="batch_size")
    assert bindings.images == []
    assert bindings.mask is None
    assert bindings.outputs == ["9"]
    assert result.warnings == []

    names = {p.name for p in result.candidate_params}
    assert {"ckpt_name", "steps", "cfg", "sampler_name", "scheduler", "denoise"} <= names
    # 差し込み先に使われている入力は候補に出ない
    assert "text" not in {p.label for p in result.candidate_params if p.node in ("6", "7")}


def test_analyze_img2img_suggests_edit() -> None:
    result = analyze_workflow(clone(IMG2IMG_GRAPH))

    assert result.suggested_operation == "edit"
    assert result.suggested_bindings is not None
    bindings = result.suggested_bindings
    assert bindings.images == [InputRef(node="10", input="image")]
    assert bindings.mask is None
    assert bindings.outputs == ["9"]
    assert result.warnings == []


def test_analyze_inpaint_suggests_edit_with_mask() -> None:
    result = analyze_workflow(clone(INPAINT_GRAPH))

    assert result.suggested_operation == "edit"
    assert result.suggested_bindings is not None
    bindings = result.suggested_bindings
    assert bindings.images == [InputRef(node="10", input="image")]
    assert bindings.mask == MaskBinding(mode="load_image_mask", node="12", input="image")
    assert bindings.outputs == ["9"]


def test_analyze_node_listing_reports_linked_flag() -> None:
    result = analyze_workflow(clone(T2I_GRAPH))
    sampler = next(n for n in result.nodes if n["id"] == "3")
    by_name = {i["name"]: i for i in sampler["inputs"]}
    assert by_name["positive"]["linked"] is True
    assert by_name["steps"]["linked"] is False
    assert by_name["steps"]["value"] == 20


# -- analyze: タイトルの優先順位 -----------------------------------------------


def test_analyze_title_overrides_heuristics() -> None:
    graph = clone(T2I_GRAPH)
    # ヒューリスティックなら "6" が prompt になるところを、タイトルで "7" を指定する。
    graph["7"]["_meta"] = {"title": "gakei:prompt"}
    result = analyze_workflow(graph)
    assert result.suggested_bindings is not None
    assert result.suggested_bindings.prompt == InputRef(node="7", input="text")


def test_analyze_title_output_node() -> None:
    graph = clone(T2I_GRAPH)
    graph["9"]["_meta"] = {"title": "gakei:output"}
    result = analyze_workflow(graph)
    assert result.suggested_bindings is not None
    assert result.suggested_bindings.outputs == ["9"]


# -- analyze: 提案できないときの warnings --------------------------------------


def test_analyze_missing_save_image_warns_but_keeps_other_suggestions() -> None:
    # グラフに PreviewImage しかなく SaveImage が無い場合は、汎用の「出力ノードが
    # 見つかりませんでした」ではなく、SaveImage を追加するよう具体的に案内する
    # (ADR-0013 フォローアップ。PreviewImage の出力は GAKEI に取り込めないため)。
    graph = clone(T2I_GRAPH)
    graph["9"]["class_type"] = "PreviewImage"
    result = analyze_workflow(graph)

    assert result.suggested_bindings.outputs == []
    # 出力ノードが見つからなくても、プロンプトや seed の提案は捨てない。
    assert result.suggested_bindings.prompt == InputRef(node="6", input="text")
    assert result.suggested_bindings.seed == [InputRef(node="3", input="seed")]
    assert any("PreviewImage" in w and "SaveImage" in w for w in result.warnings)


def test_analyze_missing_prompt_warns() -> None:
    graph = {
        "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        # positive/negative の配線先だが、文字列の入力を持たない(すべて配線)ので辿れない。
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ["2", 0]}},
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": 1,
                "steps": 1,
                "cfg": 1.0,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 1.0,
                "model": ["4", 0],
                "positive": ["4", 0],
                "negative": ["4", 0],
                "latent_image": ["4", 0],
            },
        },
    }
    result = analyze_workflow(graph)
    assert result.suggested_bindings.prompt is None
    # プロンプトが見つからなくても、出力ノードの提案は残る。
    assert result.suggested_bindings.outputs == ["9"]
    assert any("プロンプト" in w for w in result.warnings)


def test_analyze_prompt_enhancer_warns_with_candidates() -> None:
    """正規表現置換やテキスト生成ノードを挟んで複数の固定文字列ノードに行き着く
    「プロンプト強化」型のワークフローは、既存のヒューリスティックでは特定できない。
    その場合、探索が辿り着いた先の直値の文字列入力を手動選択の候補として警告に列挙する。
    """
    result = analyze_workflow(clone(PROMPT_ENHANCER_GRAPH))
    assert result.suggested_bindings.prompt is None

    warning = next(w for w in result.warnings if "候補" in w)
    assert "479" in warning
    assert "編集指示" in warning
    assert "478" in warning
    assert "PE system prompt" in warning
    assert "480" in warning
    assert "PE suffix" in warning
    # 設定値(regex_pattern/replace/delimiter やサンプリング設定)は候補に出ない。
    assert "regex_pattern" not in warning
    assert "text-gen-model" not in warning


def test_analyze_prompt_search_without_candidates_keeps_old_warning() -> None:
    """辿った先に直値の文字列入力が1つも無ければ、これまで通りの文言のまま
    (候補の一覧は出さない)。"""
    result = analyze_workflow(clone(NO_PROMPT_CANDIDATES_GRAPH))
    assert result.suggested_bindings.prompt is None
    assert "プロンプトの差し込み先を特定できませんでした。手動で選択してください。" in (
        result.warnings
    )


def test_analyze_edit_missing_image_warns() -> None:
    graph = {"10a": {"class_type": "LoadImage", "inputs": {}}}
    result = analyze_workflow(graph)
    assert result.suggested_operation == "edit"
    assert result.suggested_bindings.images == []
    assert any("入力画像" in w for w in result.warnings)


# -- analyze: サブグラフ展開後の Qwen 型グラフ(TextEncodeQwenImage21、SaveImageAdvanced) ------
#
# positive/negative が同じノード(TextEncodeQwenImage21)の出力 0/1 を指す、ノード ID が
# "459:452" のような形、出力ノードが SaveImageAdvanced、というグラフ(ADR-0013 フォローアップ)。


def test_analyze_qwen_like_without_object_info_finds_prompt_negative_seed_output() -> None:
    result = analyze_workflow(clone(QWEN_LIKE_GRAPH))

    bindings = result.suggested_bindings
    assert bindings.prompt == InputRef(node="459:452", input="prompt")
    assert bindings.negative_prompt == InputRef(node="459:452", input="negative_prompt")
    # /object_info が無いときは入力名 seed / noise_seed の整数値から推定する。
    assert bindings.seed == [InputRef(node="459:458", input="seed")]
    # /object_info が無いときは class_type に SaveImage を含むもの。PreviewImage は除く。
    assert bindings.outputs == ["461"]
    assert result.suggested_operation == "generate"
    assert result.warnings == []

    names = {p.name for p in result.candidate_params}
    assert {"steps", "cfg", "sampler_name", "aspect_ratio", "megapixels"} <= names
    # 差し込み先(prompt/negative_prompt/seed)は候補から除かれている。
    assert "prompt" not in {p.input for p in result.candidate_params if p.node == "459:452"}


def test_analyze_qwen_like_with_object_info_uses_control_after_generate_and_output_node() -> None:
    result = analyze_workflow(clone(QWEN_LIKE_GRAPH), QWEN_LIKE_OBJECT_INFO)

    bindings = result.suggested_bindings
    assert bindings.prompt == InputRef(node="459:452", input="prompt")
    assert bindings.negative_prompt == InputRef(node="459:452", input="negative_prompt")
    # KSampler.seed が control_after_generate: true の INT なので、それを候補にする。
    assert bindings.seed == [InputRef(node="459:458", input="seed")]
    # output_node: true の SaveImageAdvanced だけを候補にし、PreviewImage(同じく
    # output_node: true だが一時ファイル)は除く。
    assert bindings.outputs == ["461"]
    assert result.warnings == []


def test_find_seed_refs_from_object_info_ignores_wired_seed() -> None:
    graph = clone(QWEN_LIKE_GRAPH)
    graph["459:458"]["inputs"]["seed"] = ["13", 0]  # 配線されていれば候補にしない
    result = analyze_workflow(graph, QWEN_LIKE_OBJECT_INFO)
    assert result.suggested_bindings.seed == []


def test_find_output_ids_fallback_ignores_preview_like_class_types() -> None:
    graph = clone(QWEN_LIKE_GRAPH)
    graph["998"] = {"class_type": "PreviewImageWithMetadata", "inputs": {"images": ["459:457", 0]}}
    result = analyze_workflow(graph)
    assert result.suggested_bindings.outputs == ["461"]


# -- 22本の公式テンプレートの検証(2026-09-23)で見つかった問題の再現テスト -----------------


def test_seed_object_info_ignores_generic_primitive_but_follows_wired_seed() -> None:
    """`control_after_generate` は真偽値 true のときだけ seed とみなす。ComfyUI の汎用
    `PrimitiveInt` は同じキーに列挙値の文字列(`"fixed"` など)を返すため、修正前は
    Steps 用に切り出した PrimitiveInt まで seed として誤検出していた。KSampler.seed が
    Primitive ノードへの配線になっているときは、その配線元の `value` を seed の
    差し込み先にする。
    """
    result = analyze_workflow(clone(PRIMITIVE_SEED_GRAPH), PRIMITIVE_SEED_OBJECT_INFO)
    assert result.suggested_bindings.seed == [InputRef(node="21", input="value")]


def test_seed_fallback_follows_wired_seed_to_primitive_node() -> None:
    """`/object_info` が無いときの名前による推定(`seed`)も、配線なら配線元の
    Primitive ノードを1段辿る。"""
    result = analyze_workflow(clone(PRIMITIVE_SEED_GRAPH))
    assert result.suggested_bindings.seed == [InputRef(node="21", input="value")]


def test_width_height_matches_empty_star_latent_not_just_empty_latent_image() -> None:
    """class_type の完全一致(`EmptyLatentImage`)をやめ、`Empty` で始まり `Latent` を含む
    ノード(`EmptySD3LatentImage` など)を対象にする。配線なら Primitive ノードを1段辿る。
    """
    result = analyze_workflow(clone(PRIMITIVE_LATENT_SIZE_GRAPH))
    bindings = result.suggested_bindings
    assert bindings.width == InputRef(node="30", input="value")
    assert bindings.height == InputRef(node="31", input="value")
    assert bindings.batch_size == InputRef(node="5", input="batch_size")


def test_prompt_search_passes_through_flux_guidance_and_nulls_zeroed_negative() -> None:
    """`FluxGuidance` のような素通りノードの先まで辿ってプロンプトを見つける。
    `ConditioningZeroOut` を通った先は negative 側であっても「空にしている」ので、
    ネガティブの差し込み先にはしない(null が正しい)。
    """
    result = analyze_workflow(clone(FLUX_GUIDANCE_GRAPH))
    bindings = result.suggested_bindings
    assert bindings.prompt == InputRef(node="6", input="text")
    assert bindings.negative_prompt is None
    assert result.warnings == []


def test_prompt_search_follows_wired_text_to_primitive_string_node() -> None:
    """`CLIPTextEncode.text` が直値ではなく `PrimitiveStringMultiline` への配線に
    なっているワークフロー向けに、配線元の値を1段辿る。"""
    result = analyze_workflow(clone(PRIMITIVE_TEXT_GRAPH))
    assert result.suggested_bindings.prompt == InputRef(node="50", input="value")


def test_prompt_search_multi_hop_and_avoids_enum_input() -> None:
    """`ReferenceLatent` → `FluxKontextMultiReferenceLatentMethod` と2段の素通りノードを
    越えてプロンプトを見つける(深さ上限4の範囲内)。`reference_latents_method` のような
    enum の入力を誤ってプロンプトとして拾わない。negative は `ConditioningZeroOut` で
    空にされているので null。名前だけの推定(object_info 無し)と、`/object_info` の
    CONDITIONING / STRING 型に基づく推定の両方で確認する。
    """
    for object_info in (None, MULTI_HOP_OBJECT_INFO):
        result = analyze_workflow(clone(MULTI_HOP_PROMPT_GRAPH), object_info)
        bindings = result.suggested_bindings
        assert bindings.prompt == InputRef(node="6", input="text")
        assert bindings.negative_prompt is None


def test_find_sampler_node_id_supports_dual_cfg_guider() -> None:
    """`DualCFGGuider`(`cond1`/`negative` という入力名。`positive` という名前は持たない)を
    サンプラー相当のノードとして認識し、`cond1` を positive として扱う。
    """
    result = analyze_workflow(clone(DUAL_CFG_GRAPH))
    bindings = result.suggested_bindings
    assert bindings.prompt == InputRef(node="6", input="text")
    assert bindings.negative_prompt == InputRef(node="7", input="text")


def test_find_sampler_node_id_follows_guider_to_basic_guider() -> None:
    """`SamplerCustomAdvanced` の `guider` を1段辿って `BasicGuider`(`conditioning` のみ。
    negative の概念が無い)を見つける。"""
    result = analyze_workflow(clone(GUIDER_BASIC_GRAPH))
    bindings = result.suggested_bindings
    assert bindings.prompt == InputRef(node="6", input="text")
    assert bindings.negative_prompt is None


def test_output_ids_from_object_info_prefers_save_over_compare() -> None:
    """`ImageCompare` は `output_node: true` を返す UI 専用ノード(画像を保存しない)。
    `Save` を含む出力候補が1つでもあれば、`Save` を含まないものは除く。
    """
    result = analyze_workflow(clone(OUTPUT_FILTER_GRAPH), OUTPUT_FILTER_OBJECT_INFO)
    assert result.suggested_bindings.outputs == ["9"]


def test_no_output_and_only_preview_image_warns_to_add_save_image() -> None:
    graph = {
        "9": {"class_type": "PreviewImage", "inputs": {"images": ["8", 0]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
    }
    result = analyze_workflow(graph)
    assert result.suggested_bindings.outputs == []
    assert any("PreviewImage" in w and "SaveImage" in w for w in result.warnings)


def test_edit_missing_mask_warns_when_graph_has_unsupported_mask_node() -> None:
    """マスクの差し込み先が見つからなくても、グラフに専用のマスク作成ノード
    (`Painter` を含む、`LoadImage`/`LoadImageMask` 以外)があれば、GAKEI から渡せない
    方式だと警告する。
    """
    result = analyze_workflow(clone(PAINTER_MASK_GRAPH))
    assert result.suggested_bindings.mask is None
    assert any("マスク" in w and "GAKEI" in w for w in result.warnings)


def test_two_load_image_nodes_suggests_both_as_ordered_slots_without_warning() -> None:
    # 番号付きの可変長入力もタイトルも無い場合は、テンプレート内の出現順(id 順に相当)で
    # 両方を枠として提案する。2枚以上の入力画像であること自体は警告しない
    # (ADR-0013 3節フォローアップ: 枠の数だけ要求すればよい)。
    result = analyze_workflow(clone(TWO_LOAD_IMAGE_GRAPH))
    assert result.suggested_bindings.images == [
        InputRef(node="10", input="image"),
        InputRef(node="15", input="image"),
    ]
    assert not any("1枚" in w for w in result.warnings)


def test_analyze_qwen_edit_orders_slots_by_autogrow_number_not_node_id() -> None:
    # image_2 の LoadImage(id="410")は image_1 の LoadImage(id="480")より小さい id だが、
    # 「images.image_1」「images.image_2」の番号どおりに並ぶことを確かめる
    # (ADR-0013 3節: 可変長の画像入力につながる LoadImage は、その番号の順)。
    result = analyze_workflow(clone(QWEN_EDIT_GRAPH))

    assert result.suggested_operation == "edit"
    bindings = result.suggested_bindings
    assert bindings.images == [
        InputRef(node="480", input="image"),
        InputRef(node="410", input="image"),
    ]
    assert bindings.prompt == InputRef(node="452", input="prompt")
    assert bindings.negative_prompt == InputRef(node="452", input="negative_prompt")
    assert bindings.outputs == ["461"]
    assert result.warnings == []


def test_analyze_numbered_gakei_image_titles_override_heuristic_and_id_order() -> None:
    # gakei:image1 / gakei:image2 のタイトルは、可変長入力の番号やノード id の並びより
    # 優先する(ADR-0013 3節)。
    graph = clone(QWEN_EDIT_GRAPH)
    # ヒューリスティックなら 480 が枠1になるところを、タイトルで逆にする。
    graph["480"]["_meta"] = {"title": "gakei:image2"}
    graph["410"]["_meta"] = {"title": "gakei:image1"}

    result = analyze_workflow(graph)
    assert result.suggested_bindings.images == [
        InputRef(node="410", input="image"),
        InputRef(node="480", input="image"),
    ]


def test_analyze_bare_gakei_image_title_is_single_slot() -> None:
    graph = clone(IMG2IMG_GRAPH)
    graph["10"]["_meta"] = {"title": "gakei:image"}
    result = analyze_workflow(graph)
    assert result.suggested_bindings.images == [InputRef(node="10", input="image")]


# -- validate_workflow ---------------------------------------------------------


def _t2i_bindings() -> Bindings:
    return Bindings(
        prompt=InputRef(node="6", input="text"),
        negative_prompt=InputRef(node="7", input="text"),
        seed=[InputRef(node="3", input="seed")],
        width=InputRef(node="5", input="width"),
        height=InputRef(node="5", input="height"),
        batch_size=InputRef(node="5", input="batch_size"),
        outputs=["9"],
    )


def test_validate_workflow_accepts_valid_t2i() -> None:
    validate_workflow(clone(T2I_GRAPH), "generate", _t2i_bindings(), [])


def test_validate_workflow_rejects_ui_format() -> None:
    with pytest.raises(WorkflowValidationError, match="Export"):
        validate_workflow({"nodes": [], "links": []}, "generate", _t2i_bindings(), [])


def test_validate_workflow_rejects_non_api_format() -> None:
    with pytest.raises(WorkflowValidationError):
        validate_workflow({"1": {"foo": "bar"}}, "generate", _t2i_bindings(), [])


def test_validate_workflow_rejects_missing_node() -> None:
    bindings = _t2i_bindings().model_copy(update={"prompt": InputRef(node="99", input="text")})
    with pytest.raises(WorkflowValidationError, match="99"):
        validate_workflow(clone(T2I_GRAPH), "generate", bindings, [])


def test_validate_workflow_rejects_missing_input() -> None:
    bindings = _t2i_bindings().model_copy(update={"prompt": InputRef(node="6", input="nope")})
    with pytest.raises(WorkflowValidationError, match="nope"):
        validate_workflow(clone(T2I_GRAPH), "generate", bindings, [])


def test_validate_workflow_rejects_edit_without_image() -> None:
    bindings = Bindings(prompt=InputRef(node="6", input="text"), outputs=["9"])
    with pytest.raises(WorkflowValidationError, match="image"):
        validate_workflow(clone(IMG2IMG_GRAPH), "edit", bindings, [])


def test_validate_workflow_accepts_multiple_image_slots() -> None:
    bindings = Bindings(
        prompt=InputRef(node="6", input="text"),
        images=[
            InputRef(node="10", input="image"),
            InputRef(node="15", input="image"),
        ],
        outputs=["9"],
    )
    validate_workflow(clone(TWO_LOAD_IMAGE_GRAPH), "edit", bindings, [])


def test_validate_workflow_rejects_duplicate_image_slots() -> None:
    bindings = Bindings(
        prompt=InputRef(node="6", input="text"),
        images=[
            InputRef(node="10", input="image"),
            InputRef(node="10", input="image"),
        ],
        outputs=["9"],
    )
    with pytest.raises(WorkflowValidationError, match="重複"):
        validate_workflow(clone(TWO_LOAD_IMAGE_GRAPH), "edit", bindings, [])


def test_validate_workflow_rejects_image_slot_equal_to_mask_ref() -> None:
    bindings = Bindings(
        prompt=InputRef(node="6", input="text"),
        images=[InputRef(node="12", input="image")],
        mask=MaskBinding(mode="load_image_mask", node="12", input="image"),
        outputs=["9"],
    )
    with pytest.raises(WorkflowValidationError, match="重複"):
        validate_workflow(clone(INPAINT_GRAPH), "edit", bindings, [])


def test_validate_workflow_rejects_generate_with_mask_binding() -> None:
    bindings = _t2i_bindings().model_copy(update={"mask": MaskBinding(mode="image_alpha")})
    with pytest.raises(WorkflowValidationError, match="generate"):
        validate_workflow(clone(T2I_GRAPH), "generate", bindings, [])


def test_validate_workflow_rejects_duplicate_binding_targets() -> None:
    bindings = _t2i_bindings().model_copy(
        update={"negative_prompt": InputRef(node="6", input="text")}
    )
    with pytest.raises(WorkflowValidationError, match="重複"):
        validate_workflow(clone(T2I_GRAPH), "generate", bindings, [])


def test_validate_workflow_rejects_exposed_param_overlapping_binding() -> None:
    exposed = [
        ExposedParam(name="steps", node="6", input="text", type="text", label="text"),
    ]
    with pytest.raises(WorkflowValidationError, match="重複"):
        validate_workflow(clone(T2I_GRAPH), "generate", _t2i_bindings(), exposed)


def test_validate_workflow_rejects_exposed_param_missing_input() -> None:
    exposed = [ExposedParam(name="steps", node="3", input="missing", type="int", label="steps")]
    with pytest.raises(WorkflowValidationError):
        validate_workflow(clone(T2I_GRAPH), "generate", _t2i_bindings(), exposed)


def test_validate_workflow_rejects_duplicate_exposed_param_names() -> None:
    exposed = [
        ExposedParam(name="steps", node="3", input="steps", type="int", label="steps"),
        ExposedParam(name="steps", node="3", input="cfg", type="float", label="cfg"),
    ]
    with pytest.raises(WorkflowValidationError, match="重複"):
        validate_workflow(clone(T2I_GRAPH), "generate", _t2i_bindings(), exposed)


# -- ExposedParam の名前規則(pydantic のフィールドバリデータ) -------------------


@pytest.mark.parametrize(
    "name",
    ["Steps", "1steps", "steps-count", "comfyui_seed", "prompt", "seed", "model", "operation"],
)
def test_exposed_param_rejects_invalid_names(name: str) -> None:
    with pytest.raises(ValidationError):
        ExposedParam(name=name, node="3", input="steps", type="int", label="steps")


def test_exposed_param_accepts_valid_name() -> None:
    param = ExposedParam(name="ckpt_name", node="4", input="ckpt_name", type="text", label="ckpt")
    assert param.name == "ckpt_name"


def test_mask_binding_requires_node_and_input_for_load_image_mask() -> None:
    with pytest.raises(ValidationError):
        MaskBinding(mode="load_image_mask")


def test_bindings_requires_at_least_one_output() -> None:
    with pytest.raises(ValidationError):
        Bindings(prompt=InputRef(node="6", input="text"), outputs=[])


# -- 旧形式(`image` 単数)からの互換変換 --------------------------------------------


def test_bindings_converts_legacy_image_field_to_images_list() -> None:
    # 移行前に保存された DB の行(`bindings` は JSON)や、移行前のクライアントからの
    # 入力を、そのまま読み込めることを確かめる(ADR-0013 3節フォローアップ)。
    bindings = Bindings.model_validate(
        {
            "prompt": {"node": "6", "input": "text"},
            "image": {"node": "10", "input": "image"},
            "outputs": ["9"],
        }
    )
    assert bindings.images == [InputRef(node="10", input="image")]


def test_bindings_prefers_images_over_legacy_image_when_both_present() -> None:
    bindings = Bindings.model_validate(
        {
            "prompt": {"node": "6", "input": "text"},
            "image": {"node": "10", "input": "image"},
            "images": [{"node": "15", "input": "image"}],
            "outputs": ["9"],
        }
    )
    assert bindings.images == [InputRef(node="15", input="image")]


def test_suggested_bindings_converts_legacy_image_field_to_images_list() -> None:
    bindings = SuggestedBindings.model_validate({"image": {"node": "10", "input": "image"}})
    assert bindings.images == [InputRef(node="10", input="image")]


def test_bindings_serializes_only_images_field() -> None:
    bindings = Bindings(
        prompt=InputRef(node="6", input="text"),
        images=[InputRef(node="10", input="image")],
        outputs=["9"],
    )
    dumped = bindings.model_dump(mode="json")
    assert dumped["images"] == [{"node": "10", "input": "image"}]
    assert "image" not in dumped


# -- workflow_model_capabilities ------------------------------------------------


def _make_workflow(
    *, operation: str = "generate", bindings: Bindings, exposed: list[ExposedParam], template=None
) -> ComfyWorkflow:
    return ComfyWorkflow(
        id=uuid.uuid4(),
        name="サンプル",
        operation=operation,
        template=template if template is not None else clone(T2I_GRAPH),
        bindings=bindings.model_dump(mode="json"),
        exposed_params=[p.model_dump(mode="json") for p in exposed],
        template_sha256=compute_template_sha256(template if template is not None else T2I_GRAPH),
    )


def test_workflow_model_capabilities_generate_params_and_order() -> None:
    exposed = [
        ExposedParam(name="ckpt_name", node="4", input="ckpt_name", type="text", label="モデル")
    ]
    wf = _make_workflow(operation="generate", bindings=_t2i_bindings(), exposed=exposed)

    caps = workflow_model_capabilities(wf)
    assert caps.model == str(wf.id)
    assert caps.label == "サンプル"
    assert caps.quality_choices == []
    assert len(caps.operations) == 1

    op = caps.operations[0]
    assert op.operation == "generate"
    assert op.max_input_images == 0
    assert op.min_input_images == 0
    assert op.supports_mask is False
    assert op.requires_mask is False

    names = [p.name for p in op.params]
    assert names == ["negative_prompt", "seed", "width", "height", "batch_size", "ckpt_name"]

    by_name = {p.name: p for p in op.params}
    assert by_name["seed"].type == "int"
    assert by_name["seed"].minimum == 0
    assert by_name["seed"].maximum == SEED_MAX
    assert by_name["seed"].required is False
    assert by_name["width"].default == 512
    assert by_name["ckpt_name"].default == "sd_xl_base_1.0.safetensors"
    assert by_name["ckpt_name"].type == "text"


def test_workflow_model_capabilities_edit_requires_mask() -> None:
    bindings = Bindings(
        prompt=InputRef(node="6", input="text"),
        images=[InputRef(node="10", input="image")],
        mask=MaskBinding(mode="load_image_mask", node="12", input="image"),
        outputs=["9"],
    )
    wf = _make_workflow(
        operation="edit", bindings=bindings, exposed=[], template=clone(INPAINT_GRAPH)
    )
    caps = workflow_model_capabilities(wf)
    op = caps.operations[0]
    assert op.operation == "edit"
    assert op.max_input_images == 1
    assert op.min_input_images == 1
    assert op.supports_mask is True
    assert op.requires_mask is True


def test_workflow_model_capabilities_edit_min_equals_max_for_multiple_slots() -> None:
    # 画像の枠が2つなら min == max == 2 になる(ADR-0013 3節: 枠はすべて必須)。
    bindings = Bindings(
        prompt=InputRef(node="6", input="text"),
        images=[
            InputRef(node="10", input="image"),
            InputRef(node="15", input="image"),
        ],
        outputs=["9"],
    )
    wf = _make_workflow(
        operation="edit", bindings=bindings, exposed=[], template=clone(TWO_LOAD_IMAGE_GRAPH)
    )
    op = workflow_model_capabilities(wf).operations[0]
    assert op.min_input_images == 2
    assert op.max_input_images == 2


# -- resolve_prompt --------------------------------------------------------------


def test_resolve_prompt_is_deterministic_and_does_not_mutate_template() -> None:
    template = clone(T2I_GRAPH)
    bindings = _t2i_bindings()

    first = resolve_prompt(
        template,
        bindings,
        [],
        prompt="a red bicycle",
        params={},
        seed=42,
        image_names=None,
        mask_name=None,
    )
    second = resolve_prompt(
        template,
        bindings,
        [],
        prompt="a red bicycle",
        params={},
        seed=42,
        image_names=None,
        mask_name=None,
    )

    assert first == second
    assert template == T2I_GRAPH  # 元のテンプレートは変わらない

    assert first["6"]["inputs"]["text"] == "a red bicycle"
    assert first["3"]["inputs"]["seed"] == 42
    # 明示的に指定していないので、既定値(テンプレートの値)のまま。
    assert first["7"]["inputs"]["text"] == "blurry, low quality"
    assert first["5"]["inputs"]["width"] == 512


def test_resolve_prompt_sets_all_seed_targets() -> None:
    graph = clone(T2I_GRAPH)
    graph["3b"] = {
        "class_type": "KSamplerAdvanced",
        "inputs": {"noise_seed": 0, "model": ["4", 0]},
    }
    bindings = _t2i_bindings().model_copy(
        update={
            "seed": [
                InputRef(node="3", input="seed"),
                InputRef(node="3b", input="noise_seed"),
            ]
        }
    )
    result = resolve_prompt(
        graph, bindings, [], prompt="x", params={}, seed=999, image_names=None, mask_name=None
    )
    assert result["3"]["inputs"]["seed"] == 999
    assert result["3b"]["inputs"]["noise_seed"] == 999


def test_resolve_prompt_applies_explicit_and_default_exposed_params() -> None:
    exposed = [
        ExposedParam(name="steps", node="3", input="steps", type="int", label="steps", default=30),
        ExposedParam(
            name="sampler_name", node="3", input="sampler_name", type="text", label="sampler"
        ),
    ]
    result = resolve_prompt(
        clone(T2I_GRAPH),
        _t2i_bindings(),
        exposed,
        prompt="x",
        params={"steps": 10},
        seed=1,
        image_names=None,
        mask_name=None,
    )
    # params にあればそちらを優先。
    assert result["3"]["inputs"]["steps"] == 10
    # params に無ければテンプレートの値のまま(sampler_name に default が無いため)。
    assert result["3"]["inputs"]["sampler_name"] == "euler"


def test_resolve_prompt_sets_image_and_mask_names() -> None:
    bindings = Bindings(
        prompt=InputRef(node="6", input="text"),
        images=[InputRef(node="10", input="image")],
        mask=MaskBinding(mode="load_image_mask", node="12", input="image"),
        outputs=["9"],
    )
    result = resolve_prompt(
        clone(INPAINT_GRAPH),
        bindings,
        [],
        prompt="x",
        params={},
        seed=1,
        image_names=["gakei_abc.png"],
        mask_name="gakei_def.png",
    )
    assert result["10"]["inputs"]["image"] == "gakei_abc.png"
    assert result["12"]["inputs"]["image"] == "gakei_def.png"


def test_resolve_prompt_sets_each_slot_in_order() -> None:
    bindings = Bindings(
        prompt=InputRef(node="452", input="prompt"),
        images=[
            InputRef(node="480", input="image"),
            InputRef(node="410", input="image"),
        ],
        outputs=["461"],
    )
    result = resolve_prompt(
        clone(QWEN_EDIT_GRAPH),
        bindings,
        [],
        prompt="x",
        params={},
        seed=1,
        image_names=["gakei_aaa.png", "gakei_bbb.png"],
        mask_name=None,
    )
    assert result["480"]["inputs"]["image"] == "gakei_aaa.png"
    assert result["410"]["inputs"]["image"] == "gakei_bbb.png"
