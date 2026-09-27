"""ADR-0019: 実行者(Run)・アップロード者(Asset)の記録。"""

from __future__ import annotations

import io
import uuid

from fastapi.testclient import TestClient

from app.domain.models import Asset, Run
from tests.conftest import login_as, make_png_bytes, wait_for_run_terminal


def _create_generate_run(client: TestClient) -> str:
    response = client.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": "gpt-image-2.5-sunburst",
            "prompt": "a red apple on a table",
            "params": {"n": 1, "size": "1024x1024", "output_format": "png"},
        },
    )
    assert response.status_code == 202, response.text
    return response.json()["id"]


def test_run_created_by_is_recorded_in_oidc_mode(client_oidc: TestClient) -> None:
    login_as(client_oidc, "runner@example.com", "実行者")

    run_id = _create_generate_run(client_oidc)
    detail = wait_for_run_terminal(client_oidc, run_id)

    assert detail["status"] == "succeeded"
    assert detail["created_by"] is not None
    assert detail["created_by"]["email"] == "runner@example.com"
    assert detail["created_by"]["name"] == "実行者"

    # 一覧(RunSummary)にも同じ created_by が出ること。
    listed = client_oidc.get("/api/runs").json()
    listed_item = next(item for item in listed["items"] if item["id"] == run_id)
    assert listed_item["created_by"]["email"] == "runner@example.com"

    # 生成出力 Asset は run.created_by_user_id をそのまま引き継ぐ(DB・API 両方で確認)。
    output_asset_id = detail["outputs"][0]["asset_id"]
    asset_detail = client_oidc.get(f"/api/assets/{output_asset_id}").json()
    assert asset_detail["created_by"]["email"] == "runner@example.com"

    session_factory = client_oidc.app.state.session_factory
    with session_factory() as db:
        run_row = db.get(Run, uuid.UUID(run_id))
        asset_row = db.get(Asset, uuid.UUID(output_asset_id))
        assert run_row.created_by_user_id is not None
        assert asset_row.created_by_user_id == run_row.created_by_user_id


def test_asset_upload_created_by_is_recorded_in_oidc_mode(client_oidc: TestClient) -> None:
    login_as(client_oidc, "uploader@example.com", "アップロード者")

    response = client_oidc.post(
        "/api/assets",
        files={"file": ("input.png", io.BytesIO(make_png_bytes()), "image/png")},
        data={"kind": "upload"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["created_by"]["email"] == "uploader@example.com"

    session_factory = client_oidc.app.state.session_factory
    with session_factory() as db:
        asset_row = db.get(Asset, uuid.UUID(body["id"]))
        assert asset_row.created_by_user_id is not None


def test_created_by_is_null_in_none_mode(client: TestClient) -> None:
    run_id = _create_generate_run(client)
    detail = wait_for_run_terminal(client, run_id)
    assert detail["created_by"] is None

    output_asset_id = detail["outputs"][0]["asset_id"]
    asset_detail = client.get(f"/api/assets/{output_asset_id}").json()
    assert asset_detail["created_by"] is None

    upload_response = client.post(
        "/api/assets",
        files={"file": ("input.png", io.BytesIO(make_png_bytes()), "image/png")},
        data={"kind": "upload"},
    )
    assert upload_response.json()["created_by"] is None

    session_factory = client.app.state.session_factory
    with session_factory() as db:
        run_row = db.get(Run, uuid.UUID(run_id))
        asset_row = db.get(Asset, uuid.UUID(output_asset_id))
        assert run_row.created_by_user_id is None
        assert asset_row.created_by_user_id is None
