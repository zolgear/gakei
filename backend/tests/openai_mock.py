"""OpenAI プロバイダーのテスト用ヘルパー。

実 API は一切呼ばない。`httpx2.MockTransport` を `AsyncOpenAI(http_client=...)` に差し込み、
送信内容(JSON ボディ / multipart)をそのまま検証できるようにする。
"""

from __future__ import annotations

import asyncio
import base64
import email
import email.policy
import functools
import json
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from email.message import Message
from typing import Any

import httpx2
from openai import AsyncOpenAI


def run_async[F: Callable[..., Coroutine[Any, Any, None]]](func: F) -> Callable[..., None]:
    """`async def test_...` を同期テストとして実行するデコレータ。

    `pytest-asyncio` はこのプロジェクトの依存に入れない方針(依存追加はステップ1で `uv add` 済みの
    ものに限定)のため、`asyncio.run` で素朴に実行する。
    """

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> None:
        asyncio.run(func(*args, **kwargs))

    return wrapper


def make_b64(marker: bytes = b"fake-image-bytes") -> str:
    return base64.b64encode(marker).decode("ascii")


def make_client(
    handler: Callable[[httpx2.Request], httpx2.Response], *, max_retries: int = 0
) -> AsyncOpenAI:
    """MockTransport を仕込んだ `AsyncOpenAI` を作る。

    `max_retries=0` にしておくと、429 のテストで SDK の再試行を待たずに即座に例外を確認できる。
    """
    transport = httpx2.MockTransport(handler)
    http_client = httpx2.AsyncClient(transport=transport)
    return AsyncOpenAI(api_key="test-key", http_client=http_client, max_retries=max_retries)


@dataclass
class CapturedRequest:
    """テストの assert で使いやすい形に整えたリクエストのスナップショット。"""

    method: str
    path: str
    json_body: dict | None
    parts: list[Message] = field(default_factory=list)

    def part_names(self, name: str) -> list[Message]:
        return [p for p in self.parts if p.get_param("name", header="content-disposition") == name]


def capture(request: httpx2.Request) -> CapturedRequest:
    """multipart/form-data も application/json も解釈してスナップショットにする。"""
    content_type = request.headers.get("content-type", "")
    body = request.content

    json_body: dict | None = None
    parts: list[Message] = []

    if content_type.startswith("multipart/form-data"):
        # email モジュールで multipart/form-data をパースする(専用ライブラリを増やさないため)。
        header_bytes = f"Content-Type: {content_type}\r\n\r\n".encode()
        msg = email.message_from_bytes(header_bytes + body, policy=email.policy.default)
        parts = list(msg.iter_parts())
    elif content_type.startswith("application/json"):
        json_body = json.loads(body)

    return CapturedRequest(
        method=request.method, path=request.url.path, json_body=json_body, parts=parts
    )


def part_field_value(part: Message) -> str:
    """multipart のテキストフィールド(ファイルでない部分)の値を返す。"""
    return part.get_content().strip()


def part_file_bytes(part: Message) -> bytes:
    return part.get_payload(decode=True)


def part_filename(part: Message) -> str | None:
    return part.get_filename()


def sse_response(events: list[dict], *, status_code: int = 200) -> httpx2.Response:
    """`data: {...}\\n\\n` を並べた SSE レスポンスを作る。"""
    body = b""
    for event in events:
        body += b"data: " + json.dumps(event).encode("utf-8") + b"\n\n"
    body += b"data: [DONE]\n\n"
    return httpx2.Response(status_code, headers={"content-type": "text/event-stream"}, content=body)


def json_response(
    body: dict, *, status_code: int = 200, headers: dict[str, str] | None = None
) -> httpx2.Response:
    all_headers = {"content-type": "application/json"}
    if headers:
        all_headers.update(headers)
    content = json.dumps(body).encode("utf-8")
    return httpx2.Response(status_code, headers=all_headers, content=content)


def usage_payload(
    input_tokens: int = 10, output_tokens: int = 100, image_tokens: int = 5, text_tokens: int = 5
) -> dict:
    return {
        "input_tokens": input_tokens,
        "input_tokens_details": {"image_tokens": image_tokens, "text_tokens": text_tokens},
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }


def generation_response_body(
    *, b64: str | None = None, output_format: str = "png", quality: str = "auto"
) -> dict:
    return {
        "created": 1_700_000_000,
        "background": "auto",
        "output_format": output_format,
        "quality": quality,
        "size": "1024x1024",
        "data": [{"b64_json": b64 or make_b64()}],
        "usage": usage_payload(),
    }
