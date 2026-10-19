"""展開の爆弾を模した PNG を作るテスト用の部品(大きなバイト列をメモリに置かずに作る)。"""

from __future__ import annotations

import struct
import zlib

from tests.conftest import make_png_bytes


def _chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def compressed_zeros(total: int, piece: int = 1024 * 1024) -> bytes:
    """`total` バイトの 0 を zlib で圧縮したもの(元の 0 の並びは `piece` ずつしか作らない)。"""
    compressor = zlib.compressobj(9)
    out = bytearray()
    zeros = bytes(piece)
    remaining = total
    while remaining > 0:
        size = min(piece, remaining)
        out += compressor.compress(zeros if size == piece else bytes(size))
        remaining -= size
    out += compressor.flush()
    return bytes(out)


def with_itxt(png: bytes, itxt_data: bytes) -> bytes:
    """IEND の直前に iTXt チャンクを差し込む。"""
    iend = png.index(b"IEND") - 4
    return png[:iend] + _chunk(b"iTXt", itxt_data) + png[iend:]


def png_with_gakei_zlib_bomb(total: int) -> bytes:
    """`total` バイトの 0 を圧縮した、圧縮フラグ付きの `gakei` iTXt を持つ PNG。"""
    itxt = b"gakei\x00" + bytes([1, 0]) + b"\x00" + b"\x00" + compressed_zeros(total)
    return with_itxt(make_png_bytes(), itxt)


def large_bilevel_png(width: int, height: int) -> bytes:
    """寸法の大きい、本物の(デコードできる)白黒 1 ビットの PNG。単色なので数十 KB に収まる。"""
    ihdr = struct.pack(">IIBBBBB", width, height, 1, 0, 0, 0, 0)  # 1 ビット、グレースケール
    row = b"\x00" + bytes((width + 7) // 8)  # フィルターなし + 黒の画素
    compressor = zlib.compressobj(9)
    idat = bytearray()
    for _ in range(height):
        idat += compressor.compress(row)
    idat += compressor.flush()
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", bytes(idat))
        + _chunk(b"IEND", b"")
    )
