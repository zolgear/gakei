"""`OpenAIImagesProvider` の単体テスト。実 API は呼ばず、httpx2 の MockTransport で完結させる。"""

from __future__ import annotations

import uuid
from pathlib import Path

import httpx2
import pytest

from app.providers.base import InputImage, ProviderError, RunRequest
from app.providers.openai_images import OpenAIImagesProvider
from tests.openai_mock import (
    capture,
    generation_response_body,
    json_response,
    make_b64,
    make_client,
    part_field_value,
    part_file_bytes,
    part_filename,
    run_async,
    sse_response,
    usage_payload,
)


def _run_request(
    *,
    operation: str = "generate",
    params: dict | None = None,
    inputs: list[InputImage] | None = None,
    prompt: str = "a red apple on a table",
) -> RunRequest:
    return RunRequest(
        run_id=uuid.uuid4(),
        operation=operation,
        model="gpt-image-2.5-sunburst",
        prompt=prompt,
        params=params or {},
        inputs=inputs or [],
    )


async def _noop_progress(_event) -> None:  # noqa: ANN001
    pass


# -- generate(非ストリーミング) -------------------------------------------


@run_async
async def test_generate_sends_model_prompt_and_params_only_no_extra_keys() -> None:
    captured: dict = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured["request"] = capture(request)
        return json_response(generation_response_body(b64=make_b64(b"result-1")))

    client = make_client(handler)
    provider = OpenAIImagesProvider(client=client)

    params = {"quality": "low", "size": "1024x1024", "output_format": "png", "n": 1}
    request = _run_request(params=params)
    result = await provider.execute(request, _noop_progress)

    sent = captured["request"]
    assert sent.path.endswith("/images/generations")
    assert sent.json_body == {
        "model": "gpt-image-2.5-sunburst",
        "prompt": "a red apple on a table",
        "quality": "low",
        "size": "1024x1024",
        "output_format": "png",
        "n": 1,
    }

    assert len(result.outputs) == 1
    assert result.outputs[0].data == b"result-1"
    assert result.outputs[0].mime == "image/png"
    # 非ストリーミングの Usage には output_tokens_details も乗るので、主要な値だけ照合する。
    assert result.usage["input_tokens"] == usage_payload()["input_tokens"]
    assert result.usage["output_tokens"] == usage_payload()["output_tokens"]
    assert isinstance(result.provider_request_id, str) or result.provider_request_id is None


@run_async
async def test_generate_decodes_b64_and_reads_usage_and_request_id() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return json_response(
            generation_response_body(b64=make_b64(b"actual-png-bytes")),
            headers={"x-request-id": "req_generate_123"},
        )

    client = make_client(handler)
    provider = OpenAIImagesProvider(client=client)

    result = await provider.execute(_run_request(params={"n": 1}), _noop_progress)

    assert result.outputs[0].data == b"actual-png-bytes"
    assert result.usage["output_tokens"] == 100
    assert result.provider_request_id == "req_generate_123"

    for output in result.outputs:
        assert isinstance(output.data, bytes)


@run_async
async def test_empty_data_is_not_recorded_as_success() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return json_response({"created": 0, "data": []}, headers={"x-request-id": "req_empty"})

    provider = OpenAIImagesProvider(client=make_client(handler))

    with pytest.raises(ProviderError) as excinfo:
        await provider.execute(_run_request(params={"n": 1}), _noop_progress)

    assert excinfo.value.code == "providerError"
    assert excinfo.value.request_id == "req_empty"


# -- edit(multipart) --------------------------------------------------------


@run_async
async def test_edit_multipart_has_images_in_position_order_and_mask() -> None:
    captured: dict = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured["request"] = capture(request)
        return json_response(generation_response_body())

    client = make_client(handler)
    provider = OpenAIImagesProvider(client=client)

    # わざと position の逆順で渡し、送信時には position 昇順になることを確認する。
    inputs = [
        InputImage(role="image", position=1, data=b"IMG-POS-1", mime="image/png"),
        InputImage(role="image", position=0, data=b"IMG-POS-0", mime="image/png"),
        InputImage(role="mask", position=0, data=b"MASK-BYTES", mime="image/png"),
    ]
    request = _run_request(operation="edit", params={"input_fidelity": "high"}, inputs=inputs)
    await provider.execute(request, _noop_progress)

    sent = captured["request"]
    assert sent.path.endswith("/images/edits")

    image_parts = sent.part_names("image[]") or sent.part_names("image")
    assert len(image_parts) == 2
    assert [part_file_bytes(p) for p in image_parts] == [b"IMG-POS-0", b"IMG-POS-1"]
    assert all((part_filename(p) or "").endswith(".png") for p in image_parts)

    mask_parts = sent.part_names("mask")
    assert len(mask_parts) == 1
    assert part_file_bytes(mask_parts[0]) == b"MASK-BYTES"

    fidelity_parts = sent.part_names("input_fidelity")
    assert len(fidelity_parts) == 1
    assert part_field_value(fidelity_parts[0]) == "high"


