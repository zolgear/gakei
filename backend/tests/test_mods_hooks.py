"""ADR-0051 2章: `ai.before_send` の4つの入口(Run の実行、自動タイトル・タグ、アシスタント、
リモートの埋め込み)と、`asset.ingested` / `run.finished` を積む場所。管理者設定の Mod の API。"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import ModDelivery, ModInvocation
from tests.conftest import make_png_bytes, wait_for_run_terminal

MODS = "/api/settings/mods"
SECRET = "hook-secret-value-0123456789"


class _Hook:
    """Webhook の受け口(MockTransport)。届いたイベントを覚え、決めた応答を返す。"""

    def __init__(self, decision: dict[str, Any] | None = None) -> None:
        self.events: list[dict[str, Any]] = []
        self.decision = decision or {"decision": "allow"}

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        self.events.append(payload)
        if payload["event"] == "ai.before_send":
            return httpx.Response(200, json=self.decision)
        return httpx.Response(200, json=[])


def _install(client: TestClient, hook: _Hook, **webhook: Any) -> dict:
    body = {
        "name": "社内チェック",
        "url": "https://check.example.test/hook",
        "secret": SECRET,
        "enabled": True,
        "events": ["ai.before_send"],
        **webhook,
    }
    response = client.post(f"{MODS}/webhooks", json=body)
    assert response.status_code == 201, response.text
    client.app.state.mods.http_transport = httpx.MockTransport(hook)
    return response.json()


def _invocations(client: TestClient) -> list[ModInvocation]:
    with client.app.state.session_factory() as db:
        rows = db.execute(select(ModInvocation).order_by(ModInvocation.created_at)).scalars().all()
        for row in rows:
            db.expunge(row)
        return list(rows)


def _create_run(client: TestClient, prompt: str = "a red apple") -> str:
    response = client.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": "gpt-image-2.5-sunburst",
            "prompt": prompt,
            "params": {"n": 1, "size": "1024x1024"},
        },
    )
    assert response.status_code == 202, response.text
    return response.json()["id"]


def _upload(client: TestClient, kind: str = "upload") -> dict:
    response = client.post(
        "/api/assets",
        files={"file": ("a.png", make_png_bytes(48, 32), "image/png")},
        data={"kind": kind},
    )
    assert response.status_code == 201, response.text
    return response.json()


# -- image_run ---------------------------------------------------------------------


def test_image_run_denied_marks_run_failed(client: TestClient) -> None:
    hook = _Hook({"decision": "deny", "reason": "社外秘の語を含みます", "reference": "R-1"})
    _install(client, hook)
    run_id = _create_run(client, "secret project apple")
    detail = wait_for_run_terminal(client, run_id)
    assert detail["status"] == "failed"
    assert detail["error_code"] == "hook_denied"
    assert "社内チェック" in detail["error_message"]
    assert "社外秘の語を含みます" in detail["error_message"]
    assert detail["outputs"] == []

    event = hook.events[0]
    assert event["event"] == "ai.before_send"
    assert event["schema_version"] == 1
    assert event["actor"] is None  # 個人モード
    subject = event["subject"]
    assert subject["purpose"] == "image_run"
    assert subject["run_id"] == run_id
    assert subject["text"] == [{"role": "prompt", "text": "secret project apple"}]
    assert subject["params"]["size"] == "1024x1024"
    assert subject["target"]["provider"] == "fake"
    assert subject["target"]["locality"] == "unset"
    # API キーは載せない。
    assert "key" not in json.dumps(subject["target"]).lower()

    rows = _invocations(client)
    assert [(r.outcome, r.effective, r.purpose, str(r.run_id)) for r in rows] == [
        ("deny", "deny", "image_run", run_id)
    ]
    assert rows[0].reference == "R-1"


def test_image_run_allowed_runs_and_reports_finished(client: TestClient) -> None:
    hook = _Hook()
    _install(client, hook, events=["ai.before_send", "run.finished", "asset.ingested"])
    run_id = _create_run(client)
    detail = wait_for_run_terminal(client, run_id)
    assert detail["status"] == "succeeded"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        names = [e["event"] for e in hook.events]
        if "run.finished" in names and "asset.ingested" in names:
            break
        time.sleep(0.05)
    by_name = {e["event"]: e for e in hook.events}
    finished = by_name["run.finished"]["subject"]
    assert finished["run_id"] == run_id and finished["status"] == "succeeded"
    assert [o["asset_id"] for o in finished["outputs"]] == [
        o["asset_id"] for o in detail["outputs"]
    ]
    ingested = by_name["asset.ingested"]["subject"]
    assert (ingested["source"], ingested["run_id"]) == ("generated", run_id)


# -- asset.ingested と run.finished(取り消し) -----------------------------------------


def test_upload_and_cancel_are_queued(client_no_runner: TestClient) -> None:
    client = client_no_runner
    hook = _Hook()
    _install(client, hook, events=["asset.ingested", "run.finished"])
    _upload(client, "sketch")
    run_id = _create_run(client)
    assert client.post(f"/api/runs/{run_id}/cancel").status_code == 200
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and len(hook.events) < 2:
        time.sleep(0.05)
    by_name = {e["event"]: e["subject"] for e in hook.events}
    assert by_name["asset.ingested"]["source"] == "sketch"
    assert by_name["asset.ingested"]["kind"] == "sketch"
    assert (by_name["run.finished"]["run_id"], by_name["run.finished"]["status"]) == (
        run_id,
        "canceled",
    )
    with client.app.state.session_factory() as db:
        statuses = {r.status for r in db.execute(select(ModDelivery)).scalars()}
    assert statuses == {"done"}


def test_source_filter(client: TestClient) -> None:
    hook = _Hook()
    _install(client, hook, events=["asset.ingested"], filters={"sources": ["mask"]})
    _upload(client, "upload")
    _upload(client, "mask")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not hook.events:
        time.sleep(0.05)
    time.sleep(0.3)
    assert [e["subject"]["source"] for e in hook.events] == ["mask"]


# -- annotation -----------------------------------------------------------------------


def test_annotation_denied_marks_annotation_failed(client: TestClient) -> None:
    hook = _Hook({"decision": "deny", "reason": "画像は外へ送れません"})
    _install(client, hook, filters={"purposes": ["annotation"]})
    response = client.patch(
        "/api/settings/annotation",
        json={"auto_on_ingest": True, "llm_enabled": True, "vlm_enabled": True},
    )
    assert response.status_code == 200, response.text
    asset = _upload(client)
    deadline = time.monotonic() + 10
    body: dict = {}
    while time.monotonic() < deadline:
        body = client.get(f"/api/assets/{asset['id']}").json()
        if body.get("annotation") and body["annotation"]["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.05)
    assert body["annotation"]["status"] == "failed"
    assert "画像は外へ送れません" in body["annotation"]["error"]
    subject = hook.events[0]["subject"]
    assert subject["purpose"] == "annotation"
    assert subject["asset_id"] == asset["id"]
    assert [i["asset_id"] for i in subject["images"]] == [asset["id"]]
    assert subject["target"]["connection"] == {"id": "openai", "name": None, "builtin": True}
    assert subject["target"]["locality"] == "cloud"


# -- assistant ------------------------------------------------------------------------


def test_assistant_denied_stops_the_turn(client: TestClient) -> None:
    from app.assistant.engine import FakeAssistantEngine
    from app.assistant.service import AssistantTurn, TurnInput
    from app.auth.identity import LOCAL_ADMIN
    from app.domain import assistant as assistant_domain
    from app.domain.assistant_settings import AssistantConfig

    hook = _Hook({"decision": "deny", "reason": "相談の内容は外へ送れません"})
    _install(client, hook, filters={"purposes": ["assistant"]})
    state = client.app.state
    runtime = state.assistant
    engine = FakeAssistantEngine()
    runtime.engine = engine
    with state.session_factory() as db:
        conversation = assistant_domain.create_conversation(db, LOCAL_ADMIN)
        message = assistant_domain.append_message(
            db, conversation.id, assistant_domain.ROLE_USER, {"text": "hi", "attachments": []}
        )
        db.refresh(message)
        db.expunge(message)
        conversation_id = conversation.id
    assert runtime.begin(conversation_id)
    turn = AssistantTurn(
        state,
        runtime,
        TurnInput(
            conversation_id=conversation_id,
            user=LOCAL_ADMIN,
            config=AssistantConfig(enabled=True),
            target=None,
            locale="ja",
            user_message=message,
        ),
    )

    async def collect() -> list[str]:
        return [chunk async for chunk in turn.events()]

    chunks = client.portal.call(collect)
    text = "".join(chunks)
    assert "hook_denied" in text
    assert "相談の内容は外へ送れません" in text
    assert "text_delta" not in text
    subject = hook.events[0]["subject"]
    assert subject["purpose"] == "assistant"
    assert {"role": "user", "text": "hi"} in subject["text"]


# -- embedding(リモート) ----------------------------------------------------------------


def test_remote_embedding_denied(tmp_path, db_session_factory) -> None:  # noqa: ANN001
    from app.config import Settings
    from app.domain import embedding_settings, llm_connections, semantic_search
    from app.domain import embeddings as embeddings_domain
    from app.domain.assets import ingest
    from app.domain.models import AssetEmbedding, AssetKind
    from app.domain.storage import LocalFsStore
    from app.embedding.engines import EmbeddingEngines
    from app.mods import config as mods_config
    from app.mods.runtime import ModRuntime
    from app.worker.embedder import Embedder

    settings = Settings(_env_file=None, data_dir=tmp_path / "data", fake_provider=False)
    store = LocalFsStore(tmp_path / "store")
    factory = db_session_factory
    sent: list[httpx.Request] = []

    def infinity(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        body = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "data": [{"index": i, "embedding": [1.0, 0.0]} for i in range(len(body["input"]))]
            },
        )

    with factory() as db:
        connection_id = llm_connections.add_connection(
            db, "infinity", "http://infer.local:7997", "chat", "local"
        )
        embedding_settings.save(
            db,
            {
                "enabled": True,
                "engine": "remote",
                "remote_connection_id": connection_id,
                "remote_model": "clip",
            },
        )
        asset = ingest(db, store, make_png_bytes(), AssetKind.UPLOAD)
        assert embeddings_domain.enqueue_on_ingest(db, asset, settings)
        mods_config.add_webhook(
            db,
            settings.data_dir,
            {
                "name": "embed-check",
                "url": "https://check.example.test/hook",
                "secret": SECRET,
                "enabled": True,
                "events": ["ai.before_send"],
            },
        )
        db.commit()
        asset_id = asset.id

    hook = _Hook({"decision": "deny", "reason": "埋め込みは止めます"})
    mods = ModRuntime(factory, store, settings)
    mods.scan()
    with factory() as db:
        mods.reload(db)
    mods.http_transport = httpx.MockTransport(hook)
    engines = EmbeddingEngines(
        settings,
        remote_client_factory=lambda: httpx.Client(transport=httpx.MockTransport(infinity)),
    )
    engines.mods = mods
    embedder = Embedder(factory, store, settings, engines)
    job = embedder._prepare()
    assert job is not None
    asyncio.run(embedder._process(job))
    assert sent == []
    with factory() as db:
        row = db.execute(select(AssetEmbedding)).scalar_one()
        assert row.status == "failed"
        assert "埋め込みは止めます" in (row.error or "")
    subject = hook.events[0]["subject"]
    assert subject["purpose"] == "embedding"
    assert subject["target"]["provider"] == "infinity"
    assert subject["target"]["host"] == "infer.local"
    assert (subject["target"]["locality"], subject["target"]["locality_source"]) == (
        "local",
        "connection",
    )
    assert [i["asset_id"] for i in subject["images"]] == [str(asset_id)]

    # 文章での検索も、送る前に止まる。
    with factory() as db:
        active = semantic_search.require_active(db, settings)
        with pytest.raises(semantic_search.EmbeddingsEngineError, match="埋め込みは止めます"):
            semantic_search.embed_query(
                db, active, "a cat", engines, semantic_search.QueryVectorCache()
            )
    assert sent == []
    assert hook.events[-1]["subject"]["text"] == [{"role": "query", "text": "a cat"}]


# -- 管理者設定の API ---------------------------------------------------------------------


def test_mods_api_never_returns_the_secret(client: TestClient) -> None:
    created = _install(client, _Hook())
    assert SECRET not in json.dumps(created)
    webhook = created["webhooks"][0]
    assert webhook["secret_set"] is True
    assert "secret" not in webhook
    listed = client.get(MODS)
    assert SECRET not in listed.text
    updated = client.patch(
        f"{MODS}/webhooks/{webhook['id']}", json={"secret": "another-secret", "name": "renamed"}
    )
    assert updated.status_code == 200
    assert "another-secret" not in updated.text
    assert updated.json()["webhooks"][0]["name"] == "renamed"

    # テスト送信: 応答と所要時間を返す。記録は残さない。
    result = client.post(f"{MODS}/webhooks/{webhook['id']}/test", json={"event": "ai.before_send"})
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["outcome"] == "allow" and body["status_code"] == 200
    assert body["duration_ms"] >= 0
    assert SECRET not in result.text
    assert _invocations(client) == []

    assert client.delete(f"{MODS}/webhooks/{webhook['id']}").json()["webhooks"] == []
    assert client.delete(f"{MODS}/webhooks/{webhook['id']}").status_code == 404


def test_mods_api_validation_and_lists(client: TestClient) -> None:
    for bad in (
        {"name": "", "url": "https://x.test", "secret": "s"},
        {"name": "x", "url": "ftp://x.test", "secret": "s"},
        {"name": "x", "url": "https://user:pw@x.test", "secret": "s"},
        {"name": "x", "url": "https://x.test", "secret": ""},
        {"name": "x", "url": "https://x.test", "secret": "s", "timeout_seconds": 0},
    ):
        assert client.post(f"{MODS}/webhooks", json=bad).status_code == 422, bad
    assert client.patch(MODS, json={"instance_name": "Studio A"}).json()["instance_name"] == (
        "Studio A"
    )
    hook = _Hook({"decision": "deny", "reason": "no"})
    _install(client, hook)
    run_id = _create_run(client)
    wait_for_run_terminal(client, run_id)
    assert hook.events[0]["instance"]["name"] == "Studio A"
    invocations = client.get(f"{MODS}/invocations").json()["items"]
    assert [(i["outcome"], i["run_id"]) for i in invocations] == [("deny", run_id)]
    assert client.get(f"{MODS}/invocations", params={"failed_only": True}).json()["items"]
    assert client.get(f"{MODS}/deliveries").json()["items"] == []
    assert (
        client.patch(f"{MODS}/python/{uuid.uuid4().hex}", json={"enabled": True}).status_code == 404
    )


def test_mods_api_requires_admin(client_oidc: TestClient) -> None:
    from tests.conftest import login_as

    login_as(client_oidc, "user@example.com")
    assert client_oidc.get(MODS).status_code == 403
    assert client_oidc.get("/api/settings/provider-localities").status_code == 403
