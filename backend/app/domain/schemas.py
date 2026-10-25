"""API の入出力に使う Pydantic モデル。OpenAPI から型が出るよう、レスポンスは必ずこれを通す。"""

from __future__ import annotations

import unicodedata
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from app.domain import prompt_templates
from app.domain.comfy_analyze import (
    AnalyzeEdge,
    AnalyzeWarning,
    BindingField,
    BindingReading,
    NodeKind,
    ParamTierInfo,
)
from app.domain.comfy_graph import NodeState
from app.domain.comfy_workflow import (
    Bindings,
    ExposedParam,
    SuggestedBindings,
    ValidationIssue,
)
from app.providers.base import ParamType, ProviderCapabilities, RegionsDelivery
from app.providers.base import RegionKind as _RegionKind

# -- Capabilities (ADR-0013: 複数プロバイダー) -------------------------------


class ProviderEntry(ProviderCapabilities):
    """`GET /api/capabilities` の `providers[]` の1件。"""

    available: bool
    unavailable_reason: str | None = None
    # 接続先(SD WebUI・ComfyUI)にまだ一度も問い合わせ終えていない(裏で確かめている)。
    # そのあいだ available は False、モデルの一覧は空のことがある。フロントは少し後に読み直す。
    checking: bool = False
    requires_api_key: bool
    supports_pricing: bool


class EmbeddingCapabilities(BaseModel):
    """画像の埋め込み(ADR-0033 7章)。画面の出し分けに使う。"""

    # 有効で、いま使えるか(文章での検索、似た画像、重複の候補、マップ)。
    available: bool
    # 使うモデルの識別子。使えないときは null。
    model_key: str | None = None
    # 使うモデルの対応言語(ローカルのモデルだけ分かる。リモートは null)。
    languages: list[Literal["ja", "en"]] | None = None
    # 日本語に対応するか(リモートは分からないので null)。
    multilingual: bool | None = None
    # 検索の方式(起動時に決める)。
    index_backend: Literal["pgvector", "numpy"]
    # 使うエンジン(使えないときは null)。リモートなら、画像で探す画像と検索の文章が推論
    # サーバーに送られることを画面に書く(ADR-0033 2章)。
    engine: Literal["onnx", "remote"] | None = None
    # 指示(プロンプト)で探せるか(ADR-0045)。使うモデルがプロンプトの埋め込みに対応するときだけ
    # true。検索ページの「画像 / 指示」の切り替えの出し分けに使う。
    prompt_search: bool = False


class CapabilitiesResponse(BaseModel):
    default_provider: str
    providers: list[ProviderEntry] = Field(default_factory=list)
    embeddings: EmbeddingCapabilities


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


# -- 認証の設定 (ADR-0034) ---------------------------------------------------
# 管理者だけが見る。クライアントシークレットは一部も返さない(設定済みかと出どころだけ)。

AuthSettingSource = Literal["setting", "env", "default"]


class AuthModeSetting(BaseModel):
    value: Literal["none", "oidc"]
    source: AuthSettingSource
    # `.env` に AUTH_MODE が明示されていて、画面から変えられない。
    locked: bool


class AuthTextSetting(BaseModel):
    value: str | None
    source: AuthSettingSource


class AuthListSetting(BaseModel):
    value: list[str]
    source: AuthSettingSource


class AuthSessionHoursSetting(BaseModel):
    value: int
    source: AuthSettingSource
    min: int
    max: int


class AuthClientSecretStatus(BaseModel):
    configured: bool
    source: Literal["file", "env"] | None = None


class AuthConnectionView(BaseModel):
    """本登録の接続。`configured` は発行者・クライアント ID・`PUBLIC_BASE_URL` が揃っているか。"""

    configured: bool
    issuer: AuthTextSetting
    client_id: AuthTextSetting
    scopes: AuthTextSetting
    public_base_url: AuthTextSetting
    client_secret: AuthClientSecretStatus
    # IdP に登録するリダイレクト URI。`PUBLIC_BASE_URL` が無ければ null。
    redirect_uri: str | None = None


class AuthPendingConnectionView(BaseModel):
    """仮登録の接続(テストログインに成功するまで使われない)。"""

    issuer: str
    client_id: str
    scopes: str
    public_base_url: str
    client_secret: AuthClientSecretStatus
    redirect_uri: str
    created_at: datetime | None = None


class AuthVerifiedView(BaseModel):
    """テストログインに成功した記録。`matches_current` は今の本登録の接続でのテストか。"""

    email: str
    verified_at: datetime
    matches_current: bool


AuthEnableBlocker = Literal[
    "env_locked",
    "no_connection",
    "not_verified",
    "admin_emails_empty",
    "verified_email_not_admin",
]


class AuthSettingsResponse(BaseModel):
    """`GET /api/settings/auth`(管理者だけ)。"""

    mode: AuthModeSetting
    connection: AuthConnectionView
    pending: AuthPendingConnectionView | None = None
    verified: AuthVerifiedView | None = None
    admin_emails: AuthListSetting
    allowed_email_domains: AuthListSetting
    session_hours: AuthSessionHoursSetting
    # 今の設定のまま oidc にできない理由(画面のスイッチの近くに出す)。空なら有効にできる。
    enable_blockers: list[AuthEnableBlocker]


class AuthSettingsUpdateRequest(BaseModel):
    """`PATCH /api/settings/auth` の本文。省略した項目は変更せず、明示的な `null` は保存済みの
    値を消して `.env`・既定値に戻す(`model_fields_set` で区別する)。値の妥当性は
    `app/domain/auth_settings.py` が検証し、i18n 対応のメッセージで 422 にする。
    """

    mode: Literal["none", "oidc"] | None = None
    admin_emails: list[str] | None = None
    allowed_email_domains: list[str] | None = None
    # `true` を 1 として受けないよう厳密にする。
    session_hours: StrictInt | None = None


class AuthConnectionRequest(BaseModel):
    """`PUT /api/settings/auth/connection`(仮登録)の本文。

    `client_secret` は、省略すると今のもの(仮登録があればそのもの、無ければ本登録のもの)を
    引き継ぎ、空文字なら public client(シークレットなし)、値があれば差し替える。
    """

    issuer: str
    client_id: str
    scopes: str
    public_base_url: str
    client_secret: str | None = None


class AuthSettingsErrorDetail(BaseModel):
    """認証の設定の 409・422・502 の `detail`。`code` で画面の案内を出し分ける。

    - 409: `env_locked` / `no_connection` / `not_verified` / `admin_emails_empty` /
      `verified_email_not_admin` / `self_not_admin`
    - 422: `invalid_value`(入力の形)/ `discovery_invalid`(Discovery 文書の中身)/
      `client_secret_reentry`(発行者のオリジンが変わるのに、シークレットを入れ直していない)
    - 502: `discovery_failed`(Discovery 文書を取得できない)
    """

    code: str
    message: str
    field: str | None = None


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


class FocalPoint(BaseModel):
    """サムネイルの焦点(ADR-0043)。画像の幅・高さに対する 0〜1 の位置。画面は
    `object-fit: cover` の画像に `object-position: x% y%` を付ける。"""

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


# 画像のレーティングの段階(ADR-0049 1章)。弱い順に グリーン、R、X、XX、XXX。
ContentRatingLevel = Literal["g", "r", "x", "xx", "xxx"]


class ContentRatingSignal(BaseModel):
    """判定の根拠の1つ(ADR-0049 2章)。引き上げの表に載っているタグのうち確率 0.2 以上のもの。"""

    # WD の表記(`_` 区切り)。
    tag: str
    score: float
    # このタグの行の引き上げ先。
    level: ContentRatingLevel
    # このタグがしきい値を超えて段階を上げたか。
    raised: bool = False


class ContentRatingDetail(BaseModel):
    """レーティングの詳細(ADR-0049 2章・4章)。`AssetDetail.content_rating_detail`。"""

    # 表示に使う段階(人が選んでいれば人の段階)。未判定なら null。
    level: ContentRatingLevel | None = None
    # auto | user。判定も人の選択もまだなら null。
    source: Literal["auto", "user"] | None = None
    # 自動判定の段階(人が上書きしても残す)。
    auto_level: ContentRatingLevel | None = None
    # 判定の待ち行列の状態。null は未実行。
    status: Literal["queued", "running", "succeeded", "failed"] | None = None
    error: str | None = None
    # 判定に使った WD Tagger のモデル。
    model: str | None = None
    # 判定の規則の版。
    rules_version: int | None = None
    # rating の4つの確率(`general`、`sensitive`、`questionable`、`explicit`)。
    scores: dict[str, float] | None = None
    signals: list[ContentRatingSignal] = Field(default_factory=list)
    requested_at: datetime | None = None
    judged_at: datetime | None = None


class AssetSummary(BaseModel):
    id: uuid.UUID
    kind: Literal["upload", "generated", "mask", "sketch", "composite", "regions"]
    mime: str
    width: int
    height: int
    bytes: int
    created_at: datetime
    # タイトル(ADR-0024)。人が付けたものか自動のものかは `AssetDetail.title_source` を見る。
    title: str | None = None
    # サムネイルの焦点(ADR-0043)。無ければ null(画面は中央)。
    focal_point: FocalPoint | None = None
    # レーティングの段階(ADR-0049)。未判定なら null。
    content_rating: ContentRatingLevel | None = None
    # Mod の `deny` の判定で画像(原本と派生)を残さなかったか(ADR-0051 4章)。true なら
    # `/content` は 410 で、画面は「画像なし(拒否)」の置き換えの表示にする。
    content_withheld: bool = False
    content_withheld_at: datetime | None = None
    # 見る人のお気に入りか(ADR-0055)。埋めるのはストックの一覧(`GET /api/assets`)だけで、
    # 検索の結果などほかの応答では常に false。
    favorite: bool = False
    # 系列の取り込み(ADR-0037)で入った、取り込んだ Run が作った生成画像か(記録は未検証)。
    # タイルの種別のリボンに使う。取り込みで入ったアップロードなどは種別のとおり false。
    imported: bool = False


class ModVerdictView(BaseModel):
    """Mod の判定(ADR-0051 4章・5章)。Mod ごとの最新だけを返す。"""

    id: uuid.UUID
    mod_kind: Literal["webhook", "python"]
    mod_id: str
    # 判定を記録した時点の Mod の名前。
    mod_name: str
    verdict: Literal["ok", "flag", "deny"]
    label: str | None = None
    # 0〜1(任意)。
    score: float | None = None
    message: str | None = None
    # 外部のシステムの受付番号など。
    reference: str | None = None
    # 外部の詳細へのリンク(http / https)。
    url: str | None = None
    # 同じ内容(sha256)のほかの Asset への判定を及ぼしたものなら true。
    propagated: bool = False
    created_at: datetime


class RunModInvocationView(BaseModel):
    """Mod の呼び出しの記録(ADR-0051 5章)。Run の詳細には `ai.before_send` のものだけを返す。
    運用の情報(HTTP の状態、例外の種類)は含めない。"""

    id: uuid.UUID
    mod_kind: Literal["webhook", "python"]
    mod_id: str
    mod_name: str
    event: str
    purpose: str | None = None
    # Mod の答え(allow / deny / ok / error / timeout / skipped)。
    outcome: str
    # GAKEI がそれをどう扱ったか(失敗時の扱いを当てた後の allow / deny)。
    effective: Literal["allow", "deny"] | None = None
    reason: str | None = None
    reference: str | None = None
    duration_ms: int
    created_at: datetime


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


class AssetContentRatingUpdateRequest(BaseModel):
    """`PUT /api/assets/{id}/content-rating`。`level` に段階を入れると人が選んだ段階になり、
    以後の再判定で変わらない。`null` で自動の段階に戻す(ADR-0049 2章)。"""

    level: ContentRatingLevel | None


class AssetContentRatingResponse(BaseModel):
    """レーティングの変更と再判定の応答(更新後の状態)。"""

    asset_id: uuid.UUID
    content_rating: ContentRatingLevel | None = None
    content_rating_detail: ContentRatingDetail | None = None


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


class TagSuggestion(BaseModel):
    """プロンプトのタグの候補(ADR-0039 2章、ADR-0041 2章)。名前は `_` を空白にしたタグ名で、
    括弧はエスケープしていない(プロンプトに入れるときにクライアントがエスケープする)。"""

    name: str
    # vocabulary: WD Tagger の語彙(count は語彙の件数)、tag: GAKEI のタグ(count は Asset の数)、
    # dictionary: タグ辞書(count は辞書の件数。ADR-0041)
    source: Literal["vocabulary", "tag", "dictionary"]
    count: int
    # 以下はタグ辞書の候補で入る(ほかは null)。カテゴリーは辞書の番号のまま
    # (`category_scheme` が danbooru なら 0 一般、1 作者、3 作品、4 キャラクター、5 メタ)。
    category: int | None = None
    category_scheme: str | None = None
    # 代表の訳(訳の辞書に訳があるとき。語彙・GAKEI のタグの候補にも付く)
    translation: str | None = None
    # 一致の種類(name: タグ名、alias: 別名、translation: 訳)と、別名・訳で一致したときの語
    match: Literal["name", "alias", "translation"] | None = None
    matched: str | None = None


class TagSuggestionResponse(BaseModel):
    items: list[TagSuggestion] = Field(default_factory=list)
    # WD Tagger の語彙(`selected_tags.csv`)を使えたか
    vocabulary_available: bool
    # タグ辞書(有効なタグの一覧)で答えたか(ADR-0041)
    dictionary_available: bool = False


class TagTranslationsRequest(BaseModel):
    """訳を引くタグ名(空白でも `_` でもよい。ADR-0041 4章)。"""

    names: list[str] = Field(max_length=200)


class TagTranslationsResponse(BaseModel):
    # 渡したタグ名 → 代表の訳。訳の無いタグは含めない。
    translations: dict[str, str] = Field(default_factory=dict)


class TagDictionaryItem(BaseModel):
    """登録したタグ辞書(ADR-0041 1章)。"""

    id: uuid.UUID
    filename: str
    kind: Literal["tags", "translations"]
    # タグの一覧のカテゴリーの体系(訳の辞書では null)
    category_scheme: str | None
    row_count: int
    enabled: bool
    status: Literal["importing", "ready", "failed"]
    # 失敗の種類(interrupted / importFailed / noRows)と、その文言
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    finished_at: datetime | None = None


class TagDictionaryListResponse(BaseModel):
    items: list[TagDictionaryItem] = Field(default_factory=list)
    # 選べるカテゴリーの体系
    category_schemes: list[str] = Field(default_factory=list)


class TagDictionaryUpdateRequest(BaseModel):
    enabled: bool | None = None
    category_scheme: Literal["danbooru", "other"] | None = None


class PromptTagsResponse(BaseModel):
    """画像のタグをプロンプトにするときの並び(ADR-0039 1章)。エスケープ前のタグ名。"""

    asset_id: uuid.UUID
    tags: list[str] = Field(default_factory=list)
    vocabulary_available: bool


