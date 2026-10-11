"""`app/providers/sdwebui/client.py` のテスト(ADR-0038)。偽の WebUI だけを使う。"""

from __future__ import annotations

import asyncio

import pytest

from app.providers.sdwebui.client import (
    SdWebuiClient,
    SdWebuiError,
    decode_base64_image,
    parse_info,
    summarize_execution_error,
    summarize_validation_error,
)
from tests.sdwebui_fake import FAKE_PATH_ROOT, FakeSdWebui, png_bytes, unreachable_transport

URL = "http://127.0.0.1:7860"


def _client(fake: FakeSdWebui, credentials: tuple[str, str] | None = None) -> SdWebuiClient:
    return SdWebuiClient(URL, credentials=credentials, transport=fake.transport())


# -- check_available ------------------------------------------------------------------


@pytest.mark.parametrize("flavor", ["forge", "a1111"])
def test_check_available_detects_flavor(flavor: str) -> None:
    result = _client(FakeSdWebui(flavor=flavor)).check_available()
    assert result.available is True
    assert result.flavor == flavor
    assert result.reason is None


def test_check_available_404_means_api_not_enabled() -> None:
    result = _client(FakeSdWebui(api_enabled=False)).check_available()
    assert result.available is False
    assert result.reason == "apiNotEnabled"
    assert "--api" in (result.message or "")


def test_check_available_401_without_credentials() -> None:
    fake = FakeSdWebui(credentials=("user-x", "secret-pass-123"))
    result = _client(fake).check_available()
    assert result.reason == "unauthorized"
    wrong = _client(fake, ("user-x", "wrong")).check_available()
    assert wrong.reason == "unauthorized"
    ok = _client(fake, ("user-x", "secret-pass-123")).check_available()
    assert ok.available is True


def test_internal_progress_also_gets_credentials() -> None:
    fake = FakeSdWebui(credentials=("user-x", "secret-pass-123"))
    client = _client(fake, ("user-x", "secret-pass-123"))

    async def run() -> None:
        async with client.async_client() as http:
            await client.internal_progress(http, "gakei-1", -1)

    asyncio.run(run())
    progress_request = next(r for r in fake.requests if r.url.path == "/internal/progress")
    assert progress_request.headers["authorization"].startswith("Basic ")


def test_check_available_unreachable() -> None:
    client = SdWebuiClient(URL, transport=unreachable_transport())
    result = client.check_available()
    assert result.available is False
    assert result.reason == "unreachable"


def test_messages_do_not_contain_credentials() -> None:
    fake = FakeSdWebui(credentials=("user-x", "secret-pass-123"))
    result = _client(fake, ("user-x", "wrong-pass-456")).check_available()
    assert "wrong-pass-456" not in (result.message or "")
    assert "user-x" not in (result.message or "")


# -- fetch_catalog --------------------------------------------------------------------


def test_fetch_catalog_forge_lists_only_vae_folder_modules() -> None:
    catalog = _client(FakeSdWebui(flavor="forge")).fetch_catalog()
    assert catalog.flavor == "forge"
    assert [c.model_name for c in catalog.checkpoints] == ["model-a", "model-b"]
    assert catalog.checkpoints[1].title == "model-b.safetensors"
    assert catalog.samplers == ["Euler a", "Euler", "DPM++ 2M"]
    assert catalog.schedulers == ["automatic", "karras"]
    assert catalog.vaes == ["vae-a.safetensors", "vae-b.safetensors"]
    # パスは持たない
    assert FAKE_PATH_ROOT not in repr(catalog)


def test_fetch_catalog_a1111_uses_sd_vae() -> None:
    fake = FakeSdWebui(flavor="a1111")
    catalog = _client(fake).fetch_catalog()
    assert catalog.flavor == "a1111"
    assert catalog.vaes == ["vae-a.safetensors", "vae-b.safetensors"]
    assert any(r.url.path == "/sdapi/v1/sd-vae" for r in fake.requests)
    assert not any(r.url.path == "/sdapi/v1/sd-modules" for r in fake.requests)


def test_fetch_catalog_without_schedulers_endpoint() -> None:
    fake = FakeSdWebui()
    fake.schedulers = None
    catalog = _client(fake).fetch_catalog()
    assert catalog.schedulers == []


