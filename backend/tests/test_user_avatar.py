"""ADR-0020: ユーザーのアバター(アップロードと生成画像からの選択)。"""

from __future__ import annotations

import io
import json
import uuid

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.domain.avatars import MAX_AVATAR_BYTES, CropError, crop_image
from app.domain.schemas import CropRect
from tests.conftest import login_as, make_png_bytes, wait_for_run_terminal


def _upload_avatar(client: TestClient, data: bytes, filename: str = "avatar.png"):
    return client.post(
        "/api/users/me/avatar",
        files={"file": (filename, io.BytesIO(data), "image/png")},
    )


def _assert_near_color(
    pixel: tuple[int, int, int], expected: tuple[int, int, int], tolerance: int = 10
) -> None:
    """WebP はロッシー圧縮なので、色は完全一致ではなく近似で比較する。"""
    assert all(abs(a - b) <= tolerance for a, b in zip(pixel, expected, strict=True)), (
        pixel,
        expected,
    )


def test_upload_avatar_sets_url_and_serves_square_webp(client_oidc: TestClient) -> None:
    login_as(client_oidc, "avatar-upload@example.com", "Avatar Upload")

    response = _upload_avatar(client_oidc, make_png_bytes(300, 300))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["avatar_url"] is not None
    assert body["avatar_url"].startswith(f"/api/users/{body['id']}/avatar?v=")

    me = client_oidc.get("/api/auth/me").json()
    assert me["user"]["avatar_url"] == body["avatar_url"]

    content = client_oidc.get(body["avatar_url"])
    assert content.status_code == 200
    assert content.headers["content-type"] == "image/webp"
    image = Image.open(io.BytesIO(content.content))
    assert image.format == "WEBP"
    assert image.size == (256, 256)


def test_upload_wide_image_is_cropped_to_center_square(client_oidc: TestClient) -> None:
    login_as(client_oidc, "avatar-wide@example.com", "Avatar Wide")

    response = _upload_avatar(client_oidc, make_png_bytes(400, 100))
    assert response.status_code == 200, response.text

    content = client_oidc.get(response.json()["avatar_url"])
    image = Image.open(io.BytesIO(content.content))
    assert image.size == (256, 256)


def test_upload_tall_image_is_cropped_to_center_square(client_oidc: TestClient) -> None:
    login_as(client_oidc, "avatar-tall@example.com", "Avatar Tall")

    response = _upload_avatar(client_oidc, make_png_bytes(100, 400))
    assert response.status_code == 200, response.text

    content = client_oidc.get(response.json()["avatar_url"])
    image = Image.open(io.BytesIO(content.content))
    assert image.size == (256, 256)


def test_exif_orientation_is_applied_before_cropping(client_oidc: TestClient) -> None:
    """EXIF の向き情報(orientation=6)を反映してから正方形に切り出す。

    横長(200x40、左半分=赤、右半分=青)の画像に orientation=6 を付けると、表示上は
    縦長(40x200、上半分=赤、下半分=青)になる。EXIF を反映していれば、切り出した
    正方形の上端は赤、下端は青になる(反映していなければ左右の分割のまま残り、
    上端・下端がどちらも同じ色になってしまう)。
    """
    login_as(client_oidc, "avatar-exif@example.com", "Avatar Exif")

    base = Image.new("RGB", (200, 40), (0, 0, 0))
    pixels = base.load()
    for x in range(200):
        for y in range(40):
            pixels[x, y] = (255, 0, 0) if x < 100 else (0, 0, 255)

    exif = base.getexif()
    exif[274] = 6  # Orientation
    buffer = io.BytesIO()
    base.save(buffer, format="JPEG", exif=exif)

    response = _upload_avatar(client_oidc, buffer.getvalue(), filename="rotated.jpg")
    assert response.status_code == 200, response.text

    content = client_oidc.get(response.json()["avatar_url"])
    image = Image.open(io.BytesIO(content.content)).convert("RGB")
    top_pixel = image.getpixel((128, 4))
    bottom_pixel = image.getpixel((128, 251))
    assert top_pixel[0] > 200 and top_pixel[2] < 50  # 赤に近い
    assert bottom_pixel[2] > 200 and bottom_pixel[0] < 50  # 青に近い


