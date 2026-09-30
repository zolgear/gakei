"""自動タイトル・タグの管理者設定(ADR-0024 3章・4章・5章・8章)。

値は項目ごとに `app_setting` の `annotation.<項目名>` に保存する(`mcp_settings` と同じ形式
`{"value": ...}`。画面で保存した値だけ。環境変数の既定値は持たない)。壊れた値・範囲外の値は
既定値として扱う。

接続先とモデルの組(ADR-0024 8章):

- **接続先**(`annotation.connections`): 名前、Base URL、API 形式を持つ接続先の一覧。組み込みの
  接続先 `openai`(「OpenAI の設定」)は一覧に保存せず、常に先頭にあるものとして扱う。これは
  ADR-0017 のキーと Base URL をそのまま使い、名前・Base URL・キーは変えられず、削除もできない。
  API 形式だけは `annotation.openai_api_style` に保存して変えられる(今の設定からの移行で、
  OpenAI の設定に Chat Completions で送っていた場合を変えないため)。
- 接続先のキーは DB に入れず、`DATA_DIR/secrets.json` の `annotation_connection_key.<id>` に
  置く(`app/domain/api_key.py` と同じファイル・同じ書き込み)。キーが無ければダミーのキー
  (`PLACEHOLDER_API_KEY`)を送る(ローカルの Ollama などへ OpenAI のキーを漏らさないため。SDK は
  空のキーを受け付けない)。
- **用途ごとの組**(`annotation.profiles`): 「既定」(`default`)と「ComfyUI の画像」
  (`comfyui`)のそれぞれに、タイトル(`llm`)とタグ(`vlm`)の `{connection_id, model}`。
  `comfyui` の各用途は null なら「既定と同じ」。

今の設定(接続先1組の形)からの移行は `migrate_legacy` が起動時に一度だけ行う(冪等)。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, get_args

from sqlalchemy.orm import Session

from app.domain import api_key as api_key_domain
from app.domain.general_settings import (
    GeneralSettingsValidationError,
    _get_raw_value,
    _save,
    _utcnow,
)
from app.domain.models import AppSetting
from app.i18n import t

if TYPE_CHECKING:
    from app.config import Settings

_KEY_PREFIX = "annotation."
_CONNECTIONS_KEY = _KEY_PREFIX + "connections"
_PROFILES_KEY = _KEY_PREFIX + "profiles"
_OPENAI_API_STYLE_KEY = _KEY_PREFIX + "openai_api_style"
_CONNECTION_SECRET_PREFIX = "annotation_connection_key."

# 移行前(接続先1組の形)の項目。`migrate_legacy` が読み、移したあと消す。
_LEGACY_KEYS = ("base_url", "api_style", "llm_model", "vlm_model")
_LEGACY_SECRET_FIELD = "annotation_api_key"

ApiStyle = Literal["responses", "chat"]
Language = Literal["ja", "en"]
# タグの言語(ADR-0024 6章)。native はエンジン任せ、localized は `language` に合わせる
# (WD Tagger の英語のタグには訳を足す)。
TagLanguage = Literal["native", "localized"]
OnnxModelName = Literal["wd-vit-tagger-v3", "wd-swinv2-tagger-v3", "wd-eva02-large-tagger-v3"]
Profile = Literal["default", "comfyui"]
Purpose = Literal["llm", "vlm"]

API_STYLES: tuple[str, ...] = get_args(ApiStyle)
LANGUAGES: tuple[str, ...] = get_args(Language)
TAG_LANGUAGES: tuple[str, ...] = get_args(TagLanguage)
ONNX_MODEL_NAMES: tuple[str, ...] = get_args(OnnxModelName)
PROFILES: tuple[str, ...] = get_args(Profile)
PURPOSES: tuple[str, ...] = get_args(Purpose)

# 組み込みの接続先(OpenAI の設定。ADR-0017)の id。利用者の接続先には使えない。
BUILTIN_CONNECTION_ID = "openai"
# ComfyUI の画像の組を使う Run のプロバイダー(ADR-0024 8章)。
COMFYUI_PROVIDER = "comfyui"

# 既定のモデル。gpt-5.6-luna は安価なテキストモデルとして選んだ。画像入力(VLM)に
# 対応しているかは要確認(実機で確かめる。ADR-0024 3章)。
DEFAULT_LLM_MODEL = "gpt-5.6-luna"
DEFAULT_VLM_MODEL = "gpt-5.6-luna"
DEFAULT_HOURLY_LIMIT = 100
HOURLY_LIMIT_MIN = 1
HOURLY_LIMIT_MAX = 10000
DEFAULT_ONNX_THRESHOLD = 0.35
ONNX_THRESHOLD_MIN = 0.01
ONNX_THRESHOLD_MAX = 0.99
MODEL_NAME_MAX = 200
CONNECTION_NAME_MAX = 100
CONNECTIONS_MAX = 50
# OpenAI の設定に Base URL が無いとき(OpenAI 本体)の URL。移行で接続先を作るときに使う。
OPENAI_DEFAULT_BASE_URL = "https://api.openai.com/v1"

# 利用者の接続先にキーが無いときに送るダミーのキー。
PLACEHOLDER_API_KEY = "gakei-no-key"


class AnnotationSettingsValidationError(GeneralSettingsValidationError):
    """保存しようとした値が不正(API 層で 422 にする)。"""


class ConnectionNotFoundError(Exception):
    """指定した接続先が無い(API 層で 404、推定では `failed`)。"""


class ConnectionReservedError(Exception):
    """組み込みの接続先は編集・削除できない(API 層で 409)。"""


class ConnectionInUseError(Exception):
    """用途の組で使っている接続先は削除できない(API 層で 409)。"""


# -- 設定の型 ------------------------------------------------------------------


@dataclass(frozen=True)
class ConnectionConfig:
    """推定の接続先1つ。組み込みの `openai` は `base_url=None`(実行時に ADR-0017 の値を使う)。"""

    id: str
    name: str
    base_url: str | None
    api_style: ApiStyle = "responses"

    @property
    def builtin(self) -> bool:
        return self.id == BUILTIN_CONNECTION_ID


@dataclass(frozen=True)
class TargetChoice:
    """用途1つに選んだ「接続先 + モデル名」の組。"""

    connection_id: str
    model: str


def _default_llm_choice() -> TargetChoice:
    return TargetChoice(BUILTIN_CONNECTION_ID, DEFAULT_LLM_MODEL)


def _default_vlm_choice() -> TargetChoice:
    return TargetChoice(BUILTIN_CONNECTION_ID, DEFAULT_VLM_MODEL)


@dataclass(frozen=True)
class Profiles:
    """用途ごとの組。`comfyui_*` が None なら「既定と同じ」。"""

    default_llm: TargetChoice = field(default_factory=_default_llm_choice)
    default_vlm: TargetChoice = field(default_factory=_default_vlm_choice)
    comfyui_llm: TargetChoice | None = None
    comfyui_vlm: TargetChoice | None = None

    def slot(self, profile: str, purpose: str) -> TargetChoice | None:
        """保存されている値そのもの(comfyui の None は None のまま)。"""
        return getattr(self, f"{profile}_{purpose}")

    def choice(self, profile: str, purpose: str) -> TargetChoice:
        """実際に使う組(comfyui が None なら既定の組)。"""
        value = self.slot(profile, purpose)
        if value is None:
            value = self.slot("default", purpose)
        assert value is not None
        return value

    def connection_ids(self) -> set[str]:
        return {
            value.connection_id
            for value in (self.default_llm, self.default_vlm, self.comfyui_llm, self.comfyui_vlm)
            if value is not None
        }


@dataclass(frozen=True)
class AnnotationConfig:
    auto_on_ingest: bool = False
    llm_enabled: bool = False
    vlm_enabled: bool = False
    language: Language = "ja"
    tag_language: TagLanguage = "localized"
    hourly_limit: int = DEFAULT_HOURLY_LIMIT
    onnx_enabled: bool = False
    onnx_model: OnnxModelName = "wd-vit-tagger-v3"
    onnx_threshold: float = DEFAULT_ONNX_THRESHOLD
    # 利用者が登録した接続先(組み込みの openai は含まない。`all_connections` を参照)。
    connections: tuple[ConnectionConfig, ...] = ()
    # 組み込みの接続先(OpenAI の設定)の API 形式。
    openai_api_style: ApiStyle = "responses"
    profiles: Profiles = field(default_factory=Profiles)

    @property
    def api_engines_enabled(self) -> bool:
        return self.llm_enabled or self.vlm_enabled

    def all_connections(self) -> tuple[ConnectionConfig, ...]:
        builtin = ConnectionConfig(
            id=BUILTIN_CONNECTION_ID, name="", base_url=None, api_style=self.openai_api_style
        )
        return (builtin, *self.connections)

    def find_connection(self, connection_id: str) -> ConnectionConfig | None:
        return next((c for c in self.all_connections() if c.id == connection_id), None)

    def connection_ids_for(self, profile: str) -> set[str]:
        """その組で有効な用途(LLM・VLM)が使う接続先。1時間の上限の事前確認に使う。"""
        ids: set[str] = set()
        if self.llm_enabled:
            ids.add(self.profiles.choice(profile, "llm").connection_id)
        if self.vlm_enabled:
            ids.add(self.profiles.choice(profile, "vlm").connection_id)
        return ids


# 画面の PATCH で1項目ずつ変えられる値(接続先と組は別の関数で扱う)。
SCALAR_FIELDS: tuple[str, ...] = tuple(
    f.name
    for f in fields(AnnotationConfig)
    if f.name not in ("connections", "openai_api_style", "profiles")
)
_DEFAULTS = AnnotationConfig()


# -- 検証 --------------------------------------------------------------------


def _is_bool(value: object) -> bool:
    return isinstance(value, bool)


def _is_model_name(value: object) -> bool:
    return isinstance(value, str) and 0 < len(value.strip()) <= MODEL_NAME_MAX


def _is_hourly_limit(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, int)
        and HOURLY_LIMIT_MIN <= value <= HOURLY_LIMIT_MAX
    )


def _is_threshold(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, int | float)
        and ONNX_THRESHOLD_MIN <= float(value) <= ONNX_THRESHOLD_MAX
    )


def normalize_value(name: str, value: Any) -> Any:
    """1項目を検証して保存する形に整える。不正なら AnnotationSettingsValidationError。"""
    if name in ("auto_on_ingest", "llm_enabled", "vlm_enabled", "onnx_enabled"):
        if not _is_bool(value):
            raise AnnotationSettingsValidationError(t("settings.annotation.invalidBool", name=name))
        return value
    if name == "language":
        if value not in LANGUAGES:
            raise AnnotationSettingsValidationError(t("settings.annotation.invalidLanguage"))
        return value
    if name == "tag_language":
        if value not in TAG_LANGUAGES:
            raise AnnotationSettingsValidationError(t("settings.annotation.invalidTagLanguage"))
        return value
    if name == "hourly_limit":
        if not _is_hourly_limit(value):
            raise AnnotationSettingsValidationError(
                t(
                    "settings.annotation.invalidHourlyLimit",
                    min=HOURLY_LIMIT_MIN,
                    max=HOURLY_LIMIT_MAX,
                )
            )
        return value
    if name == "onnx_model":
        if value not in ONNX_MODEL_NAMES:
            raise AnnotationSettingsValidationError(t("settings.annotation.invalidOnnxModel"))
        return value
    if name == "onnx_threshold":
        if not _is_threshold(value):
            raise AnnotationSettingsValidationError(
                t(
                    "settings.annotation.invalidThreshold",
                    min=ONNX_THRESHOLD_MIN,
                    max=ONNX_THRESHOLD_MAX,
                )
            )
        return float(value)
    raise KeyError(name)


def normalize_model(value: object) -> str:
    if not _is_model_name(value):
        raise AnnotationSettingsValidationError(
            t("settings.annotation.invalidModel", max=MODEL_NAME_MAX)
        )
    assert isinstance(value, str)
    return value.strip()


def normalize_api_style(value: object) -> ApiStyle:
    if value not in API_STYLES:
        raise AnnotationSettingsValidationError(t("settings.annotation.invalidApiStyle"))
    return value  # type: ignore[return-value]


def normalize_connection_name(value: object) -> str:
    if not isinstance(value, str) or not (0 < len(value.strip()) <= CONNECTION_NAME_MAX):
        raise AnnotationSettingsValidationError(
            t("settings.annotation.invalidConnectionName", max=CONNECTION_NAME_MAX)
        )
    return value.strip()


def normalize_connection_base_url(value: object) -> str:
    """接続先の Base URL(必須。http / https のみ。ADR-0017 と同じ検証)。"""
    if not isinstance(value, str) or not value.strip():
        raise AnnotationSettingsValidationError(t("openai.baseUrl.invalid"))
    try:
        return api_key_domain.normalize_base_url(value.strip())
    except api_key_domain.BaseUrlValidationError as e:
        raise AnnotationSettingsValidationError(str(e)) from e


def _parse_choice(value: object) -> TargetChoice:
    """`{"connection_id": ..., "model": ...}` を読む。不正なら AnnotationSettingsValidationError
    (接続先があるかどうかは呼び出し側で確かめる)。"""
    if not isinstance(value, dict):
        raise AnnotationSettingsValidationError(t("settings.annotation.invalidTarget"))
    connection_id = value.get("connection_id")
    if not isinstance(connection_id, str) or not connection_id:
        raise AnnotationSettingsValidationError(t("settings.annotation.invalidTarget"))
    return TargetChoice(connection_id=connection_id, model=normalize_model(value.get("model")))


def _choice_to_json(value: TargetChoice | None) -> dict[str, str] | None:
    if value is None:
        return None
    return {"connection_id": value.connection_id, "model": value.model}


def _profiles_to_json(profiles: Profiles) -> dict[str, Any]:
    return {
        profile: {purpose: _choice_to_json(profiles.slot(profile, purpose)) for purpose in PURPOSES}
        for profile in PROFILES
    }


def _profiles_from_json(raw: object) -> Profiles:
    """保存された組を読む。壊れたマスは既定値(comfyui は None)にする。"""
    values: dict[str, TargetChoice | None] = {}
    data = raw if isinstance(raw, dict) else {}
    for profile in PROFILES:
        section = data.get(profile)
        section = section if isinstance(section, dict) else {}
        for purpose in PURPOSES:
            item = section.get(purpose)
            if item is None:
                continue
            try:
                values[f"{profile}_{purpose}"] = _parse_choice(item)
            except AnnotationSettingsValidationError:
                continue
    return Profiles(**values)  # type: ignore[arg-type]


def _connection_to_json(connection: ConnectionConfig) -> dict[str, Any]:
    return {
        "id": connection.id,
        "name": connection.name,
        "base_url": connection.base_url,
        "api_style": connection.api_style,
    }


def _connections_from_json(raw: object) -> tuple[ConnectionConfig, ...]:
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
        except AnnotationSettingsValidationError:
            continue
        seen.add(connection_id)
        result.append(connection)
    return tuple(result)


# -- 読み書き ------------------------------------------------------------------


def load(db: Session) -> AnnotationConfig:
    """保存された設定を読む。無い・壊れている項目は既定値。"""
    values: dict[str, Any] = {}
    for name in SCALAR_FIELDS:
        raw = _get_raw_value(db, _KEY_PREFIX + name)
        if raw is None:
            continue
        try:
            values[name] = normalize_value(name, raw)
        except AnnotationSettingsValidationError:
            continue
    values["connections"] = _connections_from_json(_get_raw_value(db, _CONNECTIONS_KEY))
    raw_style = _get_raw_value(db, _OPENAI_API_STYLE_KEY)
    if raw_style in API_STYLES:
        values["openai_api_style"] = raw_style
    values["profiles"] = _profiles_from_json(_get_raw_value(db, _PROFILES_KEY))
    return replace(_DEFAULTS, **values)


def save(db: Session, updates: dict[str, Any]) -> None:
    """1項目ずつ変えられる値(`SCALAR_FIELDS`)を検証してからまとめて保存する(1項目だけ
    保存される事態を避ける)。"""
    normalized = {name: normalize_value(name, value) for name, value in updates.items()}
    for name, value in normalized.items():
        _save(db, _KEY_PREFIX + name, value)


def validate_profile_updates(
    config: AnnotationConfig, updates: dict[str, dict[str, Any]]
) -> Profiles:
    """組の変更(`{"default": {"llm": {...}}, "comfyui": {"vlm": None}}` の形。書いたマスだけ
    変える)を検証し、変えた後の組を返す。保存はしない。

    `default` のマスは null にできない。接続先は登録済みのものだけ。
    """
    values: dict[str, TargetChoice | None] = {
        f"{profile}_{purpose}": config.profiles.slot(profile, purpose)
        for profile in PROFILES
        for purpose in PURPOSES
    }
    for profile, section in updates.items():
        if profile not in PROFILES or not isinstance(section, dict):
            raise AnnotationSettingsValidationError(t("settings.annotation.invalidTarget"))
        for purpose, item in section.items():
            if purpose not in PURPOSES:
                raise AnnotationSettingsValidationError(t("settings.annotation.invalidTarget"))
            if item is None:
                if profile == "default":
                    raise AnnotationSettingsValidationError(t("settings.annotation.invalidTarget"))
                values[f"{profile}_{purpose}"] = None
                continue
            choice = _parse_choice(item)
            if config.find_connection(choice.connection_id) is None:
                raise AnnotationSettingsValidationError(t("settings.annotation.unknownConnection"))
            values[f"{profile}_{purpose}"] = choice
    return Profiles(**values)  # type: ignore[arg-type]


def save_profiles(db: Session, profiles: Profiles) -> None:
    _save(db, _PROFILES_KEY, _profiles_to_json(profiles))


def _save_connections(db: Session, connections: tuple[ConnectionConfig, ...]) -> None:
    _save(db, _CONNECTIONS_KEY, [_connection_to_json(c) for c in connections])


def add_connection(db: Session, name: object, base_url: object, api_style: object) -> str:
    """接続先を足して、その id を返す。"""
    config = load(db)
    if len(config.connections) >= CONNECTIONS_MAX:
        raise AnnotationSettingsValidationError(
            t("settings.annotation.tooManyConnections", max=CONNECTIONS_MAX)
        )
    normalized_url = normalize_connection_base_url(base_url)
    connection = ConnectionConfig(
        id=uuid.uuid4().hex,
        name=normalize_connection_name(name),
        base_url=normalized_url,
        api_style=normalize_api_style(api_style),
    )
    api_key_domain.warn_if_insecure_base_url(normalized_url)
    _save_connections(db, (*config.connections, connection))
    return connection.id


def update_connection(db: Session, connection_id: str, updates: dict[str, Any]) -> None:
    """接続先の名前・Base URL・API 形式を変える(書いた項目だけ)。組み込みの接続先は API 形式
    だけ変えられる。"""
    if connection_id == BUILTIN_CONNECTION_ID:
        if set(updates) - {"api_style"}:
            raise ConnectionReservedError
        if "api_style" in updates:
            _save(db, _OPENAI_API_STYLE_KEY, normalize_api_style(updates["api_style"]))
        return
    config = load(db)
    current = config.find_connection(connection_id)
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
    _save_connections(
        db, tuple(changed if c.id == connection_id else c for c in config.connections)
    )


def delete_connection(db: Session, data_dir: Path, connection_id: str) -> None:
    """接続先を消す(キーも消す)。組み込み・使用中なら例外。"""
    if connection_id == BUILTIN_CONNECTION_ID:
        raise ConnectionReservedError
    config = load(db)
    if config.find_connection(connection_id) is None:
        raise ConnectionNotFoundError
    if connection_id in config.profiles.connection_ids():
        raise ConnectionInUseError
    _save_connections(db, tuple(c for c in config.connections if c.id != connection_id))
    delete_connection_key(data_dir, connection_id)


# -- 接続先のキー(secrets.json) ------------------------------------------------


def _secret_field(connection_id: str) -> str:
    return _CONNECTION_SECRET_PREFIX + connection_id


def read_connection_key(data_dir: Path, connection_id: str) -> str | None:
    return api_key_domain.read_secret_field(data_dir, _secret_field(connection_id))


def write_connection_key(data_dir: Path, connection_id: str, value: str) -> None:
    api_key_domain.write_secret_field(data_dir, _secret_field(connection_id), value)


def delete_connection_key(data_dir: Path, connection_id: str) -> None:
    api_key_domain.delete_secret_field(data_dir, _secret_field(connection_id))


# -- 接続先の解決 ----------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    """推定1用途の送り先(接続先の実際のキーと Base URL、API 形式、モデル名)。

    キーが無ければ `api_key=None`(組み込みの接続先で OpenAI のキーが未設定のとき)。
    """

    connection_id: str
    model: str
    api_key: str | None
    base_url: str | None
    api_style: ApiStyle = "responses"


def profile_for_provider(provider: str | None) -> Profile:
    """Asset を作った Run のプロバイダーから、使う組を決める(ADR-0024 8章)。アップロード・
    スケッチ(Run が無い)と ComfyUI 以外のプロバイダーは既定。"""
    return "comfyui" if provider == COMFYUI_PROVIDER else "default"


def resolve_target(
    config: AnnotationConfig, settings: Settings, profile: str, purpose: str
) -> Target:
    """組と用途から送り先を決める。接続先が無ければ ConnectionNotFoundError(既定の組には
    切り替えない)。"""
    choice = config.profiles.choice(profile, purpose)
    connection = config.find_connection(choice.connection_id)
    if connection is None:
        raise ConnectionNotFoundError(choice.connection_id)
    if connection.builtin:
        openai_key, _ = api_key_domain.resolve_key(settings)
        base_url, _ = api_key_domain.resolve_base_url(settings)
        return Target(
            connection_id=connection.id,
            model=choice.model,
            api_key=openai_key,
            base_url=base_url,
            api_style=connection.api_style,
        )
    own_key = read_connection_key(settings.data_dir, connection.id)
    return Target(
        connection_id=connection.id,
        model=choice.model,
        api_key=own_key or PLACEHOLDER_API_KEY,
        base_url=connection.base_url,
        api_style=connection.api_style,
    )


# -- 今の設定からの移行(ADR-0024 8章) --------------------------------------------


def _put(db: Session, key: str, value: object) -> None:
    """commit せずに保存する(移行を1回の commit にまとめるため)。"""
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value={"value": value}, updated_at=_utcnow()))
    else:
        row.value = {"value": value}
        row.updated_at = _utcnow()


def migrate_legacy(db: Session, settings: Settings) -> bool:
    """接続先1組の形(`annotation.base_url` など)の設定を、接続先の一覧と用途ごとの組に移す。
    起動時に呼ぶ。移すものが無ければ何もしない(冪等)。移したら True。

    - 推定専用の Base URL があれば、接続先「推定専用」を作り、推定専用キーと API 形式を移す。
    - 推定専用の Base URL が無く推定専用キーだけがあれば、今は OpenAI の Base URL にそのキーで
      送っているので、その時点の OpenAI の Base URL(無ければ OpenAI 本体)で「推定専用」を
      作る(組み込みの接続先は OpenAI のキーしか使えないため)。
    - どちらも無ければ組み込みの接続先を使い、API 形式は組み込みの接続先に移す。
    - LLM と VLM のモデル名は「既定」の組に入れる。
    - 既に組(`annotation.profiles`)があれば、古い項目を消すだけにする。
    """
    data_dir = settings.data_dir
    legacy_rows = [
        name for name in _LEGACY_KEYS if db.get(AppSetting, _KEY_PREFIX + name) is not None
    ]
    legacy_key = api_key_domain.read_secret_field(data_dir, _LEGACY_SECRET_FIELD)
    if not legacy_rows and legacy_key is None:
        return False

    if db.get(AppSetting, _PROFILES_KEY) is None:
        legacy = {name: _get_raw_value(db, _KEY_PREFIX + name) for name in _LEGACY_KEYS}
        base_url: str | None = None
        if legacy["base_url"] is not None:
            try:
                base_url = normalize_connection_base_url(legacy["base_url"])
            except AnnotationSettingsValidationError:
                base_url = None
        api_style: ApiStyle = "responses"
        if legacy["api_style"] in API_STYLES:
            api_style = legacy["api_style"]  # type: ignore[assignment]
        llm_model = DEFAULT_LLM_MODEL
        if _is_model_name(legacy["llm_model"]):
            llm_model = str(legacy["llm_model"]).strip()
        vlm_model = DEFAULT_VLM_MODEL
        if _is_model_name(legacy["vlm_model"]):
            vlm_model = str(legacy["vlm_model"]).strip()

        connection_id = BUILTIN_CONNECTION_ID
        if base_url is not None or legacy_key is not None:
            if base_url is None:
                openai_base, _ = api_key_domain.resolve_base_url(settings)
                base_url = openai_base or OPENAI_DEFAULT_BASE_URL
            connection = ConnectionConfig(
                id=uuid.uuid4().hex,
                name=t("settings.annotation.migratedConnectionName"),
                base_url=base_url,
                api_style=api_style,
            )
            connections = _connections_from_json(_get_raw_value(db, _CONNECTIONS_KEY))
            _put(
                db,
                _CONNECTIONS_KEY,
                [_connection_to_json(c) for c in (*connections, connection)],
            )
            connection_id = connection.id
            if legacy_key is not None:
                write_connection_key(data_dir, connection_id, legacy_key)
        elif api_style != "responses":
            _put(db, _OPENAI_API_STYLE_KEY, api_style)

        profiles = Profiles(
            default_llm=TargetChoice(connection_id, llm_model),
            default_vlm=TargetChoice(connection_id, vlm_model),
        )
        _put(db, _PROFILES_KEY, _profiles_to_json(profiles))

    for name in legacy_rows:
        row = db.get(AppSetting, _KEY_PREFIX + name)
        if row is not None:
            db.delete(row)
    db.commit()
    if legacy_key is not None:
        api_key_domain.delete_secret_field(data_dir, _LEGACY_SECRET_FIELD)
    return True