@run_async
async def test_edit_without_image_input_raises_provider_error() -> None:
    provider = OpenAIImagesProvider(client=make_client(lambda r: json_response({})))
    request = _run_request(operation="edit", params={}, inputs=[])

    with pytest.raises(ProviderError) as exc_info:
        await provider.execute(request, _noop_progress)
    assert exc_info.value.code == "providerError"


# -- ストリーミング(partial_images) -----------------------------------------


@run_async
async def test_generate_streaming_sends_stream_true_and_collects_partials() -> None:
    captured: dict = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured["request"] = capture(request)
        events = [
            {
                "type": "image_generation.partial_image",
                "b64_json": make_b64(b"partial-0"),
                "background": "auto",
                "created_at": 1,
                "output_format": "png",
                "partial_image_index": 0,
                "quality": "auto",
                "size": "1024x1024",
            },
            {
                "type": "image_generation.partial_image",
                "b64_json": make_b64(b"partial-1"),
                "background": "auto",
                "created_at": 2,
                "output_format": "png",
                "partial_image_index": 1,
                "quality": "auto",
                "size": "1024x1024",
            },
            {
                "type": "image_generation.completed",
                "b64_json": make_b64(b"completed-final"),
                "background": "auto",
                "created_at": 3,
                "output_format": "png",
                "quality": "auto",
                "size": "1024x1024",
                "usage": usage_payload(),
            },
        ]
        return sse_response(events)

    client = make_client(handler)
    provider = OpenAIImagesProvider(client=client)

    partials: list = []

    async def on_progress(event) -> None:  # noqa: ANN001
        partials.append(event)

    request = _run_request(params={"partial_images": 2, "n": 1})
    result = await provider.execute(request, on_progress)

    sent = captured["request"]
    assert sent.json_body["stream"] is True
    assert sent.json_body["partial_images"] == 2
    # run.params 自体には stream キーを書き戻さない(DB に保存される値と一致させるため)。
    assert request.params == {"partial_images": 2, "n": 1}

    assert len(partials) == 2
    assert [p.partial_index for p in partials] == [0, 1]
    assert partials[0].data == b"partial-0"

    assert len(result.outputs) == 1
    assert result.outputs[0].data == b"completed-final"
    assert result.usage == usage_payload()


@run_async
async def test_no_partial_images_means_no_stream() -> None:
    captured: dict = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured["request"] = capture(request)
        return json_response(generation_response_body())

    client = make_client(handler)
    provider = OpenAIImagesProvider(client=client)

    await provider.execute(_run_request(params={"n": 1}), _noop_progress)
    assert "stream" not in captured["request"].json_body

    captured.clear()
    await provider.execute(_run_request(params={"n": 1, "partial_images": 0}), _noop_progress)
    assert "stream" not in captured["request"].json_body


# -- エラー変換 ---------------------------------------------------------------


@run_async
async def test_content_filter_moderation_blocked_maps_to_content_filter() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return json_response(
            {
                "error": {
                    "code": "moderation_blocked",
                    "type": "image_generation_user_error",
                    "message": "blocked",
                }
            },
            status_code=400,
            headers={"x-request-id": "req_blocked_1"},
        )

    client = make_client(handler)
    provider = OpenAIImagesProvider(client=client)

    with pytest.raises(ProviderError) as exc_info:
        await provider.execute(_run_request(params={"n": 1}), _noop_progress)

    assert exc_info.value.code == "contentFilter"
    assert exc_info.value.request_id == "req_blocked_1"


@run_async
async def test_content_policy_violation_maps_to_content_filter() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return json_response(
            {
                "error": {
                    "code": "content_policy_violation",
                    "type": "invalid_request_error",
                    "message": "no",
                }
            },
            status_code=400,
        )

    client = make_client(handler)
    provider = OpenAIImagesProvider(client=client)

    with pytest.raises(ProviderError) as exc_info:
        await provider.execute(_run_request(params={"n": 1}), _noop_progress)
    assert exc_info.value.code == "contentFilter"


