"""ユーザーのアバター(ADR-0020)。oidc モードだけの機能で、`none` モードでは全エンドポイントを
404(`t("auth.disabled")`)にする(`/api/auth/login` 等の既存の扱いに合わせる)。

自分のアバターだけを変えられる(管理者が他人のアバターを変える機能は無い)。表示(`GET`)は
ログインしていれば誰のものでも見られる(履歴の実行者に出すため)。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.auth.deps import require_user
from app.auth.identity import CurrentUser
from app.auth.runtime import AuthRuntime, get_auth_runtime
from app.config import Settings
from app.deps import get_session, get_settings, get_store
from app.domain.avatars import (
    MAX_AVATAR_BYTES,
    AvatarError,
    avatar_path,
    avatar_url,
    delete_avatar,
    make_avatar_webp,
    save_avatar,
)
from app.domain.models import AppUser
from app.domain.schemas import AuthUser, AvatarFromAssetRequest, CropRect
from app.domain.storage import AssetStore
from app.domain.visibility import get_visible_asset
from app.i18n import t

router = APIRouter(prefix="/api/users", tags=["users"])


def _require_oidc_mode(runtime: AuthRuntime) -> None:
    if not runtime.is_oidc:
        raise HTTPException(status_code=404, detail=t("auth.disabled"))


def _to_auth_user(user: AppUser) -> AuthUser:
    return AuthUser(
        id=user.id,
        name=user.name,
        email=user.email,
        role=user.role,  # type: ignore[arg-type]
        avatar_url=avatar_url(user.id, user.avatar_sha256),
    )


def _get_app_user(db: Session, user: CurrentUser) -> AppUser:
    assert user.id is not None  # _require_oidc_mode を先に通しているので none モードではない
    app_user = db.get(AppUser, user.id)
    assert app_user is not None  # ログイン済みセッションが指すユーザーは必ず存在する
    return app_user


def _parse_crop(crop: str | None) -> CropRect | None:
    """form の `crop`(JSON文字列)を `CropRect` にする。壊れていれば422。"""
    if crop is None:
        return None
    try:
        return CropRect.model_validate_json(crop)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=t("users.avatar.invalidCrop")) from e


def _apply_new_avatar(
    db: Session,
    settings: Settings,
    user: CurrentUser,
    data: bytes,
    crop: CropRect | None = None,
) -> AuthUser:
    try:
        webp = make_avatar_webp(data, crop)
    except AvatarError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    assert user.id is not None
    sha256 = save_avatar(settings.data_dir, user.id, webp)
    app_user = _get_app_user(db, user)
    app_user.avatar_sha256 = sha256
    db.commit()
    return _to_auth_user(app_user)


@router.post("/me/avatar", response_model=AuthUser, operation_id="upload_avatar")
def upload_avatar(
    file: UploadFile = File(...),
    crop: str | None = Form(None),
    settings: Settings = Depends(get_settings),
    runtime: AuthRuntime = Depends(get_auth_runtime),
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> AuthUser:
    _require_oidc_mode(runtime)
    data = file.file.read()
    if len(data) > MAX_AVATAR_BYTES:
        raise HTTPException(status_code=413, detail=t("users.avatar.tooLarge"))
    parsed_crop = _parse_crop(crop)
    return _apply_new_avatar(db, settings, user, data, parsed_crop)


@router.post("/me/avatar/from-asset", response_model=AuthUser, operation_id="set_avatar_from_asset")
def set_avatar_from_asset(
    body: AvatarFromAssetRequest,
    settings: Settings = Depends(get_settings),
    runtime: AuthRuntime = Depends(get_auth_runtime),
    db: Session = Depends(get_session),
    store: AssetStore = Depends(get_store),
    user: CurrentUser = Depends(require_user),
) -> AuthUser:
    _require_oidc_mode(runtime)
    # 他人の Asset は存在しないものと同じ扱い(ADR-0025)。
    asset = get_visible_asset(db, user, body.asset_id)
    if asset is None or asset.deleted_at is not None:
        raise HTTPException(status_code=422, detail=t("users.avatar.assetNotFound"))
    data = store.read(asset.blob_key)
    return _apply_new_avatar(db, settings, user, data, body.crop)


@router.delete("/me/avatar", response_model=AuthUser, operation_id="delete_avatar")
def remove_avatar(
    settings: Settings = Depends(get_settings),
    runtime: AuthRuntime = Depends(get_auth_runtime),
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> AuthUser:
    _require_oidc_mode(runtime)
    assert user.id is not None
    delete_avatar(settings.data_dir, user.id)
    app_user = _get_app_user(db, user)
    app_user.avatar_sha256 = None
    db.commit()
    return _to_auth_user(app_user)


@router.get("/{user_id}/avatar", operation_id="get_avatar")
def get_avatar(
    user_id: uuid.UUID,
    request: Request,
    settings: Settings = Depends(get_settings),
    runtime: AuthRuntime = Depends(get_auth_runtime),
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> Response:
    _require_oidc_mode(runtime)
    # ADR-0025: 他人の Run や Asset は見えないので、他人の実行者表示(アバター)を出す画面も
    # 無い。本人のアバターだけを返し、他人の id は存在しないものと同じ 404 にする。
    if user.id != user_id:
        raise HTTPException(status_code=404, detail=t("users.avatar.notFound"))
    app_user = db.get(AppUser, user_id)
    if app_user is None or app_user.avatar_sha256 is None:
        raise HTTPException(status_code=404, detail=t("users.avatar.notFound"))

    path = avatar_path(settings.data_dir, user_id)
    if not path.is_file():
        # DB とファイルの食い違いは通常無いはずだが、念のため 404 にする。
        raise HTTPException(status_code=404, detail=t("users.avatar.notFound"))

    etag = f'"{app_user.avatar_sha256}"'
    headers = {"Cache-Control": "private, max-age=31536000, immutable", "ETag": etag}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=path.read_bytes(), media_type="image/webp", headers=headers)
