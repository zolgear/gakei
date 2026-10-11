"""SD WebUI の LoRA の一覧の解析(ADR-0038 8章)。

`GET /sdapi/v1/loras` は `[{"name", "alias", "path", "metadata"}]` を返す。`metadata` は学習時の
情報(kohya の `ss_*`、`modelspec.*`)で、学習した人の環境の情報(`ss_dataset_dirs` のパス、
`ss_training_comment` など)も入っている。ここでは 8章で決めた4つ(name、alias、base_model、
trigger_tags)だけを取り出し、`path` とその他のメタ情報はどこにも残さない。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

BaseModel = Literal["sdxl", "sd1"]

# トリガーワードの候補の上限(ADR-0038 8章)
TRIGGER_TAGS_MAX = 20
# 1つのタグの長さの上限。学習データの説明文のような長いものは候補にしない。
_TAG_MAX_LENGTH = 100


@dataclass(frozen=True)
class LoraInfo:
    """LoRA 1つ。`alias` は name と違うときだけ。"""

    name: str
    alias: str | None
    base_model: BaseModel | None
    trigger_tags: tuple[str, ...]


def _as_dict(value: Any) -> dict[str, Any] | None:
    """dict か、dict を表す JSON 文字列を dict にする。読めなければ None。"""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def detect_base_model(metadata: dict[str, Any]) -> BaseModel | None:
    """ベースモデルの判定。`ss_base_model_version` を優先し、無ければ
    `modelspec.architecture` を見る。SDXL と SD1.5 以外(SD2 など)や、分からないものは None。"""
    version = metadata.get("ss_base_model_version")
    if isinstance(version, str) and version.strip():
        lowered = version.strip().lower()
        if lowered.startswith("sdxl"):
            return "sdxl"
        if lowered.startswith("sd_v1"):
            return "sd1"
        return None
    architecture = metadata.get("modelspec.architecture")
    if isinstance(architecture, str) and architecture.strip():
        lowered = architecture.strip().lower()
        if lowered.startswith("stable-diffusion-xl"):
            return "sdxl"
        if lowered.startswith("stable-diffusion-v1"):
            return "sd1"
    return None


def _count(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def trigger_tags(metadata: dict[str, Any], limit: int = TRIGGER_TAGS_MAX) -> tuple[str, ...]:
    """`ss_tag_frequency`(`{"<データセットのフォルダー名>": {"tag": 出現数}}`)を、フォルダーを
    またいで足し、多い順に `limit` 個。`_` は空白にする。フォルダー名は使わない。
    同じ数のものは名前の順(結果を安定させるため)。"""
    frequency = _as_dict(metadata.get("ss_tag_frequency"))
    if frequency is None:
        return ()
    totals: dict[str, int] = {}
    for folder in frequency.values():
        tags = _as_dict(folder)
        if tags is None:
            continue
        for raw_tag, raw_count in tags.items():
            if not isinstance(raw_tag, str):
                continue
            tag = " ".join(raw_tag.replace("_", " ").split())
            count = _count(raw_count)
            if not tag or len(tag) > _TAG_MAX_LENGTH or count is None or count <= 0:
                continue
            totals[tag] = totals.get(tag, 0) + count
    ordered = sorted(totals.items(), key=lambda item: (-item[1], item[0].casefold(), item[0]))
    return tuple(tag for tag, _count_value in ordered[:limit])


def parse_lora(item: Any) -> LoraInfo | None:
    """`/sdapi/v1/loras` の1件。name が無ければ None。"""
    if not isinstance(item, dict):
        return None
    name = item.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    alias = item.get("alias")
    alias_value = alias if isinstance(alias, str) and alias.strip() and alias != name else None
    metadata = _as_dict(item.get("metadata")) or {}
    return LoraInfo(
        name=name,
        alias=alias_value,
        base_model=detect_base_model(metadata),
        trigger_tags=trigger_tags(metadata),
    )


def parse_loras(body: Any) -> list[LoraInfo]:
    """`/sdapi/v1/loras` の応答。同じ name は先のものだけ。name の順(大文字小文字を無視)。"""
    if not isinstance(body, list):
        return []
    result: dict[str, LoraInfo] = {}
    for item in body:
        lora = parse_lora(item)
        if lora is not None and lora.name not in result:
            result[lora.name] = lora
    return sorted(result.values(), key=lambda lora: (lora.name.casefold(), lora.name))
