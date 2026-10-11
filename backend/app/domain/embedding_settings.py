"""画像の埋め込みの管理者設定(ADR-0033 2章・3章・5章)。

値は項目ごとに `app_setting` の `embedding.<項目名>` に保存する(`annotation_settings` と
同じ形式 `{"value": ...}`。画面で保存した値だけ。環境変数の既定値は持たない)。壊れた値・
範囲外の値は既定値として扱う。

- 使うモデル(エンジン + モデル)はインスタンス全体で1つ(`active_model_key`)。
- リモートの接続先は「LLM の接続先」(ADR-0032)から選ぶ。組み込みの接続先(OpenAI の設定)は
  選べない(OpenAI の API は画像の埋め込みを受け付けず、画像を OpenAI に送ってしまうため)。
  使っている接続先は `llm_connections.register_usage` で答える(削除できなくなる)。
- `FAKE_PROVIDER=1` では、どのモデルを選んでもダミーのエンジンで計算する。本物のベクトルと
  混ざらないよう、`model_key` の先頭に `fake:` を付ける。
- 重複の候補のしきい値はモデルごと(ADR-0044 5章)。既定はカタログ(リモートは 0.90)に持ち、
  管理者が変えた値は `embedding.duplicate_thresholds` に、モデルの識別子(`threshold_key`。
  ローカルは `onnx:<モデル>`、リモートは `remote:<接続先 id>:<モデル名>`)ごとに保存する。
  画面と API の `duplicate_threshold` は、使うモデルの値。
  - モデルごとにする前の値(`embedding.duplicate_threshold`)は、そのとき使っていたモデルの
    値とみなす。使うモデルは `save` を通してしか変わらないので、`save` の最初に、変わる前の
    モデルの値として `duplicate_thresholds` に移し、古い項目を消す(マイグレーションは要らない)。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Literal, get_args

from sqlalchemy.orm import Session

from app.domain import llm_connections
from app.domain.general_settings import (
    GeneralSettingsValidationError,
    _delete,
    _get_raw_value,
    _save,
)
from app.embedding.catalog import (
    CLIP_DUPLICATE_THRESHOLD,
    CLIP_MODELS,
    DEFAULT_MODEL,
    InputVariant,
    is_downloaded,
    onnx_model_key,
)
from app.embedding.remote import remote_model_key
from app.i18n import t

if TYPE_CHECKING:
    from app.config import Settings

_KEY_PREFIX = "embedding."

EngineKind = Literal["onnx", "remote"]
RemoteApiFormat = Literal["infinity"]
ENGINE_KINDS: tuple[str, ...] = get_args(EngineKind)
REMOTE_API_FORMATS: tuple[str, ...] = get_args(RemoteApiFormat)

# 強い劣化(縮小と JPEG の q60 で 0.936)も拾う。色違いのような別の画像は知覚ハッシュで外す
# (ADR-0033 12章)。ローカルのモデルの既定はカタログに持つ。これはリモートと、モデルが
# 分からないときの既定。
DEFAULT_DUPLICATE_THRESHOLD = CLIP_DUPLICATE_THRESHOLD
_THRESHOLDS_KEY = _KEY_PREFIX + "duplicate_thresholds"
# モデルごとにする前のしきい値(モジュールの docstring)。
_LEGACY_THRESHOLD_KEY = _KEY_PREFIX + "duplicate_threshold"
DUPLICATE_THRESHOLD_MIN = 0.5
DUPLICATE_THRESHOLD_MAX = 1.0
# `model_key`(VARCHAR(200))に収まるように: `fake:remote:<接続先 id 32文字>:<モデル名>`。
MODEL_NAME_MAX = 150
FAKE_KEY_PREFIX = "fake:"


class EmbeddingSettingsValidationError(GeneralSettingsValidationError):
    """保存しようとした値が不正(API 層で 422 にする)。"""


@dataclass(frozen=True)
class EmbeddingConfig:
    enabled: bool = False
    engine: EngineKind = "onnx"
    onnx_model: str = DEFAULT_MODEL
    remote_connection_id: str | None = None
    remote_model: str | None = None
    remote_api_format: RemoteApiFormat = "infinity"
    # 埋め込みを有効にしたときの既定はオン(ADR-0033 5章)。
    auto_on_ingest: bool = True
    # 使うモデルの重複のしきい値(保存した値、無ければそのモデルの既定。`load` が決める)。
    duplicate_threshold: float = DEFAULT_DUPLICATE_THRESHOLD
    # 管理者が変えたしきい値(`threshold_key` → 値)。
    duplicate_thresholds: dict[str, float] = field(default_factory=dict)


# 項目ごとに `embedding.<項目名>` に保存する項目。
STORED_FIELDS: tuple[str, ...] = (
    "enabled",
    "engine",
    "onnx_model",
    "remote_connection_id",
    "remote_model",
    "remote_api_format",
    "auto_on_ingest",
)
# API(画面)に出す項目と、`save` で受け付ける項目。
FIELDS: tuple[str, ...] = (*STORED_FIELDS, "duplicate_threshold")
_DEFAULTS = EmbeddingConfig()


# -- 検証 --------------------------------------------------------------------


def _is_threshold(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, int | float)
        and DUPLICATE_THRESHOLD_MIN <= float(value) <= DUPLICATE_THRESHOLD_MAX
    )


def normalize_value(db: Session, name: str, value: Any) -> Any:
    """1項目を検証して保存する形に整える。不正なら EmbeddingSettingsValidationError。
    `remote_connection_id` と `remote_model` は null(未設定)にできる。"""
    if name in ("enabled", "auto_on_ingest"):
        if not isinstance(value, bool):
            raise EmbeddingSettingsValidationError(t("settings.embedding.invalidBool", name=name))
        return value
    if name == "engine":
        if value not in ENGINE_KINDS:
            raise EmbeddingSettingsValidationError(t("settings.embedding.invalidEngine"))
        return value
    if name == "onnx_model":
        if value not in CLIP_MODELS:
            raise EmbeddingSettingsValidationError(
                t("settings.embedding.invalidOnnxModel", models=", ".join(CLIP_MODELS))
            )
        return value
    if name == "remote_connection_id":
        if value is None:
            return None
        if not isinstance(value, str) or not value:
            raise EmbeddingSettingsValidationError(t("settings.embedding.unknownConnection"))
        if value == llm_connections.BUILTIN_CONNECTION_ID:
            raise EmbeddingSettingsValidationError(t("settings.embedding.builtinConnection"))
        if (
            llm_connections.find_connection(llm_connections.load_user_connections(db), value)
            is None
        ):
            raise EmbeddingSettingsValidationError(t("settings.embedding.unknownConnection"))
        return value
    if name == "remote_model":
        if value is None:
            return None
        if not isinstance(value, str) or not 0 < len(value.strip()) <= MODEL_NAME_MAX:
            raise EmbeddingSettingsValidationError(
                t("settings.embedding.invalidModel", max=MODEL_NAME_MAX)
            )
        return value.strip()
    if name == "remote_api_format":
        if value not in REMOTE_API_FORMATS:
            raise EmbeddingSettingsValidationError(t("settings.embedding.invalidApiFormat"))
        return value
    if name == "duplicate_threshold":
        if not _is_threshold(value):
            raise EmbeddingSettingsValidationError(
                t(
                    "settings.embedding.invalidThreshold",
                    min=DUPLICATE_THRESHOLD_MIN,
                    max=DUPLICATE_THRESHOLD_MAX,
                )
            )
        return float(value)
    raise KeyError(name)


# -- 読み書き ------------------------------------------------------------------


def _load_thresholds(db: Session) -> dict[str, float]:
    raw = _get_raw_value(db, _THRESHOLDS_KEY)
    if not isinstance(raw, dict):
        return {}
    return {
        key: float(value)
        for key, value in raw.items()
        if isinstance(key, str) and key and _is_threshold(value)
    }


def _load_legacy_threshold(db: Session) -> float | None:
    raw = _get_raw_value(db, _LEGACY_THRESHOLD_KEY)
    return float(raw) if _is_threshold(raw) else None  # type: ignore[arg-type]


def load(db: Session) -> EmbeddingConfig:
    """保存された設定を読む。無い・壊れている項目は既定値(消えた接続先は未設定に戻す)。"""
    values: dict[str, Any] = {}
    for name in STORED_FIELDS:
        raw = _get_raw_value(db, _KEY_PREFIX + name)
        if raw is None:
            continue
        try:
            values[name] = normalize_value(db, name, raw)
        except EmbeddingSettingsValidationError:
            continue
    config = replace(_DEFAULTS, **values, duplicate_thresholds=_load_thresholds(db))
    return replace(
        config, duplicate_threshold=_effective_threshold(config, _load_legacy_threshold(db))
    )


def validate(db: Session, updates: dict[str, Any]) -> dict[str, Any]:
    """全項目を検証して、保存する形の値を返す(保存はしない)。"""
    return {name: normalize_value(db, name, value) for name, value in updates.items()}


def save(db: Session, updates: dict[str, Any]) -> None:
    """検証してからまとめて保存する(1項目だけ保存される事態を避ける)。

    `duplicate_threshold` は、同じ更新でモデルを変えるなら変えた後のモデルの値として保存する。
    """
    values = validate(db, updates)
    current = load(db)
    threshold = values.pop("duplicate_threshold", None)
    after = replace(current, **values)
    threshold_target = threshold_key(after) if threshold is not None else None
    if threshold is not None and threshold_target is None:
        raise EmbeddingSettingsValidationError(t("settings.embedding.thresholdNoModel"))

    thresholds = dict(current.duplicate_thresholds)
    legacy = _load_legacy_threshold(db)
    before = threshold_key(current)
    # 変わる前のモデルが分からない(リモートで未設定)間は、古い値を残しておく。
    drop_legacy = _get_raw_value(db, _LEGACY_THRESHOLD_KEY) is not None and (
        legacy is None or before is not None
    )
    if legacy is not None and before is not None:
        # 古い値は、変わる前のモデルの値(モジュールの docstring)。
        thresholds.setdefault(before, legacy)
    if threshold_target is not None:
        thresholds[threshold_target] = float(threshold)  # type: ignore[arg-type]

    for name, value in values.items():
        _save(db, _KEY_PREFIX + name, value)
    if thresholds != current.duplicate_thresholds:
        _save(db, _THRESHOLDS_KEY, thresholds)
    if drop_legacy:
        _delete(db, _LEGACY_THRESHOLD_KEY)


# -- 重複のしきい値(ADR-0044 5章) -----------------------------------------------------


def threshold_key(config: EmbeddingConfig) -> str | None:
    """しきい値を保存するときのモデルの識別子。リモートで接続先かモデル名が未設定なら None。

    リビジョンは含めない(同じモデルの版を上げても、管理者の値を引き継ぐ)。
    """
    if config.engine == "onnx":
        return f"onnx:{config.onnx_model}"
    if not config.remote_connection_id or not config.remote_model:
        return None
    return remote_model_key(config.remote_connection_id, config.remote_model)


def default_threshold(config: EmbeddingConfig) -> float:
    """使うモデルのしきい値の既定(ローカルはカタログ、リモートは 0.90)。"""
    if config.engine == "onnx" and config.onnx_model in CLIP_MODELS:
        return CLIP_MODELS[config.onnx_model].duplicate_threshold
    return DEFAULT_DUPLICATE_THRESHOLD


def onnx_threshold(config: EmbeddingConfig, name: str) -> float:
    """ローカルのモデル `name` のしきい値(保存した値、無ければカタログの既定)。今使っている
    モデルなら、移す前の古い値も含めた `duplicate_threshold` と同じ。"""
    if config.engine == "onnx" and config.onnx_model == name:
        return config.duplicate_threshold
    return config.duplicate_thresholds.get(f"onnx:{name}", CLIP_MODELS[name].duplicate_threshold)


def _effective_threshold(config: EmbeddingConfig, legacy: float | None) -> float:
    key = threshold_key(config)
    if key is not None and key in config.duplicate_thresholds:
        return config.duplicate_thresholds[key]
    if legacy is not None:
        return legacy
    return default_threshold(config)


def input_variant(config: EmbeddingConfig) -> InputVariant:
    """画像の入力にする派生画像(ローカルのモデルはカタログ。リモートは thumb)。"""
    if config.engine == "onnx" and config.onnx_model in CLIP_MODELS:
        return CLIP_MODELS[config.onnx_model].input_variant
    return "thumb"


# -- 使うモデル ------------------------------------------------------------------


def active_model_key(config: EmbeddingConfig, settings: Settings) -> str | None:
    """使うモデルの `model_key`(ADR-0033 3章)。リモートで接続先かモデル名が未設定なら None。"""
    key: str | None
    if config.engine == "onnx":
        model = CLIP_MODELS.get(config.onnx_model)
        key = onnx_model_key(model) if model is not None else None
    else:
        if not config.remote_connection_id or not config.remote_model:
            key = None
        else:
            key = remote_model_key(config.remote_connection_id, config.remote_model)
    if key is not None and settings.fake_provider:
        key = FAKE_KEY_PREFIX + key
    return key


def active_dim(config: EmbeddingConfig) -> int | None:
    """使うモデルの次元(ローカルのモデルだけ分かる。リモートは None)。"""
    if config.engine == "onnx":
        model = CLIP_MODELS.get(config.onnx_model)
        return model.dim if model is not None else None
    return None


def usable(config: EmbeddingConfig, settings: Settings) -> bool:
    """有効で、いま計算できるか。ローカルはモデルをダウンロード済み(`FAKE_PROVIDER=1` では
    要らない)、リモートは接続先とモデル名がそろっていること。"""
    if not config.enabled or active_model_key(config, settings) is None:
        return False
    if config.engine == "onnx" and not settings.fake_provider:
        return is_downloaded(settings.data_dir, config.onnx_model)
    return True


def used_connection_ids(db: Session) -> set[str]:
    """リモートの接続先に選んでいる接続先(エンジンや有効かどうかに関わらず数える。
    ADR-0032 3章。使っている接続先は削除できない)。"""
    value = _get_raw_value(db, _KEY_PREFIX + "remote_connection_id")
    return {value} if isinstance(value, str) and value else set()


FEATURE_ID = "embedding"
llm_connections.register_usage(FEATURE_ID, used_connection_ids)
