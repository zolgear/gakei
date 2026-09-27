"""SQLAlchemy 2.0 モデル。
ADR-0003 のデータモデルから project_id / created_by / batch_id を除いた形。

来歴に関わる列(asset, run_input の全列、run の実行状態以外の列)は追記のみで、
UPDATE してよいのは run.status など実行状態の遷移に関わる列だけ(worker/runner.py が担う)。
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


class Base(DeclarativeBase):
    pass


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
    issuer: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
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


class Asset(Base):
    """画像そのものを表すノード。バイナリは不変で、blob_key は内容のハッシュから決まる。"""

    __tablename__ = "asset"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_new_uuid)
    kind: Mapped[AssetKind] = mapped_column(String(16), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    blob_key: Mapped[str] = mapped_column(String(255), nullable=False)
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
    origin_meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # 埋め込まれていたグラフ(ADR-0014 6章)の root が自称する asset id への対応付け。
    # このファイルからチャンクを除いた sha256 が、その root が自称する sha256 と一致した
    # ときだけ設定する(= 改ざんされていない)。他のインスタンスの id を指すことがあるため
    # 外部キーは張らない(migration 0008)。追記のみ(INSERT 時に設定し、UPDATE しない)。
    origin_ref_asset_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)

    # 他の画像生成ツール(A1111、ComfyUI、NovelAI など)や C2PA が埋め込んだ生成メタ情報
    # (ADR-0018、2026-09-26 追記。`gakei.embedded/1` の JSON)。kind=upload 以外は常に null。
    # 署名の無い自己申告(画面では「未検証」)で、run/run_input には書き込まない。追記のみ。
    # 例外として app/tools/backfill_embedded_meta.py だけが null の行を一度だけ埋める。
    embedded_meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)

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
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    deployment: Mapped[str | None] = mapped_column(String(64), nullable=True)
    operation: Mapped[RunOperation] = mapped_column(String(16), nullable=False)
    prompt: Mapped[str] = mapped_column(String(32_000), nullable=False)
    params: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    status: Mapped[RunStatus] = mapped_column(
        String(16), nullable=False, default=RunStatus.QUEUED, index=True
    )
    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    provider_request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # 実行したユーザー(ADR-0019)。`none` モードでは常に null。追記のみ
    # (INSERT 時に設定し、UPDATE しない)。
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("app_user.id"), nullable=True, index=True
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


class PromptSet(Base):
    """名前を付けたプロンプトの集まり(ADR-0009)。証跡ではないので更新・論理削除ができる。

    `run` とは外部キーを張らない。`run.prompt` は実行時の文字列をそのまま保持し、
    プロンプトセットを書き換えても消しても Run の記録は変わらない。
    """

    __tablename__ = "prompt_set"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_new_uuid)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
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
    label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    text: Mapped[str] = mapped_column(String(32_000), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=_utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)

    prompt_set: Mapped[PromptSet] = relationship("PromptSet", back_populates="items")


class AssetGroup(Base):
    """グループ(ストックの手動整理。ADR-0022)。証跡ではないので更新・論理削除ができる。

    メンバーはフラットな多対多(`AssetGroupMember`)。階層・入れ子・並べ替えは持たない。
    表紙画像は列に持たず、一覧の応答でメンバーの `added_at` が最新のものから都度求める。
    """

    __tablename__ = "asset_group"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_new_uuid)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # 作成したユーザー(ADR-0019)。`none` モードでは常に null。追記のみ
    # (INSERT 時に設定し、UPDATE しない)。
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("app_user.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=_utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)


class AssetGroupMember(Base):
    """グループのメンバー(多対多)。証跡ではないので、外すときは物理削除する
    (いつ誰が外したかは残らない。ADR-0022)。
    """

    __tablename__ = "asset_group_member"

    asset_group_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("asset_group.id"), primary_key=True
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("asset.id"), primary_key=True, index=True
    )
    added_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=_utcnow)


class AppSetting(Base):
    """`DATA_DIR` ごとの画面設定(キーと値)。証跡ではないので更新してよい(ADR-0013 7章)。

    キー `comfyui.connection` に ComfyUI の接続設定(`{"url": "<url>" | None}`)を持つ。
    `value["url"] is None` は「切り離した」ことを明示的に保存した状態を表す
    (環境変数 `COMFYUI_URL` があっても、再起動で再び有効にはならない)。
    """

    __tablename__ = "app_setting"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=_utcnow)


class ComfyWorkflow(Base):
    """ローカル ComfyUI のワークフロー登録(ADR-0013)。証跡ではないので更新・論理削除できる。

    `run` とは外部キーを張らない。実行時は `run.params["comfyui_prompt"]` に値を差し込んだ
    グラフ全体をそのまま保存するため、ワークフローを後で編集・削除しても過去の Run と
    再実行の内容は変わらない。
    """

    __tablename__ = "comfy_workflow"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_new_uuid)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    operation: Mapped[RunOperation] = mapped_column(String(16), nullable=False)
    # API 形式のグラフ({node_id: {class_type, inputs, _meta?}})。ComfyUI の
    # 「Export (API)」の書き出しそのままを保存する。
    template: Mapped[dict] = mapped_column(JSON, nullable=False)
    # domain/comfy_workflow.py の Bindings を model_dump(mode="json") した dict。
    bindings: Mapped[dict] = mapped_column(JSON, nullable=False)
    # domain/comfy_workflow.py の ExposedParam のリストを model_dump(mode="json") した list。
    exposed_params: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    template_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=_utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
