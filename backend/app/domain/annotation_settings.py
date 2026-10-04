"""自動タイトル・タグの管理者設定(ADR-0024 3章・4章・5章・8章)。

値は項目ごとに `app_setting` の `annotation.<項目名>` に保存する(`mcp_settings` と同じ形式
`{"value": ...}`。画面で保存した値だけ。環境変数の既定値は持たない)。壊れた値・範囲外の値は
既定値として扱う。

接続先とモデルの組(ADR-0024 8章):

- **接続先**: LLM の接続先(`app/domain/llm_connections.py`。ADR-0032)から選ぶ。接続先の一覧と
  キーの読み書きは向こうにある。この設定は、用途ごとの組で使っている接続先を
  `llm_connections.register_usage` で答える(使っている接続先は削除できない)。
- **用途ごとの組**(`annotation.profiles`): 「既定」(`default`)と「ComfyUI の画像」
  (`comfyui`)のそれぞれに、タイトル(`llm`)とタグ(`vlm`)の `{connection_id, model}`。
  `comfyui` の各用途は null なら「既定と同じ」。

今の設定(接続先1組の形)からの移行は `migrate_legacy` が起動時に一度だけ行う(冪等)。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field, fields, replace
from typing import TYPE_CHECKING, Any, Literal, get_args

from sqlalchemy.orm import Session

from app.domain import api_key as api_key_domain
from app.domain import llm_connections
from app.domain.general_settings import (
    GeneralSettingsValidationError,
    _get_raw_value,
    _save,
    _utcnow,
)
from app.domain.llm_connections import (
    API_STYLES,
    BUILTIN_CONNECTION_ID,
    ApiStyle,
    ConnectionConfig,
    ConnectionNotFoundError,
)
from app.domain.models import AppSetting
from app.i18n import t

if TYPE_CHECKING:
    from app.config import Settings

logger = logging.getLogger(__name__)

_KEY_PREFIX = "annotation."
_PROFILES_KEY = _KEY_PREFIX + "profiles"

# 移行前(接続先1組の形)の項目。`migrate_legacy` が読み、移したあと消す。
_LEGACY_KEYS = ("base_url", "api_style", "llm_model", "vlm_model")
_LEGACY_SECRET_FIELD = "annotation_api_key"
# 移行で旧キーを移す先の接続先 id。DB の commit の後にキーを書くので、書き終えるまでの目印に
# する(キーを書く前に落ちても、次の起動でこの目印と残った旧キーから書き直す)。
_LEGACY_KEY_TARGET_KEY = _KEY_PREFIX + "legacy_key_connection"

Language = Literal["ja", "en"]
# タグの言語(ADR-0024 6章)。native はエンジン任せ、localized は `language` に合わせる
# (WD Tagger の英語のタグには訳を足す)。
TagLanguage = Literal["native", "localized"]
OnnxModelName = Literal["wd-vit-tagger-v3", "wd-swinv2-tagger-v3", "wd-eva02-large-tagger-v3"]
Profile = Literal["default", "comfyui"]
Purpose = Literal["llm", "vlm"]

LANGUAGES: tuple[str, ...] = get_args(Language)
TAG_LANGUAGES: tuple[str, ...] = get_args(TagLanguage)
ONNX_MODEL_NAMES: tuple[str, ...] = get_args(OnnxModelName)
PROFILES: tuple[str, ...] = get_args(Profile)
PURPOSES: tuple[str, ...] = get_args(Purpose)

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


class AnnotationSettingsValidationError(GeneralSettingsValidationError):
    """保存しようとした値が不正(API 層で 422 にする)。"""


# -- 設定の型 ------------------------------------------------------------------


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
    # LLM の接続先のうち利用者が登録したもの(組み込みの openai は含まない。`all_connections`
    # を参照)。`llm_connections` から読む。
    connections: tuple[ConnectionConfig, ...] = ()
    # 組み込みの接続先(OpenAI の設定)の API 形式。
    openai_api_style: ApiStyle = "responses"
    profiles: Profiles = field(default_factory=Profiles)

    @property
    def api_engines_enabled(self) -> bool:
        return self.llm_enabled or self.vlm_enabled

    def all_connections(self) -> tuple[ConnectionConfig, ...]:
        return llm_connections.all_connections(self.connections, self.openai_api_style)

    def find_connection(self, connection_id: str) -> ConnectionConfig | None:
        return llm_connections.find_connection(self.all_connections(), connection_id)

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
    values["connections"] = llm_connections.load_user_connections(db)
    values["openai_api_style"] = llm_connections.load_openai_api_style(db)
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


def used_connection_ids(db: Session) -> set[str]:
    """用途ごとの組(既定 / ComfyUI の画像)のどこかで使っている接続先。有効かどうかに関わらず
    数える(LLM の接続先の「使っている機能」と、削除の禁止に使う。ADR-0032 3章)。"""
    return _profiles_from_json(_get_raw_value(db, _PROFILES_KEY)).connection_ids()


FEATURE_ID = "annotation"
llm_connections.register_usage(FEATURE_ID, used_connection_ids)


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
    resolved = llm_connections.resolve(connection, settings)
    return Target(
        connection_id=connection.id,
        model=choice.model,
        api_key=resolved.api_key,
        base_url=resolved.base_url,
        api_style=resolved.api_style,
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
    - 既に組(`annotation.profiles`)があれば、古い項目を消すだけにする。旧キーが残っていても
      どの接続先のものか分からないので、消さずに残して警告をログに出す。
    - 旧キーは DB の commit の後に接続先へ書く(commit に失敗しても旧キーは残り、次の起動で
      最初からやり直せる)。書く先は目印(`annotation.legacy_key_connection`)として同じ commit
      で残し、キーを書く前に落ちたときは次の起動で目印から書き直す。
    """
    data_dir = settings.data_dir
    legacy_rows = [
        name for name in _LEGACY_KEYS if db.get(AppSetting, _KEY_PREFIX + name) is not None
    ]
    legacy_key = api_key_domain.read_secret_field(data_dir, _LEGACY_SECRET_FIELD)
    key_target = _get_raw_value(db, _LEGACY_KEY_TARGET_KEY)
    if not isinstance(key_target, str):
        key_target = None
    if not legacy_rows and legacy_key is None and key_target is None:
        return False

    changed = bool(legacy_rows) or key_target is not None
    if db.get(AppSetting, _PROFILES_KEY) is None:
        changed = True
        legacy = {name: _get_raw_value(db, _KEY_PREFIX + name) for name in _LEGACY_KEYS}
        base_url: str | None = None
        if legacy["base_url"] is not None:
            try:
                base_url = llm_connections.normalize_connection_base_url(legacy["base_url"])
            except llm_connections.LlmConnectionValidationError:
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
                base_url = openai_base or llm_connections.OPENAI_DEFAULT_BASE_URL
            connection = ConnectionConfig(
                id=uuid.uuid4().hex,
                name=t("settings.annotation.migratedConnectionName"),
                base_url=base_url,
                api_style=api_style,
            )
            connections = llm_connections.load_user_connections(db)
            _put(
                db,
                llm_connections.CONNECTIONS_KEY,
                [llm_connections.connection_to_json(c) for c in (*connections, connection)],
            )
            connection_id = connection.id
            if legacy_key is not None:
                _put(db, _LEGACY_KEY_TARGET_KEY, connection_id)
                key_target = connection_id
        elif api_style != "responses":
            _put(db, llm_connections.OPENAI_API_STYLE_KEY, api_style)

        profiles = Profiles(
            default_llm=TargetChoice(connection_id, llm_model),
            default_vlm=TargetChoice(connection_id, vlm_model),
        )
        _put(db, _PROFILES_KEY, _profiles_to_json(profiles))
    elif legacy_key is not None and key_target is None:
        logger.warning(
            "推定の組は既にありますが、旧い推定専用キー(secrets.json の %s)が残っています。"
            "どの接続先のキーか分からないので、移さずに残します。必要なら管理者設定で接続先に"
            "キーを設定し、secrets.json からこの項目を消してください。",
            _LEGACY_SECRET_FIELD,
        )

    for name in legacy_rows:
        row = db.get(AppSetting, _KEY_PREFIX + name)
        if row is not None:
            db.delete(row)
    db.commit()

    # キーは commit の後に書く(上の docstring)。
    if key_target is not None:
        if legacy_key is not None:
            connections = llm_connections.load_user_connections(db)
            if any(c.id == key_target for c in connections):
                llm_connections.write_connection_key(data_dir, key_target, legacy_key)
                api_key_domain.delete_secret_field(data_dir, _LEGACY_SECRET_FIELD)
            else:
                logger.warning(
                    "旧い推定専用キーを移す先の接続先が見つからないので、移さずに残します。"
                )
        row = db.get(AppSetting, _LEGACY_KEY_TARGET_KEY)
        if row is not None:
            db.delete(row)
            db.commit()
    return changed