class RunTextOutput(BaseModel):
    """実行時に作られたテキスト(ADR-0030 2章)。

    - `role = "final_prompt"`: モデルに渡った最終プロンプト。ComfyUI では PE の出力で、Run に
      1件(`output_index` は null。すべての出力に当たる)。`node_id`・`class_type`・`title` は
      送ったグラフ(`run.params.comfyui_prompt`)から取った値。SD WebUI では Dynamic Prompts
      などが1枚ずつ展開したプロンプトで、出力ごとに1件(`output_index` が出力の Asset の
      `output_index` に当たる。ADR-0038 7章)。
    - `role = "final_negative_prompt"`: SD WebUI の、出力ごとの展開後のネガティブプロンプト。

    SD WebUI では、展開しなくても(元のプロンプトと同じでも)記録する。表示する側が Run の
    `prompt`(ネガティブは `params.negative_prompt`)と比べて、同じなら出さない。
    """

    role: str
    # 出力ごとのテキストのとき、その出力の `output_index`。Run 全体に当たるときは null。
    output_index: int | None = None
    node_id: str | None = None
    class_type: str | None = None
    title: str | None = None
    text: str
    # 100,000 文字を超えて切り詰めたとき true。
    truncated: bool = False


class ProducedByRunSummary(BaseModel):
    id: uuid.UUID
    operation: Literal["generate", "edit"]
    model: str
    status: Literal["queued", "running", "succeeded", "failed", "canceled"]
    prompt: str
    # 最終プロンプトなど(ADR-0030)。無ければ null。
    text_outputs: list[RunTextOutput] | None = None


class AssetOrigin(BaseModel):
    """埋め込まれていた `gakei` メタ情報の由来(ADR-0014、2026-09-24 追記)。署名が無いため
    `verified` は常に false(「未検証」)。`origin_meta` を持つ Asset にのみ付く。
    """

    # 同じインスタンスの既存 Asset を指していた場合のみ(内容は一致しなかった)。
    # 見る人に見えない Asset(他人のもの。ADR-0025)のときは null にし、`asset_hidden` を立てる。
    asset_id: uuid.UUID | None = None
    # 由来がこのインスタンスの別の Asset だが、見る人には見せられない(ADR-0025 4章)。
    # 画面は「このインスタンスの別の Asset(表示できません)」とだけ示す。
    asset_hidden: bool = False
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


# -- 入力画像の配置(ADR-0050) -----------------------------------------

COMPOSITION_RECIPE_VERSION = 1
COMPOSITION_MAX_LAYERS = 16
COMPOSITION_MAX_CANVAS_SIDE = 8192
# 四隅の座標の範囲(キャンバスの幅・高さの何倍まで外に出してよいか)。
COMPOSITION_CORNER_BOUND = 4


class CompositionCanvas(BaseModel):
    model_config = ConfigDict(extra="forbid")

    width: int = Field(ge=1, le=COMPOSITION_MAX_CANVAS_SIDE)
    height: int = Field(ge=1, le=COMPOSITION_MAX_CANVAS_SIDE)
    background: Literal["white", "transparent"]


class CompositionLayer(BaseModel):
    """素材1枚。`corners` はキャンバスの画素での四隅(画像の左上・右上・右下・左下の順。反転は
    この四隅に貼る前のテクスチャーに掛ける)。凸で、ねじれていない四角形であること。"""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    source_asset_id: uuid.UUID
    corners: list[tuple[float, float]] = Field(min_length=4, max_length=4)
    flip_x: bool = False
    flip_y: bool = False

    @field_validator("corners")
    @classmethod
    def _convex_quad(cls, value: list[tuple[float, float]]) -> list[tuple[float, float]]:
        crosses: list[float] = []
        for i in range(4):
            ax, ay = value[i]
            bx, by = value[(i + 1) % 4]
            cx, cy = value[(i + 2) % 4]
            crosses.append((bx - ax) * (cy - by) - (by - ay) * (cx - bx))
        # 4つの角がすべて同じ向きに曲がっていれば、凸でねじれていない(4点なので自己交差もない)。
        if not (all(c > 0 for c in crosses) or all(c < 0 for c in crosses)):
            raise ValueError("corners must form a convex, non-self-intersecting quadrilateral")
        area = 0.0
        for i in range(4):
            x1, y1 = value[i]
            x2, y2 = value[(i + 1) % 4]
            area += x1 * y2 - x2 * y1
        if abs(area) / 2 < 1e-6:
            raise ValueError("corners must enclose a nonzero area")
        return value


class CompositionRecipe(BaseModel):
    """配置の記録(レシピ。ADR-0050 3章)。`layers` は重ね順(下から上)。同じ素材を
    複数のレイヤーに使ってよい。`primary_source_asset_id` はレイヤーの素材のどれか。

    `version` は作り方の版。1 は四隅を射影変換で描いたもの、2 は双一次の写像で描いたもの
    (ADR-0050 1章の描画の改訂)。四隅の座標の形は同じなので、どちらも受け付ける。"""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1, 2]
    canvas: CompositionCanvas
    primary_source_asset_id: uuid.UUID
    layers: list[CompositionLayer] = Field(min_length=1, max_length=COMPOSITION_MAX_LAYERS)

    @model_validator(mode="after")
    def _check(self) -> CompositionRecipe:
        if self.primary_source_asset_id not in {layer.source_asset_id for layer in self.layers}:
            raise ValueError("primary_source_asset_id must be one of the layers' sources")
        bound_x = COMPOSITION_CORNER_BOUND * self.canvas.width
        bound_y = COMPOSITION_CORNER_BOUND * self.canvas.height
        for layer in self.layers:
            for x, y in layer.corners:
                if abs(x) > bound_x or abs(y) > bound_y:
                    raise ValueError("corners are too far outside the canvas")
        return self

    def source_order(self) -> list[uuid.UUID]:
        """`asset_composition_source` の並び: 主たる素材が先頭、残りは重ならないようレイヤー順。"""
        order = [self.primary_source_asset_id]
        for layer in self.layers:
            if layer.source_asset_id not in order:
                order.append(layer.source_asset_id)
        return order


class AssetCompositionSourceView(BaseModel):
    """配置の素材1件(`position` の順)。論理削除済み・原本が無い・見えない素材は
    `available = false`(それでも並べる)。"""

    asset_id: uuid.UUID
    available: bool


class AssetComposition(BaseModel):
    recipe: CompositionRecipe
    sources: list[AssetCompositionSourceView]


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
    # ADR-0049: レーティングの詳細(段階の出どころ、確率、根拠、判定の状態)。行が無ければ null。
    content_rating_detail: ContentRatingDetail | None = None
    # 配置の結果(kind=composite)のレシピと素材(ADR-0050)。それ以外、またはレシピの無い配置
    # (素材を範囲の外にして書き出した ZIP から取り込んだものなど)は null。
    composition: AssetComposition | None = None
    # Mod の判定(ADR-0051 4章)。Mod ごとの最新で、新しい順。
    verdicts: list[ModVerdictView] = Field(default_factory=list)
    # 画像を残さなかった理由の判定(`content_withheld` が true のときだけ。系列の ZIP から
    # 画像の無いノードとして取り込んだものなど、判定が無ければ null)。
    content_withheld_verdict: ModVerdictView | None = None
    # 見る人のお気に入りか(ADR-0055)。他人の印は見えない。
    favorite: bool = False


class AssetUploadResponse(AssetDetail):
    """`POST /api/assets` の応答(ADR-0014、2026-09-24 追記)。"""

    ingest_outcome: Literal["created", "matched_existing"]


class AssetListResponse(BaseModel):
    items: list[AssetSummary]
    next_cursor: str | None = None


# -- プロンプトテンプレート(実験) ----------------------------------------
# プロンプトを「スロット(枠)」に分けた組み立ての雛形。証跡ではないので更新・論理削除ができる。
# 作成・置換はスロット・候補・組み合わせを入れ子で丸ごと受ける。

PROMPT_TEMPLATE_NAME_MAX = 100
PROMPT_TEMPLATE_SEPARATOR_MAX = 20
PROMPT_TEMPLATE_TEXT_MAX = 32_000
PROMPT_TEMPLATE_REF_MAX = 64


class PromptVariantInput(BaseModel):
    """候補。`id` を渡すと既存の候補を更新し、渡さなければ新しく作る。

    `ref` は同じリクエストの中だけで使う一時キー。新しい候補を組み合わせから指すときに使う
    (サーバーが id を決めるため、作成前の候補は id で指せない)。
    """

    id: uuid.UUID | None = None
    ref: str | None = Field(default=None, min_length=1, max_length=PROMPT_TEMPLATE_REF_MAX)
    name: str | None = Field(default=None, max_length=PROMPT_TEMPLATE_NAME_MAX)
    text: str = Field(min_length=1, max_length=PROMPT_TEMPLATE_TEXT_MAX)
    # ランダム選択の重み。0 は「選ばれない」。
    weight: float = Field(default=1.0, ge=0, le=1000)


class PromptSlotInput(BaseModel):
    id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=PROMPT_TEMPLATE_NAME_MAX)
    default_mode: Literal["random", "off"] = "random"
    variants: list[PromptVariantInput] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("name must not be blank")
        return stripped


class PromptComboItemInput(BaseModel):
    """組み合わせが指す候補。既存は `variant_id`、同じリクエストで作る候補は `variant_ref`。"""

    variant_id: uuid.UUID | None = None
    variant_ref: str | None = Field(default=None, min_length=1, max_length=PROMPT_TEMPLATE_REF_MAX)

    @model_validator(mode="after")
    def _exactly_one(self) -> PromptComboItemInput:
        if (self.variant_id is None) == (self.variant_ref is None):
            raise ValueError("either variant_id or variant_ref is required")
        return self


class PromptComboInput(BaseModel):
    id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=PROMPT_TEMPLATE_NAME_MAX)
    weight: float = Field(default=1.0, ge=0, le=1000)
    items: list[PromptComboItemInput] = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("name must not be blank")
        return stripped


class PromptTemplateWriteRequest(BaseModel):
    """作成(POST)と丸ごと置換(PUT)の両方で使う。"""

    name: str = Field(min_length=1, max_length=PROMPT_TEMPLATE_NAME_MAX)
    separator: str = Field(default=", ", max_length=PROMPT_TEMPLATE_SEPARATOR_MAX)
    slots: list[PromptSlotInput] = Field(default_factory=list)
    combos: list[PromptComboInput] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("name must not be blank")
        return stripped


class PromptVariantResponse(BaseModel):
    id: uuid.UUID
    name: str | None
    text: str
    weight: float
    position: int


class PromptSlotResponse(BaseModel):
    id: uuid.UUID
    name: str
    default_mode: Literal["random", "off"]
    position: int
    variants: list[PromptVariantResponse] = Field(default_factory=list)


class PromptComboResponse(BaseModel):
    id: uuid.UUID
    name: str
    weight: float
    position: int
    # 指している候補(削除済みの候補は含まない)。
    variant_ids: list[uuid.UUID] = Field(default_factory=list)


class PromptTemplateResponse(BaseModel):
    id: uuid.UUID
    name: str
    separator: str
    created_at: datetime
    updated_at: datetime
    slots: list[PromptSlotResponse] = Field(default_factory=list)
    combos: list[PromptComboResponse] = Field(default_factory=list)


class PromptTemplateSummary(BaseModel):
    """一覧用。中身は数だけ出す。"""

    id: uuid.UUID
    name: str
    separator: str
    slot_count: int
    variant_count: int
    combo_count: int
    created_at: datetime
    updated_at: datetime


class PromptTemplateListResponse(BaseModel):
    items: list[PromptTemplateSummary] = Field(default_factory=list)


class PromptComboSelection(BaseModel):
    """`id` でその組み合わせ、`random` でまだ空いているスロットを覆う組から1つ。"""

    id: uuid.UUID | None = None
    random: bool = False


class PromptSlotSelection(BaseModel):
    mode: Literal["fixed", "random", "off", "text"]
    # mode=fixed のときだけ必要。
    variant_id: uuid.UUID | None = None
    # mode=text のときだけ必要。
    text: str | None = Field(default=None, max_length=PROMPT_TEMPLATE_TEXT_MAX)


class PromptSelections(BaseModel):
    """組み立ての選択。`slots` のキーはスロットの id。"""

    combos: list[PromptComboSelection] = Field(default_factory=list)
    slots: dict[uuid.UUID, PromptSlotSelection] = Field(default_factory=dict)

    def to_domain(self) -> prompt_templates.Selections:
        return prompt_templates.Selections(
            combos=tuple(
                prompt_templates.ComboSelection(id=c.id, random=c.random) for c in self.combos
            ),
            slots={
                slot_id: prompt_templates.SlotSelection(
                    mode=s.mode, variant_id=s.variant_id, text=s.text
                )
                for slot_id, s in self.slots.items()
            },
        )


class PromptBuildRequest(BaseModel):
    selections: PromptSelections = Field(default_factory=PromptSelections)
    # 確認用に乱数の種を固定できる(省略すると毎回引き直す)。
    seed: int | None = Field(default=None, ge=0, le=2**32 - 1)


class PromptBuildResponse(BaseModel):
    prompt: str
    # `run.prompt_build` に入るのと同じ内訳。
    build: dict[str, Any]


class RunPromptTemplate(BaseModel):
    """`POST /api/runs` の `prompt_template`。`prompt` とは排他。"""

    template_id: uuid.UUID
    selections: PromptSelections = Field(default_factory=PromptSelections)


# -- Run -------------------------------------------------------------------


class RunInputCreate(BaseModel):
    asset_id: uuid.UUID
    role: Literal["image", "mask", "reference"]
    position: int = Field(ge=0)


# 繰り返し回数の上限(ADR-0042 1章)。
REPEAT_MAX = 20

# 領域の指示(ADR-0053 3章)。四角の数と文字の長さの上限。
REGIONS_MAX = 16
REGION_INSTRUCTION_MAX_LENGTH = 2000
REGION_TEXT_MAX_LENGTH = 200
REGION_LABEL_MAX_LENGTH = 100

RegionKind = _RegionKind


def _check_region_box(value: list[float]) -> list[float]:
    """主たる入力に対する割合(0〜1)で、左上を原点にした `[x0, y0, x1, y1]`。"""
    x0, y0, x1, y1 = value
    for v in value:
        if not 0.0 <= v <= 1.0:
            raise ValueError("box values must be within [0, 1]")
    if not (x0 < x1 and y0 < y1):
        raise ValueError("box must satisfy x0 < x1 and y0 < y1")
    return [float(v) for v in value]


