"""領域の四角を印(markup)で送る(ADR-0053 5章、2026-11-05 追記)。GPT Image 2.5 / 2.6 の Edit。"""

from __future__ import annotations

import io
from typing import Any

from fastapi.testclient import TestClient
from PIL import Image

from tests.conftest import make_png_bytes, wait_for_run_terminal

_MODEL = "gpt-image-2.6-flare"


def _upload(client: TestClient, data: bytes | None = None, kind: str = "upload") -> str:
    response = client.post(
        "/api/assets",
        files={"file": ("x.png", data or make_png_bytes(200, 100), "image/png")},
        data={"kind": kind},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _create(client: TestClient, inputs: list[dict[str, Any]], regions: list[dict[str, Any]]):
    response = client.post(
        "/api/runs",
        json={
            "operation": "edit",
            "model": _MODEL,
            "prompt": "make it nice",
            "params": {"n": 1, "quality": "low"},
            "inputs": inputs,
            "regions": regions,
        },
    )
    assert response.status_code == 202, response.text
    return wait_for_run_terminal(client, response.json()["id"])


def _box(number: int = 1, **extra: Any) -> dict[str, Any]:
    return {"number": number, "box": [0.1, 0.2, 0.5, 0.6], "instruction": "a red hat", **extra}


def test_regions_are_sent_as_a_transparent_markup(client: TestClient) -> None:
    primary = _upload(client)
    second = _upload(client, make_png_bytes(80, 80, (10, 200, 10)))
    reference = _upload(client, make_png_bytes(90, 90, (10, 10, 200)))
    regions = [_box(1), _box(2, reference_asset_id=reference), _box(3, reference_asset_id=second)]
    detail = _create(
        client,
        [
            {"asset_id": primary, "role": "image", "position": 0},
            {"asset_id": second, "role": "image", "position": 1},
        ],
        regions,
    )
    assert detail["status"] == "succeeded", detail

    # 印は画像の列に入れず role=markup で、四角を描いた画像と同じ位置(利用者の入力の直後)に
    # 残す。参照画像はその後ろ。
    images = sorted(
        (i for i in detail["inputs"] if i["role"] == "image"), key=lambda i: i["position"]
    )
    assert [i["asset_id"] for i in images] == [primary, second, reference]
    assert [i["position"] for i in images] == [0, 1, 3]
    markups = [i for i in detail["inputs"] if i["role"] == "markup"]
    assert [m["position"] for m in markups] == [2]
    markup_id = markups[0]["asset_id"]

    markup = client.get(f"/api/assets/{markup_id}").json()
    assert markup["kind"] == "regions"
    assert markup["source_asset_id"] == primary
    # 1枚目と同じ大きさの、透過の PNG。
    assert (markup["width"], markup["height"]) == (200, 100)
    original = client.get(f"/api/assets/{markup_id}/content", params={"variant": "original"})
    with Image.open(io.BytesIO(original.content)) as image:
        assert image.format == "PNG"
        assert image.mode == "RGBA"
        assert image.getpixel((199, 99))[3] == 0  # 四角の外は透明

    record = detail["regions"]
    assert record["delivery"] == "markup"
    assert record["overlay_asset_id"] == markup_id
    text = record["appended_text"]
    assert "マークアップ" in text
    assert "3枚目の画像を参考" in text  # 足した参照画像
    assert "2枚目の画像を参考" in text  # 利用者の入力にある参照画像


def test_regions_with_a_mask_fall_back_to_image_and_text(client: TestClient) -> None:
    # API は markup と mask を同時に受けないので、マスクがあれば四角を描いた画像で送る。
    primary = _upload(client)
    mask = _upload(client, make_png_bytes(200, 100, (0, 0, 0)), kind="mask")
    detail = _create(
        client,
        [
            {"asset_id": primary, "role": "image", "position": 0},
            {"asset_id": mask, "role": "mask", "position": 0},
        ],
        [_box(1)],
    )
    assert detail["status"] == "succeeded", detail
    assert detail["regions"]["delivery"] == "image_and_text"
    assert not [i for i in detail["inputs"] if i["role"] == "markup"]
    images = [i for i in detail["inputs"] if i["role"] == "image"]
    assert len(images) == 2  # 主たる入力と、四角を描いた画像


def test_markup_does_not_count_toward_the_input_limit(client: TestClient) -> None:
    # 印は画像の列に入らないので、16 枚の入力でも四角を付けられる。
    ids = [_upload(client) for _ in range(16)]
    detail = _create(
        client,
        [{"asset_id": asset_id, "role": "image", "position": i} for i, asset_id in enumerate(ids)],
        [_box(1)],
    )
    assert detail["status"] == "succeeded", detail
    assert detail["regions"]["delivery"] == "markup"
