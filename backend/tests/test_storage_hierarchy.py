"""原本の保存先の階層(ADR-0026)。キー規則、フォルダ名の正規化、同名の連番、同じ内容の
共有、古いキー(ADR-0004)の Asset の読み出しと配信。
"""

from __future__ import annotations

import re
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.domain.assets import ingest
from app.domain.embedded_meta import read_gakei_meta
from app.domain.models import Asset, AssetKind
from app.domain.storage import LocalFsStore, OriginalKeyInfo, normalize_segment
from tests.conftest import make_png_bytes, wait_for_run_terminal

_NAME = r"\d{8}-\d{6}_[0-9a-f]{8}(-\d+)?"
_MONTH = r"\d{4}-\d{2}"


def _info(kind: str = "upload", **kwargs) -> OriginalKeyInfo:
    return OriginalKeyInfo(
        kind=kind,  # type: ignore[arg-type]
        asset_id=kwargs.pop("asset_id", uuid.UUID("1a2b3c4d-0000-4000-8000-000000000000")),
        created_at=kwargs.pop("created_at", datetime(2026, 9, 29, 0, 30, 15, tzinfo=UTC)),
        **kwargs,
    )


# -- 正規化(ADR-0026 2章) --------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("gpt-image-2.5-sunburst", "gpt-image-2.5-sunburst"),
        ("openai", "openai"),
        ("a/b\\c:d*e?f", "a_b_c_d_e_f"),
        ("t2i サンプル", "t2i"),
        ("日本語だけ", "unknown"),
        ("線画 → 着色 v2", "v2"),
        ("my  flow!!name", "my_flow_name"),
        ("__.hidden._", "hidden"),
        ("..", "unknown"),
        ("../../etc", "etc"),
        ("", "unknown"),
        (None, "unknown"),
        ("CON", "_CON"),
        ("nul", "_nul"),
        ("com1", "_com1"),
        ("LPT9.flow", "_LPT9.flow"),
        ("CONSOLE", "CONSOLE"),
    ],
)
def test_normalize_segment(raw: str | None, expected: str) -> None:
    assert normalize_segment(raw) == expected


def test_normalize_segment_truncates_to_64_chars_and_strips_again() -> None:
    assert normalize_segment("a" * 100) == "a" * 64
    # 64 文字目で切った結果の末尾が `.` や `_` なら取り除く
    assert normalize_segment("a" * 63 + "_b") == "a" * 63
    assert len(normalize_segment("x" * 200)) == 64


# -- キー規則(ADR-0026 1章) --------------------------------------------------------


def test_generated_key_uses_provider_model_month_and_local_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """日時と年月はサーバーのローカル時刻(TZ)で決まる。"""
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    time.tzset()
    try:
        store = LocalFsStore(tmp_path)
        info = _info(
            "generated",
            provider="openai",
            model="gpt-image-2.5-sunburst",
            # UTC では 9 月 30 日 15:00:05。JST では 10 月 1 日 0:00:05。
            created_at=datetime(2026, 9, 30, 15, 0, 5, tzinfo=UTC),
        )
        key = store.write_original(b"data", "png", info)
    finally:
        monkeypatch.delenv("TZ")
        time.tzset()
    assert key == "assets/openai/gpt-image-2.5-sunburst/2026-10/20261001-000005_1a2b3c4d.png"
    assert (tmp_path / key).read_bytes() == b"data"


@pytest.mark.parametrize(
    ("kind", "folder"),
    [("upload", "uploads"), ("mask", "masks"), ("sketch", "sketches")],
)
def test_non_generated_kind_folders(tmp_path: Path, kind: str, folder: str) -> None:
    store = LocalFsStore(tmp_path)
    key = store.write_original(b"x", "png", _info(kind, provider="openai", model="ignored"))
    assert re.fullmatch(rf"assets/{folder}/{_MONTH}/{_NAME}\.png", key), key


def test_generated_key_normalizes_folder_names(tmp_path: Path) -> None:
    store = LocalFsStore(tmp_path)
    key = store.write_original(b"x", "webp", _info("generated", provider="comfyui", model=".."))
    assert re.fullmatch(rf"assets/comfyui/unknown/{_MONTH}/{_NAME}\.webp", key), key


def test_same_name_gets_numbered_suffix(tmp_path: Path) -> None:
    store = LocalFsStore(tmp_path)
    info = _info("upload")
    first = store.write_original(b"1", "png", info)
    second = store.write_original(b"2", "png", info)
    third = store.write_original(b"3", "png", info)
    assert second == first.removesuffix(".png") + "-2.png"
    assert third == first.removesuffix(".png") + "-3.png"
    assert (tmp_path / first).read_bytes() == b"1"
    assert (tmp_path / second).read_bytes() == b"2"
    assert (tmp_path / third).read_bytes() == b"3"
    # 一時ファイルは残らない
    assert [p.name for p in (tmp_path / first).parent.iterdir() if p.name.startswith(".")] == []


