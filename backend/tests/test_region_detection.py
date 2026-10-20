"""ADR-0053 7章: VLM での四角の検出。

設定(検証、使っている接続先の登録)、検出の API(権限、見える範囲、無効時、上限、Mod の
`ai.before_send` の目的 `region_detection`)、応答の読み取り(範囲外・空の四角を捨てる、重複、
上限)、Fake の検出、画像を添えた構造化出力の要求(`detail: high`)を確かめる。実 API は呼ばない
(VLM は `FakeStructuredEngine` と、SDK のクライアントの差し替え)。
"""

from __future__ import annotations

import asyncio
import io
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.assistant.engine import AssistantEngineError
from app.assistant.structured import (
    FakeStructuredEngine,
    OpenAIStructuredEngine,
    StructuredImage,
    StructuredRequest,
)
from app.domain import llm_connections
from app.domain import region_detection as detection
from app.domain import region_detection_settings as detection_settings
from app.domain.llm_connections import LlmTarget
from app.domain.models import ModVerdict
from app.i18n import use_locale
from tests.conftest import login_as, make_png_bytes
from tests.test_mods_hooks import _Hook, _install

SETTINGS = "/api/settings/region-detection"
CONNECTIONS = "/api/settings/llm-connections"


def _detect_url(asset_id: str) -> str:
    return f"/api/assets/{asset_id}/regions/detect"


