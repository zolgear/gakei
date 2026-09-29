"""`GET /api/downloads/{token}`: 原本の1回限りのダウンロード URL の受け口(ADR-0023 8章 3)。

URL は MCP の `create_download_url` が発行する。Cookie やアクセストークンによる認証は行わず、
URL に含むトークン自体を認可とする(そのため `main.py` の `require_user` の括りに入れない)。
エージェント(モデル)がトークンを知らなくても、手元で動く道具(curl など)で原本を取れる
ようにするため。

取得の時点でも次を確かめ、どれかが満たされなければ 404(理由は区別しない):
- MCP が有効
- 期限内・未使用で、発行に使ったアクセストークンが失効していない
- 発行した利用者に、その Asset が今も見える(ADR-0025)

使用済みにしてから配信する。応答は `GET /api/assets/{id}/content?variant=original&download=1`
と同じ(`Content-Disposition`、PNG なら系列情報の埋め込み。ADR-0014)。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.assets import asset_content_response
from app.auth.sessions import viewer_for_issuer
from app.config import Settings
from app.deps import get_session, get_settings, get_store
from app.domain import download_tickets as download_tickets_domain
from app.domain import mcp_settings
from app.domain.storage import AssetStore
from app.domain.visibility import get_visible_asset
from app.i18n import t

router = APIRouter(tags=["downloads"])


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail=t("downloads.notFound"))


@router.get(
    "/api/downloads/{token}",
    operation_id="download_by_url",
    responses={200: {"description": "原本の画像ファイル", "content": {"image/*": {}}}},
)
def download_by_url(
    token: str,
    db: Session = Depends(get_session),
    store: AssetStore = Depends(get_store),
    settings: Settings = Depends(get_settings),
) -> Response:
    """MCP の `create_download_url` で発行した URL から原本を取得する。URL は10分間・1回限り
    有効。不明・使用済み・期限切れ・見えなくなった Asset は、どれも 404。"""
    if not mcp_settings.is_enabled(db):
        raise _not_found()
    ticket = download_tickets_domain.find_usable_ticket(db, token)
    if ticket is None:
        raise _not_found()
    viewer = viewer_for_issuer(
        db,
        ticket.user_id,
        auth_mode=settings.auth_mode,
        admin_emails=settings.admin_email_set(),
    )
    if viewer is None:
        raise _not_found()
    asset = get_visible_asset(db, viewer, ticket.asset_id)
    if asset is None:
        raise _not_found()
    if not store.content_path(asset.blob_key, asset.sha256, "original").exists():
        raise HTTPException(status_code=404, detail=t("assets.contentNotFound"))
    # 同時に取得されても片方だけが通る。使用済みを確定してから配信する。
    if not download_tickets_domain.mark_used(db, ticket):
        db.rollback()
        raise _not_found()
    db.commit()
    return asset_content_response(
        db,
        store,
        asset,
        viewer=viewer,
        variant="original",
        download=True,
        # 1回限りの URL なので、ブラウザにも中継にも覚えさせない。
        cache_control="private, no-store",
    )
