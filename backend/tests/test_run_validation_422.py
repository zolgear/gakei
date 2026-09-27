"""POST /api/runs のサーバー側検証が 422 を返すこと。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import make_png_bytes


def _upload(client: TestClient, width: int = 128, height: int = 128) -> str:
    data = make_png_bytes(width=width, height=height)
    response = client.post(
        "/api/assets",
        files={"file": ("base.png", data, "image/png")},
        data={"kind": "upload"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _upload_mask(client: TestClient, width: int, height: int) -> str:
    data = make_png_bytes(width=width, height=height)
    response = client.post(
        "/api/assets",
        files={"file": ("mask.png", data, "image/png")},
        data={"kind": "mask"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_mask_dimension_mismatch_returns_422(client_no_runner: TestClient) -> None:
    base_asset_id = _upload(client_no_runner, width=128, height=128)
    mask_asset_id = _upload_mask(client_no_runner, width=64, height=64)  # 寸法が異なる

    response = client_no_runner.post(
        "/api/runs",
        json={
            "operation": "edit",
            "model": "gpt-image-2.5-sunburst",
            "prompt": "edit with mismatched mask",
            "params": {},
            "inputs": [
                {"asset_id": base_asset_id, "role": "image", "position": 0},
                {"asset_id": mask_asset_id, "role": "mask", "position": 0},
            ],
        },
    )
    assert response.status_code == 422


def test_more_than_16_image_inputs_returns_422(client_no_runner: TestClient) -> None:
    asset_ids = [_upload(client_no_runner) for _ in range(17)]
    inputs = [
        {"asset_id": asset_id, "role": "image", "position": i}
        for i, asset_id in enumerate(asset_ids)
    ]

    response = client_no_runner.post(
        "/api/runs",
        json={
            "operation": "edit",
            "model": "gpt-image-2.5-sunburst",
            "prompt": "too many inputs",
            "params": {},
            "inputs": inputs,
        },
    )
    assert response.status_code == 422


def test_transparent_background_with_jpeg_returns_422(client_no_runner: TestClient) -> None:
    response = client_no_runner.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": "gpt-image-2.5-sunburst",
            "prompt": "transparent jpeg is invalid",
            "params": {"background": "transparent", "output_format": "jpeg"},
        },
    )
    assert response.status_code == 422


def test_unknown_model_returns_422(client_no_runner: TestClient) -> None:
    response = client_no_runner.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": "no-such-model",
            "prompt": "hello",
            "params": {},
        },
    )
    assert response.status_code == 422


def test_generate_with_inputs_returns_422(client_no_runner: TestClient) -> None:
    base_asset_id = _upload(client_no_runner)
    response = client_no_runner.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": "gpt-image-2.5-sunburst",
            "prompt": "generate cannot take inputs",
            "params": {},
            "inputs": [{"asset_id": base_asset_id, "role": "image", "position": 0}],
        },
    )
    assert response.status_code == 422
