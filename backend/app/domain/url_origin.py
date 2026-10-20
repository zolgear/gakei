"""URL のオリジン(scheme + host + port)と、資格情報の送り先の確かめ。

保存済みの資格情報(パスワード、API キー、クライアントシークレット)は、保存したときと同じ
オリジンにだけ送る。接続先の URL を別のオリジンに変えるときは、資格情報を入れ直させる
(同じオリジンの中でパスを変えるだけなら、そのまま使う)。管理者の画面の操作で、保存済みの
資格情報が意図しない宛先へ送られないようにするため。
"""

from __future__ import annotations

from urllib.parse import urlsplit

_DEFAULT_PORTS = {"http": 80, "https": 443}


def origin_of(url: str | None) -> str | None:
    """`scheme://host:port`(小文字。ポートは既定のものも書く)。読めない URL は None。"""
    if not url:
        return None
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    host = parts.hostname
    if scheme not in _DEFAULT_PORTS or not host:
        return None
    if port is None:
        port = _DEFAULT_PORTS[scheme]
    if ":" in host:
        # IPv6 のアドレスは角かっこで囲む
        host = f"[{host}]"
    return f"{scheme}://{host.lower()}:{port}"


def same_origin(a: str | None, b: str | None) -> bool:
    """2つの URL が同じオリジンか。どちらかが読めなければ False。"""
    origin_a = origin_of(a)
    return origin_a is not None and origin_a == origin_of(b)
