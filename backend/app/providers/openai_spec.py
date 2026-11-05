"""gpt-image-2.5 / 2.6 系 API 仕様(2.5 は 2026-09-21、2.6 は 2026-11-05 に OpenAI API Reference
と実機で確認済み)。

`FakeProvider` と、ステップ2で実装する OpenAI 実プロバイダーの両方がこの定義を共有する。
実装時は Microsoft Learn / OpenAI の公式ドキュメントで再確認すること(CLAUDE.md 作業ルール)。

表示名(`label`/`choice_labels`)と初期値(`form_default`)は、フォームを一般的な日本語の
見出しで組み立てられるようにするためのもの。既定は「安価な設定」(モデル Flare、
サイズ 1024x1024、画質 low)。料金ページ(2026-09-22)では Sunburst と Flare の単価は同じ
(出力 $30 / 1M tok)なので、Flare を「安価」とは表記しない。`auto` サイズは結果が
安定しないため既定にはしない(選択肢としては残す)。
"""

from __future__ import annotations

from typing import Any

from app.domain import sizes
from app.i18n import t
from app.providers.base import (
    ALL_REGION_KINDS,
    ConditionalParam,
    IncompatiblePair,
    ModelCapabilities,
    OperationCapabilities,
    ParamDef,
    ProviderCapabilities,
    SizeConstraints,
)

# モデルID一覧(表示名・説明は言語ごとに変わるので `_model_info()` で都度組み立てる)。
_MODEL_IDS = [
    "gpt-image-2.6-sunburst",
    "gpt-image-2.6-flare",
    "gpt-image-2.5-sunburst",
    "gpt-image-2.5-flare",
    "gpt-image-2",
]
MODELS = list(_MODEL_IDS)


def _model_info() -> list[tuple[str, str, str]]:
    """(モデルID, 表示名, 説明)。現在の言語で都度組み立てる(リクエストごとに変わるため)。"""
    return [
        (
            "gpt-image-2.6-sunburst",
            "GPT Image 2.6 Sunburst",
            t("openaiSpec.editingStrong"),
        ),
        ("gpt-image-2.6-flare", "GPT Image 2.6 Flare", t("openaiSpec.fast")),
        (
            "gpt-image-2.5-sunburst",
            "GPT Image 2.5 Sunburst",
            t("openaiSpec.editingStrong"),
        ),
        ("gpt-image-2.5-flare", "GPT Image 2.5 Flare", t("openaiSpec.fast")),
        ("gpt-image-2", "GPT Image 2", ""),
    ]


# 2.6 は同じ画質・サイズでも出力のトークンが多く(1024x1024 の low で 2.5 の約 4.2 倍。
# 2026-11-05 の実測)、単価は同じなので1枚あたりが高い。既定は安価な 2.5 Flare のまま。
DEFAULT_MODEL = "gpt-image-2.5-flare"
DEFAULT_SIZE = "1024x1024"

MAX_INPUT_IMAGES = 16
MAX_INPUT_IMAGE_BYTES = 50 * 1024 * 1024
MAX_MASK_BYTES = 4 * 1024 * 1024
# ADR-0061: GIF のコマ数。公式のガイドは 2〜64 と書くが、API は 16 を超えると 400 を返す
# (2026-11-05 の実機確認)。
GIF_FRAME_COUNT_MIN = 2
GIF_FRAME_COUNT_MAX = 16
# GIF の1コマの大きさ。API は全コマを少なくとも2×2のシートに並べ、シートを通常の制約で確かめる
# ので、長辺は通常の半分、総ピクセルは通常の4分の1を上限にする。シートに収まるかは確かめない
# (並べ方が公開されていない。ADR-0061 2章)。
GIF_FRAME_MAX_LONG_EDGE = sizes.MAX_LONG_EDGE // 2
GIF_FRAME_MIN_TOTAL_PIXELS = 256 * 256
GIF_FRAME_MAX_TOTAL_PIXELS = sizes.MAX_TOTAL_PIXELS // 4
# プロンプトの最大長は domain.sizes に集約する(プロンプトセットの検証とも共有するため)。
PROMPT_MAX_LENGTH = sizes.PROMPT_MAX_LENGTH


def _quality_labels() -> dict[str, str]:
    return {
        "auto": t("openaiSpec.qualityAuto"),
        "low": t("openaiSpec.qualityLow"),
        "medium": t("openaiSpec.qualityMedium"),
        "high": t("openaiSpec.qualityHigh"),
        "xhigh": t("openaiSpec.qualityXhigh"),
        "max": t("openaiSpec.qualityMax"),
    }


