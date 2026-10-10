"""SQLAlchemy 2.0 モデル。
ADR-0003 のデータモデルから project_id / created_by / batch_id を除いた形。

来歴に関わる列(asset, run_input の全列、run の実行状態以外の列)は追記のみで、
UPDATE してよいのは run.status など実行状態の遷移に関わる列だけ(worker/runner.py が担う)。
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


class Base(DeclarativeBase):
    pass


# ADR-0027 2章: JSON の列は PostgreSQL では JSONB にする(`json` 型は等値比較ができず、
# DISTINCT や重複の確認で失敗するため)。SQLite ではこれまでどおり JSON(TEXT)。
JsonType = JSON().with_variant(JSONB(), "postgresql")

# ADR-0027 2章: 文字列の列の型の分け方。
# - 利用者や外部(IdP、プロバイダー、ComfyUI など)から来る文字列は、長さのない `Text`
#   (PostgreSQL では TEXT)。長さの上限が要るものは API の入力検証(Pydantic など)で弾く。
#   SQLite は VARCHAR(n) の長さを無視するので、PostgreSQL だけが失敗することを避ける。
# - コードが決める値(status、kind、role、origin、sha256 など)は `String(n)` のまま。
#   長さが決まっているので、超えたらバグとして気づける。


def _new_uuid() -> uuid.UUID:
    return uuid.uuid4()


def _utcnow() -> datetime:
    return datetime.now(UTC)


class UtcDateTime(TypeDecorator):
    """timezone-aware な UTC の datetime として読み書きする。

    SQLite は tz 情報を保持しないため、素の `DateTime(timezone=True)` だと読み出し時に
    naive になってしまい、API の JSON にオフセットが付かずフロントで時刻がずれる。
    書き込み時は UTC に正規化し、読み出し時に `tzinfo=UTC` を付け直す。
    PostgreSQL の TIMESTAMPTZ に対してもそのまま動く。
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            # このアプリは常に UTC で datetime を作る前提なので、naive は UTC とみなす。
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class AssetKind(enum.StrEnum):
    UPLOAD = "upload"
    GENERATED = "generated"
    MASK = "mask"
    SKETCH = "sketch"


class RunOperation(enum.StrEnum):
    GENERATE = "generate"
    EDIT = "edit"


class RunStatus(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


# `run.origin` の値(ADR-0023 5章、ADR-0037 2章)。null は画面、`mcp` は MCP のツール。
# `import` は、系列の ZIP から取り込んだ記録(この GAKEI では実行していない。ADR-0037)。
RUN_ORIGIN_IMPORT = "import"


class RunInputRole(enum.StrEnum):
    IMAGE = "image"
    MASK = "mask"
    REFERENCE = "reference"


class AppUser(Base):
    """OIDC でログインしたユーザー(ADR-0019)。`none` モードでは1行も作られない。

    ロール(`role`)はログインのたびに `AUTH_ADMIN_EMAILS` から再計算する(IdP のクレームは
    使わない)。`(issuer, subject)` の組がその人を一意に表す(同じメールでも IdP が違えば別人)。
    """

    __tablename__ = "app_user"
    __table_args__ = (UniqueConstraint("issuer", "subject", name="uq_app_user_issuer_subject"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_new_uuid)
    issuer: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=_utcnow)
    last_login_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=_utcnow)

    # アバター画像のハッシュ(ADR-0020)。null ならアバター無し(頭文字表示に戻る)。実体は
    # Asset ではなく `DATA_DIR/avatars/{id}.webp` の1枚(`domain/avatars.py`)なので、来歴の
    # 追記のみの規則の対象外で、差し替え・削除のたびに UPDATE してよい。
    avatar_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)


class AuthSession(Base):
    """サーバー側のログインセッション(ADR-0019)。Cookie にはランダムトークンのみを置き、
    ここに保存するのはそのハッシュ(sha256)だけ(トークン自体は保存しない)。
    """

    __tablename__ = "auth_session"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("app_user.id"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, index=True)