def _upload(client: TestClient, kind: str = "upload", size: tuple[int, int] = (96, 64)) -> str:
    response = client.post(
        "/api/assets",
        files={"file": ("a.png", make_png_bytes(*size), "image/png")},
        data={"kind": kind},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _enable(client: TestClient, **extra: Any) -> None:
    response = client.patch(SETTINGS, json={"enabled": True, **extra})
    assert response.status_code == 200, response.text


def _engine(client: TestClient) -> FakeStructuredEngine:
    engine = client.app.state.assistant.structured
    assert isinstance(engine, FakeStructuredEngine)
    return engine


# -- 応答の読み取り --------------------------------------------------------------------


def test_interpret_converts_to_ratios() -> None:
    boxes = detection.interpret({"objects": [{"label": " cup ", "box": [100, 200, 500, 1000]}]}, 20)
    assert len(boxes) == 1
    assert boxes[0].label == "cup"
    assert boxes[0].box == pytest.approx((0.1, 0.2, 0.5, 1.0))


def test_interpret_drops_invalid_and_duplicates() -> None:
    data = {
        "objects": [
            {"label": "ok", "box": [0, 0, 1000, 1000]},
            {"label": "ok", "box": [0, 0, 1000, 1000]},  # 同じ名前と四角
            {"label": "other", "box": [0, 0, 1000, 1000]},  # 名前が違えば残す
            {"label": "out", "box": [-1, 0, 10, 10]},  # 範囲外
            {"label": "out2", "box": [0, 0, 1001, 10]},
            {"label": "empty", "box": [10, 10, 10, 50]},  # 幅が 0
            {"label": "reversed", "box": [50, 50, 10, 10]},
            {"label": "short", "box": [1, 2, 3]},
            {"label": "bool", "box": [True, 0, 10, 10]},
            {"label": "text", "box": ["1", 0, 10, 10]},
            {"label": "", "box": [0, 0, 10, 10]},
            {"label": 3, "box": [0, 0, 10, 10]},
            "not-an-object",
            {"label": "float", "box": [10.4, 20.6, 30.0, 40.0]},
        ]
    }
    boxes = detection.interpret(data, 20)
    assert [b.label for b in boxes] == ["ok", "other", "float"]
    assert boxes[2].box == pytest.approx((0.01, 0.021, 0.03, 0.04))


def test_interpret_truncates_to_max_items_and_bad_shapes() -> None:
    data = {"objects": [{"label": f"o{i}", "box": [i, 0, i + 10, 10]} for i in range(30)]}
    assert len(detection.interpret(data, 20)) == 20
    assert len(detection.interpret(data, 3)) == 3
    assert detection.interpret({}, 20) == []
    assert detection.interpret({"objects": "x"}, 20) == []
    assert detection.interpret(None, 20) == []


def test_instructions_include_language_limit_and_query() -> None:
    text = detection.instructions("en", query=None, max_items=7)
    assert "英語" in text and "7" in text and "0〜1000" in text
    assert "探している物だけ" not in text
    text = detection.instructions("ja", query="cup", max_items=20)
    assert "日本語" in text and "探している物だけ" in text
    assert detection.build_input("cup").endswith("cup")


def test_fake_response() -> None:
    data = detection.fake_response("ja", query=None, max_items=20)
    assert len(data["objects"]) == 3
    assert len(detection.interpret(data, 20)) == 3
    data = detection.fake_response("en", query="red balloon", max_items=1)
    assert [o["label"] for o in data["objects"]] == ["red balloon"]


def test_prepare_image_downscales_to_jpeg() -> None:
    jpeg = detection.prepare_image(make_png_bytes(3000, 1500))
    with Image.open(io.BytesIO(jpeg)) as image:
        assert image.format == "JPEG"
        assert image.size == (detection.IMAGE_MAX_SIDE, detection.IMAGE_MAX_SIDE // 2)


# -- エンジン(SDK のクライアントを差し替え) --------------------------------------------


class _Stub:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.response


def _request(api_style: str = "responses", images: tuple[StructuredImage, ...] = ()) -> Any:
    return StructuredRequest(
        target=LlmTarget(
            connection_id="openai",
            model="test-model",
            api_key="k",
            base_url=None,
            api_style=api_style,  # type: ignore[arg-type]
        ),
        instructions="i",
        input_text="find",
        schema_name=detection.SCHEMA_NAME,
        schema=detection.RESPONSE_SCHEMA,
        images=images,
    )


def test_engine_responses_sends_image_with_detail_high() -> None:
    output = SimpleNamespace(
        type="message", content=[SimpleNamespace(type="output_text", text='{"objects": []}')]
    )
    stub = _Stub(SimpleNamespace(output=[output], error=None, model="m", usage=None))
    engine = OpenAIStructuredEngine()
    engine._cache.get = lambda target: SimpleNamespace(responses=stub)  # type: ignore[method-assign]
    image = StructuredImage(data=b"\xff\xd8jpeg", detail="high")
    result = asyncio.run(engine.complete(_request(images=(image,))))
    assert result.data == {"objects": []}
    call = stub.calls[0]
    assert call["input"] == [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "find"},
                {"type": "input_image", "image_url": image.data_url(), "detail": "high"},
            ],
        }
    ]
    assert image.data_url().startswith("data:image/jpeg;base64,")
    fmt = call["text"]["format"]
    assert fmt["name"] == detection.SCHEMA_NAME and fmt["strict"] is True
    assert fmt["schema"] == detection.RESPONSE_SCHEMA
    assert call["store"] is False


def test_engine_responses_without_images_keeps_plain_text() -> None:
    output = SimpleNamespace(
        type="message", content=[SimpleNamespace(type="output_text", text="{}")]
    )
    stub = _Stub(SimpleNamespace(output=[output], error=None, model="m", usage=None))
    engine = OpenAIStructuredEngine()
    engine._cache.get = lambda target: SimpleNamespace(responses=stub)  # type: ignore[method-assign]
    asyncio.run(engine.complete(_request()))
    assert stub.calls[0]["input"] == "find"


