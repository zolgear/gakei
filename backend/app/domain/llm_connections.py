"""LLM・VLM の接続先(ADR-0032。中身は ADR-0024 8章「接続先(一覧)」)。

接続先は、名前、Base URL、キー(任意)、API 形式(Responses / Chat Completions)を持つ。LLM を
使う機能(今は自動タイトル・タグ)は、ここから接続先を選んで使う。

- 組み込みの接続先 `openai`(「OpenAI の設定」)は一覧に保存せず、常に先頭にあるものとして扱う。
  ADR-0017 のキーと Base URL をそのまま使い、名前・Base URL・キーは変えられず、削除もできない。
  API 形式だけは変えられる。
- 利用者の接続先のキーは DB に入れず、`DATA_DIR/secrets.json` に接続先ごとに置く
  (`app/domain/api_key.py` と同じファイル・同じ書き込み)。キーが無ければダミーのキー
  (`PLACEHOLDER_API_KEY`)を送る(ローカルの Ollama などへ OpenAI のキーを漏らさないため。SDK は
  空のキーを受け付けない)。
- どの機能が接続先を使っているかは、使う側が `register_usage` で登録した関数が答える。この
  モジュールは特定の機能を知らない。使われている接続先は削除できない。

**保存先の名前が `annotation` のままである理由(ADR-0032 4章)。** 接続先は、はじめ自動タイトル・
タグの一部として作った(ADR-0024 8章)。そのため保存先は次の名前になっている。

- `app_setting` の `annotation.connections`(利用者の接続先の一覧)
- `app_setting` の `annotation.openai_api_style`(組み込みの接続先の API 形式)
- `secrets.json` の `annotation_connection_key.<id>`(接続先ごとのキー)

名前を変えると実データの移行が要り、移行に失敗するとキーを失うおそれがある。名前は内部のもので
利用者には見えないので、そのまま使う。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, get_args

from sqlalchemy.orm import Session

from app.domain import api_key as api_key_domain
from app.domain.general_settings import GeneralSettingsValidationError, _get_raw_value, _save
from app.i18n import t

if TYPE_CHECKING:
    from app.config import Settings

# 保存先(上の docstring のとおり、名前は変えない)。
CONNECTIONS_KEY = "annotation.connections"
OPENAI_API_STYLE_KEY = "annotation.openai_api_style"
_CONNECTION_SECRET_PREFIX = "annotation_connection_key."

ApiStyle = Literal["responses", "chat"]
API_STYLES: tuple[str, ...] = get_args(ApiStyle)

# 組み込みの接続先(OpenAI の設定。ADR-0017)の id。利用者の接続先には使えない。
BUILTIN_CONNECTION_ID = "openai"
CONNECTION_NAME_MAX = 100
CONNECTIONS_MAX = 50
# OpenAI の設定に Base URL が無いとき(OpenAI 本体)の URL。
OPENAI_DEFAULT_BASE_URL = "https://api.openai.com/v1"
# 利用者の接続先にキーが無いときに送るダミーのキー。
PLACEHOLDER_API_KEY = "gakei-no-key"


class LlmConnectionValidationError(GeneralSettingsValidationError):
    """保存しようとした値が不正(API 層で 422 にする)。"""


class ConnectionNotFoundError(Exception):
    """指定した接続先が無い(API 層で 404。推定では `failed`)。"""


class ConnectionReservedError(Exception):
    """組み込みの接続先は API 形式のほかは編集できず、削除もできない(API 層で 409)。"""


class ConnectionInUseError(Exception):
    """どこかの機能で使っている接続先は削除できない(API 層で 409)。`features` は使っている
    機能の id。"""

    def __init__(self, features: list[str]) -> None:
        super().__init__(features)
        self.features = features


# -- 型 ------------------------------------------------------------------------


@dataclass(frozen=True)
class ConnectionConfig:
    """接続先1つ。組み込みの `openai` は `base_url=None`(実行時に ADR-0017 の値を使う)。"""

    id: str
    name: str
    base_url: str | None
    api_style: ApiStyle = "responses"

    @property
    def builtin(self) -> bool:
        return self.id == BUILTIN_CONNECTION_ID


def builtin_connection(api_style: ApiStyle = "responses") -> ConnectionConfig:
    return ConnectionConfig(id=BUILTIN_CONNECTION_ID, name="", base_url=None, api_style=api_style)


def all_connections(
    user_connections: tuple[ConnectionConfig, ...], openai_api_style: ApiStyle
) -> tuple[ConnectionConfig, ...]:
    """組み込みの接続先を先頭にした一覧。"""
    return (builtin_connection(openai_api_style), *user_connections)


@dataclass(frozen=True)
class ResolvedConnection:
    """接続先の実際の送り先。組み込みの接続先で OpenAI のキーが未設定なら `api_key=None`。"""

    connection_id: str
    api_key: str | None
    base_url: str | None
    api_style: ApiStyle


# -- 検証 --------------------------------------------------------------------


def normalize_api_style(value: object) -> ApiStyle:
    if value not in API_STYLES:
        raise LlmConnectionValidationError(t("settings.llmConnections.invalidApiStyle"))
    return value  # type: ignore[return-value]


def normalize_connection_name(value: object) -> str:
    if not isinstance(value, str) or not (0 < len(value.strip()) <= CONNECTION_NAME_MAX):
        raise LlmConnectionValidationError(
            t("settings.llmConnections.invalidName", max=CONNECTION_NAME_MAX)
        )
    return value.strip()


def normalize_connection_base_url(value: object) -> str:
    """接続先の Base URL(必須。http / https のみ。ADR-0017 と同じ検証)。"""
    if not isinstance(value, str) or not value.strip():
        raise LlmConnectionValidationError(t("openai.baseUrl.invalid"))
    try:
        return api_key_domain.normalize_base_url(value.strip())
    except api_key_domain.BaseUrlValidationError as e:
        raise LlmConnectionValidationError(str(e)) from e


def connection_to_json(connection: ConnectionConfig) -> dict[str, Any]:
    return {
        "id": connection.id,
        "name": connection.name,
        "base_url": connection.base_url,
        "api_style": connection.api_style,
    }


def connections_from_json(raw: object) -> tuple[ConnectionConfig, ...]:
    """保存された接続先の一覧を読む。壊れた項目・重複した id・予約 id は捨てる。"""
    if not isinstance(raw, list):
        return ()
    result: list[ConnectionConfig] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        connection_id = item.get("id")
        if (
            not isinstance(connection_id, str)
            or not connection_id
            or connection_id == BUILTIN_CONNECTION_ID
            or connection_id in seen
        ):
            continue
        try:
            connection = ConnectionConfig(
                id=connection_id,
                name=normalize_connection_name(item.get("name")),
                base_url=normalize_connection_base_url(item.get("base_url")),
                api_style=normalize_api_style(item.get("api_style", "responses")),
            )
        except LlmConnectionValidationError:
            continue
        seen.add(connection_id)
        result.append(connection)
    return tuple(result)


# -- 読み書き ------------------------------------------------------------------


def load_user_connections(db: Session) -> tuple[ConnectionConfig, ...]:
    """利用者が登録した接続先(組み込みは含まない)。"""
    return connections_from_json(_get_raw_value(db, CONNECTIONS_KEY))


def load_openai_api_style(db: Session) -> ApiStyle:
    raw = _get_raw_value(db, OPENAI_API_STYLE_KEY)
    return raw if raw in API_STYLES else "responses"  # type: ignore[return-value]


def load(db: Session) -> tuple[ConnectionConfig, ...]:
    """組み込みを先頭にした接続先の一覧。追加した接続先は追加した順に並ぶ。"""
    return all_connections(load_user_connections(db), load_openai_api_style(db))


def find_connection(
    connections: tuple[ConnectionConfig, ...], connection_id: str
) -> ConnectionConfig | None:
    return next((c for c in connections if c.id == connection_id), None)


def _save_connections(db: Session, connections: tuple[ConnectionConfig, ...]) -> None:
    _save(db, CONNECTIONS_KEY, [connection_to_json(c) for c in connections])


def add_connection(db: Session, name: object, base_url: object, api_style: object) -> str:
    """接続先を足して、その id を返す。"""
    current = load_user_connections(db)
    if len(current) >= CONNECTIONS_MAX:
        raise LlmConnectionValidationError(
            t("settings.llmConnections.tooMany", max=CONNECTIONS_MAX)
        )
    normalized_url = normalize_connection_base_url(base_url)
    connection = ConnectionConfig(
        id=uuid.uuid4().hex,
        name=normalize_connection_name(name),
        base_url=normalized_url,
        api_style=normalize_api_style(api_style),
    )
    api_key_domain.warn_if_insecure_base_url(normalized_url)
    _save_connections(db, (*current, connection))
    return connection.id


def update_connection(db: Session, connection_id: str, updates: dict[str, Any]) -> None:
    """接続先の名前・Base URL・API 形式を変える(書いた項目だけ)。組み込みの接続先は API 形式
    だけ変えられる。"""
    if connection_id == BUILTIN_CONNECTION_ID:
        if set(updates) - {"api_style"}:
            raise ConnectionReservedError
        if "api_style" in updates:
            _save(db, OPENAI_API_STYLE_KEY, normalize_api_style(updates["api_style"]))
        return
    connections = load_user_connections(db)
    current = find_connection(connections, connection_id)
    if current is None:
        raise ConnectionNotFoundError
    changed = current
    if "name" in updates:
        changed = replace(changed, name=normalize_connection_name(updates["name"]))
    if "base_url" in updates:
        changed = replace(changed, base_url=normalize_connection_base_url(updates["base_url"]))
    if "api_style" in updates:
        changed = replace(changed, api_style=normalize_api_style(updates["api_style"]))
    if changed.base_url and changed.base_url != current.base_url:
        api_key_domain.warn_if_insecure_base_url(changed.base_url)
    _save_connections(db, tuple(changed if c.id == connection_id else c for c in connections))


def delete_connection(db: Session, data_dir: Path, connection_id: str) -> None:
    """接続先を消す(キーも消す)。組み込み・使用中なら例外。"""
    if connection_id == BUILTIN_CONNECTION_ID:
        raise ConnectionReservedError
    connections = load_user_connections(db)
    if find_connection(connections, connection_id) is None:
        raise ConnectionNotFoundError
    features = features_using(db).get(connection_id, [])
    if features:
        raise ConnectionInUseError(features)
    _save_connections(db, tuple(c for c in connections if c.id != connection_id))
    delete_connection_key(data_dir, connection_id)


def require_user_connection(db: Session, connection_id: str) -> None:
    """キーを扱えるのは利用者の接続先だけ(組み込みは ConnectionReservedError、無ければ
    ConnectionNotFoundError)。"""
    if connection_id == BUILTIN_CONNECTION_ID:
        raise ConnectionReservedError
    if find_connection(load_user_connections(db), connection_id) is None:
        raise ConnectionNotFoundError


# -- 接続先のキー(secrets.json) ------------------------------------------------


def _secret_field(connection_id: str) -> str:
    return _CONNECTION_SECRET_PREFIX + connection_id


def read_connection_key(data_dir: Path, connection_id: str) -> str | None:
    return api_key_domain.read_secret_field(data_dir, _secret_field(connection_id))


def write_connection_key(data_dir: Path, connection_id: str, value: str) -> None:
    api_key_domain.write_secret_field(data_dir, _secret_field(connection_id), value)


def delete_connection_key(data_dir: Path, connection_id: str) -> None:
    api_key_domain.delete_secret_field(data_dir, _secret_field(connection_id))


def connection_key_set(connection: ConnectionConfig, settings: Settings) -> bool:
    """キーを設定しているか(組み込みの接続先は OpenAI のキーの有無)。値は返さない。"""
    if connection.builtin:
        key, _ = api_key_domain.resolve_key(settings)
        return key is not None
    return read_connection_key(settings.data_dir, connection.id) is not None


# -- 送り先の解決 ----------------------------------------------------------------


def resolve(connection: ConnectionConfig, settings: Settings) -> ResolvedConnection:
    """接続先の実際のキーと Base URL を決める。組み込みは ADR-0017 の値、利用者の接続先は
    自分のキー(無ければダミーのキー)と Base URL。"""
    if connection.builtin:
        openai_key, _ = api_key_domain.resolve_key(settings)
        base_url, _ = api_key_domain.resolve_base_url(settings)
        return ResolvedConnection(
            connection_id=connection.id,
            api_key=openai_key,
            base_url=base_url,
            api_style=connection.api_style,
        )
    own_key = read_connection_key(settings.data_dir, connection.id)
    return ResolvedConnection(
        connection_id=connection.id,
        api_key=own_key or PLACEHOLDER_API_KEY,
        base_url=connection.base_url,
        api_style=connection.api_style,
    )


def display_base_url(connection: ConnectionConfig, settings: Settings) -> str | None:
    """画面に出す Base URL(組み込みは OpenAI の設定の値。未設定なら None = OpenAI 本体)。"""
    if connection.builtin:
        base_url, _ = api_key_domain.resolve_base_url(settings)
        return base_url
    return connection.base_url


# -- 使っている機能 ----------------------------------------------------------------

# 機能の id → その機能が使っている接続先の id を返す関数。使う側のモジュールが読み込まれたときに
# `register_usage` で登録する(このモジュールは特定の機能を知らない)。
UsageFunction = Callable[[Session], set[str]]
_usage_functions: dict[str, UsageFunction] = {}


def register_usage(feature: str, function: UsageFunction) -> None:
    """接続先を使う機能を登録する。同じ機能を登録し直したら置き換える。"""
    _usage_functions[feature] = function


def features_using(db: Session) -> dict[str, list[str]]:
    """接続先の id → それを使っている機能の id の一覧(登録順)。"""
    result: dict[str, list[str]] = {}
    for feature, function in _usage_functions.items():
        for connection_id in function(db):
            result.setdefault(connection_id, []).append(feature)
    return result
