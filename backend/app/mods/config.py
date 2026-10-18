"""Mod の設定の読み書き(ADR-0051 6章・7章)。

管理者設定に保存するもの(`app_setting`。証跡ではないので更新してよい):

- `mods.instance_name`: イベントの `instance.name`(管理者が決めた名前。任意)。
- `mods.webhooks`: Webhook の一覧(名前、URL、有効、購読するイベント、絞り込み、画像を送るか、
  待つ時間、失敗時の扱い、順番)。
- `mods.python`: Python の Mod ごとの設定(有効、購読するイベント、絞り込み、待つ時間、失敗時の
  扱い、順番、設定の項目の値)。Mod の id ごと。マニフェストが消えても設定は残す。

鍵(Webhook の署名の鍵、Python の Mod の秘密の項目)は DB に入れず、`DATA_DIR/secrets.json` に
置く(`app/domain/api_key.py` と同じファイル・同じ書き込み)。値は一部も API の応答に出さない。

順番(`order`)は Webhook と Python の Mod で共通の整数で、小さいものから呼ぶ。同じ値なら種類と
id の順。
"""

from __future__ import annotations

import math
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal
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
from app.mods import api as contract

INSTANCE_NAME_KEY = "mods.instance_name"
WEBHOOKS_KEY = "mods.webhooks"
PYTHON_KEY = "mods.python"
_WEBHOOK_SECRET_PREFIX = "mod_webhook_secret."
_PYTHON_SECRET_PREFIX = "mod_python_secret."

OnFailure = Literal["block", "allow"]
ON_FAILURE_VALUES: tuple[str, ...] = ("block", "allow")

DEFAULT_TIMEOUT_SECONDS = 10.0
TIMEOUT_MIN_SECONDS = 1.0
TIMEOUT_MAX_SECONDS = 300.0
NAME_MAX = 100
INSTANCE_NAME_MAX = 100
URL_MAX = 2000
SECRET_MAX = 1000
WEBHOOKS_MAX = 50
SETTING_VALUE_MAX = 4000

KIND_WEBHOOK = "webhook"
KIND_PYTHON = "python"

_MOD_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")


class ModValidationError(GeneralSettingsValidationError):
    """保存しようとした値が不正(API 層で 422 にする)。"""


class ModNotFoundError(Exception):
    """指定した Mod が無い(API 層で 404)。"""


# -- 型 ------------------------------------------------------------------------


@dataclass(frozen=True)
class Filters:
    """購読の絞り込み。空なら絞らない。`providers` は `ai.before_send` の送り先、`run.finished`
    の Run、生成の出力の `asset.ingested` の Run のプロバイダーに当てる。"""

    purposes: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    providers: tuple[str, ...] = ()


@dataclass(frozen=True)
class CommonConfig:
    """Webhook と Python の Mod に共通の項目。`events` が None なら(Python の Mod で)マニフェストの
    イベントすべて。"""

    enabled: bool = False
    events: tuple[str, ...] | None = None
    filters: Filters = field(default_factory=Filters)
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    on_failure: OnFailure = "block"
    # 「判定を待つ」(ADR-0051 2章)。`asset.ingested` と `run.finished` を、原本を書く前に同期で
    # 呼ぶ(`app/mods/gate.py`)。
    wait_for_verdict: bool = False
    order: int | None = None


@dataclass(frozen=True)
class WebhookConfig:
    id: str
    name: str
    url: str
    common: CommonConfig
    send_images: bool = False


@dataclass(frozen=True)
class PythonModConfig:
    mod_id: str
    common: CommonConfig
    # 秘密でない設定の項目の値(秘密の項目は secrets.json)。
    values: dict[str, Any] = field(default_factory=dict)


# -- 検証 --------------------------------------------------------------------


def is_valid_mod_id(value: object) -> bool:
    return isinstance(value, str) and bool(_MOD_ID_RE.match(value))


