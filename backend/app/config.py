"""アプリ設定。pydantic-settings で環境変数 / .env を読む。"""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ の1つ上(リポジトリ直下)を既定の探索場所にする。
_REPO_ROOT = Path(__file__).resolve().parents[2]
_BACKEND_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    """`.env` はリポジトリ直下または backend/ のものを読む(両方あれば backend/ を優先)。"""

    model_config = SettingsConfigDict(
        env_file=(_REPO_ROOT / ".env", _BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # OpenAI 本体の呼び出し(AsyncOpenAI)に使う。
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")

    # 429 の再試行回数。SDK の既定(2)より少し多めにする。
    openai_max_retries: int = Field(default=4, alias="OPENAI_MAX_RETRIES")

    # 4K・高品質だと数分かかる前提で長めにする(秒)。
    openai_timeout_seconds: float = Field(default=600.0, alias="OPENAI_TIMEOUT_SECONDS")

    # 既定はリポジトリ直下の data/。
    data_dir: Path = Field(default=_REPO_ROOT / "data", alias="DATA_DIR")

    # ADR-0017: 主プロバイダーは常に openai。FAKE_PROVIDER=1 のときだけ fake に切り替える
    # (開発・CI・確認用の内部フラグ。利用者向けの設定一覧には載せない)。
    fake_provider: bool = Field(default=False, alias="FAKE_PROVIDER")

    # ADR-0017: OpenAI 互換のプロキシ(LiteLLM 等)を使うための接続先。未設定(None)なら
    # OpenAI 本体。画面で保存した値より優先する(`app/domain/api_key.py` の `resolve_base_url`)。
    openai_base_url: str | None = Field(default=None, alias="OPENAI_BASE_URL")

    # ADR-0017 移行の安全策専用のフィールド。`PROVIDER` はもう使わないが、設定されたまま
    # 起動されたときに検知するためだけに残す(`extra="ignore"` なので、フィールドが無いと
    # 環境変数 / `.env` の値を読み取れない)。他の用途では参照しない。
    legacy_provider: str | None = Field(default=None, alias="PROVIDER")

    host: str = Field(default="127.0.0.1", alias="HOST")
    port: int = Field(default=8000, alias="PORT")

    # Generate のとき常に送る表現の制限。フォームからは指定できず、プロバイダーの
    # finalize_params が params へ入れる(ADR-0003: params は API に送った値そのもの)。
    moderation: Literal["auto", "low"] = Field(default="low", alias="MODERATION")

    # ADR-0013 7章: ローカル ComfyUI(実験)。既定は無効(空文字)。通常は起動後の設定画面
    # (設定 → ComfyUI)から接続する。ここで指定した値は、画面で一度も設定していない間だけ
    # 使う既定値で、画面で接続・切り離しをすると以降は画面の設定が優先する(DB には
    # 保存しない)。
    comfyui_url: str = Field(default="", alias="COMFYUI_URL")
    comfyui_timeout_seconds: float = Field(default=1800.0, alias="COMFYUI_TIMEOUT_SECONDS")

    # ADR-0019: 個人モード(none、既定)か OIDC(Keycloak・Entra ID 等)か。
    auth_mode: Literal["none", "oidc"] = Field(default="none", alias="AUTH_MODE")

    # OIDC の発行者(例 https://keycloak.example.com/realms/gakei)。discovery は
    # `{issuer}/.well-known/openid-configuration` から行う(初回ログイン時、Authlib がキャッシュ)。
    oidc_issuer: str | None = Field(default=None, alias="OIDC_ISSUER")
    oidc_client_id: str | None = Field(default=None, alias="OIDC_CLIENT_ID")
    # 空(未指定)なら public client として PKCE のみで認可コード交換を行う
    # (`token_endpoint_auth_method="none"`。auth/oidc.py)。
    oidc_client_secret: str | None = Field(default=None, alias="OIDC_CLIENT_SECRET")
    oidc_scopes: str = Field(default="openid profile email", alias="OIDC_SCOPES")

    # 利用者がブラウザで開く URL。redirect_uri は `{PUBLIC_BASE_URL}/api/auth/callback`。
    # https なら Cookie に Secure を付ける(`public_base_is_https`)。
    public_base_url: str | None = Field(default=None, alias="PUBLIC_BASE_URL")

    # カンマ区切りのメールアドレス。大文字小文字は無視し、ログインのたびに再評価する
    # (IdP のクレームは使わない。ADR-0019)。
    auth_admin_emails: str = Field(default="", alias="AUTH_ADMIN_EMAILS")

    # ログインを許すメールアドレスのドメイン(カンマ区切り)。空なら制限しない。Google のような
    # 誰でもアカウントを作れる IdP では、これを指定しないと世界中の誰でもログインできてしまう
    # (ADR-0019 3章、2026-09-27 追記)。`AUTH_ADMIN_EMAILS` に載っている人は常に許す。
    auth_allowed_email_domains: str = Field(default="", alias="AUTH_ALLOWED_EMAIL_DOMAINS")

    # ログインセッション(`auth_session`)の有効期間(時間)。1時間〜30日(720時間)の範囲
    # (I-10、2026-09-27 追記)。
    auth_session_hours: int = Field(default=12, ge=1, le=24 * 30, alias="AUTH_SESSION_HOURS")

    # `gakei_oidc` Cookie(state/nonce/PKCE の一時保存)の署名鍵。未指定なら生成して
    # `DATA_DIR/secrets.json` の `auth_secret` に保存する(auth/secret.py)。
    auth_secret: str | None = Field(default=None, alias="AUTH_SECRET")

    # ADR-0021 3章: リリースワークフロー(GHCR イメージ)がビルド時に埋め込む git のコミット
    # SHA。利用者が設定するものではない(`docs/configuration.md` には載せない)。起動スクリプト
    # で動かす場合は未設定のままでよい(`app.version.get_commit` が None を返す)。
    gakei_commit: str | None = Field(default=None, alias="GAKEI_COMMIT")

    def admin_email_set(self) -> set[str]:
        """`AUTH_ADMIN_EMAILS` を小文字化・trim して集合にする。"""
        return {part.strip().lower() for part in self.auth_admin_emails.split(",") if part.strip()}

    def allowed_email_domain_set(self) -> set[str]:
        """`AUTH_ALLOWED_EMAIL_DOMAINS` を小文字化・trim して集合にする(先頭の `@` は取る)。"""
        return {
            part.strip().lower().lstrip("@")
            for part in self.auth_allowed_email_domains.split(",")
            if part.strip()
        }

    @property
    def public_base_is_https(self) -> bool:
        if not self.public_base_url:
            return False
        return urlparse(self.public_base_url).scheme == "https"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "gakei.db"

    @property
    def assets_dir(self) -> Path:
        return self.data_dir / "assets"

    @property
    def derived_dir(self) -> Path:
        return self.data_dir / "derived"

    @property
    def tmp_partial_dir(self) -> Path:
        return self.data_dir / "tmp" / "partial"


def get_settings() -> Settings:
    """設定を都度読み直す(テストで DATA_DIR を差し替えやすくするため)。"""
    return Settings()
