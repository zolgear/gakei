"""ADR-0005: プロバイダー抽象化。`ImageProvider` プロトコルを1つだけ定義する。

パラメータは共通化せず、`capabilities()` が返す定義から UI のフォームを組み立てる。
値は検証後 `run.params` にそのまま保存する(プロバイダー固有の項目を列にしない)。

ADR-0013 で 複数プロバイダーの土台に対応: `label`/`availability`/`finalize_params` を
プロトコルに追加し、`RunInputMeta`/`RunDraft`(Run 作成の検証・確定に使う値)と
`StepProgressEvent`(ステップ進捗、ComfyUI 等)をここに集約した。
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

ParamType = Literal["enum", "int", "float", "bool", "text"]
OperationName = Literal["generate", "edit"]


class ParamDef(BaseModel):
    """1つのパラメータの定義。UI のフォーム部品と、サーバー側検証の両方に使う。"""

    name: str
    type: ParamType
    label: str
    choices: list[str] | None = None
    choice_labels: dict[str, str] | None = None
    minimum: int | float | None = None
    maximum: int | float | None = None
    step: float | None = None  # float 用(UI のヒント。サーバーは検証しない)
    max_length: int | None = None  # text 用(サーバーで検証する)
    default: Any | None = None
    form_default: str | int | bool | None = None
    required: bool = False
    description: str = ""
    # UI に専用の入力欄を出す目印(seed: ランダム/固定の切り替えと乱数ボタン。ADR-0013)。
    widget: Literal["seed"] | None = None


class IncompatiblePair(BaseModel):
    """同時に指定できないパラメータの組み合わせ。
    例: background=transparent と output_format=jpeg。
    """

    field_a: str
    value_a: str
    field_b: str
    value_b: str


class ConditionalParam(BaseModel):
    """特定のフィールドが特定の値のときだけ有効になるパラメータ(例: output_compression)。"""

    field: str
    depends_on_field: str
    depends_on_values: list[str]


class OperationCapabilities(BaseModel):
    """generate / edit それぞれのパラメータ定義。"""

    operation: OperationName
    params: list[ParamDef]
    max_input_images: int = 0
    # edit に必要な image 入力の最小枚数。ComfyUI は画像の枠(スロット)の数が
    # そのまま最小=最大になる(ADR-0013 3節: 枠はすべて必須)。
    min_input_images: int = 1
    supports_mask: bool = False
    # mask が無いと実行できない(ComfyUI の inpaint ワークフローなど)。
    requires_mask: bool = False


class ModelCapabilities(BaseModel):
    model: str
    label: str
    description: str = ""
    quality_choices: list[str]
    operations: list[OperationCapabilities]


class SizeConstraints(BaseModel):
    multiple_of: int
    max_long_edge: int
    min_total_pixels: int
    max_total_pixels: int
    min_aspect_ratio: float
    max_aspect_ratio: float
    allow_auto: bool = True


class ProviderCapabilities(BaseModel):
    """`GET /api/capabilities` の `providers[]` の1件がそのまま返す形。"""

    provider: str
    label: str  # 表示名("OpenAI", "Fake", "ComfyUI")
    models: list[ModelCapabilities]
    default_model: str
    default_size: str | None = None
    size: SizeConstraints | None = None  # None のプロバイダーは size パラメータを取らない
    incompatible_pairs: list[IncompatiblePair] = Field(default_factory=list)
    conditional_params: list[ConditionalParam] = Field(default_factory=list)
    prompt_max_length: int = 32_000
    n_min: int = 1
    n_max: int = 10
    partial_images_min: int = 0
    partial_images_max: int = 3
    max_input_image_bytes: int = 50 * 1024 * 1024
    max_mask_bytes: int = 4 * 1024 * 1024
    max_input_images: int = 16


@dataclass
class RunInputMeta:
    """検証に必要な入力 Asset の情報(`app/domain/run_validation.py` が再エクスポートする)。"""

    asset_id: uuid.UUID
    role: str
    position: int
    width: int
    height: int
    sha256: str
    mime: str


@dataclass
class RunDraft:
    """検証済みの Run 作成要求。`ImageProvider.finalize_params` への入力。"""

    operation: OperationName
    model: str
    prompt: str
    params: dict[str, Any]
    inputs: list[RunInputMeta]


class InputImage(BaseModel):
    """execute に渡す入力画像の実体。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    role: Literal["image", "mask"]
    position: int
    data: bytes
    mime: str


class RunRequest(BaseModel):
    """`ImageProvider.execute` への入力。値は API 層で検証済みの前提。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    run_id: uuid.UUID
    operation: OperationName
    model: str
    prompt: str
    params: dict[str, Any] = Field(default_factory=dict)
    inputs: list[InputImage] = Field(default_factory=list)


class RunOutputImage(BaseModel):
    """execute が返す1枚の出力画像。base64 は使わずバイト列のまま扱う。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    data: bytes
    mime: str


class RunResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    outputs: list[RunOutputImage]
    usage: dict[str, Any] | None = None
    provider_request_id: str | None = None
    # 実行時に作られたテキスト(ADR-0030。ComfyUI の最終プロンプトと、SD WebUI の出力ごとの
    # 展開後のプロンプト。ADR-0038 7章)。
    # `run.text_outputs` にそのまま書く。無ければ None。
    text_outputs: list[dict[str, Any]] | None = None


class PartialImageEvent(BaseModel):
    """途中経過画像の通知(`partial_images > 0` のとき)。Asset にはしない。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    output_index: int
    partial_index: int
    data: bytes
    mime: str = "image/png"


class StepProgressEvent(BaseModel):
    """ステップの進捗(value / max)。プレビュー画像は伴わない(ComfyUI 等)。"""

    value: int
    max: int
    node: str | None = None


ProgressEvent = PartialImageEvent | StepProgressEvent
ProgressCallback = Callable[[ProgressEvent], Awaitable[None]]


class ProviderError(Exception):
    """プロバイダー呼び出しの失敗。`run.error_code` にそのまま格納する。"""

    def __init__(self, code: str, message: str, request_id: str | None = None) -> None:
        self.code = code
        self.message = message
        self.request_id = request_id
        super().__init__(message)


class ProviderUnavailableError(Exception):
    """プロバイダーが今使えない(接続できない等)。API 層で 409 にする。"""


class ImageProvider(Protocol):
    """ADR-0005 / ADR-0013 のプロバイダー抽象化。"""

    name: str
    label: str
    # 画面の設定(/api/settings/openai-key)と Run 作成時の事前チェック(ADR-0012)に使う。
    # 実 API を呼ぶプロバイダーは True、FakeProvider は False。
    requires_api_key: bool
    # 参考価格(app/domain/pricing.py)を表示してよいか。
    supports_pricing: bool

    def capabilities(self) -> ProviderCapabilities: ...

    def availability(self) -> tuple[bool, str | None]:
        """(使えるか, 使えない理由)。呼び出しのたびに評価する(キャッシュは実装側の責任)。"""
        ...

    def finalize_params(self, db: Session, draft: RunDraft) -> dict[str, Any]:
        """検証の後、INSERT の前に呼ぶ。戻り値がそのまま `run.params` になる。

        `RunValidationError`(422)と `ProviderUnavailableError`(409)を投げてよい。
        """
        ...

    async def execute(self, run: RunRequest, on_progress: ProgressCallback) -> RunResult: ...
