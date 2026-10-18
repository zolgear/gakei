"""接続先(`locality`。ADR-0051 3章)。

送り先が外部(組織の外のサービス)かローカル(組織の中)かを、管理者が設定で決める。GAKEI は
自動で判定せず、この値で動作も変えない。`ai.before_send` の `target` に載せて、判断は Mod に任せる。

設定する場所と保存先:

- 画像のプロバイダーの接続(OpenAI、ComfyUI、SD WebUI): `app_setting` の `providers.locality`
  (`{"openai": "external", ...}`)。
- ComfyUI のワークフロー: `comfy_workflow.locality`。
- LLM・VLM の接続先(ADR-0032): 接続先の一覧の各項目の `locality`。リモートの埋め込みの接続先も
  同じ一覧から選ぶので、ここで決まる。組み込みの接続先(OpenAI の設定)は、画像のプロバイダーの
  OpenAI と同じ値を使う(同じ接続のため)。

既定値と優先順:

- 接続の既定はすべて `external`(決めていない接続を、安全な側に倒す)。
- ワークフローの既定は「接続の設定に従う」(保存する値は `unset`)。`external` か `local` を
  選べば接続の値より優先する。`target.locality` に `unset` は載らない。
- どこで決まった値かを `locality_source`(`workflow` / `connection` / `default`)として返す。
- 2026-10-18 より前の値(`cloud` と、接続の `unset`)はマイグレーション 0038 で移した。読むときも
  `cloud` は `external`、それ以外の知らない値は既定として扱う。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, get_args
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.domain import api_key as api_key_domain
from app.domain.general_settings import (
    GeneralSettingsValidationError,
    _delete,
    _get_raw_value,
    _save,
)
from app.i18n import t

if TYPE_CHECKING:
    from app.config import Settings

# 接続の値(と `target.locality`)。並びは画面の選択肢の順(外部 → ローカル)。
Locality = Literal["external", "local"]
# ワークフローの値。`unset` は「接続の設定に従う」。
WorkflowLocality = Literal["external", "local", "unset"]
LocalitySource = Literal["workflow", "connection", "default"]
LOCALITIES: tuple[str, ...] = get_args(Locality)
WORKFLOW_LOCALITIES: tuple[str, ...] = get_args(WorkflowLocality)
DEFAULT: Locality = "external"
UNSET: WorkflowLocality = "unset"
# 旧い値の読み替え(マイグレーション 0038 より前の値が残っていても読めるように)。
_LEGACY: dict[str, Locality] = {"cloud": "external"}

PROVIDERS_KEY = "providers.locality"
# 接続先を設定できる画像のプロバイダー。
PROVIDERS: tuple[str, ...] = ("openai", "comfyui", "sdwebui")

OPENAI_OFFICIAL_HOST = "api.openai.com"


class LocalityValidationError(GeneralSettingsValidationError):
    """値が `external` / `local`(ワークフローは `unset` も)のどれでもない(API 層で 422 にする)。"""


def normalize(value: object) -> Locality:
    if value not in LOCALITIES:
        raise LocalityValidationError(t("settings.locality.invalid"))
    return value  # type: ignore[return-value]


def read_saved(value: object) -> Locality | None:
    """保存された接続の値を読む。旧い `cloud` は `external`。それ以外の知らない値(旧い `unset`
    や壊れた値)は None(= 既定を使う)。"""
    if isinstance(value, str):
        value = _LEGACY.get(value, value)
    return value if value in LOCALITIES else None  # type: ignore[return-value]


def read_workflow(value: object) -> WorkflowLocality:
    """保存されたワークフローの値を読む。旧い `cloud` は `external`。壊れていれば `unset`
    (接続の設定に従う)。"""
    return read_saved(value) or UNSET


# -- 画像のプロバイダーの接続 --------------------------------------------------------


def _saved_provider_values(db: Session) -> dict[str, Locality]:
    raw = _get_raw_value(db, PROVIDERS_KEY)
    if not isinstance(raw, dict):
        return {}
    result: dict[str, Locality] = {}
    for key, value in raw.items():
        locality = read_saved(value)
        if key in PROVIDERS and locality is not None:
            result[key] = locality
    return result


def default_provider_locality(provider: str, settings: Settings | None = None) -> Locality:
    """接続の既定。OpenAI の公式かどうかで変えず、すべて `external`。"""
    return DEFAULT


def provider_locality(
    db: Session, settings: Settings | None, provider: str
) -> tuple[Locality, LocalitySource]:
    """プロバイダーの接続の値と、どこで決まったか。"""
    saved = _saved_provider_values(db).get(provider)
    if saved is not None:
        return saved, "connection"
    return default_provider_locality(provider, settings), "default"


def saved_provider_locality(db: Session, provider: str) -> Locality | None:
    """画面で保存した値(無ければ None = 既定を使う)。"""
    return _saved_provider_values(db).get(provider)


def save_provider_localities(db: Session, updates: dict[str, Locality | None]) -> None:
    """プロバイダーごとに保存する。None は保存した値を消す(既定に戻す)。先に全部を検証する。"""
    for provider, value in updates.items():
        if provider not in PROVIDERS:
            raise LocalityValidationError(t("settings.locality.invalid"))
        if value is not None:
            normalize(value)
    current: dict[str, str] = dict(_saved_provider_values(db))
    for provider, value in updates.items():
        if value is None:
            current.pop(provider, None)
        else:
            current[provider] = value
    if current:
        _save(db, PROVIDERS_KEY, current)
    else:
        _delete(db, PROVIDERS_KEY)


# -- 送り先の解決 ---------------------------------------------------------------------


def resolve(
    connection: tuple[Locality, LocalitySource], workflow: WorkflowLocality | None = None
) -> tuple[Locality, LocalitySource]:
    """ワークフローの値(`unset` でなければ)を接続の値より優先する。"""
    if workflow is not None and workflow != UNSET:
        return workflow, "workflow"  # type: ignore[return-value]
    return connection


def connection_locality(
    db: Session,
    settings: Settings | None,
    *,
    builtin: bool,
    value: str | None,
) -> tuple[Locality, LocalitySource]:
    """LLM・VLM・埋め込みの接続先の値。組み込みの接続先は OpenAI の接続の値。"""
    if builtin:
        return provider_locality(db, settings, "openai")
    locality = read_saved(value)
    if locality is None:
        return DEFAULT, "default"
    return locality, "connection"


def host_of(url: str | None) -> str | None:
    """接続先のホスト名(ポートを含めない)。URL が無ければ None。"""
    if not url:
        return None
    try:
        return urlparse(url).hostname
    except ValueError:
        return None


def openai_host(settings: Settings) -> str:
    base_url, _ = api_key_domain.resolve_base_url(settings)
    return host_of(base_url) or OPENAI_OFFICIAL_HOST