class ApiToken(Base):
    """MCP 用のアクセストークン(ADR-0023 2章。認証モードのみ)。DB には SHA-256 のハッシュだけを
    保存し、値そのものは発行時に1回だけ返す。証跡ではないので失効(`revoked_at`)と最終使用日時
    (`last_used_at`)は更新してよい。行は消さない(Run の `api_token_id` から参照されるため)。

    有効期限(`expires_at`。null は無期限)と権限(`scope`。`full` = すべて、`read` = 読み取り
    のみ)は発行のときに1回だけ書き、後から変えない(ADR-0023 11章)。11章より前に発行した
    トークンは無期限・`full`。
    """

    __tablename__ = "api_token"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("app_user.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=_utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    scope: Mapped[str] = mapped_column(
        String(16), nullable=False, default="full", server_default="full"
    )


class UploadTicket(Base):
    """MCP の `create_upload_url` が発行する、1回限りのアップロード URL(ADR-0023 7章 2)。

    URL に含むトークンは保存せず、SHA-256 のハッシュだけを持つ。有効期限は発行から10分。
    使ったら `used_at` と取り込んだ `asset_id` を書く(証跡ではないが、行は消さない)。
    `user_id` は発行者(個人モードは null)で、取り込んだ Asset の `created_by_user_id` になる。
    `api_token_id` は認証モードで発行に使ったトークン(失効していたら URL も無効にする)。
    """

    __tablename__ = "upload_ticket"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_new_uuid)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("app_user.id"), nullable=True
    )
    api_token_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("api_token.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, index=True)
    used_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    asset_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("asset.id"), nullable=True)


class DownloadTicket(Base):
    """MCP の `create_download_url` が発行する、原本の1回限りのダウンロード URL
    (ADR-0023 8章 3)。

    アップロード URL(`UploadTicket`)と同じ作りで、URL に含むトークンは保存せず SHA-256 の
    ハッシュだけを持つ。有効期限は発行から10分。取得したら `used_at` を書く(証跡ではないが、
    行は消さない)。`user_id` は発行者(個人モードは null)で、取得の時点でもこの利用者に
    `asset_id` が見えるかを確かめる(ADR-0025)。`api_token_id` は認証モードで発行に使った
    トークン(失効していたら URL も無効にする)。
    """

    __tablename__ = "download_ticket"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_new_uuid)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    asset_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("asset.id"), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("app_user.id"), nullable=True
    )
    api_token_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("api_token.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, index=True)
    used_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)


class Asset(Base):
    """画像そのものを表すノード。バイナリは不変。

    blob_key の決め方は ADR-0026(それより前に保存した行は ADR-0004 のキーのまま)。
    """

    __tablename__ = "asset"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_new_uuid)
    kind: Mapped[AssetKind] = mapped_column(String(16), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    blob_key: Mapped[str] = mapped_column(Text, nullable=False)
    mime: Mapped[str] = mapped_column(String(64), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    bytes: Mapped[int] = mapped_column(Integer, nullable=False)

    produced_by_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("run.id"), nullable=True, index=True
    )
    output_index: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # 上描きスケッチの下地 Asset(ADR-0010、2026-09-23 追記)。kind=sketch 以外は常に null。
    # 追記のみ(INSERT 時に設定し、UPDATE しない)。
    source_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("asset.id"), nullable=True, index=True
    )

    # ダウンロードした PNG を再アップロードしたときの由来(ADR-0014、2026-09-24 追記)。
    # kind=upload 以外は常に null。埋め込まれた `gakei` メタ情報の自己申告で、署名は無い
    # ため証跡ではない(`run_input` などには書き込まない。画面では「未検証」と表示する)。
    # 追記のみ(INSERT 時に設定し、UPDATE しない)。内容が一致した場合は新しい Asset 行を
    # 作らず既存の Asset をそのまま返すため、ここに値が入るのは一致しなかった場合だけ。
    origin_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("asset.id"), nullable=True, index=True
    )
    # 読み取った `gakei.lineage/1`・`/2` の JSON をそのまま保存する(自己申告)。
    origin_meta: Mapped[dict | None] = mapped_column(JsonType, nullable=True)

    # 埋め込まれていたグラフ(ADR-0014 6章)の root が自称する asset id への対応付け。
    # このファイルからチャンクを除いた sha256 が、その root が自称する sha256 と一致した
    # ときだけ設定する(= 改ざんされていない)。他のインスタンスの id を指すことがあるため
    # 外部キーは張らない(migration 0008)。追記のみ(INSERT 時に設定し、UPDATE しない)。
    origin_ref_asset_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)

    # 他の画像生成ツール(A1111、ComfyUI、NovelAI など)や C2PA が埋め込んだ生成メタ情報
    # (ADR-0018、2026-09-26 追記。`gakei.embedded/1` の JSON)。kind=upload 以外は常に null。
    # 署名の無い自己申告(画面では「未検証」)で、run/run_input には書き込まない。追記のみ。
    # 例外として app/tools/backfill_embedded_meta.py だけが null の行を一度だけ埋める。
    embedded_meta: Mapped[dict | None] = mapped_column(JsonType, nullable=True)

    # アップロード/マスク/スケッチを行ったユーザー(ADR-0019)。`none` モードと、生成出力
    # (produced_by_run 経由。実行者は run.created_by_user_id 側に記録する)は常に null。
    # 追記のみ(INSERT 時に設定し、UPDATE しない)。
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("app_user.id"), nullable=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=_utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)

    produced_by_run: Mapped[Run | None] = relationship(
        "Run", foreign_keys=[produced_by_run_id], back_populates="outputs"
    )
    run_inputs: Mapped[list[RunInput]] = relationship(
        "RunInput", back_populates="asset", foreign_keys="RunInput.asset_id"
    )


