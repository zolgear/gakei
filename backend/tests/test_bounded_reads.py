"""上限までだけ読む(`app/http_body_limit.py`)と、それを使う箇所のテスト。

外への要求の応答(ComfyUI の出力画像、モデルのダウンロード、Webhook)と、受け取ったファイル
(画像の取り込み、アバター)を、上限を超えたら読むのをやめて断る。実ネットワークには出ない。
"""

from __future__ import annotations

import asyncio
import hashlib
import io
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.domain.assets import MAX_UPLOAD_BYTES
from app.http_body_limit import BodyTooLarge, aread_limited, read_file_limited
from app.i18n import t
from app.model_store.downloader import DownloadError, RemoteFile, download_file
from app.mods import webhook
from app.providers.comfyui import client as comfy_client
from app.providers.comfyui.client import ComfyUIClient, ComfyUIError, OutputImageRef
from tests.conftest import login_as, make_png_bytes


class _CountingStream(httpx.AsyncByteStream):
    """返した量を数える本文(打ち切ったあとに読み続けていないかを確かめる)。"""

    def __init__(self, chunks: int, chunk_size: int = 1024) -> None:
        self.chunks = chunks
        self.chunk_size = chunk_size
        self.sent = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for _ in range(self.chunks):
            self.sent += self.chunk_size
            yield b"x" * self.chunk_size


