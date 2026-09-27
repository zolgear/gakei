"""ComfyUI の偽サーバー(`app/providers/comfyui/client.py` のテスト用)。

HTTP は `httpx.MockTransport` で状態を持つ `FakeComfyUI` が応答を作る。
WebSocket は、指定したフレームの列をそのまま返す `fake_ws_connect` を使う。

`ComfyUIProvider` のテストでも、次のようにそのまま使い回せる想定。

HTTP::

    fake = FakeComfyUI()
    fake.queue_success(prompt_id="run-1")                 # 既定でも成功するが、明示してもよい
    fake.set_history("run-1", {"outputs": {"9": {"images": [
        {"filename": "out.png", "subfolder": "", "type": "output"},
    ]}}})
    fake.add_output_file("out.png", "", "output", b"...png bytes...")

    client = ComfyUIClient("http://127.0.0.1:8188", http=fake.make_async_client())
    prompt_id = await client.queue_prompt({...}, "client-1")
    entry = await client.history(prompt_id)

失敗させたいときは `fake.queue_error(...)` / `fake.fail_upload(...)` を呼んでから実行する。
接続そのものができない状態を再現したいときは、`FakeComfyUI` の代わりに
`unavailable_async_client()` を渡す。

WebSocket::

    ws_connect = fake_ws_connect([
        WsMessage("status", {"exec_info": {"queue_remaining": 1}}),
        encode_preview_frame(b"...jpeg bytes...", image_type=1),
        WsMessage("execution_success", {"prompt_id": "run-1"}),
    ])
    client = ComfyUIClient("http://127.0.0.1:8188", ws_connect=ws_connect)

途中切断を再現したいときは、列の途中に例外インスタンスを混ぜる
(`fake_ws_connect([WsMessage(...), ConnectionError("boom")])`)。
接続自体ができない状態を再現したいときは `unavailable_ws_connect()` を使う。
"""

from __future__ import annotations

import json
import re
import struct
from collections.abc import AsyncIterator, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.providers.comfyui.client import WsConnect, WsMessage

# -- HTTP ---------------------------------------------------------------------


@dataclass
class _PromptResult:
    kind: str  # "success" | "error"
    prompt_id: str | None = None
    status_code: int = 400
    extra: dict[str, Any] = field(default_factory=dict)
    error_body: dict[str, Any] = field(default_factory=dict)


