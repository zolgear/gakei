"""リクエストの文字列に NUL(`\\u0000`)があれば 422 で拒む(ADR-0027 2章の追記)。

`create_app` が `FastAPI(dependencies=[...])` でアプリ全体の依存として掛ける。個々のスキーマに
書き足すのではなく、全ルートの入口で次をまとめて確かめる(新しいルートやスキーマを足しても
漏れない)。

- パスパラメーターとクエリ(キーと値)
- JSON の本文(入れ子の dict / list の中の文字列と、dict のキー)
- フォーム(`multipart/form-data` など)のフィールドの値と、アップロードのファイル名

本文とフォームは、そのルートが本文を受け取る(`body_field` がある)場合だけ見る。FastAPI は
依存を解く前に本文を読んで `Request` にキャッシュしているので、ここで読み直しても二重には
読まない。本文を自分で読むルート(`PUT /api/uploads/{token}` の画像のバイト列)には触れない。

`/mcp` はこの依存の外にある(Starlette のルート)。MCP のツール引数は `app.mcp.server` で
同じ規則を当てる。
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException, Request
from fastapi.routing import APIRoute
from starlette.datastructures import UploadFile

from app.domain.text_safety import NUL, find_nul
from app.i18n import t


def _reject(location: tuple[str | int, ...]) -> None:
    raise HTTPException(
        status_code=422,
        detail=t("app.nulInRequest", location=".".join(str(part) for part in location)),
    )


def _is_json(content_type: str) -> bool:
    media = content_type.split(";", 1)[0].strip().lower()
    return media == "application/json" or media.endswith("+json")


async def reject_nul_in_request(request: Request) -> None:
    """アプリ全体の依存。NUL を含む文字列があれば 422 にする。"""
    found = find_nul(dict(request.path_params))
    if found is not None:
        _reject(("path", *found))

    for key, value in request.query_params.multi_items():
        if NUL in key or NUL in value:
            _reject(("query", key.replace(NUL, "")))

    route = request.scope.get("route")
    if not isinstance(route, APIRoute) or route.body_field is None:
        return

    content_type = request.headers.get("content-type", "")
    if _is_json(content_type):
        raw = await request.body()
        # 速く済ませるための前置き。エスケープ(`\u0000`)も生の NUL も無ければ調べるまでもない。
        if b"\\u0000" not in raw and b"\x00" not in raw:
            return
        try:
            payload: Any = json.loads(raw)
        except ValueError:
            # 壊れた JSON は FastAPI が先に 422 にしている。ここへは来ない想定。
            return
        found = find_nul(payload)
        if found is not None:
            _reject(("body", *found))
        return

    if content_type.startswith(("multipart/form-data", "application/x-www-form-urlencoded")):
        form = await request.form()
        for key, value in form.multi_items():
            if NUL in key:
                _reject(("body", key.replace(NUL, "")))
            if isinstance(value, UploadFile):
                if value.filename and NUL in value.filename:
                    _reject(("body", key, "filename"))
            elif NUL in value:
                _reject(("body", key))
