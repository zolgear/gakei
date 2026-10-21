"""ADR-0053 第1段: 領域の指示の四角(`RunCreateRequest.regions`、`run.regions`、Asset の種類
`regions`、「画像と文」の渡し方)。Fake のプロバイダーだけを使う(実 API は呼ばない)。"""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from app.domain import regions as regions_domain
from app.domain.models import Asset, Run, RunInput
from app.domain.schemas import RegionBox
from app.i18n import use_locale
from app.mods import events as mod_events
from tests.conftest import extra_database_url, make_png_bytes, wait_for_run_terminal

_MODEL = "gpt-image-2.5-flare"


def _upload(client: TestClient, data: bytes | None = None) -> str:
    response = client.post(
        "/api/assets",
        files={"file": ("x.png", data or make_png_bytes(200, 100), "image/png")},
        data={"kind": "upload"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _body(inputs: list[str], regions: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {
        "operation": "edit",
        "model": _MODEL,
        "prompt": "make it nice",
        "params": {"n": 1, "quality": "low"},
        "inputs": [
            {"asset_id": asset_id, "role": "image", "position": i}
            for i, asset_id in enumerate(inputs)
        ],
        "regions": regions,
        **extra,
    }


def _box(number: int = 1, **extra: Any) -> dict[str, Any]:
    return {"number": number, "box": [0.1, 0.2, 0.5, 0.6], "instruction": "a red hat", **extra}


def _create(client: TestClient, body: dict[str, Any]) -> dict:
    response = client.post("/api/runs", json=body)
    assert response.status_code == 202, response.text
    return wait_for_run_terminal(client, response.json()["id"])


# -- 検証 ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "region",
    [
        {"number": 1, "box": [0.5, 0.2, 0.4, 0.6], "instruction": "x"},  # x0 >= x1
        {"number": 1, "box": [0.1, 0.6, 0.5, 0.6], "instruction": "x"},  # y0 == y1
        {"number": 1, "box": [-0.1, 0.2, 0.5, 0.6], "instruction": "x"},  # 範囲外
        {"number": 1, "box": [0.1, 0.2, 1.2, 0.6], "instruction": "x"},
        {"number": 1, "box": [0.1, 0.2, 0.5], "instruction": "x"},  # 要素の数
        {"number": 0, "box": [0.1, 0.2, 0.5, 0.6], "instruction": "x"},
        {"number": 1, "kind": "move", "box": [0.1, 0.2, 0.5, 0.6]},  # source_box が無い
        {
            "number": 1,
            "kind": "change",
            "box": [0.1, 0.2, 0.5, 0.6],
            "source_box": [0.0, 0.0, 0.1, 0.1],
            "instruction": "x",
        },
        {"number": 1, "kind": "zoom", "box": [0.1, 0.2, 0.5, 0.6]},
        {"number": 1, "box": [0.1, 0.2, 0.5, 0.6], "instruction": "x" * 2001},
        {"number": 1, "box": [0.1, 0.2, 0.5, 0.6], "text": "x" * 201},
        {"number": 1, "box": [0.1, 0.2, 0.5, 0.6], "unknown": 1},
        {"number": 1, "box": [0.1, 0.2, 0.5, 0.6], "label": "x" * 101},
        {"number": 1, "box": [0.1, 0.2, 0.5, 0.6], "label": "a\nb"},
        {"number": 1, "box": [0.1, 0.2, 0.5, 0.6], "label": "a\x00b"},
    ],
)
def test_invalid_region_shape_is_422(client: TestClient, region: dict) -> None:
    asset_id = _upload(client)
    response = client.post("/api/runs", json=_body([asset_id], [region]))
    assert response.status_code == 422, response.text


def test_too_many_and_duplicate_numbers_are_422(client: TestClient) -> None:
    asset_id = _upload(client)
    many = [_box(i + 1) for i in range(17)]
    assert client.post("/api/runs", json=_body([asset_id], many)).status_code == 422
    dup = [_box(1), _box(1)]
    assert client.post("/api/runs", json=_body([asset_id], dup)).status_code == 422


def test_generate_with_regions_is_rejected(client: TestClient) -> None:
    body = {
        "operation": "generate",
        "model": _MODEL,
        "prompt": "x",
        "params": {"n": 1},
        "regions": [_box()],
    }
    response = client.post("/api/runs", json=body)
    assert response.status_code == 422
    assert "Edit" in response.json()["detail"]


def test_regions_need_a_primary_input(client: TestClient) -> None:
    asset_id = _upload(client)
    body = _body([], [_box()])
    body["inputs"] = [{"asset_id": asset_id, "role": "image", "position": 1}]
    response = client.post("/api/runs", json=body)
    assert response.status_code == 422


def test_boxes_without_any_instruction_are_allowed(client: TestClient) -> None:
    """四角の定義(位置と名前)と指示は分ける。指示の無い四角は目印として送る(2026-10-21)。"""
    asset_id = _upload(client)
    detail = _create(
        client,
        _body(
            [asset_id],
            [
                {"number": 1, "box": [0.1, 0.1, 0.2, 0.2], "label": "  白いカフス  "},
                {"number": 2, "kind": "add", "box": [0.3, 0.3, 0.4, 0.4]},
                {"number": 3, "kind": "keep", "box": [0.5, 0.5, 0.6, 0.6]},
                {"number": 4, "kind": "remove", "box": [0.7, 0.7, 0.8, 0.8], "label": ""},
            ],
        ),
    )
    assert detail["status"] == "succeeded"
    saved = detail["regions"]["regions"]
    # 名前は前後の空白を除いて保存し、空は無しと同じ。再実行はここから戻す。
    assert saved[0]["label"] == "白いカフス"
    assert saved[1]["label"] is None
    assert saved[3]["label"] is None
    text = detail["regions"]["appended_text"]
    assert "- 四角 1(赤、白いカフス): 横 10〜20%、縦 10〜20% の範囲。" in text
    assert "- 四角 2(青): 横 30〜40%、縦 30〜40% の範囲。" in text
    assert "変える" not in text and "新しく足す" not in text
    assert "そのまま残す" in text
    assert "消し" in text


def test_run_without_regions_stores_sql_null(client: TestClient) -> None:
    """四角の無い Run の `run.regions` は SQL の NULL(JSON の `null` ではない)。"""
    from sqlalchemy import text

    asset_id = _upload(client)
    detail = _create(client, _body([asset_id], []))
    assert detail["regions"] is None
    with client.app.state.session_factory() as db:
        run_id = uuid.UUID(detail["id"])
        found = db.execute(
            select(Run.id).where(Run.id == run_id, Run.regions.is_(None))
        ).scalar_one_or_none()
        assert found == run_id
        # 前に JSON の `null` で書いた行も None で読める。
        updated = db.execute(
            text("UPDATE run SET regions = 'null' WHERE id = :id"),
            {"id": run_id if db.bind.dialect.name == "postgresql" else run_id.hex},
        )
        assert updated.rowcount == 1
        db.commit()
        db.expire_all()
        assert db.get(Run, run_id).regions is None


def test_unknown_reference_asset_is_rejected(client: TestClient) -> None:
    asset_id = _upload(client)
    response = client.post(
        "/api/runs",
        json=_body([asset_id], [_box(reference_asset_id=str(uuid.uuid4()))]),
    )
    assert response.status_code == 422
    with client.app.state.session_factory() as db:
        assert db.execute(select(Run)).first() is None


def test_input_limit_counts_the_overlay_and_references(client: TestClient) -> None:
    inputs = [_upload(client, make_png_bytes(64, 64, (i, 10, 10))) for i in range(16)]
    response = client.post("/api/runs", json=_body(inputs, [_box()]))
    assert response.status_code == 422
    assert "17" in response.json()["detail"] and "16" in response.json()["detail"]

    # 15 枚 + 四角の画像 1 枚は通る。参照画像を 1 枚足すと超える。
    extra = _upload(client, make_png_bytes(64, 64, (1, 200, 1)))
    response = client.post("/api/runs", json=_body(inputs[:15], [_box(reference_asset_id=extra)]))
    assert response.status_code == 422
    with client.app.state.session_factory() as db:
        assert db.execute(select(Run)).first() is None
        assert db.execute(select(Asset).where(Asset.kind == "regions")).first() is None
    detail = _create(client, _body(inputs[:15], [_box()]))
    assert detail["status"] == "succeeded"


# -- 「画像と文」 ----------------------------------------------------------------------


def test_overlay_asset_inputs_prompt_and_record(client: TestClient) -> None:
    primary = _upload(client)
    second = _upload(client, make_png_bytes(80, 80, (10, 200, 10)))
    reference = _upload(client, make_png_bytes(90, 90, (10, 10, 200)))
    regions = [
        _box(2, reference_asset_id=reference, text="OPEN"),
        {
            "number": 1,
            "kind": "move",
            "box": [0.6, 0.6, 0.9, 0.9],
            "source_box": [0.0, 0.0, 0.2, 0.2],
            "instruction": "",
        },
        _box(3, reference_asset_id=second),
    ]
    detail = _create(client, _body([primary, second], regions))
    assert detail["status"] == "succeeded"

    images = sorted(
        (i for i in detail["inputs"] if i["role"] == "image"), key=lambda i: i["position"]
    )
    # 利用者の入力(主たる入力が 0 番)の後ろに、四角を描いた画像、参照画像の順。
    assert [i["position"] for i in images] == [0, 1, 2, 3]
    assert images[0]["asset_id"] == primary
    assert images[1]["asset_id"] == second
    overlay_id = images[2]["asset_id"]
    assert images[3]["asset_id"] == reference
    assert not [i for i in detail["inputs"] if i["role"] == "reference"]

    overlay = client.get(f"/api/assets/{overlay_id}").json()
    assert overlay["kind"] == "regions"
    assert overlay["source_asset_id"] == primary
    assert (overlay["width"], overlay["height"]) == (200, 100)
    original = client.get(f"/api/assets/{overlay_id}/content", params={"variant": "original"})
    assert original.status_code == 200
    assert Image.open(io.BytesIO(original.content)).format == "PNG"

    record = detail["regions"]
    assert record["version"] == regions_domain.VERSION
    assert record["delivery"] == "image_and_text"
    assert record["overlay_asset_id"] == overlay_id
    # 番号の順に並べて保存する。
    assert [r["number"] for r in record["regions"]] == [1, 2, 3]
    assert record["regions"][0]["source_box"] == [0.0, 0.0, 0.2, 0.2]
    assert record["regions"][1]["reference_asset_id"] == reference

    prompt = detail["prompt"]
    assert prompt.startswith("make it nice\n\n")
    assert prompt.endswith(record["appended_text"])
    assert prompt.removesuffix(record["appended_text"]) == "make it nice"
    text = record["appended_text"]
    assert "3枚目の画像" in text  # 四角を描いた画像
    assert "「OPEN」" in text
    assert "4枚目の画像を参考" in text  # 足した参照画像
    assert "2枚目の画像を参考" in text  # 利用者の入力にある参照画像
    assert "点線の四角" in text
    assert "描かないでください" in text

    # 一覧(再実行で使う)にも出る。
    listed = client.get("/api/runs").json()["items"]
    assert next(r for r in listed if r["id"] == detail["id"])["regions"] == record

    # 主たる親は利用者の1枚目のまま。系列では四角の画像が主たる入力の下にぶら下がる。
    assert detail["primary_parent_asset_id"] == primary
    lineage = client.get(f"/api/assets/{overlay_id}/lineage").json()
    assert any(
        e["source"] == primary and e["target"] == overlay_id and e["kind"] == "sketch_source"
        for e in lineage["edges"]
    )


def test_prompt_text_follows_the_request_locale(client: TestClient) -> None:
    asset_id = _upload(client)
    response = client.post(
        "/api/runs",
        json=_body([asset_id], [_box(kind="keep")]),
        headers={"Accept-Language": "en"},
    )
    assert response.status_code == 202
    detail = wait_for_run_terminal(client, response.json()["id"])
    assert "Region instructions: image 2" in detail["prompt"]
    assert "Box 1 (red)" in detail["prompt"]


def test_repeat_shares_one_overlay(client: TestClient) -> None:
    asset_id = _upload(client)
    response = client.post("/api/runs", json=_body([asset_id], [_box()], repeat=2))
    assert response.status_code == 202, response.text
    run_ids = [r["id"] for r in response.json()["runs"]]
    details = [wait_for_run_terminal(client, run_id) for run_id in run_ids]
    overlays = {d["regions"]["overlay_asset_id"] for d in details}
    assert len(overlays) == 1
    with client.app.state.session_factory() as db:
        assert len(db.execute(select(Asset).where(Asset.kind == "regions")).all()) == 1


def test_prompt_length_includes_appended_text(client: TestClient) -> None:
    asset_id = _upload(client)
    body = _body([asset_id], [_box()])
    body["prompt"] = "x" * 32_000
    response = client.post("/api/runs", json=body)
    assert response.status_code == 422
    with client.app.state.session_factory() as db:
        assert db.execute(select(Asset).where(Asset.kind == "regions")).first() is None


def test_overlay_is_deterministic_and_draws_boxes() -> None:
    image = Image.new("RGB", (400, 300), (128, 128, 128))
    regions = [
        RegionBox(number=1, box=[0.1, 0.1, 0.4, 0.4], instruction="a"),
        RegionBox(number=2, kind="move", box=[0.6, 0.6, 0.9, 0.9], source_box=[0.5, 0.1, 0.7, 0.3]),
    ]
    first = regions_domain.render_overlay(image, regions)
    assert first == regions_domain.render_overlay(image, list(reversed(regions)))
    drawn = Image.open(io.BytesIO(first)).convert("RGB")
    assert drawn.size == (400, 300)
    # 1 番の四角の右の辺は 1 色目(赤)。
    x1 = round(0.4 * 399)
    assert drawn.getpixel((x1, 75)) == regions_domain.PALETTE[0][1]
    # 四角の内側の中ほどは元のまま。
    assert drawn.getpixel((100, 90)) == (128, 128, 128)


def test_prompt_text_wording() -> None:
    regions = [
        RegionBox(number=1, kind="remove", box=[0.0, 0.0, 0.5, 0.5]),
        RegionBox(number=2, kind="add", box=[0.5, 0.5, 1.0, 1.0], instruction="a cat"),
    ]
    with use_locale("ja"):
        text = regions_domain.build_prompt_text(
            regions, overlay_image_number=2, reference_image_numbers={}
        )
    assert text.startswith("\n\n領域の指示: 2枚目の画像")
    assert "- 四角 1(赤):" in text
    assert "- 四角 2(青):" in text
    assert "指示: a cat" in text
    assert "横 0〜50%" in text
    assert "番号(「四角 1」など)か名前" in text


def test_prompt_text_with_labels() -> None:
    regions = [
        RegionBox(number=1, box=[0.1, 0.2, 0.3, 0.4], label="右手の白いカフス"),
        RegionBox(number=2, box=[0.5, 0.5, 0.9, 0.9], label="帽子", instruction="赤くする"),
        RegionBox(number=3, kind="keep", box=[0.0, 0.0, 0.1, 0.1], label="顔"),
    ]
    with use_locale("ja"):
        text = regions_domain.build_prompt_text(
            regions, overlay_image_number=2, reference_image_numbers={}
        )
    assert "- 四角 1(赤、右手の白いカフス): 横 10〜30%、縦 20〜40% の範囲。" in text
    assert (
        "- 四角 2(青、帽子): 四角の中(横 50〜90%、縦 50〜90% の範囲)を変える。 指示: 赤くする"
        in text
    )
    assert "- 四角 3(緑、顔): 四角の中(横 0〜10%、縦 0〜10% の範囲)は変えずに" in text
    with use_locale("en"):
        text = regions_domain.build_prompt_text(
            regions, overlay_image_number=2, reference_image_numbers={}
        )
    assert "- Box 1 (red, 右手の白いカフス): The area at x 10–30%, y 20–40% of the image." in text
    assert "by its name" in text


def test_label_is_trimmed_and_optional() -> None:
    assert RegionBox(number=1, box=[0, 0, 1, 1], label="  a  ").label == "a"
    assert RegionBox(number=1, box=[0, 0, 1, 1], label="   ").label is None
    assert RegionBox(number=1, box=[0, 0, 1, 1]).label is None


# -- 対応しないプロバイダー --------------------------------------------------------------


def test_capabilities_flag(client: TestClient) -> None:
    providers = client.get("/api/capabilities").json()["providers"]
    fake = next(p for p in providers if p["provider"] in ("fake", "openai"))
    for model in fake["models"]:
        ops = {o["operation"]: o for o in model["operations"]}
        assert ops["edit"]["supports_regions"] is True
        assert ops["edit"]["regions_delivery"] == "image_and_text"
        assert ops["generate"]["supports_regions"] is False
        assert ops["generate"]["regions_delivery"] is None


def test_provider_without_regions_rejects(client: TestClient) -> None:
    from app.domain.run_create import RunCreateError, _regions_op_caps
    from app.domain.schemas import RunCreateRequest
    from app.providers import openai_spec

    caps = openai_spec.build_capabilities("fake")
    for model in caps.models:
        for op in model.operations:
            op.supports_regions = False
    body = RunCreateRequest.model_validate(_body([str(uuid.uuid4())], [_box()]))
    with pytest.raises(RunCreateError) as e:
        _regions_op_caps(caps, body)
    assert e.value.kind == "invalid"


# -- Mod --------------------------------------------------------------------------------


def test_mod_subject_has_region_texts(client: TestClient) -> None:
    asset_id = _upload(client)
    detail = _create(
        client,
        _body(
            [asset_id],
            [_box(1, text="SALE"), {"number": 2, "kind": "keep", "box": [0.6, 0.6, 0.7, 0.7]}],
        ),
    )
    with client.app.state.session_factory() as db:
        run = db.get(Run, uuid.UUID(detail["id"]))
        subject = mod_events.image_run_subject(db, client.app.state.settings, run)
    assert subject.region_count == 2
    roles = [(p.role, p.text) for p in subject.text]
    assert ("region", "a red hat") in roles
    assert ("region_text", "SALE") in roles
    # 四角を描いた画像も送る画像に入る(参照として)。
    assert [i.role for i in subject.images] == ["primary", "reference"]


# -- 系列の ZIP ---------------------------------------------------------------------------


def _second_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str) -> TestClient:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / name))
    monkeypatch.setenv("FAKE_PROVIDER", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    database_url = extra_database_url()
    if database_url is not None:
        monkeypatch.setenv("DATABASE_URL", database_url)
    else:
        monkeypatch.delenv("DATABASE_URL", raising=False)

    from app.main import create_app

    return TestClient(create_app())


def test_lineage_zip_round_trip_keeps_regions(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    primary = _upload(client)
    reference = _upload(client, make_png_bytes(90, 90, (10, 10, 200)))
    detail = _create(client, _body([primary], [_box(reference_asset_id=reference)]))
    output = detail["outputs"][0]["asset_id"]

    response = client.get(f"/api/assets/{output}/export", params={"scope": "ancestors"})
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        manifest = json.loads(zf.read("manifest.json"))
    mrun = next(r for r in manifest["runs"] if r["id"] == detail["id"])
    assert mrun["regions"] == detail["regions"]
    kinds = {a["kind"] for a in manifest["assets"]}
    assert "regions" in kinds

    with _second_client(monkeypatch, tmp_path, "other") as other:
        imported = other.post(
            "/api/imports/lineage",
            files={"file": ("lineage.zip", response.content, "application/zip")},
        )
        assert imported.status_code == 201, imported.text
        root = imported.json()["root_asset_id"]
        root_detail = other.get(f"/api/assets/{root}").json()
        run_id = root_detail["produced_by_run"]["id"]
        run = other.get(f"/api/runs/{run_id}").json()
        regions = run["regions"]
        assert regions is not None
        assert regions["appended_text"] == detail["regions"]["appended_text"]
        # Asset の id は取り込み先のものに付け替わる。
        with other.app.state.session_factory() as db:
            overlay = db.get(Asset, uuid.UUID(regions["overlay_asset_id"]))
            assert overlay is not None and overlay.kind == "regions"
            ref = uuid.UUID(regions["regions"][0]["reference_asset_id"])
            inputs = db.execute(
                select(RunInput.asset_id).where(RunInput.run_id == uuid.UUID(run_id))
            ).scalars()
            assert ref in set(inputs)


def test_import_without_regions_field_still_works(client: TestClient) -> None:
    """古い ZIP(`regions` が無い)は今までどおり読める(ManifestRun の既定)。"""
    from app.domain.lineage_import import ManifestRun

    run = ManifestRun.model_validate(
        {
            "id": str(uuid.uuid4()),
            "provider": "fake",
            "model": _MODEL,
            "operation": "edit",
            "prompt": "x",
            "status": "succeeded",
        }
    )
    assert run.regions is None
