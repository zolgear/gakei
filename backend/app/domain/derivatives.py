"""派生画像(サムネイル・プレビュー)の生成と読み出し。ADR-0004: 長辺512px / 長辺2048px の WebP。

元画像がすでに目標サイズ以下の場合は拡大しない。透過(RGBA)は保持する。

ADR-0036: 作り方の版を `DERIVED_VERSION` で持つ。作り方(大きさ、品質、色や透過の扱い)を
変えたら 1 つ上げる。版ごとにキーが変わり(`storage.derived_key`)、今の版の派生が無ければ
`ensure_derived` が原本から作って保存する。派生を読む処理は、すべて `ensure_derived` を通す。
"""

from __future__ import annotations

import io
import logging
from collections.abc import Iterable
from typing import TYPE_CHECKING, Literal

from PIL import Image
from PIL import UnidentifiedImageError as PillowUnidentifiedImageError

if TYPE_CHECKING:
    from app.domain.storage import AssetStore, StoredContent

logger = logging.getLogger(__name__)

DerivedVariant = Literal["thumb", "preview"]
DERIVED_VARIANTS: tuple[DerivedVariant, ...] = ("thumb", "preview")

# 派生の作り方の版(ADR-0036 1章)。版 1 は ADR-0036 より前から作っていた派生で、キーは
# `derived/{sha256}/thumb.webp` のまま。作り方を変えたら 1 つ上げ、フロントの
# `DERIVED_VERSION`(`frontend/src/api/assetUrl.ts`)も合わせる(テストで一致を確かめる)。
DERIVED_VERSION = 1

THUMB_LONG_EDGE = 512
PREVIEW_LONG_EDGE = 2048
WEBP_QUALITY = 90


def _resize_long_edge(image: Image.Image, long_edge: int) -> Image.Image:
    width, height = image.size
    current_long_edge = max(width, height)
    if current_long_edge <= long_edge:
        return image.copy()

    scale = long_edge / current_long_edge
    new_size = (max(1, round(width * scale)), max(1, round(height * scale)))
    return image.resize(new_size, Image.LANCZOS)


def _to_webp_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="WEBP", quality=WEBP_QUALITY)
    return buffer.getvalue()


def make_thumb(image: Image.Image) -> bytes:
    """長辺512pxのWebPを作る。"""
    return _to_webp_bytes(_resize_long_edge(image, THUMB_LONG_EDGE))


def make_preview(image: Image.Image) -> bytes:
    """長辺2048pxのWebPを作る。"""
    return _to_webp_bytes(_resize_long_edge(image, PREVIEW_LONG_EDGE))


def make_derived(image: Image.Image, variant: DerivedVariant) -> bytes:
    """variant に応じた派生を作る(取り込みとその場の作り直しで同じ作り方にする)。"""
    if variant == "thumb":
        return make_thumb(image)
    if variant == "preview":
        return make_preview(image)
    raise ValueError(f"派生ではない variant です: {variant}")


def generate_derived(
    store: AssetStore, blob_key: str, sha256: str, variants: Iterable[DerivedVariant]
) -> dict[DerivedVariant, bytes] | None:
    """原本から今の版の派生を作って保存し、作った中身を返す(原本のデコードは1回)。

    原本が無い・画像として読めないときは None(何も書かない)。保存先との通信の失敗
    (`StorageIOError`)はそのまま上げる。同じ派生を同時に作っても同じ内容を上書きする
    だけなので、排他はしない(ADR-0036 2章)。
    """
    try:
        data = store.read(blob_key)
    except FileNotFoundError:
        logger.warning("派生を作る原本がありません: %s", blob_key)
        return None
    try:
        # 取り込み(`assets.ingest`)と同じく、開いた画像をそのまま縮める。
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            made = {variant: make_derived(image, variant) for variant in dict.fromkeys(variants)}
    except (PillowUnidentifiedImageError, OSError, Image.DecompressionBombError):
        logger.warning("派生を作る原本を画像として読めません: %s", blob_key, exc_info=True)
        return None
    for variant, derived in made.items():
        store.write_derived(sha256, variant, derived)
    return made


def ensure_derived(
    store: AssetStore, blob_key: str, sha256: str, variant: DerivedVariant
) -> StoredContent | None:
    """今の版の派生を開く。無ければ原本から作って保存し、それを返す(ADR-0036 2章)。

    原本も無い・読めないときだけ None(配信は 404)。派生を読む処理(配信、共有リンク、
    埋め込み、自動タイトルとタグ、MCP の画像)は、すべてここを通す。
    """
    from app.domain.storage import StoredContent

    content = store.open_content(blob_key, sha256, variant)
    if content is not None:
        return content
    made = generate_derived(store, blob_key, sha256, (variant,))
    if made is None:
        return None
    data = made[variant]
    return StoredContent(size=len(data), chunks=iter((data,)))