class Run(Base):
    """1回の API 実行を表すノード。失敗も証跡として残す。"""

    __tablename__ = "run"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_new_uuid)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    deployment: Mapped[str | None] = mapped_column(Text, nullable=True)
    operation: Mapped[RunOperation] = mapped_column(String(16), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    params: Mapped[dict] = mapped_column(JsonType, nullable=False, default=dict)

    status: Mapped[RunStatus] = mapped_column(
        String(16), nullable=False, default=RunStatus.QUEUED, index=True
    )
    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    usage: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    provider_request_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 実行時にワークフローが作ったテキスト(ADR-0030。ComfyUI の最終プロンプト)。
    # `[{"role", "node_id", "class_type", "title", "text", "truncated"?}]`。
    # 成功時に1回だけ書く(失敗した Run は null のまま)。
    text_outputs: Mapped[list | None] = mapped_column(JsonType, nullable=True)

    # 実行したユーザー(ADR-0019)。`none` モードでは常に null。追記のみ
    # (INSERT 時に設定し、UPDATE しない)。
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("app_user.id"), nullable=True, index=True
    )

    # 生成時に指定したグループ(ADR-0022 2章)。Run の作成時に一度だけ書き、UPDATE しない
    # (ADR-0003 の追記のみの規則に反しない)。成功時に worker が出力 Asset をこのグループに
    # 入れる。グループが実行までに削除されていたら入れずに成功させる。
    asset_group_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("asset_group.id"), nullable=True, index=True
    )

    # 実行元(ADR-0023 5章)。null は画面、`mcp` は MCP のツール、`import` は系列の ZIP から
    # 取り込んだ記録(ADR-0037。実行していない。元の記録は `run_import`)。`api_token_id` は MCP を
    # アクセストークンで呼んだとき(認証モード)だけ入る。いずれも作成時に一度だけ書き、UPDATE
    # しない(ADR-0003 の追記のみの規則に反しない)。
    origin: Mapped[str | None] = mapped_column(String(16), nullable=True)
    api_token_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("api_token.id"), nullable=True, index=True
    )

    queued_at: Mapped[datetime] = mapped_column(
        UtcDateTime(), nullable=False, default=_utcnow, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)

    # 論理削除。終了状態(succeeded/failed/canceled)の Run だけ削除でき、
    # 削除時に出力 Asset も同時に論理削除する(app/api/runs.py の delete_run)。
    deleted_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True, index=True)

    inputs: Mapped[list[RunInput]] = relationship(
        "RunInput",
        back_populates="run",
        foreign_keys="RunInput.run_id",
        order_by="RunInput.position",
    )
    outputs: Mapped[list[Asset]] = relationship(
        "Asset",
        back_populates="produced_by_run",
        foreign_keys="Asset.produced_by_run_id",
        order_by="Asset.output_index",
    )