def _make_quadrant_png_bytes(size: int = 100) -> bytes:
    """左上 (0,0)-(size/2,size/2) だけ赤、それ以外は青のPNG(crop の範囲確認用)。"""
    half = size // 2
    image = Image.new("RGB", (size, size), (0, 0, 255))
    pixels = image.load()
    for x in range(half):
        for y in range(half):
            pixels[x, y] = (255, 0, 0)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_upload_avatar_with_crop_selects_region(client_oidc: TestClient) -> None:
    """`crop` で左上の赤い領域だけを指定すると、出力は赤一色になる。"""
    login_as(client_oidc, "avatar-crop@example.com", "Avatar Crop")

    crop = json.dumps({"x": 0, "y": 0, "width": 50, "height": 50})
    response = client_oidc.post(
        "/api/users/me/avatar",
        files={"file": ("quad.png", io.BytesIO(_make_quadrant_png_bytes()), "image/png")},
        data={"crop": crop},
    )
    assert response.status_code == 200, response.text

    content = client_oidc.get(response.json()["avatar_url"])
    image = Image.open(io.BytesIO(content.content)).convert("RGB")
    assert image.size == (256, 256)
    _assert_near_color(image.getpixel((10, 10)), (255, 0, 0))
    _assert_near_color(image.getpixel((200, 200)), (255, 0, 0))


def test_upload_avatar_with_non_square_crop_becomes_center_square(client_oidc: TestClient) -> None:
    """`crop` が非正方形なら、その中央の正方形を使う。"""
    login_as(client_oidc, "avatar-crop-nonsquare@example.com", "Avatar Crop Nonsquare")

    # 幅200x高さ100の画像で、x=[50,75)が赤、x=[75,125)が緑、x=[125,150)が青の縦帯。
    image = Image.new("RGB", (200, 100), (128, 128, 128))
    pixels = image.load()
    for x in range(200):
        for y in range(100):
            if 50 <= x < 75:
                pixels[x, y] = (255, 0, 0)
            elif 75 <= x < 125:
                pixels[x, y] = (0, 255, 0)
            elif 125 <= x < 150:
                pixels[x, y] = (0, 0, 255)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")

    # crop は幅100x高さ50の非正方形。中央の正方形(幅50)を取ると、緑の帯だけが残るはず。
    crop = json.dumps({"x": 50, "y": 25, "width": 100, "height": 50})
    response = client_oidc.post(
        "/api/users/me/avatar",
        files={"file": ("stripes.png", io.BytesIO(buffer.getvalue()), "image/png")},
        data={"crop": crop},
    )
    assert response.status_code == 200, response.text

    content = client_oidc.get(response.json()["avatar_url"])
    out = Image.open(io.BytesIO(content.content)).convert("RGB")
    _assert_near_color(out.getpixel((10, 10)), (0, 255, 0))
    _assert_near_color(out.getpixel((200, 200)), (0, 255, 0))


def test_upload_avatar_crop_uses_exif_transposed_coordinates(client_oidc: TestClient) -> None:
    """`crop` はEXIF反映後の座標で解釈される。

    200x40(横長)の原本に orientation=6 を付けると、EXIF反映後は40x200(縦長)になる。
    反映前の座標系ではこの範囲(幅40・高さ90)は画像の高さ(40)を超えてしまうので、
    反映後の座標として解釈されていなければ 422 になるはず。反映後の座標として正しく
    解釈されれば、上側(赤)だけを指定しているので出力は赤一色になる。
    """
    login_as(client_oidc, "avatar-crop-exif@example.com", "Avatar Crop Exif")

    base = Image.new("RGB", (200, 40), (0, 0, 0))
    pixels = base.load()
    for x in range(200):
        for y in range(40):
            pixels[x, y] = (255, 0, 0) if x < 100 else (0, 0, 255)
    exif = base.getexif()
    exif[274] = 6  # Orientation
    buffer = io.BytesIO()
    base.save(buffer, format="JPEG", exif=exif)

    crop = json.dumps({"x": 0, "y": 0, "width": 40, "height": 90})
    response = client_oidc.post(
        "/api/users/me/avatar",
        files={"file": ("rotated.jpg", io.BytesIO(buffer.getvalue()), "image/jpeg")},
        data={"crop": crop},
    )
    assert response.status_code == 200, response.text

    content = client_oidc.get(response.json()["avatar_url"])
    image = Image.open(io.BytesIO(content.content)).convert("RGB")
    top_pixel = image.getpixel((128, 4))
    bottom_pixel = image.getpixel((128, 251))
    assert top_pixel[0] > 200 and top_pixel[2] < 50  # 赤に近い
    assert bottom_pixel[0] > 200 and bottom_pixel[2] < 50  # 赤に近い(全体が赤のはず)


