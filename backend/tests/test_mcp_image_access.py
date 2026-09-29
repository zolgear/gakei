"""エージェントが画像を見る・原本を取り出す(ADR-0023 8章)。

`get_image`(本文に JPEG / PNG を載せる)、サムネイルの JPEG / PNG 化、`create_download_url`
(10分・1回限りの原本のダウンロード URL)。
"""

from __future__ import annotations

import base64
import io
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select, update

from app.domain import agent_images
from app.domain.embedded_meta import read_gakei_meta
from app.domain.models import AppUser, Asset, DownloadTicket
from tests.conftest import login_as, make_png_bytes
from tests.test_mcp import _call, _enable, _error_text, _generate, _ok
from tests.test_mcp_feedback import _issue_token, _path_of, _rgba_png, _upload_via_url


@pytest.fixture(autouse=True)
def _clear_agent_image_cache() -> None:
    agent_images.clear_cache()


def _png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _noise(width: int, height: int, mode: str = "RGB") -> Image.Image:
    channels = len(mode)
    return Image.frombytes(mode, (width, height), os.urandom(width * height * channels))


def _noise_rgba_with_transparency(width: int, height: int) -> Image.Image:
    image = _noise(width, height, "RGBA")
    # ランダムな alpha に加えて、確実に透明な画素を置く。
    image.putpixel((0, 0), (0, 0, 0, 0))
    return image


def _image_of(result: dict[str, Any]) -> tuple[dict[str, Any], Image.Image]:
    images = [c for c in result["content"] if c["type"] == "image"]
    assert len(images) == 1
    content = images[0]
    data = base64.b64decode(content["data"])
    assert len(content["data"]) <= agent_images.MAX_BASE64_BYTES
    image = Image.open(io.BytesIO(data))
    image.load()
    return content, image


def _get_image(client: TestClient, asset_id: str, **args: Any) -> tuple[dict, dict, Image.Image]:
    result = _call(client, "get_image", {"asset_id": asset_id, **args})
    payload = _ok(result)
    content, image = _image_of(result)
    return payload, content, image


# -- get_image(8章 1) ---------------------------------------------------------------


def test_get_image_large_and_small_shrink_big_images(client: TestClient) -> None:
    _enable(client)
    asset_id = _upload_via_url(client, make_png_bytes(3000, 2000, (40, 90, 160)))

    payload, content, image = _get_image(client, asset_id)
    assert payload["size"] == "large"
    assert content["mimeType"] == "image/jpeg"
    assert image.format == "JPEG"
    assert image.size == (1568, 1045)
    assert (payload["width"], payload["height"]) == image.size
    assert (payload["original_width"], payload["original_height"]) == (3000, 2000)
    assert payload["has_alpha"] is False

    payload, content, image = _get_image(client, asset_id, size="small")
    assert content["mimeType"] == "image/jpeg"
    assert image.size == (512, 341)


def test_get_image_never_enlarges(client: TestClient) -> None:
    _enable(client)
    asset_id = _upload_via_url(client, make_png_bytes(120, 80, (1, 2, 3)))
    for size in ("large", "small"):
        _, _, image = _get_image(client, asset_id, size=size)
        assert image.size == (120, 80)


def test_get_image_between_small_and_large(client: TestClient) -> None:
    """1568px 以下・512px 超の画像は、large では原寸、small では 512px。"""
    _enable(client)
    asset_id = _upload_via_url(client, make_png_bytes(1000, 600))
    assert _get_image(client, asset_id)[2].size == (1000, 600)
    assert _get_image(client, asset_id, size="small")[2].size == (512, 307)


