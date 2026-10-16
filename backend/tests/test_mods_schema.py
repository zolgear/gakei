"""ADR-0051 3章: Mod の契約の JSON Schema の書き出し(`app.tools.export_mod_schema`)。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.mods import api as contract
from app.mods.runtime import _dummy_png, dummy_subject
from app.tools import export_mod_schema

jsonschema = pytest.importorskip("jsonschema")


def _validator(schema: dict, name: str):  # noqa: ANN202
    return jsonschema.Draft202012Validator({**schema, "$ref": f"#/$defs/{name}"})


def test_schema_matches_the_contract(tmp_path: Path) -> None:
    out = tmp_path / "schema" / "mods.json"
    export_mod_schema.main([str(out)])
    schema = json.loads(out.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    for name in (
        "AiBeforeSendEvent",
        "AssetIngestedEvent",
        "RunFinishedEvent",
        "BeforeSendSubject",
        "AssetIngestedSubject",
        "RunFinishedSubject",
        "Decision",
        "VerdictsResponse",
        "VerdictSubmission",
    ):
        assert name in schema["$defs"], name

    # GAKEI が送るイベントの JSON は、それぞれの封筒の定義に合う。
    image = _dummy_png()
    names = {
        contract.AI_BEFORE_SEND: "AiBeforeSendEvent",
        contract.ASSET_INGESTED: "AssetIngestedEvent",
        contract.RUN_FINISHED: "RunFinishedEvent",
    }
    for event_name, definition in names.items():
        event = contract.Event(
            event=event_name,
            event_id="00000000-0000-0000-0000-000000000001",
            occurred_at="2026-10-16T00:00:00+00:00",
            instance=contract.Instance(version="0.0.0", name="test"),
            actor=contract.Actor(id="u1", email="a@example.test", name="A"),
            subject=dummy_subject(event_name, image),
        )
        payload = contract.event_to_json(event)
        _validator(schema, definition).validate(payload)
        _validator(schema, "Event").validate(payload)
        # 別のイベントの定義には合わない。
        other = next(d for n, d in names.items() if n != event_name)
        assert not _validator(schema, other).is_valid(payload)

    decision = _validator(schema, "Decision")
    decision.validate({"decision": "deny", "reason": "x", "reference": "r-1"})
    assert not decision.is_valid({"decision": "maybe"})

    verdict = {
        "target": {"type": "asset", "id": "a"},
        "verdict": "flag",
        "label": "類似の疑い",
        "score": 0.5,
    }
    responses = _validator(schema, "VerdictsResponse")
    for ok in (None, [], [verdict], {"verdicts": [verdict]}):
        responses.validate(ok)
    assert not responses.is_valid([{**verdict, "score": 2}])
    assert not responses.is_valid([{**verdict, "verdict": "maybe"}])
    submission = _validator(schema, "VerdictSubmission")
    submission.validate([verdict])
    assert not submission.is_valid(None)
    assert hashlib.sha256(image).hexdigest() in json.dumps(
        contract.event_to_json(event), ensure_ascii=False
    )


def test_schema_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    export_mod_schema.main(["-"])
    schema = json.loads(capsys.readouterr().out)
    assert schema["x-gakei-schema-version"] == contract.SCHEMA_VERSION
    with pytest.raises(SystemExit):
        export_mod_schema.main([])
