"""ADR-0013: ローカル ComfyUI と通信するだけの低レベルクライアント。

GAKEI の DB、Run、プロバイダーの仕組み(`app/providers/base.py` など)には一切依存しない。
単体で完結したモジュールで、`ComfyUIProvider` がこれを使って実行する。

参照した一次情報は ComfyUI の `server.py` / `protocol.py` / `execution.py`
(読むだけ、変更していない)。プロトコルの要点(ADR-0013 5, 6 節も参照):

- `POST /prompt`: 成功時は `{"prompt_id", "number", "node_errors"}`。
  グラフが不正だと 400 で `{"error", "node_errors"}`(`node_errors` はノード ID をキーに
  `{"errors": [...], "class_type": ..., "dependent_outputs": [...]}`)。
- `GET /history/{prompt_id}`: 見つかれば `{prompt_id: {"prompt", "outputs", "status", ...}}`、
  無ければ `{}`。`outputs` はノード ID をキーに
  `{"images": [{"filename", "subfolder", "type"}, ...]}`。
- `GET /view?filename=&subfolder=&type=`: 出力画像のバイト列。
- `POST /upload/image`: multipart(`image` ファイル、`overwrite`)。成功で
  `{"name", "subfolder", "type"}`。
- `GET /ws?clientId=...`: テキストフレームは `{"type": ..., "data": {...}}`。
  バイナリフレームは先頭4バイト(big-endian)がイベント種別
  (`protocol.py` の `BinaryEventTypes`: 1=PREVIEW_IMAGE, 4=PREVIEW_IMAGE_WITH_METADATA)。
  PREVIEW_IMAGE は続く4バイトが画像種別(1=JPEG, 2=PNG)。
  PREVIEW_IMAGE_WITH_METADATA は続く4バイトがメタデータ(JSON, UTF-8)の長さ、
  メタデータ本体、画像データの順(`send_image_with_metadata` / `encode_bytes` 参照)。
"""

from __future__ import annotations

import json
import struct
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx
from websockets.asyncio.client import connect as _ws_connect_default

from app.i18n import t

# websockets の既定(1 MiB)だと大きなプレビュー画像を受け取れないため広げる。
_WS_MAX_SIZE = 32 * 1024 * 1024

WsConnect = Callable[[str], AbstractAsyncContextManager[AsyncIterator["str | bytes"]]]


class ComfyUIError(Exception):
    """ComfyUI クライアントの失敗。`code` は ADR-0013 の `run.error_code` にそのまま使う値。

    code: comfyuiUnavailable / comfyuiValidation / comfyuiUpload / executionError /
          comfyuiNoOutput / timeout
    (executionError と comfyuiNoOutput、timeout はこのクライアントからは送出しない。
     WebSocket のイベント解釈や実行時間の管理は呼び出し側の責務。)
    """

    def __init__(self, code: str, message: str, detail: Any = None) -> None:
        self.code = code
        self.message = message
        self.detail = detail
        super().__init__(message)


@dataclass
class WsMessage:
    """テキストのフレーム(`{"type": ..., "data": {...}}`)。"""

    type: str
    data: dict[str, Any]


@dataclass
class WsPreview:
    """バイナリのフレーム(PREVIEW_IMAGE / PREVIEW_IMAGE_WITH_METADATA)。"""

    mime: str
    data: bytes
    metadata: dict[str, Any] | None = None


WsEvent = WsMessage | WsPreview


@dataclass
class OutputImageRef:
    """`/view` で取得するための出力画像の参照。"""

    node_id: str
    filename: str
    subfolder: str
    type: str


