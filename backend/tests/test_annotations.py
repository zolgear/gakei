"""タイトルとタグの API、自動推定の worker、管理者設定(ADR-0024)。

FAKE プロバイダーのアプリ(`client`)では推定エンジンもダミー(`FakeEngines`)になるので、
課金もダウンロードもせずに worker まで通して確かめられる。
"""

from __future__ import annotations

import io
import json
import time
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from app.annotation.engines import FakeEngines
from tests.conftest import login_as, make_png_bytes, wait_for_run_terminal


def _upload(
    client: TestClient, color=(10, 20, 30), kind: str = "upload", data: bytes | None = None
):
    response = client.post(
        "/api/assets",
        files={"file": ("a.png", data or make_png_bytes(48, 32, color), "image/png")},
        data={"kind": kind},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _generate(client: TestClient, prompt: str, n: int = 1) -> dict:
    response = client.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": "gpt-image-2.5-sunburst",
            "prompt": prompt,
            "params": {"n": n, "size": "1024x1024"},
        },
    )
    assert response.status_code == 202, response.text
    return wait_for_run_terminal(client, response.json()["id"])


def _patch_settings(client: TestClient, **values: Any) -> dict:
    response = client.patch("/api/settings/annotation", json=values)
    assert response.status_code == 200, response.text
    return response.json()


