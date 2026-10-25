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
from pathlib import Path
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
from app.domain import api_key as api_key_domain
from app.domain import content_withholding, llm_connections
from app.domain import region_detection as detection
from app.domain import region_detection_settings as detection_settings
from app.domain.general_settings import _save
from app.domain.llm_connections import LlmTarget
from app.domain.models import AppUser, AssetRegionDetection, ModVerdict
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
    # 見つからない(空の配列)は空の結果。
    assert detection.interpret({"objects": []}, 20) == []
    assert detection.interpret([], 20) == []
    # 答えの形でなければ、空の結果ではなくエラー(スキーマを黙って無視した口など)。
    for bad in ({}, {"objects": "x"}, None, {"items": []}, "text", 3):
        with pytest.raises(detection.RegionResponseError):
            detection.interpret(bad, 20)
    # 要素があるのに1つも読めないときもエラー。
    with pytest.raises(detection.RegionResponseError):
        detection.interpret({"objects": [{"name": "x", "coordinates": [0, 0, 10, 10]}]}, 20)


def test_interpret_drops_duplicates_before_max_items() -> None:
    """同じ要素が上限を超えて並んでも、重複は上限を数える前に除くので、後の要素も拾える。"""
    same = {"label": "cup", "box": [10, 10, 100, 100]}
    others = [{"label": f"o{i}", "box": [i * 10, 0, i * 10 + 5, 10]} for i in range(3)]
    boxes = detection.interpret({"objects": [same] * 25 + others}, 20)
    assert [b.label for b in boxes] == ["cup", "o0", "o1", "o2"]


def test_interpret_accepts_key_variants_and_bare_array() -> None:
    data = [
        {"label": "a", "bbox_2d": [0, 0, 100, 100]},
        {"label": "b", "bbox": [0, 0, 200, 200]},
        {"name": "c", "box": [0, 0, 300, 300]},
    ]
    boxes = detection.interpret(data, 20)
    assert [b.label for b in boxes] == ["a", "b", "c"]
    assert boxes[0].box == pytest.approx((0.0, 0.0, 0.1, 0.1))


def test_parse_response_lenient() -> None:
    expected = {"objects": [{"label": "cup", "box": [1, 2, 3, 4]}]}
    plain = '{"objects": [{"label": "cup", "box": [1, 2, 3, 4]}]}'
    assert detection.parse_response(plain) == expected
    # 前後の空白と ```json の囲み。
    assert detection.parse_response(f"  \n```json\n{plain}\n```\n ") == expected
    assert detection.parse_response(f"```\n{plain}\n```") == expected
    # 閉じていない囲み。
    assert detection.parse_response(f"```json\n{plain}") == expected
    # 前後の文(括弧を含む文でも、次の JSON を探す)。
    assert detection.parse_response(f"Here are the boxes (x first):\n{plain}\nDone.") == expected
    # 配列だけ。
    assert detection.parse_response('[{"label": "a", "bbox_2d": [1, 2, 3, 4]}]') == [
        {"label": "a", "bbox_2d": [1, 2, 3, 4]}
    ]
    # 複数行の整形。
    multi = (
        '{\n  "objects": [\n    {\n      "label": "a",\n'
        '      "box": [\n        1,\n        2,\n        3,\n        4\n      ]\n    }\n  ]\n}'
    )
    assert detection.parse_response(multi) == {"objects": [{"label": "a", "box": [1, 2, 3, 4]}]}


def test_parse_response_salvages_unclosed_array() -> None:
    """出力の上限で切れた応答は、閉じた要素までを拾う。"""
    cut = '{"objects": [{"label": "a", "box": [1, 2, 3, 4]}, {"label": "b", "box": [5, 6'
    assert detection.parse_response(cut) == [{"label": "a", "box": [1, 2, 3, 4]}]
    cut = '```json\n[\n  {"label": "a", "bbox_2d": [1, 2, 3, 4]},\n  {"label": "b", "bb'
    assert detection.parse_response(cut) == [{"label": "a", "bbox_2d": [1, 2, 3, 4]}]
    boxes = detection.interpret(detection.parse_response(cut), 20)
    assert [b.label for b in boxes] == ["a"]


def test_parse_response_errors() -> None:
    for bad in (None, "", "   ", "no json here", '{"objects": [{"label": "a", "bo', "```json\n```"):
        with pytest.raises(detection.RegionResponseError):
            detection.parse_response(bad)