def test_upload_avatar_crop_out_of_bounds_is_422(client_oidc: TestClient) -> None:
    login_as(client_oidc, "avatar-crop-oob@example.com", "Avatar Crop Oob")

    crop = json.dumps({"x": 90, "y": 90, "width": 50, "height": 50})
    response = client_oidc.post(
        "/api/users/me/avatar",
        files={"file": ("quad.png", io.BytesIO(_make_quadrant_png_bytes()), "image/png")},
        data={"crop": crop},
    )
    assert response.status_code == 422, response.text


def test_upload_avatar_crop_width_zero_is_422(client_oidc: TestClient) -> None:
    login_as(client_oidc, "avatar-crop-zero@example.com", "Avatar Crop Zero")

    crop = json.dumps({"x": 0, "y": 0, "width": 0, "height": 50})
    response = client_oidc.post(
        "/api/users/me/avatar",
        files={"file": ("quad.png", io.BytesIO(_make_quadrant_png_bytes()), "image/png")},
        data={"crop": crop},
    )
    assert response.status_code == 422, response.text


def test_upload_avatar_crop_malformed_json_is_422(client_oidc: TestClient) -> None:
    login_as(client_oidc, "avatar-crop-badjson@example.com", "Avatar Crop Badjson")

    response = client_oidc.post(
        "/api/users/me/avatar",
        files={"file": ("quad.png", io.BytesIO(_make_quadrant_png_bytes()), "image/png")},
        data={"crop": "not json"},
    )
    assert response.status_code == 422, response.text


def test_set_avatar_from_asset_with_crop(client_oidc: TestClient) -> None:
    login_as(client_oidc, "avatar-fromasset-crop@example.com", "Avatar From Asset Crop")

    upload = client_oidc.post(
        "/api/assets",
        files={"file": ("quad.png", io.BytesIO(_make_quadrant_png_bytes()), "image/png")},
        data={"kind": "upload"},
    )
    assert upload.status_code == 201, upload.text
    asset_id = upload.json()["id"]

    response = client_oidc.post(
        "/api/users/me/avatar/from-asset",
        json={"asset_id": asset_id, "crop": {"x": 0, "y": 0, "width": 50, "height": 50}},
    )
    assert response.status_code == 200, response.text

    content = client_oidc.get(response.json()["avatar_url"])
    image = Image.open(io.BytesIO(content.content)).convert("RGB")
    _assert_near_color(image.getpixel((10, 10)), (255, 0, 0))
    _assert_near_color(image.getpixel((200, 200)), (255, 0, 0))


def test_set_avatar_from_asset_crop_out_of_bounds_is_422(client_oidc: TestClient) -> None:
    login_as(client_oidc, "avatar-fromasset-crop-oob@example.com", "Avatar From Asset Crop Oob")

    upload = client_oidc.post(
        "/api/assets",
        files={"file": ("quad.png", io.BytesIO(_make_quadrant_png_bytes()), "image/png")},
        data={"kind": "upload"},
    )
    asset_id = upload.json()["id"]

    response = client_oidc.post(
        "/api/users/me/avatar/from-asset",
        json={"asset_id": asset_id, "crop": {"x": 90, "y": 90, "width": 50, "height": 50}},
    )
    assert response.status_code == 422, response.text