class RegionBox(BaseModel):
    """領域の指示の四角1つ(ADR-0053 3章)。どのモデルにも依らない形で持ち、渡すときに変換する。"""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    # 画面のバッジの番号(1 から)。プロンプトの文と四角を描いた画像の番号もこれ。
    number: int = Field(ge=1, le=REGIONS_MAX)
    kind: RegionKind = "change"
    # 四角(「動かす」では移す先)。
    box: list[float] = Field(min_length=4, max_length=4)
    # 元の四角。「動かす」だけが持つ(ほかの種類では付けられない)。
    source_box: list[float] | None = Field(default=None, min_length=4, max_length=4)
    # 四角の名前(「右手の白いカフス」など。任意)。位置と合わせて四角の定義で、指示とは分ける。
    # 指示が無い四角は名前の付いた目印になり、全体のプロンプトから番号か名前で指せる。
    label: str | None = Field(default=None, max_length=REGION_LABEL_MAX_LENGTH)
    instruction: str = Field(default="", max_length=REGION_INSTRUCTION_MAX_LENGTH)
    # 描かせる文字(看板など)。指示とは分け、そのまま渡す。
    text: str | None = Field(default=None, max_length=REGION_TEXT_MAX_LENGTH)
    # その四角に使う画像。Run の入力(role = image)として送る。
    reference_asset_id: uuid.UUID | None = None

    @field_validator("box")
    @classmethod
    def _box(cls, value: list[float]) -> list[float]:
        return _check_region_box(value)

    @field_validator("source_box")
    @classmethod
    def _source_box(cls, value: list[float] | None) -> list[float] | None:
        return None if value is None else _check_region_box(value)

    @field_validator("label")
    @classmethod
    def _label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if any(unicodedata.category(ch) == "Cc" for ch in value):
            raise ValueError("label must not contain control characters")
        value = value.strip()
        return value or None

    @field_validator("instruction")
    @classmethod
    def _instruction(cls, value: str) -> str:
        return value.strip()

    @field_validator("text")
    @classmethod
    def _text(cls, value: str | None) -> str | None:
        # 描く文字は前後の空白も含めてそのまま渡す。空なら無しと同じ。
        return value if value else None

    @model_validator(mode="after")
    def _source_box_only_for_move(self) -> RegionBox:
        if self.kind == "move" and self.source_box is None:
            raise ValueError("source_box is required for kind=move")
        if self.kind != "move" and self.source_box is not None:
            raise ValueError("source_box is only allowed for kind=move")
        return self


class RunCreateRequest(BaseModel):
    operation: Literal["generate", "edit"]
    model: str
    # プロンプトテンプレート(実験)で組み立てるときは `prompt` を送らず `prompt_template` を送る。
    # どちらか一方が必須(両方・どちらも無しは 422)。
    prompt: str | None = None
    prompt_template: RunPromptTemplate | None = None
    # 省略時は主プロバイダー(OpenAI。FAKE_PROVIDER=1 のときは fake)を使う(ADR-0013、ADR-0017)。
    provider: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    inputs: list[RunInputCreate] = Field(default_factory=list)
    # 出力を入れるグループ(ADR-0022)。存在しない・削除済みなら 404。
    asset_group_id: uuid.UUID | None = None
    # 繰り返し回数(ADR-0042)。同じ設定の Run をこの数だけまとめて積む。API に送る値では
    # ないので `run.params` には入れない(ADR-0003 ルール4)。テンプレートと併用したときは
    # Run ごとに組み立て直す(意味の通ったガチャになる)。
    repeat: int = Field(default=1, ge=1, le=REPEAT_MAX)
    # 領域の指示の四角(ADR-0053 3章)。プロバイダーが四角を受けるときだけ付けられる
    # (`OperationCapabilities.supports_regions`)。主たる入力があればそれに対する割合、入力画像の
    # 無い Run(ComfyUI。6章)では出力の大きさに対する割合。番号は重ねられない。
    regions: list[RegionBox] = Field(default_factory=list, max_length=REGIONS_MAX)
    # 四角を描いた土台(ADR-0053 1章「四角の土台を選ぶ」)。`input` は主たる入力の上、`output` は
    # 出力の大きさの白紙の上。省略時は、主たる入力があれば `input`、無ければ `output`。入力画像が
    # あっても `output` を選べる(参考の画像を入力に入れたまま、白紙で構図を指示する)。
    regions_frame: Literal["input", "output"] | None = None

    @model_validator(mode="after")
    def _unique_region_numbers(self) -> RunCreateRequest:
        numbers = [r.number for r in self.regions]
        if len(numbers) != len(set(numbers)):
            raise ValueError("region numbers must be unique")
        return self

    @model_validator(mode="after")
    def _prompt_xor_template(self) -> RunCreateRequest:
        if (self.prompt is None) == (self.prompt_template is None):
            raise ValueError("either prompt or prompt_template is required")
        return self


class RunCreatedRef(BaseModel):
    id: uuid.UUID
    status: Literal["queued", "running", "succeeded", "failed", "canceled"]


class RunCreateResponse(BaseModel):
    # 1件目の Run(繰り返し回数を足す前からの形。ADR-0042 3章)。
    id: uuid.UUID
    status: Literal["queued", "running", "succeeded", "failed", "canceled"]
    # 作った Run の全体(積んだ順)。繰り返し回数が 1 なら1件。
    runs: list[RunCreatedRef]


class RunInputRef(BaseModel):
    asset_id: uuid.UUID
    role: Literal["image", "mask", "reference"]
    position: int
    # サムネイルの焦点(ADR-0043)。マスクと焦点の無い画像は null。
    focal_point: FocalPoint | None = None
    # レーティングの段階(ADR-0049)。未判定・マスクは null。
    content_rating: ContentRatingLevel | None = None
    # 画像を残さなかったか(ADR-0051 4章)。
    content_withheld: bool = False


class RunOutputRef(BaseModel):
    asset_id: uuid.UUID
    output_index: int | None
    # 出力 Asset のタイトル(ADR-0024。履歴のカード用)。
    title: str | None = None
    # サムネイルの焦点(ADR-0043)。
    focal_point: FocalPoint | None = None
    # レーティングの段階(ADR-0049)。未判定なら null。
    content_rating: ContentRatingLevel | None = None
    # 画像を残さなかったか(ADR-0051 4章)。
    content_withheld: bool = False
    # 原本のピクセルの大きさ(履歴のカードの「画像」の形で出す)。
    width: int | None = None
    height: int | None = None
    # 見る人のお気に入りか(ADR-0055)。
    favorite: bool = False


class AssetFavoriteResponse(BaseModel):
    """お気に入りの付け外しの結果(ADR-0055)。"""

    asset_id: uuid.UUID
    favorite: bool


class RunRegions(BaseModel):
    """`run.regions` の形(ADR-0053 4章)。"""

    # 変換の版。四角を文と画像に変える作り方を変えたら上げる。
    version: int
    # どの渡し方で送ったか(ADR-0053 5章)。`comfyui_bboxes` では、重ねた画像も足す文も無い
    # (`appended_text` は空、`overlay_asset_id` は null。6章)。
    delivery: RegionsDelivery
    regions: list[RegionBox]
    # プロンプトの後ろに足した文(`run.prompt` の末尾)。再実行では、プロンプトからこの文を除いて
    # フォームに戻す。
    appended_text: str = ""
    # 四角を描いた画像の Asset(種類 `regions`)。
    overlay_asset_id: uuid.UUID | None = None
    # 四角の座標が何に対する割合か。`input` は主たる入力、`output` は出力の大きさ(入力画像の無い
    # Run。ADR-0053 6章)。古い記録(キー無し)は `input`。
    frame: Literal["input", "output"] = "input"


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
    # 主たる親のサムネイルの焦点(ADR-0043。履歴のカードの親の小さな画像用)。
    primary_parent_focal_point: FocalPoint | None = None
    # 主たる親のレーティングの段階(ADR-0049)。未判定なら null。
    primary_parent_content_rating: ContentRatingLevel | None = None
    # 主たる親の画像を残さなかったか(ADR-0051 4章。履歴のカードの親の小さな画像用)。
    primary_parent_content_withheld: bool = False
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
    # 実行時にワークフローが作ったテキスト(ADR-0030。ComfyUI の最終プロンプト)。
    # 成功した Run のうち、記録があるものだけ。それ以外は null。
    text_outputs: list[RunTextOutput] | None = None
    # 領域の指示の四角(ADR-0053 4章。`run.regions`)。四角を付けた Run だけ。再実行でフォームに
    # 四角を戻すのに使う。
    regions: RunRegions | None = None


class RunImportInfo(BaseModel):
    """系列の ZIP から取り込んだ Run の、書き出し元での記録(ADR-0037 2章)。ZIP の自己申告で
    検証していない。"""

    source_run_id: uuid.UUID
    source_creator_name: str | None = None
    source_created_at: datetime | None = None
    source_finished_at: datetime | None = None
    source_gakei_version: str | None = None
    imported_at: datetime


class RunDetail(RunSummary):
    deployment: str | None = None
    provider_request_id: str | None = None
    # プロンプトテンプレート(実験)で組み立てた Run だけ。それ以外は null。
    prompt_build: dict[str, Any] | None = None
    inputs: list[RunInputRef] = Field(default_factory=list)
    # 系列の ZIP から取り込んだ Run(`origin = 'import'`)だけ。それ以外は null(ADR-0037)。
    imported: RunImportInfo | None = None
    # Run に対する Mod の判定(ADR-0051 4章)。Mod ごとの最新で、新しい順。
    verdicts: list[ModVerdictView] = Field(default_factory=list)
    # `ai.before_send` の呼び出しの結果(ADR-0051 5章)。古い順。
    mod_invocations: list[RunModInvocationView] = Field(default_factory=list)


class RunListResponse(BaseModel):
    items: list[RunSummary]
    next_cursor: str | None = None


HistorySort = Literal["group", "embedding"]


class HistoryAssetItem(BaseModel):
    """画像単位の履歴の1項目(ADR-0054 4章)。表示は日付の並びと同じく `run` と出力で描く。"""

    asset_id: uuid.UUID
    # 画像が入っている見えるグループ(`sort=group` のときだけ。グループなし・他の並べ方は null)。
    group_id: uuid.UUID | None = None
    run: RunSummary


class HistoryGroupSection(BaseModel):
    """グループの並びの節(バナーの名前と件数)。`id = null` は「グループなし」。"""

    id: uuid.UUID | None = None
    name: str | None = None
    count: int


class HistoryAssetListResponse(BaseModel):
    items: list[HistoryAssetItem]
    next_cursor: str | None = None
    # `sort=group` のときだけ。グループの並び順で、画像のある節だけ。
    groups: list[HistoryGroupSection] | None = None


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
    kind: Literal["upload", "generated", "mask", "sketch", "composite", "regions"] | None = None
    width: int | None = None
    height: int | None = None
    mime: str | None = None
    restorable: bool = False
    # サムネイルの焦点(ADR-0043)。埋め込みノードは、手元の Asset に対応付いたときだけ。
    focal_point: FocalPoint | None = None
    # レーティングの段階(ADR-0049)。焦点と同じく、埋め込みノードは対応付いたときだけ。
    content_rating: ContentRatingLevel | None = None
    # 画像を残さなかったか(ADR-0051 4章)。埋め込みノードは常に false。
    content_withheld: bool = False
    content_withheld_at: datetime | None = None


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
    # 系列の ZIP から取り込んだ Run(`origin = 'import'`。ADR-0037)。グラフで印を付ける。
    imported: bool = False


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
    # 取り込み済みのローカル Asset があれば、その id(ADR-0014 6章)。見る人に見える
    # Asset だけを対応付ける(ADR-0025)。
    resolved_asset_id: uuid.UUID | None = None
    # 埋め込み(未検証)ノードが、このインスタンスの別の Asset / Run を指しているが、見る人には
    # 見せられない(他人のもの。ADR-0025 4章)。画面は「このインスタンスの別の Asset
    # (表示できません)」とだけ示す。ノードの内容は埋め込まれていたもの(ファイル由来)だけ。
    local_hidden: bool = False
    # 埋め込み(未検証)ノードの正規化後の生データ(prompt/params を省略せずに持つ)。
    # インスペクターでの全文表示に使う(ADR-0014 6章)。
    embedded_detail: dict[str, Any] | None = None
    asset: LineageAssetInfo | None = None
    run: LineageRunInfo | None = None


class LineageEdge(BaseModel):
    """input は Asset→Run、output は Run→Asset、sketch_source は下地 Asset→上描き Asset
    (ADR-0010、2026-09-23 追記。Run を介さない直接の来歴)。composite_source は配置の素材
    Asset→配置の結果(ADR-0050 4章。Run を介さない。`position` は素材を足した順で、0 が主たる
    素材で `primary` が true)。origin は、再アップロードで
    Asset に一致しなかった埋め込みメタ情報の由来 Asset→新しい Asset(ADR-0014、
    2026-09-24 追記。署名の無い自己申告で、`primary` は常に false)。
    """

    source: uuid.UUID
    target: uuid.UUID
    kind: Literal["input", "output", "sketch_source", "composite_source", "origin"]
    role: Literal["image", "mask", "reference"] | None = None
    position: int | None = None
    output_index: int | None = None
    primary: bool = False


class AssetLineageResponse(BaseModel):
    root_asset_id: uuid.UUID
    nodes: list[LineageNode] = Field(default_factory=list)
    edges: list[LineageEdge] = Field(default_factory=list)
    truncated: bool = False


class RunLineageResponse(BaseModel):
    """Run を起点にした系列グラフ(入力 Asset の祖先 → Run → 出力 Asset)。

    `depth` は起点の Run=0、入力は -1、出力は +1(ADR-0023 9章)。REST には出していない。
    """

    root_run_id: uuid.UUID
    nodes: list[LineageNode] = Field(default_factory=list)
    edges: list[LineageEdge] = Field(default_factory=list)
    truncated: bool = False


# -- 系列の持ち出しと取り込み(ADR-0037) -----------------------------------


class LineageExportPreviewResponse(BaseModel):
    """書き出す前の確認。範囲に含まれる画像と Run の数、原本の合計バイト数。"""

    scope: Literal["ancestors", "descendants", "lineage"]
    asset_count: int
    run_count: int
    total_bytes: int
    # 系列グラフのノード数の上限(1000)で打ち切ったか(共有リンクと同じ)。
    truncated: bool = False
    # Run の入力のうち、範囲に入らないので含めない入力の数(全 Run の合計。ID は出さない)。
    omitted_input_count: int = 0
    # ZIP に入る Asset と Run だけの系列グラフ(`include_graph=true` のときだけ。2026-10-07)。
    # 形は系列グラフの応答と同じで、ノードと辺は manifest と一致する。
    graph: AssetLineageResponse | None = None


class LineageImportResponse(BaseModel):
    """取り込みの結果。`root_asset_id` は取り込んだ側(この GAKEI)の起点の Asset。"""

    root_asset_id: uuid.UUID
    asset_count: int
    run_count: int
    # 新しく作った Asset と Run の数(既にあったものを使った分は含まない)。
    created_asset_count: int
    created_run_count: int


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