def test_get_image_transparent_is_png_opaque_is_jpeg(client: TestClient) -> None:
    _enable(client)
    transparent = _upload_via_url(client, _rgba_png(40, 20, transparent_columns=10))
    payload, content, image = _get_image(client, transparent)
    assert content["mimeType"] == "image/png"
    assert image.format == "PNG"
    assert image.mode == "RGBA"
    assert image.getpixel((0, 0))[3] == 0
    assert payload["has_alpha"] is True
    assert payload["transparent_ratio"] == pytest.approx(0.25)

    # アルファチャンネルがあっても、全画素が不透明なら JPEG。
    opaque_rgba = _upload_via_url(client, _rgba_png(30, 30, transparent_columns=0))
    _, content, image = _get_image(client, opaque_rgba)
    assert content["mimeType"] == "image/jpeg"
    assert image.format == "JPEG"


def test_get_image_noise_fits_under_limit(client: TestClient) -> None:
    """圧縮の効かない画像(ノイズ)でも、base64 後の大きさが上限に収まる。"""
    _enable(client)
    asset_id = _upload_via_url(client, _png(_noise(2400, 1600)))
    payload, content, image = _get_image(client, asset_id)
    assert content["mimeType"] == "image/jpeg"
    # JPEG は品質を下げて収めるので、できるだけ長辺は保つ。
    assert max(image.size) <= 1568
    assert len(content["data"]) <= agent_images.MAX_BASE64_BYTES

    transparent_id = _upload_via_url(client, _png(_noise_rgba_with_transparency(1800, 1200)))
    payload, content, image = _get_image(client, transparent_id)
    assert content["mimeType"] == "image/png"
    # PNG は長辺を縮めて収める。
    assert max(image.size) < 1568
    assert (payload["width"], payload["height"]) == image.size
    assert len(content["data"]) <= agent_images.MAX_BASE64_BYTES


@pytest.mark.parametrize(("mode", "mime"), [("RGB", "image/jpeg"), ("RGBA", "image/png")])
def test_encode_for_agent_4k_noise_fits(mode: str, mime: str) -> None:
    """4K のノイズ画像(原本をそのまま渡した場合)でも上限に収まる。"""
    image = (
        _noise(3840, 2160, "RGB") if mode == "RGB" else _noise_rgba_with_transparency(3840, 2160)
    )
    result = agent_images.encode_for_agent(image, agent_images.LARGE_LONG_EDGE)
    assert result.mime_type == mime
    assert agent_images.base64_length(len(result.data)) <= agent_images.MAX_BASE64_BYTES
    assert max(result.width, result.height) <= agent_images.LARGE_LONG_EDGE


def test_encode_for_agent_respects_a_smaller_limit() -> None:
    image = _noise(800, 800)
    result = agent_images.encode_for_agent(image, 1568, limit=20_000)
    assert agent_images.base64_length(len(result.data)) <= 20_000
    assert result.mime_type == "image/jpeg"


def test_get_image_unknown_asset(client: TestClient) -> None:
    _enable(client)
    text = _error_text(_call(client, "get_image", {"asset_id": str(uuid.uuid4())}))
    assert "not found" in text


def test_get_image_rejects_bad_size(client: TestClient) -> None:
    _enable(client)
    asset_id = _upload_via_url(client, make_png_bytes())
    result = _call(client, "get_image", {"asset_id": asset_id, "size": "huge"})
    assert result.get("isError") is True


def test_get_image_of_generated_output(client: TestClient) -> None:
    _enable(client)
    payload = _generate(client, {"prompt": "look at me"})
    asset_id = payload["outputs"][0]["asset_id"]
    _, content, image = _get_image(client, asset_id)
    assert content["mimeType"] in ("image/jpeg", "image/png")
    assert image.format in ("JPEG", "PNG")


def test_get_image_hides_others_assets_in_oidc(client_oidc: TestClient) -> None:
    login_as(client_oidc, "admin@example.com")
    _enable(client_oidc)
    alice = {"Authorization": f"Bearer {_issue_token(client_oidc, 'alice@example.com')}"}
    bob = {"Authorization": f"Bearer {_issue_token(client_oidc, 'bob@example.com')}"}
    upload = _ok(_call(client_oidc, "create_upload_url", {}, alice))
    client_oidc.cookies.clear()
    asset_id = client_oidc.put(_path_of(upload["upload_url"]), content=make_png_bytes()).json()[
        "asset_id"
    ]

    _ok(_call(client_oidc, "get_image", {"asset_id": asset_id}, alice))
    text = _error_text(_call(client_oidc, "get_image", {"asset_id": asset_id}, bob))
    assert text.endswith(f"Asset {asset_id} not found.")


