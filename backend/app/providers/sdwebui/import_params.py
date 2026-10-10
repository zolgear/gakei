"""ADR-0038 9章: 画像の生成情報(A1111 形式の `parameters`)から、SD WebUI の生成フォームの値を
組み立てる。WebUI の「PNG 内の情報を表示 → txt2img に転送」に当たる。

入力は `generation_meta.extract_generation_meta` の結果(`tool == "a1111"`)と、接続先の一覧
(`Catalog`)。純粋関数で、DB にも WebUI にも触れない。画像そのものは受け取らない。

対応付け:

- プロンプト: Dynamic Prompts の `Template` / `Negative Template` があり、接続先に Dynamic Prompts
  があれば、展開前のそれを使い `dynamic_prompts` を有効にする。接続先に無ければ展開後のものを使い、
  Template は「読み込めなかった項目」にする(`{a|b}` がそのまま WebUI に届くため)。
- `Steps`、`CFG scale`、`Seed`: 範囲(capabilities と同じ)に収まれば入れる。
- `Sampler`: 接続先のサンプラーの名前と照合する(大文字小文字を無視)。古い A1111 の
  「DPM++ 2M Karras」のようにスケジューラーが後ろに付いたものは、`Schedule type` が無ければ分ける。
- `Schedule type`: 表示名(`Automatic`)から名前(`automatic`)へ(大文字小文字を無視)。
- `Size`: SD WebUI のサイズの制約に収まれば `size` に入れる。
- `Model`: チェックポイントの `model_name` と照合し、無ければ `Model hash` をチェックポイントの
  短いハッシュと照合する。見つからなければ `model` は None(フォームのモデルを変えない)。
- VAE(A1111 の `VAE`、Forge の `Module 1` など): 接続先の VAE の一覧にあれば入れる。Forge は
  拡張子なしで書くので、一覧の拡張子を除いた名前とも照合する。

フォームに入れなかった項目は、名前と値を `unapplied` に並べる(重複せず、入れたものは含めない)。
`Model hash` や `VAE hash` など照合にだけ使う項目も、フォームには入らないので並べる。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from app.domain import sizes
from app.i18n import t
from app.providers.sdwebui.client import Catalog, Checkpoint
from app.providers.sdwebui.extensions import PARAM_DYNAMIC_PROMPTS, resolve_extensions
from app.providers.sdwebui.provider import SEED_MAX, SIZE_CONSTRAINTS

# capabilities のパラメーターの範囲(provider.py の `_generate_params` と同じ)
STEPS_MIN, STEPS_MAX = 1, 150
CFG_MIN, CFG_MAX = 1.0, 30.0

# infotext の項目名
KEY_STEPS = "Steps"
KEY_SAMPLER = "Sampler"
KEY_SCHEDULE_TYPE = "Schedule type"
KEY_CFG = "CFG scale"
KEY_SIZE = "Size"
KEY_MODEL = "Model"
KEY_MODEL_HASH = "Model hash"
KEY_SEED = "Seed"
KEY_VAE = "VAE"
KEY_TEMPLATE = "Template"
KEY_NEGATIVE_TEMPLATE = "Negative Template"
# Forge は VAE やテキストエンコーダーを `Module 1`、`Module 2` … と書く
_FORGE_MODULE_PREFIX = "Module "

_MODEL_FILE_EXTENSIONS = (".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".sft", ".gguf")
# 古い A1111 の短いハッシュは 8 桁。それより短いものでは照合しない。
_MIN_HASH_LENGTH = 8


@dataclass(frozen=True)
class Note:
    """利用者への注意。`code` は機械可読、`message` は利用者向けの文言。"""

    code: str
    message: str


@dataclass
class ImportResult:
    """フォームに入れる値。`params` は capabilities のパラメーター名(と `size`)で持つ。"""

    model: str | None
    prompt: str
    params: dict[str, Any] = field(default_factory=dict)
    unapplied: list[tuple[str, str]] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)
    software: str | None = None


def _stem(name: str) -> str:
    lowered = name.lower()
    for ext in _MODEL_FILE_EXTENSIONS:
        if lowered.endswith(ext):
            return name[: -len(ext)]
    return name


def _value_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return str(value)


def _parse_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text and (text.isdigit() or (text[0] in "+-" and text[1:].isdigit())):
            return int(text)
    return None


def _parse_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return number if math.isfinite(number) else None


def _find_ci(choices: list[str], value: str) -> str | None:
    """大文字小文字を無視して一致するもの(完全一致を優先)。"""
    if value in choices:
        return value
    lowered = value.strip().lower()
    return next((c for c in choices if c.lower() == lowered), None)


def _find_scheduler(catalog: Catalog, value: str) -> str | None:
    """表示名または名前から、スケジューラーの名前を探す(大文字小文字を無視)。"""
    lowered = value.strip().lower()
    if not lowered:
        return None
    for name in catalog.schedulers:
        label = catalog.scheduler_labels.get(name)
        if name.lower() == lowered or (label is not None and label.lower() == lowered):
            return name
    return None


def _split_legacy_sampler(catalog: Catalog, value: str) -> tuple[str, str] | None:
    """「DPM++ 2M Karras」→(「DPM++ 2M」, 「karras」)。どちらも接続先にあるときだけ。"""
    for name in catalog.schedulers:
        candidates = {name, catalog.scheduler_labels.get(name, name)}
        for suffix in candidates:
            if not suffix:
                continue
            ending = " " + suffix
            if value.lower().endswith(ending.lower()):
                sampler = _find_ci(catalog.samplers, value[: -len(ending)])
                if sampler is not None:
                    return sampler, name
    return None


def _match_checkpoint_by_name(checkpoints: list[Checkpoint], value: str) -> Checkpoint | None:
    """`model_name` と照合する(完全一致 → 大文字小文字を無視 → 拡張子とハッシュの表記を除く)。"""
    exact = next((c for c in checkpoints if c.model_name == value), None)
    if exact is not None:
        return exact
    candidates = {value.strip().lower(), _stem(value.strip()).lower()}
    # title(`name.safetensors [hash]`)の形で書かれていることもある
    if value.endswith("]") and " [" in value:
        base = value[: value.rindex(" [")]
        candidates |= {base.lower(), _stem(base).lower()}
    return next((c for c in checkpoints if c.model_name.lower() in candidates), None)


def _match_checkpoint_by_hash(checkpoints: list[Checkpoint], value: str) -> Checkpoint | None:
    wanted = value.strip().lower()
    if len(wanted) < _MIN_HASH_LENGTH:
        return None
    for checkpoint in checkpoints:
        known = checkpoint.hash
        if known is None or len(known) < _MIN_HASH_LENGTH:
            continue
        if known.startswith(wanted) or wanted.startswith(known):
            return checkpoint
    return None


def _match_vae(vaes: list[str], value: str) -> str | None:
    """VAE の一覧と照合する。Forge は拡張子なしで書くので、拡張子を除いた名前とも比べる。"""
    text = value.strip()
    if not text:
        return None
    exact = _find_ci(vaes, text)
    if exact is not None:
        return exact
    stem = _stem(text).lower()
    return next((v for v in vaes if _stem(v).lower() == stem), None)


def build_form_values(meta: dict[str, Any], catalog: Catalog) -> ImportResult:
    """`extract_generation_meta` の結果(A1111 形式)と接続先の一覧から、フォームの値を作る。"""
    raw_params = meta.get("params")
    infotext: dict[str, Any] = dict(raw_params) if isinstance(raw_params, dict) else {}
    # Model と Seed は extract_generation_meta が params から外して上の段に置いている。元の順に
    # 近づけるため、先頭に戻してから扱う。
    leading: dict[str, Any] = {}
    if meta.get("model") is not None:
        leading[KEY_MODEL] = meta["model"]
    if meta.get("seed") is not None:
        leading[KEY_SEED] = meta["seed"]
    infotext = {**leading, **infotext}

    software = meta.get("software")
    result = ImportResult(
        model=None,
        prompt="",
        software=software if isinstance(software, str) and software else None,
    )
    consumed: set[str] = set()

    def note(code: str, **kwargs: Any) -> None:
        result.notes.append(Note(code=code, message=_note_message(code, **kwargs)))

    # -- プロンプト ----------------------------------------------------------------------
    prompt = meta.get("prompt") if isinstance(meta.get("prompt"), str) else ""
    negative = meta.get("negative_prompt") if isinstance(meta.get("negative_prompt"), str) else ""
    template = infotext.get(KEY_TEMPLATE)
    negative_template = infotext.get(KEY_NEGATIVE_TEMPLATE)
    has_template = isinstance(template, str) or isinstance(negative_template, str)
    if has_template:
        if resolve_extensions(catalog.scripts):
            if isinstance(template, str):
                prompt = template
                consumed.add(KEY_TEMPLATE)
            if isinstance(negative_template, str):
                negative = negative_template
                consumed.add(KEY_NEGATIVE_TEMPLATE)
            result.params[PARAM_DYNAMIC_PROMPTS] = True
        else:
            note("dynamicPromptsUnavailable")
    result.prompt = prompt or ""
    result.params["negative_prompt"] = negative or ""

    # -- 数値 --------------------------------------------------------------------------
    if KEY_STEPS in infotext:
        steps = _parse_int(infotext[KEY_STEPS])
        if steps is not None and STEPS_MIN <= steps <= STEPS_MAX:
            result.params["steps"] = steps
            consumed.add(KEY_STEPS)
        else:
            note("invalidValue", name=KEY_STEPS)

    if KEY_CFG in infotext:
        cfg = _parse_float(infotext[KEY_CFG])
        if cfg is not None and CFG_MIN <= cfg <= CFG_MAX:
            result.params["cfg_scale"] = int(cfg) if cfg.is_integer() else cfg
            consumed.add(KEY_CFG)
        else:
            note("invalidValue", name=KEY_CFG)

    if KEY_SEED in infotext:
        seed = _parse_int(infotext[KEY_SEED])
        if seed is not None and 0 <= seed <= SEED_MAX:
            result.params["seed"] = seed
            consumed.add(KEY_SEED)
        else:
            note("invalidValue", name=KEY_SEED)

    # -- サンプラーとスケジューラー ----------------------------------------------------------
    scheduler_from_sampler: str | None = None
    if KEY_SAMPLER in infotext:
        value = _value_text(infotext[KEY_SAMPLER])
        sampler = _find_ci(catalog.samplers, value)
        if sampler is None and KEY_SCHEDULE_TYPE not in infotext:
            split = _split_legacy_sampler(catalog, value)
            if split is not None:
                sampler, scheduler_from_sampler = split
        if sampler is not None:
            result.params["sampler_name"] = sampler
            consumed.add(KEY_SAMPLER)
            if scheduler_from_sampler is not None:
                result.params["scheduler"] = scheduler_from_sampler
        else:
            note("samplerNotFound", value=value)

    if KEY_SCHEDULE_TYPE in infotext:
        value = _value_text(infotext[KEY_SCHEDULE_TYPE])
        scheduler = _find_scheduler(catalog, value)
        if scheduler is not None:
            result.params["scheduler"] = scheduler
            consumed.add(KEY_SCHEDULE_TYPE)
        else:
            note("schedulerNotFound", value=value)

    # -- サイズ ------------------------------------------------------------------------
    if KEY_SIZE in infotext:
        value = _value_text(infotext[KEY_SIZE]).strip()
        try:
            parsed = sizes.parse_size(value, SIZE_CONSTRAINTS)
        except sizes.InvalidSizeError as exc:
            note("sizeOutOfRange", value=value, reason=str(exc))
        else:
            if parsed is None:
                note("sizeOutOfRange", value=value, reason=t("sizes.autoNotAllowed"))
            else:
                result.params["size"] = f"{parsed[0]}x{parsed[1]}"
                consumed.add(KEY_SIZE)

    # -- チェックポイント ----------------------------------------------------------------
    model_name = infotext.get(KEY_MODEL)
    model_hash = infotext.get(KEY_MODEL_HASH)
    checkpoint: Checkpoint | None = None
    if isinstance(model_name, str) and model_name.strip():
        checkpoint = _match_checkpoint_by_name(catalog.checkpoints, model_name)
    if checkpoint is None and isinstance(model_hash, str):
        checkpoint = _match_checkpoint_by_hash(catalog.checkpoints, model_hash)
        if checkpoint is not None:
            note(
                "modelMatchedByHash",
                name=model_name if isinstance(model_name, str) else "",
                model=checkpoint.model_name,
            )
    if checkpoint is not None:
        result.model = checkpoint.model_name
        consumed.add(KEY_MODEL)
    elif model_name is not None or model_hash is not None:
        note("modelNotFound", name=_value_text(model_name or model_hash))

    # -- VAE ---------------------------------------------------------------------------
    vae_keys = [KEY_VAE] if KEY_VAE in infotext else []
    vae_keys += [k for k in infotext if k.startswith(_FORGE_MODULE_PREFIX)]
    vae_applied = False
    for key in vae_keys:
        value = _value_text(infotext[key])
        matched = _match_vae(catalog.vaes, value) if not vae_applied else None
        if matched is not None:
            result.params["vae"] = matched
            consumed.add(key)
            vae_applied = True
        elif key == KEY_VAE:
            note("vaeNotFound", value=value)
    # Forge の Module はテキストエンコーダーのこともあるので、どれも一覧に無いときだけ注意を出す。
    module_keys = [k for k in vae_keys if k != KEY_VAE]
    if module_keys and not vae_applied and KEY_VAE not in infotext:
        note("vaeNotFound", value=", ".join(_value_text(infotext[k]) for k in module_keys))

    # -- 入れなかった項目 -----------------------------------------------------------------
    seen: set[str] = set()
    for key, value in infotext.items():
        if key in consumed or key in seen:
            continue
        seen.add(key)
        result.unapplied.append((key, _value_text(value)))

    if meta.get("truncated"):
        note("truncated")
    return result


def _note_message(code: str, **kwargs: Any) -> str:
    """注意の文言(i18n のキーはリテラルで書く。キーの欠けをテストで見つけるため)。"""
    if code == "modelNotFound":
        return t("sdwebui.importParams.notes.modelNotFound", **kwargs)
    if code == "modelMatchedByHash":
        return t("sdwebui.importParams.notes.modelMatchedByHash", **kwargs)
    if code == "sizeOutOfRange":
        return t("sdwebui.importParams.notes.sizeOutOfRange", **kwargs)
    if code == "invalidValue":
        return t("sdwebui.importParams.notes.invalidValue", **kwargs)
    if code == "samplerNotFound":
        return t("sdwebui.importParams.notes.samplerNotFound", **kwargs)
    if code == "schedulerNotFound":
        return t("sdwebui.importParams.notes.schedulerNotFound", **kwargs)
    if code == "vaeNotFound":
        return t("sdwebui.importParams.notes.vaeNotFound", **kwargs)
    if code == "dynamicPromptsUnavailable":
        return t("sdwebui.importParams.notes.dynamicPromptsUnavailable", **kwargs)
    if code == "truncated":
        return t("sdwebui.importParams.notes.truncated", **kwargs)
    raise ValueError(code)