def _stream_response(
    handler_body: httpx.AsyncByteStream | bytes, headers: dict[str, str] | None = None
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(handler_body, bytes):
            return httpx.Response(200, content=handler_body, headers=headers)
        return httpx.Response(200, stream=handler_body, headers=headers)

    return httpx.MockTransport(handler)


async def _read(transport: httpx.MockTransport, max_bytes: int) -> bytes:
    async with httpx.AsyncClient(transport=transport) as http:
        async with http.stream("GET", "http://127.0.0.1/x") as response:
            return await aread_limited(response, max_bytes)


# -- 共通の関数 ----------------------------------------------------------------------


def test_aread_limited_reads_up_to_limit() -> None:
    assert asyncio.run(_read(_stream_response(b"a" * 100), 100)) == b"a" * 100


def test_aread_limited_rejects_declared_length_before_reading() -> None:
    stream = _CountingStream(chunks=10)
    transport = _stream_response(stream, headers={"Content-Length": "10240"})
    with pytest.raises(BodyTooLarge):
        asyncio.run(_read(transport, 1000))
    assert stream.sent == 0


def test_aread_limited_stops_while_reading_without_length() -> None:
    stream = _CountingStream(chunks=1000)
    with pytest.raises(BodyTooLarge):
        asyncio.run(_read(_stream_response(stream), 4096))
    # 上限を少し超えたところで打ち切る(全部は読まない)
    assert stream.sent <= 4096 + 1024


def test_read_file_limited() -> None:
    def too_large() -> Exception:
        return HTTPException(status_code=413)

    assert read_file_limited(io.BytesIO(b"a" * 10), 10, too_large) == b"a" * 10
    source = io.BytesIO(b"a" * 100_000)
    with pytest.raises(HTTPException):
        read_file_limited(source, 10, too_large)
    # 上限より 1 バイトだけ多く読んだところでやめる
    assert source.tell() == 11


# -- ComfyUI の /view ---------------------------------------------------------------


def test_view_limit_matches_upload_limit() -> None:
    assert comfy_client.VIEW_MAX_BYTES == MAX_UPLOAD_BYTES


def _view(transport: httpx.MockTransport) -> bytes:
    async def go() -> bytes:
        client = ComfyUIClient("http://127.0.0.1:8188", http=httpx.AsyncClient(transport=transport))
        try:
            return await client.view(
                OutputImageRef(node_id="9", filename="out.png", subfolder="", type="output")
            )
        finally:
            await client.aclose()

    return asyncio.run(go())


def test_view_rejects_output_over_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(comfy_client, "VIEW_MAX_BYTES", 4096)
    assert _view(_stream_response(b"a" * 4096)) == b"a" * 4096

    stream = _CountingStream(chunks=1000)
    with pytest.raises(ComfyUIError) as excinfo:
        _view(_stream_response(stream))
    assert excinfo.value.code == "comfyuiUnavailable"
    assert stream.sent <= 4096 + 1024

    stream = _CountingStream(chunks=10)
    with pytest.raises(ComfyUIError):
        _view(_stream_response(stream, headers={"Content-Length": "10240"}))
    assert stream.sent == 0


# -- モデルのダウンロード -------------------------------------------------------------


def _download(tmp_path: Path, expected: RemoteFile, transport: httpx.MockTransport) -> None:
    async def go() -> None:
        async with httpx.AsyncClient(transport=transport) as http:
            await download_file(
                http, "https://huggingface.co/x", expected, tmp_path, lambda _n: None
            )

    asyncio.run(go())


def test_download_rejects_body_over_catalog_size(tmp_path: Path) -> None:
    content = b"m" * 1024
    expected = RemoteFile("model.onnx", len(content), hashlib.sha256(content).hexdigest())
    _download(tmp_path, expected, _stream_response(content))
    assert (tmp_path / "model.onnx").read_bytes() == content

    for transport in (
        # 宣言された大きさがカタログより大きい(読む前に断る)
        _stream_response(_CountingStream(chunks=10), headers={"Content-Length": "10240"}),
        # 宣言が無く、読みながら超える
        _stream_response(_CountingStream(chunks=100)),
    ):
        target = tmp_path / "other"
        target.mkdir(exist_ok=True)
        with pytest.raises(DownloadError) as excinfo:
            _download(target, expected, transport)
        assert str(excinfo.value) == t("modelStore.download.tooLarge", file="model.onnx")
        assert not (target / "model.onnx").exists()
        assert not list(target.glob(".*.part"))


# -- Webhook の応答 -----------------------------------------------------------------


def test_webhook_response_over_limit_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(webhook, "_RESPONSE_MAX_BYTES", 4096)
    request = webhook.build_request("http://127.0.0.1:9/hook", "secret", {"event": "x"})

    def handler_for(stream: httpx.AsyncByteStream) -> httpx.MockTransport:
        return httpx.MockTransport(lambda _r: httpx.Response(200, stream=stream))

    stream = _CountingStream(chunks=1000)
    with pytest.raises(webhook.WebhookError) as excinfo:
        asyncio.run(webhook.send(request, timeout_seconds=5, transport=handler_for(stream)))
    assert str(excinfo.value) == "response too large"
    assert stream.sent <= 4096 + 1024

    ok = httpx.MockTransport(lambda _r: httpx.Response(200, json={"decision": "allow"}))
    result = asyncio.run(webhook.send(request, timeout_seconds=5, transport=ok))
    assert result.body == {"decision": "allow"}


# -- 受け取ったファイル -------------------------------------------------------------


def test_asset_upload_stops_at_limit(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.api.assets.MAX_UPLOAD_BYTES", 1000)
    data = make_png_bytes() + b"\0" * 2000
    response = client.post(
        "/api/assets",
        files={"file": ("big.png", io.BytesIO(data), "image/png")},
        data={"kind": "upload"},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == t("assets.uploadTooLarge")


def test_avatar_upload_stops_at_limit(
    client_oidc: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    login_as(client_oidc, "avatar-limit@example.com")
    monkeypatch.setattr("app.api.users.MAX_AVATAR_BYTES", 1000)
    data = make_png_bytes() + b"\0" * 2000
    response = client_oidc.post(
        "/api/users/me/avatar", files={"file": ("a.png", io.BytesIO(data), "image/png")}
    )
    assert response.status_code == 413
    assert response.json()["detail"] == t("users.avatar.tooLarge")
