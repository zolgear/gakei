"""派生「カード」(長辺 1024px の WebP。ADR-0036 5章、2026-10-23 追記)。

作り方(長辺、拡大しないこと、透過)、取り込み、配信、その場での作り直し、前もって作るツール。
"""

from __future__ import annotations

import io
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.domain import derivatives
from app.domain.models import Asset
from tests.conftest import make_png_bytes, wait_for_background_reads


def _decode(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


def _upload(client: TestClient, width: int, height: int) -> str:
    response = client.post(
        "/api/assets",
        files={"file": ("a.png", make_png_bytes(width, height, (30, 90, 200)), "image/png")},
        data={"kind": "upload"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _sha(client: TestClient, asset_id: str) -> str:
    with client.app.state.session_factory() as db:
        asset = db.get(Asset, uuid.UUID(asset_id))
        assert asset is not None
        return asset.sha256


# -- 作り方 ----------------------------------------------------------------------


def test_card_is_in_derived_variants_and_version_unchanged() -> None:
    assert derivatives.DERIVED_VARIANTS == ("thumb", "card", "preview")
    assert derivatives.CARD_LONG_EDGE == 1024
    # カードを足しても thumb / preview の作り方は変えないので、版は 1 のまま(ADR-0036 5章)。
    assert derivatives.DERIVED_VERSION == 1


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        ((1184, 2560), (474, 1024)),
        ((3000, 2000), (1024, 683)),
        ((1024, 1536), (683, 1024)),
    ],
)
def test_make_card_fits_long_edge(size: tuple[int, int], expected: tuple[int, int]) -> None:
    with _decode(derivatives.make_card(Image.new("RGB", size, (1, 2, 3)))) as card:
        assert card.format == "WEBP"
        assert card.size == expected
        assert max(card.size) <= derivatives.CARD_LONG_EDGE


def test_make_card_does_not_upscale_small_image() -> None:
    with _decode(derivatives.make_derived(Image.new("RGB", (600, 300)), "card")) as card:
        assert card.size == (600, 300)


def test_make_card_keeps_alpha() -> None:
    image = Image.new("RGBA", (2048, 1024), (255, 0, 0, 0))
    with _decode(derivatives.make_card(image)) as card:
        assert card.size == (1024, 512)
        assert card.mode == "RGBA"
        assert card.getpixel((10, 10))[3] == 0


def test_card_is_between_thumb_and_preview() -> None:
    image = Image.new("RGB", (4096, 2048), (9, 9, 9))
    sizes = {
        variant: _decode(derivatives.make_derived(image, variant)).size
        for variant in derivatives.DERIVED_VARIANTS
    }
    assert sizes == {"thumb": (512, 256), "card": (1024, 512), "preview": (2048, 1024)}


# -- 取り込みと配信 --------------------------------------------------------------


def test_ingest_writes_card_and_serves_it(client: TestClient, data_dir: Path) -> None:
    asset_id = _upload(client, 1184, 2560)
    sha = _sha(client, asset_id)
    card_path = data_dir / f"derived/{sha}/card.webp"
    assert card_path.is_file()

    response = client.get(f"/api/assets/{asset_id}/content?variant=card&dv=1")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/webp"
    assert response.headers["etag"] == f'"{sha}-card-d1"'
    assert response.content == card_path.read_bytes()
    with _decode(response.content) as card:
        assert card.size == (474, 1024)

    cached = client.get(
        f"/api/assets/{asset_id}/content?variant=card",
        headers={"If-None-Match": f'"{sha}-card-d1"'},
    )
    assert cached.status_code == 304


def test_card_missing_for_existing_asset_is_made_on_read(
    client: TestClient, data_dir: Path
) -> None:
    """カードを足す前の Asset(card.webp が無い)は、初めて読むときに原本から作る。"""
    asset_id = _upload(client, 2000, 1000)
    sha = _sha(client, asset_id)
    card_path = data_dir / f"derived/{sha}/card.webp"
    wait_for_background_reads(client)
    card_path.unlink()

    response = client.get(f"/api/assets/{asset_id}/content?variant=card")
    assert response.status_code == 200
    with _decode(response.content) as card:
        assert card.size == (1024, 512)
    assert card_path.read_bytes() == response.content


def test_unknown_variant_is_rejected(client: TestClient) -> None:
    asset_id = _upload(client, 64, 64)
    assert client.get(f"/api/assets/{asset_id}/content?variant=huge").status_code == 422


def test_openapi_lists_card_variant(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    for path in (
        "/api/assets/{asset_id}/content",
        "/api/public/shares/{token}/assets/{asset_id}/content",
    ):
        params = spec["paths"][path]["get"]["parameters"]
        variant = next(p for p in params if p["name"] == "variant")
        assert "card" in variant["schema"]["enum"], path


def test_frontend_long_edges_match_backend() -> None:
    """フロントの `DERIVED_LONG_EDGE`(assetUrl.ts。srcset の w を求める)とバックエンドの長辺が
    一致する。"""
    import re

    source = (
        Path(__file__).resolve().parents[2] / "frontend" / "src" / "api" / "assetUrl.ts"
    ).read_text(encoding="utf-8")
    match = re.search(r"DERIVED_LONG_EDGE\s*=\s*\{([^}]*)\}", source)
    assert match is not None, "assetUrl.ts に DERIVED_LONG_EDGE がありません"
    edges = {k: int(v) for k, v in re.findall(r"(\w+)\s*:\s*(\d+)", match.group(1))}
    assert edges == {
        "thumb": derivatives.THUMB_LONG_EDGE,
        "card": derivatives.CARD_LONG_EDGE,
        "preview": derivatives.PREVIEW_LONG_EDGE,
    }
