"""Mod の契約(ADR-0051 3章・4章・8章)の JSON Schema を書き出す。

使い方: uv run python -m app.tools.export_mod_schema <出力パス|->

`-` なら標準出力に書く。中身は `$defs` に名前を付けた定義の集まり(JSON Schema 2020-12):

- イベントの封筒と `subject`: `AiBeforeSendEvent`、`AssetIngestedEvent`、`RunFinishedEvent`
  (`subject` はそれぞれ `BeforeSendSubject`、`AssetIngestedSubject`、`RunFinishedSubject`)
- `ai.before_send` の応答: `Decision`
- `asset.ingested` / `run.finished` の応答(判定): `VerdictsResponse`(要素は `Verdict`)
- 判定の受付の API(`POST /api/mods/{mod_id}/verdicts`)の本文: `VerdictSubmission`

型の元は `app/mods/api.py`(Python の Mod の `gakei_mod`)。値の一覧(`purpose`、`source` など)も
そこから取る。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from app.mods import api as contract

SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
_REF = "#/$defs/{model}"


def _ref(name: str) -> dict[str, str]:
    return {"$ref": f"#/$defs/{name}"}


def _collect(defs: dict[str, Any], tp: type) -> None:
    """dataclass の JSON Schema を `defs` に足す(入れ子の型も `$defs` に分けて足す)。"""
    schema = TypeAdapter(tp).json_schema(ref_template=_REF, mode="serialization")
    nested = schema.pop("$defs", {})
    defs.update(nested)
    defs[tp.__name__] = schema


def _set_enum(
    defs: dict[str, Any], model: str, prop: str, values: tuple[str, ...], *, nullable: bool
) -> None:
    enum: dict[str, Any] = {"type": "string", "enum": list(values)}
    target = defs[model]["properties"][prop]
    keep = {k: v for k, v in target.items() if k in ("title", "default", "description")}
    target.clear()
    target.update(keep)
    if nullable:
        target["anyOf"] = [enum, {"type": "null"}]
    else:
        target.update(enum)


def _envelope(event: str, subject: str, description: str) -> dict[str, Any]:
    return {
        "type": "object",
        "description": description,
        "required": [
            "schema_version",
            "event",
            "event_id",
            "occurred_at",
            "instance",
            "actor",
            "subject",
        ],
        "properties": {
            "schema_version": {"type": "integer", "const": contract.SCHEMA_VERSION},
            "event": {"type": "string", "const": event},
            "event_id": {"type": "string", "format": "uuid"},
            "occurred_at": {"type": "string", "format": "date-time"},
            "instance": _ref("Instance"),
            "actor": {"anyOf": [_ref("Actor"), {"type": "null"}]},
            "subject": _ref(subject),
        },
    }


def _verdict_list(description: str, *, nullable: bool) -> dict[str, Any]:
    array = {"type": "array", "items": _ref("Verdict"), "maxItems": contract._VERDICTS_MAX}
    forms: list[dict[str, Any]] = [
        array,
        {
            "type": "object",
            "required": ["verdicts"],
            "properties": {"verdicts": {"anyOf": [array, {"type": "null"}]}},
        },
    ]
    if nullable:
        forms.insert(0, {"type": "null"})
    return {"description": description, "anyOf": forms}


def build_schema() -> dict[str, Any]:
    defs: dict[str, Any] = {}
    for tp in (
        contract.Instance,
        contract.Actor,
        contract.BeforeSendSubject,
        contract.AssetIngestedSubject,
        contract.RunFinishedSubject,
        contract.Decision,
        contract.Verdict,
    ):
        _collect(defs, tp)

    _set_enum(defs, "BeforeSendSubject", "purpose", contract.PURPOSES, nullable=False)
    _set_enum(defs, "AssetIngestedSubject", "source", contract.SOURCES, nullable=False)
    _set_enum(defs, "Target", "locality", contract.LOCALITIES, nullable=False)
    _set_enum(defs, "Target", "locality_source", contract.LOCALITY_SOURCES, nullable=False)
    _set_enum(defs, "ImageRef", "role", contract.IMAGE_ROLES, nullable=True)
    score = defs["Verdict"]["properties"]["score"]
    score["anyOf"] = [{"type": "number", "minimum": 0, "maximum": 1}, {"type": "null"}]

    defs["AiBeforeSendEvent"] = _envelope(
        contract.AI_BEFORE_SEND,
        "BeforeSendSubject",
        "外部の AI へ送る直前に同期で呼ぶ。応答は Decision。",
    )
    defs["AssetIngestedEvent"] = _envelope(
        contract.ASSET_INGESTED,
        "AssetIngestedSubject",
        "Asset を取り込んだ後に呼ぶ。応答は VerdictsResponse(省略できる)。",
    )
    defs["RunFinishedEvent"] = _envelope(
        contract.RUN_FINISHED,
        "RunFinishedSubject",
        "Run が終わった後に呼ぶ。応答は VerdictsResponse(省略できる)。",
    )
    defs["Event"] = {
        "oneOf": [
            _ref("AiBeforeSendEvent"),
            _ref("AssetIngestedEvent"),
            _ref("RunFinishedEvent"),
        ]
    }
    defs["VerdictsResponse"] = _verdict_list(
        "asset.ingested / run.finished の応答。null・空の本文は判定なし。", nullable=True
    )
    defs["VerdictSubmission"] = _verdict_list(
        "判定の受付の API(POST /api/mods/{mod_id}/verdicts)の本文。", nullable=False
    )
    return {
        "$schema": SCHEMA_DIALECT,
        "title": "GAKEI Mod contract",
        "description": (
            f"schema_version {contract.SCHEMA_VERSION}。項目を足すときは版を上げない。"
            "消す・意味を変えるときは schema_version を上げる(docs/mods.md)。"
        ),
        "x-gakei-schema-version": contract.SCHEMA_VERSION,
        "$defs": dict(sorted(defs.items())),
    }


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("使い方: uv run python -m app.tools.export_mod_schema <出力パス|->", file=sys.stderr)
        raise SystemExit(1)
    text = json.dumps(build_schema(), ensure_ascii=False, indent=2) + "\n"
    if args[0] == "-":
        sys.stdout.write(text)
        return
    output_path = Path(args[0])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8")
    print(f"Mod の契約の JSON Schema を書き出しました: {output_path}")


if __name__ == "__main__":
    main()
