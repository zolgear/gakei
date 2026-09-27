"""gpt-image-2.5 系 API 仕様(2026-09-21 に OpenAI API Reference で確認済み)。

`FakeProvider` と、ステップ2で実装する OpenAI 実プロバイダーの両方がこの定義を共有する。
実装時は Microsoft Learn / OpenAI の公式ドキュメントで再確認すること(CLAUDE.md 作業ルール)。

表示名(`label`/`choice_labels`)と初期値(`form_default`)は、フォームを一般的な日本語の
見出しで組み立てられるようにするためのもの。既定は「安価な設定」(モデル Flare、
サイズ 1024x1024、画質 low)。料金ページ(2026-09-22)では Sunburst と Flare の単価は同じ
(出力 $30 / 1M tok)なので、Flare を「安価」とは表記しない。`auto` サイズは結果が
安定しないため既定にはしない(選択肢としては残す)。
"""

from __future__ import annotations

from app.domain import sizes
from app.i18n import t
from app.providers.base import (
    ConditionalParam,
    IncompatiblePair,
    ModelCapabilities,
    OperationCapabilities,
    ParamDef,
    ProviderCapabilities,
    SizeConstraints,
)

# モデルID一覧(表示名・説明は言語ごとに変わるので `_model_info()` で都度組み立てる)。
_MODEL_IDS = ["gpt-image-2.5-sunburst", "gpt-image-2.5-flare", "gpt-image-2"]
MODELS = list(_MODEL_IDS)


def _model_info() -> list[tuple[str, str, str]]:
    """(モデルID, 表示名, 説明)。現在の言語で都度組み立てる(リクエストごとに変わるため)。"""
    return [
        (
            "gpt-image-2.5-sunburst",
            "GPT Image 2.5 Sunburst",
            t("openaiSpec.editingStrong"),
        ),
        ("gpt-image-2.5-flare", "GPT Image 2.5 Flare", t("openaiSpec.fast")),
        ("gpt-image-2", "GPT Image 2", ""),
    ]


DEFAULT_MODEL = "gpt-image-2.5-flare"
DEFAULT_SIZE = "1024x1024"

MAX_INPUT_IMAGES = 16
MAX_INPUT_IMAGE_BYTES = 50 * 1024 * 1024
MAX_MASK_BYTES = 4 * 1024 * 1024
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
    return {"png": "PNG", "jpeg": "JPEG", "webp": "WebP"}


def _background_labels() -> dict[str, str]:
    return {
        "auto": t("openaiSpec.backgroundAuto"),
        "opaque": t("openaiSpec.backgroundOpaque"),
        "transparent": t("openaiSpec.backgroundTransparent"),
    }


def _quality_choices(model: str) -> list[str]:
    if model.startswith("gpt-image-2.5"):
        return ["auto", "low", "medium", "high", "xhigh", "max"]
    return ["auto", "low", "medium", "high"]


def _common_params(model: str) -> list[ParamDef]:
    quality_choices = _quality_choices(model)
    return [
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
            choices=["png", "jpeg", "webp"],
            choice_labels=_output_format_labels(),
            default="png",
            form_default="png",
            description=t("openaiSpec.fileFormatDescription"),
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


def _operation_capabilities(model: str, operation: str) -> OperationCapabilities:
    params = _common_params(model)
    if operation == "generate":
        # `moderation`(表現の制限、Generate 専用)はフォームに出さない。設定 `MODERATION`
        # (既定 low)の値を、Run 作成時にサーバーが params へ入れる(api/runs.py の create_run)。
        return OperationCapabilities(
            operation="generate",
            params=params,
            max_input_images=0,
            min_input_images=0,
            supports_mask=False,
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
    )


def build_capabilities(provider_name: str, label: str | None = None) -> ProviderCapabilities:
    """provider 名(と表示名)だけ差し替えて、gpt-image-2.5 系の仕様をそのまま返す。

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
    ]

    return ProviderCapabilities(
        provider=provider_name,
        label=label if label is not None else provider_name,
        models=models,
        default_model=DEFAULT_MODEL,
        default_size=DEFAULT_SIZE,
        size=size,
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