class RunInput(Base):
    """Run が使った入力 Asset。role=image かつ position=0 が「主たる親」。"""

    __tablename__ = "run_input"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_new_uuid)
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("run.id"), nullable=False, index=True
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("asset.id"), nullable=False, index=True
    )
    role: Mapped[RunInputRole] = mapped_column(String(16), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=_utcnow)

    run: Mapped[Run] = relationship("Run", back_populates="inputs", foreign_keys=[run_id])
    asset: Mapped[Asset] = relationship(
        "Asset", back_populates="run_inputs", foreign_keys=[asset_id]
    )


Index("ix_run_input_run_role_position", RunInput.run_id, RunInput.role, RunInput.position)


class RunImport(Base):
    """系列の ZIP から取り込んだ Run(`run.origin = 'import'`)の、書き出し元での記録(ADR-0037 2章)。

    この GAKEI で実行していない Run なので、元の Run の ID・実行者の表示名・日時は Run の列には
    入れず、ここに置く。ZIP の自己申告で検証していない(画面でもそう示す)。来歴の列なので
    追記のみ(取り込んだときに1回だけ書き、UPDATE しない。ADR-0003)。

    同じ利用者が同じ書き出し元の Run をもう一度取り込んだときは、`source_run_id` で見つけて
    新しい Run を作らない(二重取り込みの扱い)。
    """

    __tablename__ = "run_import"

    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("run.id"), primary_key=True)
    # 書き出し元の Run の ID(別インスタンスのこともあるので外部キーは張らない)。
    source_run_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    source_creator_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_created_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    source_finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    source_gakei_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    imported_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=_utcnow)


class PromptSet(Base):
    """名前を付けたプロンプトの集まり(ADR-0009)。証跡ではないので更新・論理削除ができる。

    `run` とは外部キーを張らない。`run.prompt` は実行時の文字列をそのまま保持し、
    プロンプトセットを書き換えても消しても Run の記録は変わらない。
    """

    __tablename__ = "prompt_set"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_new_uuid)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # 作成したユーザー(ADR-0025。マイグレーション 0018)。`none` モードと、0018 より前の行は
    # null(認証モードでは管理者だけに見える)。追記のみ(INSERT 時に設定し、UPDATE しない)。
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("app_user.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=_utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)

    items: Mapped[list[PromptSetItem]] = relationship(
        "PromptSetItem", back_populates="prompt_set", order_by="PromptSetItem.position"
    )


class PromptSetItem(Base):
    """プロンプトセットの1項目。パラメーターは持たず、文字列だけを扱う。"""

    __tablename__ = "prompt_set_item"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_new_uuid)
    prompt_set_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("prompt_set.id"), nullable=False, index=True
    )
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=_utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)

    prompt_set: Mapped[PromptSet] = relationship("PromptSet", back_populates="items")