@run_async
async def test_generic_bad_request_maps_to_provider_error_not_content_filter() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return json_response(
            {
                "error": {
                    "code": "invalid_size",
                    "type": "invalid_request_error",
                    "message": "bad size",
                }
            },
            status_code=400,
        )

    client = make_client(handler)
    provider = OpenAIImagesProvider(client=client)

    with pytest.raises(ProviderError) as exc_info:
        await provider.execute(_run_request(params={"n": 1}), _noop_progress)
    assert exc_info.value.code == "providerError"
    assert "bad size" in exc_info.value.message


@run_async
async def test_rate_limit_429_maps_to_rate_limited() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return json_response(
            {"error": {"code": "rate_limit_exceeded", "type": "requests", "message": "too many"}},
            status_code=429,
        )

    # 再試行を無効化しておかないと、テストが SDK のバックオフで遅くなる。
    client = make_client(handler, max_retries=0)
    provider = OpenAIImagesProvider(client=client)

    with pytest.raises(ProviderError) as exc_info:
        await provider.execute(_run_request(params={"n": 1}), _noop_progress)
    assert exc_info.value.code == "rateLimited"


@run_async
async def test_authentication_error_401_maps_to_auth_error() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return json_response(
            {
                "error": {
                    "code": "invalid_api_key",
                    "type": "invalid_request_error",
                    "message": "Incorrect API key provided: sk-XXXX",
                }
            },
            status_code=401,
        )

    client = make_client(handler)
    provider = OpenAIImagesProvider(client=client)

    with pytest.raises(ProviderError) as exc_info:
        await provider.execute(_run_request(params={"n": 1}), _noop_progress)
    assert exc_info.value.code == "authError"
    # APIキー(のような文字列)をメッセージに含めない。固定の日本語メッセージのみを使う。
    assert "sk-XXXX" not in exc_info.value.message


def test_construction_without_key_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR-0012: キーが無くても構築(=起動)できる。失敗するのは実行時のみ。"""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    # 例外を送出しないことそのものがこのテストの確認内容。
    OpenAIImagesProvider()


def test_get_provider_openai_without_key_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    from app.providers import get_provider

    provider = get_provider("openai")
    assert provider.name == "openai"


@run_async
async def test_execute_without_key_raises_missing_api_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`client` を注入せず、環境変数にもファイルにもキーが無い状態で実行すると
    `missingApiKey` の `ProviderError` になる(実 API は呼ばれない)。"""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    provider = OpenAIImagesProvider()  # client を注入しない = 実行時に解決させる

    with pytest.raises(ProviderError) as exc_info:
        await provider.execute(_run_request(params={"n": 1}), _noop_progress)

    assert exc_info.value.code == "missingApiKey"


def test_resolve_client_uses_file_key_and_caches_by_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """クライアントの組み立てだけならネットワークを叩かないので、実行せずに検証できる。"""
    from app.domain.api_key import write_file_key

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    write_file_key(tmp_path, "sk-file-key-1")

    provider = OpenAIImagesProvider()
    client_1 = provider._resolve_client()
    assert client_1.api_key == "sk-file-key-1"

    # 同じキーなら同じインスタンスを再利用する。
    client_again = provider._resolve_client()
    assert client_again is client_1

    # 画面でキーを変更したら、次の実行で作り直す。
    write_file_key(tmp_path, "sk-file-key-2")
    client_2 = provider._resolve_client()
    assert client_2.api_key == "sk-file-key-2"
    assert client_2 is not client_1


def test_resolve_client_prefers_env_key_over_file_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.domain.api_key import write_file_key

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-key")
    write_file_key(tmp_path, "sk-file-key")

    provider = OpenAIImagesProvider()
    client = provider._resolve_client()
    assert client.api_key == "sk-env-key"


def test_capabilities_match_fake_provider_spec() -> None:
    from app.providers.fake import FakeProvider

    provider = OpenAIImagesProvider(client=make_client(lambda r: json_response({})))
    openai_caps = provider.capabilities()
    fake_caps = FakeProvider().capabilities()

    assert [m.model for m in openai_caps.models] == [m.model for m in fake_caps.models]
    assert openai_caps.provider == "openai"
    assert fake_caps.provider == "fake"