def normalize_name(value: object) -> str:
    if not isinstance(value, str) or not (0 < len(value.strip()) <= NAME_MAX):
        raise ModValidationError(t("mods.invalidName", max=NAME_MAX))
    return value.strip()


def normalize_instance_name(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value.strip()) > INSTANCE_NAME_MAX:
        raise ModValidationError(t("mods.invalidInstanceName", max=INSTANCE_NAME_MAX))
    return value.strip() or None


def normalize_url(value: object) -> str:
    """Webhook の URL。http / https で、ユーザー情報とフラグメントを含まないもの。"""
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > URL_MAX:
        raise ModValidationError(t("mods.invalidUrl"))
    url = value.strip()
    try:
        parsed = urlparse(url)
    except ValueError as e:
        raise ModValidationError(t("mods.invalidUrl")) from e
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ModValidationError(t("mods.invalidUrl"))
    if parsed.username or parsed.password or parsed.fragment:
        raise ModValidationError(t("mods.invalidUrl"))
    return url


def normalize_secret(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > SECRET_MAX:
        raise ModValidationError(t("mods.invalidSecret", max=SECRET_MAX))
    return value.strip()


def _normalize_choices(
    value: object, allowed: tuple[str, ...], message: Callable[[], str]
) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list | tuple):
        raise ModValidationError(message())
    result: list[str] = []
    for item in value:
        if item not in allowed:
            raise ModValidationError(message())
        if item not in result:
            result.append(item)
    return tuple(result)


def _invalid_filters() -> str:
    return t("mods.invalidFilters")


def normalize_events(value: object, allowed: tuple[str, ...] = contract.EVENTS) -> tuple[str, ...]:
    return _normalize_choices(value, allowed, lambda: t("mods.invalidEvents"))


def normalize_filters(value: object) -> Filters:
    if value is None:
        return Filters()
    if not isinstance(value, dict):
        raise ModValidationError(t("mods.invalidFilters"))
    providers_raw = value.get("providers")
    providers: list[str] = []
    if providers_raw is not None:
        if not isinstance(providers_raw, list | tuple):
            raise ModValidationError(t("mods.invalidFilters"))
        for item in providers_raw:
            if not isinstance(item, str) or not item.strip() or len(item) > 64:
                raise ModValidationError(t("mods.invalidFilters"))
            if item.strip() not in providers:
                providers.append(item.strip())
    return Filters(
        purposes=_normalize_choices(value.get("purposes"), contract.PURPOSES, _invalid_filters),
        sources=_normalize_choices(value.get("sources"), contract.SOURCES, _invalid_filters),
        providers=tuple(providers),
    )


