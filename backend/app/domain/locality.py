"""送り先の区分(`locality`。ADR-0051 3章)。

送り先がローカル(組織の中)かクラウド(組織の外)かを、管理者が設定で決める。GAKEI は自動で
判定せず、この値で動作も変えない。`ai.before_send` の `target` に載せて、判断は Mod に任せる。

設定する場所と保存先:

- 画像のプロバイダーの接続(OpenAI、ComfyUI、SD WebUI): `app_setting` の `providers.locality`
  (`{"openai": "cloud", ...}`)。
- ComfyUI のワークフロー: `comfy_workflow.locality`。
- LLM・VLM の接続先(ADR-0032): 接続先の一覧の各項目の `locality`。リモートの埋め込みの接続先も
  同じ一覧から選ぶので、ここで決まる。組み込みの接続先(OpenAI の設定)は、画像のプロバイダーの
  OpenAI と同じ値を使う(同じ接続のため)。

既定値と優先順:

- OpenAI の公式の接続先(Base URL を変えていない)は `cloud`。それ以外はすべて `unset`。
- ワークフローの値は、`unset` でなければ接続の値より優先する。
- どこで決まった値かを `locality_source`(`workflow` / `connection` / `default`)として返す。
  接続で `unset` を選んだときも、値が決まっていないので `default` にする。
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

Locality = Literal["local", "cloud", "unset"]
LocalitySource = Literal["workflow", "connection", "default"]
LOCALITIES: tuple[str, ...] = get_args(Locality)
UNSET: Locality = "unset"

PROVIDERS_KEY = "providers.locality"
# 区分を設定できる画像のプロバイダー。
PROVIDERS: tuple[str, ...] = ("openai", "comfyui", "sdwebui")

OPENAI_OFFICIAL_HOST = "api.openai.com"


class LocalityValidationError(GeneralSettingsValidationError):
    """値が `local` / `cloud` / `unset` のどれでもない(API 層で 422 にする)。"""


def normalize(value: object) -> Locality:
    if value not in LOCALITIES:
        raise LocalityValidationError(t("settings.locality.invalid"))
    return value  # type: ignore[return-value]


def normalize_or_unset(value: object) -> Locality:
    """保存された値を読む(壊れていれば `unset`)。"""
    return value if value in LOCALITIES else UNSET  # type: ignore[return-value]


# -- 画像のプロバイダーの接続 --------------------------------------------------------


def _saved_provider_values(db: Session) -> dict[str, Locality]:
    raw = _get_raw_value(db, PROVIDERS_KEY)
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if k in PROVIDERS and v in LOCALITIES}  # type: ignore[misc]


def openai_is_official(settings: Settings) -> bool:
    """OpenAI の接続先が公式のもの(Base URL を変えていない)か。"""
    base_url, _ = api_key_domain.resolve_base_url(settings)
    return base_url is None


def default_provider_locality(provider: str, settings: Settings) -> Locality:
    if provider == "openai" and openai_is_official(settings):
        return "cloud"
    return UNSET


def provider_locality(
    db: Session, settings: Settings, provider: str
) -> tuple[Locality, LocalitySource]:
    """プロバイダーの接続の区分と、どこで決まったか。保存した値が `unset` なら既定と同じ扱い
    (`default`)にはせず、`unset` のまま返す(管理者が決めていないことを明示した)。"""
    saved = _saved_provider_values(db).get(provider)
    if saved is not None and saved != UNSET:
        return saved, "connection"
    if saved == UNSET:
        return UNSET, "default"
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
    connection: tuple[Locality, LocalitySource], workflow: Locality | None = None
) -> tuple[Locality, LocalitySource]:
    """ワークフローの値(`unset` でなければ)を接続の値より優先する。"""
    if workflow is not None and workflow != UNSET:
        return workflow, "workflow"
    return connection


def connection_locality(
    db: Session,
    settings: Settings,
    *,
    builtin: bool,
    value: str | None,
) -> tuple[Locality, LocalitySource]:
    """LLM・VLM・埋め込みの接続先の区分。組み込みの接続先は OpenAI の接続の値。"""
    if builtin:
        return provider_locality(db, settings, "openai")
    locality = normalize_or_unset(value)
    if locality == UNSET:
        return UNSET, "default"
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