# -- パラメーターセット(ADR-0040) -----------------------------------------
# 生成のフォームの設定一式。`params` はフォームの値(型変換済み)で、入れ子は持たない。
# サーバーだけが書く項目(`comfyui_*`、`sdwebui_*`)は API で 422 にする。

PARAMETER_SET_NAME_MAX = 100
PARAMETER_SET_PROVIDER_MAX = 32
PARAMETER_SET_MODEL_MAX = 512
PARAMETER_SET_PROMPT_MAX = 32_000
PARAMETER_SET_PARAMS_MAX_KEYS = 200
PARAMETER_SET_PARAM_KEY_MAX = 100

# bool を int より先に置く(Strict なので 1 と true は取り違えない)。
ParameterSetValue = StrictBool | StrictInt | StrictFloat | StrictStr


def _validate_parameter_set_params(
    value: dict[str, ParameterSetValue] | None,
) -> dict[str, ParameterSetValue] | None:
    if value is None:
        return None
    if len(value) > PARAMETER_SET_PARAMS_MAX_KEYS:
        raise ValueError(f"params must have at most {PARAMETER_SET_PARAMS_MAX_KEYS} keys")
    for key, item in value.items():
        if not key or len(key) > PARAMETER_SET_PARAM_KEY_MAX:
            raise ValueError(f"param name must be 1-{PARAMETER_SET_PARAM_KEY_MAX} characters")
        if isinstance(item, str) and len(item) > PARAMETER_SET_PROMPT_MAX:
            raise ValueError(f"param {key} must be at most {PARAMETER_SET_PROMPT_MAX} characters")
    return value


class ParameterSetCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=PARAMETER_SET_NAME_MAX)
    # 登録簿にあるかは問わない(後で無効になることもあるため)。長さだけを確かめる。
    provider: str = Field(min_length=1, max_length=PARAMETER_SET_PROVIDER_MAX)
    model: str | None = Field(default=None, min_length=1, max_length=PARAMETER_SET_MODEL_MAX)
    prompt: str | None = Field(default=None, max_length=PARAMETER_SET_PROMPT_MAX)
    params: dict[str, ParameterSetValue] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("name must not be blank")
        return stripped

    @field_validator("params")
    @classmethod
    def _check_params(cls, value: dict[str, ParameterSetValue]) -> dict[str, ParameterSetValue]:
        return _validate_parameter_set_params(value) or {}


class ParameterSetUpdateRequest(BaseModel):
    """送った項目だけを変える。`model` と `prompt` は null を送ると「保存しない」に戻す。"""

    name: str | None = Field(default=None, min_length=1, max_length=PARAMETER_SET_NAME_MAX)
    provider: str | None = Field(default=None, min_length=1, max_length=PARAMETER_SET_PROVIDER_MAX)
    model: str | None = Field(default=None, min_length=1, max_length=PARAMETER_SET_MODEL_MAX)
    prompt: str | None = Field(default=None, max_length=PARAMETER_SET_PROMPT_MAX)
    params: dict[str, ParameterSetValue] | None = None

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("name must not be blank")
        return stripped

    @field_validator("params")
    @classmethod
    def _check_params(
        cls, value: dict[str, ParameterSetValue] | None
    ) -> dict[str, ParameterSetValue] | None:
        return _validate_parameter_set_params(value)


class ParameterSetResponse(BaseModel):
    id: uuid.UUID
    name: str
    provider: str
    model: str | None
    prompt: str | None
    params: dict[str, ParameterSetValue]
    created_at: datetime
    updated_at: datetime


class ParameterSetListResponse(BaseModel):
    items: list[ParameterSetResponse] = Field(default_factory=list)


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
# 画面から OpenAI の API キーを設定する。キーは一部も返さない(設定済みかどうかと出どころだけ)。


class OpenAIKeyStatusResponse(BaseModel):
    # 有効なプロバイダーがキーを必要とするか(fake は false)。
    required: bool
    configured: bool
    source: Literal["env", "file"] | None = None


class OpenAIKeyUpdateRequest(BaseModel):
    api_key: str


# -- Settings (ADR-0017) --------------------------------------------------
# 画面から OpenAI の接続先(Base URL)を設定する。値は秘密ではないので全文を返す。


class OpenAIBaseUrlStatusResponse(BaseModel):
    # 未設定(OpenAI 本体)なら null。
    value: str | None = None
    source: Literal["env", "file"] | None = None


class OpenAIBaseUrlUpdateRequest(BaseModel):
    """`PUT /api/settings/openai-base-url`。

    `api_key` は Base URL と一緒に入れ直すキー(確かめてから両方を保存する)。画面で保存した
    キーがあり、Base URL のオリジン(scheme + host + port)が変わるときは必須で、省くと 422。
    キーが環境変数(`.env`)のものなら、オリジンの変わる Base URL は画面から保存できない(422)。
    """

    base_url: str
    api_key: str | None = None


# -- General settings (ADR-0009、ADR-0013 7章) -------------------------------
# moderation(Generate 専用)と ComfyUI・SD WebUI のタイムアウトを画面から変える。優先順位は
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


class SdWebuiTimeoutSetting(BaseModel):
    """SD WebUI の1回の実行を待つ上限(秒。ADR-0038 6章)。"""

    value: int
    source: Literal["setting", "env", "default"]
    default: int


class GeneralSettingsResponse(BaseModel):
    moderation: ModerationSetting
    comfyui_timeout_seconds: ComfyUITimeoutSetting
    sdwebui_timeout_seconds: SdWebuiTimeoutSetting


class ProviderLocalitySetting(BaseModel):
    """画像のプロバイダーの接続の接続先(`locality`。ADR-0051 3章)。"""

    # 実効の値(保存した値、無ければ既定)。
    value: Literal["external", "local"]
    # connection = 画面で決めた値、default = 既定(すべて external)。
    source: Literal["connection", "default"]
    default: Literal["external", "local"]


class ProviderLocalitiesResponse(BaseModel):
    openai: ProviderLocalitySetting
    comfyui: ProviderLocalitySetting
    sdwebui: ProviderLocalitySetting


class ProviderLocalitiesUpdateRequest(BaseModel):
    """`PATCH /api/settings/provider-localities`。省略した項目は変えず、明示的な `null` は保存した
    値を消して既定に戻す。"""

    openai: Literal["external", "local"] | None = None
    comfyui: Literal["external", "local"] | None = None
    sdwebui: Literal["external", "local"] | None = None


class GeneralSettingsUpdateRequest(BaseModel):
    """`PATCH /api/settings/general` の本文。項目を省略すると変更せず、明示的な `null` は
    保存済みの値を削除して環境変数・既定値に戻す(`model_fields_set` で区別する)。

    値の妥当性(moderation は auto/low のみ、タイムアウトは60〜10800秒)は
    `app/domain/general_settings.py` が検証し、i18n 対応のメッセージで 422 にする。
    そのため、ここでは型を絞り過ぎない。
    """

    moderation: str | None = None
    comfyui_timeout_seconds: int | None = None
    sdwebui_timeout_seconds: int | None = None


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
    # ADR-0013 7章「無効にする / 有効にする」: URL を残したまま使わない状態か。true の間は
    # enabled も false(モデルの選択肢に出さず、接続先に問い合わせない)。url は残っていれば返す。
    disabled: bool


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


# -- SD WebUI(ADR-0038) ------------------------------------------------------------
# 資格情報(Basic 認証)の値は返さず、設定済みかだけを返す。接続先の options や一覧の
# filename(フルパス)も返さない。


SdWebuiReason = Literal[
    "apiNotEnabled", "unauthorized", "unreachable", "timeout", "unexpectedResponse", "parseFailed"
]


class SdWebuiStatusResponse(BaseModel):
    url: str | None
    enabled: bool
    available: bool
    # 機械可読の理由(available が false のとき)。reason_message は利用者向けの文言。
    reason: SdWebuiReason | None = None
    reason_message: str | None = None
    source: Literal["setting", "env", "none"]
    # true の間は接続設定と資格情報を変更できない(SD WebUI の Run が queued/running)。
    locked: bool
    loopback: bool | None = None
    credentials_set: bool
    # 接続できたときだけ。forge は Forge 系(forge_additional_modules がある)、a1111 はそれ以外。
    flavor: Literal["forge", "a1111"] | None = None
    checkpoint_count: int | None = None
    # ADR-0038 6章「無効にする / 有効にする」: URL・資格情報を残したまま使わない状態か。true の間は
    # enabled も false(モデルの選択肢に出さず、接続先に問い合わせない)。url は残っていれば返す。
    disabled: bool


class SdWebuiLora(BaseModel):
    """接続先の LoRA 1つ(ADR-0038 8章)。`path` とその他のメタ情報は返さない。"""

    name: str
    # name と違うときだけ
    alias: str | None = None
    # 学習時のメタ情報から分かるベースモデル。分からなければ null。
    base_model: Literal["sdxl", "sd1"] | None = None
    # `ss_tag_frequency` を足し合わせて多い順(最大 20。`_` は空白)。
    trigger_tags: list[str] = Field(default_factory=list)


class SdWebuiLorasResponse(BaseModel):
    """`GET /api/sdwebui/loras`。name の順(大文字小文字を無視)。"""

    items: list[SdWebuiLora]


class SdWebuiImportUnapplied(BaseModel):
    """フォームに入れなかった生成情報の項目(infotext の名前と値のまま)。"""

    name: str
    value: str


class SdWebuiImportNote(BaseModel):
    """読み込みの注意。`code` は機械可読(modelNotFound、modelMatchedByHash、sizeOutOfRange、
    invalidValue、samplerNotFound、schedulerNotFound、vaeNotFound、dynamicPromptsUnavailable、
    truncated)、`message` は利用者向けの文言。"""

    code: str
    message: str


class SdWebuiImportSource(BaseModel):
    """読み込んだ生成情報の出どころ(infotext の `Version`。無ければ null)。"""

    software: str | None = None


class SdWebuiImportParamsResponse(BaseModel):
    """`POST /api/sdwebui/import-params`(ADR-0038 9章)。フォームに入れる値。

    `model` はチェックポイントの `model_name`(見つからなければ null。フォームのモデルを変えない)。
    `params` は SD WebUI の Generate のパラメーター名(capabilities と同じ)と `size`(`WxH`)。
    """

    model: str | None
    prompt: str
    params: dict[str, str | int | float | bool]
    unapplied: list[SdWebuiImportUnapplied]
    notes: list[SdWebuiImportNote]
    source: SdWebuiImportSource


class SdWebuiConnectionTestRequest(BaseModel):
    """接続テスト(`POST /api/sdwebui/connection/test`)。

    url を省くと現在の有効な URL。username と password は両方そろえて指定すると、その値で
    試す(保存はしない)。省くと保存済みの資格情報を使う。ただし保存済みの資格情報は、保存した
    ときと同じオリジン(scheme + host + port)にだけ送る。違うオリジンの URL を、資格情報を
    入れずに試すと 422(入れ直すよう伝える)。
    """

    url: str | None = None
    username: str | None = None
    password: str | None = None


class SdWebuiConnectionTestResponse(BaseModel):
    url: str
    available: bool
    reason: SdWebuiReason | None = None
    reason_message: str | None = None
    loopback: bool
    flavor: Literal["forge", "a1111"] | None = None
    checkpoint_count: int | None = None


class SdWebuiConnectionRequest(BaseModel):
    """接続・変更(`PUT /api/sdwebui/connection`)。

    username と password は両方そろえて指定すると、接続先と一緒に保存する(この URL のオリジンに
    結び付ける)。保存済みの資格情報があり、URL のオリジン(scheme + host + port)が変わるときは
    必須で、省くと 422(資格情報を入れ直すか、先に削除する)。同じオリジンでパスだけ変えるなら
    省いてよい。
    """

    url: str
    allow_non_loopback: bool = False
    username: str | None = None
    password: str | None = None


class SdWebuiCredentialsRequest(BaseModel):
    """Basic 認証の資格情報(`PUT /api/sdwebui/credentials`)。値は応答に含めない。"""

    username: str
    password: str


class ComfyNodeInputInfo(BaseModel):
    name: str
    value: Any = None
    linked: bool


class ComfyNodeInfo(BaseModel):
    id: str
    class_type: str | None = None
    title: str | None = None
    inputs: list[ComfyNodeInputInfo] = Field(default_factory=list)
    # 出力につながる(live)/ 表示だけ(display)/ 使われない(unused)。ADR-0052 2章。
    state: NodeState = "live"
    # 図で残す錨の種類(保存、表示、サンプラー、エンコーダー、画像の読み込み、切り替え)。
    # どれでもなければ null。ADR-0052 4章。
    kind: NodeKind | None = None
    # 出力の型(`/object_info` に接続できたときだけ。無ければ null)。
    output_types: list[str] | None = None


class ComfyAnalyzeRequest(BaseModel):
    """`template` か `workflow_id`(登録済みのワークフローの保存したテンプレート)のどちらか一方。

    `workflow_id` は編集画面で使う。ブラウザで JSON を読み直すと `1.0` が `1` になり、公開
    パラメーターの型(FLOAT)が分からなくなるため、保存したテンプレートをサーバーで読む。
    """

    template: dict[str, Any] | None = None
    workflow_id: uuid.UUID | None = None


class ComfyAnalyzeResponse(BaseModel):
    nodes: list[ComfyNodeInfo] = Field(default_factory=list)
    # 見つからなかった必須項目(prompt、outputs)があっても、見つかったものは提案として
    # 返す(null にしない。ADR-0013 フォローアップ)。欠けている必須項目は warnings に書く。
    suggested_bindings: SuggestedBindings
    suggested_operation: Literal["generate", "edit"]
    candidate_params: list[ExposedParam] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    # `warnings` に種別を付けたもの。`reading` は読み取り(readings)で同じことを示している。
    warning_items: list[AnalyzeWarning] = Field(default_factory=list)
    # 項目ごとの読み取り(確信度・理由・道筋・候補・問い。ADR-0052 2章)。
    readings: list[BindingReading] = Field(default_factory=list)
    # 公開パラメーターの候補の段(推奨 / その他 / 公開しない)。
    param_tiers: list[ParamTierInfo] = Field(default_factory=list)
    # 提案した差し込み先に、保存時と同じ検証を掛けた結果(ADR-0052 3章)。
    validation: list[ValidationIssue] = Field(default_factory=list)
    # 配線の一覧(読むだけの図に使う。ADR-0052 4章)。切り替えで選ばれなかった枝は followed=false。
    edges: list[AnalyzeEdge] = Field(default_factory=list)


class ComfyValidateRequest(BaseModel):
    """登録・編集の画面で、保存の前に差し込み先と公開パラメーターを検証する(ADR-0052 3章)。

    `bindings` は analyze の提案と同じ形で、まだ選んでいない必須項目(prompt、outputs)は問わない
    (欠けは画面が別に示す)。保存はしない。
    """

    template: dict[str, Any]
    operation: Literal["generate", "edit"]
    bindings: SuggestedBindings
    exposed_params: list[ExposedParam] = Field(default_factory=list)