def _output_format_labels() -> dict[str, str]:
    return {"png": "PNG", "jpeg": "JPEG", "webp": "WebP", "gif": "GIF", "svg": "SVG"}


def _background_labels() -> dict[str, str]:
    return {
        "auto": t("openaiSpec.backgroundAuto"),
        "opaque": t("openaiSpec.backgroundOpaque"),
        "transparent": t("openaiSpec.backgroundTransparent"),
    }


def _complexity_labels() -> dict[str, str]:
    return {
        "low": t("openaiSpec.complexityLow"),
        "medium": t("openaiSpec.complexityMedium"),
        "high": t("openaiSpec.complexityHigh"),
    }


def _supports_complexity(model: str) -> bool:
    # 2.5 は `complexity` を 400 `invalid_value` で拒む(2026-11-05 の実機確認)。
    return model.startswith("gpt-image-2.6")


def _supports_gif(model: str, operation: str) -> bool:
    # ADR-0061: 2.6 の Generate と Edit(Edit も 2026-11-05 の実機で GIF を返した)。
    return model.startswith("gpt-image-2.6")


def _supports_svg(model: str, operation: str) -> bool:
    # ADR-0062: 2.6 の Generate だけ(Edit は SVG を出力も入力もできない)。2.5 は 400。
    return model.startswith("gpt-image-2.6") and operation == "generate"


# SVG を書くモデル(ADR-0062 2章)。テキストのモデルなら受けるが、選択肢は単価を持つものに絞る。
SVG_AUTHOR_MODELS = ["gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"]
SVG_AUTHOR_LABELS = {
    "gpt-5.6-luna": "GPT-5.6 Luna",
    "gpt-5.6-terra": "GPT-5.6 Terra",
    "gpt-5.6-sol": "GPT-5.6 Sol",
}
SVG_EFFORTS = ["none", "low", "medium", "high", "xhigh", "max"]


def fill_svg_author(params: dict[str, Any]) -> dict[str, Any]:
    """SVG で書き手が未指定なら既定(Luna)を入れる(API は `svg.model` が無いと 400)。Run の
    確定で呼び、送る値として記録する(ADR-0062 2章)。"""
    if params.get("output_format") == "svg" and params.get("svg_model") is None:
        return {**params, "svg_model": SVG_AUTHOR_MODELS[0]}
    return params


def _svg_effort_labels() -> dict[str, str]:
    return {
        "none": t("openaiSpec.svgEffortNone"),
        "low": t("openaiSpec.complexityLow"),
        "medium": t("openaiSpec.complexityMedium"),
        "high": t("openaiSpec.complexityHigh"),
        "xhigh": t("openaiSpec.qualityXhigh"),
        "max": t("openaiSpec.qualityMax"),
    }


def _quality_choices(model: str) -> list[str]:
    if model.startswith(("gpt-image-2.5", "gpt-image-2.6")):
        return ["auto", "low", "medium", "high", "xhigh", "max"]
    return ["auto", "low", "medium", "high"]


