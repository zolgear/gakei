"""`kind=sketch` の Asset(ADR-0010)。保存後は通常の入力画像として扱えることを確認する。"""

from __future__ import annotations

import io

from fastapi.testclient import TestClient
from PIL import Image

from tests.conftest import make_png_bytes, wait_for_run_terminal


def test_create_sketch_asset_accepts_png(client: TestClient) -> None:
    data = make_png_bytes()
    response = client.post(
        "/api/assets",
        files={"file": ("sketch.png", data, "image/png")},
        data={"kind": "sketch"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["kind"] == "sketch"


def test_create_sketch_asset_rejects_jpeg(client: TestClient) -> None:
    image = Image.new("RGB", (32, 32), (0, 0, 0))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")

    response = client.post(
        "/api/assets",
        files={"file": ("sketch.jpg", buffer.getvalue(), "image/jpeg")},
        data={"kind": "sketch"},
    )
    assert response.status_code == 422, response.text


def test_list_assets_filters_by_sketch_kind(client: TestClient) -> None:
    sketch_data = make_png_bytes()
    upload_data = make_png_bytes(color=(10, 20, 30))

    sketch_response = client.post(
        "/api/assets",
        files={"file": ("sketch.png", sketch_data, "image/png")},
        data={"kind": "sketch"},
    )
    assert sketch_response.status_code == 201, sketch_response.text
    sketch_id = sketch_response.json()["id"]

    upload_response = client.post(
        "/api/assets",
        files={"file": ("upload.png", upload_data, "image/png")},
        data={"kind": "upload"},
    )
    assert upload_response.status_code == 201, upload_response.text

    list_response = client.get("/api/assets", params={"kind": "sketch"})
    assert list_response.status_code == 200, list_response.text
    items = list_response.json()["items"]
    assert [item["id"] for item in items] == [sketch_id]
    assert items[0]["kind"] == "sketch"


def test_edit_run_accepts_sketch_as_primary_image_input(client: TestClient) -> None:
    data = make_png_bytes(width=256, height=256)
    sketch_response = client.post(
        "/api/assets",
        files={"file": ("sketch.png", data, "image/png")},
        data={"kind": "sketch"},
    )
    assert sketch_response.status_code == 201, sketch_response.text
    sketch_id = sketch_response.json()["id"]

    run_response = client.post(
        "/api/runs",
        json={
            "operation": "edit",
            "model": "gpt-image-2.5-sunburst",
            "prompt": "turn this sketch into a photorealistic image",
            "params": {"n": 1},
            "inputs": [{"asset_id": sketch_id, "role": "image", "position": 0}],
        },
    )
    assert run_response.status_code == 202, run_response.text
    run_id = run_response.json()["id"]

    detail = wait_for_run_terminal(client, run_id)
    assert detail["status"] == "succeeded"
    assert detail["inputs"][0]["asset_id"] == sketch_id


# -- source_asset_id (上描きスケッチの下地。ADR-0010、2026-09-23 追記) -----------------


def _upload(client: TestClient, kind: str = "upload") -> str:
    response = client.post(
        "/api/assets",
        files={"file": ("f.png", make_png_bytes(), "image/png")},
        data={"kind": kind},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_sketch_over_asset_records_source_asset_id(client: TestClient) -> None:
    base_id = _upload(client)

    response = client.post(
        "/api/assets",
        files={"file": ("sketch.png", make_png_bytes(), "image/png")},
        data={"kind": "sketch", "source_asset_id": base_id},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["source_asset_id"] == base_id


def test_blank_sketch_has_no_source_asset_id(client: TestClient) -> None:
    response = client.post(
        "/api/assets",
        files={"file": ("sketch.png", make_png_bytes(), "image/png")},
        data={"kind": "sketch"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["source_asset_id"] is None


def test_non_sketch_asset_has_no_source_asset_id(client: TestClient) -> None:
    response = client.post(
        "/api/assets",
        files={"file": ("f.png", make_png_bytes(), "image/png")},
        data={"kind": "upload"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["source_asset_id"] is None


def test_source_asset_id_rejected_for_non_sketch_kind(client: TestClient) -> None:
    base_id = _upload(client)

    response = client.post(
        "/api/assets",
        files={"file": ("f.png", make_png_bytes(), "image/png")},
        data={"kind": "upload", "source_asset_id": base_id},
    )
    assert response.status_code == 422, response.text


def test_source_asset_id_rejected_for_mask_kind(client: TestClient) -> None:
    base_id = _upload(client)

    response = client.post(
        "/api/assets",
        files={"file": ("f.png", make_png_bytes(), "image/png")},
        data={"kind": "mask", "source_asset_id": base_id},
    )
    assert response.status_code == 422, response.text


def test_source_asset_id_rejected_when_asset_missing(client: TestClient) -> None:
    missing_id = "00000000-0000-0000-0000-000000000000"

    response = client.post(
        "/api/assets",
        files={"file": ("sketch.png", make_png_bytes(), "image/png")},
        data={"kind": "sketch", "source_asset_id": missing_id},
    )
    assert response.status_code == 422, response.text


def test_source_asset_id_rejected_when_asset_deleted(client: TestClient) -> None:
    base_id = _upload(client)
    assert client.delete(f"/api/assets/{base_id}").status_code == 204

    response = client.post(
        "/api/assets",
        files={"file": ("sketch.png", make_png_bytes(), "image/png")},
        data={"kind": "sketch", "source_asset_id": base_id},
    )
    assert response.status_code == 422, response.text


# -- 未使用スケッチの再編集(ADR-0010、2026-09-25 追記) --------------------------------


def _create_sketch(client: TestClient, source_asset_id: str | None = None, **size: int) -> str:
    data: dict[str, str] = {"kind": "sketch"}
    if source_asset_id is not None:
        data["source_asset_id"] = source_asset_id
    png = make_png_bytes(**size) if size else make_png_bytes()
    response = client.post(
        "/api/assets",
        files={"file": ("sketch.png", png, "image/png")},
        data=data,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _replace_sketch(client: TestClient, replaces_asset_id: str, **extra_form: str):
    data = {"kind": "sketch", "replaces_asset_id": replaces_asset_id, **extra_form}
    return client.post(
        "/api/assets",
        files={"file": ("sketch2.png", make_png_bytes(), "image/png")},
        data=data,
    )


def _run_edit_with_sketch(client: TestClient, sketch_id: str) -> None:
    response = client.post(
        "/api/runs",
        json={
            "operation": "edit",
            "model": "gpt-image-2.5-sunburst",
            "prompt": "turn this sketch into a photorealistic image",
            "params": {"n": 1},
            "inputs": [{"asset_id": sketch_id, "role": "image", "position": 0}],
        },
    )
    assert response.status_code == 202, response.text
    detail = wait_for_run_terminal(client, response.json()["id"])
    assert detail["status"] == "succeeded", detail


def test_replace_unused_sketch_inherits_source_and_deletes_old(client: TestClient) -> None:
    base_id = _upload(client)
    sketch_id = _create_sketch(client, source_asset_id=base_id)

    response = _replace_sketch(client, sketch_id)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["source_asset_id"] == base_id
    assert body["id"] != sketch_id

    old = client.get(f"/api/assets/{sketch_id}")
    assert old.status_code == 200, old.text
    assert old.json()["deleted_at"] is not None

    listed = client.get("/api/assets", params={"kind": "sketch"}).json()["items"]
    listed_ids = [item["id"] for item in listed]
    assert sketch_id not in listed_ids
    assert body["id"] in listed_ids


def test_replace_blank_sketch_keeps_source_null(client: TestClient) -> None:
    sketch_id = _create_sketch(client)

    response = _replace_sketch(client, sketch_id)
    assert response.status_code == 201, response.text
    assert response.json()["source_asset_id"] is None

    old = client.get(f"/api/assets/{sketch_id}")
    assert old.json()["deleted_at"] is not None


def test_replace_used_sketch_keeps_old_and_chains(client: TestClient) -> None:
    sketch_id = _create_sketch(client, width=256, height=256)
    _run_edit_with_sketch(client, sketch_id)

    response = _replace_sketch(client, sketch_id)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["source_asset_id"] == sketch_id

    old = client.get(f"/api/assets/{sketch_id}")
    old_body = old.json()
    assert old_body["deleted_at"] is None
    assert old_body["used_as_input"] is True


def test_replace_chain_of_unused_sketches_inherits_original_source(client: TestClient) -> None:
    base_id = _upload(client)
    sketch1 = _create_sketch(client, source_asset_id=base_id)
    replace1 = _replace_sketch(client, sketch1)
    assert replace1.status_code == 201, replace1.text
    sketch2 = replace1.json()["id"]

    replace2 = _replace_sketch(client, sketch2)
    assert replace2.status_code == 201, replace2.text
    body = replace2.json()
    assert body["source_asset_id"] == base_id

    for old_id in (sketch1, sketch2):
        old = client.get(f"/api/assets/{old_id}")
        assert old.json()["deleted_at"] is not None


def test_replaces_asset_id_rejected_for_non_sketch_kind(client: TestClient) -> None:
    base_id = _upload(client)
    response = client.post(
        "/api/assets",
        files={"file": ("f.png", make_png_bytes(), "image/png")},
        data={"kind": "upload", "replaces_asset_id": base_id},
    )
    assert response.status_code == 422, response.text


def test_replaces_asset_id_rejected_with_source_asset_id(client: TestClient) -> None:
    base_id = _upload(client)
    sketch_id = _create_sketch(client)

    response = client.post(
        "/api/assets",
        files={"file": ("sketch.png", make_png_bytes(), "image/png")},
        data={"kind": "sketch", "source_asset_id": base_id, "replaces_asset_id": sketch_id},
    )
    assert response.status_code == 422, response.text


def test_replaces_asset_id_rejected_when_missing(client: TestClient) -> None:
    missing_id = "00000000-0000-0000-0000-000000000000"
    response = _replace_sketch(client, missing_id)
    assert response.status_code == 422, response.text


def test_replaces_asset_id_rejected_when_deleted(client: TestClient) -> None:
    sketch_id = _create_sketch(client)
    assert client.delete(f"/api/assets/{sketch_id}").status_code == 204

    response = _replace_sketch(client, sketch_id)
    assert response.status_code == 422, response.text


def test_replaces_asset_id_rejected_when_target_not_sketch(client: TestClient) -> None:
    base_id = _upload(client)
    response = _replace_sketch(client, base_id)
    assert response.status_code == 422, response.text


def test_used_as_input_false_by_default_then_true_after_run(client: TestClient) -> None:
    sketch_id = _create_sketch(client, width=256, height=256)
    assert client.get(f"/api/assets/{sketch_id}").json()["used_as_input"] is False

    _run_edit_with_sketch(client, sketch_id)

    assert client.get(f"/api/assets/{sketch_id}").json()["used_as_input"] is True


def test_used_as_input_false_for_ordinary_upload(client: TestClient) -> None:
    base_id = _upload(client)
    assert client.get(f"/api/assets/{base_id}").json()["used_as_input"] is False