class ParameterSet(Base):
    """パラメーターセット(生成のフォームの設定一式に名前を付けたもの。ADR-0040)。

    証跡ではないので名前と中身を更新でき、削除は論理削除。`run` とは外部キーを張らない
    (Run には実行時の値がこれまでどおり残る。ADR-0003)。`params` にはフォームの値だけを入れ、
    サーバーだけが書く項目(`comfyui_*`、`sdwebui_*`)は入れない(API で 422)。
    `provider` は登録簿にあるかを保存時には問わない(後で無効になることもあるため。読み込む側で
    判定する)。
    """

    __tablename__ = "parameter_set"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_new_uuid)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    # 保存しなければ null(読み込み時は今のモデル・プロンプトのまま)。
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    params: Mapped[dict] = mapped_column(JsonType, nullable=False, default=dict)
    # 作成したユーザー(ADR-0025)。`none` モードでは null。追記のみ(INSERT 時に設定し、
    # UPDATE しない)。
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("app_user.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime(), nullable=False, default=_utcnow, index=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)


class AssetGroup(Base):
    """グループ(ストックの手動整理。ADR-0022)。証跡ではないので更新・論理削除ができる。

    メンバーは `AssetGroupMember`。1 つの Asset が属するグループは 1 つだけ
    (2026-09-28 に多対多から変更)。階層・入れ子は持たない。
    並び順は利用者が決める(`position`。2026-09-28 追加)。
    表紙画像は列に持たず、一覧の応答でメンバーの `added_at` が最新のものから都度求める。
    """

    __tablename__ = "asset_group"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_new_uuid)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # 利用者が決める並び順(小さいほど上。ADR-0022 2章)。一覧は `position ASC, created_at DESC`。
    # 新しいグループは既存の最小値 − 1 で先頭に入り、並べ替えで 0 から振り直す。
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 作成したユーザー(ADR-0019)。`none` モードでは常に null。追記のみ
    # (INSERT 時に設定し、UPDATE しない)。
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("app_user.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=_utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)


class AssetGroupMember(Base):
    """グループのメンバー。証跡ではないので、外すときは物理削除する
    (いつ誰が外したかは残らない。ADR-0022)。

    1 つの Asset が属するグループは 1 つだけ(2026-09-28 に多対多から変更)。
    グループに入れる操作は常に「移す」で、既存の行を消してから入れる
    (削除済みグループに残った行も含む。`app/domain/asset_groups.add_members`)。
    """

    __tablename__ = "asset_group_member"
    # 1 つの Asset は 1 つのグループにだけ入る(ADR-0022 2章。マイグレーション 0015)。
    # 主キー (asset_group_id, asset_id) は残し、asset_id 単独の一意索引で所属を 1 行に限る。
    __table_args__ = (Index("ux_asset_group_member_asset_id", "asset_id", unique=True),)

    asset_group_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("asset_group.id"), primary_key=True
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("asset.id"), primary_key=True)
    added_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=_utcnow)


class AppSetting(Base):
    """`DATA_DIR` ごとの画面設定(キーと値)。証跡ではないので更新してよい(ADR-0013 7章)。

    キー `comfyui.connection` に ComfyUI の接続設定(`{"url": "<url>" | None}`)を持つ。
    `value["url"] is None` は「切り離した」ことを明示的に保存した状態を表す
    (環境変数 `COMFYUI_URL` があっても、再起動で再び有効にはならない)。
    """

    __tablename__ = "app_setting"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JsonType, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=_utcnow)


class ComfyWorkflow(Base):
    """ローカル ComfyUI のワークフロー登録(ADR-0013)。証跡ではないので更新・論理削除できる。

    `run` とは外部キーを張らない。実行時は `run.params["comfyui_prompt"]` に値を差し込んだ
    グラフ全体をそのまま保存するため、ワークフローを後で編集・削除しても過去の Run と
    再実行の内容は変わらない。
    """

    __tablename__ = "comfy_workflow"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_new_uuid)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    operation: Mapped[RunOperation] = mapped_column(String(16), nullable=False)
    # API 形式のグラフ({node_id: {class_type, inputs, _meta?}})。ComfyUI の
    # 「Export (API)」の書き出しそのままを保存する。
    template: Mapped[dict] = mapped_column(JsonType, nullable=False)
    # domain/comfy_workflow.py の Bindings を model_dump(mode="json") した dict。
    bindings: Mapped[dict] = mapped_column(JsonType, nullable=False)
    # domain/comfy_workflow.py の ExposedParam のリストを model_dump(mode="json") した list。
    exposed_params: Mapped[list] = mapped_column(JsonType, nullable=False, default=list)
    template_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=_utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)


class AssetAnnotation(Base):
    """Asset のタイトルと自動推定の状態(ADR-0024 2章)。証跡ではないので更新してよい。

    `auto_status` が推定の待ち行列を兼ねる(`queued` の行を `app/worker/annotator.py` が
    1件ずつ処理する。ADR-0024 4章)。NULL は未実行。
    """

    __tablename__ = "asset_annotation"

    asset_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("asset.id"), primary_key=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    # auto | user。user になったタイトルは再推定で上書きしない。
    title_source: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # queued | running | succeeded | failed(NULL は未実行)
    auto_status: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    # 実行したエンジン(例 `llm+vlm+onnx`)
    auto_engines: Mapped[str | None] = mapped_column(String(32), nullable=True)
    auto_models: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    auto_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    auto_requested_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    auto_finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=_utcnow)


