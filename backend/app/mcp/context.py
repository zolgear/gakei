"""`/mcp` のリクエストごとの文脈(誰として動くか、どのトークンか、URL の基点)。

`endpoint.McpEndpoint` が認証を済ませてから ASGI の scope に入れ、ツールのハンドラーは
SDK の `Context`(`ctx.request_context.request.scope`)から取り出す。SDK はツールを別タスク・
別スレッドで動かすため、ContextVar は補助として同じ値を入れるだけにし、scope を正とする。
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError

from app.auth.identity import CurrentUser
from app.domain.api_tokens import TokenScope

SCOPE_KEY = "gakei.mcp"


@dataclass(frozen=True)
class McpRequestContext:
    user: CurrentUser
    # アクセストークンで認証したとき(認証モード)だけ入る。個人モードは None。
    api_token_id: uuid.UUID | None
    # トークンの権限(ADR-0023 11章 2)。`read` なら `server.READ_SCOPE_TOOLS` のツールだけ。
    # 個人モードは `full`。
    token_scope: TokenScope
    # 原本 URL などを組み立てる基点(`PUBLIC_BASE_URL`、無ければリクエストの base URL)。
    base_url: str
    # FastAPI の `app.state`(session_factory、store、registry、runner、progress_bus、settings)。
    state: Any


current_mcp_context: ContextVar[McpRequestContext | None] = ContextVar(
    "gakei_mcp_context", default=None
)


def find_mcp_context(request: Any) -> McpRequestContext | None:
    """SDK が渡す HTTP リクエスト(Starlette の Request)から文脈を引く。無ければ ContextVar。"""
    value: McpRequestContext | None = None
    scope = getattr(request, "scope", None)
    if isinstance(scope, dict):
        value = scope.get(SCOPE_KEY)
    if value is None:
        value = current_mcp_context.get()
    return value


def get_mcp_context(ctx: Context) -> McpRequestContext:
    """ツールのハンドラーから現在の利用者などを引く。"""
    try:
        request = ctx.request_context.request
    except ValueError:
        request = None
    value = find_mcp_context(request)
    if value is None:
        # `McpEndpoint` を通らずに呼ばれることは無いはずだが、念のため誰としても動かない。
        raise ToolError("Not authenticated.")
    return value