def test_instructions_include_language_limit_and_query() -> None:
    text = detection.instructions("en", query=None, max_items=7)
    assert "英語" in text and "7" in text and "0〜1000" in text
    assert "探している物だけ" not in text
    text = detection.instructions("ja", query="cup", max_items=20)
    assert "日本語" in text and "探している物だけ" in text
    assert detection.build_input("cup").endswith("cup")
    # スキーマを付けないときだけ、末尾に答えの形を示す。
    assert '"objects"' not in text
    text = detection.instructions("ja", query=None, max_items=20, structured=False)
    assert text.rstrip().endswith('{"objects":[{"label":"名前","box":[x0,y0,x1,y1]}]}')


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


def _request(
    api_style: str = "responses", images: tuple[StructuredImage, ...] = (), **extra: Any
) -> Any:
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
        images=images,
        **{"schema": detection.RESPONSE_SCHEMA, **extra},
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


def test_engine_responses_without_schema_and_with_limit() -> None:
    """スキーマの無い要求は `text.format` を付けない。上限は `max_output_tokens`。`raw_text` なら
    文のまま返す(上限で切れた応答も)。"""
    output = SimpleNamespace(
        type="message", content=[SimpleNamespace(type="output_text", text="```json\n[")]
    )
    stub = _Stub(
        SimpleNamespace(output=[output], error=None, model="m", usage=None, status="incomplete")
    )
    engine = OpenAIStructuredEngine()
    engine._cache.get = lambda target: SimpleNamespace(responses=stub)  # type: ignore[method-assign]
    result = asyncio.run(
        engine.complete(_request(schema=None, max_output_tokens=1234, raw_text=True))
    )
    assert result.text == "```json\n[" and result.data == {}
    call = stub.calls[0]
    assert "text" not in call
    assert call["max_output_tokens"] == 1234
    # スキーマのある要求にも上限を付けられる。
    asyncio.run(engine.complete(_request(max_output_tokens=99, raw_text=True)))
    assert stub.calls[1]["max_output_tokens"] == 99 and "format" in stub.calls[1]["text"]


def test_engine_chat_without_schema_and_with_limit() -> None:
    message = SimpleNamespace(content='[{"label": "a", "bbox": [1, 2, 3, 4]}]', refusal=None)
    completions = _Stub(SimpleNamespace(choices=[SimpleNamespace(message=message)], model="m"))
    engine = OpenAIStructuredEngine()
    engine._cache.get = lambda target: SimpleNamespace(  # type: ignore[method-assign]
        chat=SimpleNamespace(completions=completions)
    )
    result = asyncio.run(
        engine.complete(_request("chat", schema=None, max_output_tokens=2000, raw_text=True))
    )
    assert result.text == '[{"label": "a", "bbox": [1, 2, 3, 4]}]'
    call = completions.calls[0]
    assert "response_format" not in call
    assert call["max_completion_tokens"] == 2000
    # 上限を付けない要求には付けない(今までの呼び方)。
    asyncio.run(engine.complete(_request("chat", raw_text=True)))
    assert "max_completion_tokens" not in completions.calls[1]
    assert "response_format" in completions.calls[1]


def test_engine_chat_falls_back_to_max_tokens() -> None:
    """`max_completion_tokens` を断る接続先には、`max_tokens` で送り直す。"""
    import httpx
    import openai

    message = SimpleNamespace(content="{}", refusal=None)
    ok = SimpleNamespace(choices=[SimpleNamespace(message=message)], model="m")

    class _Picky(_Stub):
        async def create(self, **kwargs: Any) -> Any:
            self.calls.append(dict(kwargs))
            if "max_completion_tokens" in kwargs:
                request = httpx.Request("POST", "http://x/v1/chat/completions")
                raise openai.BadRequestError(
                    "Unrecognized request argument supplied: max_completion_tokens",
                    response=httpx.Response(400, request=request),
                    body=None,
                )
            return self.response

    completions = _Picky(ok)
    engine = OpenAIStructuredEngine()
    engine._cache.get = lambda target: SimpleNamespace(  # type: ignore[method-assign]
        chat=SimpleNamespace(completions=completions)
    )
    asyncio.run(engine.complete(_request("chat", max_output_tokens=500)))
    assert len(completions.calls) == 2
    assert completions.calls[1]["max_tokens"] == 500
    assert "max_completion_tokens" not in completions.calls[1]


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
    assert request.max_output_tokens == detection.MAX_OUTPUT_TOKENS
    assert request.raw_text is True
    assert '"objects"' not in request.instructions
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