def test_engine_chat_sends_image_url_with_detail() -> None:
    message = SimpleNamespace(content='{"objects": []}', refusal=None)
    completions = _Stub(SimpleNamespace(choices=[SimpleNamespace(message=message)], model="m"))
    engine = OpenAIStructuredEngine()
    engine._cache.get = lambda target: SimpleNamespace(  # type: ignore[method-assign]
        chat=SimpleNamespace(completions=completions)
    )
    image = StructuredImage(data=b"jpeg", detail="high")
    asyncio.run(engine.complete(_request("chat", images=(image,))))
    user = completions.calls[0]["messages"][1]
    assert user["content"] == [
        {"type": "text", "text": "find"},
        {"type": "image_url", "image_url": {"url": image.data_url(), "detail": "high"}},
    ]


# -- 設定 ------------------------------------------------------------------------------


def test_settings_defaults_and_validation(client: TestClient) -> None:
    body = client.get(SETTINGS).json()
    assert body["enabled"] is False
    assert body["connection_id"] == "openai"
    assert body["model"] == detection_settings.DEFAULT_MODEL
    assert body["usable"] is False
    assert body["requests_last_hour"] == 0
    for bad in (
        {"enabled": None},
        {"enabled": "yes"},
        {"connection_id": "missing"},
        {"model": "  "},
        {"hourly_request_limit": 0},
        {"hourly_request_limit": True},
    ):
        assert client.patch(SETTINGS, json=bad).status_code == 422, bad
    # 不正な項目があれば、ほかの項目も保存しない。
    assert client.patch(SETTINGS, json={"enabled": True, "model": ""}).status_code == 422
    assert client.get(SETTINGS).json()["enabled"] is False
    _enable(client, model=" vlm-x ", hourly_request_limit=5)
    body = client.get(SETTINGS).json()
    assert body["enabled"] is True and body["model"] == "vlm-x"
    assert body["hourly_request_limit"] == 5
    # Fake のエンジンでは接続先が要らない。
    assert body["usable"] is True


def test_used_connection_ids(client: TestClient) -> None:
    factory = client.app.state.session_factory
    with factory() as db:
        assert detection_settings.used_connection_ids(db) == set()
    _enable(client)
    with factory() as db:
        assert detection_settings.used_connection_ids(db) == {"openai"}
        assert "region_detection" in llm_connections.features_using(db)["openai"]


def test_connection_in_use_cannot_be_deleted(client: TestClient) -> None:
    created = client.post(
        CONNECTIONS,
        json={"name": "vlm", "base_url": "http://127.0.0.1:11434/v1", "api_style": "chat"},
    )
    assert created.status_code == 201, created.text
    connection_id = created.json()["connections"][-1]["id"]
    # 無効でも、選んで保存した接続先は使用中に数える。
    response = client.patch(SETTINGS, json={"connection_id": connection_id})
    assert response.status_code == 200, response.text
    listed = {c["id"]: c for c in client.get(CONNECTIONS).json()["connections"]}
    assert "region_detection" in listed[connection_id]["used_by"]
    assert client.delete(f"{CONNECTIONS}/{connection_id}").status_code == 409
    client.patch(SETTINGS, json={"connection_id": "openai"})
    assert client.delete(f"{CONNECTIONS}/{connection_id}").status_code in (200, 204)


def test_settings_update_requires_admin(client_oidc: TestClient) -> None:
    login_as(client_oidc, "user@example.com")
    assert client_oidc.get(SETTINGS).status_code == 200
    assert client_oidc.patch(SETTINGS, json={"enabled": True}).status_code == 403
    login_as(client_oidc, "admin@example.com")
    assert client_oidc.patch(SETTINGS, json={"enabled": True}).status_code == 200
    login_as(client_oidc, "user@example.com")
    assert client_oidc.get(SETTINGS).json()["usable"] is True


# -- 検出の API ------------------------------------------------------------------------


