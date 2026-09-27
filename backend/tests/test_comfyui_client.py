"""`app/providers/comfyui/client.py` のテスト。

実物の ComfyUI には一切接続しない。HTTP は `tests/comfyui_fake.py` の `FakeComfyUI`
(`httpx.MockTransport`)、WebSocket は `fake_ws_connect` で組み立てたフレーム列を使う。
"""

from __future__ import annotations

import struct

import httpx
import pytest

from app.providers.comfyui.client import (
    ComfyUIClient,
    ComfyUIError,
    OutputImageRef,
    WsMessage,
    WsPreview,
    check_available,
    collect_output_images,
    parse_binary_frame,
    summarize_node_errors,
)
from tests.comfyui_fake import (
    FakeComfyUI,
    encode_preview_frame,
    encode_preview_with_metadata_frame,
    fake_ws_connect,
    unavailable_async_client,
    unavailable_ws_connect,
)
from tests.openai_mock import run_async

# -- parse_binary_frame ---------------------------------------------------------


def test_parse_binary_frame_preview_jpeg() -> None:
    frame = encode_preview_frame(b"jpeg-bytes", image_type=1)
    preview = parse_binary_frame(frame)
    assert preview == WsPreview(mime="image/jpeg", data=b"jpeg-bytes")


def test_parse_binary_frame_preview_png() -> None:
    frame = encode_preview_frame(b"png-bytes", image_type=2)
    preview = parse_binary_frame(frame)
    assert preview == WsPreview(mime="image/png", data=b"png-bytes")


def test_parse_binary_frame_with_metadata() -> None:
    metadata = {"node": "9", "image_type": "image/png"}
    frame = encode_preview_with_metadata_frame(b"png-bytes", metadata)
    preview = parse_binary_frame(frame)
    assert preview is not None
    assert preview.mime == "image/png"
    assert preview.data == b"png-bytes"
    assert preview.metadata == {"node": "9", "image_type": "image/png"}


def test_parse_binary_frame_unknown_event_type_returns_none() -> None:
    frame = struct.pack(">I", 99) + b"whatever"
    assert parse_binary_frame(frame) is None


def test_parse_binary_frame_unknown_image_subtype_returns_none() -> None:
    frame = struct.pack(">II", 1, 3) + b"data"  # 1/2 以外の画像種別
    assert parse_binary_frame(frame) is None


def test_parse_binary_frame_too_short_returns_none() -> None:
    assert parse_binary_frame(b"\x00\x00") is None


# -- summarize_node_errors --------------------------------------------------------


def test_summarize_node_errors_includes_node_class_type_input_and_message() -> None:
    body = {
        "error": {
            "type": "prompt_outputs_failed_validation",
            "message": "Prompt outputs failed validation",
            "details": "",
        },
        "node_errors": {
            "12": {
                "class_type": "CheckpointLoaderSimple",
                "errors": [
                    {
                        "type": "value_not_in_list",
                        "message": "ckpt_name の値が一覧にありません",
                        "details": "",
                        "extra_info": {"input_name": "ckpt_name"},
                    }
                ],
                "dependent_outputs": ["9"],
            }
        },
    }
    summary = summarize_node_errors(body)
    assert "12" in summary
    assert "CheckpointLoaderSimple" in summary
    assert "ckpt_name" in summary
    assert "ckpt_name の値が一覧にありません" in summary


def test_summarize_node_errors_truncates_long_summary() -> None:
    node_errors = {
        str(i): {
            "class_type": "X",
            "errors": [{"message": "エラー" * 50, "extra_info": {}}],
        }
        for i in range(20)
    }
    summary = summarize_node_errors({"node_errors": node_errors})
    assert len(summary) <= 800
    assert summary.endswith("…")


def test_summarize_node_errors_falls_back_when_empty() -> None:
    assert summarize_node_errors({}) != ""


# -- collect_output_images --------------------------------------------------------


def test_collect_output_images_order_and_temp_excluded() -> None:
    history_entry = {
        "outputs": {
            "9": {
                "images": [
                    {"filename": "a.png", "subfolder": "", "type": "output"},
                    {"filename": "preview.png", "subfolder": "", "type": "temp"},
                    {"filename": "b.png", "subfolder": "sub", "type": "output"},
                ]
            },
            "12": {"images": [{"filename": "c.png", "subfolder": "", "type": "output"}]},
        }
    }
    refs = collect_output_images(history_entry, ["9", "12"])
    assert refs == [
        OutputImageRef(node_id="9", filename="a.png", subfolder="", type="output"),
        OutputImageRef(node_id="9", filename="b.png", subfolder="sub", type="output"),
        OutputImageRef(node_id="12", filename="c.png", subfolder="", type="output"),
    ]


def test_collect_output_images_missing_node_or_images_is_ignored() -> None:
    assert collect_output_images({"outputs": {}}, ["9"]) == []
    assert collect_output_images({}, ["9"]) == []
    assert collect_output_images({"outputs": {"9": {}}}, ["9"]) == []