def normalize_timeout(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or math.isnan(value):
        raise ModValidationError(
            t("mods.invalidTimeout", min=TIMEOUT_MIN_SECONDS, max=TIMEOUT_MAX_SECONDS)
        )
    if not (TIMEOUT_MIN_SECONDS <= float(value) <= TIMEOUT_MAX_SECONDS):
        raise ModValidationError(
            t("mods.invalidTimeout", min=TIMEOUT_MIN_SECONDS, max=TIMEOUT_MAX_SECONDS)
        )
    return float(value)


def normalize_on_failure(value: object) -> OnFailure:
    if value not in ON_FAILURE_VALUES:
        raise ModValidationError(t("mods.invalidOnFailure"))
    return value  # type: ignore[return-value]


def _normalize_order(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ModValidationError(t("mods.invalidOrder"))
    return value


def filters_to_json(filters: Filters) -> dict[str, list[str]]:
    return {
        "purposes": list(filters.purposes),
        "sources": list(filters.sources),
        "providers": list(filters.providers),
    }


def _common_to_json(common: CommonConfig) -> dict[str, Any]:
    return {
        "enabled": common.enabled,
        "events": list(common.events) if common.events is not None else None,
        "filters": filters_to_json(common.filters),
        "timeout_seconds": common.timeout_seconds,
        "on_failure": common.on_failure,
        "wait_for_verdict": common.wait_for_verdict,
        "order": common.order,
    }


def _common_from_json(raw: dict[str, Any], *, events_required: bool) -> CommonConfig:
    """保存された値を読む(壊れた項目は既定に戻す)。"""

    def safe(fn, value, default):  # noqa: ANN001, ANN202
        try:
            return fn(value)
        except ModValidationError:
            return default

    events_raw = raw.get("events")
    events: tuple[str, ...] | None
    if events_raw is None and not events_required:
        events = None
    else:
        events = safe(normalize_events, events_raw, ())
    return CommonConfig(
        enabled=raw.get("enabled") is True,
        events=events,
        filters=safe(normalize_filters, raw.get("filters"), Filters()),
        timeout_seconds=safe(
            normalize_timeout,
            raw.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS),
            DEFAULT_TIMEOUT_SECONDS,
        ),
        on_failure=safe(normalize_on_failure, raw.get("on_failure", "block"), "block"),
        wait_for_verdict=raw.get("wait_for_verdict") is True,
        order=safe(_normalize_order, raw.get("order"), None),
    )


def apply_common_updates(
    current: CommonConfig, updates: dict[str, Any], *, allowed_events: tuple[str, ...]
) -> CommonConfig:
    """`updates` に書いた項目だけを変える(検証してから)。"""
    changed = current
    if "enabled" in updates:
        if not isinstance(updates["enabled"], bool):
            raise ModValidationError(t("mods.invalidEnabled"))
        changed = replace(changed, enabled=updates["enabled"])
    if "events" in updates:
        value = updates["events"]
        changed = replace(
            changed, events=None if value is None else normalize_events(value, allowed_events)
        )
    if "filters" in updates:
        changed = replace(changed, filters=normalize_filters(updates["filters"]))
    if "timeout_seconds" in updates:
        changed = replace(changed, timeout_seconds=normalize_timeout(updates["timeout_seconds"]))
    if "on_failure" in updates:
        changed = replace(changed, on_failure=normalize_on_failure(updates["on_failure"]))
    if "wait_for_verdict" in updates:
        if not isinstance(updates["wait_for_verdict"], bool):
            raise ModValidationError(t("mods.invalidEnabled"))
        changed = replace(changed, wait_for_verdict=updates["wait_for_verdict"])
    if "order" in updates:
        changed = replace(changed, order=_normalize_order(updates["order"]))
    return changed


# -- インスタンスの名前 -------------------------------------------------------------


def load_instance_name(db: Session) -> str | None:
    value = _get_raw_value(db, INSTANCE_NAME_KEY)
    return value if isinstance(value, str) and value else None


def save_instance_name(db: Session, value: object) -> None:
    name = normalize_instance_name(value)
    if name is None:
        _delete(db, INSTANCE_NAME_KEY)
    else:
        _save(db, INSTANCE_NAME_KEY, name)


# -- Webhook -------------------------------------------------------------------


def webhook_to_json(webhook: WebhookConfig) -> dict[str, Any]:
    return {
        "id": webhook.id,
        "name": webhook.name,
        "url": webhook.url,
        "send_images": webhook.send_images,
        **_common_to_json(webhook.common),
    }


def webhooks_from_json(raw: object) -> tuple[WebhookConfig, ...]:
    if not isinstance(raw, list):
        return ()
    result: list[WebhookConfig] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        webhook_id = item.get("id")
        if not isinstance(webhook_id, str) or not webhook_id or webhook_id in seen:
            continue
        try:
            webhook = WebhookConfig(
                id=webhook_id,
                name=normalize_name(item.get("name")),
                url=normalize_url(item.get("url")),
                common=_common_from_json(item, events_required=True),
                send_images=item.get("send_images") is True,
            )
        except ModValidationError:
            continue
        seen.add(webhook_id)
        result.append(webhook)
    return tuple(result)


def load_webhooks(db: Session) -> tuple[WebhookConfig, ...]:
    return webhooks_from_json(_get_raw_value(db, WEBHOOKS_KEY))


def _save_webhooks(db: Session, webhooks: tuple[WebhookConfig, ...]) -> None:
    _save(db, WEBHOOKS_KEY, [webhook_to_json(w) for w in webhooks])


def find_webhook(webhooks: tuple[WebhookConfig, ...], webhook_id: str) -> WebhookConfig | None:
    return next((w for w in webhooks if w.id == webhook_id), None)


def next_order(db: Session) -> int:
    orders = [w.common.order for w in load_webhooks(db) if w.common.order is not None]
    orders += [
        p.common.order for p in load_python_configs(db).values() if p.common.order is not None
    ]
    return (max(orders) + 1) if orders else 0


def add_webhook(db: Session, data_dir: Path, values: dict[str, Any]) -> str:
    """Webhook を足して、その id を返す。鍵は必須。"""
    current = load_webhooks(db)
    if len(current) >= WEBHOOKS_MAX:
        raise ModValidationError(t("mods.tooManyWebhooks", max=WEBHOOKS_MAX))
    secret = normalize_secret(values.get("secret"))
    common = apply_common_updates(
        CommonConfig(events=()),
        {k: v for k, v in values.items() if k in COMMON_FIELDS},
        allowed_events=contract.EVENTS,
    )
    if common.events is None:
        common = replace(common, events=())
    if common.order is None:
        common = replace(common, order=next_order(db))
    webhook = WebhookConfig(
        id=uuid.uuid4().hex,
        name=normalize_name(values.get("name")),
        url=normalize_url(values.get("url")),
        common=common,
        send_images=_bool(values.get("send_images", False)),
    )
    write_webhook_secret(data_dir, webhook.id, secret)
    _save_webhooks(db, (*current, webhook))
    return webhook.id


def update_webhook(db: Session, data_dir: Path, webhook_id: str, updates: dict[str, Any]) -> None:
    webhooks = load_webhooks(db)
    current = find_webhook(webhooks, webhook_id)
    if current is None:
        raise ModNotFoundError
    changed = current
    if "name" in updates:
        changed = replace(changed, name=normalize_name(updates["name"]))
    if "url" in updates:
        changed = replace(changed, url=normalize_url(updates["url"]))
    if "send_images" in updates:
        changed = replace(changed, send_images=_bool(updates["send_images"]))
    common = apply_common_updates(
        changed.common,
        {k: v for k, v in updates.items() if k in COMMON_FIELDS},
        allowed_events=contract.EVENTS,
    )
    if common.events is None:
        common = replace(common, events=())
    changed = replace(changed, common=common)
    secret = normalize_secret(updates["secret"]) if updates.get("secret") is not None else None
    _save_webhooks(db, tuple(changed if w.id == webhook_id else w for w in webhooks))
    if secret is not None:
        write_webhook_secret(data_dir, webhook_id, secret)


def delete_webhook(db: Session, data_dir: Path, webhook_id: str) -> None:
    webhooks = load_webhooks(db)
    if find_webhook(webhooks, webhook_id) is None:
        raise ModNotFoundError
    _save_webhooks(db, tuple(w for w in webhooks if w.id != webhook_id))
    delete_webhook_secret(data_dir, webhook_id)


def _bool(value: object) -> bool:
    if not isinstance(value, bool):
        raise ModValidationError(t("mods.invalidEnabled"))
    return value


COMMON_FIELDS = frozenset(
    {"enabled", "events", "filters", "timeout_seconds", "on_failure", "wait_for_verdict", "order"}
)


def read_webhook_secret(data_dir: Path, webhook_id: str) -> str | None:
    return api_key_domain.read_secret_field(data_dir, _WEBHOOK_SECRET_PREFIX + webhook_id)


def write_webhook_secret(data_dir: Path, webhook_id: str, value: str) -> None:
    api_key_domain.write_secret_field(data_dir, _WEBHOOK_SECRET_PREFIX + webhook_id, value)


def delete_webhook_secret(data_dir: Path, webhook_id: str) -> None:
    api_key_domain.delete_secret_field(data_dir, _WEBHOOK_SECRET_PREFIX + webhook_id)


# -- Python の Mod ----------------------------------------------------------------


def python_config_to_json(config: PythonModConfig) -> dict[str, Any]:
    return {**_common_to_json(config.common), "values": dict(config.values)}


def load_python_configs(db: Session) -> dict[str, PythonModConfig]:
    raw = _get_raw_value(db, PYTHON_KEY)
    if not isinstance(raw, dict):
        return {}
    result: dict[str, PythonModConfig] = {}
    for mod_id, item in raw.items():
        if not is_valid_mod_id(mod_id) or not isinstance(item, dict):
            continue
        values = item.get("values")
        result[mod_id] = PythonModConfig(
            mod_id=mod_id,
            common=_common_from_json(item, events_required=False),
            values=dict(values) if isinstance(values, dict) else {},
        )
    return result


def python_config_or_default(configs: dict[str, PythonModConfig], mod_id: str) -> PythonModConfig:
    return configs.get(mod_id) or PythonModConfig(mod_id=mod_id, common=CommonConfig())


def save_python_config(db: Session, config: PythonModConfig) -> None:
    configs = load_python_configs(db)
    configs[config.mod_id] = config
    _save(db, PYTHON_KEY, {k: python_config_to_json(v) for k, v in configs.items()})


def _python_secret_field(mod_id: str, key: str) -> str:
    return f"{_PYTHON_SECRET_PREFIX}{mod_id}.{key}"


def read_python_secret(data_dir: Path, mod_id: str, key: str) -> str | None:
    return api_key_domain.read_secret_field(data_dir, _python_secret_field(mod_id, key))


def write_python_secret(data_dir: Path, mod_id: str, key: str, value: str) -> None:
    api_key_domain.write_secret_field(data_dir, _python_secret_field(mod_id, key), value)


def delete_python_secret(data_dir: Path, mod_id: str, key: str) -> None:
    api_key_domain.delete_secret_field(data_dir, _python_secret_field(mod_id, key))


# -- 順番 ------------------------------------------------------------------------


def save_order(db: Session, items: list[tuple[str, str]]) -> None:
    """`(種類, id)` の並びの順に `order` を振り直す。並びに無い Mod の順番は変えない。"""
    positions: dict[tuple[str, str], int] = {}
    for index, (kind, mod_id) in enumerate(items):
        if kind not in (KIND_WEBHOOK, KIND_PYTHON) or not isinstance(mod_id, str):
            raise ModValidationError(t("mods.invalidOrder"))
        positions[(kind, mod_id)] = index
    webhooks = load_webhooks(db)
    known = {(KIND_WEBHOOK, w.id) for w in webhooks}
    for kind, mod_id in positions:
        if kind == KIND_WEBHOOK and (kind, mod_id) not in known:
            raise ModNotFoundError
        if kind == KIND_PYTHON and not is_valid_mod_id(mod_id):
            raise ModNotFoundError
    new_webhooks = tuple(
        replace(w, common=replace(w.common, order=positions[(KIND_WEBHOOK, w.id)]))
        if (KIND_WEBHOOK, w.id) in positions
        else w
        for w in webhooks
    )
    _save_webhooks(db, new_webhooks)
    configs = load_python_configs(db)
    for (kind, mod_id), position in positions.items():
        if kind != KIND_PYTHON:
            continue
        current = python_config_or_default(configs, mod_id)
        configs[mod_id] = replace(current, common=replace(current.common, order=position))
    if configs:
        _save(db, PYTHON_KEY, {k: python_config_to_json(v) for k, v in configs.items()})
