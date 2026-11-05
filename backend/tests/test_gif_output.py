"""アニメーション GIF の出力(ADR-0061)。

Fake プロバイダーで、検証・保存・派生・入力の断りを確かめる。
"""

from __future__ import annotations

import io

from fastapi.testclient import TestClient
from PIL import Image

from app.domain.pricing import estimate_cost
from app.providers.openai_spec import build_capabilities
from tests.conftest import wait_for_run_terminal

MODEL_26 = "gpt-image-2.6-flare"


def _gif_bytes(frames: int = 3) -> bytes:
    images = [Image.new("RGB", (64, 64), (k * 40, 0, 0)) for k in range(frames)]
    buffer = io.BytesIO()
    images[0].save(buffer, format="GIF", save_all=True, append_images=images[1:], loop=0)
    return buffer.getvalue()


def _params(caps, model: str, operation: str) -> dict:
    model_caps = next(m for m in caps.models if m.model == model)
    op = next(o for o in model_caps.operations if o.operation == operation)
    return {p.name: p for p in op.params}


def _post_generate(client: TestClient, params: dict, model: str = MODEL_26):
    return client.post(
        "/api/runs",
        json={"operation": "generate", "model": model, "prompt": "a paper boat", "params": params},
    )


# -- capabilities -------------------------------------------------------------


def test_gif_is_offered_only_for_26_generate() -> None:
    caps = build_capabilities("fake")
    gen = _params(caps, MODEL_26, "generate")
    assert "gif" in (gen["output_format"].choices or [])
    assert gen["frame_count"].minimum == 2
    assert gen["frame_count"].maximum == 16
    assert (gen["frame_duration_ms"].minimum, gen["frame_duration_ms"].maximum) == (20, 2000)

    edit = _params(caps, MODEL_26, "edit")
    assert "gif" not in (edit["output_format"].choices or [])
    assert "frame_count" not in edit
    gen_25 = _params(caps, "gpt-image-2.5-flare", "generate")
    assert "gif" not in (gen_25["output_format"].choices or [])
    assert caps.gif_frame_size is not None


# -- 検証 -----------------------------------------------------------------------


def test_gif_size_is_validated_as_one_frame(client: TestClient) -> None:
    # 512x512 は通常の最小の総ピクセルを下回るが、GIF の1コマとしては通る。
    assert _post_generate(client, {"size": "512x512"}).status_code == 422
    response = _post_generate(client, {"size": "512x512", "output_format": "gif", "frame_count": 4})
    assert response.status_code == 202, response.text
    # 1コマの長辺の上限(1920)を超えると断る。
    too_big = _post_generate(client, {"size": "2048x1024", "output_format": "gif"})
    assert too_big.status_code == 422


def test_gif_only_params_need_gif(client: TestClient) -> None:
    assert _post_generate(client, {"size": "1024x1024", "frame_count": 4}).status_code == 422
    assert (
        _post_generate(
            client, {"size": "auto", "output_format": "gif", "frame_count": 17}
        ).status_code
        == 422
    )
    # GIF はストリーミングできないので、途中経過は指定できない。
    assert (
        _post_generate(
            client, {"size": "auto", "output_format": "gif", "partial_images": 1}
        ).status_code
        == 422
    )


# -- 保存・派生・表示 ---------------------------------------------------------------


def test_gif_output_is_kept_as_is_and_derived_from_first_frame(client: TestClient) -> None:
    response = _post_generate(
        client,
        {"size": "512x512", "output_format": "gif", "frame_count": 4, "frame_duration_ms": 120},
    )
    assert response.status_code == 202, response.text
    detail = wait_for_run_terminal(client, response.json()["id"])
    assert detail["status"] == "succeeded", detail
    asset_id = detail["outputs"][0]["asset_id"]

    asset = client.get(f"/api/assets/{asset_id}").json()
    assert asset["mime"] == "image/gif"

    original = client.get(f"/api/assets/{asset_id}/content", params={"variant": "original"})
    assert original.status_code == 200
    assert original.headers["content-type"] == "image/gif"
    with Image.open(io.BytesIO(original.content)) as image:
        assert image.n_frames == 4
        assert image.info["duration"] == 120

    thumb = client.get(f"/api/assets/{asset_id}/content", params={"variant": "thumb"})
    assert thumb.status_code == 200
    assert thumb.headers["content-type"] == "image/webp"
    with Image.open(io.BytesIO(thumb.content)) as image:
        assert image.size == (512, 512)

    # ADR-0061 6章: GIF は入力にしない。
    edit = client.post(
        "/api/runs",
        json={
            "operation": "edit",
            "model": MODEL_26,
            "prompt": "make it blue",
            "params": {},
            "inputs": [{"asset_id": asset_id, "role": "image", "position": 0}],
        },
    )
    assert edit.status_code == 422
    assert "GIF" in edit.json()["detail"]


def test_gif_upload_is_rejected(client: TestClient) -> None:
    response = client.post("/api/assets", files={"file": ("anim.gif", _gif_bytes(), "image/gif")})
    assert response.status_code in (400, 415, 422), response.text


# -- 参考価格 -------------------------------------------------------------------


def test_gif_price_is_not_estimated() -> None:
    result = estimate_cost(
        model=MODEL_26,
        quality="low",
        size="512x512",
        n=1,
        prompt_length=10,
        output_format="gif",
    )
    assert result.total_usd is None
    assert result.unavailable_reason == "animated_output"