def test_detect_with_fake_engine(client: TestClient) -> None:
    _enable(client)
    asset_id = _upload(client)
    response = client.post(_detect_url(asset_id), json={})
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["regions"]) == 3
    for region in body["regions"]:
        x0, y0, x1, y1 = region["box"]
        assert 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1
        assert region["label"]
    assert body["model"]
    assert body["usage"]["total_tokens"] == 0
    request = _engine(client).requests[-1]
    assert request.schema == detection.RESPONSE_SCHEMA
    assert request.schema_name == detection.SCHEMA_NAME
    assert len(request.images) == 1
    assert request.images[0].detail == "high"
    with Image.open(io.BytesIO(request.images[0].data)) as image:
        assert image.format == "JPEG"
        assert image.size == (96, 64)
    assert "20" in request.instructions
    assert client.get(SETTINGS).json()["requests_last_hour"] == 1


def test_detect_with_query_and_max_items(client: TestClient) -> None:
    _enable(client)
    asset_id = _upload(client)
    response = client.post(_detect_url(asset_id), json={"query": "  カップ ", "max_items": 1})
    assert response.status_code == 200, response.text
    assert [r["label"] for r in response.json()["regions"]] == ["カップ"]
    request = _engine(client).requests[-1]
    assert "カップ" in request.input_text
    assert "1 個" in request.instructions
    assert client.post(_detect_url(asset_id), json={"max_items": 21}).status_code == 422
    assert client.post(_detect_url(asset_id), json={"max_items": 0}).status_code == 422
    assert client.post(_detect_url(asset_id), json={"query": "x" * 201}).status_code == 422


def test_detect_cleans_engine_response(client: TestClient) -> None:
    _enable(client)
    asset_id = _upload(client)
    _engine(client).responses.append(
        {
            "objects": [
                {"label": "a", "box": [0, 0, 500, 500]},
                {"label": "a", "box": [0, 0, 500, 500]},
                {"label": "b", "box": [0, 0, 2000, 500]},
                {"label": "c", "box": [300, 300, 300, 400]},
            ]
        }
    )
    body = client.post(_detect_url(asset_id), json={}).json()
    assert body["regions"] == [{"label": "a", "box": [0.0, 0.0, 0.5, 0.5]}]


def test_detect_rejected_when_disabled(client: TestClient) -> None:
    asset_id = _upload(client)
    response = client.post(_detect_url(asset_id), json={})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "disabled"
    assert _engine(client).requests == []


def test_detect_unknown_asset_and_mask(client: TestClient) -> None:
    _enable(client)
    assert client.post(_detect_url(str(uuid.uuid4())), json={}).status_code == 404
    mask_id = _upload(client, kind="mask")
    response = client.post(_detect_url(mask_id), json={})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "mask_not_supported"


def test_detect_rejects_deleted_asset(client: TestClient) -> None:
    _enable(client)
    asset_id = _upload(client)
    assert client.delete(f"/api/assets/{asset_id}").status_code in (200, 204)
    response = client.post(_detect_url(asset_id), json={})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "asset_deleted"


def test_detect_rejects_denied_image(client: TestClient) -> None:
    _enable(client)
    asset_id = _upload(client)
    with client.app.state.session_factory() as db:
        db.add(
            ModVerdict(
                mod_kind="webhook",
                mod_id="manual",
                mod_name="check",
                asset_id=uuid.UUID(asset_id),
                verdict="deny",
                label="x",
                message="x",
            )
        )
        db.commit()
    response = client.post(_detect_url(asset_id), json={})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "image_withheld"
    assert _engine(client).requests == []


def test_detect_hourly_limit(client: TestClient) -> None:
    _enable(client, hourly_request_limit=1)
    asset_id = _upload(client)
    assert client.post(_detect_url(asset_id), json={}).status_code == 200
    response = client.post(_detect_url(asset_id), json={})
    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "rate_limited"
    # アシスタントの会話とは別に数える。
    assert client.get("/api/settings/assistant").json()["requests_last_hour"] == 0


def test_detect_llm_error(client: TestClient) -> None:
    _enable(client)
    asset_id = _upload(client)
    _engine(client).responses.append(AssistantEngineError("boom"))
    response = client.post(_detect_url(asset_id), json={})
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["code"] == "llm_error" and "boom" in detail["message"]