# -- サムネイルの JPEG / PNG 化(8章 2) -------------------------------------------------


def test_thumbnails_are_jpeg_or_png(client: TestClient) -> None:
    _enable(client)
    opaque = _upload_via_url(client, make_png_bytes(1200, 900, (5, 6, 7)))
    transparent = _upload_via_url(client, _rgba_png(40, 20, transparent_columns=10))

    result = _call(client, "get_asset", {"asset_id": opaque})
    content, image = _image_of(result)
    assert content["mimeType"] == "image/jpeg"
    assert image.size == (512, 384)

    result = _call(client, "get_asset", {"asset_id": transparent})
    content, image = _image_of(result)
    assert content["mimeType"] == "image/png"
    assert image.getpixel((0, 0))[3] == 0

    result = _call(client, "search_assets", {"include_thumbnails": True})
    mimes = sorted(c["mimeType"] for c in result["content"] if c["type"] == "image")
    assert mimes == ["image/jpeg", "image/png"]


# -- create_download_url(8章 3) -------------------------------------------------------


def _download_url(
    client: TestClient, asset_id: str, headers: dict[str, str] | None = None
) -> dict[str, Any]:
    return _ok(_call(client, "create_download_url", {"asset_id": asset_id}, headers))


def test_download_url_once_with_lineage(client: TestClient) -> None:
    _enable(client)
    payload = _generate(client, {"prompt": "download me", "params": {"output_format": "png"}})
    asset_id = payload["outputs"][0]["asset_id"]

    issued = _download_url(client, asset_id)
    assert issued["asset_id"] == asset_id
    assert issued["method"] == "GET"
    assert issued["expires_in_seconds"] == 600
    assert issued["download_url"].startswith("http://testserver/api/downloads/")
    assert issued["download_url"] in issued["curl_example"]

    path = _path_of(issued["download_url"])
    response = client.get(path)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/png"
    assert (
        response.headers["content-disposition"] == f'attachment; filename="{asset_id}-original.png"'
    )
    assert "no-store" in response.headers["cache-control"]
    # PNG には系列情報が入る(ADR-0014。原本の download=1 と同じ)。
    meta = read_gakei_meta(response.content)
    assert meta is not None
    assert meta["root"] == asset_id
    assert meta["nodes"]

    # 2回目は 404。
    assert client.get(path).status_code == 404
    with client.app.state.session_factory() as db:
        ticket = db.execute(select(DownloadTicket)).scalar_one()
        assert ticket.used_at is not None
        assert str(ticket.asset_id) == asset_id
        # トークンそのものは保存しない。
        assert ticket.token_hash not in issued["download_url"]


def test_download_url_serves_same_bytes_as_original(client: TestClient) -> None:
    _enable(client)
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), (10, 20, 30)).save(buffer, format="JPEG")
    asset_id = _upload_via_url(client, buffer.getvalue())
    path = _path_of(_download_url(client, asset_id)["download_url"])
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    original = client.get(f"/api/assets/{asset_id}/content?variant=original&download=1")
    assert response.content == original.content
    assert response.headers["content-disposition"] == original.headers["content-disposition"]