class ComfyValidateResponse(BaseModel):
    # 保存時と同じ検証の結果(エラーと警告)。
    issues: list[ValidationIssue] = Field(default_factory=list)


class ComfySuggestRequest(BaseModel):
    """アシスタントでの推定(ADR-0052 6章)。`template` か `workflow_id` のどちらか一方(analyze と
    同じ)。`name` は登録画面で入力中の名前(`ai.before_send` の `params` に載せる)。保存はしない。"""

    template: dict[str, Any] | None = None
    workflow_id: uuid.UUID | None = None
    name: str | None = Field(default=None, max_length=100)


class ComfySuggestItem(BaseModel):
    """提案した項目1つ。値は `ComfySuggestResponse.suggested_bindings` の同じ項目にある。"""

    field: BindingField
    # LLM が書いた理由の文(無ければ空)。
    reason: str = ""
    # 規則の読み取り(analyze)と同じ値か。
    matches_reading: bool
    # 保存時の検証の警告(保存はできる)。
    warnings: list[str] = Field(default_factory=list)


class ComfySuggestDiscarded(BaseModel):
    """検証を通らずに捨てた提案。`target` は `seed[1]`、`outputs.9` のように、どの値か。"""

    field: BindingField
    target: str
    message: str


class ComfySuggestUsage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None


class ComfySuggestResponse(BaseModel):
    """アシスタントの提案(ADR-0052 6章)。`items` にある項目だけが提案で、`suggested_bindings` の
    ほかの項目は使わない。LLM が申告する確信度は返さない。"""

    suggested_operation: Literal["generate", "edit"]
    operation_reason: str = ""
    operation_matches_reading: bool
    suggested_bindings: SuggestedBindings
    items: list[ComfySuggestItem] = Field(default_factory=list)
    discarded: list[ComfySuggestDiscarded] = Field(default_factory=list)
    model: str
    usage: ComfySuggestUsage = Field(default_factory=ComfySuggestUsage)


class ComfyWorkflowCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    operation: Literal["generate", "edit"]
    template: dict[str, Any]
    bindings: Bindings
    exposed_params: list[ExposedParam] = Field(default_factory=list)
    # 接続先(ADR-0051 3章)。`unset` は「接続の設定に従う」で、それ以外なら ComfyUI の接続の
    # 値より優先する。省略・null は `unset`。
    locality: Literal["external", "local", "unset"] | None = None


class ComfyWorkflowUpdateRequest(BaseModel):
    """全項目 Optional(PATCH)。渡された項目だけを更新するが、検証は更新後の全体で行う。"""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    operation: Literal["generate", "edit"] | None = None
    template: dict[str, Any] | None = None
    bindings: Bindings | None = None
    exposed_params: list[ExposedParam] | None = None
    locality: Literal["external", "local", "unset"] | None = None


class ComfyWorkflowSummary(BaseModel):
    id: uuid.UUID
    name: str
    operation: Literal["generate", "edit"]
    template_sha256: str
    # 接続先(ADR-0051 3章)。`unset` は「接続の設定に従う」。
    locality: Literal["external", "local", "unset"] = "unset"
    created_at: datetime
    updated_at: datetime


class ComfyWorkflowDetail(ComfyWorkflowSummary):
    template: dict[str, Any]
    bindings: Bindings
    exposed_params: list[ExposedParam] = Field(default_factory=list)
    # 保存時の検証の結果(ADR-0052 3章)。作成・更新の応答では警告だけ(エラーなら 422)。
    # 取得では、保存済みの差し込み先を今の検証に掛けた結果(古い登録のエラーも出る)。
    validation_issues: list[ValidationIssue] = Field(default_factory=list)


class ComfyWorkflowListResponse(BaseModel):
    items: list[ComfyWorkflowSummary] = Field(default_factory=list)


class ComfyViewNodeInput(BaseModel):
    """閲覧用の入力1つ(ADR-0052 7章)。秘密に見える値(ADR-0013 8章)は `value` を返さず、
    `redacted` を True にする。"""

    name: str
    value: Any = None
    linked: bool
    redacted: bool = False


class ComfyViewNode(BaseModel):
    id: str
    class_type: str | None = None
    title: str | None = None
    inputs: list[ComfyViewNodeInput] = Field(default_factory=list)
    # 出力につながる(live)/ 表示だけ(display)/ 使われない(unused)。ADR-0052 2章。
    state: NodeState = "live"
    kind: NodeKind | None = None


class ComfyViewExposedParam(BaseModel):
    """閲覧用の公開パラメーター。登録した既定値が秘密に見えれば伏せる(`default_redacted`)。"""

    name: str
    label: str
    node: str
    input: str
    type: ParamType
    default: Any | None = None
    default_redacted: bool = False


class ComfyWorkflowViewResponse(BaseModel):
    """生成画面からの閲覧(`GET /api/comfyui/workflows/{id}/view`。ADR-0052 7章)。

    図に要る構造と値だけを返す。テンプレートそのものは返さない。差し込み先(`bindings`)には
    出力(`outputs`)と最終プロンプト(`final_prompt`)も含まれる。
    """

    id: uuid.UUID
    name: str
    operation: Literal["generate", "edit"]
    nodes: list[ComfyViewNode] = Field(default_factory=list)
    edges: list[AnalyzeEdge] = Field(default_factory=list)
    bindings: Bindings
    exposed_params: list[ComfyViewExposedParam] = Field(default_factory=list)


# -- About (ADR-0021 3章) ---------------------------------------------------


class HealthResponse(BaseModel):
    """`GET /api/health`(Issue #43)。ログインなしで呼べるので、これ以外の情報は出さない。"""

    status: Literal["ok"] = "ok"


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
    # 有効期限(null は無期限)と権限(ADR-0023 11章)。
    expires_at: datetime | None = None
    scope: Literal["full", "read"]
    # 期限を過ぎているか(サーバーの時刻で判定する)。期限切れでも失効させるまで一覧に残る。
    expired: bool


class ApiTokenListResponse(BaseModel):
    items: list[ApiTokenRow] = Field(default_factory=list)


class ApiTokenCreateRequest(BaseModel):
    """名前の妥当性(空でない、100文字以内)は `app/domain/api_tokens.py` が検証する。"""

    name: str
    # 有効期限の日数。30 / 90 / 365 か、null で無期限。省くと 90 日(ADR-0023 11章 1)。
    expires_in_days: Literal[30, 90, 365] | None = 90
    # 権限。`full`(すべて)か `read`(読み取りのみ)。省くと `full`(11章 2)。
    scope: Literal["full", "read"] = "full"


class ApiTokenCreateResponse(ApiTokenRow):
    # 発行したトークンの値。この応答でだけ返し、以後は取り出せない。
    token: str


# -- 自動タイトル・タグ(ADR-0024) ----------------------------------------------


class OnnxModelStatus(BaseModel):
    """ONNX タガーのモデル1つの状態。"""

    name: Literal["wd-vit-tagger-v3", "wd-swinv2-tagger-v3", "wd-eva02-large-tagger-v3"]
    # 取得するファイル(model.onnx と selected_tags.csv)の合計の大きさ。
    size_bytes: int
    # 読み込みと推論1回に要るメモリの目安(バイト。`WdModel.memory_bytes`)。
    memory_bytes: int
    downloaded: bool
    download_status: Literal["idle", "downloading", "failed"]
    # 0〜1。ダウンロード中だけ値が入る。
    download_progress: float | None = None
    download_error: str | None = None


class AnnotationConnectionCalls(BaseModel):
    """接続先1つへの、自動タイトル・タグの直近1時間の LLM・VLM の呼び出し回数(上限は接続先
    ごと。ADR-0024 8章)。"""

    connection_id: str
    calls_last_hour: int


class AnnotationTarget(BaseModel):
    """用途1つの「接続先 + モデル名」の組。"""

    connection_id: str
    model: str


class AnnotationDefaultProfile(BaseModel):
    llm: AnnotationTarget
    vlm: AnnotationTarget


class AnnotationComfyuiProfile(BaseModel):
    # null なら「既定と同じ」。
    llm: AnnotationTarget | None = None
    vlm: AnnotationTarget | None = None


class AnnotationProfiles(BaseModel):
    """用途ごとの組(ADR-0024 8章)。`comfyui` は ComfyUI の Run の出力にだけ使う。"""

    default: AnnotationDefaultProfile
    comfyui: AnnotationComfyuiProfile


class AnnotationSettingsResponse(BaseModel):
    """`GET /api/settings/annotation`。"""

    auto_on_ingest: bool
    llm_enabled: bool
    vlm_enabled: bool
    language: Literal["ja", "en"]
    # タグの言語(ADR-0024 6章)。native はエンジン任せ、localized は `language` に合わせる。
    tag_language: Literal["native", "localized"]
    hourly_limit: int
    onnx_enabled: bool
    onnx_model: Literal["wd-vit-tagger-v3", "wd-swinv2-tagger-v3", "wd-eva02-large-tagger-v3"]
    onnx_threshold: float
    profiles: AnnotationProfiles
    onnx_models: list[OnnxModelStatus] = Field(default_factory=list)
    # 一度も推定していない Asset(削除済み・マスクを除く)の件数。一括実行の対象。
    pending_count: int
    # 待ち行列にある件数(queued と running)。
    queued_count: int
    # 直近1時間の LLM・VLM の呼び出し回数の全接続先の合計(プロセス内で数える。再起動で 0 に
    # 戻る)。
    calls_last_hour: int
    # 接続先ごとの直近1時間の呼び出し回数。LLM の接続先の一覧と同じ順(先頭は組み込みの
    # `openai`)。接続先そのものは `GET /api/settings/llm-connections`。
    connection_calls: list[AnnotationConnectionCalls] = Field(default_factory=list)
    # いま使えるエンジン(有効かつ、ONNX はモデルをダウンロード済み)。空なら推定できない。
    usable_engines: list[Literal["llm", "vlm", "onnx"]] = Field(default_factory=list)
    # レーティングの判定(ADR-0049 3章)。`onnx_model` と同じ WD のモデルを使い、ONNX の
    # タグ付け(`onnx_enabled`)とは独立にオンにできる。
    content_rating_enabled: bool = False
    # 判定に使うモデル(`onnx_model`)をダウンロード済みか(FAKE_PROVIDER=1 では常に true)。
    content_rating_model_ready: bool = False
    # 有効かつモデルの準備ができていて、判定できるか。
    content_rating_usable: bool = False
    # 一括判定の対象(未判定と、古い版の規則で自動判定した画像。削除済み・マスクを除く)の件数。
    content_rating_pending_count: int = 0
    # 判定の待ち行列にある件数(queued と running)。
    content_rating_queued_count: int = 0


class AnnotationDefaultProfileUpdate(BaseModel):
    """省略した用途は変更しない。null にはできない。"""

    llm: AnnotationTarget | None = None
    vlm: AnnotationTarget | None = None


class AnnotationComfyuiProfileUpdate(BaseModel):
    """省略した用途は変更しない。null を送ると「既定と同じ」に戻す。"""

    llm: AnnotationTarget | None = None
    vlm: AnnotationTarget | None = None


class AnnotationProfilesUpdate(BaseModel):
    default: AnnotationDefaultProfileUpdate | None = None
    comfyui: AnnotationComfyuiProfileUpdate | None = None


class AnnotationSettingsUpdateRequest(BaseModel):
    """`PATCH /api/settings/annotation`。省略した項目は変更しない。値の妥当性は
    `app/domain/annotation_settings.py` が検証する。`profiles` は書いたマスだけ変える。
    """

    auto_on_ingest: bool | None = None
    llm_enabled: bool | None = None
    vlm_enabled: bool | None = None
    language: str | None = None
    tag_language: str | None = None
    hourly_limit: int | None = None
    onnx_enabled: bool | None = None
    onnx_model: str | None = None
    onnx_threshold: float | None = None
    content_rating_enabled: bool | None = None
    profiles: AnnotationProfilesUpdate | None = None


# -- LLM の接続先(ADR-0032) --------------------------------------------------

# 接続先を使う機能の id(`app/domain/llm_connections.py` の `register_usage` で登録したもの)。
StructuredOutputsMode = Literal["auto", "supported", "unsupported"]
LlmConnectionFeature = Literal[
    "annotation", "embedding", "assistant", "region_detection", "group_naming"
]


class LlmConnectionView(BaseModel):
    """LLM・VLM の接続先1つ(ADR-0032、ADR-0024 8章)。キーの値は一部も返さない。"""

    id: str
    # 組み込みの接続先(`openai`、OpenAI の設定)の名前は画面の言語で表示する(サーバーの言語の
    # 文言を入れて返すが、画面は `builtin` を見て自分の言語で出してよい)。
    name: str
    # 組み込みの接続先(OpenAI の設定。ADR-0017)なら true。名前・Base URL・キーは変えられず、
    # 削除もできない(API 形式だけは変えられる)。
    builtin: bool
    # 組み込みの接続先は OpenAI の設定の Base URL(未設定なら null = OpenAI 本体)。
    base_url: str | None = None
    api_style: Literal["responses", "chat"]
    # キーを設定しているか(組み込みの接続先は OpenAI のキーの有無)。
    api_key_set: bool
    # 接続先(ADR-0051 3章)と、どこで決まったか。組み込みの接続先は OpenAI の接続の値。
    locality: Literal["external", "local"] = "external"
    locality_source: Literal["connection", "default"] = "default"
    # 構造化出力(JSON のスキーマ)への対応の指定(既定は `auto`)と、それで決まった判定。`auto` は
    # OpenAI の公式(組み込みで Base URL を変えていないときを含む)だけを対応とみなす。
    structured_outputs: StructuredOutputsMode = "auto"
    structured_outputs_supported: bool = False
    # この接続先を使っている機能。空でなければ削除できない。
    used_by: list[LlmConnectionFeature] = Field(default_factory=list)


class LlmConnectionsResponse(BaseModel):
    """`GET /api/settings/llm-connections` と、接続先を変える API の応答。"""

    # 先頭は常に組み込みの `openai`。追加した接続先は追加した順に並ぶ。
    connections: list[LlmConnectionView]


class LlmConnectionCreateRequest(BaseModel):
    """`POST /api/settings/llm-connections`。キーは任意(省略・空ならキーなし)。"""

    name: str
    base_url: str
    api_style: str = "responses"
    api_key: str | None = None
    # 接続先(ADR-0051 3章)。省略・null は既定(`external`)。
    locality: Literal["external", "local"] | None = None
    # 構造化出力への対応。省略・null は既定(`auto`)。
    structured_outputs: StructuredOutputsMode | None = None