def test_set_avatar_from_asset_crop_width_zero_is_422(client_oidc: TestClient) -> None:
    login_as(client_oidc, "avatar-fromasset-crop-zero@example.com", "Avatar From Asset Crop Zero")

    upload = client_oidc.post(
        "/api/assets",
        files={"file": ("quad.png", io.BytesIO(_make_quadrant_png_bytes()), "image/png")},
        data={"kind": "upload"},
    )
    asset_id = upload.json()["id"]

    response = client_oidc.post(
        "/api/users/me/avatar/from-asset",
        json={"asset_id": asset_id, "crop": {"x": 0, "y": 0, "width": 0, "height": 50}},
    )
    assert response.status_code == 422, response.text


def test_set_avatar_from_asset(client_oidc: TestClient) -> None:
    login_as(client_oidc, "avatar-fromasset@example.com", "Avatar From Asset")

    upload = client_oidc.post(
        "/api/assets",
        files={"file": ("input.png", io.BytesIO(make_png_bytes(200, 200)), "image/png")},
        data={"kind": "upload"},
    )
    assert upload.status_code == 201, upload.text
    asset_id = upload.json()["id"]

    response = client_oidc.post("/api/users/me/avatar/from-asset", json={"asset_id": asset_id})
    assert response.status_code == 200, response.text
    assert response.json()["avatar_url"] is not None

    content = client_oidc.get(response.json()["avatar_url"])
    assert content.status_code == 200
    image = Image.open(io.BytesIO(content.content))
    assert image.size == (256, 256)


def test_set_avatar_from_deleted_asset_is_422(client_oidc: TestClient) -> None:
    login_as(client_oidc, "avatar-deletedasset@example.com", "Avatar Deleted Asset")

    upload = client_oidc.post(
        "/api/assets",
        files={"file": ("input.png", io.BytesIO(make_png_bytes()), "image/png")},
        data={"kind": "upload"},
    )
    asset_id = upload.json()["id"]
    delete_response = client_oidc.delete(f"/api/assets/{asset_id}")
    assert delete_response.status_code == 204

    response = client_oidc.post("/api/users/me/avatar/from-asset", json={"asset_id": asset_id})
    assert response.status_code == 422


def test_set_avatar_from_nonexistent_asset_is_422(client_oidc: TestClient) -> None:
    login_as(client_oidc, "avatar-noasset@example.com", "Avatar No Asset")

    response = client_oidc.post(
        "/api/users/me/avatar/from-asset", json={"asset_id": str(uuid.uuid4())}
    )
    assert response.status_code == 422


def test_delete_avatar_reverts_to_none_and_404s(client_oidc: TestClient) -> None:
    login_as(client_oidc, "avatar-delete@example.com", "Avatar Delete")

    upload = _upload_avatar(client_oidc, make_png_bytes())
    avatar_url = upload.json()["avatar_url"]
    assert client_oidc.get(avatar_url).status_code == 200

    delete_response = client_oidc.delete("/api/users/me/avatar")
    assert delete_response.status_code == 200, delete_response.text
    assert delete_response.json()["avatar_url"] is None

    me = client_oidc.get("/api/auth/me").json()
    assert me["user"]["avatar_url"] is None

    # v= のクエリを外した(=ユーザーIDだけの)URL でも404になること。
    user_id = upload.json()["id"]
    assert client_oidc.get(f"/api/users/{user_id}/avatar").status_code == 404


def test_upload_avatar_over_20mb_is_413(client_oidc: TestClient) -> None:
    login_as(client_oidc, "avatar-toolarge@example.com", "Avatar Too Large")

    oversized = b"\x00" * (MAX_AVATAR_BYTES + 1)
    response = _upload_avatar(client_oidc, oversized)
    assert response.status_code == 413


def test_upload_corrupted_data_is_422(client_oidc: TestClient) -> None:
    login_as(client_oidc, "avatar-corrupt@example.com", "Avatar Corrupt")

    response = _upload_avatar(client_oidc, b"this is not an image")
    assert response.status_code == 422


