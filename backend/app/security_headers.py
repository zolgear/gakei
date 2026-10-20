"""応答に付けるセキュリティのヘッダー(Issue #166)。

全リクエストの応答に、純粋な ASGI ミドルウェア `SecurityHeadersMiddleware` が次を足す。

- `X-Content-Type-Options: nosniff`(全応答。`Content-Type` と違う種類として解釈させない)。
- `X-Frame-Options: DENY`(全応答。フレームへの埋め込みを禁止する。CSP の
  `frame-ancestors 'none'` を解さない古いブラウザ向け)。
- `Referrer-Policy: same-origin`(全応答)。別のオリジンへは Referer を送らない(URL に
  トークンを含む1回限りのダウンロード URL などを外に渡さない)。`no-referrer` にしないのは、
  `no-referrer` のページからの同じオリジンへの更新の要求では、ブラウザが `Origin: null` を
  送り、`HostOriginGuardMiddleware` が断ってしまうため。共有のページ(`/s/`)と公開の API は、
  内側の `PublicShareHeadersMiddleware` が先に `no-referrer` を付ける(ADR-0029 6章。付いて
  いれば上書きしない)。
- `Content-Security-Policy`(HTML の応答だけ)。ルートが自分で付けたもの(`/docs`、テスト
  ログインの結果のページ)は上書きしない。値は `SPA_CSP`。

ヘッダーはルートやほかのミドルウェアが既に付けていれば上書きしない(リバースプロキシで別の値を
付けたいときは、`SECURITY_HEADERS=false` で GAKEI 側のものを止める。共有のページの
`no-referrer` は ADR-0029 の決めごとなので止めない)。

`BaseHTTPMiddleware` は使わない(SSE のストリーミングをバッファしてしまうため。`http_guard.py`
と同じ)。
"""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

# SPA(`frontend/dist/index.html`)の CSP。
# - script-src 'self': Vite の出力はすべて `/static/` の外部ファイルで、index.html に
#   インラインのスクリプトは無い。
# - style-src 'unsafe-inline': React の `style` 属性と、ライブラリ(系列のグラフなど)が足す
#   `<style>` のため。
# - img-src data: blob:: ファビコンの差し替え(data: URI)と、アップロード前のプレビュー・
#   マップのサムネイルの blob: URL。画像はすべて同じオリジンの API から読む。
# - font-src data:: 同梱のフォント(`@fontsource`)は `/static/` から読むが、data: で
#   埋め込まれるものがあっても読めるように。
# - worker-src blob:: マップの UMAP は `/static/` の Worker で計算する。blob: は念のため。
# - connect-src 'self': API と SSE はすべて同じオリジン(ComfyUI などの外部へはサーバーが
#   つなぐ)。
SPA_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "worker-src 'self' blob:; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "frame-ancestors 'none'; "
    "form-action 'self'"
)

# `/docs`(Swagger UI)と `/redoc` の CSP(個人モードだけで出る開発者向けのページ)。FastAPI の
# 既定の HTML は CDN(cdn.jsdelivr.net)のスクリプトとスタイル、インラインのスクリプト、
# Google Fonts(ReDoc)、FastAPI のサイトのファビコンを読むので、SPA より緩める。
DOCS_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
    "img-src 'self' data: blob: https://fastapi.tiangolo.com https://cdn.redoc.ly; "
    "font-src 'self' data: https://fonts.gstatic.com; "
    "connect-src 'self'; "
    "worker-src 'self' blob:; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "frame-ancestors 'none'; "
    "form-action 'self'"
)

# 全応答に付ける(既に付いていれば足さない)ヘッダー。
_COMMON_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"same-origin"),
)

_CSP = b"content-security-policy"


def _is_html(headers: list[tuple[bytes, bytes]]) -> bool:
    for name, value in headers:
        if name.lower() == b"content-type":
            return value.split(b";", 1)[0].strip().lower() == b"text/html"
    return False


class SecurityHeadersMiddleware:
    """全応答にセキュリティのヘッダーを足し、HTML の応答に CSP を足す純粋な ASGI ミドルウェア。

    `enabled=False`(`SECURITY_HEADERS=false`)なら何もしない。"""

    def __init__(self, app: ASGIApp, enabled: bool = True) -> None:
        self._app = app
        self._enabled = enabled

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self._enabled:
            await self._app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                names = {name.lower() for name, _ in headers}
                extra = [(k, v) for k, v in _COMMON_HEADERS if k not in names]
                if _CSP not in names and _is_html(headers):
                    extra.append((_CSP, SPA_CSP.encode("ascii")))
                if extra:
                    message = {**message, "headers": [*headers, *extra]}
            await send(message)

        await self._app(scope, receive, send_with_headers)
