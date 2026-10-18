"""Webhook の送り方(ADR-0051 3章・7章)。

- 本文は既定で `application/json`(契約のイベントの JSON)。画像を送る設定のときは
  `multipart/form-data` にして、JSON の部分(名前 `event`)と画像の原本の部分(名前 `images`、
  ファイル名は `<Asset の id>.<拡張子>`)を送る。base64 にはしない(ADR-0004)。
- 署名: `X-Gakei-Signature: sha256=<HMAC-SHA256(鍵, "<timestamp>." + 本文のバイト列) の hex>`、
  `X-Gakei-Timestamp` は UNIX 秒。受ける側は時刻の古さも確かめられる。multipart のときも、
  送るバイト列そのもの(境界を含む)に署名する。
- 応答の本文は JSON として読む。2xx 以外と、JSON として読めない応答は失敗。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.version import get_version

_RESPONSE_MAX_BYTES = 1024 * 1024
_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


class WebhookError(Exception):
    """送れなかった、または応答が不正(文言は記録に残す。鍵は含めない)。"""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class WebhookImage:
    asset_id: str | None
    sha256: str
    mime: str
    data: bytes

    @property
    def filename(self) -> str:
        stem = self.asset_id or self.sha256
        return stem + _EXTENSIONS.get(self.mime, ".bin")


@dataclass(frozen=True)
class WebhookResponse:
    status_code: int
    # 本文を JSON として読んだもの(空なら None)。
    body: Any


def sign(secret: str, timestamp: str, body: bytes) -> str:
    """`X-Gakei-Signature` の値。"""
    digest = hmac.new(
        secret.encode("utf-8"), timestamp.encode("ascii") + b"." + body, hashlib.sha256
    ).hexdigest()
    return f"sha256={digest}"


def build_request(
    url: str,
    secret: str,
    event_json: dict[str, Any],
    images: list[WebhookImage] | None = None,
    *,
    timestamp: int | None = None,
) -> httpx.Request:
    """署名を付けた要求を組み立てる(テストで中身を確かめられるよう、送るのとは分ける)。"""
    body_json = json.dumps(event_json, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = {
        "User-Agent": f"GAKEI/{get_version()}",
        "X-Gakei-Event": str(event_json.get("event", "")),
        "X-Gakei-Delivery": str(event_json.get("event_id", "")),
    }
    if images:
        files: list[tuple[str, tuple[str, bytes, str]]] = [
            ("event", ("event.json", body_json, "application/json"))
        ]
        files.extend(("images", (image.filename, image.data, image.mime)) for image in images)
        request = httpx.Request("POST", url, files=files, headers=headers)
    else:
        request = httpx.Request(
            "POST",
            url,
            content=body_json,
            headers={**headers, "Content-Type": "application/json"},
        )
    body = request.read()
    ts = str(timestamp if timestamp is not None else int(time.time()))
    request.headers["X-Gakei-Timestamp"] = ts
    request.headers["X-Gakei-Signature"] = sign(secret, ts, body)
    return request


async def send(
    request: httpx.Request,
    *,
    timeout_seconds: float,
    transport: httpx.AsyncBaseTransport | None = None,
) -> WebhookResponse:
    """送って応答を読む。時間切れは `httpx.TimeoutException`、それ以外の失敗は WebhookError。"""
    timeout = httpx.Timeout(timeout_seconds)
    async with httpx.AsyncClient(
        timeout=timeout, transport=transport, follow_redirects=False
    ) as client:
        try:
            response = await client.send(request)
        except httpx.TimeoutException:
            raise
        except httpx.HTTPError as e:
            raise WebhookError(f"{e.__class__.__name__}") from e
        content = response.content[:_RESPONSE_MAX_BYTES]
    if not (200 <= response.status_code < 300):
        raise WebhookError(f"HTTP {response.status_code}", response.status_code)
    if not content.strip():
        return WebhookResponse(status_code=response.status_code, body=None)
    try:
        body = json.loads(content)
    except ValueError as e:
        raise WebhookError("invalid JSON response", response.status_code) from e
    return WebhookResponse(status_code=response.status_code, body=body)