# -- check_available (同期) --------------------------------------------------------


def test_check_available_success(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeComfyUI()

    def fake_get(url: str, timeout: float) -> httpx.Response:
        request = httpx.Request("GET", url)
        return fake._handle(request)

    monkeypatch.setattr(httpx, "get", fake_get)

    ok, reason, body = check_available("http://127.0.0.1:8188")
    assert ok is True
    assert reason is None
    assert body == fake.system_stats_body


def test_check_available_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, timeout: float) -> httpx.Response:
        raise httpx.ConnectError("boom", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)

    ok, reason, body = check_available("http://127.0.0.1:8188")
    assert ok is False
    assert reason is not None
    assert body is None


def test_check_available_unexpected_status(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, timeout: float) -> httpx.Response:
        return httpx.Response(500)

    monkeypatch.setattr(httpx, "get", fake_get)

    ok, reason, body = check_available("http://127.0.0.1:8188")
    assert ok is False
    assert reason is not None
    assert body is None


# -- ComfyUIClient (非同期の HTTP 部分) ------------------------------------------


@run_async
async def test_system_stats_and_object_info_pass_through() -> None:
    fake = FakeComfyUI()
    fake.object_info_responses["KSampler"] = {"input": {"required": {}}}
    client = ComfyUIClient("http://127.0.0.1:8188", http=fake.make_async_client())
    try:
        assert await client.system_stats() == fake.system_stats_body
        assert await client.object_info("KSampler") == {"KSampler": {"input": {"required": {}}}}
        assert await client.object_info() == fake.object_info_responses
    finally:
        await client.aclose()


@run_async
async def test_queue_prompt_success_returns_prompt_id_and_sends_client_id() -> None:
    fake = FakeComfyUI()
    fake.queue_success(prompt_id="run-123")
    client = ComfyUIClient("http://127.0.0.1:8188", http=fake.make_async_client())
    try:
        prompt_id = await client.queue_prompt({"1": {"class_type": "X"}}, "client-abc")
    finally:
        await client.aclose()

    assert prompt_id == "run-123"
    assert len(fake.queued_prompts) == 1
    assert fake.queued_prompts[0]["client_id"] == "client-abc"
    assert fake.queued_prompts[0]["prompt"] == {"1": {"class_type": "X"}}


@run_async
async def test_queue_prompt_400_raises_comfyui_validation_with_summary() -> None:
    fake = FakeComfyUI()
    fake.queue_error(
        node_errors={
            "5": {
                "class_type": "CheckpointLoaderSimple",
                "errors": [
                    {
                        "message": "存在しない ckpt です",
                        "extra_info": {"input_name": "ckpt_name"},
                    }
                ],
            }
        }
    )
    client = ComfyUIClient("http://127.0.0.1:8188", http=fake.make_async_client())
    try:
        with pytest.raises(ComfyUIError) as exc_info:
            await client.queue_prompt({"1": {}}, "client-abc")
    finally:
        await client.aclose()

    assert exc_info.value.code == "comfyuiValidation"
    assert "5" in exc_info.value.message
    assert "ckpt_name" in exc_info.value.message
    assert "存在しない ckpt です" in exc_info.value.message


@run_async
async def test_queue_prompt_connection_error_raises_comfyui_unavailable() -> None:
    client = ComfyUIClient("http://127.0.0.1:8188", http=unavailable_async_client())
    try:
        with pytest.raises(ComfyUIError) as exc_info:
            await client.queue_prompt({"1": {}}, "client-abc")
    finally:
        await client.aclose()

    assert exc_info.value.code == "comfyuiUnavailable"


@run_async
async def test_upload_image_success_sends_overwrite_and_returns_name() -> None:
    fake = FakeComfyUI()
    client = ComfyUIClient("http://127.0.0.1:8188", http=fake.make_async_client())
    try:
        name = await client.upload_image(b"png-bytes", "gakei_abc123.png", overwrite=True)
    finally:
        await client.aclose()

    assert name == "gakei_abc123.png"
    assert len(fake.uploads) == 1
    upload = fake.uploads[0]
    assert upload["overwrite"] == "true"
    assert upload["image_filename"] == "gakei_abc123.png"
    assert upload["image_bytes"] == b"png-bytes"


@run_async
async def test_upload_image_with_subfolder_response_joins_path() -> None:
    fake = FakeComfyUI()
    fake._upload_response_body = {"name": "a.png", "subfolder": "gakei", "type": "input"}
    client = ComfyUIClient("http://127.0.0.1:8188", http=fake.make_async_client())
    try:
        name = await client.upload_image(b"data", "a.png")
    finally:
        await client.aclose()
    assert name == "gakei/a.png"


@run_async
async def test_upload_image_failure_raises_comfyui_upload() -> None:
    fake = FakeComfyUI()
    fake.fail_upload(400, {"error": "bad request"})
    client = ComfyUIClient("http://127.0.0.1:8188", http=fake.make_async_client())
    try:
        with pytest.raises(ComfyUIError) as exc_info:
            await client.upload_image(b"data", "a.png")
    finally:
        await client.aclose()
    assert exc_info.value.code == "comfyuiUpload"