def test_avatar_endpoints_are_404_in_none_mode(client: TestClient) -> None:
    upload = _upload_avatar(client, make_png_bytes())
    assert upload.status_code == 404

    from_asset = client.post(
        "/api/users/me/avatar/from-asset", json={"asset_id": str(uuid.uuid4())}
    )
    assert from_asset.status_code == 404

    delete_response = client.delete("/api/users/me/avatar")
    assert delete_response.status_code == 404

    get_response = client.get(f"/api/users/{uuid.uuid4()}/avatar")
    assert get_response.status_code == 404


def test_other_users_avatar_is_visible(client_oidc: TestClient) -> None:
    login_as(client_oidc, "avatar-owner@example.com", "Avatar Owner")
    upload = _upload_avatar(client_oidc, make_png_bytes())
    avatar_url = upload.json()["avatar_url"]

    login_as(client_oidc, "avatar-viewer@example.com", "Avatar Viewer")
    response = client_oidc.get(avatar_url)
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/webp"


def test_avatar_response_has_etag_and_cache_control(client_oidc: TestClient) -> None:
    login_as(client_oidc, "avatar-etag@example.com", "Avatar Etag")
    upload = _upload_avatar(client_oidc, make_png_bytes())
    avatar_url = upload.json()["avatar_url"]

    response = client_oidc.get(avatar_url)
    assert response.status_code == 200
    etag = response.headers.get("ETag")
    assert etag
    assert response.headers.get("Cache-Control") == "private, max-age=31536000, immutable"

    cached = client_oidc.get(avatar_url, headers={"If-None-Match": etag})
    assert cached.status_code == 304


def test_created_by_avatar_url_appears_on_run_summary(client_oidc: TestClient) -> None:
    login_as(client_oidc, "avatar-runner@example.com", "Avatar Runner")
    _upload_avatar(client_oidc, make_png_bytes())

    run_response = client_oidc.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": "gpt-image-2.5-sunburst",
            "prompt": "a red apple on a table",
            "params": {"n": 1, "size": "1024x1024", "output_format": "png"},
        },
    )
    assert run_response.status_code == 202, run_response.text
    run_id = run_response.json()["id"]
    detail = wait_for_run_terminal(client_oidc, run_id)
    assert detail["status"] == "succeeded"
    assert detail["created_by"]["avatar_url"] is not None

    listed = client_oidc.get("/api/runs").json()
    listed_item = next(item for item in listed["items"] if item["id"] == run_id)
    assert listed_item["created_by"]["avatar_url"] is not None


# -- domain/avatars.crop_image の単体テスト -----------------------------------


def test_crop_image_returns_specified_region() -> None:
    image = Image.new("RGB", (100, 100), (0, 0, 255))
    pixels = image.load()
    for x in range(50):
        for y in range(50):
            pixels[x, y] = (255, 0, 0)

    cropped = crop_image(image, CropRect(x=0, y=0, width=50, height=50))
    assert cropped.size == (50, 50)
    assert cropped.getpixel((10, 10)) == (255, 0, 0)


def test_crop_image_at_exact_bottom_right_edge_succeeds() -> None:
    """右端・下端ぴったりに収まる範囲は、はみ出しではない。"""
    image = Image.new("RGB", (100, 100), (0, 255, 0))
    cropped = crop_image(image, CropRect(x=50, y=50, width=50, height=50))
    assert cropped.size == (50, 50)


def test_crop_image_out_of_bounds_raises_crop_error() -> None:
    image = Image.new("RGB", (100, 100), (0, 0, 0))
    with pytest.raises(CropError):
        crop_image(image, CropRect(x=90, y=0, width=50, height=50))


def test_crop_image_starting_outside_image_raises_crop_error() -> None:
    image = Image.new("RGB", (100, 100), (0, 0, 0))
    with pytest.raises(CropError):
        crop_image(image, CropRect(x=100, y=100, width=1, height=1))
