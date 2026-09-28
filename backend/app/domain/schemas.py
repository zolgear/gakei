"""API の入出力に使う Pydantic モデル。OpenAPI から型が出るよう、レスポンスは必ずこれを通す。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.domain.comfy_workflow import Bindings, ExposedParam, SuggestedBindings
from app.providers.base import ProviderCapabilities

# -- Capabilities (ADR-0013: 複数プロバイダー) -------------------------------


class ProviderEntry(ProviderCapabilities):
    """`GET /api/capabilities` の `providers[]` の1件。"""

    available: bool
    unavailable_reason: str | None = None
    requires_api_key: bool
    supports_pricing: bool


class CapabilitiesResponse(BaseModel):
    default_provider: str
    providers: list[ProviderEntry] = Field(default_factory=list)


# -- Auth (ADR-0019) ------------------------------------------------------
# `none`(個人)モードと `oidc` モードの両方で同じ形を返す。


class AuthUser(BaseModel):
    id: uuid.UUID
    name: str | None
    email: str | None
    role: Literal["user", "admin"]
    # `/api/users/{id}/avatar?v=...`(ADR-0020)。アバター未設定なら null。
    avatar_url: str | None = None


class AuthMeResponse(BaseModel):
    """`GET /api/auth/me`。`none` モードは常に `user: null`
    (フロントは `isAdmin = mode === 'none' || user?.role === 'admin'` で判定する)。
    """

    mode: Literal["none", "oidc"]
    user: AuthUser | None = None


class AuthLogoutResponse(BaseModel):
    """`POST /api/auth/logout`。SPA はこの URL に `window.location.assign()` で遷移する。"""

    redirect_url: str


# -- Users / avatar (ADR-0020) ---------------------------------------------
# oidc モードだけの機能。アバターは Asset にしない(証跡ではないので更新・物理削除ができる)。


class CropRect(BaseModel):
    """画像の切り出し範囲(ADR-0020 5章)。元画像(EXIF反映後)のピクセル座標。
    サーバー側で切り出す前に、画像の外にはみ出していないか・空でないかを検証する
    (`domain/avatars.crop_image`)。"""

    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(ge=1)
    height: int = Field(ge=1)


class AvatarFromAssetRequest(BaseModel):
    """`POST /api/users/me/avatar/from-asset`。既存の Asset(削除済みでないもの)を
    その時点の原本から複製してアバターにする。`crop` は原本のピクセル座標(省略時は中央の正方形)。"""

    asset_id: uuid.UUID
    crop: CropRect | None = None


class CreatedBy(BaseModel):
    """実行者・アップロード者(ADR-0019)。`none` モードでは付かない(`created_by: null`)。"""

    id: uuid.UUID
    name: str | None
    email: str | None
    # `/api/users/{id}/avatar?v=...`(ADR-0020)。アバター未設定なら null。
    avatar_url: str | None = None


# -- Asset groups (ADR-0022) -----------------------------------------------
# ストックの手動整理。証跡ではないので更新・論理削除ができる。名前は前後の空白を
# 除いて1〜100文字(同名は禁止しない)。


def _normalize_group_name(value: str) -> str:
    stripped = value.strip()
    if not (1 <= len(stripped) <= 100):
        raise ValueError("name must be 1-100 characters after stripping whitespace")
    return stripped


class AssetGroupCreate(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return _normalize_group_name(value)


class AssetGroupUpdate(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return _normalize_group_name(value)


class AssetGroupRow(BaseModel):
    """`GET /api/asset-groups` の1件。`member_count` / `cover_asset_id` は
    削除済みでない Asset だけを数える(cover は `added_at` が最新のメンバー)。
    `position` は利用者が決める並び順(小さいほど上)。"""

    id: uuid.UUID
    name: str
    position: int
    member_count: int
    cover_asset_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime


class AssetGroupListResponse(BaseModel):
    items: list[AssetGroupRow] = Field(default_factory=list)


class AssetGroupOrderRequest(BaseModel):
    """`PUT /api/asset-groups/order` の本文。削除済みでない全グループの id を望む順に並べたもの
    (過不足・重複があれば 422)。"""

    group_ids: list[uuid.UUID]


class AssetGroupMembersRequest(BaseModel):
    """`.../assets` と `.../assets/remove` の共通の本文。1〜200件。"""

    asset_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)


class AssetGroupRef(BaseModel):
    """`AssetDetail.group` と `RunSummary.asset_group` の1件。"""

    id: uuid.UUID
    name: str


# -- Asset ---------------------------------------------------------------


class AssetSummary(BaseModel):
    id: uuid.UUID
    kind: Literal["upload", "generated", "mask", "sketch"]
    mime: str
    width: int
    height: int
    bytes: int
    created_at: datetime
    # タイトル(ADR-0024)。人が付けたものか自動のものかは `AssetDetail.title_source` を見る。
    title: str | None = None


class AssetTagRef(BaseModel):
    """Asset に付いたタグ(ADR-0024 2章)。人が消したもの(removed)は含めない。"""

    name: str
    source: Literal["auto", "user"]


class AnnotationStatusView(BaseModel):
    """自動推定の状態(ADR-0024 4章)。一度も推定していなければ `AssetDetail.annotation` は null。"""

    status: Literal["queued", "running", "succeeded", "failed"]
    error: str | None = None
    requested_at: datetime | None = None
    finished_at: datetime | None = None


class AssetAnnotationResponse(BaseModel):
    """タイトル・タグの編集と再推定の応答(更新後の注釈)。"""

    asset_id: uuid.UUID
    title: str | None = None
    title_source: Literal["auto", "user"] | None = None
    tags: list[AssetTagRef] = Field(default_factory=list)
    annotation: AnnotationStatusView | None = None


class AssetTitleUpdateRequest(BaseModel):
    """`PATCH /api/assets/{id}/title`。null・空文字はタイトルを消す(以後も自動で付けない)。"""

    title: str | None = None


class AssetTagAddRequest(BaseModel):
    """`POST /api/assets/{id}/tags`。名前は正規化して保存する(NFKC、小文字化など)。"""

    name: str


class TagCount(BaseModel):
    name: str
    count: int


class TagListResponse(BaseModel):
    items: list[TagCount] = Field(default_factory=list)


class ProducedByRunSummary(BaseModel):
    id: uuid.UUID
    operation: Literal["generate", "edit"]
    model: str
    status: Literal["queued", "running", "succeeded", "failed", "canceled"]
    prompt: str


class AssetOrigin(BaseModel):
    """埋め込まれていた `gakei` メタ情報の由来(ADR-0014、2026-09-24 追記)。署名が無いため
    `verified` は常に false(「未検証」)。`origin_meta` を持つ Asset にのみ付く。
    """

    # 同じインスタンスの既存 Asset を指していた場合のみ(内容は一致しなかった)。
    asset_id: uuid.UUID | None = None
    # meta["instance"] がこの GAKEI インスタンスの id と一致するか。
    same_instance: bool
    # 読み取った `gakei.lineage/1` の JSON をそのまま返す(自己申告)。
    meta: dict[str, Any]
    verified: Literal[False] = False


class EmbeddedGenerationMeta(BaseModel):
    """他の画像生成ツールや C2PA が画像に埋め込んだ生成メタ情報(ADR-0018、2026-09-26 追記)。
    保存している JSON(`gakei.embedded/1`)から `schema` キーを除いたもの。署名が無いため
    `verified` は常に false(「未検証」)。kind=upload で何か読み取れた Asset にのみ付く。
    """

    tool: Literal["a1111", "comfyui", "novelai", "invokeai", "swarmui", "c2pa", "generic"]
    software: str | None = None
    prompt: str | None = None
    negative_prompt: str | None = None
    model: str | None = None
    seed: int | str | None = None
    params: dict[str, str | int | float | bool] = Field(default_factory=dict)
    raw: dict[str, str] = Field(default_factory=dict)
    truncated: bool = False
    verified: Literal[False] = False


class AssetDetail(AssetSummary):
    sha256: str
    output_index: int | None
    produced_by_run: ProducedByRunSummary | None = None
    # 論理削除されていても200で返す。削除済みなら日時が入る(ADR-0008「削除」追加分)。
    deleted_at: datetime | None = None
    # 削除済みで、かつ生んだRunが削除済みでなければ true(ADR-0008「Assetの復元」)。
    # 未削除なら常に false。フロントの復元ボタンの出し分けに使う。
    restorable: bool = False
    # 上描きスケッチの下地 Asset の id(ADR-0010、2026-09-23 追記)。kind=sketch 以外は常に null。
    source_asset_id: uuid.UUID | None = None
    # 埋め込まれていたメタ情報の由来(ADR-0014、2026-09-24 追記)。origin_meta が無ければ null。
    origin: AssetOrigin | None = None
    # 他の画像生成ツールや C2PA が埋め込んだ生成メタ情報(ADR-0018、2026-09-26 追記)。
    # 読み取れなかった・kind=upload 以外の Asset では null。
    embedded_meta: EmbeddedGenerationMeta | None = None
    # その Asset を入力にした run_input が1行でもあるか(Run の状態は問わない)。
    # 未使用スケッチの再編集の可否をフロントが判断するのに使う(ADR-0010、2026-09-25 追記)。
    used_as_input: bool = False
    # アップロード・マスク・スケッチを行った、または(生成出力なら)実行した Run と同じ
    # ユーザー(ADR-0019。runner が `run.created_by_user_id` をそのまま引き継ぐ)。
    # `none` モードは常に null。
    created_by: CreatedBy | None = None
    # 所属しているグループ(ADR-0022)。1 つの Asset が属するグループは 1 つだけ
    # (2026-09-28 に `groups: []` から変更)。未所属、または削除済みグループにだけ残っている
    # ときは null。`AssetSummary` には足さない(一覧が重くなるため)。
    group: AssetGroupRef | None = None
    # ADR-0024: タイトルの出所、タグ(removed を除く)、自動推定の状態。
    title_source: Literal["auto", "user"] | None = None
    tags: list[AssetTagRef] = Field(default_factory=list)
    annotation: AnnotationStatusView | None = None


class AssetUploadResponse(AssetDetail):
    """`POST /api/assets` の応答(ADR-0014、2026-09-24 追記)。"""

    ingest_outcome: Literal["created", "matched_existing"]


class AssetListResponse(BaseModel):
    items: list[AssetSummary]
    next_cursor: str | None = None


# -- Run -------------------------------------------------------------------


class RunInputCreate(BaseModel):
    asset_id: uuid.UUID
    role: Literal["image", "mask", "reference"]
    position: int = Field(ge=0)


class RunCreateRequest(BaseModel):
    operation: Literal["generate", "edit"]
    model: str
    prompt: str
    # 省略時は主プロバイダー(OpenAI。FAKE_PROVIDER=1 のときは fake)を使う(ADR-0013、ADR-0017)。
    provider: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    inputs: list[RunInputCreate] = Field(default_factory=list)
    # 出力を入れるグループ(ADR-0022)。存在しない・削除済みなら 404。
    asset_group_id: uuid.UUID | None = None


class RunCreateResponse(BaseModel):
    id: uuid.UUID
    status: Literal["queued", "running", "succeeded", "failed", "canceled"]


class RunInputRef(BaseModel):
    asset_id: uuid.UUID
    role: Literal["image", "mask", "reference"]
    position: int


class RunOutputRef(BaseModel):
    asset_id: uuid.UUID
    output_index: int | None
    # 出力 Asset のタイトル(ADR-0024。履歴のカード用)。
    title: str | None = None


class RunSummary(BaseModel):
    """履歴カード用。`params`/`usage`/`error_message` を含み、Edit の主たる親も分かる形。"""

    id: uuid.UUID
    # 実行したプロバイダー(レジストリの名前)。再実行でフォームの provider を戻すのに使う(ADR-0013)。
    provider: str
    operation: Literal["generate", "edit"]
    model: str
    # ComfyUI のワークフロー名など、model だけでは分からない表示名(ADR-0013)。
    # `run.params["comfyui_workflow"]["name"]` があればそれ、無ければ None。
    model_label: str | None = None
    status: Literal["queued", "running", "succeeded", "failed", "canceled"]
    prompt: str
    params: dict[str, Any] = Field(default_factory=dict)
    usage: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    queued_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    outputs: list[RunOutputRef] = Field(default_factory=list)
    # role=image かつ position=0 の入力(「主たる親」)。generate や入力なしの edit は None。
    primary_parent_asset_id: uuid.UUID | None = None
    # role=image の入力枚数(mask/reference は含まない)。
    input_count: int = 0
    # この Run の出力を入力に使っている、削除されていない Run の数。削除の警告に使う。
    descendant_run_count: int = 0
    # 論理削除されていても200で返す。削除済みなら日時が入る(ADR-0008「削除」追加分)。
    deleted_at: datetime | None = None
    # 実コスト(参考)。usage × 単価(app/domain/pricing.py の cost_from_usage)。
    # usage 無し(未実行・失敗)・単価不明なモデルは None。請求額ではない(ADR-0009「参考価格」節)。
    cost_usd: float | None = None
    # 実行したユーザー(ADR-0019)。`none` モードは常に null。
    created_by: CreatedBy | None = None
    # 生成時に指定したグループ(ADR-0022)。指定なし・削除済みのグループなら null。
    # 再実行でフォームに戻すためと、履歴・Run 詳細の表示に使う。
    asset_group: AssetGroupRef | None = None
    # 実行元(ADR-0023 5章)。null は画面、`mcp` は MCP のツールから作った Run。
    origin: str | None = None


class RunDetail(RunSummary):
    deployment: str | None = None
    provider_request_id: str | None = None
    inputs: list[RunInputRef] = Field(default_factory=list)


class RunListResponse(BaseModel):
    items: list[RunSummary]
    next_cursor: str | None = None


class RunCancelResponse(BaseModel):
    id: uuid.UUID
    status: Literal["canceled"]


# -- SSE ---------------------------------------------------------------


class RunEvent(BaseModel):
    """`GET /api/runs/{id}/events` が1件ごとに送る `data:` 行の JSON の形。

    status/partial の両方をこの1モデルで表す(該当しないフィールドは省略/null)。
    実際のレスポンス本体は text/event-stream のままで、これは型生成用の schema。
    """

    type: Literal["status", "partial", "progress"]
    status: Literal["queued", "running", "succeeded", "failed", "canceled"] | None = None
    error_code: str | None = None
    error_message: str | None = None
    output_asset_ids: list[uuid.UUID] | None = None
    index: int | None = None
    output_index: int | None = None
    partial_index: int | None = None
    # type=progress のみ(ADR-0013: ステップ進捗、ComfyUI 等)。
    value: int | None = None
    max: int | None = None
    node: str | None = None


# -- Lineage (ADR-0009) ------------------------------------------------


class LineageAssetInfo(BaseModel):
    # 埋め込み(未検証)ノードは kind/width/height/mime が分からないことがある
    # (ADR-0014 6章。`gakei.lineage/1` から変換した入力 Asset など)。
    kind: Literal["upload", "generated", "mask", "sketch"] | None = None
    width: int | None = None
    height: int | None = None
    mime: str | None = None
    restorable: bool = False


class LineageRunInfo(BaseModel):
    operation: Literal["generate", "edit"]
    model: str
    # 表示用のモデル名。ComfyUI の Run は `model` がワークフローの id なので、その名前を入れる
    # (ADR-0013)。それ以外は None で、画面は `model` を使う。
    model_label: str | None = None
    # 埋め込み(未検証)ノードにのみ付く(ADR-0014 6章)。ローカルの Run は常に null のまま。
    provider: str | None = None
    status: Literal["queued", "running", "succeeded", "failed", "canceled"]
    prompt: str
    error_code: str | None = None
    # 埋め込み(未検証)ノードには無いので null(ADR-0014 6章)。
    queued_at: datetime | None = None


class LineageNode(BaseModel):
    """asset / run のどちらかを表す1ノード。`depth` は起点=0、祖先は負、子孫は正。

    `embedded=true` は、この環境の DB 行ではなく、祖先の画像に埋め込まれていた
    `gakei` メタ情報(自己申告・未検証)から組み立てたノードであることを表す
    (ADR-0014 6章)。`instance` はそのノードを生んだ GAKEI インスタンスの id
    (埋め込みノードのみ持つ)。
    """

    id: uuid.UUID
    type: Literal["asset", "run"]
    depth: int
    deleted: bool = False
    embedded: bool = False
    instance: str | None = None
    # 埋め込み(未検証)の asset ノードのうち、`origin_ref_asset_id` で対応付けられた
    # 取り込み済みのローカル Asset があれば、その id(ADR-0014 6章)。
    resolved_asset_id: uuid.UUID | None = None
    # 埋め込み(未検証)ノードの正規化後の生データ(prompt/params を省略せずに持つ)。
    # インスペクターでの全文表示に使う(ADR-0014 6章)。
    embedded_detail: dict[str, Any] | None = None
    asset: LineageAssetInfo | None = None
    run: LineageRunInfo | None = None


class LineageEdge(BaseModel):
    """input は Asset→Run、output は Run→Asset、sketch_source は下地 Asset→上描き Asset
    (ADR-0010、2026-09-23 追記。Run を介さない直接の来歴)。origin は、再アップロードで
    Asset に一致しなかった埋め込みメタ情報の由来 Asset→新しい Asset(ADR-0014、
    2026-09-24 追記。署名の無い自己申告で、`primary` は常に false)。
    """

    source: uuid.UUID
    target: uuid.UUID
    kind: Literal["input", "output", "sketch_source", "origin"]
    role: Literal["image", "mask", "reference"] | None = None
    position: int | None = None
    output_index: int | None = None
    primary: bool = False


class AssetLineageResponse(BaseModel):
    root_asset_id: uuid.UUID
    nodes: list[LineageNode] = Field(default_factory=list)
    edges: list[LineageEdge] = Field(default_factory=list)
    truncated: bool = False


# -- Prompt sets (ADR-0009) ----------------------------------------------
# 証跡ではないので更新・論理削除ができる。Run との外部キーは張らない。


class PromptSetItemCreate(BaseModel):
    label: str | None = Field(default=None, max_length=100)
    text: str = Field(min_length=1, max_length=32_000)


class PromptSetCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    items: list[PromptSetItemCreate] = Field(default_factory=list)


class PromptSetUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class PromptSetItemAppendRequest(BaseModel):
    label: str | None = Field(default=None, max_length=100)
    text: str = Field(min_length=1, max_length=32_000)


class PromptSetItemUpdateRequest(BaseModel):
    label: str | None = Field(default=None, max_length=100)
    text: str | None = Field(default=None, min_length=1, max_length=32_000)
    position: int | None = Field(default=None, ge=0)


class PromptSetItemResponse(BaseModel):
    id: uuid.UUID
    label: str | None
    text: str
    position: int
    created_at: datetime
    updated_at: datetime


class PromptSetResponse(BaseModel):
    id: uuid.UUID
    name: str
    created_at: datetime
    updated_at: datetime
    items: list[PromptSetItemResponse] = Field(default_factory=list)


class PromptSetListResponse(BaseModel):
    items: list[PromptSetResponse] = Field(default_factory=list)


# -- Global search (ADR-0009 6章) ----------------------------------------
# テキストの部分一致のみ。FTS・形態素解析なし。削除済みは除外。
# 将来 embedding 検索を足すときも、この API の形はそのまま使う想定。


class SearchRunHit(RunSummary):
    """`RunSummary` と同じ項目 + 一致箇所の抜粋。"""

    snippet: str


class SearchAssetHit(AssetSummary):
    """`AssetSummary` と同じ項目 + 一致箇所の抜粋。

    一致は Run の `prompt`(kind=generated)か、画像に埋め込まれた生成情報
    (`asset.embedded_meta`、ADR-0018、2026-09-27 追記)のどちらか。
    """

    # Run 由来の一致でなければ null(embedded 由来、または produced_by_run_id を持たない Asset)。
    produced_by_run_id: uuid.UUID | None
    # 一致が Run の prompt 由来("run")か、埋め込まれた生成情報由来("embedded"、未検証)か。
    prompt_source: Literal["run", "embedded", "title", "tag"]
    prompt_snippet: str


class SearchPromptSetItemHit(BaseModel):
    id: uuid.UUID
    label: str | None
    snippet: str


class SearchPromptSetHit(BaseModel):
    id: uuid.UUID
    name: str
    # 名前だけの一致なら空(本文が一致した項目だけを載せる)。
    matched_items: list[SearchPromptSetItemHit] = Field(default_factory=list)


class SearchTruncated(BaseModel):
    runs: bool = False
    assets: bool = False
    prompt_sets: bool = False


class SearchResponse(BaseModel):
    query: str
    runs: list[SearchRunHit] = Field(default_factory=list)
    assets: list[SearchAssetHit] = Field(default_factory=list)
    prompt_sets: list[SearchPromptSetHit] = Field(default_factory=list)
    truncated: SearchTruncated = Field(default_factory=SearchTruncated)


# -- Settings (ADR-0012 Decision 4) --------------------------------------
# 画面から OpenAI の API キーを設定する。キーの全文は返さない(末尾4文字のみ)。


class OpenAIKeyStatusResponse(BaseModel):
    # 有効なプロバイダーがキーを必要とするか(fake は false)。
    required: bool
    configured: bool
    source: Literal["env", "file"] | None = None
    # 例: "…abcd"。未設定なら null。
    hint: str | None = None


class OpenAIKeyUpdateRequest(BaseModel):
    api_key: str


# -- Settings (ADR-0017) --------------------------------------------------
# 画面から OpenAI の接続先(Base URL)を設定する。値は秘密ではないので全文を返す。


class OpenAIBaseUrlStatusResponse(BaseModel):
    # 未設定(OpenAI 本体)なら null。
    value: str | None = None
    source: Literal["env", "file"] | None = None


class OpenAIBaseUrlUpdateRequest(BaseModel):
    base_url: str


# -- General settings (ADR-0009、ADR-0013 7章) -------------------------------
# moderation(Generate 専用)と ComfyUI のタイムアウトを画面から変える。優先順位は
# 画面で保存した値(`app_setting`) > 環境変数 > 組み込みの既定値。


class ModerationSetting(BaseModel):
    value: Literal["auto", "low"]
    source: Literal["setting", "env", "default"]
    # 「既定値に戻す」を押したときに戻る値(環境変数があればその値、無ければ組み込みの low)。
    default: Literal["auto", "low"]


class ComfyUITimeoutSetting(BaseModel):
    value: int
    source: Literal["setting", "env", "default"]
    default: int


class GeneralSettingsResponse(BaseModel):
    moderation: ModerationSetting
    comfyui_timeout_seconds: ComfyUITimeoutSetting


class GeneralSettingsUpdateRequest(BaseModel):
    """`PATCH /api/settings/general` の本文。項目を省略すると変更せず、明示的な `null` は
    保存済みの値を削除して環境変数・既定値に戻す(`model_fields_set` で区別する)。

    値の妥当性(moderation は auto/low のみ、タイムアウトは60〜10800秒)は
    `app/domain/general_settings.py` が検証し、i18n 対応のメッセージで 422 にする。
    そのため、ここでは型を絞り過ぎない。
    """

    moderation: str | None = None
    comfyui_timeout_seconds: int | None = None


# -- ComfyUI (ADR-0013) ----------------------------------------------------
# ワークフローは証跡ではないので更新・論理削除ができる。`run` との外部キーは張らない。


class ComfyUIStatusResponse(BaseModel):
    url: str | None
    enabled: bool
    available: bool
    reason: str | None = None
    version: str | None = None
    device: str | None = None
    # ADR-0013 7章: 接続設定の出所。setting = 画面で保存(切り離しの明示も含む)、
    # env = COMFYUI_URL を既定値としてだけ使っている、none = どちらも無い(無効)。
    source: Literal["setting", "env", "none"]
    # true の間は接続設定を変更できない(ComfyUI の Run が queued/running)。
    locked: bool
    # url が None のときは null。
    loopback: bool | None = None


class ComfyUIConnectionTestRequest(BaseModel):
    """接続テスト(`POST /api/comfyui/connection/test`)。省略時は現在の有効な URL を使う。"""

    url: str | None = None


class ComfyUIConnectionTestResponse(BaseModel):
    url: str
    available: bool
    reason: str | None = None
    version: str | None = None
    device: str | None = None
    loopback: bool


class ComfyUIConnectionRequest(BaseModel):
    """接続・変更(`PUT /api/comfyui/connection`)。"""

    url: str
    # ループバック以外の URL を保存するための、画面での確認チェック。
    allow_non_loopback: bool = False


class ComfyNodeInputInfo(BaseModel):
    name: str
    value: Any = None
    linked: bool


class ComfyNodeInfo(BaseModel):
    id: str
    class_type: str | None = None
    title: str | None = None
    inputs: list[ComfyNodeInputInfo] = Field(default_factory=list)


class ComfyAnalyzeRequest(BaseModel):
    template: dict[str, Any]


class ComfyAnalyzeResponse(BaseModel):
    nodes: list[ComfyNodeInfo] = Field(default_factory=list)
    # 見つからなかった必須項目(prompt、outputs)があっても、見つかったものは提案として
    # 返す(null にしない。ADR-0013 フォローアップ)。欠けている必須項目は warnings に書く。
    suggested_bindings: SuggestedBindings
    suggested_operation: Literal["generate", "edit"]
    candidate_params: list[ExposedParam] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ComfyWorkflowCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    operation: Literal["generate", "edit"]
    template: dict[str, Any]
    bindings: Bindings
    exposed_params: list[ExposedParam] = Field(default_factory=list)


class ComfyWorkflowUpdateRequest(BaseModel):
    """全項目 Optional(PATCH)。渡された項目だけを更新するが、検証は更新後の全体で行う。"""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    operation: Literal["generate", "edit"] | None = None
    template: dict[str, Any] | None = None
    bindings: Bindings | None = None
    exposed_params: list[ExposedParam] | None = None


class ComfyWorkflowSummary(BaseModel):
    id: uuid.UUID
    name: str
    operation: Literal["generate", "edit"]
    template_sha256: str
    created_at: datetime
    updated_at: datetime


class ComfyWorkflowDetail(ComfyWorkflowSummary):
    template: dict[str, Any]
    bindings: Bindings
    exposed_params: list[ExposedParam] = Field(default_factory=list)


class ComfyWorkflowListResponse(BaseModel):
    items: list[ComfyWorkflowSummary] = Field(default_factory=list)


# -- About (ADR-0021 3章) ---------------------------------------------------


class AboutResponse(BaseModel):
    """`GET /api/about`。バージョンの正は `backend/pyproject.toml`(`app.version.get_version`)。"""

    version: str
    commit: str | None = None


# -- MCP サーバー(ADR-0023) --------------------------------------------------


class McpSettingsResponse(BaseModel):
    """`GET /api/settings/mcp`。有効/無効と、MCP 経由の Run の上限(1時間あたり)。"""

    enabled: bool
    hourly_run_limit: int
    hourly_run_limit_default: int
    hourly_run_limit_max: int
    # 直近1時間に MCP から作った Run の件数(上限との比較の参考)。
    runs_last_hour: int
    # エージェントに登録する接続先(`PUBLIC_BASE_URL` があればそれ、無ければリクエストの URL
    # から組み立てる)。
    endpoint_url: str


class McpSettingsUpdateRequest(BaseModel):
    """`PATCH /api/settings/mcp` の本文。省略した項目は変更しない。

    値の妥当性は `app/domain/mcp_settings.py` が検証し、i18n 対応のメッセージで 422 にする。
    """

    enabled: bool | None = None
    hourly_run_limit: int | None = None


class ApiTokenRow(BaseModel):
    """アクセストークンの1件。値そのものは発行時の応答(`ApiTokenCreateResponse`)にだけ載る。"""

    id: uuid.UUID
    name: str
    created_at: datetime
    last_used_at: datetime | None = None


class ApiTokenListResponse(BaseModel):
    items: list[ApiTokenRow] = Field(default_factory=list)


class ApiTokenCreateRequest(BaseModel):
    """名前の妥当性(空でない、100文字以内)は `app/domain/api_tokens.py` が検証する。"""

    name: str


class ApiTokenCreateResponse(ApiTokenRow):
    # 発行したトークンの値。この応答でだけ返し、以後は取り出せない。
    token: str


# -- 自動タイトル・タグ(ADR-0024) ----------------------------------------------


class OnnxModelStatus(BaseModel):
    """ONNX タガーのモデル1つの状態。"""

    name: Literal["wd-vit-tagger-v3", "wd-swinv2-tagger-v3", "wd-eva02-large-tagger-v3"]
    # 取得するファイル(model.onnx と selected_tags.csv)の合計の大きさ。
    size_bytes: int
    downloaded: bool
    download_status: Literal["idle", "downloading", "failed"]
    # 0〜1。ダウンロード中だけ値が入る。
    download_progress: float | None = None
    download_error: str | None = None


class AnnotationSettingsResponse(BaseModel):
    """`GET /api/settings/annotation`。"""

    auto_on_ingest: bool
    llm_enabled: bool
    llm_model: str
    vlm_enabled: bool
    vlm_model: str
    # 推定専用の接続先。null なら OpenAI の設定(キー、Base URL)を流用する。
    base_url: str | None = None
    api_style: Literal["responses", "chat"]
    language: Literal["ja", "en"]
    hourly_limit: int
    onnx_enabled: bool
    onnx_model: Literal["wd-vit-tagger-v3", "wd-swinv2-tagger-v3", "wd-eva02-large-tagger-v3"]
    onnx_threshold: float
    # 推定専用の API キーを保存しているか(値は返さない)。
    api_key_set: bool
    onnx_models: list[OnnxModelStatus] = Field(default_factory=list)
    # 一度も推定していない Asset(削除済み・マスクを除く)の件数。一括実行の対象。
    pending_count: int
    # 待ち行列にある件数(queued と running)。
    queued_count: int
    # 直近1時間の LLM・VLM の呼び出し回数(プロセス内で数える。再起動で 0 に戻る)。
    calls_last_hour: int
    # いま使えるエンジン(有効かつ、ONNX はモデルをダウンロード済み)。空なら推定できない。
    usable_engines: list[Literal["llm", "vlm", "onnx"]] = Field(default_factory=list)


class AnnotationSettingsUpdateRequest(BaseModel):
    """`PATCH /api/settings/annotation`。省略した項目は変更しない。`base_url` は null か空文字で
    「OpenAI の設定を流用」に戻す。値の妥当性は `app/domain/annotation_settings.py` が検証する。
    """

    auto_on_ingest: bool | None = None
    llm_enabled: bool | None = None
    llm_model: str | None = None
    vlm_enabled: bool | None = None
    vlm_model: str | None = None
    base_url: str | None = None
    api_style: str | None = None
    language: str | None = None
    hourly_limit: int | None = None
    onnx_enabled: bool | None = None
    onnx_model: str | None = None
    onnx_threshold: float | None = None


class AnnotationApiKeyUpdateRequest(BaseModel):
    api_key: str


class OnnxDownloadRequest(BaseModel):
    model: str


class AnnotationBackfillResponse(BaseModel):
    # 待ち行列に入れた件数。
    queued: int