def test_fetch_catalog_unreachable_raises() -> None:
    with pytest.raises(SdWebuiError) as excinfo:
        SdWebuiClient(URL, transport=unreachable_transport()).fetch_catalog()
    assert excinfo.value.code == "sdwebuiUnavailable"


def test_fetch_catalog_401_raises() -> None:
    fake = FakeSdWebui(credentials=("u", "p"))
    with pytest.raises(SdWebuiError):
        _client(fake).fetch_catalog()


def test_refresh_checkpoints() -> None:
    fake = FakeSdWebui()
    _client(fake).refresh_checkpoints()
    assert fake.refresh_count == 1


# -- txt2img のエラー ------------------------------------------------------------------


def _txt2img(fake: FakeSdWebui, body: dict) -> dict:
    client = _client(fake)

    async def run() -> dict:
        async with client.async_client() as http:
            return await client.txt2img(http, body, timeout=10)

    return asyncio.run(run())


def test_txt2img_422_is_validation_without_input_echo() -> None:
    fake = FakeSdWebui()
    fake.txt2img_status = 422
    fake.txt2img_error_body = {
        "detail": [
            {
                "loc": ["body", "steps"],
                "msg": "Input should be a valid integer",
                "type": "int_parsing",
                "input": "QUFBQUFBQUFBQUFB" * 100,
            }
        ]
    }
    with pytest.raises(SdWebuiError) as excinfo:
        _txt2img(fake, {"prompt": "x"})
    assert excinfo.value.code == "sdwebuiValidation"
    assert "body.steps" in excinfo.value.message
    assert "QUFB" not in excinfo.value.message


def test_txt2img_500_is_execution_error() -> None:
    fake = FakeSdWebui()
    fake.txt2img_status = 500
    fake.txt2img_error_body = {"error": "OutOfMemoryError", "errors": "out of memory", "body": ""}
    with pytest.raises(SdWebuiError) as excinfo:
        _txt2img(fake, {"prompt": "x"})
    assert excinfo.value.code == "executionError"
    assert "OutOfMemoryError" in excinfo.value.message


def test_txt2img_unreachable() -> None:
    client = SdWebuiClient(URL, transport=unreachable_transport())

    async def run() -> None:
        async with client.async_client() as http:
            await client.txt2img(http, {"prompt": "x"}, timeout=1)

    with pytest.raises(SdWebuiError) as excinfo:
        asyncio.run(run())
    assert excinfo.value.code == "sdwebuiUnavailable"


def test_summaries_are_truncated() -> None:
    long = "x" * 5000
    assert len(summarize_execution_error({"error": long})) <= 500
    assert len(summarize_validation_error({"detail": long})) <= 500
    assert summarize_validation_error({}) != ""


# -- 進捗 ------------------------------------------------------------------------------


def test_internal_progress_404_returns_none() -> None:
    fake = FakeSdWebui()
    fake.internal_progress = False
    client = _client(fake)

    async def run():
        async with client.async_client() as http:
            return await client.internal_progress(http, "gakei-1", -1)

    assert asyncio.run(run()) is None


def test_internal_progress_decodes_live_preview() -> None:
    fake = FakeSdWebui()
    fake.live_preview = png_bytes()
    client = _client(fake)

    async def run():
        async with client.async_client() as http:
            return await client.internal_progress(http, "gakei-1", -1)

    snapshot = asyncio.run(run())
    assert snapshot is not None
    assert snapshot.progress == 0.25
    assert snapshot.live_preview == png_bytes()
    assert fake.progress_bodies[0] == {
        "id_task": "gakei-1",
        "id_live_preview": -1,
        "live_preview": True,
    }


def test_public_progress_reads_steps() -> None:
    fake = FakeSdWebui()
    client = _client(fake)

    async def run():
        async with client.async_client() as http:
            return await client.public_progress(http)

    snapshot = asyncio.run(run())
    assert snapshot.step == 5
    assert snapshot.steps == 20


def test_decode_and_parse_helpers() -> None:
    assert decode_base64_image("data:image/png;base64,AAEC") == b"\x00\x01\x02"
    assert decode_base64_image(None) is None
    assert parse_info('{"seed": 1}') == {"seed": 1}
    assert parse_info("not json") == {}
