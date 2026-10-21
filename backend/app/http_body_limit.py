"""外へ出した HTTP の要求の応答と、受け取ったファイルを、上限までだけ読む。

相手の応答(Webhook、ComfyUI の出力画像、モデルのダウンロード)や、送られたファイル(画像、
アバター)を一度に全部メモリに読むと、上限を超える大きさのものでもメモリに載ってしまう。ここの
関数は、`Content-Length` が上限を超えていれば読む前に断り、無い・偽りのときも読みながら数えて、
上限を超えた時点で打ち切る。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import BinaryIO

import httpx

# ファイルを読む単位。
CHUNK_BYTES = 1024 * 1024


class BodyTooLarge(Exception):
    """本文が上限を超えた(読むのを打ち切った)。"""


def declared_length(response: httpx.Response) -> int | None:
    """`Content-Length` の値。無い・読めないときは None。"""
    raw = response.headers.get("content-length")
    if raw is None or not raw.strip().isdigit():
        return None
    return int(raw.strip())


def check_declared_length(response: httpx.Response, max_bytes: int) -> None:
    """`Content-Length` が上限を超えていれば、読む前に BodyTooLarge。"""
    length = declared_length(response)
    if length is not None and length > max_bytes:
        raise BodyTooLarge


async def iter_limited(response: httpx.Response, max_bytes: int) -> AsyncIterator[bytes]:
    """`stream` で開いた応答の本文を、上限を超えない範囲で少しずつ返す。超えたら BodyTooLarge。"""
    check_declared_length(response, max_bytes)
    total = 0
    # 大きさを指定すると httpx がその大きさまで貯めてから返すので、届いた単位のまま数える。
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > max_bytes:
            raise BodyTooLarge
        yield chunk


async def aread_limited(response: httpx.Response, max_bytes: int) -> bytes:
    """`stream` で開いた応答の本文を、上限までだけ読む。超えたら BodyTooLarge。"""
    chunks = [chunk async for chunk in iter_limited(response, max_bytes)]
    return b"".join(chunks)


def read_file_limited(file: BinaryIO, max_bytes: int, too_large: Callable[[], Exception]) -> bytes:
    """受け取ったファイル(multipart の一時ファイルなど)を、上限までだけ読む。

    上限を超えたら `too_large()` の例外を送出する(呼び出し側の既存のステータスと文言のまま)。
    上限ちょうどまでは読み、1 バイトでも超えると分かった時点で読むのをやめる。
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = file.read(min(CHUNK_BYTES, max_bytes + 1 - total))
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > max_bytes:
            raise too_large()
        chunks.append(chunk)