class FakeComfyUI:
    """状態を持つ偽の ComfyUI HTTP サーバー。

    `make_async_client()` で `httpx.AsyncClient` を作る。
    """

    def __init__(self) -> None:
        self.system_stats_body: dict[str, Any] = {
            "system": {"comfyui_version": "0.0.0-test", "os": "test"},
            "devices": [],
        }
        self.object_info_responses: dict[str, dict[str, Any]] = {}

        self.uploads: list[dict[str, Any]] = []
        self._upload_status_code = 200
        self._upload_response_body: dict[str, Any] | None = None

        self._prompt_result = _PromptResult(kind="success", prompt_id="prompt-1")
        self.queued_prompts: list[dict[str, Any]] = []

        self.history: dict[str, dict[str, Any]] = {}
        self.output_files: dict[tuple[str, str, str], bytes] = {}

    # -- 応答の設定 ------------------------------------------------------------

    def queue_success(
        self, prompt_id: str = "prompt-1", *, node_errors: dict[str, Any] | None = None
    ) -> None:
        self._prompt_result = _PromptResult(
            kind="success", prompt_id=prompt_id, extra={"node_errors": node_errors or {}}
        )

    def queue_error(
        self,
        *,
        status_code: int = 400,
        error: dict[str, Any] | None = None,
        node_errors: dict[str, Any] | None = None,
    ) -> None:
        body = {
            "error": error
            or {
                "type": "prompt_outputs_failed_validation",
                "message": "Prompt outputs failed validation",
                "details": "",
                "extra_info": {},
            },
            "node_errors": node_errors or {},
        }
        self._prompt_result = _PromptResult(kind="error", status_code=status_code, error_body=body)

    def fail_upload(self, status_code: int = 400, body: dict[str, Any] | None = None) -> None:
        self._upload_status_code = status_code
        self._upload_response_body = body or {}

    def set_history(self, prompt_id: str, entry: dict[str, Any]) -> None:
        self.history[prompt_id] = entry

    def add_output_file(self, filename: str, subfolder: str, type_: str, data: bytes) -> None:
        self.output_files[(filename, subfolder, type_)] = data

    # -- httpx との橋渡し --------------------------------------------------------

    def make_async_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/system_stats":
            return httpx.Response(200, json=self.system_stats_body)
        if path == "/object_info":
            return httpx.Response(200, json=self.object_info_responses)
        if path.startswith("/object_info/"):
            node_class = path.removeprefix("/object_info/")
            body = (
                {node_class: self.object_info_responses[node_class]}
                if node_class in self.object_info_responses
                else {}
            )
            return httpx.Response(200, json=body)
        if path == "/upload/image":
            return self._handle_upload(request)
        if path == "/prompt":
            return self._handle_prompt(request)
        if path.startswith("/history/"):
            prompt_id = path.removeprefix("/history/")
            entry = self.history.get(prompt_id)
            return httpx.Response(200, json={prompt_id: entry} if entry is not None else {})
        if path == "/view":
            return self._handle_view(request)
        return httpx.Response(404, json={"error": f"fake ComfyUI: unknown path {path}"})

    def _handle_upload(self, request: httpx.Request) -> httpx.Response:
        fields = _parse_multipart(request)
        self.uploads.append(fields)
        if self._upload_status_code != 200:
            return httpx.Response(self._upload_status_code, json=self._upload_response_body or {})
        filename = fields.get("image_filename", "upload.png")
        body = self._upload_response_body or {"name": filename, "subfolder": "", "type": "input"}
        return httpx.Response(200, json=body)

    def _handle_prompt(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        self.queued_prompts.append(payload)
        result = self._prompt_result
        if result.kind == "success":
            body = {"prompt_id": result.prompt_id, "number": len(self.queued_prompts)}
            body.update(result.extra)
            return httpx.Response(200, json=body)
        return httpx.Response(result.status_code, json=result.error_body)

    def _handle_view(self, request: httpx.Request) -> httpx.Response:
        params = request.url.params
        key = (params.get("filename", ""), params.get("subfolder", ""), params.get("type", ""))
        data = self.output_files.get(key)
        if data is None:
            return httpx.Response(404, content=b"not found")
        return httpx.Response(200, content=data, headers={"content-type": "image/png"})


def unavailable_async_client() -> httpx.AsyncClient:
    """すべてのリクエストで接続失敗を再現するトランスポート。"""

    def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("fake ComfyUI: connection refused", request=request)

    return httpx.AsyncClient(transport=httpx.MockTransport(_handler))


def unavailable_ws_connect() -> WsConnect:
    """WebSocket の接続自体ができない状態を再現する偽の `ws_connect`。"""

    def _connect(url: str) -> AbstractAsyncContextManager[AsyncIterator[str | bytes]]:
        raise OSError("fake ComfyUI: connection refused")

    return _connect


_BOUNDARY_RE = re.compile(r'boundary="?([^";]+)"?')
_DISPOSITION_PARAM_RE_TEMPLATE = r'{param}="([^"]*)"'


def _parse_multipart(request: httpx.Request) -> dict[str, Any]:
    """テスト用の簡易 multipart パーサー(httpx が組み立てた本文だけを想定)。"""
    content_type = request.headers.get("content-type", "")
    match = _BOUNDARY_RE.search(content_type)
    if match is None:
        return {}
    boundary = match.group(1).encode()

    fields: dict[str, Any] = {}
    for raw_part in request.content.split(b"--" + boundary):
        part = raw_part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        header_blob, sep, content = part.partition(b"\r\n\r\n")
        if not sep:
            continue
        content = content[:-2] if content.endswith(b"\r\n") else content
        headers = _parse_part_headers(header_blob)
        disposition = headers.get("content-disposition", "")
        name = _extract_disposition_param(disposition, "name")
        if name is None:
            continue
        filename = _extract_disposition_param(disposition, "filename")
        if filename is not None:
            fields[f"{name}_filename"] = filename
            fields[f"{name}_bytes"] = content
        else:
            fields[name] = content.decode("utf-8")
    return fields


def _parse_part_headers(blob: bytes) -> dict[str, str]:
    headers: dict[str, str] = {}
    for line in blob.split(b"\r\n"):
        if not line:
            continue
        key, _, value = line.decode("utf-8").partition(":")
        headers[key.strip().lower()] = value.strip()
    return headers


def _extract_disposition_param(header_value: str, param: str) -> str | None:
    match = re.search(_DISPOSITION_PARAM_RE_TEMPLATE.format(param=param), header_value)
    return match.group(1) if match else None


# -- WebSocket ------------------------------------------------------------------

WsFrame = str | bytes | WsMessage | BaseException


def encode_text_frame(msg_type: str, data: dict[str, Any]) -> str:
    """`{"type": ..., "data": ...}` の JSON テキストフレームを組み立てる。"""
    return json.dumps({"type": msg_type, "data": data})


def encode_preview_frame(image_bytes: bytes, *, image_type: int = 2) -> bytes:
    """`PREVIEW_IMAGE`(=1)のバイナリフレーム。`image_type`: 1=JPEG, 2=PNG。"""
    return struct.pack(">II", 1, image_type) + image_bytes


def encode_preview_with_metadata_frame(image_bytes: bytes, metadata: dict[str, Any]) -> bytes:
    """`PREVIEW_IMAGE_WITH_METADATA`(=4)のバイナリフレーム。"""
    metadata_json = json.dumps(metadata).encode("utf-8")
    return struct.pack(">II", 4, len(metadata_json)) + metadata_json + image_bytes


def fake_ws_connect(frames: Sequence[WsFrame]) -> WsConnect:
    """フレームの列を順に返す偽の `ws_connect`。

    列の要素は `str`(テキストフレームをそのまま送る)、`bytes`(バイナリフレームをそのまま送る)、
    `WsMessage`(JSON にエンコードして送る)、または `BaseException` のインスタンス
    (その時点で送出し、途中切断を再現する)のいずれか。
    """

    def _connect(url: str) -> AbstractAsyncContextManager[AsyncIterator[str | bytes]]:
        return _FakeWsConnection(frames)

    return _connect


class _FakeWsConnection:
    def __init__(self, frames: Sequence[WsFrame]) -> None:
        self._frames = list(frames)

    async def __aenter__(self) -> AsyncIterator[str | bytes]:
        return self._iterate()

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def _iterate(self) -> AsyncIterator[str | bytes]:
        for frame in self._frames:
            if isinstance(frame, BaseException):
                raise frame
            if isinstance(frame, WsMessage):
                yield encode_text_frame(frame.type, frame.data)
            elif isinstance(frame, str | bytes):
                yield frame
            else:
                raise TypeError(f"fake_ws_connect: 未対応のフレームです: {frame!r}")