def test_detect_unsupported_connection(client: TestClient) -> None:
    created = client.post(
        CONNECTIONS,
        json={"name": "local", "base_url": "http://127.0.0.1:11434/v1", "api_style": "chat"},
    )
    assert created.status_code == 201, created.text
    _enable(client, connection_id=created.json()["connections"][-1]["id"])
    asset_id = _upload(client)

    class _RealLike(FakeStructuredEngine):
        requires_target = True

    client.app.state.assistant.structured = _RealLike()
    response = client.post(_detect_url(asset_id), json={})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "unsupported"
    assert client.get(SETTINGS).json()["usable"] is False

    # 組み込みの接続先(OpenAI の公式)なら対応とみなす。キーが無ければ接続先の不足。
    _enable(client, connection_id="openai")
    response = client.post(_detect_url(asset_id), json={})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "connection_missing"
    assert client.get(SETTINGS).json()["usable"] is False


def test_detect_calls_before_send_and_stops_on_deny(client: TestClient) -> None:
    _enable(client)
    asset_id = _upload(client)
    hook = _Hook({"decision": "deny", "reason": "画像は外へ送れません"})
    _install(client, hook, filters={"purposes": ["region_detection"]})
    response = client.post(_detect_url(asset_id), json={"query": "猫のしっぽ"})
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "hook_denied"
    assert "画像は外へ送れません" in detail["message"]
    # VLM は呼ばない。
    assert _engine(client).requests == []
    subject = hook.events[0]["subject"]
    assert subject["purpose"] == "region_detection"
    roles = {part["role"]: part["text"] for part in subject["text"]}
    assert set(roles) == {"system", "query"}
    assert "猫のしっぽ" in roles["query"]
    assert subject["params"]["query"] == "猫のしっぽ"
    assert subject["params"]["max_items"] == 20
    assert subject["params"]["image_detail"] == "high"
    assert subject["target"]["connection"]["id"] == "openai"
    assert subject["asset_id"] == asset_id
    assert [image["asset_id"] for image in subject["images"]] == [asset_id]
    assert subject["images"][0]["role"] == "primary"


def test_detect_allowed_by_hook(client: TestClient) -> None:
    _enable(client)
    asset_id = _upload(client)
    hook = _Hook({"decision": "allow"})
    _install(client, hook)
    response = client.post(_detect_url(asset_id), json={})
    assert response.status_code == 200, response.text
    subject = hook.events[0]["subject"]
    assert subject["purpose"] == "region_detection"
    assert {part["role"] for part in subject["text"]} == {"system", "user"}


def test_detect_not_checked_for_other_purposes(client: TestClient) -> None:
    _enable(client)
    asset_id = _upload(client)
    hook = _Hook({"decision": "deny", "reason": "x"})
    _install(client, hook, filters={"purposes": ["assistant"]})
    assert client.post(_detect_url(asset_id), json={}).status_code == 200
    assert hook.events == []


def test_detect_visibility(client_oidc: TestClient) -> None:
    login_as(client_oidc, "admin@example.com")
    _enable(client_oidc)
    login_as(client_oidc, "alice@example.com")
    alice_asset = _upload(client_oidc)
    assert client_oidc.post(_detect_url(alice_asset), json={}).status_code == 200
    login_as(client_oidc, "bob@example.com")
    assert client_oidc.post(_detect_url(alice_asset), json={}).status_code == 404


def test_detect_labels_follow_locale(client: TestClient) -> None:
    _enable(client)
    asset_id = _upload(client)
    body = client.post(_detect_url(asset_id), json={}, headers={"Accept-Language": "en"}).json()
    with use_locale("en"):
        assert body["regions"][0]["label"] == "Person (dummy)"
    assert "英語" in _engine(client).requests[-1].instructions