def test_download_url_expired_unknown_and_disabled(client: TestClient) -> None:
    _enable(client)
    asset_id = _upload_via_url(client, make_png_bytes())
    assert client.get(f"/api/downloads/{'x' * 43}").status_code == 404

    expired = _path_of(_download_url(client, asset_id)["download_url"])
    with client.app.state.session_factory() as db:
        db.execute(
            update(DownloadTicket).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        db.commit()
    assert client.get(expired).status_code == 404

    fresh = _path_of(_download_url(client, asset_id)["download_url"])
    assert client.patch("/api/settings/mcp", json={"enabled": False}).status_code == 200
    assert client.get(fresh).status_code == 404
    # MCP が無効の間の取得では使用済みにならない。有効に戻せば期限内の URL は使える。
    _enable(client)
    assert client.get(fresh).status_code == 200


def test_download_url_unknown_asset(client: TestClient) -> None:
    _enable(client)
    text = _error_text(_call(client, "create_download_url", {"asset_id": str(uuid.uuid4())}))
    assert "not found" in text


def test_download_url_visibility_in_oidc(client_oidc: TestClient) -> None:
    login_as(client_oidc, "admin@example.com")
    _enable(client_oidc)
    alice = {"Authorization": f"Bearer {_issue_token(client_oidc, 'alice@example.com')}"}
    bob = {"Authorization": f"Bearer {_issue_token(client_oidc, 'bob@example.com')}"}
    upload = _ok(_call(client_oidc, "create_upload_url", {}, alice))
    client_oidc.cookies.clear()
    asset_id = client_oidc.put(_path_of(upload["upload_url"]), content=make_png_bytes()).json()[
        "asset_id"
    ]

    # 他人の Asset の URL は発行できない。
    text = _error_text(_call(client_oidc, "create_download_url", {"asset_id": asset_id}, bob))
    assert text.endswith(f"Asset {asset_id} not found.")

    # URL 自体が認可なので、Cookie もトークンも要らない。
    path = _path_of(_download_url(client_oidc, asset_id, alice)["download_url"])
    client_oidc.cookies.clear()
    response = client_oidc.get(path)
    assert response.status_code == 200
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"

    # 発行後に見えなくなった(所有者が変わった)場合は、取得の時点で 404。
    later = _path_of(_download_url(client_oidc, asset_id, alice)["download_url"])
    with client_oidc.app.state.session_factory() as db:
        bob_id = db.execute(
            select(AppUser.id).where(AppUser.email == "bob@example.com")
        ).scalar_one()
        db.execute(
            update(Asset).where(Asset.id == uuid.UUID(asset_id)).values(created_by_user_id=bob_id)
        )
        db.commit()
    assert client_oidc.get(later).status_code == 404


def test_download_url_invalid_after_token_revoked(client_oidc: TestClient) -> None:
    login_as(client_oidc, "admin@example.com")
    _enable(client_oidc)
    token = _issue_token(client_oidc, "revoker@example.com")
    auth = {"Authorization": f"Bearer {token}"}
    upload = _ok(_call(client_oidc, "create_upload_url", {}, auth))
    asset_id = client_oidc.put(_path_of(upload["upload_url"]), content=make_png_bytes()).json()[
        "asset_id"
    ]
    path = _path_of(_download_url(client_oidc, asset_id, auth)["download_url"])
    token_id = client_oidc.get("/api/users/me/api-tokens").json()["items"][0]["id"]
    assert client_oidc.delete(f"/api/users/me/api-tokens/{token_id}").status_code == 204
    client_oidc.cookies.clear()
    assert client_oidc.get(path).status_code == 404


# -- 説明(8章 4) ---------------------------------------------------------------------


def test_descriptions_explain_viewing_and_downloading(client: TestClient) -> None:
    _enable(client)
    body = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        },
        headers={
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        },
    ).json()
    instructions = body["result"]["instructions"]
    assert "get_image" in instructions
    assert "create_download_url" in instructions
    assert "cloud" in instructions

    tools = {
        t["name"]: t
        for t in client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
                "MCP-Protocol-Version": "2025-11-25",
            },
        ).json()["result"]["tools"]
    }
    assert tools["get_image"]["annotations"]["readOnlyHint"] is True
    assert tools["get_image"]["inputSchema"]["properties"]["size"]["default"] == "large"
    assert "create_download_url" in tools["get_image"]["description"]
    assert "cloud" in tools["create_download_url"]["description"]
    assert tools["create_download_url"]["annotations"]["readOnlyHint"] is False