def parse_binary_frame(frame: bytes) -> WsPreview | None:
    """WebSocket のバイナリフレームを解釈する。未対応の種別・壊れたフレームは None。"""
    if len(frame) < 4:
        return None
    (event_type,) = struct.unpack(">I", frame[:4])
    body = frame[4:]

    if event_type == 1:  # PREVIEW_IMAGE
        if len(body) < 4:
            return None
        (image_type_num,) = struct.unpack(">I", body[:4])
        mime = {1: "image/jpeg", 2: "image/png"}.get(image_type_num)
        if mime is None:
            return None
        return WsPreview(mime=mime, data=body[4:])

    if event_type == 4:  # PREVIEW_IMAGE_WITH_METADATA
        if len(body) < 4:
            return None
        (metadata_length,) = struct.unpack(">I", body[:4])
        metadata_json = body[4 : 4 + metadata_length]
        image_bytes = body[4 + metadata_length :]
        try:
            metadata = json.loads(metadata_json.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return None
        if not isinstance(metadata, dict):
            return None
        mime = metadata.get("image_type") or "image/png"
        return WsPreview(mime=mime, data=image_bytes, metadata=metadata)

    return None


def summarize_node_errors(body: dict[str, Any]) -> str:
    """`/prompt` の 400 応答を短い日本語の要約にする。"""
    parts: list[str] = []

    error = body.get("error")
    if isinstance(error, dict):
        message = error.get("message") or error.get("type") or ""
        details = error.get("details")
        if details and details != message:
            message = f"{message}: {details}" if message else str(details)
        if message:
            parts.append(str(message))

    node_errors = body.get("node_errors")
    if isinstance(node_errors, dict):
        for node_id, info in node_errors.items():
            if not isinstance(info, dict):
                continue
            class_type = info.get("class_type", "?")
            for err in info.get("errors") or []:
                if not isinstance(err, dict):
                    continue
                extra_info = err.get("extra_info") or {}
                input_name = extra_info.get("input_name") if isinstance(extra_info, dict) else None
                message = err.get("message") or err.get("type") or t("comfyui.client.genericError")
                if input_name:
                    parts.append(
                        t(
                            "comfyui.client.nodeInputError",
                            node=node_id,
                            classType=class_type,
                            inputName=input_name,
                            message=message,
                        )
                    )
                else:
                    parts.append(
                        t(
                            "comfyui.client.nodeError",
                            node=node_id,
                            classType=class_type,
                            message=message,
                        )
                    )

    if not parts:
        return t("comfyui.client.rejectedNoDetails")

    summary = " / ".join(parts)
    max_length = 800
    if len(summary) > max_length:
        summary = summary[: max_length - 1] + "…"
    return summary


def collect_output_images(
    history_entry: dict[str, Any], output_node_ids: list[str]
) -> list[OutputImageRef]:
    """`output_node_ids` の順、各ノードの中は `images` の順。`type == "temp"` は除く。"""
    outputs = history_entry.get("outputs")
    if not isinstance(outputs, dict):
        return []

    refs: list[OutputImageRef] = []
    for node_id in output_node_ids:
        node_output = outputs.get(node_id)
        if not isinstance(node_output, dict):
            continue
        images = node_output.get("images")
        if not isinstance(images, list):
            continue
        for image in images:
            if not isinstance(image, dict):
                continue
            if image.get("type") == "temp":
                continue
            refs.append(
                OutputImageRef(
                    node_id=node_id,
                    filename=image.get("filename", ""),
                    subfolder=image.get("subfolder", ""),
                    type=image.get("type", "output"),
                )
            )
    return refs


def sanitize_text(text: str) -> str:
    """DB に書けない文字を取り除く(ADR-0030 2026-10-01 改訂)。PostgreSQL の JSONB は
    `\u0000` を受け付けず、対になっていないサロゲートは UTF-8 にできない。どちらも
    完了時の commit を失敗させ、画像はできているのに Run が failed になるので、NUL は
    取り除き、対になっていないサロゲートは置き換える。"""
    return text.replace("\x00", "").encode("utf-8", "replace").decode("utf-8")


def collect_output_texts(history_entry: dict[str, Any], node_id: str) -> str | None:
    """`outputs[node_id]["text"]` を1つの文字列にする(ADR-0030 2章)。

    `text` は文字列のリスト(`PreviewAny` など)。文字列単体でも受ける。リストの要素は
    改行でつなぐ。NUL などの DB に書けない文字は `sanitize_text` で除く。ノードの出力が
    無い、`text` が無い、空白を除くと空になるときは None(空文字は記録しない)。
    """
    outputs = history_entry.get("outputs")
    if not isinstance(outputs, dict):
        return None
    node_output = outputs.get(node_id)
    if not isinstance(node_output, dict):
        return None
    text = node_output.get("text")
    if isinstance(text, list):
        parts = [item for item in text if isinstance(item, str)]
        text = "\n".join(parts) if parts else None
    if not isinstance(text, str):
        return None
    text = sanitize_text(text)
    if not text.strip():
        return None
    return text


def check_available(
    base_url: str, timeout: float = 1.0
) -> tuple[bool, str | None, dict[str, Any] | None]:
    """同期版の `/system_stats`。capabilities の `availability()` 用。"""
    url = _join(base_url, "/system_stats")
    try:
        response = httpx.get(url, timeout=timeout)
    except httpx.TimeoutException:
        return (False, t("comfyui.client.connectTimeout"), None)
    except httpx.TransportError as exc:
        return (False, t("comfyui.client.connectFailed", error=exc), None)

    if response.status_code != 200:
        return (
            False,
            t("comfyui.client.unexpectedResponse", status=response.status_code),
            None,
        )
    try:
        body = response.json()
    except ValueError:
        return (False, t("comfyui.client.parseFailed"), None)
    if not isinstance(body, dict):
        return (False, t("comfyui.client.parseFailed"), None)
    return True, None, body


class ComfyUIClient:
    """ComfyUI の HTTP / WebSocket API を薄く包んだクライアント。"""

    def __init__(
        self,
        base_url: str,
        *,
        http: httpx.AsyncClient | None = None,
        ws_connect: WsConnect | None = None,
        request_timeout: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._owns_http = http is None
        self._http = http or httpx.AsyncClient(timeout=request_timeout)
        self._ws_connect = ws_connect

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def system_stats(self) -> dict[str, Any]:
        return await self._get_json("/system_stats")

    async def object_info(self, node_class: str | None = None) -> dict[str, Any]:
        path = f"/object_info/{node_class}" if node_class else "/object_info"
        return await self._get_json(path)

    async def upload_image(self, data: bytes, filename: str, *, overwrite: bool = True) -> str:
        files = {"image": (filename, data)}
        form = {"overwrite": "true" if overwrite else "false"}
        response = await self._send(
            "POST",
            "/upload/image",
            unavailable_hint_key="comfyui.client.hintUpload",
            data=form,
            files=files,
        )
        if response.status_code != 200:
            raise ComfyUIError(
                "comfyuiUpload",
                t("comfyui.client.uploadFailed", status=response.status_code),
                detail=_safe_body(response),
            )
        body = response.json()
        name = body.get("name", filename)
        subfolder = body.get("subfolder") or ""
        return f"{subfolder}/{name}" if subfolder else name

    async def queue_prompt(self, prompt: dict[str, Any], client_id: str) -> str:
        payload = {"prompt": prompt, "client_id": client_id}
        response = await self._send(
            "POST",
            "/prompt",
            unavailable_hint_key="comfyui.client.hintSubmit",
            json=payload,
        )
        if response.status_code == 400:
            body = _safe_json(response)
            raise ComfyUIError("comfyuiValidation", summarize_node_errors(body), detail=body)
        if response.status_code != 200:
            raise ComfyUIError(
                "comfyuiUnavailable",
                t("comfyui.client.unexpectedResponse", status=response.status_code),
                detail=_safe_body(response),
            )
        body = response.json()
        prompt_id = body.get("prompt_id")
        if not prompt_id:
            raise ComfyUIError(
                "comfyuiUnavailable",
                t("comfyui.client.missingPromptId"),
                detail=body,
            )
        return prompt_id

    async def history(self, prompt_id: str) -> dict[str, Any] | None:
        body = await self._get_json(f"/history/{prompt_id}")
        if not isinstance(body, dict):
            return None
        return body.get(prompt_id)

    async def view(self, ref: OutputImageRef) -> bytes:
        params = {"filename": ref.filename, "subfolder": ref.subfolder, "type": ref.type}
        response = await self._send(
            "GET",
            "/view",
            unavailable_hint_key="comfyui.client.hintFetchOutput",
            params=params,
        )
        if response.status_code != 200:
            raise ComfyUIError(
                "comfyuiUnavailable",
                t("comfyui.client.fetchOutputFailed", status=response.status_code),
                detail=_safe_body(response),
            )
        return response.content

    def ws_events(self, client_id: str) -> AbstractAsyncContextManager[AsyncIterator[WsEvent]]:
        """進捗とプレビューを受け取る WebSocket イベントの非同期イテレーター。

        使い方::

            async with client.ws_events(client_id) as events:
                async for event in events:
                    ...

        接続できない、または途中で切断したときは `ComfyUIError("comfyuiUnavailable", ...)` を
        送出する(呼び出し側で `/history` のポーリングに切り替える。ADR-0013 5節)。
        """
        return _ws_events(self._base_url, client_id, self._ws_connect)

    async def _get_json(self, path: str) -> dict[str, Any]:
        response = await self._send("GET", path, unavailable_hint_key="comfyui.client.hintContact")
        if response.status_code != 200:
            raise ComfyUIError(
                "comfyuiUnavailable",
                t("comfyui.client.unexpectedResponse", status=response.status_code),
                detail=_safe_body(response),
            )
        return response.json()

    async def _send(
        self, method: str, path: str, *, unavailable_hint_key: str, **kwargs: Any
    ) -> httpx.Response:
        url = _join(self._base_url, path)
        try:
            return await self._http.request(method, url, **kwargs)
        except httpx.TimeoutException as exc:
            raise ComfyUIError(
                "comfyuiUnavailable",
                t("comfyui.client.timedOutWhile", hint=t(unavailable_hint_key)),
            ) from exc
        except httpx.TransportError as exc:
            raise ComfyUIError(
                "comfyuiUnavailable",
                t("comfyui.client.connectFailed", error=exc),
            ) from exc


def _join(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}{path}"


def _to_ws_base(base_url: str) -> str:
    if base_url.startswith("https://"):
        return "wss://" + base_url[len("https://") :]
    if base_url.startswith("http://"):
        return "ws://" + base_url[len("http://") :]
    return base_url


def _ws_url(base_url: str, client_id: str) -> str:
    return f"{_to_ws_base(base_url).rstrip('/')}/ws?clientId={quote(client_id, safe='')}"


def _default_ws_connect(url: str) -> AbstractAsyncContextManager[AsyncIterator[str | bytes]]:
    return _ws_connect_default(url, max_size=_WS_MAX_SIZE)


@asynccontextmanager
async def _ws_events(
    base_url: str, client_id: str, ws_connect: WsConnect | None
) -> AsyncIterator[AsyncIterator[WsEvent]]:
    connect = ws_connect or _default_ws_connect
    url = _ws_url(base_url, client_id)
    async with AsyncExitStack() as stack:
        # 変換するのは接続時の失敗だけ。`async with` の本体で呼び出し側が投げた例外
        # (タイムアウトなど)は書き換えずにそのまま通す。
        try:
            stream = await stack.enter_async_context(connect(url))
        except Exception as exc:
            raise ComfyUIError(
                "comfyuiUnavailable",
                t("comfyui.client.wsConnectFailed", error=exc),
            ) from exc
        yield _consume(stream)


async def _consume(stream: AsyncIterator[str | bytes]) -> AsyncIterator[WsEvent]:
    try:
        async for frame in stream:
            if isinstance(frame, bytes | bytearray):
                preview = parse_binary_frame(bytes(frame))
                if preview is not None:
                    yield preview
                continue
            try:
                payload = json.loads(frame)
            except ValueError:
                continue
            if not isinstance(payload, dict):
                continue
            yield WsMessage(type=payload.get("type", ""), data=payload.get("data") or {})
    except ComfyUIError:
        raise
    except Exception as exc:  # 途中切断をまとめて comfyuiUnavailable に変換する
        raise ComfyUIError(
            "comfyuiUnavailable",
            t("comfyui.client.wsLost", error=exc),
        ) from exc


def _safe_json(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _safe_body(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text
