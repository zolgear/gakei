"""GET /api/about、GET /api/about/third-party-notices(ADR-0021 3章)。

`/api/about` 以外の全 API と同じくログインが要る(ADR-0019。`app/main.py` で
`Depends(require_user)` を付けて include する)。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse

from app.config import Settings
from app.deps import get_frontend_dist, get_settings
from app.domain.schemas import AboutResponse
from app.domain.third_party import render_notices
from app.version import get_commit, get_version

router = APIRouter(prefix="/api/about", tags=["about"])


@router.get("", response_model=AboutResponse, operation_id="get_about")
def get_about(settings: Settings = Depends(get_settings)) -> AboutResponse:
    return AboutResponse(version=get_version(), commit=get_commit(settings))


@router.get("/third-party-notices", operation_id="get_third_party_notices")
def get_third_party_notices(frontend_dist: Path = Depends(get_frontend_dist)) -> PlainTextResponse:
    return PlainTextResponse(render_notices(frontend_dist), media_type="text/plain; charset=utf-8")