def _common_params(model: str, operation: str) -> list[ParamDef]:
    quality_choices = _quality_choices(model)
    gif = _supports_gif(model, operation)
    svg = _supports_svg(model, operation)
    format_choices = ["png", "jpeg", "webp"] + (["gif"] if gif else []) + (["svg"] if svg else [])
    params = [
        ParamDef(
            name="quality",
            type="enum",
            label=t("openaiSpec.qualityLabel"),
            choices=quality_choices,
            choice_labels={k: v for k, v in _quality_labels().items() if k in quality_choices},
            default="auto",
            form_default="low",
            description=t("openaiSpec.qualityDescription"),
        ),
        ParamDef(
            name="output_format",
            type="enum",
            label=t("openaiSpec.fileFormatLabel"),
            choices=format_choices,
            choice_labels={k: v for k, v in _output_format_labels().items() if k in format_choices},
            default="png",
            form_default="png",
            description=t("openaiSpec.fileFormatDescription"),
            widget="segmented",
        ),
        ParamDef(
            name="output_compression",
            type="int",
            label=t("openaiSpec.compressionLabel"),
            minimum=0,
            maximum=100,
            description=t("openaiSpec.compressionDescription"),
        ),
        ParamDef(
            name="background",
            type="enum",
            label=t("openaiSpec.backgroundLabel"),
            choices=["auto", "opaque", "transparent"],
            choice_labels=_background_labels(),
            default="auto",
            description=t("openaiSpec.backgroundDescription"),
            widget="segmented",
        ),
        ParamDef(
            name="n",
            type="int",
            label=t("openaiSpec.countLabel"),
            minimum=1,
            maximum=10,
            default=1,
            form_default=1,
            description=t("openaiSpec.countDescription"),
        ),
        ParamDef(
            name="partial_images",
            type="int",
            label=t("openaiSpec.previewLabel"),
            minimum=0,
            maximum=3,
            default=0,
            description=t("openaiSpec.previewDescription"),
        ),
    ]
    if gif:
        # ADR-0061 1章: GIF のときだけ使う(`conditional_params` で output_format に結び付ける)。
        params += [
            ParamDef(
                name="frame_count",
                type="int",
                label=t("openaiSpec.frameCountLabel"),
                minimum=GIF_FRAME_COUNT_MIN,
                maximum=GIF_FRAME_COUNT_MAX,
                default=9,
                description=t("openaiSpec.frameCountDescription"),
            ),
            ParamDef(
                name="frame_duration_ms",
                type="int",
                label=t("openaiSpec.frameDurationLabel"),
                minimum=20,
                maximum=2000,
                default=200,
                description=t("openaiSpec.frameDurationDescription"),
            ),
        ]
    if svg:
        # ADR-0062 2章: SVG のときだけ使う。API には `svg: {model, reasoning: {effort}}` で送る
        # (プロバイダーが組み立てる)。思考の量は未指定なら書き手のモデルの既定。
        params += [
            ParamDef(
                name="svg_model",
                type="enum",
                label=t("openaiSpec.svgModelLabel"),
                choices=SVG_AUTHOR_MODELS,
                choice_labels=SVG_AUTHOR_LABELS,
                default=SVG_AUTHOR_MODELS[0],
                form_default=SVG_AUTHOR_MODELS[0],
                description=t("openaiSpec.svgModelDescription"),
            ),
            ParamDef(
                name="svg_effort",
                type="enum",
                label=t("openaiSpec.svgEffortLabel"),
                choices=SVG_EFFORTS,
                choice_labels=_svg_effort_labels(),
                description=t("openaiSpec.svgEffortDescription"),
                widget="segmented",
            ),
        ]
    if _supports_complexity(model):
        # 描く前の計画の量。画質とは別の項目で、料金は変わらない。API の既定は high なので、
        # フォームの初期値は置かない(未指定のまま送ると high になる)。
        params.insert(
            1,
            ParamDef(
                name="complexity",
                type="enum",
                label=t("openaiSpec.complexityLabel"),
                choices=["low", "medium", "high"],
                choice_labels=_complexity_labels(),
                default="high",
                description=t("openaiSpec.complexityDescription"),
                widget="segmented",
            ),
        )
    return params


def _operation_capabilities(model: str, operation: str) -> OperationCapabilities:
    params = _common_params(model, operation)
    if operation == "generate":
        # `moderation`(表現の制限、Generate 専用)はフォームに出さない。設定 `MODERATION`
        # (既定 low)の値を、Run 作成時にサーバーが params へ入れる(api/runs.py の create_run)。
        return OperationCapabilities(
            operation="generate",
            params=params,
            max_input_images=0,
            min_input_images=0,
            supports_mask=False,
            # ADR-0053 5章: 入力画像の無い Run でも四角を使える。出力の大きさの白紙に四角を描いた
            # 配置図を唯一の入力にして、Edit の API で送る(Run は operation=edit として記録する。
            # run_create)。白紙には残す物・消す物・動かす物が無いので、種類は「変える」「足す」
            # だけ。参照画像は配置図の後ろに入力として足せる。
            supports_regions=True,
            regions_delivery="image_and_text",
            regions_kinds=["change", "add"],
            regions_reference_images=True,
            regions_requires_input_image=False,
        )

    # `input_fidelity` は載せない。2026-09-21 の実機確認で、gpt-image-2 / 2.5-sunburst /
    # 2.5-flare のいずれも 400 `invalid_input_fidelity_model` を返した(API リファレンスには
    # 記載があるが、対象は gpt-image-1 系)。Azure 側で使える場合は Azure アダプターの
    # capabilities で足す。
    return OperationCapabilities(
        operation="edit",
        params=params,
        max_input_images=MAX_INPUT_IMAGES,
        min_input_images=1,
        supports_mask=True,
        # スケッチの線と領域の四角を、1枚目に重ねる透過の印(markup)で送る(ADR-0010、ADR-0053。
        # 2026-11-05 追記)。
        supports_markup=model.startswith(("gpt-image-2.5", "gpt-image-2.6")),
        # ADR-0053 5章: 四角を描いた画像と、番号ごとの指示の文で渡す。2.5 / 2.6 は、四角を透過の
        # 印(markup)にして1枚目に重ねて送る(2026-11-05 追記。マスクのある Run と白紙の配置図は
        # Run の作成で「画像と文」に戻す)。
        supports_regions=True,
        regions_delivery=(
            "markup" if model.startswith(("gpt-image-2.5", "gpt-image-2.6")) else "image_and_text"
        ),
        # 文で伝えるので、どの種類も参照画像も使える。四角は主たる入力の上に描くので、入力画像が
        # 要る。
        regions_kinds=list(ALL_REGION_KINDS),
        regions_reference_images=True,
        regions_requires_input_image=True,
    )


