"""LLM・VLM の接続先(ADR-0032。中身は ADR-0024 8章「接続先(一覧)」)。

接続先は、名前、Base URL、キー(任意)、API 形式(Responses / Chat Completions)を持つ。LLM を
使う機能(今は自動タイトル・タグ)は、ここから接続先を選んで使う。

- 組み込みの接続先 `openai`(「OpenAI の設定」)は一覧に保存せず、常に先頭にあるものとして扱う。
  ADR-0017 のキーと Base URL をそのまま使い、名前・Base URL・キーは変えられず、削除もできない。
  API 形式、`locality`、構造化出力の対応だけは変えられる。
- 利用者の接続先のキーは DB に入れず、`DATA_DIR/secrets.json` に接続先ごとに置く
  (`app/domain/api_key.py` と同じファイル・同じ書き込み)。キーが無ければダミーのキー
  (`PLACEHOLDER_API_KEY`)を送る(ローカルの Ollama などへ OpenAI のキーを漏らさないため。SDK は
  空のキーを受け付けない)。
- どの機能が接続先を使っているかは、使う側が `register_usage` で登録した関数が答える。この
  モジュールは特定の機能を知らない。使われている接続先は削除できない。
- 構造化出力(Structured Outputs、JSON のスキーマ)への対応は、接続先ごとに管理者が選ぶ
  (`auto` / `supported` / `unsupported`。ADR-0032 の 2026-10-21 追記)。GAKEI は接続先に対応を
  問い合わせない。`auto` は、OpenAI の公式(組み込みの接続先で Base URL を変えていないときを
  含む)だけを対応とみなす。判定は `supports_structured_outputs` の1か所にまとめ、領域の検出
  (ADR-0053)と ComfyUI のワークフローの推定(ADR-0052)が使う。

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
from app.domain import locality as locality_domain
from app.domain.general_settings import GeneralSettingsValidationError, _get_raw_value, _save
from app.domain.url_origin import same_origin
from app.i18n import t

if TYPE_CHECKING:
    from app.config import Settings

# 保存先(上の docstring のとおり、名前は変えない)。
CONNECTIONS_KEY = "annotation.connections"
OPENAI_API_STYLE_KEY = "annotation.openai_api_style"
# 組み込みの接続先の構造化出力の対応(2026-10-21 に足した。利用者の接続先は一覧の項目に持つ)。
OPENAI_STRUCTURED_OUTPUTS_KEY = "llm_connections.openai_structured_outputs"
_CONNECTION_SECRET_PREFIX = "annotation_connection_key."

ApiStyle = Literal["responses", "chat"]
API_STYLES: tuple[str, ...] = get_args(ApiStyle)

# 構造化出力への対応。`auto` は GAKEI の判定(`STRUCTURED_OUTPUTS_HOSTS`)に任せる。
StructuredOutputsMode = Literal["auto", "supported", "unsupported"]
STRUCTURED_OUTPUTS_MODES: tuple[str, ...] = get_args(StructuredOutputsMode)

# `auto` のとき、構造化出力(JSON Schema の strict)に対応するとみなす接続先のホスト。確かな
# OpenAI の公式だけにする(ほかは管理者が `supported` を選ぶ)。
STRUCTURED_OUTPUTS_HOSTS = frozenset({"api.openai.com"})

# 接続先を使えない理由(`connection_problem`)。
ConnectionProblem = Literal[
    "connection_missing", "api_key_missing", "structured_outputs_unsupported"
]

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
    # 接続先(ADR-0051 3章)。None は既定(`external`)。組み込みの接続先では使わない
    # (OpenAI の接続の値を使う)。
    locality: str | None = None
    # 構造化出力への対応(管理者の指定。既定は `auto`)。
    structured_outputs: StructuredOutputsMode = "auto"

    @property
    def builtin(self) -> bool:
        return self.id == BUILTIN_CONNECTION_ID


def builtin_connection(
    api_style: ApiStyle = "responses", structured_outputs: StructuredOutputsMode = "auto"
) -> ConnectionConfig:
    return ConnectionConfig(
        id=BUILTIN_CONNECTION_ID,
        name="",
        base_url=None,
        api_style=api_style,
        structured_outputs=structured_outputs,
    )


def all_connections(
    user_connections: tuple[ConnectionConfig, ...],
    openai_api_style: ApiStyle,
    openai_structured_outputs: StructuredOutputsMode = "auto",
) -> tuple[ConnectionConfig, ...]:
    """組み込みの接続先を先頭にした一覧。"""
    return (builtin_connection(openai_api_style, openai_structured_outputs), *user_connections)


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


def normalize_structured_outputs(value: object) -> StructuredOutputsMode:
    if value not in STRUCTURED_OUTPUTS_MODES:
        raise LlmConnectionValidationError(t("settings.llmConnections.invalidStructuredOutputs"))
    return value  # type: ignore[return-value]


def _read_structured_outputs(value: object) -> StructuredOutputsMode:
    """保存された値を読む。無い・壊れている値は `auto`。"""
    return value if value in STRUCTURED_OUTPUTS_MODES else "auto"  # type: ignore[return-value]


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


def _normalize_locality(value: object) -> str:
    try:
        return locality_domain.normalize(value)
    except locality_domain.LocalityValidationError as e:
        raise LlmConnectionValidationError(str(e)) from e


def connection_to_json(connection: ConnectionConfig) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": connection.id,
        "name": connection.name,
        "base_url": connection.base_url,
        "api_style": connection.api_style,
    }
    # 既定(None / `auto`)のときは書かない。
    if connection.locality is not None:
        data["locality"] = connection.locality
    if connection.structured_outputs != "auto":
        data["structured_outputs"] = connection.structured_outputs
    return data


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
                locality=locality_domain.read_saved(item.get("locality")),
                structured_outputs=_read_structured_outputs(item.get("structured_outputs")),
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


def load_openai_structured_outputs(db: Session) -> StructuredOutputsMode:
    return _read_structured_outputs(_get_raw_value(db, OPENAI_STRUCTURED_OUTPUTS_KEY))


def load(db: Session) -> tuple[ConnectionConfig, ...]:
    """組み込みを先頭にした接続先の一覧。追加した接続先は追加した順に並ぶ。"""
    return all_connections(
        load_user_connections(db), load_openai_api_style(db), load_openai_structured_outputs(db)
    )


def find_connection(
    connections: tuple[ConnectionConfig, ...], connection_id: str
) -> ConnectionConfig | None:
    return next((c for c in connections if c.id == connection_id), None)


def _save_connections(db: Session, connections: tuple[ConnectionConfig, ...]) -> None:
    _save(db, CONNECTIONS_KEY, [connection_to_json(c) for c in connections])


def add_connection(
    db: Session,
    name: object,
    base_url: object,
    api_style: object,
    locality: object = None,
    structured_outputs: object = None,
) -> str:
    """接続先を足して、その id を返す。`structured_outputs` の None は既定(`auto`)。"""
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
        locality=_normalize_locality(locality) if locality is not None else None,
        structured_outputs=(
            normalize_structured_outputs(structured_outputs)
            if structured_outputs is not None
            else "auto"
        ),
    )
    api_key_domain.warn_if_insecure_base_url(normalized_url)
    _save_connections(db, (*current, connection))
    return connection.id


def update_connection(
    db: Session,
    connection_id: str,
    updates: dict[str, Any],
    data_dir: Path | None = None,
) -> None:
    """接続先の名前・Base URL・API 形式・`locality`・`structured_outputs` を変える(書いた項目
    だけ)。組み込みの接続先は API 形式と `locality` と `structured_outputs` だけ変えられる。
    組み込みの `locality` は OpenAI の接続の値そのもの(ADR-0051 3章)。None は既定に戻す
    (`structured_outputs` の None は不正な値)。

    `api_key` は Base URL と一緒に入れ直すキー(`data_dir` が要る)。保存済みのキーは、保存した
    ときの Base URL と同じオリジン(scheme + host + port)にだけ送る。キーがあって Base URL の
    オリジンが変わるときは `api_key` が必須で、無ければ LlmConnectionValidationError。同じ
    オリジンでパスだけ変えるならキーはそのまま使う。"""
    if connection_id == BUILTIN_CONNECTION_ID:
        if set(updates) - {"api_style", "locality", "structured_outputs"}:
            raise ConnectionReservedError
        # 先に全部を検証する(一部だけ保存される事態を避ける)。
        if "locality" in updates and updates["locality"] is not None:
            _normalize_locality(updates["locality"])
        if "api_style" in updates:
            normalize_api_style(updates["api_style"])
        if "structured_outputs" in updates:
            normalize_structured_outputs(updates["structured_outputs"])
        if "api_style" in updates:
            _save(db, OPENAI_API_STYLE_KEY, normalize_api_style(updates["api_style"]))
        if "structured_outputs" in updates:
            _save(
                db,
                OPENAI_STRUCTURED_OUTPUTS_KEY,
                normalize_structured_outputs(updates["structured_outputs"]),
            )
        if "locality" in updates:
            locality_domain.save_provider_localities(db, {"openai": updates["locality"]})
        return
    updates = dict(updates)
    raw_key = updates.pop("api_key", None)
    new_key = raw_key.strip() if isinstance(raw_key, str) else None
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
    if "locality" in updates:
        # None は既定(`external`)に戻す。
        value = updates["locality"]
        changed = replace(
            changed, locality=_normalize_locality(value) if value is not None else None
        )
    if "structured_outputs" in updates:
        changed = replace(
            changed, structured_outputs=normalize_structured_outputs(updates["structured_outputs"])
        )
    if changed.base_url and changed.base_url != current.base_url:
        api_key_domain.warn_if_insecure_base_url(changed.base_url)
    if (
        not new_key
        and data_dir is not None
        and not same_origin(changed.base_url, current.base_url)
        and read_connection_key(data_dir, connection_id) is not None
    ):
        # 保存済みのキーを、保存したときと別のオリジンへ送らない(入れ直させる)。
        raise LlmConnectionValidationError(t("settings.llmConnections.reenterKey"))
    _save_connections(db, tuple(changed if c.id == connection_id else c for c in connections))
    if new_key and data_dir is not None:
        write_connection_key(data_dir, connection_id, new_key)


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


@dataclass(frozen=True)
class LlmTarget:
    """LLM の1用途の送り先(接続先の実際のキーと Base URL、API 形式、モデル名)。自動タイトル・
    タグ(ADR-0024)と生成のアシスタント(ADR-0048)が使う。

    キーが無ければ `api_key=None`(組み込みの接続先で OpenAI のキーが未設定のとき)。
    """

    connection_id: str
    model: str
    api_key: str | None
    base_url: str | None
    api_style: ApiStyle = "responses"


def resolve_target(
    connections: tuple[ConnectionConfig, ...],
    connection_id: str,
    model: str,
    settings: Settings,
) -> LlmTarget:
    """接続先の id とモデル名から送り先を決める。接続先が無ければ ConnectionNotFoundError。"""
    connection = find_connection(connections, connection_id)
    if connection is None:
        raise ConnectionNotFoundError(connection_id)
    resolved = resolve(connection, settings)
    return LlmTarget(
        connection_id=connection.id,
        model=model,
        api_key=resolved.api_key,
        base_url=resolved.base_url,
        api_style=resolved.api_style,
    )


def display_base_url(connection: ConnectionConfig, settings: Settings) -> str | None:
    """画面に出す Base URL(組み込みは OpenAI の設定の値。未設定なら None = OpenAI 本体)。"""
    if connection.builtin:
        base_url, _ = api_key_domain.resolve_base_url(settings)
        return base_url
    return connection.base_url


# -- 構造化出力 ----------------------------------------------------------------------


def supports_structured_outputs(connection: ConnectionConfig, settings: Settings) -> bool:
    """接続先が構造化出力(JSON のスキーマ)に対応するとみなせるか。管理者の指定が `auto` なら、
    組み込みの接続先で Base URL を変えていないとき(OpenAI 本体)か、ホストが OpenAI の公式の
    ときだけ対応とみなす。"""
    if connection.structured_outputs == "supported":
        return True
    if connection.structured_outputs == "unsupported":
        return False
    base_url = display_base_url(connection, settings)
    if base_url is None:
        return connection.builtin
    return locality_domain.host_of(base_url) in STRUCTURED_OUTPUTS_HOSTS


def connection_supports_structured_outputs(
    db: Session, connection_id: str, settings: Settings
) -> bool:
    """id で引いた接続先が構造化出力に対応するとみなせるか。接続先が無ければ False。"""
    connection = find_connection(load(db), connection_id)
    return connection is not None and supports_structured_outputs(connection, settings)


def connection_problem(
    db: Session, connection_id: str, settings: Settings, *, structured_outputs: bool
) -> ConnectionProblem | None:
    """接続先を使えない理由を1つ返す(使えるなら None)。見る順は、接続先が無い → キーが無い
    (組み込みの接続先で OpenAI のキーが未設定) → 構造化出力に対応しない(`structured_outputs`
    が真のときだけ)。"""
    connection = find_connection(load(db), connection_id)
    if connection is None:
        return "connection_missing"
    if resolve(connection, settings).api_key is None:
        return "api_key_missing"
    if structured_outputs and not supports_structured_outputs(connection, settings):
        return "structured_outputs_unsupported"
    return None


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
