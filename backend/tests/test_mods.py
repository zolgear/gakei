"""ADR-0051: Mod とフックの土台(契約、署名、multipart、呼ぶ順番、失敗時の扱い、時間切れ、
配達の待ち行列、送り先の区分)。Webhook は `httpx.MockTransport` で受け、実際には送らない。"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import Settings
from app.domain import llm_connections
from app.domain import locality as locality_domain
from app.domain.assets import ingest
from app.domain.models import (
    AssetKind,
    ComfyWorkflow,
    ModDelivery,
    ModInvocation,
    ModVerdict,
    Run,
    RunOperation,
    RunStatus,
)
from app.mods import api as contract
from app.mods import config as mods_config
from app.mods import events as mods_events
from app.mods import webhook as webhook_send
from app.mods.runtime import ModRuntime
from app.worker.mod_delivery import ModDeliveryWorker, reset_delivering
from tests.conftest import make_png_bytes

SECRET = "test-signing-secret"


# -- 共通 -----------------------------------------------------------------------------


def _settings(tmp_path: Path, **extra: Any) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path / "data", fake_provider=True, **extra)


def _runtime(db_session_factory, local_store, settings: Settings) -> ModRuntime:  # noqa: ANN001
    runtime = ModRuntime(db_session_factory, local_store, settings)
    runtime.scan()
    return runtime


def _add_webhook(
    factory,  # noqa: ANN001
    settings: Settings,
    *,
    name: str = "checker",
    url: str = "https://hooks.example.test/gakei",
    events: list[str] | None = None,
    **extra: Any,
) -> str:
    with factory() as db:
        return mods_config.add_webhook(
            db,
            settings.data_dir,
            {
                "name": name,
                "url": url,
                "secret": SECRET,
                "enabled": True,
                "events": events or [contract.AI_BEFORE_SEND],
                **extra,
            },
        )


def _reload(runtime: ModRuntime) -> None:
    with runtime.session_factory() as db:
        runtime.reload(db)


def _subject(provider: str = "openai", purpose: str = "image_run") -> contract.BeforeSendSubject:
    return contract.BeforeSendSubject(
        purpose=purpose,
        target=contract.Target(
            provider=provider,
            model="m",
            host="api.openai.com",
            locality="cloud",
            locality_source="default",
        ),
        text=[contract.TextPart(role="prompt", text="a cat")],
        params={"size": "1024x1024"},
    )


def _invocations(factory) -> list[ModInvocation]:  # noqa: ANN001
    with factory() as db:
        rows = db.execute(select(ModInvocation).order_by(ModInvocation.created_at)).scalars().all()
        for row in rows:
            db.expunge(row)
        return list(rows)


# -- 契約 -----------------------------------------------------------------------------


def test_gakei_mod_alias_is_the_contract_module() -> None:
    import gakei_mod

    assert gakei_mod is contract
    assert gakei_mod.Decision.deny("no").to_json() == {
        "decision": "deny",
        "reason": "no",
        "reference": None,
    }


def test_event_json_roundtrip_and_envelope() -> None:
    subject = contract.RunFinishedSubject(
        run_id="r",
        status="failed",
        provider="openai",
        model="m",
        operation="generate",
        prompt="p",
        outputs=[contract.ImageRef("a", "s" * 64, 1, 2, 3, "image/png")],
        error=contract.RunError(code="x", message="y"),
    )
    event = contract.Event(
        event=contract.RUN_FINISHED,
        event_id=str(uuid.uuid4()),
        occurred_at="2026-10-16T00:00:00+00:00",
        instance=contract.Instance(version="1.0.0", name="studio"),
        actor=None,
        subject=subject,
    )
    raw = contract.event_to_json(event)
    assert list(raw) == [
        "schema_version",
        "event",
        "event_id",
        "occurred_at",
        "instance",
        "actor",
        "subject",
    ]
    assert raw["schema_version"] == 1
    assert raw["actor"] is None
    assert raw["subject"]["outputs"][0]["asset_id"] == "a"
    # JSON を通しても同じものに戻る(配達の待ち行列から読み戻す)。
    assert contract.event_from_json(json.loads(json.dumps(raw))) == event


@pytest.mark.parametrize(
    "raw",
    [
        None,
        [],
        {"decision": "maybe"},
        {"decision": "deny", "reason": 3},
    ],
)
def test_decision_contract_rejects_invalid(raw: Any) -> None:
    with pytest.raises(contract.ContractError):
        contract.decision_from_json(raw)


def test_verdicts_contract() -> None:
    assert contract.verdicts_from_json(None) == []
    assert contract.verdicts_from_json({"verdicts": None}) == []
    verdicts = contract.verdicts_from_json(
        [
            {
                "target": {"type": "asset", "id": "a1"},
                "verdict": "flag",
                "label": "similar",
                "score": 0.8,
                "url": "https://x.test/1",
            }
        ]
    )
    assert verdicts[0].verdict == "flag" and verdicts[0].score == 0.8
    for bad in (
        [{"target": {"type": "asset", "id": "a"}, "verdict": "bad"}],
        [{"target": {"type": "user", "id": "a"}, "verdict": "ok"}],
        [{"target": {"type": "asset", "id": "a"}, "verdict": "ok", "score": 2}],
        [{"target": {"type": "asset", "id": "a"}, "verdict": "ok", "url": "javascript:x"}],
        {"other": 1},
    ):
        with pytest.raises(contract.ContractError):
            contract.verdicts_from_json(bad)


# -- 署名と multipart -----------------------------------------------------------------


def test_signature_is_hmac_of_timestamp_and_body() -> None:
    event = {"event": "ai.before_send", "event_id": "e-1", "subject": {}}
    request = webhook_send.build_request(
        "https://hooks.example.test/x", SECRET, event, timestamp=1700000000
    )
    body = request.content
    expected = hmac.new(SECRET.encode(), b"1700000000." + body, hashlib.sha256).hexdigest()
    assert request.headers["X-Gakei-Signature"] == f"sha256={expected}"
    assert request.headers["X-Gakei-Timestamp"] == "1700000000"
    assert request.headers["X-Gakei-Event"] == "ai.before_send"
    assert request.headers["X-Gakei-Delivery"] == "e-1"
    assert request.headers["Content-Type"] == "application/json"
    assert json.loads(body) == event


def test_multipart_sends_json_and_original_bytes_without_base64() -> None:
    png = make_png_bytes(8, 8)
    event = {"event": "asset.ingested", "event_id": "e-2", "subject": {}}
    image = webhook_send.WebhookImage(
        asset_id="asset-1", sha256=hashlib.sha256(png).hexdigest(), mime="image/png", data=png
    )
    request = webhook_send.build_request(
        "https://hooks.example.test/x", SECRET, event, [image], timestamp=1
    )
    content_type = request.headers["Content-Type"]
    assert content_type.startswith("multipart/form-data; boundary=")
    body = request.content
    # 署名は送るバイト列そのもの(境界を含む)に付ける。
    expected = hmac.new(SECRET.encode(), b"1." + body, hashlib.sha256).hexdigest()
    assert request.headers["X-Gakei-Signature"] == f"sha256={expected}"
    # 画像の原本がそのまま入っている(base64 ではない)。
    assert png in body
    assert b'name="event"' in body and b'name="images"; filename="asset-1.png"' in body
    assert b'"event_id":"e-2"' in body


# -- ai.before_send ------------------------------------------------------------------


def _handler_by_host(responses: dict[str, Any], calls: list[str]):  # noqa: ANN202
    async def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        calls.append(host)
        value = responses[host]
        if callable(value):
            value = value()
        if isinstance(value, Exception):
            raise value
        if isinstance(value, httpx.Response):
            return value
        return httpx.Response(200, json=value)

    return handler


def test_no_subscribers_does_nothing(db_session_factory, local_store, tmp_path: Path) -> None:  # noqa: ANN001
    runtime = _runtime(db_session_factory, local_store, _settings(tmp_path))
    _reload(runtime)
    assert not runtime.wants(contract.AI_BEFORE_SEND)
    assert asyncio.run(runtime.before_send(_subject(), actor=None)) is None
    assert _invocations(db_session_factory) == []


def test_calls_in_order_and_stops_at_first_deny(
    db_session_factory,
    local_store,
    tmp_path: Path,  # noqa: ANN001
) -> None:
    settings = _settings(tmp_path)
    _add_webhook(db_session_factory, settings, name="A", url="https://a.test/")
    _add_webhook(db_session_factory, settings, name="B", url="https://b.test/")
    _add_webhook(db_session_factory, settings, name="C", url="https://c.test/")
    runtime = _runtime(db_session_factory, local_store, settings)
    _reload(runtime)
    calls: list[str] = []
    runtime.http_transport = httpx.MockTransport(
        _handler_by_host(
            {
                "a.test": {"decision": "allow", "reference": "ok-1"},
                "b.test": {"decision": "deny", "reason": "禁止語", "reference": "T-9"},
                "c.test": {"decision": "allow"},
            },
            calls,
        )
    )
    denial = asyncio.run(runtime.before_send(_subject(), actor=None))
    assert calls == ["a.test", "b.test"]
    assert denial is not None
    assert (denial.mod_name, denial.reason, denial.reference) == ("B", "禁止語", "T-9")
    assert "B" in denial.message and "禁止語" in denial.message
    rows = _invocations(db_session_factory)
    assert [(r.mod_name, r.outcome, r.effective) for r in rows] == [
        ("A", "allow", "allow"),
        ("B", "deny", "deny"),
    ]
    assert rows[0].reference == "ok-1" and rows[1].purpose == "image_run"
    assert rows[0].event_id == rows[1].event_id

    # 順番を入れ替えると、C → A → B の順に呼ぶ。
    with db_session_factory() as db:
        ids = {w.name: w.id for w in mods_config.load_webhooks(db)}
        mods_config.save_order(
            db, [("webhook", ids["C"]), ("webhook", ids["A"]), ("webhook", ids["B"])]
        )
        runtime.reload(db)
    calls.clear()
    asyncio.run(runtime.before_send(_subject(), actor=None))
    assert calls == ["c.test", "a.test", "b.test"]


@pytest.mark.parametrize(
    ("response", "outcome"),
    [
        (lambda: httpx.Response(500, json={"decision": "allow"}), "error"),
        (lambda: httpx.Response(200, text="not json"), "error"),
        (lambda: httpx.Response(200, json={"decision": "perhaps"}), "error"),
        (lambda: httpx.ConnectError("refused"), "error"),
        (lambda: httpx.ReadTimeout("slow"), "timeout"),
    ],
)
def test_fail_closed_and_fail_open(
    db_session_factory,
    local_store,
    tmp_path: Path,
    response: Any,
    outcome: str,  # noqa: ANN001
) -> None:
    settings = _settings(tmp_path)
    _add_webhook(db_session_factory, settings, name="strict", url="https://strict.test/")
    runtime = _runtime(db_session_factory, local_store, settings)
    _reload(runtime)
    runtime.http_transport = httpx.MockTransport(
        _handler_by_host({"strict.test": response, "loose.test": response}, [])
    )
    # 既定は block(fail-closed)。理由は「チェックに失敗した」。
    denial = asyncio.run(runtime.before_send(_subject(), actor=None))
    assert denial is not None and denial.reason == "送る前のチェックに失敗しました"
    rows = _invocations(db_session_factory)
    assert (rows[-1].outcome, rows[-1].effective) == (outcome, "deny")
    assert rows[-1].error

    # allow(fail-open)にすると通し、通したことを残す。
    with db_session_factory() as db:
        webhook = mods_config.load_webhooks(db)[0]
        mods_config.update_webhook(db, settings.data_dir, webhook.id, {"on_failure": "allow"})
        runtime.reload(db)
    assert asyncio.run(runtime.before_send(_subject(), actor=None)) is None
    rows = _invocations(db_session_factory)
    assert (rows[-1].outcome, rows[-1].effective) == (outcome, "allow")


def test_timeout_uses_the_mod_setting(db_session_factory, local_store, tmp_path: Path) -> None:  # noqa: ANN001
    settings = _settings(tmp_path)
    _add_webhook(db_session_factory, settings, timeout_seconds=1.0)
    runtime = _runtime(db_session_factory, local_store, settings)
    _reload(runtime)

    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, json={"decision": "allow"})

    runtime.http_transport = httpx.MockTransport(slow)
    started = time.monotonic()
    denial = asyncio.run(runtime.before_send(_subject(), actor=None))
    assert time.monotonic() - started < 4
    assert denial is not None
    assert _invocations(db_session_factory)[-1].outcome == "timeout"


def test_filters_by_purpose_and_provider(db_session_factory, local_store, tmp_path: Path) -> None:  # noqa: ANN001
    settings = _settings(tmp_path)
    _add_webhook(
        db_session_factory,
        settings,
        filters={"purposes": ["annotation"], "providers": ["comfyui"]},
    )
    runtime = _runtime(db_session_factory, local_store, settings)
    _reload(runtime)
    calls: list[str] = []
    runtime.http_transport = httpx.MockTransport(
        _handler_by_host({"hooks.example.test": {"decision": "deny"}}, calls)
    )
    assert asyncio.run(runtime.before_send(_subject("openai", "annotation"), actor=None)) is None
    assert asyncio.run(runtime.before_send(_subject("comfyui", "image_run"), actor=None)) is None
    assert calls == []
    denial = asyncio.run(runtime.before_send(_subject("comfyui", "annotation"), actor=None))
    assert denial is not None and denial.reason == "理由は示されていません"
    assert calls == ["hooks.example.test"]


def test_webhook_request_has_signature_and_images(
    db_session_factory,
    local_store,
    tmp_path: Path,  # noqa: ANN001
) -> None:
    settings = _settings(tmp_path)
    _add_webhook(db_session_factory, settings, send_images=True)
    png = make_png_bytes(16, 16)
    with db_session_factory() as db:
        asset = ingest(db, local_store, png, AssetKind.UPLOAD)
        db.commit()
        ref = mods_events.image_ref(asset, "primary")
    runtime = _runtime(db_session_factory, local_store, settings)
    _reload(runtime)
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"decision": "allow"})

    runtime.http_transport = httpx.MockTransport(handler)
    subject = contract.BeforeSendSubject(
        purpose="image_run", target=_subject().target, images=[ref], run_id=str(uuid.uuid4())
    )
    assert asyncio.run(runtime.before_send(subject, actor=None)) is None
    request = seen[0]
    body = request.content
    ts = request.headers["X-Gakei-Timestamp"]
    expected = hmac.new(SECRET.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
    assert request.headers["X-Gakei-Signature"] == f"sha256={expected}"
    assert request.headers["Content-Type"].startswith("multipart/form-data")
    assert png in body
    assert f'filename="{ref.asset_id}.png"'.encode() in body


# -- 非同期のイベントの配達 ------------------------------------------------------------


def _make_run(factory, status: RunStatus = RunStatus.SUCCEEDED) -> uuid.UUID:  # noqa: ANN001
    with factory() as db:
        run = Run(
            provider="fake",
            model="m",
            operation=RunOperation.GENERATE,
            prompt="p",
            params={"n": 1},
            status=status,
        )
        db.add(run)
        db.commit()
        return run.id


def _deliveries(factory) -> list[ModDelivery]:  # noqa: ANN001
    with factory() as db:
        rows = db.execute(select(ModDelivery)).scalars().all()
        for row in rows:
            db.expunge(row)
        return list(rows)


def test_delivery_retries_with_backoff_then_fails(
    db_session_factory,
    local_store,
    tmp_path: Path,  # noqa: ANN001
) -> None:
    settings = _settings(tmp_path)
    _add_webhook(db_session_factory, settings, events=[contract.RUN_FINISHED])
    runtime = _runtime(db_session_factory, local_store, settings)
    _reload(runtime)
    attempts: list[int] = []

    async def failing(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(503)

    runtime.http_transport = httpx.MockTransport(failing)
    run_id = _make_run(db_session_factory)
    with db_session_factory() as db:
        assert runtime.enqueue_run_finished(db, db.get(Run, run_id))
        db.commit()
    worker = ModDeliveryWorker(
        db_session_factory, runtime, backoff_base_seconds=60.0, max_attempts=3
    )

    async def scenario() -> None:
        assert await worker.run_once()
        # 次に送る時刻は先なので、すぐには取らない。
        assert not await worker.run_once()

    asyncio.run(scenario())
    row = _deliveries(db_session_factory)[0]
    assert (row.status, row.attempts, row.last_error) == ("queued", 1, "HTTP 503")
    first_wait = row.next_attempt_at - datetime.now(UTC)
    assert timedelta(seconds=50) < first_wait <= timedelta(seconds=60)

    def due_now() -> None:
        with db_session_factory() as db:
            db.get(ModDelivery, row.id).next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
            db.commit()

    due_now()
    asyncio.run(worker.run_once())
    row2 = _deliveries(db_session_factory)[0]
    # 間隔は回数ごとに倍になる。
    assert row2.attempts == 2
    assert row2.next_attempt_at - datetime.now(UTC) > timedelta(seconds=110)
    due_now()
    asyncio.run(worker.run_once())
    final = _deliveries(db_session_factory)[0]
    assert (final.status, final.attempts) == ("failed", 3)
    assert final.finished_at is not None
    assert len(attempts) == 3
    outcomes = [r.outcome for r in _invocations(db_session_factory)]
    assert outcomes == ["error", "error", "error"]


def test_delivery_records_verdicts_for_event_targets_only(
    db_session_factory,
    local_store,
    tmp_path: Path,  # noqa: ANN001
) -> None:
    settings = _settings(tmp_path)
    _add_webhook(db_session_factory, settings, events=[contract.ASSET_INGESTED])
    runtime = _runtime(db_session_factory, local_store, settings)
    _reload(runtime)
    with db_session_factory() as db:
        asset = ingest(db, local_store, make_png_bytes(), AssetKind.UPLOAD)
        assert runtime.enqueue_asset_ingested(db, asset, "upload")
        db.commit()
        asset_id = str(asset.id)
    received: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        received.append(payload)
        return httpx.Response(
            200,
            json=[
                {
                    "target": {"type": "asset", "id": asset_id},
                    "verdict": "flag",
                    "label": "類似の疑い",
                    "score": 0.91,
                    "reference": "case-1",
                },
                # このイベントで届いていない Asset への判定は捨てる。
                {"target": {"type": "asset", "id": str(uuid.uuid4())}, "verdict": "deny"},
            ],
        )

    runtime.http_transport = httpx.MockTransport(handler)
    worker = ModDeliveryWorker(db_session_factory, runtime)
    asyncio.run(worker.run_once())
    assert received[0]["event"] == "asset.ingested"
    assert received[0]["subject"]["source"] == "upload"
    assert received[0]["subject"]["asset"]["asset_id"] == asset_id
    assert _deliveries(db_session_factory)[0].status == "done"
    with db_session_factory() as db:
        verdicts = db.execute(select(ModVerdict)).scalars().all()
        assert [(str(v.asset_id), v.verdict, v.label, v.score) for v in verdicts] == [
            (asset_id, "flag", "類似の疑い", 0.91)
        ]
    assert [r.outcome for r in _invocations(db_session_factory)] == ["ok"]


def test_delivery_to_disabled_mod_is_skipped(
    db_session_factory,
    local_store,
    tmp_path: Path,  # noqa: ANN001
) -> None:
    settings = _settings(tmp_path)
    webhook_id = _add_webhook(db_session_factory, settings, events=[contract.RUN_FINISHED])
    runtime = _runtime(db_session_factory, local_store, settings)
    _reload(runtime)
    run_id = _make_run(db_session_factory, RunStatus.CANCELED)
    with db_session_factory() as db:
        runtime.enqueue_run_finished(db, db.get(Run, run_id))
        db.commit()
        mods_config.update_webhook(db, settings.data_dir, webhook_id, {"enabled": False})
        runtime.reload(db)
    asyncio.run(ModDeliveryWorker(db_session_factory, runtime).run_once())
    assert _deliveries(db_session_factory)[0].status == "done"
    assert [r.outcome for r in _invocations(db_session_factory)] == ["skipped"]


def test_reset_delivering_on_startup(db_session_factory, local_store, tmp_path: Path) -> None:  # noqa: ANN001
    now = datetime.now(UTC)
    with db_session_factory() as db:
        db.add(
            ModDelivery(
                event="run.finished",
                event_id=uuid.uuid4(),
                mod_kind="webhook",
                mod_id="x",
                payload={},
                status="delivering",
                attempts=0,
                next_attempt_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        db.commit()
    assert reset_delivering(db_session_factory) == 1
    assert _deliveries(db_session_factory)[0].status == "queued"


def test_enqueue_without_subscribers_adds_nothing(
    db_session_factory,
    local_store,
    tmp_path: Path,  # noqa: ANN001
) -> None:
    runtime = _runtime(db_session_factory, local_store, _settings(tmp_path))
    _reload(runtime)
    run_id = _make_run(db_session_factory)
    with db_session_factory() as db:
        assert not runtime.enqueue_run_finished(db, db.get(Run, run_id))
        db.commit()
    assert _deliveries(db_session_factory) == []


# -- 送り先の区分 ---------------------------------------------------------------------


def test_locality_defaults_and_priority(db_session_factory, tmp_path: Path) -> None:  # noqa: ANN001
    settings = _settings(tmp_path)
    with db_session_factory() as db:
        # OpenAI の公式の接続先だけ cloud。ほかは unset。
        assert locality_domain.provider_locality(db, settings, "openai") == ("cloud", "default")
        assert locality_domain.provider_locality(db, settings, "comfyui") == ("unset", "default")
        assert locality_domain.provider_locality(db, settings, "sdwebui") == ("unset", "default")
        # Base URL を変えた OpenAI は unset。
        proxied = _settings(tmp_path, openai_base_url="http://proxy.local/v1")
        assert locality_domain.provider_locality(db, proxied, "openai") == ("unset", "default")

        locality_domain.save_provider_localities(db, {"comfyui": "local", "openai": "unset"})
        assert locality_domain.provider_locality(db, settings, "comfyui") == ("local", "connection")
        assert locality_domain.provider_locality(db, settings, "openai") == ("unset", "default")
        locality_domain.save_provider_localities(db, {"openai": None})
        assert locality_domain.provider_locality(db, settings, "openai") == ("cloud", "default")

        # ワークフローの値は unset でなければ接続より優先する。
        workflow = ComfyWorkflow(
            name="wf",
            operation=RunOperation.GENERATE,
            template={},
            bindings={},
            exposed_params=[],
            template_sha256="0" * 64,
            locality="unset",
        )
        db.add(workflow)
        db.commit()
        run = Run(
            provider="comfyui",
            model=str(workflow.id),
            operation=RunOperation.GENERATE,
            prompt="p",
            params={"comfyui_workflow": {"id": str(workflow.id), "name": "wf"}},
        )
        db.add(run)
        db.commit()
        target = mods_events.run_target(db, settings, run)
        assert (target.locality, target.locality_source) == ("local", "connection")
        assert target.workflow == contract.WorkflowRef(id=str(workflow.id), name="wf")
        workflow.locality = "cloud"
        db.commit()
        target = mods_events.run_target(db, settings, run)
        assert (target.locality, target.locality_source) == ("cloud", "workflow")


def test_llm_connection_locality(db_session_factory, tmp_path: Path) -> None:  # noqa: ANN001
    settings = _settings(tmp_path)
    with db_session_factory() as db:
        connection_id = llm_connections.add_connection(
            db, "ollama", "http://gpu.local:11434/v1", "chat", "local"
        )
        connections = llm_connections.load(db)
        builtin = llm_connections.find_connection(connections, "openai")
        user = llm_connections.find_connection(connections, connection_id)
        assert builtin is not None and user is not None
        target = mods_events.llm_target(db, settings, builtin, model="gpt", base_url=None)
        assert (target.provider, target.host, target.locality, target.locality_source) == (
            "openai",
            "api.openai.com",
            "cloud",
            "default",
        )
        target = mods_events.llm_target(db, settings, user, model="q", base_url=user.base_url)
        assert (target.provider, target.host, target.locality, target.locality_source) == (
            "openai_compatible",
            "gpu.local",
            "local",
            "connection",
        )
        # 組み込みの接続先の区分は、OpenAI の接続の区分そのもの。
        llm_connections.update_connection(db, "openai", {"locality": "local"})
        assert locality_domain.provider_locality(db, settings, "openai") == ("local", "connection")


def test_locality_settings_api(client: TestClient) -> None:
    body = client.get("/api/settings/provider-localities").json()
    assert body["openai"] == {"value": "cloud", "source": "default", "default": "cloud"}
    assert body["comfyui"]["value"] == "unset"
    response = client.patch(
        "/api/settings/provider-localities", json={"comfyui": "local", "sdwebui": "cloud"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["comfyui"] == {
        "value": "local",
        "source": "connection",
        "default": "unset",
    }
    assert (
        client.patch("/api/settings/provider-localities", json={"comfyui": "far"}).status_code
        == 422
    )
    reset = client.patch("/api/settings/provider-localities", json={"comfyui": None}).json()
    assert reset["comfyui"]["source"] == "default"

    created = client.post(
        "/api/settings/llm-connections",
        json={"name": "local llm", "base_url": "http://llm.local/v1", "locality": "local"},
    )
    assert created.status_code == 201, created.text
    views = {c["name"]: c for c in created.json()["connections"]}
    assert views["local llm"]["locality"] == "local"
    assert views["local llm"]["locality_source"] == "connection"