class Tag(Base):
    """タグの表記(ADR-0024 2章)。`name` は `app/domain/annotations.normalize_tag_name` で
    正規化した値(NFKC、前後の空白除去、小文字化、空白の連続を1つに)。"""

    __tablename__ = "tag"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_new_uuid)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=_utcnow)


class AssetTag(Base):
    """Asset に付いたタグ(ADR-0024 2章)。証跡ではないので更新してよい。

    人が消したタグは行を残して `removed = True`・`source = user` にする(再推定で付け直さない
    ため)。画面・API には出さない。
    """

    __tablename__ = "asset_tag"

    asset_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("asset.id"), primary_key=True)
    tag_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tag.id"), primary_key=True, index=True
    )
    # auto | user
    source: Mapped[str] = mapped_column(String(8), nullable=False)
    # ONNX タガーの確信度(VLM と人は NULL)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    removed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=_utcnow)


class AssetEmbedding(Base):
    """Asset の埋め込みベクトル(ADR-0033 4章)。証跡ではないので更新・削除してよい。

    (Asset、`model_key`)ごとに1行。`status` が計算の待ち行列を兼ねる(`queued` の行を
    `app/worker/embedder.py` が処理する。ADR-0033 5章)。`vector` は float32 リトルエンディアンで
    L2 正規化済みのベクトルで、これが正本。

    PostgreSQL で拡張 `vector` が使えるときは `embedding vector` 列もある(同じ値。検索の索引に
    使う)。SQLite と同じモデルを保つため、その列は ORM には載せず SQL で読み書きする
    (`app/domain/embedding_index.py`)。
    """

    __tablename__ = "asset_embedding"
    __table_args__ = (Index("ix_asset_embedding_model_key_status", "model_key", "status"),)

    asset_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("asset.id"), primary_key=True)
    # ベクトル空間の識別子(`onnx:<モデル>@<リビジョン>`、`remote:<接続先>:<モデル>`)。
    model_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    # queued | running | succeeded | failed
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    dim: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vector: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=_utcnow)


class AssetPerceptualHash(Base):
    """Asset の知覚ハッシュ(ADR-0033 12章)。重複の候補の判定に、CLIP の類似度と併せて使う。

    thumb から作り、Asset の原本は不変なので一度作れば変わらない。証跡ではないので、作り直し
    (`version` が古いとき)や削除をしてよい。値の形は `app/domain/perceptual_hash.py`。
    埋め込みの worker が、その Asset の埋め込みを計算するときに作る(`app/worker/embedder.py`)。
    """

    __tablename__ = "asset_perceptual_hash"

    asset_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("asset.id"), primary_key=True)
    # 差分ハッシュ(64 ビット、ビッグエンディアンの 8 バイト)。PostgreSQL の BIGINT は符号付き
    # なので、整数ではなくバイト列で持つ。
    dhash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    # 色(4×4 の Lab と色相のヒストグラム。64 バイト)。
    color: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    # アルゴリズムの版(`perceptual_hash.ALGORITHM_VERSION`)。違えば作り直す。
    version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=_utcnow)