def _wait_annotation(client: TestClient, asset_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/api/assets/{asset_id}").json()
        annotation = body.get("annotation")
        if annotation and annotation["status"] in ("succeeded", "failed"):
            return body
        time.sleep(0.05)
    raise TimeoutError(f"asset {asset_id} の推定が終わりませんでした")


def _a1111_png(prompt: str) -> bytes:
    image = Image.new("RGB", (48, 32), (120, 60, 30))
    info = PngInfo()
    info.add_text(
        "parameters",
        f"{prompt}\nNegative prompt: lowres\nSteps: 30, Sampler: Euler a, CFG scale: 7, Seed: 1",
    )
    buffer = io.BytesIO()
    image.save(buffer, "PNG", pnginfo=info)
    return buffer.getvalue()


class CountingEngines(FakeEngines):
    def __init__(self) -> None:
        self.title_calls = 0
        self.vlm_calls = 0
        self.onnx_calls = 0

    async def title_from_prompt(self, prompt, ctx):  # noqa: ANN001, ANN201
        self.title_calls += 1
        return await super().title_from_prompt(prompt, ctx)

    async def describe_image(self, image_jpeg, prompt, want_title, ctx, known_tags=None):  # noqa: ANN001, ANN201
        self.vlm_calls += 1
        return await super().describe_image(image_jpeg, prompt, want_title, ctx, known_tags)

    def onnx_tags(self, image, ctx):  # noqa: ANN001, ANN201
        self.onnx_calls += 1
        return super().onnx_tags(image, ctx)


@pytest.fixture
def counting(client: TestClient) -> CountingEngines:
    engines = CountingEngines()
    client.app.state.annotator.engines = engines
    return engines


# -- 人の編集の API ------------------------------------------------------------


def test_asset_detail_has_empty_annotation_fields(client: TestClient) -> None:
    asset = _upload(client)
    assert asset["title"] is None
    assert asset["title_source"] is None
    assert asset["tags"] == []
    assert asset["annotation"] is None


def test_edit_title_and_tags(client: TestClient) -> None:
    asset = _upload(client)
    asset_id = asset["id"]

    body = client.patch(f"/api/assets/{asset_id}/title", json={"title": " 海辺 "}).json()
    assert body["asset_id"] == asset_id
    assert (body["title"], body["title_source"]) == ("海辺", "user")

    body = client.post(f"/api/assets/{asset_id}/tags", json={"name": "Blue  Sky"}).json()
    assert body["tags"] == [{"name": "blue sky", "source": "user"}]

    detail = client.get(f"/api/assets/{asset_id}").json()
    assert detail["title"] == "海辺"
    assert detail["tags"] == [{"name": "blue sky", "source": "user"}]

    body = client.delete(f"/api/assets/{asset_id}/tags/BLUE SKY").json()
    assert body["tags"] == []
    assert client.delete(f"/api/assets/{asset_id}/tags/blue sky").status_code == 404

    body = client.patch(f"/api/assets/{asset_id}/title", json={"title": None}).json()
    assert (body["title"], body["title_source"]) == (None, "user")


def test_tag_name_with_slash_can_be_removed(client: TestClient) -> None:
    asset_id = _upload(client)["id"]
    client.post(f"/api/assets/{asset_id}/tags", json={"name": "AC/DC"})
    response = client.delete(f"/api/assets/{asset_id}/tags/ac%2Fdc")
    assert response.status_code == 200, response.text
    assert response.json()["tags"] == []


def test_edit_validation_and_targets(client: TestClient) -> None:
    asset_id = _upload(client)["id"]
    assert client.post(f"/api/assets/{asset_id}/tags", json={"name": "   "}).status_code == 422
    assert client.post(f"/api/assets/{asset_id}/tags", json={"name": "x" * 101}).status_code == 422
    assert (
        client.patch(f"/api/assets/{asset_id}/title", json={"title": "x" * 201}).status_code == 422
    )
    missing = uuid.uuid4()
    assert client.patch(f"/api/assets/{missing}/title", json={"title": "a"}).status_code == 404

    mask_id = _upload(client, color=(0, 0, 0), kind="mask")["id"]
    assert client.patch(f"/api/assets/{mask_id}/title", json={"title": "a"}).status_code == 409
    assert client.post(f"/api/assets/{mask_id}/tags", json={"name": "a"}).status_code == 409

    assert client.delete(f"/api/assets/{asset_id}").status_code == 204
    assert client.patch(f"/api/assets/{asset_id}/title", json={"title": "a"}).status_code == 409


def test_list_tags_endpoint(client: TestClient) -> None:
    a = _upload(client, color=(1, 1, 1))["id"]
    b = _upload(client, color=(2, 2, 2))["id"]
    for asset_id in (a, b):
        client.post(f"/api/assets/{asset_id}/tags", json={"name": "cat"})
    client.post(f"/api/assets/{a}/tags", json={"name": "dog"})

    body = client.get("/api/tags").json()
    assert body["items"] == [{"name": "cat", "count": 2}, {"name": "dog", "count": 1}]
    assert client.get("/api/tags", params={"q": "DO"}).json()["items"] == [
        {"name": "dog", "count": 1}
    ]
    assert len(client.get("/api/tags", params={"limit": 1}).json()["items"]) == 1


def test_stock_list_tag_filter_and_title(client: TestClient) -> None:
    a = _upload(client, color=(1, 1, 1))["id"]
    b = _upload(client, color=(2, 2, 2))["id"]
    client.post(f"/api/assets/{a}/tags", json={"name": "cat"})
    client.patch(f"/api/assets/{a}/title", json={"title": "ねこ"})
    client.post(f"/api/assets/{b}/tags", json={"name": "dog"})

    items = client.get("/api/assets", params={"tag": "Cat"}).json()["items"]
    assert [i["id"] for i in items] == [a]
    assert items[0]["title"] == "ねこ"

    all_items = client.get("/api/assets").json()["items"]
    titles = {i["id"]: i["title"] for i in all_items}
    assert titles == {a: "ねこ", b: None}

    # 消したタグでは絞り込めない。
    client.delete(f"/api/assets/{a}/tags/cat")
    assert client.get("/api/assets", params={"tag": "cat"}).json()["items"] == []


def test_search_matches_title_and_tags_and_filters_by_tag(client: TestClient) -> None:
    a = _upload(client, color=(1, 1, 1))["id"]
    b = _upload(client, color=(2, 2, 2))["id"]
    client.patch(f"/api/assets/{a}/title", json={"title": "Sunset Beach"})
    client.post(f"/api/assets/{b}/tags", json={"name": "sunset"})
    client.post(f"/api/assets/{b}/tags", json={"name": "mountain"})

    body = client.get("/api/search", params={"q": "sunset", "types": "asset"}).json()
    hits = {h["id"]: h for h in body["assets"]}
    assert set(hits) == {a, b}
    assert hits[a]["prompt_source"] == "title"
    assert hits[a]["title"] == "Sunset Beach"
    assert hits[b]["prompt_source"] == "tag"
    assert "sunset" in hits[b]["prompt_snippet"]

    # 語ごとに別の場所でもよい(タイトル + タグ)。
    client.post(f"/api/assets/{a}/tags", json={"name": "ocean"})
    body = client.get("/api/search", params={"q": "beach ocean", "types": "asset"}).json()
    assert [h["id"] for h in body["assets"]] == [a]

    body = client.get(
        "/api/search", params={"q": "sunset", "types": "asset", "tag": "mountain"}
    ).json()
    assert [h["id"] for h in body["assets"]] == [b]

    assert client.get("/api/search", params={"q": "x", "tag": "a" * 101}).status_code == 422


def test_history_outputs_include_title(client: TestClient) -> None:
    run = _generate(client, "a lighthouse")
    asset_id = run["outputs"][0]["asset_id"]
    client.patch(f"/api/assets/{asset_id}/title", json={"title": "灯台"})
    runs = client.get("/api/runs").json()["items"]
    assert runs[0]["outputs"][0]["title"] == "灯台"


# -- 設定 ----------------------------------------------------------------------


def test_settings_defaults(client: TestClient) -> None:
    body = client.get("/api/settings/annotation").json()
    assert body["auto_on_ingest"] is False
    assert body["llm_enabled"] is False
    assert body["vlm_enabled"] is False
    assert body["onnx_enabled"] is False
    assert body["llm_model"] == "gpt-5.6-luna"
    assert body["vlm_model"] == "gpt-5.6-luna"
    assert body["base_url"] is None
    assert body["api_style"] == "responses"
    assert body["language"] == "ja"
    assert body["hourly_limit"] == 100
    assert body["onnx_model"] == "wd-vit-tagger-v3"
    assert body["onnx_threshold"] == pytest.approx(0.35)
    assert body["api_key_set"] is False
    assert body["usable_engines"] == []
    assert body["calls_last_hour"] == 0
    assert body["queued_count"] == 0
    names = [m["name"] for m in body["onnx_models"]]
    assert names == ["wd-vit-tagger-v3", "wd-swinv2-tagger-v3", "wd-eva02-large-tagger-v3"]
    assert all(
        m["downloaded"] is False and m["download_status"] == "idle" for m in body["onnx_models"]
    )
    assert body["onnx_models"][0]["size_bytes"] > 300_000_000
    # メモリの目安(設定画面に出す)。eva02-large は Base の2つより大きい。
    memory = {m["name"]: m["memory_bytes"] for m in body["onnx_models"]}
    assert memory["wd-eva02-large-tagger-v3"] > memory["wd-swinv2-tagger-v3"] > 0


def test_settings_patch_and_validation(client: TestClient) -> None:
    body = _patch_settings(
        client,
        llm_enabled=True,
        llm_model=" my-model ",
        base_url="http://127.0.0.1:11434/v1/",
        api_style="chat",
        language="en",
        hourly_limit=5,
        onnx_threshold=0.5,
        onnx_model="wd-eva02-large-tagger-v3",
    )
    assert body["llm_enabled"] is True
    assert body["llm_model"] == "my-model"
    assert body["base_url"] == "http://127.0.0.1:11434/v1"
    assert body["api_style"] == "chat"
    assert body["language"] == "en"
    assert body["hourly_limit"] == 5
    assert body["onnx_threshold"] == pytest.approx(0.5)
    assert body["usable_engines"] == ["llm"]

    assert _patch_settings(client, base_url="")["base_url"] is None

    for bad in (
        {"api_style": "foo"},
        {"language": "fr"},
        {"hourly_limit": 0},
        {"onnx_threshold": 1.5},
        {"onnx_model": "other"},
        {"llm_model": ""},
        {"base_url": "ftp://x"},
        {"llm_enabled": None},
    ):
        response = client.patch("/api/settings/annotation", json=bad)
        assert response.status_code == 422, bad
    # 一部が不正なら何も保存しない。
    client.patch("/api/settings/annotation", json={"language": "ja", "hourly_limit": -1})
    assert client.get("/api/settings/annotation").json()["language"] == "en"


def test_annotation_api_key_is_stored_in_secrets_and_never_returned(
    client: TestClient, data_dir
) -> None:  # noqa: ANN001
    response = client.put("/api/settings/annotation/api-key", json={"api_key": "sk-local-123"})
    assert response.status_code == 200
    assert response.json()["api_key_set"] is True
    assert "sk-local-123" not in response.text
    secrets = json.loads((data_dir / "secrets.json").read_text())
    assert secrets["annotation_api_key"] == "sk-local-123"
    assert client.put("/api/settings/annotation/api-key", json={"api_key": " "}).status_code == 400

    response = client.delete("/api/settings/annotation/api-key")
    assert response.json()["api_key_set"] is False


def test_connection_resolution(client: TestClient, data_dir) -> None:  # noqa: ANN001
    from app.domain import annotation_settings as s
    from app.domain import api_key

    settings = client.app.state.settings
    api_key.write_file_key(data_dir, "sk-openai")

    config = s.AnnotationConfig()
    assert s.resolve_connection(config, settings) == s.Connection("sk-openai", None)

    s.write_api_key(data_dir, "sk-own")
    assert s.resolve_connection(config, settings) == s.Connection("sk-own", None)

    # 推定専用の Base URL では、推定専用キーが無ければ OpenAI のキーは送らない。
    local = s.AnnotationConfig(base_url="http://127.0.0.1:1234/v1")
    assert s.resolve_connection(local, settings).api_key == "sk-own"
    s.delete_api_key(data_dir)
    assert s.resolve_connection(local, settings) == s.Connection(
        s.PLACEHOLDER_API_KEY, "http://127.0.0.1:1234/v1"
    )


# -- worker(FAKE のエンジン) ---------------------------------------------------


def test_annotate_returns_409_without_engine(client: TestClient) -> None:
    asset_id = _upload(client)["id"]
    response = client.post(f"/api/assets/{asset_id}/annotate")
    assert response.status_code == 409
    assert client.post("/api/settings/annotation/backfill").status_code == 409


def test_auto_on_ingest_upload_without_prompt_uses_vlm_title(
    client: TestClient, counting: CountingEngines
) -> None:
    _patch_settings(
        client, auto_on_ingest=True, llm_enabled=True, vlm_enabled=True, onnx_enabled=True
    )
    uploaded = _upload(client)
    assert uploaded["annotation"]["status"] == "queued"
    body = _wait_annotation(client, uploaded["id"])
    assert body["annotation"]["status"] == "succeeded", body["annotation"]
    # プロンプトが無いので LLM は呼ばず、VLM がタイトルを付ける。
    assert counting.title_calls == 0
    assert (body["title"], body["title_source"]) == ("ダミー画像", "auto")
    names = {t["name"]: t["source"] for t in body["tags"]}
    # 既定のタグの言語は localized(ja)。ONNX の英語のタグの訳も VLM が返す。
    assert names == {
        "fake onnx": "auto",
        "訳 fake onnx": "auto",
        "fake vlm": "auto",
        "landscape": "auto",
    }


def test_auto_on_ingest_upload_with_embedded_prompt_uses_llm(
    client: TestClient, counting: CountingEngines
) -> None:
    _patch_settings(client, auto_on_ingest=True, llm_enabled=True, vlm_enabled=True)
    uploaded = _upload(client, data=_a1111_png("a red fox in snow"))
    body = _wait_annotation(client, uploaded["id"])
    assert body["title"] == "ダミー: a red fox in snow"
    assert counting.title_calls == 1
    assert counting.vlm_calls == 1


def test_auto_on_ingest_off_does_not_queue(client: TestClient) -> None:
    _patch_settings(client, llm_enabled=True)
    uploaded = _upload(client)
    assert uploaded["annotation"] is None
    assert client.get("/api/settings/annotation").json()["pending_count"] == 1


def test_mask_is_not_queued_on_ingest(client: TestClient) -> None:
    _patch_settings(client, auto_on_ingest=True, vlm_enabled=True)
    mask = _upload(client, color=(0, 0, 0), kind="mask")
    assert mask["annotation"] is None


def test_reupload_of_existing_asset_is_not_queued_again(client: TestClient) -> None:
    run = _generate(client, "a small boat")
    asset_id = run["outputs"][0]["asset_id"]
    downloaded = client.get(
        f"/api/assets/{asset_id}/content", params={"variant": "original", "download": 1}
    ).content
    _patch_settings(client, auto_on_ingest=True, llm_enabled=True)
    response = client.post(
        "/api/assets",
        files={"file": ("a.png", downloaded, "image/png")},
        data={"kind": "upload"},
    )
    assert response.json()["ingest_outcome"] == "matched_existing"
    assert response.json()["annotation"] is None


def test_llm_is_called_once_per_run(client: TestClient, counting: CountingEngines) -> None:
    _patch_settings(client, auto_on_ingest=True, llm_enabled=True)
    run = _generate(client, "two cats on a sofa", n=3)
    ids = [o["asset_id"] for o in run["outputs"]]
    bodies = [_wait_annotation(client, i) for i in ids]
    assert counting.title_calls == 1
    assert {b["title"] for b in bodies} == {"ダミー: two cats on a sofa"}
    assert all(b["title_source"] == "auto" for b in bodies)
    assert all(b["annotation"]["status"] == "succeeded" for b in bodies)


def test_hourly_limit_keeps_items_queued(client: TestClient, counting: CountingEngines) -> None:
    _patch_settings(client, auto_on_ingest=True, vlm_enabled=True, hourly_limit=1)
    first = _upload(client, color=(1, 1, 1))["id"]
    body = _wait_annotation(client, first)
    assert body["annotation"]["status"] == "succeeded"

    second = _upload(client, color=(2, 2, 2))["id"]
    time.sleep(1.5)
    body = client.get(f"/api/assets/{second}").json()
    assert body["annotation"]["status"] == "queued"
    assert counting.vlm_calls == 1
    settings_body = client.get("/api/settings/annotation").json()
    assert settings_body["calls_last_hour"] == 1
    assert settings_body["queued_count"] == 1

    # 上限を上げれば処理が進む。
    _patch_settings(client, hourly_limit=10)
    assert _wait_annotation(client, second)["annotation"]["status"] == "succeeded"


def test_reannotate_keeps_user_title_and_removed_tags(client: TestClient) -> None:
    _patch_settings(client, auto_on_ingest=True, vlm_enabled=True, onnx_enabled=True)
    asset_id = _upload(client)["id"]
    _wait_annotation(client, asset_id)

    client.patch(f"/api/assets/{asset_id}/title", json={"title": "人のタイトル"})
    client.delete(f"/api/assets/{asset_id}/tags/fake vlm")
    client.post(f"/api/assets/{asset_id}/tags", json={"name": "mine"})

    response = client.post(f"/api/assets/{asset_id}/annotate")
    assert response.status_code == 200
    assert response.json()["annotation"]["status"] == "queued"
    body = _wait_annotation(client, asset_id)
    assert (body["title"], body["title_source"]) == ("人のタイトル", "user")
    names = {t["name"]: t["source"] for t in body["tags"]}
    assert names == {
        "mine": "user",
        "fake onnx": "auto",
        "訳 fake onnx": "auto",
        "landscape": "auto",
    }


def test_backfill_queues_pending_assets(client: TestClient) -> None:
    a = _upload(client, color=(1, 1, 1))["id"]
    b = _upload(client, color=(2, 2, 2))["id"]
    _upload(client, color=(3, 3, 3), kind="mask")
    _patch_settings(client, onnx_enabled=True)
    assert client.get("/api/settings/annotation").json()["pending_count"] == 2

    response = client.post("/api/settings/annotation/backfill")
    assert response.status_code == 200
    assert response.json() == {"queued": 2}
    for asset_id in (a, b):
        body = _wait_annotation(client, asset_id)
        assert body["tags"] == [{"name": "fake onnx", "source": "auto"}]
    assert client.post("/api/settings/annotation/backfill").json() == {"queued": 0}


def test_engine_failure_is_recorded(client: TestClient) -> None:
    from app.annotation.engines import AnnotationEngineError

    class Broken(FakeEngines):
        async def describe_image(self, image_jpeg, prompt, want_title, ctx, known_tags=None):  # noqa: ANN001, ANN201
            raise AnnotationEngineError("VLM が壊れた")

    client.app.state.annotator.engines = Broken()
    _patch_settings(client, auto_on_ingest=True, vlm_enabled=True)
    asset_id = _upload(client)["id"]
    body = _wait_annotation(client, asset_id)
    assert body["annotation"]["status"] == "failed"
    assert body["annotation"]["error"] == "VLM が壊れた"
    assert body["annotation"]["finished_at"] is not None


def test_running_rows_are_requeued_on_start(tmp_path, db_session_factory) -> None:  # noqa: ANN001
    from datetime import UTC, datetime

    from app.domain.assets import ingest
    from app.domain.models import AssetAnnotation, AssetKind
    from app.domain.storage import LocalFsStore
    from app.worker.annotator import reset_running_annotations

    factory = db_session_factory
    store = LocalFsStore(tmp_path)
    with factory() as db:
        asset = ingest(db, store, make_png_bytes(), AssetKind.UPLOAD)
        db.add(
            AssetAnnotation(asset_id=asset.id, auto_status="running", updated_at=datetime.now(UTC))
        )
        db.commit()
        asset_id = asset.id
    assert reset_running_annotations(factory) == 1
    with factory() as db:
        assert db.get(AssetAnnotation, asset_id).auto_status == "queued"


# -- ONNX モデルのダウンロード(FAKE ではダウンロードしない) ---------------------------


def test_fake_onnx_download_and_delete(client: TestClient, data_dir) -> None:  # noqa: ANN001
    response = client.post(
        "/api/settings/annotation/onnx/download", json={"model": "wd-vit-tagger-v3"}
    )
    assert response.status_code == 202
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        models = client.get("/api/settings/annotation").json()["onnx_models"]
        if models[0]["downloaded"]:
            break
        time.sleep(0.05)
    assert models[0]["downloaded"] is True
    assert (data_dir / "models" / "wd" / "wd-vit-tagger-v3" / "model.onnx").is_file()

    response = client.delete("/api/settings/annotation/onnx/wd-vit-tagger-v3")
    assert response.status_code == 200
    assert response.json()["onnx_models"][0]["downloaded"] is False
    assert (
        client.post("/api/settings/annotation/onnx/download", json={"model": "nope"}).status_code
        == 404
    )


# -- 認可(oidc) ------------------------------------------------------------------


def test_oidc_user_cannot_change_annotation_settings(client_oidc: TestClient) -> None:
    login_as(client_oidc, "user@example.com")
    assert client_oidc.get("/api/settings/annotation").status_code == 200
    forbidden = [
        client_oidc.patch("/api/settings/annotation", json={"llm_enabled": True}),
        client_oidc.post("/api/settings/annotation/backfill"),
        client_oidc.post(
            "/api/settings/annotation/onnx/download", json={"model": "wd-vit-tagger-v3"}
        ),
        client_oidc.delete("/api/settings/annotation/onnx/wd-vit-tagger-v3"),
        client_oidc.put("/api/settings/annotation/api-key", json={"api_key": "x"}),
        client_oidc.delete("/api/settings/annotation/api-key"),
    ]
    assert [r.status_code for r in forbidden] == [403] * len(forbidden)

    # 一般の利用者もタイトルとタグは編集できる。
    asset = client_oidc.post(
        "/api/assets",
        files={"file": ("a.png", make_png_bytes(), "image/png")},
        data={"kind": "upload"},
    ).json()
    response = client_oidc.patch(f"/api/assets/{asset['id']}/title", json={"title": "t"})
    assert response.status_code == 200
    response = client_oidc.post(f"/api/assets/{asset['id']}/tags", json={"name": "t"})
    assert response.status_code == 200


def test_oidc_admin_can_change_annotation_settings(client_oidc: TestClient) -> None:
    login_as(client_oidc, "admin@example.com")
    response = client_oidc.patch("/api/settings/annotation", json={"llm_enabled": True})
    assert response.status_code == 200


def test_unauthenticated_cannot_read_tags(client_oidc: TestClient) -> None:
    assert client_oidc.get("/api/tags").status_code == 401
