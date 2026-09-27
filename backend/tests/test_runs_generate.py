"""generate Run の成功フロー。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import make_png_bytes, wait_for_run_terminal


def test_generate_run_succeeds_with_two_outputs(client: TestClient) -> None:
    response = client.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": "gpt-image-2.5-sunburst",
            "prompt": "a red apple on a table",
            "params": {"n": 2, "size": "1024x1024", "output_format": "png"},
        },
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]

    detail = wait_for_run_terminal(client, run_id)
    assert detail["status"] == "succeeded"
    assert len(detail["outputs"]) == 2

    output_indexes = sorted(o["output_index"] for o in detail["outputs"])
    assert output_indexes == [0, 1]

    assert detail["usage"] is not None
    assert detail["usage"]["output_tokens"] > 0

    # 出力Assetがビューアで表示できること(サムネイル取得)
    output_asset_id = detail["outputs"][0]["asset_id"]
    content = client.get(f"/api/assets/{output_asset_id}/content", params={"variant": "thumb"})
    assert content.status_code == 200
    assert content.headers["content-type"] == "image/webp"


def test_generate_run_appears_in_list(client: TestClient) -> None:
    response = client.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": "gpt-image-2.5-sunburst",
            "prompt": "a blue sky",
            "params": {"n": 1},
        },
    )
    run_id = response.json()["id"]
    wait_for_run_terminal(client, run_id)

    listed = client.get("/api/runs")
    assert listed.status_code == 200
    ids = [item["id"] for item in listed.json()["items"]]
    assert run_id in ids


# -- moderation: サーバーが params へ入れる(フォームからは指定できない) --------------


def test_generate_run_params_include_default_moderation(client: TestClient) -> None:
    """MODERATION を指定しない場合、既定の low が params に入る。"""
    response = client.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": "gpt-image-2.5-sunburst",
            "prompt": "a red apple on a table",
            "params": {"n": 1},
        },
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]

    detail = wait_for_run_terminal(client, run_id)
    assert detail["params"]["moderation"] == "low"


def test_generate_run_moderation_follows_environment_setting(
    monkeypatch: pytest.MonkeyPatch, data_dir: Path
) -> None:
    """MODERATION=auto を与えたアプリでは、params の moderation が auto になる。"""
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("FAKE_PROVIDER", "1")
    monkeypatch.setenv("MODERATION", "auto")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    from app.main import create_app

    app = create_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/runs",
            json={
                "operation": "generate",
                "model": "gpt-image-2.5-sunburst",
                "prompt": "a red apple on a table",
                "params": {"n": 1},
            },
        )
        assert response.status_code == 202, response.text
        run_id = response.json()["id"]

        detail = wait_for_run_terminal(client, run_id)
        assert detail["params"]["moderation"] == "auto"


def test_generate_run_rejects_client_supplied_moderation(client: TestClient) -> None:
    """moderation は capabilities から消えたので、クライアントが送ると未知パラメーターで 422。"""
    response = client.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": "gpt-image-2.5-sunburst",
            "prompt": "a red apple on a table",
            "params": {"n": 1, "moderation": "auto"},
        },
    )
    assert response.status_code == 422, response.text


def test_edit_run_params_do_not_include_moderation(client: TestClient) -> None:
    """moderation は Generate 専用なので、edit の params には入らない。"""
    upload = client.post(
        "/api/assets",
        files={"file": ("base.png", make_png_bytes(), "image/png")},
        data={"kind": "upload"},
    )
    assert upload.status_code == 201, upload.text
    base_asset_id = upload.json()["id"]

    response = client.post(
        "/api/runs",
        json={
            "operation": "edit",
            "model": "gpt-image-2.5-sunburst",
            "prompt": "make it look like sunset",
            "params": {"n": 1},
            "inputs": [{"asset_id": base_asset_id, "role": "image", "position": 0}],
        },
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]

    detail = wait_for_run_terminal(client, run_id)
    assert "moderation" not in detail["params"]