class Share(Base):
    """ログイン不要の共有リンク(ADR-0029)。証跡ではないので、取り消し(`revoked_at`。論理削除)
    と、開かれた日時・回数(`last_accessed_at` / `access_count`)は更新してよい。行は消さない。

    `token` は URL(`/s/{token}`)に含める乱数そのもの。共有した人があとからリンクをコピーできる
    よう、ハッシュにせず平文で持つ(ADR-0029 1章)。含まれる Asset は作成時に固定し
    (`ShareAsset`)、そのあと作った子孫は載せない(2章)。
    """

    __tablename__ = "share"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_new_uuid)
    token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    root_asset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("asset.id"), nullable=False, index=True
    )
    # single | ancestors | lineage
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    allow_original: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # 共有した人(ADR-0019)。個人モードでは null。追記のみ(INSERT 時に設定し、UPDATE しない)。
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("app_user.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=_utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    last_accessed_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    access_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ShareAsset(Base):
    """共有に含まれる Asset(ADR-0029 2章)。作成時に書き、UPDATE しない。

    `depth` は作成時の系列グラフでの深さ(起点 0、祖先が負、子孫が正)。共有のページで
    グラフを段組みするのに使う。Run は持たない(含まれる Asset の `produced_by_run_id` から
    引く。Run と run_input は追記のみなので、Asset の一覧が決まれば Run も一意に決まる)。
    """

    __tablename__ = "share_asset"

    share_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("share.id"), primary_key=True)
    asset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("asset.id"), primary_key=True, index=True
    )
    depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


# ADR-0041 1章: タグ辞書の検索用の列(小文字にしたタグ名など)。前方一致を通常の索引の範囲検索
# (`>= 'abc' AND < 'abd'`)で引くので、PostgreSQL では照合順序を "C"(バイト順)にする
# (既定のロケールの照合では、記号を飛ばして並べるなどで範囲が前方一致と一致しないため)。
# SQLite の既定(BINARY)はもともとバイト順。
SearchKeyText = Text().with_variant(Text(collation="C"), "postgresql")


class TagDictionary(Base):
    """登録したタグ辞書(ADR-0041 1章)。インスタンス全体で共有し、管理者だけが登録・削除する。

    証跡ではないので、有効/無効を変えられ、削除は物理削除(中身の行も消す)。アップロードした
    ファイルそのものは保存しない。`kind` は `tags`(タグの一覧)か `translations`(訳)。
    `status` は `importing` → `ready` | `failed`。候補と訳に使うのは `ready` で有効なものだけ。
    """

    __tablename__ = "tag_dictionary"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_new_uuid)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    # タグの一覧のカテゴリーの番号の体系(`danbooru` など)。訳の辞書では null。
    category_scheme: Mapped[str | None] = mapped_column(String(32), nullable=True)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("app_user.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)


class TagDictionaryEntry(Base):
    """タグの一覧の1行。`name` は辞書の表記のまま(`_` 区切り)、`name_key` は検索用
    (NFKC・小文字・空白を `_` に)。主キーの索引 (dictionary_id, name_key) で前方一致を引く。"""

    __tablename__ = "tag_dictionary_entry"

    dictionary_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tag_dictionary.id", ondelete="CASCADE"), primary_key=True
    )
    name_key: Mapped[str] = mapped_column(SearchKeyText, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[int | None] = mapped_column(Integer, nullable=True)
    post_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class TagDictionaryAlias(Base):
    """タグの一覧の別名(1行に複数ある別名を1つずつ)。`alias_key` は `name_key` と同じ正規化。"""

    __tablename__ = "tag_dictionary_alias"

    dictionary_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tag_dictionary.id", ondelete="CASCADE"), primary_key=True
    )
    alias_key: Mapped[str] = mapped_column(SearchKeyText, primary_key=True)
    name_key: Mapped[str] = mapped_column(SearchKeyText, primary_key=True)
    alias: Mapped[str] = mapped_column(Text, nullable=False)


class TagDictionaryTranslation(Base):
    """訳の1行。`translation` は代表の訳(先頭)、`translations` は欄の全体(カンマ区切り)。
    `search_key` は訳のそれぞれを正規化して `,訳1,訳2,` の形にしたもの(訳の前方一致・部分一致に
    使う。全件を走査するが、訳の辞書は数万行なので十分速い)。"""

    __tablename__ = "tag_dictionary_translation"

    dictionary_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tag_dictionary.id", ondelete="CASCADE"), primary_key=True
    )
    name_key: Mapped[str] = mapped_column(SearchKeyText, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    translation: Mapped[str] = mapped_column(Text, nullable=False)
    translations: Mapped[str] = mapped_column(Text, nullable=False)
    search_key: Mapped[str] = mapped_column(SearchKeyText, nullable=False)