def test_detect_reads_loose_text_response(client: TestClient) -> None:
    _enable(client)
    asset_id = _upload(client)
    _engine(client).responses.append(
        'Sure!\n```json\n[\n  {"label": "a", "bbox_2d": [0, 0, 500, 500]},\n  {"label": "b", "b'
    )
    body = client.post(_detect_url(asset_id), json={}).json()
    assert body["regions"] == [{"label": "a", "box": [0.0, 0.0, 0.5, 0.5]}]


def test_detect_shape_mismatch_is_error_not_empty(client: TestClient) -> None:
    """答えの形でない応答は「見つからない」ではなくエラーにし、履歴にも残さない。"""
    _enable(client)
    asset_id = _upload(client)
    for bad in ({"results": [{"name": "x", "coords": [0, 0, 1, 1]}]}, "I cannot see any image."):
        _engine(client).responses.append(bad)
        response = client.post(_detect_url(asset_id), json={})
        assert response.status_code == 502, response.text
        assert response.json()["detail"]["code"] == "llm_error"
    assert client.get(_detections_url(asset_id)).json()["items"] == []
    # 空の配列は「見つからない」(200)。
    _engine(client).responses.append({"objects": []})
    response = client.post(_detect_url(asset_id), json={})
    assert response.status_code == 200 and response.json()["regions"] == []


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


def test_detect_with_connection_without_structured_outputs(client: TestClient) -> None:
    """構造化出力に対応しない接続先(手元の VLM)でも検出できる。スキーマを付けずに、答えの形を
    指示文で示して送る。"""
    created = client.post(
        CONNECTIONS,
        json={"name": "local", "base_url": "http://127.0.0.1:11434/v1", "api_style": "chat"},
    )
    assert created.status_code == 201, created.text
    _enable(client, connection_id=created.json()["connections"][-1]["id"])
    asset_id = _upload(client)
    engine = _RealLikeEngine()
    client.app.state.assistant.structured = engine
    assert _settings_reason(client) == (True, None)
    engine.responses.append(
        '```json\n{"objects": [{"label": "cup", "bbox_2d": [0, 0, 500, 500]}]}\n```'
    )
    response = client.post(_detect_url(asset_id), json={})
    assert response.status_code == 200, response.text
    assert response.json()["regions"] == [{"label": "cup", "box": [0.0, 0.0, 0.5, 0.5]}]
    request = engine.requests[-1]
    assert request.schema is None
    assert request.raw_text is True
    assert request.max_output_tokens == detection.MAX_OUTPUT_TOKENS
    assert '{"objects":[{"label":' in request.instructions

    # 組み込みの接続先(OpenAI の公式)でキーが無ければ接続先の不足。
    _enable(client, connection_id="openai")
    response = client.post(_detect_url(asset_id), json={})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "connection_missing"
    assert client.get(SETTINGS).json()["usable"] is False


class _RealLikeEngine(FakeStructuredEngine):
    """接続先が要るエンジン(実物と同じ検証を通す。LLM は呼ばない)。"""

    requires_target = True


def _settings_reason(client: TestClient) -> tuple[bool, str | None]:
    body = client.get(SETTINGS).json()
    return body["usable"], body["unusable_reason"]


def test_unusable_reason_codes(client: TestClient, data_dir: Path) -> None:
    client.app.state.assistant.structured = _RealLikeEngine()
    assert _settings_reason(client) == (False, "disabled")
    _enable(client)
    # 組み込みの接続先で OpenAI のキーが無い。
    assert _settings_reason(client) == (False, "api_key_missing")
    api_key_domain.write_file_key(data_dir, "sk-test")
    # 組み込みで Base URL が既定のまま(OpenAI 本体)なら、自動で対応とみなす。
    assert _settings_reason(client) == (True, None)
    # 接続先が無い(保存の後でほかの手段で消えたとき)。
    with client.app.state.session_factory() as db:
        _save(db, "region_detection.connection_id", "missing")
    assert _settings_reason(client) == (False, "connection_missing")