@run_async
async def test_history_found_returns_entry() -> None:
    fake = FakeComfyUI()
    fake.set_history(
        "run-1", {"outputs": {"9": {"images": []}}, "status": {"status_str": "success"}}
    )
    client = ComfyUIClient("http://127.0.0.1:8188", http=fake.make_async_client())
    try:
        entry = await client.history("run-1")
    finally:
        await client.aclose()
    assert entry == {"outputs": {"9": {"images": []}}, "status": {"status_str": "success"}}


@run_async
async def test_history_not_found_returns_none() -> None:
    fake = FakeComfyUI()
    client = ComfyUIClient("http://127.0.0.1:8188", http=fake.make_async_client())
    try:
        entry = await client.history("missing")
    finally:
        await client.aclose()
    assert entry is None


@run_async
async def test_view_returns_bytes() -> None:
    fake = FakeComfyUI()
    fake.add_output_file("out.png", "", "output", b"the-bytes")
    client = ComfyUIClient("http://127.0.0.1:8188", http=fake.make_async_client())
    ref = OutputImageRef(node_id="9", filename="out.png", subfolder="", type="output")
    try:
        data = await client.view(ref)
    finally:
        await client.aclose()
    assert data == b"the-bytes"


@run_async
async def test_view_missing_raises_comfyui_error() -> None:
    fake = FakeComfyUI()
    client = ComfyUIClient("http://127.0.0.1:8188", http=fake.make_async_client())
    ref = OutputImageRef(node_id="9", filename="missing.png", subfolder="", type="output")
    try:
        with pytest.raises(ComfyUIError):
            await client.view(ref)
    finally:
        await client.aclose()


# -- ws_events --------------------------------------------------------------------


@run_async
async def test_ws_events_converts_json_and_binary_frames() -> None:
    ws_connect = fake_ws_connect(
        [
            WsMessage("status", {"exec_info": {"queue_remaining": 1}}),
            encode_preview_frame(b"jpeg-bytes", image_type=1),
            WsMessage("execution_success", {"prompt_id": "run-1"}),
        ]
    )
    client = ComfyUIClient("http://127.0.0.1:8188", ws_connect=ws_connect)
    try:
        events = []
        async with client.ws_events("client-1") as stream:
            async for event in stream:
                events.append(event)
    finally:
        await client.aclose()

    assert events == [
        WsMessage(type="status", data={"exec_info": {"queue_remaining": 1}}),
        WsPreview(mime="image/jpeg", data=b"jpeg-bytes"),
        WsMessage(type="execution_success", data={"prompt_id": "run-1"}),
    ]


@run_async
async def test_ws_events_skips_unknown_binary_frames() -> None:
    ws_connect = fake_ws_connect(
        [
            struct.pack(">I", 3) + b"raw text event, unsupported",  # TEXT (=3) は非対応
            WsMessage("status", {}),
        ]
    )
    client = ComfyUIClient("http://127.0.0.1:8188", ws_connect=ws_connect)
    try:
        events = []
        async with client.ws_events("client-1") as stream:
            async for event in stream:
                events.append(event)
    finally:
        await client.aclose()

    assert events == [WsMessage(type="status", data={})]


@run_async
async def test_ws_events_mid_stream_disconnect_raises_comfyui_unavailable() -> None:
    ws_connect = fake_ws_connect(
        [
            WsMessage("status", {}),
            ConnectionError("connection lost"),
        ]
    )
    client = ComfyUIClient("http://127.0.0.1:8188", ws_connect=ws_connect)
    try:
        events = []
        with pytest.raises(ComfyUIError) as exc_info:
            async with client.ws_events("client-1") as stream:
                async for event in stream:
                    events.append(event)
    finally:
        await client.aclose()

    assert events == [WsMessage(type="status", data={})]
    assert exc_info.value.code == "comfyuiUnavailable"


@run_async
async def test_ws_events_connect_failure_raises_comfyui_unavailable() -> None:
    client = ComfyUIClient("http://127.0.0.1:8188", ws_connect=unavailable_ws_connect())
    try:
        with pytest.raises(ComfyUIError) as exc_info:
            async with client.ws_events("client-1") as stream:
                async for _event in stream:
                    pass
    finally:
        await client.aclose()

    assert exc_info.value.code == "comfyuiUnavailable"


@run_async
async def test_ws_events_does_not_rewrite_caller_exceptions() -> None:
    """`async with` の本体で呼び出し側が投げた例外(タイムアウトなど)は書き換えない。"""
    ws_connect = fake_ws_connect([WsMessage("status", {})])
    client = ComfyUIClient("http://127.0.0.1:8188", ws_connect=ws_connect)
    try:
        with pytest.raises(TimeoutError):
            async with client.ws_events("client-1") as stream:
                async for _event in stream:
                    raise TimeoutError
    finally:
        await client.aclose()
