"""マスクの再編集(ADR-0010、2026-09-25 追記を一般化)。

スケッチと同じ `replaces_asset_id` の仕組みを `kind=mask` でも使えることを確認する。
未使用マスクの置き換えは旧マスクを論理削除し、使用済みマスクの置き換えは旧マスクを
証跡として残す。マスクは `source_asset_id` を持たないので常に null のまま。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import make_png_bytes, wait_for_run_terminal

_DIM = 256


def _upload(client: TestClient, kind: str = "upload") -> str:
    data = make_png_bytes(width=_DIM, height=_DIM)
    response = client.post(
        "/api/assets",
        files={"file": ("f.png", data, "image/png")},
        data={"kind": kind},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _replace_mask(client: TestClient, replaces_asset_id: str, **extra_form: str):
    data = {"kind": "mask", "replaces_asset_id": replaces_asset_id, **extra_form}
    return client.post(
        "/api/assets",
        files={"file": ("mask2.png", make_png_bytes(width=_DIM, height=_DIM), "image/png")},
        data=data,
    )


def _run_edit_with_mask(client: TestClient, base_asset_id: str, mask_asset_id: str) -> None:
    response = client.post(
        "/api/runs",
        json={
            "operation": "edit",
            "model": "gpt-image-2.5-sunburst",
            "prompt": "edit with mask",
            "params": {"n": 1},
            "inputs": [
                {"asset_id": base_asset_id, "role": "image", "position": 0},
                {"asset_id": mask_asset_id, "role": "mask", "position": 0},
            ],
        },
    )
    assert response.status_code == 202, response.text
    detail = wait_for_run_terminal(client, response.json()["id"])
    assert detail["status"] == "succeeded", detail


def test_replace_unused_mask_deletes_old_and_creates_new(client: TestClient) -> None:
    mask_id = _upload(client, kind="mask")

    response = _replace_mask(client, mask_id)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["kind"] == "mask"
    assert body["id"] != mask_id
    assert body["source_asset_id"] is None

    old = client.get(f"/api/assets/{mask_id}")
    assert old.status_code == 200, old.text
    assert old.json()["deleted_at"] is not None

    listed = client.get("/api/assets", params={"kind": "mask"}).json()["items"]
    listed_ids = [item["id"] for item in listed]
    assert mask_id not in listed_ids
    assert body["id"] in listed_ids


def test_replace_used_mask_keeps_old(client: TestClient) -> None:
    base_id = _upload(client)
    mask_id = _upload(client, kind="mask")
    _run_edit_with_mask(client, base_id, mask_id)

    response = _replace_mask(client, mask_id)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["id"] != mask_id
    assert body["source_asset_id"] is None

    old = client.get(f"/api/assets/{mask_id}")
    old_body = old.json()
    assert old_body["deleted_at"] is None
    assert old_body["used_as_input"] is True


def test_replace_mask_rejected_when_target_kind_mismatch(client: TestClient) -> None:
    base_id = _upload(client)

    response = _replace_mask(client, base_id)
    assert response.status_code == 422, response.text


def test_replace_sketch_rejected_when_target_kind_mismatch(client: TestClient) -> None:
    """逆方向(スケッチの置き換えでマスクを指す)も同じ 422 になる。"""
    mask_id = _upload(client, kind="mask")

    response = client.post(
        "/api/assets",
        files={"file": ("sketch.png", make_png_bytes(width=_DIM, height=_DIM), "image/png")},
        data={"kind": "sketch", "replaces_asset_id": mask_id},
    )
    assert response.status_code == 422, response.text


def test_replace_mask_rejected_when_missing(client: TestClient) -> None:
    missing_id = "00000000-0000-0000-0000-000000000000"
    response = _replace_mask(client, missing_id)
    assert response.status_code == 422, response.text


def test_replace_mask_rejected_when_deleted(client: TestClient) -> None:
    mask_id = _upload(client, kind="mask")
    assert client.delete(f"/api/assets/{mask_id}").status_code == 204

    response = _replace_mask(client, mask_id)
    assert response.status_code == 422, response.text


def test_mask_rejects_source_asset_id_and_replaces_together(client: TestClient) -> None:
    base_id = _upload(client)
    mask_id = _upload(client, kind="mask")

    response = client.post(
        "/api/assets",
        files={"file": ("mask.png", make_png_bytes(width=_DIM, height=_DIM), "image/png")},
        data={
            "kind": "mask",
            "source_asset_id": base_id,
            "replaces_asset_id": mask_id,
        },
    )
    assert response.status_code == 422, response.text