class LlmConnectionUpdateRequest(BaseModel):
    """`PATCH /api/settings/llm-connections/{id}`。省略した項目は変更しない。
    組み込みの接続先(`openai`)は `api_style`、`locality`、`structured_outputs` だけ変えられる
    (ほかを送ると 409)。"""

    name: str | None = None
    base_url: str | None = None
    api_style: str | None = None
    # 接続先(ADR-0051 3章)。null は既定(`external`)に戻す。
    locality: Literal["external", "local"] | None = None
    # 構造化出力への対応(null は不正な値で 422)。
    structured_outputs: StructuredOutputsMode | None = None
    # Base URL と一緒に入れ直すキー。保存済みのキーがあり、Base URL のオリジン(scheme + host +
    # port)が変わるときは必須(省くと 422)。同じオリジンでパスだけ変えるなら省いてよい。
    api_key: str | None = None


class LlmConnectionApiKeyUpdateRequest(BaseModel):
    api_key: str


class OnnxDownloadRequest(BaseModel):
    model: str


class AnnotationBackfillResponse(BaseModel):
    # 待ち行列に入れた件数。
    queued: int


# -- ユーザーごとの設定(ADR-0049 6章) -------------------------------------------------


class UserPreferencesResponse(BaseModel):
    """`GET`/`PATCH /api/users/me/preferences`。すべてのキーを、保存していなければ既定値で返す。
    キーは `content_rating.mask_levels` のように `.` を含む。"""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    # マスクする段階(既定は R 以上)。弱い順に並べ、重複は除く。
    content_rating_mask_levels: list[ContentRatingLevel] = Field(
        default_factory=lambda: ["r", "x", "xx", "xxx"], alias="content_rating.mask_levels"
    )
    # 未判定の画像もマスクするか(既定 false)。
    content_rating_mask_unrated: bool = Field(default=False, alias="content_rating.mask_unrated")


class UserPreferencesUpdateRequest(BaseModel):
    """`PATCH /api/users/me/preferences`。書いたキーだけ変える。知らないキー、形の合わない値、
    `null` は 422。"""

    model_config = ConfigDict(extra="forbid")

    content_rating_mask_levels: list[ContentRatingLevel] | None = Field(
        default=None, alias="content_rating.mask_levels"
    )
    content_rating_mask_unrated: StrictBool | None = Field(
        default=None, alias="content_rating.mask_unrated"
    )


# -- 画像の埋め込み(ADR-0033) -------------------------------------------------------

EmbeddingOnnxModelName = Literal[
    "clip-vit-b32-u8", "clip-vit-b32", "clip-japanese-base", "embeddinggemma-2-q8"
]


class EmbeddingOnnxModelStatus(BaseModel):
    """埋め込みのローカルの ONNX モデル1つの状態。"""

    name: EmbeddingOnnxModelName
    # このモデルのベクトルの `model_key`(`onnx:<モデル>@<リビジョン>`)。
    model_key: str
    # 対応する言語。英語だけのモデルでは、日本語で検索してもほとんど当たらない。
    languages: list[Literal["ja", "en"]]
    dim: int
    # 取得するファイルの合計の大きさ。
    size_bytes: int
    # 読み込みと推論に要るメモリの目安(バイト)。画像側と文章側の両方を読み込んだとき。
    memory_bytes: int
    # 文章での検索だけのとき(文章側だけを読み込む)のメモリの目安(バイト)。
    memory_text_bytes: int
    license: str
    # 検索の質が高い代わりに計算が重いモデル(EmbeddingGemma 2。ADR-0044 6章)。
    heavy: bool = False
    # 日本語・英語のほかにも多くの言語に対応する(画面では「多言語」と出す)。
    many_languages: bool = False
    # このモデルの重複のしきい値(保存した値、無ければ既定)と、その既定(ADR-0044 5章)。
    duplicate_threshold: float
    duplicate_threshold_default: float
    downloaded: bool
    download_status: Literal["idle", "downloading", "failed"]
    # 0〜1。ダウンロード中だけ値が入る。
    download_progress: float | None = None
    download_error: str | None = None


class EmbeddingStoredCount(BaseModel):
    """モデル(`model_key`)ごとの保存済みのベクトルの件数。"""

    model_key: str
    count: int
    dim: int | None = None
    # 今使っているモデルか。
    active: bool


class EmbeddingSettingsResponse(BaseModel):
    """`GET /api/settings/embeddings`。"""

    enabled: bool
    engine: Literal["onnx", "remote"]
    onnx_model: EmbeddingOnnxModelName
    # リモートの接続先(LLM の接続先の id。ADR-0032)。未設定なら null。
    remote_connection_id: str | None = None
    remote_model: str | None = None
    remote_api_format: Literal["infinity"]
    auto_on_ingest: bool
    # 使うモデルの重複のしきい値(モデルごと。ADR-0044 5章)と、そのモデルの既定。
    duplicate_threshold: float
    duplicate_threshold_default: float
    # 使うモデルの `model_key`。リモートで接続先かモデル名が未設定なら null。
    active_model_key: str | None = None
    # 使うモデルの対応言語(ローカルのモデルだけ分かる。リモートは null)。
    active_languages: list[Literal["ja", "en"]] | None = None
    # 有効で、いま計算できるか(ローカルはモデルをダウンロード済み)。
    usable: bool
    # 検索の方式(ADR-0033 4章)。起動時に決める。
    index_backend: Literal["pgvector", "numpy"]
    onnx_models: list[EmbeddingOnnxModelStatus] = Field(default_factory=list)
    stored: list[EmbeddingStoredCount] = Field(default_factory=list)
    # 使うモデルのベクトルが無い画像(削除済み・マスクを除く。失敗したものを含む)の件数。
    # 一括実行の対象。
    pending_count: int
    # 使うモデルの待ち行列にある件数(queued と running)。
    queued_count: int
    # 使うモデルで計算に失敗した件数。
    failed_count: int
    # 使うモデルが Run のプロンプトの埋め込みに対応するか(ADR-0045 1章)。false なら下の
    # `prompt_*` の件数は 0 で、画面には出さない。
    prompt_supported: bool = False
    # プロンプトの埋め込みの件数(使うモデル。画像の件数と同じ数え方。ADR-0045 6章)。
    # 計算済み、一括実行の対象(失敗したものを含む)、待ち行列(queued と running)、失敗。
    prompt_stored_count: int = 0
    prompt_pending_count: int = 0
    prompt_queued_count: int = 0
    prompt_failed_count: int = 0


class EmbeddingSettingsUpdateRequest(BaseModel):
    """`PATCH /api/settings/embeddings`。省略した項目は変更しない。値の妥当性は
    `app/domain/embedding_settings.py` が検証する。`remote_connection_id` と `remote_model` は
    null で未設定に戻す(ほかの項目の null は 422)。
    """

    enabled: bool | None = None
    engine: str | None = None
    onnx_model: str | None = None
    remote_connection_id: str | None = None
    remote_model: str | None = None
    remote_api_format: str | None = None
    auto_on_ingest: bool | None = None
    # 使うモデル(同じ更新でモデルを変えるなら変えた後のモデル)のしきい値。
    duplicate_threshold: float | None = None


class EmbeddingBackfillResponse(BaseModel):
    # 待ち行列に入れた画像の件数。
    queued: int
    # 待ち行列に入れた Run のプロンプトの件数(使うモデルが対応するときだけ。ADR-0045 4章)。
    prompt_queued: int = 0


class AssetEmbeddingStatus(BaseModel):
    """1枚の埋め込みの状態(`POST /api/assets/{id}/embedding` の応答)。"""

    asset_id: uuid.UUID
    model_key: str
    status: Literal["queued", "running", "succeeded", "failed"]
    error: str | None = None
    requested_at: datetime | None = None
    finished_at: datetime | None = None


# -- 埋め込みでの検索(ADR-0033 6章・7章) --------------------------------------------
# 埋め込みを無効にしている(または使えるモデルが無い)間は、どれも 409 を返す。


class SemanticAssetHit(AssetSummary):
    """文章での検索・似た画像の1件。`score` はコサイン類似度(-1〜1、大きいほど近い)。"""

    score: float


class SemanticRunHit(BaseModel):
    """指示(プロンプト)で探したときの1件(ADR-0045 5章)。Run の単位で、同じ Run の出力を
    何枚も並べない。`score` はコサイン類似度(-1〜1、大きいほど近い)。"""

    id: uuid.UUID
    operation: Literal["generate", "edit"]
    model: str
    created_at: datetime
    # 埋め込んだプロンプト(最終プロンプトがあればそれ)の先頭。長ければ切る。
    prompt: str
    # `prompt` を切ったとき true。
    prompt_truncated: bool = False
    # 最初の出力(削除されていない、`output_index` の最も小さいもの)。無ければ null。
    output: AssetSummary | None = None
    score: float
    # 履歴のカードで並べるための Run の要約(キーワード検索の `runs` と同じ形)。検索ページが
    # 指示の結果を履歴のカードで出すのに使う(ADR-0045 6章、2026-10-11 追記)。
    run: RunSummary


class SemanticSearchResponse(BaseModel):
    """`GET /api/search/semantic`。類似度の高い順。

    `target = "image"`(既定)は画像を `assets` に、`target = "prompt"` は Run を `runs` に
    返す(もう片方は空)。"""

    target: Literal["image", "prompt"] = "image"
    query: str
    # 使ったモデル(ベクトル空間)の識別子。
    model_key: str
    # 使うモデルの対応言語(ローカルのモデルだけ分かる。リモートは null)。
    languages: list[Literal["ja", "en"]] | None = None
    # 日本語に対応するか。英語だけのモデルでは日本語で検索してもほとんど当たらない。
    # リモートは分からないので null。
    multilingual: bool | None = None
    assets: list[SemanticAssetHit] = Field(default_factory=list)
    runs: list[SemanticRunHit] = Field(default_factory=list)


class SimilarAssetsResponse(BaseModel):
    """`GET /api/assets/{id}/similar`。起点の画像自身は含めない。"""

    asset_id: uuid.UUID
    model_key: str
    assets: list[SemanticAssetHit] = Field(default_factory=list)


class SimilarImageSearchResponse(BaseModel):
    """`POST /api/search/similar-image`。手元の画像に似た画像(類似度の高い順)。送った画像は
    保存しないので、起点の id は無い。"""

    model_key: str
    assets: list[SemanticAssetHit] = Field(default_factory=list)


class EmbeddingErrorDetail(BaseModel):
    """埋め込みの API の 409 の `detail`。`code` で画面の出し分けをする。

    - `embeddings_unavailable`: 埋め込みが無効、または使えるモデルが無い
    - `embedding_pending`: その画像のベクトルを計算中(待ち行列にある)
    - `embedding_failed`: その画像のベクトルの計算に失敗した
    - `embedding_missing`: その画像のベクトルがまだ無い(計算を頼んでいない)
    - `embedding_not_supported`: マスクは対象外
    - `content_withheld`: Mod の `deny` の判定で画像を残さなかった(ADR-0051 4章)
    """

    code: Literal[
        "embeddings_unavailable",
        "embedding_pending",
        "embedding_failed",
        "embedding_missing",
        "embedding_not_supported",
        "content_withheld",
    ]
    message: str


class DuplicateAsset(AssetSummary):
    # 代表(グループでいちばん古い画像)との類似度。代表自身は、メンバーとの類似度の最大値
    # (ADR-0033 12章)。
    max_score: float
    # この画像に知覚ハッシュが無い(まだ作っていない)ので、代表との組を CLIP の類似度だけで
    # 判定した(代表なら、メンバーとの組すべて)。サーバーは常に true / false を返す(省略可能に
    # しているのは、生成する TypeScript の型で必須にしないため)。
    hash_missing: bool | None = None


class DuplicateGroup(BaseModel):
    # 古い順(最初に作った画像が先頭)。
    assets: list[DuplicateAsset]
    # 代表との類似度の最大値。
    max_score: float
    # 知覚ハッシュが無い画像を含む(その組は CLIP の類似度だけで判定した)。常に true / false。
    hash_missing: bool | None = None


class DuplicatesResponse(BaseModel):
    """`GET /api/embeddings/duplicates`。類似度がしきい値以上で知覚ハッシュも近い組を、
    代表(いちばん古い画像)との類似度でまとめたグループ(大きい順。ADR-0033 12章)。
    見るのは新しい順に `scanned` 件まで。それより多ければ `truncated`。"""

    model_key: str
    threshold: float
    groups: list[DuplicateGroup] = Field(default_factory=list)
    # 比べた画像の数(ベクトルのある、見える画像。新しい順に上限まで)。
    scanned: int
    # 上限を超えたので、古い画像を比べていない。
    truncated: bool
    # グループの数が `limit` を超えたので、小さいグループを返していない。
    groups_truncated: bool


# -- グループの提案(ADR-0056) ----------------------------------------------------------


class GroupSuggestionTag(BaseModel):
    """集まりに偏る英語のタグ。`label` はタグ辞書の訳(無ければタグのまま)。"""

    tag: str
    label: str
    # 集まりでの出現率と、対象全体での出現率(画像の枚数の割合)。
    rate: float
    global_rate: float


class GroupSuggestionFranchise(BaseModel):
    """集まりで最も多い作品。`rate` は集まりの枚数に対する割合。`label` は訳(あれば)。"""

    name: str
    label: str | None = None
    rate: float


class GroupSuggestionTarget(BaseModel):
    """既存のグループへの追加の提案のときの追加先。`overlap` は集まりのうちそのグループに
    入っている割合(0.5 以上)。"""

    id: uuid.UUID
    name: str
    overlap: float


class GroupSuggestion(BaseModel):
    # 提案の画像と追加先から作る(同じ入力なら同じ)。
    id: str
    size: int
    # 提案の画像(集まりの重心に近い順)。
    member_ids: list[uuid.UUID]
    # 表紙(`member_ids` の先頭 12)。
    cover_ids: list[uuid.UUID]
    top_tags: list[GroupSuggestionTag] = Field(default_factory=list)
    franchise: GroupSuggestionFranchise | None = None
    # 既存のグループへの追加の提案なら、その追加先。null なら新しいグループの提案。
    target_group: GroupSuggestionTarget | None = None
    # 受け入れると別のグループから移る枚数(`scope=all` のときだけ 1 以上になりうる)。
    moving_count: int = 0
    # そのうち、別のグループから移る画像(`member_ids` の部分集合)。
    moving_ids: list[uuid.UUID] = Field(default_factory=list)
    # 名前(常に null。候補は `POST /api/asset-groups/suggestions/names`。ADR-0056 6章)。
    name: str | None = None


class GroupSuggestionsResponse(BaseModel):
    """`GET /api/asset-groups/suggestions`。見るのは新しい順に `scanned` 件まで。"""

    model_key: str
    scope: Literal["ungrouped", "all"]
    granularity: Literal["normal", "fine"]
    scanned: int
    truncated: bool
    suggestions: list[GroupSuggestion] = Field(default_factory=list)
    # 未分類(どの提案にも入らなかった、グループ外の画像。新しい順)。
    outlier_ids: list[uuid.UUID] = Field(default_factory=list)
    # 提案と未分類に出る画像のサムネイルの項目。
    assets: list[AssetSummary] = Field(default_factory=list)