# -- ingest からの保存と同じ内容の共有(ADR-0026 3章) ------------------------------


def test_ingest_uses_asset_id_and_created_at_for_file_name(
    db_session_factory: sessionmaker, local_store: LocalFsStore
) -> None:
    with db_session_factory() as session:
        asset = ingest(session, local_store, make_png_bytes(), AssetKind.UPLOAD)
        session.commit()
        local = asset.created_at.replace(tzinfo=asset.created_at.tzinfo or UTC).astimezone()
        assert asset.blob_key == (
            f"assets/uploads/{local.strftime('%Y-%m')}/"
            f"{local.strftime('%Y%m%d-%H%M%S')}_{asset.id.hex[:8]}.png"
        )
        assert local_store.exists(asset.blob_key)


def test_ingest_generated_uses_provider_and_model(
    db_session_factory: sessionmaker, local_store: LocalFsStore
) -> None:
    with db_session_factory() as session:
        asset = ingest(
            session,
            local_store,
            make_png_bytes(),
            AssetKind.GENERATED,
            provider="fake",
            model="gpt-image-2.5-sunburst",
        )
        assert re.fullmatch(
            rf"assets/fake/gpt-image-2.5-sunburst/{_MONTH}/{_NAME}\.png", asset.blob_key
        ), asset.blob_key


def test_same_content_shares_first_file_even_across_kinds(
    db_session_factory: sessionmaker, local_store: LocalFsStore
) -> None:
    data = make_png_bytes()
    with db_session_factory() as session:
        first = ingest(session, local_store, data, AssetKind.UPLOAD)
        second = ingest(
            session, local_store, data, AssetKind.GENERATED, provider="openai", model="m"
        )
        session.commit()
        assert first.id != second.id
        assert second.blob_key == first.blob_key
        assert first.blob_key.startswith("assets/uploads/")
        files = [p for p in (local_store.root / "assets").rglob("*") if p.is_file()]
        assert len(files) == 1


def test_same_content_shares_file_of_soft_deleted_asset(
    db_session_factory: sessionmaker, local_store: LocalFsStore
) -> None:
    data = make_png_bytes()
    with db_session_factory() as session:
        first = ingest(session, local_store, data, AssetKind.UPLOAD)
        first.deleted_at = datetime.now(UTC)
        session.commit()
        second = ingest(session, local_store, data, AssetKind.UPLOAD)
        session.commit()
        assert second.blob_key == first.blob_key


def test_same_content_is_rewritten_when_file_is_missing(
    db_session_factory: sessionmaker, local_store: LocalFsStore
) -> None:
    data = make_png_bytes()
    with db_session_factory() as session:
        first = ingest(session, local_store, data, AssetKind.UPLOAD)
        session.commit()
        (local_store.root / first.blob_key).unlink()

        second = ingest(session, local_store, data, AssetKind.UPLOAD)
        session.commit()
        assert second.blob_key != first.blob_key
        assert second.id.hex[:8] in second.blob_key
        assert local_store.read(second.blob_key) == data

        # 以後の同じ内容は、実在する方(2つ目)を共有する
        third = ingest(session, local_store, data, AssetKind.UPLOAD)
        assert third.blob_key == second.blob_key


def test_same_content_shares_legacy_key_file(
    db_session_factory: sessionmaker, local_store: LocalFsStore
) -> None:
    data = make_png_bytes()
    with db_session_factory() as session:
        first = ingest(session, local_store, data, AssetKind.UPLOAD)
        session.commit()
        legacy = _move_to_legacy_key(local_store, first)
        session.commit()

        second = ingest(session, local_store, data, AssetKind.UPLOAD)
        assert second.blob_key == legacy


# -- 古いキー(ADR-0004)の読み出しと配信 -------------------------------------------


def _move_to_legacy_key(store: LocalFsStore, asset: Asset) -> str:
    """テスト用に、Asset の原本を ADR-0004 の古いキーへ移して blob_key を書き換える
    (このバージョンより前に保存された Asset を再現する)。"""
    ext = asset.blob_key.rsplit(".", 1)[1]
    legacy = LocalFsStore.legacy_original_key(asset.sha256, ext)
    target = store.root / legacy
    target.parent.mkdir(parents=True, exist_ok=True)
    (store.root / asset.blob_key).rename(target)
    asset.blob_key = legacy
    return legacy


