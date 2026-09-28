"""自動タイトル・タグの管理者設定(ADR-0024 3章・4章・5章)。

値は項目ごとに `app_setting` の `annotation.<項目名>` に保存する(`mcp_settings` と同じ形式
`{"value": ...}`。画面で保存した値だけ。環境変数の既定値は持たない)。壊れた値・範囲外の値は
既定値として扱う。推定専用の API キーだけは `DATA_DIR/secrets.json` の
`annotation_api_key` に保存する(`app/domain/api_key.py` と同じファイル・同じ書き込み)。

接続先の解決(`resolve_connection`):

- 推定専用の Base URL が空なら、OpenAI の設定(キー、Base URL。ADR-0017)をそのまま使う。
  推定専用キーがあればキーだけ差し替える。
- 推定専用の Base URL を設定したときは、推定専用キーがあればそれを送る。無ければ OpenAI の
  キーは送らない(手元の Ollama・LM Studio などへ OpenAI のキーを漏らさないため)。SDK は
  空のキーを受け付けないので、代わりに固定のダミー文字列を送る。
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, get_args

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

_KEY_PREFIX = "annotation."
_SECRET_FIELD = "annotation_api_key"

ApiStyle = Literal["responses", "chat"]
Language = Literal["ja", "en"]
# タグの言語(ADR-0024 6章)。native はエンジン任せ、localized は `language` に合わせる
# (WD Tagger の英語のタグには訳を足す)。
TagLanguage = Literal["native", "localized"]
OnnxModelName = Literal["wd-vit-tagger-v3", "wd-swinv2-tagger-v3", "wd-eva02-large-tagger-v3"]

API_STYLES: tuple[str, ...] = get_args(ApiStyle)
LANGUAGES: tuple[str, ...] = get_args(Language)
TAG_LANGUAGES: tuple[str, ...] = get_args(TagLanguage)
ONNX_MODEL_NAMES: tuple[str, ...] = get_args(OnnxModelName)

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

# 推定専用の Base URL を設定し、推定専用キーが無いときに送るダミーのキー。
PLACEHOLDER_API_KEY = "gakei-no-key"


class AnnotationSettingsValidationError(GeneralSettingsValidationError):
    """保存しようとした値が不正(API 層で 422 にする)。"""


@dataclass(frozen=True)
class AnnotationConfig:
    auto_on_ingest: bool = False
    llm_enabled: bool = False
    llm_model: str = DEFAULT_LLM_MODEL
    vlm_enabled: bool = False
    vlm_model: str = DEFAULT_VLM_MODEL
    base_url: str | None = None
    api_style: ApiStyle = "responses"
    language: Language = "ja"
    tag_language: TagLanguage = "localized"
    hourly_limit: int = DEFAULT_HOURLY_LIMIT
    onnx_enabled: bool = False
    onnx_model: OnnxModelName = "wd-vit-tagger-v3"
    onnx_threshold: float = DEFAULT_ONNX_THRESHOLD

    @property
    def api_engines_enabled(self) -> bool:
        return self.llm_enabled or self.vlm_enabled


FIELD_NAMES: tuple[str, ...] = tuple(f.name for f in fields(AnnotationConfig))
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
    """1項目を検証して保存する形に整える。不正なら AnnotationSettingsValidationError。

    `base_url` は空文字・None を「OpenAI の設定を流用」(None)として受け付ける。
    """
    if name in ("auto_on_ingest", "llm_enabled", "vlm_enabled", "onnx_enabled"):
        if not _is_bool(value):
            raise AnnotationSettingsValidationError(t("settings.annotation.invalidBool", name=name))
        return value
    if name in ("llm_model", "vlm_model"):
        if not _is_model_name(value):
            raise AnnotationSettingsValidationError(
                t("settings.annotation.invalidModel", max=MODEL_NAME_MAX)
            )
        return value.strip()
    if name == "base_url":
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        if not isinstance(value, str):
            raise AnnotationSettingsValidationError(t("openai.baseUrl.invalid"))
        try:
            return api_key_domain.normalize_base_url(value.strip())
        except api_key_domain.BaseUrlValidationError as e:
            raise AnnotationSettingsValidationError(str(e)) from e
    if name == "api_style":
        if value not in API_STYLES:
            raise AnnotationSettingsValidationError(t("settings.annotation.invalidApiStyle"))
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


# -- 読み書き ------------------------------------------------------------------


def load(db: Session) -> AnnotationConfig:
    """保存された設定を読む。無い・壊れている項目は既定値。"""
    values: dict[str, Any] = {}
    for name in FIELD_NAMES:
        raw = _get_raw_value(db, _KEY_PREFIX + name)
        if raw is None:
            continue
        try:
            values[name] = normalize_value(name, raw)
        except AnnotationSettingsValidationError:
            continue
    return AnnotationConfig(**{**_DEFAULTS.__dict__, **values})


def save(db: Session, updates: dict[str, Any]) -> None:
    """検証してからまとめて保存する(1項目だけ保存される事態を避ける)。"""
    normalized = {name: normalize_value(name, value) for name, value in updates.items()}
    for name, value in normalized.items():
        key = _KEY_PREFIX + name
        if name == "base_url" and value is None:
            _delete(db, key)
            continue
        if name == "base_url":
            api_key_domain.warn_if_insecure_base_url(value)
        _save(db, key, value)


# -- 推定専用の API キー(secrets.json) -----------------------------------------


def read_api_key(data_dir: Path) -> str | None:
    return api_key_domain.read_secret_field(data_dir, _SECRET_FIELD)


def write_api_key(data_dir: Path, value: str) -> None:
    api_key_domain.write_secret_field(data_dir, _SECRET_FIELD, value)


def delete_api_key(data_dir: Path) -> None:
    api_key_domain.delete_secret_field(data_dir, _SECRET_FIELD)


@dataclass(frozen=True)
class Connection:
    api_key: str | None
    base_url: str | None


def resolve_connection(config: AnnotationConfig, settings: Settings) -> Connection:
    """LLM・VLM の接続先とキー(モジュールの docstring 参照)。キーが無ければ api_key=None。"""
    own_key = read_api_key(settings.data_dir)
    if config.base_url:
        return Connection(api_key=own_key or PLACEHOLDER_API_KEY, base_url=config.base_url)
    openai_key, _ = api_key_domain.resolve_key(settings)
    base_url, _ = api_key_domain.resolve_base_url(settings)
    return Connection(api_key=own_key or openai_key, base_url=base_url)