class GroupSuggestionAcceptRequest(BaseModel):
    """`POST /api/asset-groups/suggestions/accept`。`name`(新しいグループ)か
    `target_group_id`(既存のグループへ移す)のどちらか一方。"""

    name: str | None = None
    target_group_id: uuid.UUID | None = None
    asset_ids: list[uuid.UUID] = Field(min_length=1, max_length=5000)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str | None) -> str | None:
        return None if value is None else _normalize_group_name(value)

    @model_validator(mode="after")
    def _one_target(self) -> GroupSuggestionAcceptRequest:
        if (self.name is None) == (self.target_group_id is None):
            raise ValueError("specify exactly one of name and target_group_id")
        return self


class GroupSuggestionNamesRequest(BaseModel):
    """`POST /api/asset-groups/suggestions/names`。提案を同じ条件で計算し直し、
    `suggestion_ids` の提案(新しいグループの提案だけ。無ければ全部)に名前の候補を付ける。"""

    scope: Literal["ungrouped", "all"] = "ungrouped"
    granularity: Literal["normal", "fine"] = "normal"
    suggestion_ids: list[str] | None = Field(default=None, max_length=500)


class GroupSuggestionNames(BaseModel):
    """提案1つの名前の候補(良い順。最大3つ)。"""

    suggestion_id: str
    candidates: list[str] = Field(default_factory=list)


class GroupSuggestionNamesResponse(BaseModel):
    """名前の候補。候補を返せなかった提案は `items` に入らない。"""

    model: str | None = None
    items: list[GroupSuggestionNames] = Field(default_factory=list)


GroupNamingUnusableReason = Literal[
    "disabled", "connection_missing", "api_key_missing", "structured_outputs_unsupported"
]


class GroupNamingSettingsResponse(BaseModel):
    """`GET /api/settings/group-naming`。全ログイン者が読める(名前の候補を出すかの判断)。"""

    enabled: bool
    connection_id: str
    model: str
    # 有効で、接続先とキーがそろい、接続先が Structured Outputs に対応するとみなせる。
    usable: bool
    # 使えない理由(1つに絞る。使えるなら null)。見る順は領域の検出と同じ。
    unusable_reason: GroupNamingUnusableReason | None = None


class GroupNamingSettingsUpdateRequest(BaseModel):
    """書いた項目だけ変える。null は不正な値(422)。"""

    enabled: StrictBool | None = None
    connection_id: StrictStr | None = None
    model: StrictStr | None = None


class EmbeddingGraphNode(BaseModel):
    id: uuid.UUID
    kind: Literal["upload", "generated", "sketch", "composite"]
    width: int
    height: int
    title: str | None = None
    # サムネイルの焦点(ADR-0043)。
    focal_point: FocalPoint | None = None
    # レーティングの段階(ADR-0049 5章「マップ」)。未判定は null。マップでマスクするかを決める。
    content_rating: ContentRatingLevel | None = None


class EmbeddingGraphResponse(BaseModel):
    """`GET /api/embeddings/graph`。マップ(2D の地図と類似度のネットワーク)の元データ。

    ノードは新しい順。`neighbor_indices[i]` と `neighbor_similarities[i]` はノード i の近傍
    (`nodes` の位置と、コサイン類似度)で、先頭は必ず自分自身(類似度 1 = 距離 0)、続いて
    ほかのノードを最大 `k` 件、類似度の高い順に並べる。どの行も同じ長さ
    (`1 + min(k, ノード数 - 1)`)なので、umap-js の `setPrecomputedKNN` にそのまま渡せる
    (距離は `1 - 類似度`)。
    """

    model_key: str
    k: int
    nodes: list[EmbeddingGraphNode] = Field(default_factory=list)
    neighbor_indices: list[list[int]] = Field(default_factory=list)
    neighbor_similarities: list[list[float]] = Field(default_factory=list)
    # 系列(ADR-0003)の主たる親の辺 `[親の位置, 子の位置]`。両方が `nodes` にあるものだけ。
    # `include_lineage` を指定したときだけ入る。
    lineage_edges: list[list[int]] | None = None
    # 絞り込みに合う、ベクトルのある画像の数。
    total: int
    # `limit` を超えたので、古い画像を返していない。
    truncated: bool


class EmbeddingGraphScoresResponse(BaseModel):
    """`GET /api/embeddings/graph/scores`。マップのノード全件と検索の文章の類似度。

    `ids` は `GET /api/embeddings/graph` の `nodes` と同じ並び(同じ絞り込み・同じ `limit`
    なら位置が一致する)で、`scores[i]` が `ids[i]` のコサイン類似度(-1〜1)。
    """

    model_key: str
    # 前後の空白を落とした検索の文章。
    query: str
    ids: list[uuid.UUID] = Field(default_factory=list)
    scores: list[float] = Field(default_factory=list)
    # 絞り込みに合う、ベクトルのある画像の数。
    total: int
    # `limit` を超えたので、古い画像を返していない。
    truncated: bool


# -- 共有リンク(ADR-0029) ---------------------------------------------------------
# 本人向け(`/api/shares`、ログインが要る)と、ログイン不要の公開(`/api/public/shares`)。
# 公開の応答は、ほかの画面の型(AssetDetail、RunSummary、LineageNode など)を使い回さず、
# 見せてよい項目だけを持つ専用の型にする(ほかの型に項目が増えても、公開には載らないように)。

ShareScope = Literal["single", "ancestors", "lineage"]


class ShareSettingsResponse(BaseModel):
    """`GET /api/settings/share`。有効/無効(既定は無効)。"""

    enabled: bool


class ShareSettingsUpdateRequest(BaseModel):
    """`PATCH /api/settings/share` の本文。省略した項目は変更しない。"""

    enabled: bool | None = None


class SharePreviewRequest(BaseModel):
    asset_id: uuid.UUID
    scope: ShareScope


class ShareCreateRequest(SharePreviewRequest):
    # 原本の表示とダウンロードを許すか(ADR-0029 4章。既定は許す)。
    allow_original: bool = True


class SharePreviewAsset(BaseModel):
    """共有に含まれる画像1件(作る前の確認用。サムネイルは本人向けの配信 URL で出す)。"""

    id: uuid.UUID
    kind: Literal["upload", "generated", "mask", "sketch", "composite", "regions"]
    width: int
    height: int
    title: str | None = None
    # 画像を残さなかったか(ADR-0051 4章)。true ならサムネイルは出さない(410 になる)。
    content_withheld: bool = False


class SharePreviewResponse(BaseModel):
    scope: ShareScope
    asset_count: int
    assets: list[SharePreviewAsset] = Field(default_factory=list)
    # 系列グラフのノード数の上限で、たどり切れなかった(ADR-0009)。
    truncated: bool = False


class ShareRow(BaseModel):
    """自分の共有リンクの1件(ADR-0029 7章)。"""

    id: uuid.UUID
    url: str
    root_asset_id: uuid.UUID
    # 起点の画像が削除されている(共有のページは 404 になっている)。
    root_deleted: bool = False
    # 起点の画像を残さなかった(ADR-0051 4章。サムネイルは出さない)。
    root_content_withheld: bool = False
    root_title: str | None = None
    scope: ShareScope
    allow_original: bool
    asset_count: int
    created_at: datetime
    last_accessed_at: datetime | None = None
    access_count: int = 0


class ShareListResponse(BaseModel):
    items: list[ShareRow] = Field(default_factory=list)


class PublicShareAsset(BaseModel):
    id: uuid.UUID
    kind: Literal["upload", "generated", "mask", "sketch", "composite", "regions"]
    mime: str
    width: int
    height: int
    created_at: datetime
    title: str | None = None
    # この画像を作った Run(共有に含まれるときだけ)。
    run_id: uuid.UUID | None = None
    # 作成時の系列グラフでの深さ(起点 0、祖先が負、子孫が正)。
    depth: int = 0
    # この画像の原本を出せるか(ADR-0029 4章)。共有が原本を許していても、秘密に見える値を含む
    # ComfyUI の Run の画像は false(2026-10-01 追記)。false ならプレビューまで。
    allow_original: bool
    # サムネイルの焦点(ADR-0043)。
    focal_point: FocalPoint | None = None
    # 画像を残さなかったか(ADR-0051 9章)。true なら画像は返さない(判定は含めない)。
    content_withheld: bool = False


class PublicShareRun(BaseModel):
    """画像を作った Run のうち、見せてよい項目だけ(ADR-0029 3章)。実行者、料金・usage、
    エラー、入力の Asset の id は含めない。`params` は `shares.public_params` の値(ComfyUI の
    Run は `run.params` の写しで入れ子を含み、秘密に見える値は `***` に伏せる。ADR-0029 3章、
    2026-10-01 改訂・追記)。"""

    id: uuid.UUID
    operation: Literal["generate", "edit"]
    # モデル名(ComfyUI はワークフローの名前)。
    model: str
    prompt: str
    params: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    # 最終プロンプト(PE の出力。ADR-0030 4章)。無ければ null。
    text_outputs: list[RunTextOutput] | None = None


class PublicShareEdge(BaseModel):
    """共有に含まれるノードどうしの辺だけ(範囲外のノードへの辺は含めない)。"""

    source: uuid.UUID
    target: uuid.UUID
    kind: Literal["input", "output", "sketch_source", "composite_source", "origin"]
    role: Literal["image", "mask", "reference"] | None = None
    position: int | None = None
    output_index: int | None = None
    primary: bool = False


class PublicShareResponse(BaseModel):
    """`GET /api/public/shares/{token}`(ログイン不要)。"""

    root_asset_id: uuid.UUID
    scope: ShareScope
    allow_original: bool
    created_at: datetime
    assets: list[PublicShareAsset] = Field(default_factory=list)
    runs: list[PublicShareRun] = Field(default_factory=list)
    edges: list[PublicShareEdge] = Field(default_factory=list)


# -- 生成のアシスタント(ADR-0048) --------------------------------------------
# 会話は証跡ではないので、名前の変更と論理削除ができる。メッセージの `content`(JSON)は
# `app/domain/assistant.py` で読み書きし、応答では役割ごとの項目に平らにして返す。

ASSISTANT_ATTACHMENTS_MAX = 4
ASSISTANT_TEXT_MAX = 20_000
ASSISTANT_TITLE_MAX = 100
# ストックの選択モードで選んでいる画像を送る上限(ADR-0048 13章)。
ASSISTANT_SELECTED_MAX = 200


class AssistantConversationResponse(BaseModel):
    id: uuid.UUID
    # 最初の発言の先頭から作る(LLM は使わない)。発言の前は空文字。
    title: str
    created_at: datetime
    updated_at: datetime


class AssistantConversationListResponse(BaseModel):
    items: list[AssistantConversationResponse] = Field(default_factory=list)


class AssistantConversationCreateRequest(BaseModel):
    title: str = Field(default="", max_length=ASSISTANT_TITLE_MAX)


class AssistantConversationUpdateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=ASSISTANT_TITLE_MAX)


class AssistantUndoResult(BaseModel):
    """ストックの整理を戻した結果(ADR-0048 13章)。戻すのは書き込んだ直後から変わっていない
    画像だけで、`restored` が戻した枚数、`skipped` が変わっていたので戻さなかった枚数。
    `group_deleted` は作ったグループを消した、`group_kept` は別の画像が入っていたので
    消さなかった。"""

    restored: int
    skipped: int
    group_kept: bool = False
    group_deleted: bool = False


class AssistantToolCallView(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class AssistantMessageResponse(BaseModel):
    """会話の1メッセージ。役割(`role`)ごとに使う項目が違う。

    - `user`: `text`、`attachments`(添付の Asset ID)。
    - `assistant`: `text`、`tool_calls`、`status`(`complete` / `stopped`(利用者が止めた) /
      `error`)、`error`。
    - `tool`: `tool_call_id`、`tool_name`、`ok`、`result`(道具が LLM に返した JSON。書き込みの
      道具は `before` / `after` を含む)、`run_ids`(作った Run)、`undoable`(「元に戻す」が
      使えるか)、`undone_at`(戻した日時)。
    """

    id: uuid.UUID
    conversation_id: uuid.UUID
    position: int
    role: Literal["user", "assistant", "tool"]
    created_at: datetime
    text: str = ""
    attachments: list[uuid.UUID] = Field(default_factory=list)
    tool_calls: list[AssistantToolCallView] = Field(default_factory=list)
    status: Literal["complete", "stopped", "error"] | None = None
    error: str | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    ok: bool | None = None
    result: dict[str, Any] | None = None
    run_ids: list[uuid.UUID] = Field(default_factory=list)
    undoable: bool = False
    undone_at: datetime | None = None
    # ストックの整理を戻した結果(ADR-0048 13章)。それ以外の書き込みと、戻す前は null。
    undo_result: AssistantUndoResult | None = None


class AssistantConversationDetailResponse(AssistantConversationResponse):
    messages: list[AssistantMessageResponse] = Field(default_factory=list)


class AssistantFormInput(BaseModel):
    asset_id: uuid.UUID
    role: Literal["image", "mask", "reference"] = "image"


class AssistantFormState(BaseModel):
    """送信時の生成フォームの状態(ADR-0048 5章)。サーバーはフォームの状態を持たず、発言の
    たびにブラウザが送る。`get_form_context` がそのまま返し、`generate_image` で指定の無い
    項目に使う。"""

    provider: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=200)
    operation: Literal["generate", "edit"] | None = None
    prompt: str | None = Field(default=None, max_length=32_000)
    params: dict[str, Any] = Field(default_factory=dict)
    inputs: list[AssistantFormInput] = Field(default_factory=list, max_length=32)
    asset_group_id: uuid.UUID | None = None
    # スタジオの結果エリアに出ている画像(ADR-0048 12章)。画像が無いとき・スタジオを開いて
    # いないときは null。`view_result` が引数の無いときに見る。フォームの値ではないので
    # `update_form` では変わらない。
    result_asset_id: uuid.UUID | None = None
    # ストックの選択モードで選んでいる画像(ADR-0048 13章)。選んでいなければ空。
    # フォームの値ではないので `update_form` では変わらない。
    selected_asset_ids: list[uuid.UUID] = Field(
        default_factory=list, max_length=ASSISTANT_SELECTED_MAX
    )


class AssistantMessageCreateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=ASSISTANT_TEXT_MAX)
    # 添付するストックの Asset(本人に見えるものだけ。ADR-0048 6章)。
    attachments: list[uuid.UUID] = Field(default_factory=list, max_length=ASSISTANT_ATTACHMENTS_MAX)
    form: AssistantFormState | None = None
    # 画面の言語。応答の言語をこれに合わせる(7章)。
    locale: Literal["ja", "en"] = "ja"


