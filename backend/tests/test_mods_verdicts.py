"""ADR-0051 2章・4章・8章・9章: 判定を待つ購読、`deny` の判定の効力と画像を残さない処理、判定の
受付の API、画像を返す経路(410、共有リンク、系列の ZIP、MCP、アシスタント)。実 API は呼ばない。"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import time
import uuid
import zipfile
from email.parser import BytesParser
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.auth.identity import LOCAL_ADMIN
from app.domain import assets as assets_domain
from app.domain import content_withholding
from app.domain import embeddings as embeddings_domain
from app.domain.models import (
    Asset,
    AssetAnnotation,
    AssetEmbedding,
    AssetFocalPoint,
    AssetKind,
    AssetPerceptualHash,
    ModDelivery,
    ModInvocation,
    ModVerdict,
)
from app.domain.storage import LocalFsStore
from app.domain.vector_index import AssetFilter
from app.mods import api as contract
from app.mods import webhook as webhook_send
from app.mods.config import Filters
from app.mods.runtime import Subscriber, VerdictTargetError
from tests.conftest import make_png_bytes, wait_for_run_terminal

MODS = "/api/settings/mods"
SECRET = "verdict-secret-0123456789"
_MODEL = "gpt-image-2.5-sunburst"
_LABEL = "類似の疑い"
_MESSAGE = "既存の作品に似ています"


# -- Webhook の受け口 -----------------------------------------------------------------


def _json_part(content_type: str, body: bytes) -> tuple[dict, list[bytes]]:
    """multipart の本文から、イベントの JSON と画像の部分を取り出す。"""
    message = BytesParser().parsebytes(
        b"Content-Type: " + content_type.encode("ascii") + b"\r\n\r\n" + body
    )
    event: dict | None = None
    images: list[bytes] = []
    for part in message.get_payload():
        data = part.get_payload(decode=True)
        if part.get_param("name", header="content-disposition") == "event":
            event = json.loads(data)
        else:
            images.append(data)
    assert event is not None
    return event, images


class _VerdictHook:
    """届いた Asset(`asset.ingested` の Asset、`run.finished` の出力)に、決めた判定を返す。"""

    def __init__(self, verdict: str | None = "deny", status: int = 200) -> None:
        self.verdict = verdict
        self.status = status
        self.events: list[dict] = []
        self.images: list[bytes] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        content_type = request.headers["content-type"]
        if content_type.startswith("multipart/"):
            payload, images = _json_part(content_type, request.content)
            self.images.extend(images)
        else:
            payload = json.loads(request.content)
        self.events.append(payload)
        if self.status != 200:
            return httpx.Response(self.status)
        if payload["event"] == "ai.before_send":
            return httpx.Response(200, json={"decision": "allow"})
        subject = payload["subject"]
        if payload["event"] == "asset.ingested":
            targets = [subject["asset"]["asset_id"]]
        else:
            targets = [o["asset_id"] for o in subject["outputs"]]
        if self.verdict is None:
            return httpx.Response(200, json=[])
        return httpx.Response(
            200,
            json=[
                {
                    "target": {"type": "asset", "id": target},
                    "verdict": self.verdict,
                    "label": _LABEL,
                    "score": 0.93,
                    "message": _MESSAGE,
                    "reference": "CASE-1",
                    "url": "https://check.example.test/cases/1",
                }
                for target in targets
            ],
        )


def _install(client: TestClient, hook: _VerdictHook, **webhook: Any) -> str:
    body = {
        "name": "侵害チェック",
        "url": "https://check.example.test/hook",
        "secret": SECRET,
        "enabled": True,
        "events": ["asset.ingested"],
        **webhook,
    }
    response = client.post(f"{MODS}/webhooks", json=body)
    assert response.status_code == 201, response.text
    client.app.state.mods.http_transport = httpx.MockTransport(hook)
    return response.json()["webhooks"][-1]["id"]


def _upload(client: TestClient, data: bytes | None = None, kind: str = "upload") -> dict:
    response = client.post(
        "/api/assets",
        files={"file": ("a.png", data or make_png_bytes(40, 30), "image/png")},
        data={"kind": kind},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _asset(client: TestClient, asset_id: str) -> Asset:
    with client.app.state.session_factory() as db:
        asset = db.get(Asset, uuid.UUID(asset_id))
        assert asset is not None
        db.expunge(asset)
        return asset


def _has_files(store: Any, asset: Asset) -> bool:
    return store.exists(asset.blob_key) or bool(list(store.list_keys(f"derived/{asset.sha256}/")))


def _deny(client: TestClient, asset_id: str, mod_id: str = "manual") -> None:
    """非同期の判定と同じ道筋(`apply_denials` → commit → `purge`)で `deny` を当てる。"""
    factory = client.app.state.session_factory
    with factory() as db:
        verdict = ModVerdict(
            mod_kind="webhook",
            mod_id=mod_id,
            mod_name="侵害チェック",
            asset_id=uuid.UUID(asset_id),
            verdict="deny",
            label=_LABEL,
            message=_MESSAGE,
        )
        db.add(verdict)
        db.flush()
        targets = content_withholding.apply_denials(db, [verdict])
        db.commit()
    content_withholding.purge(client.app.state.store, targets)


def _wait(predicate, timeout: float = 10.0) -> None:  # noqa: ANN001
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise TimeoutError("条件が満たされませんでした")


def _create_run(client: TestClient, inputs: list[str] | None = None) -> Any:
    body: dict[str, Any] = {
        "operation": "edit" if inputs else "generate",
        "model": _MODEL,
        "prompt": "a red apple",
        "params": {"n": 1, "quality": "low"},
    }
    if inputs:
        body["inputs"] = [
            {"asset_id": asset_id, "role": "image", "position": i}
            for i, asset_id in enumerate(inputs)
        ]
    return client.post("/api/runs", json=body)


# -- 判定を待つ(同期) ----------------------------------------------------------------


def test_wait_for_verdict_deny_never_writes_original(client: TestClient) -> None:
    hook = _VerdictHook("deny")
    mod_id = _install(client, hook, wait_for_verdict=True, send_images=True)
    data = make_png_bytes(40, 30, (1, 2, 3))
    body = _upload(client, data)

    # 応答の時点で、画像を残さなかった Asset になっている。
    assert body["content_withheld"] is True
    assert body["content_withheld_at"] is not None
    assert body["content_withheld_verdict"]["label"] == _LABEL
    assert [v["verdict"] for v in body["verdicts"]] == ["deny"]
    assert body["verdicts"][0]["mod_name"] == "侵害チェック"
    assert body["verdicts"][0]["url"] == "https://check.example.test/cases/1"

    # Mod には、保存の前のバイト列がそのまま届いた(base64 ではない)。
    assert hook.images == [data]
    event = hook.events[0]
    assert event["subject"]["asset"]["asset_id"] == body["id"]
    assert event["subject"]["asset"]["sha256"] == hashlib.sha256(data).hexdigest()

    # 原本も派生も一度も書いていない。
    asset = _asset(client, body["id"])
    assert not _has_files(client.app.state.store, asset)

    # 呼び出しと判定を記録し、非同期の配達には重ねて積まない。
    with client.app.state.session_factory() as db:
        invocations = db.execute(select(ModInvocation)).scalars().all()
        assert [(i.event, i.outcome, str(i.asset_id)) for i in invocations] == [
            ("asset.ingested", "ok", body["id"])
        ]
        assert db.execute(select(func.count()).select_from(ModDelivery)).scalar_one() == 0
        verdict = db.get(ModVerdict, asset.content_withheld_verdict_id)
        assert verdict is not None and verdict.mod_id == mod_id
        assert verdict.invocation_id == invocations[0].id

    # 一覧にも状態が出る。
    items = client.get("/api/assets").json()["items"]
    assert items[0]["id"] == body["id"] and items[0]["content_withheld"] is True


def test_wait_for_verdict_ok_stores_normally(client: TestClient) -> None:
    hook = _VerdictHook("ok")
    _install(client, hook, wait_for_verdict=True)
    body = _upload(client)
    assert body["content_withheld"] is False
    assert [v["verdict"] for v in body["verdicts"]] == ["ok"]
    assert _has_files(client.app.state.store, _asset(client, body["id"]))
    assert client.get(f"/api/assets/{body['id']}/content?variant=thumb").status_code == 200


@pytest.mark.parametrize(("on_failure", "withheld"), [("block", True), ("allow", False)])
def test_wait_for_verdict_failure_follows_setting(
    client: TestClient, on_failure: str, withheld: bool
) -> None:
    hook = _VerdictHook(status=500)
    _install(client, hook, wait_for_verdict=True, on_failure=on_failure)
    body = _upload(client)
    assert body["content_withheld"] is withheld
    with client.app.state.session_factory() as db:
        invocation = db.execute(select(ModInvocation)).scalar_one()
        assert invocation.outcome == "error"
        assert invocation.effective == ("deny" if withheld else "allow")
    if withheld:
        assert body["content_withheld_verdict"]["label"] == "チェックの失敗"
        assert not _has_files(client.app.state.store, _asset(client, body["id"]))
    else:
        assert body["verdicts"] == []


def test_wait_for_verdict_run_finished_denies_outputs(client: TestClient) -> None:
    hook = _VerdictHook("deny")
    mod_id = _install(client, hook, events=["run.finished"], wait_for_verdict=True)
    response = _create_run(client)
    assert response.status_code == 202, response.text
    detail = wait_for_run_terminal(client, response.json()["id"])

    assert detail["status"] == "succeeded"
    output_id = detail["outputs"][0]["asset_id"]
    assert detail["outputs"][0]["content_withheld"] is True
    event = hook.events[0]
    assert event["event"] == "run.finished"
    assert event["subject"]["status"] == "succeeded"
    assert [o["asset_id"] for o in event["subject"]["outputs"]] == [output_id]
    assert not _has_files(client.app.state.store, _asset(client, output_id))
    assert client.get(f"/api/assets/{output_id}/content").status_code == 410

    # 同期で呼んだので、run.finished は配達の待ち行列に積まない。
    with client.app.state.session_factory() as db:
        rows = db.execute(select(ModDelivery).where(ModDelivery.mod_id == mod_id)).all()
        assert rows == []
    assert len(hook.events) == 1


# -- 非同期の判定 ----------------------------------------------------------------------


def test_async_deny_removes_original_and_derived(client: TestClient) -> None:
    hook = _VerdictHook("deny")
    _install(client, hook)
    body = _upload(client)
    assert body["content_withheld"] is False
    store = client.app.state.store
    assert _has_files(store, _asset(client, body["id"]))

    _wait(lambda: client.get(f"/api/assets/{body['id']}").json()["content_withheld"])
    asset = _asset(client, body["id"])
    assert not _has_files(store, asset)
    detail = client.get(f"/api/assets/{body['id']}").json()
    assert detail["content_withheld_verdict"]["message"] == _MESSAGE


def test_withheld_content_is_410_for_every_variant(client: TestClient) -> None:
    body = _upload(client)
    _deny(client, body["id"])
    for variant in ("thumb", "preview", "original"):
        response = client.get(f"/api/assets/{body['id']}/content?variant={variant}&download=1")
        assert response.status_code == 410, response.text
        assert _LABEL in response.json()["detail"]
        assert _MESSAGE in response.json()["detail"]


def test_later_ok_verdict_does_not_restore(client: TestClient) -> None:
    body = _upload(client)
    _deny(client, body["id"])
    with client.app.state.session_factory() as db:
        db.add(
            ModVerdict(
                mod_kind="webhook",
                mod_id="manual",
                mod_name="侵害チェック",
                asset_id=uuid.UUID(body["id"]),
                verdict="ok",
            )
        )
        db.commit()
    detail = client.get(f"/api/assets/{body['id']}").json()
    assert detail["content_withheld"] is True
    assert [v["verdict"] for v in detail["verdicts"]] == ["ok"]
    assert client.get(f"/api/assets/{body['id']}/content").status_code == 410


# -- 同じ内容への波及と、画像から作ったもの --------------------------------------------


def _put(store: Any, key: str, data: bytes) -> None:
    if isinstance(store, LocalFsStore):
        path = store.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    else:
        store.overwrite(key, data)


@pytest.mark.parametrize("store_kind", ["local", "s3"])
def test_withhold_spreads_to_same_content_and_drops_image_products(
    store_kind: str,
    request: pytest.FixtureRequest,
    db_session_factory: sessionmaker,
    tmp_path: Path,
) -> None:
    store: Any = (
        LocalFsStore(tmp_path / "store")
        if store_kind == "local"
        else request.getfixturevalue("s3_store")
    )
    data = make_png_bytes(32, 32, (200, 10, 10))
    other = make_png_bytes(32, 32, (10, 200, 10))
    sha = hashlib.sha256(data).hexdigest()
    with db_session_factory() as db:
        first = assets_domain.ingest(db, store, data, AssetKind.UPLOAD)
        twin = assets_domain.ingest(db, store, data, AssetKind.UPLOAD)
        keep = assets_domain.ingest(db, store, other, AssetKind.UPLOAD)
        assert twin.blob_key == first.blob_key
        # 古い版の派生も残っている。
        _put(store, f"derived/{sha}/thumb.v9.webp", b"old")
        db.add(AssetEmbedding(asset_id=first.id, model_key="m", status="succeeded", dim=2))
        db.add(AssetEmbedding(asset_id=twin.id, model_key="m", status="queued"))
        db.add(AssetPerceptualHash(asset_id=first.id, dhash=b"12345678", color=b"0", version=1))
        db.add(AssetFocalPoint(asset_id=twin.id, method="none", version=1))
        db.add(AssetAnnotation(asset_id=first.id, title="残すタイトル", auto_status="queued"))
        verdict = ModVerdict(
            mod_kind="webhook",
            mod_id="w1",
            mod_name="侵害チェック",
            asset_id=first.id,
            verdict="deny",
            label=_LABEL,
        )
        db.add(verdict)
        db.flush()
        targets = content_withholding.apply_denials(db, [verdict])
        db.commit()
        first_id, twin_id, keep_id, verdict_id = first.id, twin.id, keep.id, verdict.id
        blob_key, keep_key = first.blob_key, keep.blob_key
    content_withholding.purge(store, targets)

    assert not store.exists(blob_key)
    assert list(store.list_keys(f"derived/{sha}/")) == []
    assert store.exists(keep_key)

    with db_session_factory() as db:
        first = db.get(Asset, first_id)
        twin = db.get(Asset, twin_id)
        assert first.content_withheld_at is not None
        assert first.content_withheld_verdict_id == verdict_id
        assert twin.content_withheld_at is not None
        spread = db.get(ModVerdict, twin.content_withheld_verdict_id)
        assert spread.asset_id == twin_id and spread.propagated_from_id == verdict_id
        assert spread.label == _LABEL
        assert db.get(Asset, keep_id).content_withheld_at is None
        assert db.execute(select(func.count()).select_from(AssetEmbedding)).scalar_one() == 0
        assert db.execute(select(func.count()).select_from(AssetPerceptualHash)).scalar_one() == 0
        assert db.execute(select(func.count()).select_from(AssetFocalPoint)).scalar_one() == 0
        annotation = db.get(AssetAnnotation, first_id)
        assert annotation.title == "残すタイトル" and annotation.auto_status == "failed"
        assert content_withholding.blocked_asset_ids(db, [first_id, twin_id, keep_id]) == {
            first_id,
            twin_id,
        }
        assert not embeddings_domain.is_target(first)

        # 後から同じ内容を取り込んでも、保存せず、画像を残さなかった Asset になる。
        later = assets_domain.ingest(db, store, data, AssetKind.UPLOAD)
        db.commit()
        assert later.content_withheld_at is not None
        inherited = db.get(ModVerdict, later.content_withheld_verdict_id)
        assert inherited.propagated_from_id == verdict_id
    assert not store.exists(blob_key)
    assert list(store.list_keys(f"derived/{sha}/")) == []


def test_upload_of_withheld_content_is_withheld(client: TestClient) -> None:
    data = make_png_bytes(24, 24, (5, 6, 7))
    first = _upload(client, data)
    _deny(client, first["id"])
    second = _upload(client, data)
    assert second["id"] != first["id"]
    assert second["content_withheld"] is True
    assert second["verdicts"][0]["propagated"] is True
    assert not _has_files(client.app.state.store, _asset(client, second["id"]))


def test_similar_candidates_exclude_withheld(client: TestClient) -> None:
    kept = _upload(client, make_png_bytes(20, 20, (1, 1, 1)))
    withheld = _upload(client, make_png_bytes(20, 20, (2, 2, 2)))
    _deny(client, withheld["id"])
    with client.app.state.session_factory() as db:
        ids = {
            str(i)
            for i in db.execute(
                select(Asset.id).where(*AssetFilter(viewer=LOCAL_ADMIN).conditions())
            ).scalars()
        }
    assert kept["id"] in ids and withheld["id"] not in ids


# -- 外部の AI へ送らない ---------------------------------------------------------------


def test_run_creation_refuses_denied_input(client: TestClient) -> None:
    base = _upload(client)
    _deny(client, base["id"])
    response = _create_run(client, [base["id"]])
    assert response.status_code == 409, response.text
    assert "拒否" in response.json()["detail"]


def test_run_creation_refuses_latest_deny_without_withholding(client: TestClient) -> None:
    """最新の判定が `deny` の Mod があれば、画像を残さなかったかどうかに関わらず断る。"""
    base = _upload(client)
    with client.app.state.session_factory() as db:
        db.add(
            ModVerdict(
                mod_kind="python",
                mod_id="p",
                mod_name="P",
                asset_id=uuid.UUID(base["id"]),
                verdict="deny",
            )
        )
        db.commit()
    assert _create_run(client, [base["id"]]).status_code == 409


def test_queued_run_with_denied_input_fails_before_check(client_no_runner: TestClient) -> None:
    client = client_no_runner
    hook = _VerdictHook()
    _install(client, hook, events=["ai.before_send"])
    base = _upload(client)
    response = _create_run(client, [base["id"]])
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]
    _deny(client, base["id"])

    from app.worker.runner import _pick_and_start_run

    runner = client.app.state.runner
    run = _pick_and_start_run(client.app.state.session_factory, "fake")
    assert run is not None and str(run.id) == run_id
    asyncio.run(runner._execute_run(run, client.app.state.registry.get("fake")))

    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["status"] == "failed"
    assert detail["error_code"] == "hook_denied"
    assert detail["inputs"][0]["content_withheld"] is True
    # `ai.before_send` は呼んでいない。
    assert hook.events == []


def test_assistant_attachment_of_denied_image_is_refused(client: TestClient) -> None:
    assert client.patch("/api/settings/assistant", json={"enabled": True}).status_code == 200
    conversation = client.post("/api/assistant/conversations", json={"title": ""}).json()["id"]
    base = _upload(client)
    _deny(client, base["id"])
    response = client.post(
        f"/api/assistant/conversations/{conversation}/messages",
        json={"text": "これを見て", "attachments": [base["id"]]},
    )
    assert response.status_code == 409, response.text


# -- Run の詳細 ------------------------------------------------------------------------


def test_run_detail_includes_before_send_invocations(client: TestClient) -> None:
    hook = _VerdictHook()
    _install(client, hook, events=["ai.before_send"])
    response = _create_run(client)
    detail = wait_for_run_terminal(client, response.json()["id"])
    assert detail["status"] == "succeeded"
    assert [
        (i["mod_name"], i["purpose"], i["outcome"], i["effective"])
        for i in detail["mod_invocations"]
    ] == [("侵害チェック", "image_run", "allow", "allow")]
    assert "error" not in detail["mod_invocations"][0]
    assert detail["verdicts"] == []


# -- 共有リンク・系列の ZIP・MCP ---------------------------------------------------------


def test_share_includes_withheld_node_without_image(client: TestClient) -> None:
    assert client.patch("/api/settings/share", json={"enabled": True}).status_code == 200
    base = _upload(client)
    _deny(client, base["id"])
    share = client.post("/api/shares", json={"asset_id": base["id"], "scope": "single"})
    assert share.status_code == 201, share.text
    token = share.json()["url"].rsplit("/s/", 1)[1]
    client.cookies.clear()
    public = client.get(f"/api/public/shares/{token}")
    assert public.status_code == 200
    assert public.json()["assets"][0]["content_withheld"] is True
    # 判定(組織の内部の情報)は含めない。
    assert _LABEL not in public.text
    content = client.get(f"/api/public/shares/{token}/assets/{base['id']}/content?variant=thumb")
    assert content.status_code == 410
    assert _LABEL not in content.text


def test_lineage_zip_keeps_withheld_node_and_imports_it(client: TestClient) -> None:
    base = _upload(client, make_png_bytes(48, 32, (9, 9, 9)))
    response = _create_run(client, [base["id"]])
    detail = wait_for_run_terminal(client, response.json()["id"])
    output_id = detail["outputs"][0]["asset_id"]
    _deny(client, output_id)

    for mode in ("import", "delivery"):
        exported = client.get(
            f"/api/assets/{output_id}/export", params={"scope": "ancestors", "mode": mode}
        )
        assert exported.status_code == 200, exported.text
    exported = client.get(f"/api/assets/{output_id}/export", params={"scope": "ancestors"})
    with zipfile.ZipFile(io.BytesIO(exported.content)) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        names = set(zf.namelist())
    by_id = {a["id"]: a for a in manifest["assets"]}
    assert by_id[output_id]["file"] is None
    assert by_id[output_id]["content_withheld"] is True
    assert by_id[base["id"]]["file"] in names
    assert _LABEL not in json.dumps(manifest, ensure_ascii=False)

    imported = client.post(
        "/api/imports/lineage",
        files={"file": ("lineage.zip", exported.content, "application/zip")},
    )
    assert imported.status_code == 201, imported.text
    root = imported.json()["root_asset_id"]
    assert root != output_id
    root_detail = client.get(f"/api/assets/{root}").json()
    assert root_detail["content_withheld"] is True
    assert not _has_files(client.app.state.store, _asset(client, root))
    lineage = client.get(f"/api/assets/{root}/lineage").json()
    node = next(n for n in lineage["nodes"] if n["id"] == root)
    assert node["asset"]["content_withheld"] is True


_MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "MCP-Protocol-Version": "2025-11-25",
}


def _mcp(client: TestClient, name: str, arguments: dict) -> dict:
    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        headers=_MCP_HEADERS,
    )
    assert response.status_code == 200, response.text
    return response.json()["result"]


def test_mcp_does_not_return_withheld_images(client: TestClient) -> None:
    assert client.patch("/api/settings/mcp", json={"enabled": True}).status_code == 200
    base = _upload(client)
    _deny(client, base["id"])

    result = _mcp(client, "get_asset", {"asset_id": base["id"]})
    assert not result.get("isError"), result
    payload = result["structuredContent"]
    assert payload["content_withheld"] is True
    assert payload["verdicts"][0]["label"] == _LABEL
    assert [c["type"] for c in result["content"]] == ["text"]

    for tool in ("get_image", "create_download_url"):
        result = _mcp(client, tool, {"asset_id": base["id"]})
        assert result.get("isError") is True, result
        assert "withheld" in result["content"][0]["text"]


# -- 判定の受付の API ------------------------------------------------------------------


def _signed(body: bytes, *, secret: str = SECRET, timestamp: int | None = None) -> dict:
    ts = str(int(time.time()) if timestamp is None else timestamp)
    return {
        "Content-Type": "application/json",
        "X-Gakei-Timestamp": ts,
        "X-Gakei-Signature": webhook_send.sign(secret, ts, body),
    }


def _delivered_upload(client: TestClient) -> tuple[str, str]:
    """Webhook(判定は返さない)に `asset.ingested` を届け、その Mod の id と Asset の id を返す。"""
    hook = _VerdictHook(verdict=None)
    mod_id = _install(client, hook)
    body = _upload(client)

    def delivered() -> bool:
        with client.app.state.session_factory() as db:
            return (
                db.execute(
                    select(ModInvocation).where(ModInvocation.asset_id == uuid.UUID(body["id"]))
                ).first()
                is not None
            )

    _wait(delivered)
    return mod_id, body["id"]


def _verdict_body(asset_id: str, verdict: str = "deny") -> bytes:
    return json.dumps(
        {
            "verdicts": [
                {
                    "target": {"type": "asset", "id": asset_id},
                    "verdict": verdict,
                    "label": _LABEL,
                    "message": _MESSAGE,
                }
            ]
        }
    ).encode("utf-8")


def test_receive_verdicts_with_valid_signature(client: TestClient) -> None:
    mod_id, asset_id = _delivered_upload(client)
    payload = _verdict_body(asset_id)
    response = client.post(
        f"/api/mods/{mod_id}/verdicts", content=payload, headers=_signed(payload)
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"accepted": 1}
    detail = client.get(f"/api/assets/{asset_id}").json()
    assert detail["content_withheld"] is True
    assert detail["verdicts"][0]["mod_name"] == "侵害チェック"
    assert not _has_files(client.app.state.store, _asset(client, asset_id))


def test_receive_verdicts_rejects_bad_signature_and_old_timestamp(client: TestClient) -> None:
    mod_id, asset_id = _delivered_upload(client)
    payload = _verdict_body(asset_id)
    url = f"/api/mods/{mod_id}/verdicts"
    wrong = client.post(url, content=payload, headers=_signed(payload, secret="other-secret"))
    assert wrong.status_code == 401
    old = client.post(
        url, content=payload, headers=_signed(payload, timestamp=int(time.time()) - 3600)
    )
    assert old.status_code == 401
    missing = client.post(url, content=payload, headers={"Content-Type": "application/json"})
    assert missing.status_code == 401
    tampered = client.post(url, content=_verdict_body(asset_id, "ok"), headers=_signed(payload))
    assert tampered.status_code == 401
    assert client.get(f"/api/assets/{asset_id}").json()["content_withheld"] is False
    with client.app.state.session_factory() as db:
        assert db.execute(select(func.count()).select_from(ModVerdict)).scalar_one() == 0


def test_receive_verdicts_rejects_targets_not_delivered(client: TestClient) -> None:
    mod_id, asset_id = _delivered_upload(client)
    # この Mod に届けていない Asset(Webhook の前に取り込んだもの、存在しないもの)。
    with client.app.state.session_factory() as db:
        other = db.execute(select(Asset.id).where(Asset.id != uuid.UUID(asset_id))).first()
    stranger = str(other[0]) if other else str(uuid.uuid4())
    for target in (stranger, str(uuid.uuid4())):
        payload = json.dumps(
            [
                {"target": {"type": "asset", "id": asset_id}, "verdict": "flag"},
                {"target": {"type": "asset", "id": target}, "verdict": "deny"},
            ]
        ).encode()
        response = client.post(
            f"/api/mods/{mod_id}/verdicts", content=payload, headers=_signed(payload)
        )
        assert response.status_code == 403, response.text
    # 1つも記録しない。
    with client.app.state.session_factory() as db:
        assert db.execute(select(func.count()).select_from(ModVerdict)).scalar_one() == 0


def test_receive_verdicts_unknown_or_disabled_mod(client: TestClient) -> None:
    payload = _verdict_body(str(uuid.uuid4()))
    assert (
        client.post(
            "/api/mods/nope/verdicts", content=payload, headers=_signed(payload)
        ).status_code
        == 404
    )
    mod_id = _install(client, _VerdictHook(), enabled=False)
    response = client.post(
        f"/api/mods/{mod_id}/verdicts", content=payload, headers=_signed(payload)
    )
    assert response.status_code == 404


def test_receive_verdicts_needs_no_login(client_oidc: TestClient) -> None:
    """ログインしていなくても(Cookie なしでも)、署名が無ければ 401(ログインの 401 ではなく
    署名の確認まで進む)。"""
    client = client_oidc
    client.cookies.clear()
    payload = _verdict_body(str(uuid.uuid4()))
    response = client.post("/api/mods/nope/verdicts", content=payload)
    assert response.status_code == 404


def test_python_mod_can_submit_verdicts_later(client: TestClient) -> None:
    body = _upload(client)
    runtime = client.app.state.mods
    with client.app.state.session_factory() as db:
        db.add(
            ModInvocation(
                event=contract.ASSET_INGESTED,
                event_id=uuid.uuid4(),
                mod_kind="python",
                mod_id="local-check",
                mod_name="ローカルのチェック",
                asset_id=uuid.UUID(body["id"]),
                outcome="ok",
                duration_ms=1,
            )
        )
        db.commit()
    sub = Subscriber(
        kind="python",
        id="local-check",
        name="ローカルのチェック",
        events=frozenset({contract.ASSET_INGESTED}),
        filters=Filters(),
        timeout_seconds=5,
        on_failure="block",
        order=0,
    )
    submit = runtime._verdict_submitter(sub)
    verdict = contract.Verdict(
        target=contract.VerdictTarget(type="asset", id=body["id"]), verdict="deny", label=_LABEL
    )
    assert asyncio.run(submit([verdict])) == 1
    assert client.get(f"/api/assets/{body['id']}").json()["content_withheld"] is True

    stranger = contract.Verdict(
        target=contract.VerdictTarget(type="asset", id=str(uuid.uuid4())), verdict="deny"
    )
    with pytest.raises(VerdictTargetError):
        asyncio.run(submit([stranger]))