def test_structured_outputs_setting_switches_schema(client: TestClient, data_dir: Path) -> None:
    """組み込みの接続先を OpenAI 互換のプロキシに向けても使える。構造化出力を「対応する」に
    すればスキーマを付け、そうでなければ付けずに送る。"""
    engine = _RealLikeEngine()
    client.app.state.assistant.structured = engine
    api_key_domain.write_file_key(data_dir, "sk-test")
    api_key_domain.write_file_base_url(data_dir, "http://127.0.0.1:4000/v1")
    _enable(client)
    asset_id = _upload(client)
    assert _settings_reason(client) == (True, None)
    assert client.post(_detect_url(asset_id), json={}).status_code == 200
    assert engine.requests[-1].schema is None

    builtin = f"{CONNECTIONS}/openai"
    assert client.patch(builtin, json={"structured_outputs": "supported"}).status_code == 200
    assert client.post(_detect_url(asset_id), json={}).status_code == 200
    assert engine.requests[-1].schema == detection.RESPONSE_SCHEMA
    assert '"objects"' not in engine.requests[-1].instructions

    api_key_domain.delete_file_base_url(data_dir)
    assert client.patch(builtin, json={"structured_outputs": "unsupported"}).status_code == 200
    assert _settings_reason(client) == (True, None)
    assert client.post(_detect_url(asset_id), json={}).status_code == 200
    assert engine.requests[-1].schema is None


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


# -- 検出の履歴 ------------------------------------------------------------------------


def _detections_url(asset_id: str) -> str:
    return f"/api/assets/{asset_id}/regions/detections"


def test_detect_saves_history(client: TestClient) -> None:
    _enable(client)
    asset_id = _upload(client)
    assert client.get(_detections_url(asset_id)).json() == {"items": []}
    first = client.post(_detect_url(asset_id), json={}).json()
    detection_row = first["detection"]
    assert detection_row["query"] is None
    assert detection_row["regions"] == first["regions"]
    assert detection_row["model"] == first["model"]
    assert detection_row["id"] and detection_row["created_at"]
    # 見つからなかった結果も残す。言葉は前後の空白を除いて残す。
    _engine(client).responses.append({"objects": []})
    second = client.post(_detect_url(asset_id), json={"query": "  カップ  "}).json()
    assert second["regions"] == []
    assert second["detection"]["query"] == "カップ"
    # 空白だけの言葉は言葉なし。
    third = client.post(_detect_url(asset_id), json={"query": "   "}).json()
    assert third["detection"]["query"] is None
    assert "探している物だけ" not in _engine(client).requests[-1].instructions

    items = client.get(_detections_url(asset_id)).json()["items"]
    assert [i["id"] for i in items] == [
        third["detection"]["id"],
        second["detection"]["id"],
        detection_row["id"],
    ]
    assert items[1]["regions"] == []
    with client.app.state.session_factory() as db:
        row = db.get(AssetRegionDetection, uuid.UUID(detection_row["id"]))
        assert row is not None
        assert row.connection_id == "openai" and row.max_items == 20
        assert row.created_by_user_id is None


def test_history_kept_while_disabled(client: TestClient) -> None:
    _enable(client)
    asset_id = _upload(client)
    client.post(_detect_url(asset_id), json={})
    assert client.patch(SETTINGS, json={"enabled": False}).status_code == 200
    assert len(client.get(_detections_url(asset_id)).json()["items"]) == 1
    assert client.post(_detect_url(asset_id), json={}).status_code == 409


def test_history_is_pruned_to_max(client: TestClient) -> None:
    asset_id = uuid.UUID(_upload(client))
    other_id = uuid.UUID(_upload(client))
    factory = client.app.state.session_factory
    ids: list[uuid.UUID] = []
    with factory() as db:
        detection.save_detection(
            db,
            asset_id=other_id,
            query=None,
            connection_id="openai",
            model="m",
            max_items=20,
            boxes=[],
            user_id=None,
        )
        for i in range(detection.HISTORY_MAX + 2):
            row = detection.save_detection(
                db,
                asset_id=asset_id,
                query=f"q{i}",
                connection_id="openai",
                model="m",
                max_items=20,
                boxes=[detection.DetectedBox(label="x", box=(0.0, 0.0, 0.5, 0.5))],
                user_id=None,
            )
            ids.append(row.id)
            db.commit()
    items = client.get(_detections_url(str(asset_id))).json()["items"]
    assert len(items) == detection.HISTORY_MAX
    # 古い2回が消え、新しい順に並ぶ。
    assert [uuid.UUID(i["id"]) for i in items] == list(reversed(ids[2:]))
    assert items[0]["regions"] == [{"label": "x", "box": [0.0, 0.0, 0.5, 0.5]}]
    # ほかの Asset の履歴は消さない。
    assert len(client.get(_detections_url(str(other_id))).json()["items"]) == 1


