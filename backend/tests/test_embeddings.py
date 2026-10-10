"""画像の埋め込みの待ち行列、worker、設定の API(ADR-0033 4章・5章・7章)。

FAKE プロバイダーのアプリ(`client`)では埋め込みのエンジンもダミー(`FakeEmbeddingEngine`)に
なるので、ダウンロードも推論もせずに worker まで通して確かめられる。
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import AssetEmbedding
from app.embedding.base import EmbeddingError, blob_to_vector
from app.embedding.catalog import CLIP_MODELS, XENOVA_CLIP_REVISION
from app.embedding.fake import FakeEmbeddingEngine
from tests.conftest import login_as, make_png_bytes, wait_for_run_terminal

ACTIVE_KEY = f"fake:onnx:clip-vit-b32-u8@{XENOVA_CLIP_REVISION}"


def _upload(client: TestClient, color=(10, 20, 30), kind: str = "upload") -> dict:
    response = client.post(
        "/api/assets",
        files={"file": ("a.png", make_png_bytes(48, 32, color), "image/png")},
        data={"kind": kind},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _patch(client: TestClient, **values: Any) -> dict:
    response = client.patch("/api/settings/embeddings", json=values)
    assert response.status_code == 200, response.text
    return response.json()


def _rows(client: TestClient, asset_id: str | None = None) -> list[AssetEmbedding]:
    with client.app.state.session_factory() as session:
        query = select(AssetEmbedding)
        if asset_id is not None:
            query = query.where(AssetEmbedding.asset_id == uuid.UUID(asset_id))
        return list(session.execute(query).scalars().all())


def _wait_status(
    client: TestClient, asset_id: str, statuses: tuple[str, ...] = ("succeeded", "failed")
) -> AssetEmbedding:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        rows = [r for r in _rows(client, asset_id) if r.model_key == ACTIVE_KEY]
        if rows and rows[0].status in statuses:
            return rows[0]
        time.sleep(0.05)
    raise TimeoutError(f"asset {asset_id} の埋め込みが終わりませんでした")


# -- 設定 ------------------------------------------------------------------------


def test_settings_defaults(client: TestClient) -> None:
    body = client.get("/api/settings/embeddings").json()
    assert body["enabled"] is False
    assert body["engine"] == "onnx"
    assert body["onnx_model"] == "clip-vit-b32-u8"
    assert body["auto_on_ingest"] is True
    assert body["duplicate_threshold"] == 0.90
    assert body["remote_api_format"] == "infinity"
    assert body["active_model_key"] == ACTIVE_KEY
    assert body["active_languages"] == ["en"]
    assert body["usable"] is False
    assert body["index_backend"] in ("numpy", "pgvector")
    models = {m["name"]: m for m in body["onnx_models"]}
    assert set(models) == {
        "clip-vit-b32-u8",
        "clip-vit-b32",
        "clip-japanese-base",
        "embeddinggemma-2-q8",
    }
    assert models["clip-japanese-base"]["languages"] == ["ja", "en"]
    assert models["clip-vit-b32-u8"]["size_bytes"] == 344094415
    assert all(not m["downloaded"] for m in models.values())
    assert {name: m["dim"] for name, m in models.items()} == {
        "clip-vit-b32-u8": 512,
        "clip-vit-b32": 512,
        "clip-japanese-base": 512,
        "embeddinggemma-2-q8": 768,
    }
    # 計算が重いモデルの印と、モデルごとのしきい値(ADR-0044 5章・6章)。
    assert [name for name, m in models.items() if m["heavy"]] == ["embeddinggemma-2-q8"]
    assert body["duplicate_threshold_default"] == 0.90
    for name, m in models.items():
        assert m["duplicate_threshold"] == m["duplicate_threshold_default"]
        assert m["duplicate_threshold_default"] == CLIP_MODELS[name].duplicate_threshold
    assert body["stored"] == []
    assert (body["pending_count"], body["queued_count"], body["failed_count"]) == (0, 0, 0)


def test_settings_patch_validation(client: TestClient) -> None:
    body = _patch(client, enabled=True, duplicate_threshold=0.9, onnx_model="clip-japanese-base")
    assert body["enabled"] is True
    assert body["onnx_model"] == "clip-japanese-base"
    assert body["active_languages"] == ["ja", "en"]
    # FAKE ではダウンロードしなくても使える。
    assert body["usable"] is True
    bad = [
        {"enabled": "maybe"},
        {"enabled": None},
        {"engine": "gpu"},
        {"onnx_model": "nope"},
        {"duplicate_threshold": 1.5},
        {"remote_api_format": "vllm"},
        {"remote_model": ""},
        {"remote_model": "x" * 151},
        {"remote_connection_id": "missing"},
        # 組み込みの接続先(OpenAI)は選べない。
        {"remote_connection_id": "openai"},
        # 1つでも不正なら何も保存しない。
        {"auto_on_ingest": False, "engine": "gpu"},
    ]
    for values in bad:
        response = client.patch("/api/settings/embeddings", json=values)
        assert response.status_code == 422, (values, response.text)
    assert client.get("/api/settings/embeddings").json()["auto_on_ingest"] is True


def _add_connection(client: TestClient, name: str = "infinity") -> str:
    response = client.post(
        "/api/settings/llm-connections",
        json={"name": name, "base_url": "http://127.0.0.1:7997", "api_style": "chat"},
    )
    assert response.status_code in (200, 201), response.text
    connections = response.json()["connections"]
    return next(c["id"] for c in connections if c["name"] == name)


def test_remote_settings_and_connection_in_use(client: TestClient) -> None:
    connection_id = _add_connection(client)
    body = _patch(client, engine="remote", remote_connection_id=connection_id)
    # モデル名が未設定なら使うモデルは無い。
    assert body["active_model_key"] is None
    assert body["active_languages"] is None
    body = _patch(client, remote_model="jinaai/jina-clip-v2", enabled=True)
    assert body["active_model_key"] == f"fake:remote:{connection_id}:jinaai/jina-clip-v2"
    assert body["usable"] is True

    connections = client.get("/api/settings/llm-connections").json()["connections"]
    used = next(c for c in connections if c["id"] == connection_id)["used_by"]
    assert used == ["embedding"]
    response = client.delete(f"/api/settings/llm-connections/{connection_id}")
    assert response.status_code == 409
    assert "埋め込み" in response.json()["detail"]

    # 未設定に戻せば削除できる。
    _patch(client, remote_connection_id=None)
    assert client.delete(f"/api/settings/llm-connections/{connection_id}").status_code == 200


# -- 取り込み時の自動実行と worker ----------------------------------------------------


def test_disabled_does_not_queue(client: TestClient) -> None:
    asset = _upload(client)
    assert _rows(client, asset["id"]) == []
    # 無効でも、使うモデルのベクトルが無い件数は数える(有効にする前に画面で示せるように)。
    assert client.get("/api/settings/embeddings").json()["pending_count"] == 1
    _patch(client, enabled=True, auto_on_ingest=False)
    _upload(client, color=(1, 2, 3))
    assert _rows(client) == []
    assert client.get("/api/settings/embeddings").json()["pending_count"] == 2


def test_upload_is_embedded_by_worker(client: TestClient) -> None:
    _patch(client, enabled=True)
    asset = _upload(client)
    row = _wait_status(client, asset["id"])
    assert row.status == "succeeded", row.error
    assert row.dim == 512
    vector = blob_to_vector(row.vector)
    assert vector.shape == (512,)
    assert float(np.linalg.norm(vector)) == pytest.approx(1.0, abs=1e-5)
    assert client.app.state.embedder.version(ACTIVE_KEY) >= 1
    body = client.get("/api/settings/embeddings").json()
    assert body["stored"] == [{"model_key": ACTIVE_KEY, "count": 1, "dim": 512, "active": True}]
    assert body["pending_count"] == 0


def test_generated_outputs_and_sketches_are_queued_but_not_masks(client: TestClient) -> None:
    _patch(client, enabled=True)
    response = client.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": "gpt-image-2.5-sunburst",
            "prompt": "a boat",
            "params": {"n": 2, "size": "1024x1024"},
        },
    )
    assert response.status_code == 202, response.text
    run = wait_for_run_terminal(client, response.json()["id"])
    for output in run["outputs"]:
        assert _wait_status(client, output["asset_id"]).status == "succeeded"
    sketch = _upload(client, color=(9, 9, 9), kind="sketch")
    assert _wait_status(client, sketch["id"]).status == "succeeded"
    mask = _upload(client, color=(0, 0, 0), kind="mask")
    assert _rows(client, mask["id"]) == []


def test_reupload_of_existing_asset_is_not_queued_again(client: TestClient) -> None:
    response = client.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": "gpt-image-2.5-sunburst",
            "prompt": "a small boat",
            "params": {"n": 1, "size": "1024x1024"},
        },
    )
    run = wait_for_run_terminal(client, response.json()["id"])
    asset_id = run["outputs"][0]["asset_id"]
    downloaded = client.get(
        f"/api/assets/{asset_id}/content", params={"variant": "original", "download": 1}
    ).content
    _patch(client, enabled=True)
    response = client.post(
        "/api/assets",
        files={"file": ("a.png", downloaded, "image/png")},
        data={"kind": "upload"},
    )
    assert response.json()["ingest_outcome"] == "matched_existing"
    assert _rows(client) == []


def test_batch_one_engine_computes_one_image_at_a_time(client: TestClient) -> None:
    """量子化したモデル(既定)は1枚ずつ計算する。"""
    _patch(client, enabled=True, auto_on_ingest=False)
    for i in range(3):
        _upload(client, color=(i * 50, 0, 0))
    assert client.post("/api/settings/embeddings/backfill").json() == {"queued": 3}
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if all(r.status == "succeeded" for r in _rows(client)) and len(_rows(client)) == 3:
            break
        time.sleep(0.05)
    engine = client.app.state.embedder.engines._fakes[ACTIVE_KEY]
    assert engine.image_calls and set(engine.image_calls) == {1}


def test_backfill_counts_and_failed_rows(client: TestClient) -> None:
    _patch(client, enabled=True, auto_on_ingest=False)
    ids = [_upload(client, color=(i, i, i))["id"] for i in range(3)]
    _upload(client, color=(0, 0, 0), kind="mask")
    deleted = _upload(client, color=(5, 6, 7))["id"]
    assert client.delete(f"/api/assets/{deleted}").status_code in (200, 204)
    assert client.get("/api/settings/embeddings").json()["pending_count"] == 3

    class Failing(FakeEmbeddingEngine):
        def embed_images(self, images):  # noqa: ANN001, ANN201
            raise EmbeddingError("サーバーが落ちています")

    embedder = client.app.state.embedder
    original = embedder.engines._fake
    embedder.engines._fake = lambda key, config: Failing(key)
    assert client.post("/api/settings/embeddings/backfill").json() == {"queued": 3}
    for asset_id in ids:
        row = _wait_status(client, asset_id)
        assert row.status == "failed"
        assert row.error == "サーバーが落ちています"
    body = client.get("/api/settings/embeddings").json()
    assert body["failed_count"] == 3
    # 失敗したものも一括実行の対象(ベクトルが無い)。
    assert body["pending_count"] == 3

    embedder.engines._fake = original
    assert client.post("/api/settings/embeddings/backfill").json() == {"queued": 3}
    for asset_id in ids:
        assert _wait_status(client, asset_id).status == "succeeded"
    assert client.post("/api/settings/embeddings/backfill").json() == {"queued": 0}


def test_backfill_requires_usable(client: TestClient) -> None:
    assert client.post("/api/settings/embeddings/backfill").status_code == 409


def test_inactive_model_rows_are_not_picked(client: TestClient) -> None:
    _patch(client, enabled=True, auto_on_ingest=False)
    asset = _upload(client)
    other_key = "fake:onnx:clip-japanese-base@x"
    with client.app.state.session_factory() as session:
        from datetime import UTC, datetime

        session.add(
            AssetEmbedding(
                asset_id=uuid.UUID(asset["id"]),
                model_key=other_key,
                status="queued",
                updated_at=datetime.now(UTC),
            )
        )
        session.commit()
    client.app.state.embedder.notify()
    time.sleep(1.5)
    rows = {r.model_key: r.status for r in _rows(client, asset["id"])}
    assert rows == {other_key: "queued"}


def test_request_single_embedding(client: TestClient) -> None:
    asset = _upload(client)
    assert client.post(f"/api/assets/{asset['id']}/embedding").status_code == 409
    _patch(client, enabled=True, auto_on_ingest=False)
    response = client.post(f"/api/assets/{asset['id']}/embedding")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["model_key"] == ACTIVE_KEY
    assert body["status"] in ("queued", "running", "succeeded")
    assert _wait_status(client, asset["id"]).status == "succeeded"
    mask = _upload(client, color=(0, 0, 0), kind="mask")
    assert client.post(f"/api/assets/{mask['id']}/embedding").status_code == 409
    assert client.post(f"/api/assets/{uuid.uuid4()}/embedding").status_code == 404


def test_delete_vectors(client: TestClient) -> None:
    _patch(client, enabled=True)
    asset = _upload(client)
    _wait_status(client, asset["id"])
    version = client.app.state.embedder.version(ACTIVE_KEY)
    response = client.delete(f"/api/settings/embeddings/vectors/{ACTIVE_KEY}")
    assert response.status_code == 200, response.text
    assert response.json()["stored"] == []
    assert client.app.state.embedder.version(ACTIVE_KEY) == version + 1
    assert _rows(client) == []
    assert client.delete(f"/api/settings/embeddings/vectors/{ACTIVE_KEY}").status_code == 404
    # model_key に `/` を含むもの(リモートのモデル名)も指定できる。
    assert client.delete("/api/settings/embeddings/vectors/remote:abc:org/model").status_code == 404


def test_running_rows_are_requeued_on_start(tmp_path, db_session_factory) -> None:  # noqa: ANN001
    from datetime import UTC, datetime

    from app.domain.assets import ingest
    from app.domain.models import AssetKind
    from app.domain.storage import LocalFsStore
    from app.worker.embedder import claim, reset_running_embeddings

    factory = db_session_factory
    store = LocalFsStore(tmp_path)
    with factory() as db:
        asset = ingest(db, store, make_png_bytes(), AssetKind.UPLOAD)
        db.add(
            AssetEmbedding(
                asset_id=asset.id, model_key="k", status="running", updated_at=datetime.now(UTC)
            )
        )
        db.commit()
        asset_id = asset.id
    assert reset_running_embeddings(factory) == 1
    with factory() as db:
        assert db.get(AssetEmbedding, (asset_id, "k")).status == "queued"
    assert claim(factory, "other") == []
    assert claim(factory, "k") == [asset_id]
    assert claim(factory, "k") == []


def test_fake_download_and_delete_of_embedding_model(client: TestClient, data_dir) -> None:  # noqa: ANN001
    response = client.post(
        "/api/settings/embeddings/onnx/download", json={"model": "clip-japanese-base"}
    )
    assert response.status_code == 202
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        models = {
            m["name"]: m for m in client.get("/api/settings/embeddings").json()["onnx_models"]
        }
        if models["clip-japanese-base"]["downloaded"]:
            break
        time.sleep(0.05)
    assert models["clip-japanese-base"]["downloaded"] is True
    directory = data_dir / "models" / "clip" / "clip-japanese-base"
    assert (directory / "clyp_visual.onnx").is_file()
    assert (directory / "spiece.model").is_file()
    # WD Tagger の置き場所とは別。
    assert not (data_dir / "models" / "wd").exists()

    response = client.delete("/api/settings/embeddings/onnx/clip-japanese-base")
    assert response.status_code == 200
    assert not directory.exists()
    assert (
        client.post("/api/settings/embeddings/onnx/download", json={"model": "nope"}).status_code
        == 404
    )
    assert client.delete("/api/settings/embeddings/onnx/nope").status_code == 404


# -- 認可(oidc) ------------------------------------------------------------------


def test_oidc_user_can_read_but_not_change(client_oidc: TestClient) -> None:
    login_as(client_oidc, "user@example.com")
    assert client_oidc.get("/api/settings/embeddings").status_code == 200
    forbidden = [
        client_oidc.patch("/api/settings/embeddings", json={"enabled": True}),
        client_oidc.post("/api/settings/embeddings/backfill"),
        client_oidc.post(
            "/api/settings/embeddings/onnx/download", json={"model": "clip-vit-b32-u8"}
        ),
        client_oidc.delete("/api/settings/embeddings/onnx/clip-vit-b32-u8"),
        client_oidc.delete(f"/api/settings/embeddings/vectors/{ACTIVE_KEY}"),
    ]
    assert [r.status_code for r in forbidden] == [403] * len(forbidden)


def test_oidc_single_embedding_respects_visibility(client_oidc: TestClient) -> None:
    login_as(client_oidc, "admin@example.com")
    assert client_oidc.patch("/api/settings/embeddings", json={"enabled": True}).status_code == 200
    mine = client_oidc.post(
        "/api/assets",
        files={"file": ("a.png", make_png_bytes(), "image/png")},
        data={"kind": "upload"},
    ).json()
    assert client_oidc.post(f"/api/assets/{mine['id']}/embedding").status_code == 200

    client_oidc.cookies.clear()
    login_as(client_oidc, "user@example.com")
    # 他人の Asset は見えないので 404。
    assert client_oidc.post(f"/api/assets/{mine['id']}/embedding").status_code == 404
    own = client_oidc.post(
        "/api/assets",
        files={"file": ("b.png", make_png_bytes(color=(1, 200, 3)), "image/png")},
        data={"kind": "upload"},
    ).json()
    assert client_oidc.post(f"/api/assets/{own['id']}/embedding").status_code == 200


# -- MCP と URL でのアップロードも待ち行列に入れる -------------------------------------


def test_mcp_upload_and_upload_url_are_queued(client: TestClient) -> None:
    from tests.test_mcp import _call, _enable, _ok, _upload_b64

    _enable(client)
    _patch(client, enabled=True)
    uploaded = _ok(_call(client, "upload_image", {"data_base64": _upload_b64((7, 8, 9))}))
    assert _wait_status(client, uploaded["asset_id"]).status == "succeeded"

    issued = _ok(_call(client, "create_upload_url", {}))
    path = issued["upload_url"].removeprefix("http://testserver")
    response = client.put(path, content=make_png_bytes(40, 40, (90, 10, 200)))
    assert response.status_code == 201, response.text
    assert _wait_status(client, response.json()["asset_id"]).status == "succeeded"


def test_upload_url_also_queues_annotation(client: TestClient) -> None:
    """URL でのアップロードも、ほかの取り込みと同じく自動タイトル・タグの待ち行列に入れる。"""
    from tests.test_mcp import _call, _enable, _ok

    _enable(client)
    response = client.patch(
        "/api/settings/annotation", json={"auto_on_ingest": True, "vlm_enabled": True}
    )
    assert response.status_code == 200
    issued = _ok(_call(client, "create_upload_url", {}))
    path = issued["upload_url"].removeprefix("http://testserver")
    asset_id = client.put(path, content=make_png_bytes(40, 40, (1, 99, 2))).json()["asset_id"]
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        annotation = client.get(f"/api/assets/{asset_id}").json()["annotation"]
        if annotation and annotation["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.05)
    assert annotation["status"] == "succeeded"


# -- 本物の設定(FAKE ではない)で、リモートのエンジンを worker から通す ----------------------


def test_worker_with_remote_engine_end_to_end(tmp_path, db_session_factory) -> None:  # noqa: ANN001
    import asyncio
    import json

    import httpx

    from app.config import Settings
    from app.domain import embedding_settings, llm_connections
    from app.domain import embeddings as embeddings_domain
    from app.domain.assets import ingest
    from app.domain.models import AssetKind
    from app.domain.storage import LocalFsStore
    from app.embedding.engines import EmbeddingEngines
    from app.worker.embedder import Embedder

    settings = Settings(_env_file=None, data_dir=tmp_path / "data", fake_provider=False)
    store = LocalFsStore(tmp_path / "store")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = json.loads(request.content)
        data = [{"index": i, "embedding": [1.0, 2.0, 2.0]} for i in range(len(body["input"]))]
        return httpx.Response(200, json={"data": data})

    factory = db_session_factory
    with factory() as db:
        connection_id = llm_connections.add_connection(
            db, "infinity", "http://infer.local:7997", "chat"
        )
        llm_connections.write_connection_key(settings.data_dir, connection_id, "sk-local")
        embedding_settings.save(
            db,
            {
                "enabled": True,
                "engine": "remote",
                "remote_connection_id": connection_id,
                "remote_model": "clip",
            },
        )
        config = embedding_settings.load(db)
        assert embedding_settings.usable(config, settings)
        model_key = embedding_settings.active_model_key(config, settings)
        assert model_key == f"remote:{connection_id}:clip"
        assets = [
            ingest(db, store, make_png_bytes(color=(i * 60, 0, 0)), AssetKind.UPLOAD)
            for i in range(3)
        ]
        for asset in assets:
            assert embeddings_domain.enqueue_on_ingest(db, asset, settings)
        db.commit()

    engines = EmbeddingEngines(
        settings,
        remote_client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    embedder = Embedder(factory, store, settings, engines)
    job = embedder._prepare()
    assert job is not None and len(job.items) == 3
    asyncio.run(embedder._process(job))
    assert embedder._prepare() is None

    # 3枚をまとめて1回で送る(上限 32)。キーは Bearer で送る。
    assert len(requests) == 1
    assert requests[0].headers["authorization"] == "Bearer sk-local"
    with factory() as db:
        rows = db.execute(select(AssetEmbedding)).scalars().all()
        assert {r.status for r in rows} == {"succeeded"}
        assert {r.dim for r in rows} == {3}
        assert blob_to_vector(rows[0].vector).tolist() == pytest.approx([1 / 3, 2 / 3, 2 / 3])
    assert embedder.version(model_key) == 1


# -- 取る件数と先読み(Issue #71) --------------------------------------------------------


class _StubEngines:
    """`engine_for` で決まったエンジンを返す(worker のテスト用)。"""

    def __init__(self, engine: Any) -> None:
        self.engine = engine

    def engine_for(self, db: Any, config: Any) -> Any:  # noqa: ARG002
        return self.engine

    def release_idle(self) -> None:
        return None


def _remote_worker_setup(tmp_path, factory, count: int):  # noqa: ANN001, ANN202
    """リモートを使う設定にし、`count` 枚を待ち行列に入れる。(settings, store, model_key)"""
    from app.config import Settings
    from app.domain import embedding_settings, llm_connections
    from app.domain import embeddings as embeddings_domain
    from app.domain.assets import ingest
    from app.domain.models import AssetKind
    from app.domain.storage import LocalFsStore

    settings = Settings(_env_file=None, data_dir=tmp_path / "data", fake_provider=False)
    store = LocalFsStore(tmp_path / "store")
    with factory() as db:
        connection_id = llm_connections.add_connection(
            db, "infinity", "http://infer.local:7997", "chat"
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
        config = embedding_settings.load(db)
        model_key = embedding_settings.active_model_key(config, settings)
        for i in range(count):
            asset = ingest(
                db, store, make_png_bytes(color=(i % 256, i // 256, 7)), AssetKind.UPLOAD
            )
            assert embeddings_domain.enqueue_on_ingest(db, asset, settings)
        db.commit()
    return settings, store, model_key


def _status_counts(factory) -> dict[str, int]:  # noqa: ANN001
    counts: dict[str, int] = {}
    with factory() as db:
        for row in db.execute(select(AssetEmbedding)).scalars():
            counts[row.status] = counts.get(row.status, 0) + 1
    return counts


@pytest.mark.parametrize(("batch", "expected"), [(32, 32), (8, 8), (1, 8)])
def test_worker_claims_up_to_engine_batch_size(
    tmp_path,
    db_session_factory,
    batch: int,
    expected: int,  # noqa: ANN001
) -> None:
    """1回に取る件数は 8 とエンジンのまとめる枚数の大きいほう(リモートは 32、ONNX は 8)。"""
    import asyncio

    from app.worker.embedder import Embedder

    factory = db_session_factory
    settings, store, model_key = _remote_worker_setup(tmp_path, factory, 40)
    engine = FakeEmbeddingEngine(model_key, image_batch_size=batch)
    embedder = Embedder(factory, store, settings, _StubEngines(engine))
    job = embedder._prepare()
    assert job is not None
    assert len(job.claimed) == expected and len(job.items) == expected
    assert not job.failures
    assert _status_counts(factory) == {"running": expected, "queued": 40 - expected}
    asyncio.run(embedder._process(job))
    assert _status_counts(factory) == {"succeeded": expected, "queued": 40 - expected}
    if batch == 32:
        assert engine.image_calls == [32]


def test_worker_claims_default_limit_when_engine_is_unavailable(
    tmp_path,
    db_session_factory,  # noqa: ANN001
) -> None:
    """エンジンを用意できない(接続先が消えたなど)ときは 8 件を取って失敗にする。"""
    from app.worker.embedder import Embedder

    class _Broken(_StubEngines):
        def engine_for(self, db: Any, config: Any) -> Any:  # noqa: ARG002
            raise EmbeddingError("connection missing")

    factory = db_session_factory
    settings, store, _ = _remote_worker_setup(tmp_path, factory, 12)
    embedder = Embedder(factory, store, settings, _Broken(None))
    job = embedder._prepare()
    assert job is not None and job.engine is None
    assert len(job.failures) == 8 and not job.items


def test_worker_requeues_prefetched_job_on_stop(tmp_path, db_session_factory) -> None:  # noqa: ANN001
    """計算中に次の分を先に取る。止めたら、先に取ってまだ計算していない行は queued に戻す。"""
    import asyncio
    import threading

    from app.worker.embedder import Embedder

    class _BlockingEngine(FakeEmbeddingEngine):
        def __init__(self, model_key: str) -> None:
            super().__init__(model_key, image_batch_size=8)
            self.started = threading.Event()
            self.release = threading.Event()

        def embed_images(self, images, *, priority: bool = False):  # noqa: ANN001, ANN201
            self.started.set()
            assert self.release.wait(10)
            return super().embed_images(images, priority=priority)

    factory = db_session_factory
    settings, store, model_key = _remote_worker_setup(tmp_path, factory, 24)
    engine = _BlockingEngine(model_key)
    embedder = Embedder(factory, store, settings, _StubEngines(engine))

    async def main() -> None:
        await embedder.start()
        deadline = time.monotonic() + 10
        # 1つ目(8件)を計算している間に、次の8件を先に取る。
        while time.monotonic() < deadline:
            if engine.started.is_set() and _status_counts(factory).get("running") == 16:
                break
            await asyncio.sleep(0.02)
        else:
            raise TimeoutError("先読みが始まりませんでした")
        stopping = asyncio.create_task(embedder.stop())
        await asyncio.sleep(0.05)
        engine.release.set()
        await stopping

    asyncio.run(main())
    assert engine.image_calls == [8]
    assert _status_counts(factory) == {"succeeded": 8, "queued": 16}


# -- モデルごとの重複のしきい値と、入力の派生画像(ADR-0044) ------------------------------

EG2_NAME = "embeddinggemma-2-q8"


def test_duplicate_threshold_is_per_model(client: TestClient) -> None:
    eg2_default = CLIP_MODELS[EG2_NAME].duplicate_threshold
    body = _patch(client, duplicate_threshold=0.95)
    assert body["duplicate_threshold"] == 0.95
    assert body["duplicate_threshold_default"] == 0.90
    # モデルを変えると、そのモデルの値(保存していなければ既定)になる。
    body = _patch(client, onnx_model=EG2_NAME)
    assert body["duplicate_threshold"] == eg2_default
    assert body["duplicate_threshold_default"] == eg2_default
    models = {m["name"]: m for m in body["onnx_models"]}
    assert models["clip-vit-b32-u8"]["duplicate_threshold"] == 0.95
    assert models[EG2_NAME]["duplicate_threshold"] == eg2_default
    # モデルとしきい値を一緒に変えると、変えた後のモデルの値になる。
    body = _patch(client, onnx_model="clip-japanese-base", duplicate_threshold=0.8)
    assert body["duplicate_threshold"] == 0.8
    body = _patch(client, onnx_model="clip-vit-b32-u8")
    assert body["duplicate_threshold"] == 0.95
    models = {m["name"]: m for m in body["onnx_models"]}
    assert models["clip-japanese-base"]["duplicate_threshold"] == 0.8
    # 重複の候補の既定のしきい値も、使うモデルの値。
    _patch(client, enabled=True)
    assert client.get("/api/embeddings/duplicates").json()["threshold"] == 0.95


def test_legacy_duplicate_threshold_moves_to_the_model_in_use(client: TestClient) -> None:
    """モデルごとにする前の値は、そのとき使っていたモデルの値として引き継ぐ。"""
    from app.domain.general_settings import _get_raw_value, _save

    _patch(client, onnx_model="clip-japanese-base")
    with client.app.state.session_factory() as db:
        _save(db, "embedding.duplicate_threshold", 0.85)
    assert client.get("/api/settings/embeddings").json()["duplicate_threshold"] == 0.85
    # モデルを変えると、古い値は変える前のモデルに移り、古い項目は消える。
    body = _patch(client, onnx_model=EG2_NAME)
    assert body["duplicate_threshold"] == CLIP_MODELS[EG2_NAME].duplicate_threshold
    with client.app.state.session_factory() as db:
        assert _get_raw_value(db, "embedding.duplicate_threshold") is None
        assert _get_raw_value(db, "embedding.duplicate_thresholds") == {
            "onnx:clip-japanese-base": 0.85
        }
    assert _patch(client, onnx_model="clip-japanese-base")["duplicate_threshold"] == 0.85


def test_duplicate_threshold_for_remote_needs_a_model(client: TestClient) -> None:
    _patch(client, engine="remote")
    response = client.patch("/api/settings/embeddings", json={"duplicate_threshold": 0.9})
    assert response.status_code == 422
    connection_id = _add_connection(client)
    body = _patch(
        client, remote_connection_id=connection_id, remote_model="clip", duplicate_threshold=0.93
    )
    assert body["duplicate_threshold"] == 0.93
    assert body["duplicate_threshold_default"] == 0.90
    assert _patch(client, remote_model="other")["duplicate_threshold"] == 0.90
    assert _patch(client, remote_model="clip")["duplicate_threshold"] == 0.93


def test_worker_reads_preview_for_eg2(
    tmp_path,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
) -> None:
    """EG2 は preview から計算し、知覚ハッシュは thumb から作る。取るのは 2 件ずつ。"""
    import asyncio

    from app.config import Settings
    from app.domain import derivatives, embedding_settings
    from app.domain import embeddings as embeddings_domain
    from app.domain.assets import ingest
    from app.domain.models import AssetKind, AssetPerceptualHash
    from app.domain.storage import LocalFsStore
    from app.worker.embedder import PREVIEW_CLAIM_LIMIT, Embedder

    factory = db_session_factory
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", fake_provider=True)
    store = LocalFsStore(tmp_path / "store")
    with factory() as db:
        embedding_settings.save(db, {"enabled": True, "onnx_model": EG2_NAME})
        config = embedding_settings.load(db)
        assert embedding_settings.input_variant(config) == "preview"
        model_key = embedding_settings.active_model_key(config, settings)
        for i in range(5):
            asset = ingest(db, store, make_png_bytes(color=(i, 9, 7)), AssetKind.UPLOAD)
            assert embeddings_domain.enqueue_on_ingest(db, asset, settings)
        db.commit()

    variants: list[str] = []
    original = derivatives.ensure_derived

    def recording(store_, blob_key, sha256, variant):  # noqa: ANN001, ANN202
        variants.append(variant)
        return original(store_, blob_key, sha256, variant)

    monkeypatch.setattr(derivatives, "ensure_derived", recording)
    engine = FakeEmbeddingEngine(model_key, image_batch_size=1)
    embedder = Embedder(factory, store, settings, _StubEngines(engine))
    job = embedder._prepare()
    assert job is not None and job.variant == "preview"
    assert len(job.claimed) == PREVIEW_CLAIM_LIMIT == 2
    assert sorted(variants) == ["preview", "preview", "thumb", "thumb"]
    asyncio.run(embedder._process(job))
    assert engine.image_calls == [1, 1]
    with factory() as db:
        assert len(db.execute(select(AssetPerceptualHash)).scalars().all()) == 2


def test_query_image_uses_the_same_derived_size() -> None:
    from PIL import Image

    from app.domain.semantic_search import decode_query_image

    data = make_png_bytes(3000, 1500, (10, 20, 30))
    assert decode_query_image(data).size == (512, 256)
    assert decode_query_image(data, "preview").size == (2048, 1024)
    assert isinstance(decode_query_image(data, "preview"), Image.Image)
