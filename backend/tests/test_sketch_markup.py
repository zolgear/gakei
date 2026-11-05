"""スケッチの線を印(markup)で送る(ADR-0010、2026-11-05 追記)。"""

from __future__ import annotations

import io
from typing import Any

from fastapi.testclient import TestClient
from PIL import Image

from tests.conftest import make_png_bytes, wait_for_run_terminal


def _lines_png(width: int, height: int) -> bytes:
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    for x in range(10, 40):
        image.putpixel((x, 20), (229, 72, 77, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _upload(client: TestClient, data: bytes, kind: str = "upload", **extra: str) -> str:
    response = client.post(
        "/api/assets",
        files={"file": ("x.png", data, "image/png")},
        data={"kind": kind, **extra},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _base_and_lines(client: TestClient) -> tuple[str, str]:
    base = _upload(client, make_png_bytes(200, 100))
    lines = _upload(client, _lines_png(200, 100), kind="sketch", source_asset_id=base)
    return base, lines


def _post(client: TestClient, model: str, inputs: list[dict[str, Any]], **extra: Any):
    return client.post(
        "/api/runs",
        json={
            "operation": "edit",
            "model": model,
            "prompt": "make the marked cup blue",
            "params": {"n": 1, "quality": "low"},
            "inputs": inputs,
            **extra,
        },
    )


def _create(client: TestClient, model: str, inputs: list[dict[str, Any]], **extra: Any):
    response = _post(client, model, inputs, **extra)
    assert response.status_code == 202, response.text
    return wait_for_run_terminal(client, response.json()["id"])


def test_capabilities_report_markup_for_2_5_and_2_6_edit(client: TestClient) -> None:
    caps = client.get("/api/capabilities").json()
    provider = next(p for p in caps["providers"] if p["provider"] == caps["default_provider"])
    edit = {
        m["model"]: next(o for o in m["operations"] if o["operation"] == "edit")
        for m in provider["models"]
    }
    assert edit["gpt-image-2.6-flare"]["supports_markup"] is True
    assert edit["gpt-image-2.5-flare"]["supports_markup"] is True
    assert edit["gpt-image-2"]["supports_markup"] is False


def test_lines_are_sent_as_markup_on_a_supporting_model(client: TestClient) -> None:
    base, lines = _base_and_lines(client)
    detail = _create(
        client,
        "gpt-image-2.6-flare",
        [
            {"asset_id": base, "role": "image", "position": 0},
            {"asset_id": lines, "role": "markup", "position": 0},
        ],
    )
    assert detail["status"] == "succeeded", detail
    recorded = sorted((i["role"], i["position"], i["asset_id"]) for i in detail["inputs"])
    assert recorded == [("image", 0, base), ("markup", 0, lines)]


def test_lines_are_flattened_for_a_model_without_markup(client: TestClient) -> None:
    # 過去のモデルは、これまでどおり下地と線を合わせた1枚のスケッチで送る。
    base, lines = _base_and_lines(client)
    detail = _create(
        client,
        "gpt-image-2",
        [
            {"asset_id": base, "role": "image", "position": 0},
            {"asset_id": lines, "role": "markup", "position": 0},
        ],
    )
    assert detail["status"] == "succeeded", detail
    assert [i["role"] for i in detail["inputs"]] == ["image"]
    sketch_id = detail["inputs"][0]["asset_id"]
    sketch = client.get(f"/api/assets/{sketch_id}").json()
    assert sketch["kind"] == "sketch"
    assert sketch["source_asset_id"] == base
    assert (sketch["width"], sketch["height"]) == (200, 100)
    original = client.get(f"/api/assets/{sketch_id}/content", params={"variant": "original"})
    with Image.open(io.BytesIO(original.content)) as image:
        pixel = image.convert("RGBA").getpixel((20, 20))
    assert pixel[:3] == (229, 72, 77)  # 線が焼き込まれている


def test_lines_are_flattened_when_a_mask_is_present(client: TestClient) -> None:
    # API は markup と mask を同時に受けない。
    base, lines = _base_and_lines(client)
    mask = _upload(client, make_png_bytes(200, 100, (0, 0, 0)), kind="mask")
    detail = _create(
        client,
        "gpt-image-2.6-flare",
        [
            {"asset_id": base, "role": "image", "position": 0},
            {"asset_id": lines, "role": "markup", "position": 0},
            {"asset_id": mask, "role": "mask", "position": 0},
        ],
    )
    assert detail["status"] == "succeeded", detail
    roles = sorted(i["role"] for i in detail["inputs"])
    assert roles == ["image", "mask"]


def test_regions_fall_back_to_image_and_text_when_lines_use_the_markup(
    client: TestClient,
) -> None:
    # 印は1つしか送れないので、線を印で送る Run の四角は「画像と文」にする。
    base, lines = _base_and_lines(client)
    detail = _create(
        client,
        "gpt-image-2.6-flare",
        [
            {"asset_id": base, "role": "image", "position": 0},
            {"asset_id": lines, "role": "markup", "position": 0},
        ],
        regions=[{"number": 1, "box": [0.1, 0.2, 0.5, 0.6], "instruction": "a hat"}],
    )
    assert detail["status"] == "succeeded", detail
    assert detail["regions"]["delivery"] == "image_and_text"
    markups = [i for i in detail["inputs"] if i["role"] == "markup"]
    assert [m["asset_id"] for m in markups] == [lines]
    assert len([i for i in detail["inputs"] if i["role"] == "image"]) == 2


def test_lines_of_another_size_are_rejected(client: TestClient) -> None:
    base = _upload(client, make_png_bytes(200, 100))
    lines = _upload(client, _lines_png(100, 50), kind="sketch", source_asset_id=base)
    response = _post(
        client,
        "gpt-image-2.6-flare",
        [
            {"asset_id": base, "role": "image", "position": 0},
            {"asset_id": lines, "role": "markup", "position": 0},
        ],
    )
    assert response.status_code == 422, response.text


def test_lines_need_the_first_image(client: TestClient) -> None:
    base, lines = _base_and_lines(client)
    response = _post(
        client,
        "gpt-image-2.6-flare",
        [
            {"asset_id": base, "role": "image", "position": 0},
            {"asset_id": lines, "role": "markup", "position": 1},
        ],
    )
    assert response.status_code == 422, response.text