def test_delete_detection(client: TestClient) -> None:
    _enable(client)
    asset_id = _upload(client)
    other_id = _upload(client)
    detection_id = client.post(_detect_url(asset_id), json={}).json()["detection"]["id"]
    # ほかの Asset の id では消せない。
    assert client.delete(f"{_detections_url(other_id)}/{detection_id}").status_code == 404
    assert client.delete(f"{_detections_url(asset_id)}/{uuid.uuid4()}").status_code == 404
    response = client.delete(f"{_detections_url(asset_id)}/{detection_id}")
    assert response.status_code == 204
    assert client.get(_detections_url(asset_id)).json()["items"] == []
    assert client.delete(f"{_detections_url(asset_id)}/{detection_id}").status_code == 404


def test_history_rejects_mask_and_unknown(client: TestClient) -> None:
    assert client.get(_detections_url(str(uuid.uuid4()))).status_code == 404
    mask_id = _upload(client, kind="mask")
    response = client.get(_detections_url(mask_id))
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "mask_not_supported"


def test_history_visibility_and_delete_permissions(client_oidc: TestClient) -> None:
    login_as(client_oidc, "admin@example.com")
    _enable(client_oidc)
    login_as(client_oidc, "alice@example.com")
    asset_id = _upload(client_oidc)
    detection_id = client_oidc.post(_detect_url(asset_id), json={}).json()["detection"]["id"]
    with client_oidc.app.state.session_factory() as db:
        row = db.get(AssetRegionDetection, uuid.UUID(detection_id))
        assert row is not None and row.created_by_user_id is not None
        alice_id = row.created_by_user_id
    assert len(client_oidc.get(_detections_url(asset_id)).json()["items"]) == 1
    # 見えない利用者には、履歴も消す口も 404。
    login_as(client_oidc, "bob@example.com")
    assert client_oidc.get(_detections_url(asset_id)).status_code == 404
    url = f"{_detections_url(asset_id)}/{detection_id}"
    assert client_oidc.delete(url).status_code == 404
    # 見えるが本人ではない(管理者でもない)なら 403。持ち主を別の人に変えて確かめる。
    login_as(client_oidc, "alice@example.com")
    factory = client_oidc.app.state.session_factory

    def _set_owner(email: str | None, user_id: uuid.UUID | None = None) -> None:
        with factory() as db:
            row = db.get(AssetRegionDetection, uuid.UUID(detection_id))
            assert row is not None
            if email is not None:
                user_id = db.query(AppUser).filter(AppUser.email == email).one().id
            row.created_by_user_id = user_id
            db.commit()

    _set_owner("bob@example.com")
    assert client_oidc.delete(url).status_code == 403
    _set_owner(None, alice_id)
    assert client_oidc.delete(url).status_code == 204


def test_history_admin_can_delete_others(client_oidc: TestClient) -> None:
    """管理者は、見える Asset の他人の検出を消せる。"""
    login_as(client_oidc, "admin@example.com")
    _enable(client_oidc)
    asset_id = _upload(client_oidc)
    detection_id = client_oidc.post(_detect_url(asset_id), json={}).json()["detection"]["id"]
    with client_oidc.app.state.session_factory() as db:
        db.add(AppUser(email="carol@example.com", name="Carol", subject="carol", issuer="test"))
        db.flush()
        carol = db.query(AppUser).filter(AppUser.email == "carol@example.com").one()
        row = db.get(AssetRegionDetection, uuid.UUID(detection_id))
        assert row is not None
        row.created_by_user_id = carol.id
        db.commit()
    url = f"{_detections_url(asset_id)}/{detection_id}"
    assert client_oidc.delete(url).status_code == 204


def test_withholding_drops_history(client: TestClient) -> None:
    """画像を残さなかった Asset(ADR-0051 4章)では、画像から作った検出の履歴も消す。"""
    _enable(client)
    asset_id = _upload(client)
    client.post(_detect_url(asset_id), json={})
    factory = client.app.state.session_factory
    with factory() as db:
        verdict = ModVerdict(
            mod_kind="webhook",
            mod_id="manual",
            mod_name="check",
            asset_id=uuid.UUID(asset_id),
            verdict="deny",
            label="x",
            message="x",
        )
        db.add(verdict)
        db.flush()
        targets = content_withholding.apply_denials(db, [verdict])
        db.commit()
    content_withholding.purge(client.app.state.store, targets)
    with factory() as db:
        assert db.query(AssetRegionDetection).count() == 0
