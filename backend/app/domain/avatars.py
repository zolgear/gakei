"""ユーザーのアバター画像(ADR-0020)。oidc モードだけの機能。

アバターは Asset にしない(作品ではなく、ストックや系列グラフに出てはいけない。証跡でもない
ので差し替え・削除は物理削除でよい)。保存先は `DATA_DIR/avatars/{user_id}.webp` の1枚のみで、
利用者が選んだ範囲(ADR-0020 5章、`CropRect`)を正方形に切り出した256x256のWebPにする。
範囲の指定が無ければ中央の正方形(拡大・回転は作らない。ADR-0001の非ゴール「高機能な画像編集」)。

画像の検証は既存のアップロード(`domain/assets.py`)と同じ方針: Pillow でデコードできること、
拡張子ではなく中身で判定する。`Image.MAX_IMAGE_PIXELS` の既定の上限を超える巨大画像は
`PIL.Image.DecompressionBombError` になるので、そのまま `AvatarError` として扱う。
"""

from __future__ import annotations

import hashlib
import io
import os
import tempfile
import uuid
from pathlib import Path

from PIL import Image, ImageOps
from PIL import UnidentifiedImageError as PillowUnidentifiedImageError

from app.domain import derivatives
from app.domain.schemas import CropRect
from app.i18n import t

AVATAR_SIZE = 256
MAX_AVATAR_BYTES = 20 * 1024 * 1024

_AVATARS_DIRNAME = "avatars"


class AvatarError(ValueError):
    """アップロードされたバイト列が画像として使えない場合。"""


class CropError(AvatarError):
    """指定された切り出し範囲(`CropRect`)が画像の外にはみ出す、または空の場合(422用)。"""


def _normalize_mode(image: Image.Image) -> Image.Image:
    """透過を保持しつつ WebP 保存に適したモードにそろえる(アルファは残す)。"""
    if image.mode in ("RGB", "RGBA"):
        return image
    has_alpha = image.mode in ("RGBA", "LA", "PA") or (
        image.mode == "P" and "transparency" in image.info
    )
    return image.convert("RGBA" if has_alpha else "RGB")


def crop_image(image: Image.Image, crop: CropRect) -> Image.Image:
    """`crop`(EXIF反映後の画像のピクセル座標)で `image` を切り出す(ADR-0020 5章)。

    範囲が画像の外にはみ出す、または空(はみ出しの結果、実質的な幅・高さが無い)場合は
    `CropError` を送出する(呼び出し側で422にする)。`CropRect` 自体は pydantic で
    `width`/`height` >= 1 を保証しているが、`x`/`y` との組み合わせで画像の外に出ることは
    ここで検証する。
    """
    width, height = image.size
    right = crop.x + crop.width
    bottom = crop.y + crop.height
    if crop.x >= width or crop.y >= height or right > width or bottom > height:
        raise CropError(t("users.avatar.cropOutOfBounds"))
    return image.crop((crop.x, crop.y, right, bottom))


def make_avatar_webp(data: bytes, crop: CropRect | None = None) -> bytes:
    """任意の画像バイト列から256x256のWebPを作る(ADR-0020 2章・5章)。

    形式は拡張子ではなく中身で判定する(Pillow がデコードできれば何でもよい)。アニメーションは
    `Image.open` の既定のふるまい(先頭フレーム)のまま扱うので、最初のフレームだけを使う。
    EXIF の向き情報があれば反映してから切り出す。`crop` があればその範囲(EXIF反映後の座標)を
    先に切り出し、無ければ中央の正方形にする。`crop` が非正方形なら、その中央の正方形にする。
    """
    try:
        image = Image.open(io.BytesIO(data))
        image.load()  # ヘッダーだけでなく実際にデコードして検証する(domain/assets.py と同じ)
    except (PillowUnidentifiedImageError, OSError, Image.DecompressionBombError) as e:
        raise AvatarError(t("users.avatar.cannotReadImage")) from e

    image = ImageOps.exif_transpose(image) or image
    image = _normalize_mode(image)

    if crop is not None:
        image = crop_image(image, crop)

    width, height = image.size
    side = min(width, height)
    left = (width - side) // 2
    top = (height - side) // 2
    cropped = image.crop((left, top, left + side, top + side))
    resized = cropped.resize((AVATAR_SIZE, AVATAR_SIZE), Image.LANCZOS)

    buffer = io.BytesIO()
    resized.save(buffer, format="WEBP", quality=derivatives.WEBP_QUALITY)
    return buffer.getvalue()


def avatar_path(data_dir: Path, user_id: uuid.UUID) -> Path:
    return data_dir / _AVATARS_DIRNAME / f"{user_id}.webp"


def save_avatar(data_dir: Path, user_id: uuid.UUID, webp: bytes) -> str:
    """アバターを原子的に書き込み(mkstemp + os.replace。`domain/api_key.py` と同じ流儀)、
    内容の sha256(16進数文字列)を返す。"""
    path = avatar_path(data_dir, user_id)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".avatar-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(webp)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return hashlib.sha256(webp).hexdigest()


def delete_avatar(data_dir: Path, user_id: uuid.UUID) -> None:
    """アバターの実体を消す(存在しなくても何もしない)。"""
    try:
        avatar_path(data_dir, user_id).unlink()
    except FileNotFoundError:
        pass


def avatar_url(user_id: uuid.UUID, sha256: str | None) -> str | None:
    """`AuthUser.avatar_url` / `CreatedBy.avatar_url` に入れる URL。アバターが無ければ None。

    `?v=<sha256 の先頭8桁>` はキャッシュ破り用(ADR-0020 3章)。
    """
    if sha256 is None:
        return None
    return f"/api/users/{user_id}/avatar?v={sha256[:8]}"