def build_capabilities(provider_name: str, label: str | None = None) -> ProviderCapabilities:
    """provider 名(と表示名)だけ差し替えて、gpt-image-2.5 / 2.6 系の仕様をそのまま返す。

    `label` を省略した場合は `provider_name` をそのまま使う(テストの簡便のため)。
    実プロバイダー(OpenAIImagesProvider / FakeProvider)は自身の `label` を明示的に渡す。
    """
    models = [
        ModelCapabilities(
            model=model_id,
            label=label,
            description=description,
            quality_choices=_quality_choices(model_id),
            operations=[
                _operation_capabilities(model_id, "generate"),
                _operation_capabilities(model_id, "edit"),
            ],
        )
        for model_id, label, description in _model_info()
    ]

    size = SizeConstraints(
        multiple_of=sizes.MULTIPLE_OF,
        max_long_edge=sizes.MAX_LONG_EDGE,
        min_total_pixels=sizes.MIN_TOTAL_PIXELS,
        max_total_pixels=sizes.MAX_TOTAL_PIXELS,
        min_aspect_ratio=sizes.MIN_ASPECT_RATIO,
        max_aspect_ratio=sizes.MAX_ASPECT_RATIO,
        allow_auto=True,
    )

    incompatible_pairs = [
        IncompatiblePair(
            field_a="background",
            value_a="transparent",
            field_b="output_format",
            value_b="jpeg",
        ),
    ]
    conditional_params = [
        ConditionalParam(
            field="output_compression",
            depends_on_field="output_format",
            depends_on_values=["jpeg", "webp"],
        ),
        # ADR-0061 1章: GIF はストリーミングできないので、途中経過は静止画の形式のときだけ。
        # SVG(ADR-0062)も同じ。
        ConditionalParam(
            field="partial_images",
            depends_on_field="output_format",
            depends_on_values=["png", "jpeg", "webp"],
        ),
        # ADR-0062: SVG は1枚だけ(`n` は送らない。API の既定は1)。
        ConditionalParam(
            field="n",
            depends_on_field="output_format",
            depends_on_values=["png", "jpeg", "webp", "gif"],
        ),
        ConditionalParam(
            field="svg_model",
            depends_on_field="output_format",
            depends_on_values=["svg"],
        ),
        ConditionalParam(
            field="svg_effort",
            depends_on_field="output_format",
            depends_on_values=["svg"],
        ),
        ConditionalParam(
            field="frame_count",
            depends_on_field="output_format",
            depends_on_values=["gif"],
        ),
        ConditionalParam(
            field="frame_duration_ms",
            depends_on_field="output_format",
            depends_on_values=["gif"],
        ),
    ]
    gif_frame_size = SizeConstraints(
        multiple_of=sizes.MULTIPLE_OF,
        max_long_edge=GIF_FRAME_MAX_LONG_EDGE,
        min_total_pixels=GIF_FRAME_MIN_TOTAL_PIXELS,
        max_total_pixels=GIF_FRAME_MAX_TOTAL_PIXELS,
        min_aspect_ratio=sizes.MIN_ASPECT_RATIO,
        max_aspect_ratio=sizes.MAX_ASPECT_RATIO,
        allow_auto=True,
    )

    return ProviderCapabilities(
        provider=provider_name,
        label=label if label is not None else provider_name,
        models=models,
        default_model=DEFAULT_MODEL,
        default_size=DEFAULT_SIZE,
        size=size,
        gif_frame_size=gif_frame_size,
        incompatible_pairs=incompatible_pairs,
        conditional_params=conditional_params,
        prompt_max_length=PROMPT_MAX_LENGTH,
        n_min=1,
        n_max=10,
        partial_images_min=0,
        partial_images_max=3,
        max_input_image_bytes=MAX_INPUT_IMAGE_BYTES,
        max_mask_bytes=MAX_MASK_BYTES,
        max_input_images=MAX_INPUT_IMAGES,
    )
