"""ログイン不要の共有リンク(ADR-0029)。

- `router`(`/api/shares`): 本人向け。作る前の確認(範囲に含まれる画像)、作成、自分の共有の
  一覧、取り消し。`main.py` で `require_user` の括りに入れる。一覧・取り消しは本人のものだけ
  (管理者も他人のものは 404。ADR-0029 7章)。
- `public_router`(`/api/public/shares`): ログイン不要。`require_user` を掛けず、見せてよいかは
  `app/domain/shares.resolve_public_share` だけで確かめる。見せられない理由は区別せず 404。
  応答ヘッダー(`X-Robots-Tag`、`Referrer-Policy`)は `main.py` の `PublicShareHeadersMiddleware`
  が付ける(404 の応答にも付けるため)。
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.assets import asset_content_response
from app.auth.deps import require_user
from app.auth.identity import CurrentUser
from app.config import Settings
from app.deps import get_session, get_settings, get_store
from app.domain import annotations as annotations_domain
from app.domain import share_settings
from app.domain import shares as shares_domain
from app.domain.mcp_settings import resolve_public_base
from app.domain.models import Asset, Share
from app.domain.schemas import (
    PublicShareResponse,
    ShareCreateRequest,
    ShareListResponse,
    SharePreviewAsset,
    SharePreviewRequest,
    SharePreviewResponse,
    ShareRow,
)
from app.domain.storage import AssetStore
from app.i18n import t

router = APIRouter(prefix="/api/shares", tags=["shares"])
public_router = APIRouter(prefix="/api/public/shares", tags=["public-shares"])

# 公開の画像のキャッシュ。取り消したあとも長く残らないよう短くし、`immutable` は付けない
# (ADR-0029 6章)。
PUBLIC_IMAGE_CACHE_CONTROL = "private, max-age=300"


def _public_base(settings: Settings, request: Request) -> str:
    return resolve_public_base(settings.public_base_url, str(request.base_url))


def _to_row(
    db: Session, share: Share, base: str, asset_count: int, root_title: str | None
) -> ShareRow:
    root = db.get(Asset, share.root_asset_id)
    return ShareRow(
        id=share.id,
        url=shares_domain.share_url(base, share.token),
        root_asset_id=share.root_asset_id,
        root_deleted=root is None or root.deleted_at is not None,
        root_title=root_title,
        scope=share.scope,  # type: ignore[arg-type]
        allow_original=share.allow_original,
        asset_count=asset_count,
        created_at=share.created_at,
        last_accessed_at=share.last_accessed_at,
        access_count=share.access_count,
    )


# -- 本人向け ----------------------------------------------------------------------


@router.post("/preview", response_model=SharePreviewResponse, operation_id="preview_share")
def preview_share(
    body: SharePreviewRequest,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> SharePreviewResponse:
    """共有を作る前に、範囲に含まれる画像を確かめる(ADR-0029 2章)。何も書き込まない。"""
    if not share_settings.is_enabled(db):
        raise HTTPException(status_code=409, detail=t("shares.disabled"))
    try:
        result = shares_domain.collect_scope(db, user, body.asset_id, body.scope)
    except shares_domain.ShareTargetNotFoundError as e:
        raise HTTPException(status_code=404, detail=t("assets.notFound")) from e
    titles = annotations_domain.bulk_titles(db, [a.id for a, _ in result.assets])
    return SharePreviewResponse(
        scope=body.scope,
        asset_count=len(result.assets),
        assets=[
            SharePreviewAsset(
                id=a.id, kind=a.kind, width=a.width, height=a.height, title=titles.get(a.id)
            )
            for a, _depth in result.assets
        ],
        truncated=result.truncated,
    )


@router.post("", response_model=ShareRow, status_code=201, operation_id="create_share")
def create_share(
    body: ShareCreateRequest,
    request: Request,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    user: CurrentUser = Depends(require_user),
) -> ShareRow:
    """共有を作る。作れるのは起点の Asset を見られる人だけ(見えなければ 404)。"""
    try:
        share, count = shares_domain.create_share(
            db, user, body.asset_id, body.scope, allow_original=body.allow_original
        )
    except shares_domain.ShareDisabledError as e:
        db.rollback()
        raise HTTPException(status_code=409, detail=t("shares.disabled")) from e
    except shares_domain.ShareTargetNotFoundError as e:
        db.rollback()
        raise HTTPException(status_code=404, detail=t("assets.notFound")) from e
    db.commit()
    titles = annotations_domain.bulk_titles(db, [share.root_asset_id])
    return _to_row(
        db, share, _public_base(settings, request), count, titles.get(share.root_asset_id)
    )


@router.get("", response_model=ShareListResponse, operation_id="list_shares")
def list_shares(
    request: Request,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    user: CurrentUser = Depends(require_user),
) -> ShareListResponse:
    """自分の、取り消していない共有(新しい順)。機能が無効のあいだも見られる(取り消せるように)。"""
    shares = shares_domain.list_shares(db, user)
    counts = shares_domain.asset_counts(db, [s.id for s in shares])
    titles = annotations_domain.bulk_titles(db, [s.root_asset_id for s in shares])
    base = _public_base(settings, request)
    return ShareListResponse(
        items=[
            _to_row(db, s, base, counts.get(s.id, 0), titles.get(s.root_asset_id)) for s in shares
        ]
    )


@router.delete("/{share_id}", status_code=204, operation_id="revoke_share")
def revoke_share(
    share_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> None:
    """取り消す(元に戻せない)。他人の共有・取り消し済みは 404。"""
    share = shares_domain.get_own_share(db, user, share_id)
    if share is None:
        raise HTTPException(status_code=404, detail=t("shares.notFound"))
    shares_domain.revoke_share(db, share)
    db.commit()


# -- 公開(ログイン不要) ------------------------------------------------------------


def _public_not_found() -> HTTPException:
    return HTTPException(status_code=404, detail=t("shares.publicNotFound"))


@public_router.get("/{token}", response_model=PublicShareResponse, operation_id="get_public_share")
def get_public_share(token: str, db: Session = Depends(get_session)) -> PublicShareResponse:
    """共有のページの内容(画像、タイトル、Run のプロンプトとパラメーター、範囲内の系列)。"""
    access = shares_domain.resolve_public_share(db, token)
    if access is None:
        raise _public_not_found()
    shares_domain.record_access(db, access.share)
    db.commit()
    return shares_domain.build_public_response(db, access.share)


@public_router.get(
    "/{token}/assets/{asset_id}/content",
    operation_id="get_public_share_asset_content",
    responses={200: {"description": "画像", "content": {"image/*": {}}}},
)
def get_public_share_asset_content(
    token: str,
    asset_id: str,
    request: Request,
    variant: Literal["thumb", "preview", "original"] = Query(default="preview"),
    download: int = Query(default=0),
    db: Session = Depends(get_session),
    store: AssetStore = Depends(get_store),
) -> Response:
    """共有に含まれる画像の配信。原本は、共有が許しているときだけ(ADR-0029 4章)。
    ダウンロードする PNG にも系列情報(ADR-0014)は埋め込まない。"""
    # id の形が違うときも、ほかの見せられない理由と同じ 404 にする(422 で区別しない)。
    try:
        parsed_asset_id = uuid.UUID(asset_id)
    except ValueError:
        raise _public_not_found() from None
    access = shares_domain.resolve_public_share(
        db, token, asset_id=parsed_asset_id, variant=variant
    )
    if access is None or access.asset is None:
        raise _public_not_found()
    return asset_content_response(
        db,
        store,
        access.asset,
        # 見る人を渡さない = ダウンロードする PNG にも系列情報を埋め込まない。
        viewer=None,
        variant=variant,
        download=bool(download),
        if_none_match=request.headers.get("if-none-match"),
        cache_control=PUBLIC_IMAGE_CACHE_CONTROL,
    )