def _upload(client: TestClient, kind: str = "upload", data: bytes | None = None) -> str:
    response = client.post(
        "/api/assets",
        files={"file": ("x.png", data or make_png_bytes(), "image/png")},
        data={"kind": kind},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _blob_key(client: TestClient, asset_id: str) -> str:
    with client.app.state.session_factory() as session:
        asset = session.get(Asset, uuid.UUID(asset_id))
        assert asset is not None
        return asset.blob_key


def test_api_upload_mask_sketch_keys(client: TestClient) -> None:
    upload_key = _blob_key(client, _upload(client, "upload", make_png_bytes(color=(1, 2, 3))))
    mask_key = _blob_key(client, _upload(client, "mask", make_png_bytes(color=(4, 5, 6))))
    sketch_key = _blob_key(client, _upload(client, "sketch", make_png_bytes(color=(7, 8, 9))))
    assert re.fullmatch(rf"assets/uploads/{_MONTH}/{_NAME}\.png", upload_key), upload_key
    assert re.fullmatch(rf"assets/masks/{_MONTH}/{_NAME}\.png", mask_key), mask_key
    assert re.fullmatch(rf"assets/sketches/{_MONTH}/{_NAME}\.png", sketch_key), sketch_key


def test_api_generated_key_uses_provider_and_model(client: TestClient) -> None:
    response = client.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": "gpt-image-2.5-sunburst",
            "prompt": "ADR-0026",
            "params": {"n": 1, "output_format": "png"},
        },
    )
    assert response.status_code == 202, response.text
    detail = wait_for_run_terminal(client, response.json()["id"])
    assert detail["status"] == "succeeded", detail
    key = _blob_key(client, detail["outputs"][0]["asset_id"])
    assert re.fullmatch(rf"assets/fake/gpt-image-2.5-sunburst/{_MONTH}/{_NAME}\.png", key), key
    assert detail["outputs"][0]["asset_id"].replace("-", "")[:8] in key


def test_legacy_key_asset_is_served_and_downloadable(client: TestClient) -> None:
    data = make_png_bytes(color=(10, 20, 30))
    asset_id = _upload(client, data=data)
    store: LocalFsStore = client.app.state.store
    with client.app.state.session_factory() as session:
        asset = session.get(Asset, uuid.UUID(asset_id))
        assert asset is not None
        legacy = _move_to_legacy_key(store, asset)
        session.commit()
    assert re.fullmatch(r"assets/[0-9a-f]{2}/[0-9a-f]{64}\.png", legacy)

    original = client.get(f"/api/assets/{asset_id}/content", params={"variant": "original"})
    assert original.status_code == 200
    assert original.content == data
    for variant in ("thumb", "preview"):
        derived = client.get(f"/api/assets/{asset_id}/content", params={"variant": variant})
        assert derived.status_code == 200

    downloaded = client.get(
        f"/api/assets/{asset_id}/content", params={"variant": "original", "download": 1}
    )
    assert downloaded.status_code == 200
    meta = read_gakei_meta(downloaded.content)
    assert meta is not None


def test_new_key_asset_download_embeds_meta(client: TestClient) -> None:
    asset_id = _upload(client, data=make_png_bytes(color=(40, 50, 60)))
    downloaded = client.get(
        f"/api/assets/{asset_id}/content", params={"variant": "original", "download": 1}
    )
    assert downloaded.status_code == 200
    meta = read_gakei_meta(downloaded.content)
    assert meta is not None


# -- 生成画像のモデル名(ComfyUI はワークフロー名) ------------------------------------


def test_storage_model_label_for_comfyui_falls_back_to_params_then_id(
    db_session_factory: sessionmaker,
) -> None:
    from app.domain.models import Run, RunOperation
    from app.worker.runner import _storage_model_label

    with db_session_factory() as session:
        openai_run = Run(
            provider="openai", model="gpt-image-2.5", operation=RunOperation.GENERATE, prompt="p"
        )
        workflow_id = str(uuid.uuid4())
        recorded = Run(
            provider="comfyui",
            model=workflow_id,
            operation=RunOperation.GENERATE,
            prompt="p",
            params={"comfyui_workflow": {"id": workflow_id, "name": "記録した名前"}},
        )
        bare = Run(
            provider="comfyui", model="not-a-uuid", operation=RunOperation.GENERATE, prompt="p"
        )
        assert _storage_model_label(session, openai_run) == "gpt-image-2.5"
        # comfy_workflow の行が無ければ、Run の params に記録した名前
        assert _storage_model_label(session, recorded) == "記録した名前"
        # それも無ければ id(model)そのもの
        assert _storage_model_label(session, bare) == "not-a-uuid"