class AssistantUndoResponse(BaseModel):
    """「元に戻す」の結果。`message` は戻した tool のメッセージ(`undone_at` が入る)、
    `prompt_set` は戻した後のセット(作成を取り消したときと、ストックの整理では null)、
    `undo_result` はストックの整理で戻した枚数と飛ばした枚数。"""

    message: AssistantMessageResponse
    prompt_set: PromptSetResponse | None = None
    # ストックの整理を戻したとき(ADR-0048 13章)。
    undo_result: AssistantUndoResult | None = None


# SSE のイベント(`POST /api/assistant/conversations/{id}/messages` の応答)。各イベントは
# `event: <type>` と `data: <JSON>` の組で、JSON の `type` にも同じ値が入る。


class AssistantUserMessageEvent(BaseModel):
    """保存した利用者の発言(最初に1回)。"""

    type: Literal["user_message"] = "user_message"
    message: AssistantMessageResponse


class AssistantTextDeltaEvent(BaseModel):
    """アシスタントの本文の差分(届いた順につなぐ)。"""

    type: Literal["text_delta"] = "text_delta"
    text: str


class AssistantToolCallEvent(BaseModel):
    """道具の呼び出し(実行の前)。"""

    type: Literal["tool_call"] = "tool_call"
    tool_call_id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class AssistantMessageEvent(BaseModel):
    """保存したアシスタントのメッセージ(LLM の1回の応答ごと。本文と道具の呼び出し)。"""

    type: Literal["assistant_message"] = "assistant_message"
    message: AssistantMessageResponse


class AssistantToolResultEvent(BaseModel):
    """保存した道具の結果(tool のメッセージ)。"""

    type: Literal["tool_result"] = "tool_result"
    message: AssistantMessageResponse


class AssistantRunCreatedEvent(BaseModel):
    """`generate_image` が作った Run(Run ごとに1回)。進捗は既存の Run の SSE で見る。"""

    type: Literal["run_created"] = "run_created"
    run_id: uuid.UUID
    tool_call_id: str
    message_id: uuid.UUID


class AssistantErrorEvent(BaseModel):
    """処理を止めた理由。`llm_error`(LLM の呼び出しの失敗)、`rate_limited`(LLM の1時間の
    上限)、`tool_limit`(1回の発言で道具を呼ぶ回数の上限)、`hook_denied`(Mod が送る前の
    チェックで止めた。ADR-0051)、`internal`(その他)。"""

    type: Literal["error"] = "error"
    code: Literal["llm_error", "rate_limited", "tool_limit", "hook_denied", "internal"]
    message: str


class AssistantDoneEvent(BaseModel):
    """最後に1回。`status` は `complete` / `error` / `limit`(上限で止めた)。"""

    type: Literal["done"] = "done"
    status: Literal["complete", "error", "limit"]
    conversation: AssistantConversationResponse


class AssistantStreamEvent(BaseModel):
    """SSE のイベントの型(OpenAPI に型を出すための入れ物。`event` がイベント本体)。"""

    event: (
        AssistantUserMessageEvent
        | AssistantTextDeltaEvent
        | AssistantToolCallEvent
        | AssistantMessageEvent
        | AssistantToolResultEvent
        | AssistantRunCreatedEvent
        | AssistantErrorEvent
        | AssistantDoneEvent
    ) = Field(discriminator="type")


class AssistantSettingsResponse(BaseModel):
    """`GET /api/settings/assistant`(ADR-0048 7章・9章)。"""

    enabled: bool
    connection_id: str
    model: str
    hourly_request_limit: int
    runs_per_message_limit: int
    hourly_run_limit: int
    hourly_request_limit_default: int
    hourly_request_limit_max: int
    runs_per_message_limit_default: int
    runs_per_message_limit_max: int
    hourly_run_limit_default: int
    hourly_run_limit_max: int
    # 有効で、接続先とキーがそろっている(パネルを出してよい)。
    usable: bool
    # 使えない理由(1つに絞る。使えるなら null)。
    unusable_reason: Literal["disabled", "connection_missing", "api_key_missing"] | None = None
    # 接続先が Structured Outputs に対応するとみなせる(ComfyUI のワークフローの推定に使える。
    # ADR-0052 6章。判定は LLM の接続先の設定で、「対応する」にもできる)。`usable` と両方が真の
    # ときだけ、登録・編集画面にボタンを出す。
    structured_outputs: bool = False
    # 見ている利用者の、直近1時間の LLM のリクエスト数と、アシスタントが作った Run の数。
    requests_last_hour: int
    runs_last_hour: int


class AssistantSettingsUpdateRequest(BaseModel):
    """書いた項目だけ変える。null は不正な値(422)。"""

    enabled: StrictBool | None = None
    connection_id: StrictStr | None = None
    model: StrictStr | None = None
    hourly_request_limit: StrictInt | None = None
    runs_per_message_limit: StrictInt | None = None
    hourly_run_limit: StrictInt | None = None


# -- Mod とフック(ADR-0051) ---------------------------------------------------------

ModEvent = Literal["asset.ingested", "ai.before_send", "run.finished"]
ModPurpose = Literal[
    "image_run",
    "annotation",
    "assistant",
    "embedding",
    "workflow_analysis",
    "region_detection",
    "group_naming",
]
ModSource = Literal["upload", "url", "sketch", "mask", "composite", "generated", "import", "mcp"]
ModOnFailure = Literal["block", "allow"]


class ModFilters(BaseModel):
    """購読の絞り込み。空のリストは絞らない。"""

    purposes: list[ModPurpose] = Field(default_factory=list)
    sources: list[ModSource] = Field(default_factory=list)
    providers: list[str] = Field(default_factory=list)


class ModWebhookView(BaseModel):
    """Webhook 1つ。署名の鍵の値は一部も返さず、設定済みかどうか(`secret_set`)だけ返す。"""

    id: str
    name: str
    url: str
    enabled: bool
    events: list[ModEvent]
    filters: ModFilters
    send_images: bool
    timeout_seconds: float
    on_failure: ModOnFailure
    # 「判定を待つ」(ADR-0051 2章)。`asset.ingested` と `run.finished` を、原本をストレージに
    # 書く前に同期で呼ぶ。
    wait_for_verdict: bool
    order: int | None = None
    secret_set: bool
    # 保存済みの鍵が今の最小の長さ(32 文字)に満たないか。前に保存した短い鍵は動き続けるが、
    # 差し替えを勧める。
    secret_too_short: bool = False


class ModSettingFieldView(BaseModel):
    """Python の Mod のマニフェストに書いた設定の項目。秘密の項目は値を返さず、`secret_set`
    だけ返す。"""

    key: str
    type: Literal["string", "number", "bool", "path", "secret"]
    # 画面の言語の名前(マニフェストに言語ごとの名前があれば、その言語のもの)。
    label: str
    default: Any = None
    value: Any = None
    secret_set: bool | None = None


class PythonModView(BaseModel):
    """`MODS_DIR` に置いた Python の Mod 1つ。マニフェストが読めない・import できないものも
    `error` を付けて返す(読み込みエラー)。"""

    id: str
    name: str | None = None
    version: str | None = None
    api_version: int | None = None
    # マニフェストに書いた購読できるイベント。
    available_events: list[ModEvent] = Field(default_factory=list)
    # 読み込みエラー(マニフェストの不正、import の失敗)。
    error: str | None = None
    # import 済みか(有効にして、読み込めたもの)。
    loaded: bool
    enabled: bool
    # 購読するイベント。null はマニフェストのイベントすべて。
    events: list[ModEvent] | None = None
    filters: ModFilters
    timeout_seconds: float
    on_failure: ModOnFailure
    wait_for_verdict: bool
    # 判定を返す Mod か(マニフェストの `returns_verdicts`)。false なら「判定を待つ」を選べない。
    returns_verdicts: bool = True
    order: int | None = None
    settings: list[ModSettingFieldView] = Field(default_factory=list)


class ModsResponse(BaseModel):
    """`GET /api/settings/mods`(管理者だけ)。"""

    # イベントの `instance.name`(管理者が決めた名前。未設定なら null)。
    instance_name: str | None = None
    webhooks: list[ModWebhookView]
    python_mods: list[PythonModView]


class ModsUpdateRequest(BaseModel):
    """`PATCH /api/settings/mods`。null・空はインスタンスの名前を消す。"""

    instance_name: str | None = None


class ModWebhookCreateRequest(BaseModel):
    """`POST /api/settings/mods/webhooks`。署名の鍵は必須(応答には返さない)。"""

    name: str
    url: str
    secret: str
    enabled: bool = False
    events: list[ModEvent] = Field(default_factory=list)
    filters: ModFilters = Field(default_factory=ModFilters)
    send_images: bool = False
    timeout_seconds: float = 10.0
    on_failure: ModOnFailure = "block"
    wait_for_verdict: bool = False


class ModWebhookUpdateRequest(BaseModel):
    """`PATCH /api/settings/mods/webhooks/{id}`。省略した項目は変えない。`secret` を送ると鍵を
    差し替える。"""

    name: str | None = None
    url: str | None = None
    secret: str | None = None
    enabled: bool | None = None
    events: list[ModEvent] | None = None
    filters: ModFilters | None = None
    send_images: bool | None = None
    timeout_seconds: float | None = None
    on_failure: ModOnFailure | None = None
    wait_for_verdict: bool | None = None


class PythonModUpdateRequest(BaseModel):
    """`PATCH /api/settings/mods/python/{id}`。省略した項目は変えない。

    `events` の null はマニフェストのイベントすべてに戻す。`values` は秘密でない項目の値
    (null は既定に戻す)。`secrets` は秘密の項目の値(null は消す。値は応答に返さない)。"""

    enabled: bool | None = None
    events: list[ModEvent] | None = None
    filters: ModFilters | None = None
    timeout_seconds: float | None = None
    on_failure: ModOnFailure | None = None
    wait_for_verdict: bool | None = None
    values: dict[str, Any] | None = None
    secrets: dict[str, str | None] | None = None


class ModOrderItem(BaseModel):
    kind: Literal["webhook", "python"]
    id: str


class ModOrderRequest(BaseModel):
    """`PUT /api/settings/mods/order`。この並びの順に呼ぶ(並びに無い Mod の順番は変えない)。"""

    items: list[ModOrderItem]


class ModTestRequest(BaseModel):
    """テスト送信で送るダミーのイベント。"""

    event: ModEvent = "ai.before_send"


class ModTestResponse(BaseModel):
    """テスト送信の結果。記録(`mod_invocation`)には残さない。"""

    # allow / deny / ok / error / timeout
    outcome: Literal["allow", "deny", "ok", "error", "timeout"]
    duration_ms: int
    # Webhook の HTTP の状態(Python の Mod と、届かなかったときは null)。
    status_code: int | None = None
    # 応答の本文(JSON。契約に合わなくても、読めたものを返す)。
    response: Any = None
    error: str | None = None


class ModInvocationView(BaseModel):
    id: uuid.UUID
    event: ModEvent
    event_id: uuid.UUID
    mod_kind: Literal["webhook", "python"]
    mod_id: str
    mod_name: str
    purpose: ModPurpose | None = None
    run_id: uuid.UUID | None = None
    asset_id: uuid.UUID | None = None
    outcome: Literal["allow", "deny", "ok", "error", "timeout", "skipped"]
    # `ai.before_send` で GAKEI が実際にどう扱ったか(失敗時の扱いを当てた後)。
    effective: Literal["allow", "deny"] | None = None
    reason: str | None = None
    reference: str | None = None
    error: str | None = None
    duration_ms: int
    created_at: datetime


class ModInvocationListResponse(BaseModel):
    items: list[ModInvocationView]


class ModDeliveryView(BaseModel):
    id: uuid.UUID
    event: ModEvent
    event_id: uuid.UUID
    mod_kind: Literal["webhook", "python"]
    mod_id: str
    asset_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None
    status: Literal["queued", "delivering", "done", "failed"]
    attempts: int
    last_error: str | None = None
    next_attempt_at: datetime
    created_at: datetime
    finished_at: datetime | None = None


class ModDeliveryListResponse(BaseModel):
    items: list[ModDeliveryView]


class ModVerdictReceiveResponse(BaseModel):
    """`POST /api/mods/{mod_id}/verdicts` の応答(ADR-0051 8章)。"""

    # 記録した判定の数。
    accepted: int
    # 受け付け済みの要求と同じ要求だったか。true なら何も記録せず、`accepted` は1回目の数。
    duplicate: bool = False


# -- 領域の四角の検出(ADR-0053 7章) -----------------------------------------------------


# 構造化出力に対応しない接続先も使える(スキーマを付けずに送る。ADR-0053 7章)。
RegionDetectionUnusableReason = Literal["disabled", "connection_missing", "api_key_missing"]


class RegionDetectionSettingsResponse(BaseModel):
    """`GET /api/settings/region-detection`。全ログイン者が読める(検出のボタンを出すかの判断)。"""

    enabled: bool
    connection_id: str
    model: str
    hourly_request_limit: int
    hourly_request_limit_default: int
    hourly_request_limit_max: int
    # 有効で、接続先とキーがそろう(検出のボタンを出してよい)。構造化出力への対応は問わない。
    usable: bool
    # 使えない理由(1つに絞る。使えるなら null)。見る順は、無効 → 接続先が無い → キーが無い。
    unusable_reason: RegionDetectionUnusableReason | None = None
    # 見ている利用者の、直近1時間の検出の数。
    requests_last_hour: int


class RegionDetectionSettingsUpdateRequest(BaseModel):
    """書いた項目だけ変える。null は不正な値(422)。"""

    enabled: StrictBool | None = None
    connection_id: StrictStr | None = None
    model: StrictStr | None = None
    hourly_request_limit: StrictInt | None = None


class RegionDetectRequest(BaseModel):
    """`POST /api/assets/{asset_id}/regions/detect` の本文。"""

    # 探す物(「カップ」「左の人の服」など)。無ければ主な物を一通り挙げる。
    query: str | None = Field(default=None, max_length=200)
    # 挙げる数の上限。
    max_items: int = Field(default=20, ge=1, le=20)


class DetectedRegion(BaseModel):
    """検出した四角の候補1つ。"""

    label: str
    # 主たる入力の画像に対する割合(0〜1)で、左上を原点にした [x0, y0, x1, y1](ADR-0053 3章)。
    box: list[float] = Field(min_length=4, max_length=4)


class RegionDetectUsage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None


class RegionDetectionView(BaseModel):
    """保存した検出の1回分(ADR-0053 7章の検出の履歴)。"""

    id: uuid.UUID
    # 探した言葉(無ければ null)
    query: str | None
    model: str
    created_at: datetime
    # 見つからなければ空の配列
    regions: list[DetectedRegion]


class RegionDetectResponse(BaseModel):
    """検出の結果。`detection` に保存した行を返す(`regions` と `model` はその写し)。"""

    regions: list[DetectedRegion]
    model: str
    usage: RegionDetectUsage
    detection: RegionDetectionView


class RegionDetectionListResponse(BaseModel):
    """`GET /api/assets/{asset_id}/regions/detections`。新しい順。"""

    items: list[RegionDetectionView]
