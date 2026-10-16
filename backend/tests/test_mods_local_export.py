"""ADR-0051 7章: 同梱の Mod「ローカルに保存」。ファイルとパスを扱うので Windows の CI でも回す。"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path, PurePath
from typing import Any

import pytest
from sqlalchemy import select, update

from app.config import Settings
from app.domain.assets import ingest
from app.domain.embedded_meta import read_gakei_meta
from app.domain.models import (
    Asset,
    AssetKind,
    ModDelivery,
    Run,
    RunOperation,
    RunStatus,
)
from app.mods import api as contract
from app.mods import config as mods_config
from app.mods import loader
from app.mods.runtime import ModRuntime
from app.worker.mod_delivery import ModDeliveryWorker
from tests.conftest import make_png_bytes

pytestmark = pytest.mark.windows

MOD_ID = "local-export"


def _module() -> Any:
    entry = next(m for m in loader.scan(loader.BUILTIN_DIR, builtin=True) if m.id == MOD_ID)
    return loader.import_mod(entry).module


# -- 同梱の Mod の一覧 ---------------------------------------------------------------


def test_builtin_mod_is_listed_and_disabled(tmp_path: Path, client) -> None:  # noqa: ANN001
    body = client.get("/api/settings/mods").json()
    mod = {m["id"]: m for m in body["python_mods"]}[MOD_ID]
    assert mod["name"] == "ローカルに保存"
    assert (mod["enabled"], mod["loaded"], mod["error"]) == (False, False, None)
    assert mod["available_events"] == ["run.finished", "asset.ingested"]
    settings = {s["key"]: s for s in mod["settings"]}
    assert set(settings) == {
        "folder",
        "filename",
        "embed_lineage",
        "write_json",
        "ingested_sources",
    }
    assert settings["filename"]["value"] == "{date}/{time}_{run_id8}_{index}.{ext}"
    assert settings["embed_lineage"]["value"] is True
    assert settings["folder"]["type"] == "path"
    english = client.get("/api/settings/mods", headers={"Accept-Language": "en"}).json()
    names = {m["id"]: m["name"] for m in english["python_mods"]}
    assert names[MOD_ID] == "Save to a local folder"


def test_builtin_wins_over_mods_dir_with_same_id(tmp_path: Path) -> None:
    mods_dir = tmp_path / "mods"
    manifest = (
        'id = "local-export"\nname = "Mine"\nversion = "1"\napi_version = 1\n'
        'module = "main"\nevents = ["run.finished"]\n'
    )
    for folder in ("local-export", "copy"):
        (mods_dir / folder).mkdir(parents=True)
        (mods_dir / folder / loader.MANIFEST_NAME).write_text(manifest, encoding="utf-8")
    found = {m.id: m for m in loader.scan_all(mods_dir)}
    assert found[MOD_ID].builtin is True
    assert found[MOD_ID].manifest is not None and found[MOD_ID].manifest.name == "ローカルに保存"
    assert found["copy"].manifest is None and "duplicate id" in (found["copy"].error or "")


# -- ファイル名 --------------------------------------------------------------------


def _event(occurred: str = "2026-10-16T03:04:05+00:00") -> contract.Event:
    return contract.Event(
        event=contract.RUN_FINISHED,
        event_id=str(uuid.uuid4()),
        occurred_at=occurred,
        instance=contract.Instance(version="0"),
        actor=None,
        subject=contract.RunFinishedSubject(
            run_id="12345678-aaaa-bbbb-cccc-000000000000",
            status="succeeded",
            provider="fake",
            model="../evil/model",
            operation="generate",
            prompt="p",
        ),
    )


def _ref(mime: str = "image/png") -> contract.ImageRef:
    return contract.ImageRef(
        asset_id="abcdef01-0000-0000-0000-000000000000",
        sha256="f" * 64,
        width=1,
        height=1,
        bytes=1,
        mime=mime,
    )


def test_template_placeholders() -> None:
    mod = _module()
    event = _event()
    values = mod.placeholder_values(event, _ref(), 2, event.subject, "generated")
    local = datetime(2026, 10, 16, 3, 4, 5, tzinfo=UTC).astimezone()
    assert values["date"] == local.strftime("%Y-%m-%d")
    assert values["time"] == local.strftime("%H%M%S")
    assert (values["run_id8"], values["index"], values["ext"]) == ("12345678", "2", "png")
    assert values["asset_id8"] == "abcdef01" and values["sha8"] == "ffffffff"
    # 値から区切りや `..` を作らせない。
    assert "/" not in values["model"] and not values["model"].startswith(".")
    path = mod.render_path("{date}/{time}_{run_id8}_{index}.{ext}", values)
    assert path == PurePath(values["date"], f"{values['time']}_12345678_2.png")
    assert mod.render_path("{provider}/{model}.{ext}", values).parts[0] == "fake"
    assert len(mod.render_path("{provider}/{model}.{ext}", values).parts) == 2
    jpeg = mod.placeholder_values(event, _ref("image/jpeg"), 0, None, "upload")
    assert (jpeg["ext"], jpeg["run_id8"], jpeg["source"]) == ("jpg", "", "upload")


@pytest.mark.parametrize(
    "template",
    [
        "../{index}.{ext}",
        "a/../../{index}.{ext}",
        "/abs/{index}.{ext}",
        "\\abs\\{index}.{ext}",
        "C:/x/{index}.{ext}",
        "{unknown}.png",
        "{index.real}.png",
        "{index:>3}.png",
        "a//{index}.png",
        "{date}:{time}.png",
        "CON.png",
        "{index",
    ],
)
def test_template_cannot_escape_or_misuse(template: str) -> None:
    mod = _module()
    event = _event()
    values = mod.placeholder_values(event, _ref(), 0, event.subject, "generated")
    with pytest.raises(mod.ExportError):
        mod.render_path(template, values)


def test_resolve_inside_rejects_links_out(tmp_path: Path) -> None:
    mod = _module()
    root = tmp_path / "out"
    root.mkdir()
    assert mod.resolve_inside(root, PurePath("a", "b.png")) == (root / "a" / "b.png").resolve()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (root / "link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("シンボリックリンクを作れない環境")
    with pytest.raises(mod.ExportError):
        mod.resolve_inside(root, PurePath("link", "x.png"))


def test_same_name_is_numbered_and_same_content_is_not_duplicated(tmp_path: Path) -> None:
    mod = _module()
    target = tmp_path / "out" / "a.png"
    assert mod.write_unique(target, b"one") == (target, True)
    # 同じ中身は書かない(送り直し)。
    assert mod.write_unique(target, b"one") == (target, False)
    # 中身が違えば番号を付け、上書きしない。
    assert mod.write_unique(target, b"two") == (tmp_path / "out" / "a_1.png", True)
    assert mod.write_unique(target, b"three") == (tmp_path / "out" / "a_2.png", True)
    assert target.read_bytes() == b"one"
    assert mod.write_unique(target, b"two") == (tmp_path / "out" / "a_1.png", False)
    # 一時ファイルを残さない。
    assert sorted(p.name for p in target.parent.iterdir()) == ["a.png", "a_1.png", "a_2.png"]


# -- 配達から書き出す ------------------------------------------------------------------


def _setup(
    tmp_path: Path,
    db_session_factory,  # noqa: ANN001
    local_store,  # noqa: ANN001
    **values: object,
) -> tuple[ModRuntime, ModDeliveryWorker]:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        fake_provider=True,
        mods_dir=tmp_path / "mods",
    )
    runtime = ModRuntime(db_session_factory, local_store, settings)
    runtime.scan()
    with db_session_factory() as db:
        mods_config.save_python_config(
            db,
            mods_config.PythonModConfig(
                mod_id=MOD_ID,
                common=mods_config.CommonConfig(enabled=True),
                values={"folder": str(tmp_path / "out"), **values},
            ),
        )
        runtime.reload(db)
    status = next(s for s in runtime.python_mods(db_session_factory()) if s.folder.id == MOD_ID)
    assert (status.loaded, status.error) == (True, None)
    return runtime, ModDeliveryWorker(db_session_factory, runtime)


def _generated_run(db_session_factory, local_store, count: int = 1) -> uuid.UUID:  # noqa: ANN001
    with db_session_factory() as db:
        run = Run(
            provider="fake",
            model="m",
            operation=RunOperation.GENERATE,
            prompt="p",
            params={"n": count},
            status=RunStatus.SUCCEEDED,
        )
        db.add(run)
        db.flush()
        for index in range(count):
            ingest(
                db,
                local_store,
                make_png_bytes(color=(10 * index, 20, 30)),
                AssetKind.GENERATED,
                produced_by_run_id=run.id,
                output_index=index,
                provider="fake",
                model="m",
            )
        db.commit()
        return run.id


def _finish(runtime: ModRuntime, worker: ModDeliveryWorker, run_id: uuid.UUID) -> None:
    with runtime.session_factory() as db:
        assert runtime.enqueue_run_finished(db, db.get(Run, run_id))
        db.commit()
    asyncio.run(worker.run_once())


def _files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file())


def _delivery(factory) -> ModDelivery:  # noqa: ANN001
    with factory() as db:
        row = db.execute(select(ModDelivery)).scalars().one()
        db.expunge(row)
        return row


def test_run_finished_saves_outputs_with_lineage_and_json(
    tmp_path: Path,
    db_session_factory,
    local_store,  # noqa: ANN001
) -> None:
    runtime, worker = _setup(tmp_path, db_session_factory, local_store, write_json=True)
    run_id = _generated_run(db_session_factory, local_store, count=2)
    _finish(runtime, worker, run_id)
    assert _delivery(db_session_factory).status == "done"
    files = _files(tmp_path / "out")
    images = [p for p in files if p.suffix == ".png"]
    sidecars = [p for p in files if p.suffix == ".json"]
    assert len(images) == 2 and len(sidecars) == 2
    assert [p.stem.split("_")[1:] for p in images] == [
        [str(run_id)[:8], "0"],
        [str(run_id)[:8], "1"],
    ]
    # フォルダは日付。
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", images[0].parent.name)
    # ダウンロードと同じ、系列の情報を埋め込んだ PNG。
    meta = read_gakei_meta(images[0].read_bytes())
    assert meta is not None
    with db_session_factory() as db:
        outputs = (
            db.execute(select(Asset).where(Asset.produced_by_run_id == run_id)).scalars().all()
        )
        original = local_store.read(outputs[0].blob_key)
    assert images[0].read_bytes() != original
    sidecar = json.loads(sidecars[0].read_text(encoding="utf-8"))
    assert sidecar["event"]["event"] == "run.finished"
    assert sidecar["run"]["run_id"] == str(run_id)
    assert sidecar["image"]["index"] == 0

    # 同じ配達を送り直しても(途中で失敗した後など)、同じファイルを重ねて作らない。
    with db_session_factory() as db:
        db.execute(update(ModDelivery).values(status="queued"))
        db.commit()
    asyncio.run(worker.run_once())
    assert _files(tmp_path / "out") == files


def test_without_lineage_writes_the_original(
    tmp_path: Path,
    db_session_factory,
    local_store,  # noqa: ANN001
) -> None:
    runtime, worker = _setup(
        tmp_path,
        db_session_factory,
        local_store,
        embed_lineage=False,
        filename="{run_id}/{index}.{ext}",
    )
    run_id = _generated_run(db_session_factory, local_store)
    _finish(runtime, worker, run_id)
    files = _files(tmp_path / "out")
    assert [p.relative_to(tmp_path / "out").as_posix() for p in files] == [f"{run_id}/0.png"]
    with db_session_factory() as db:
        asset = db.execute(select(Asset).where(Asset.produced_by_run_id == run_id)).scalar_one()
        assert files[0].read_bytes() == local_store.read(asset.blob_key)
    assert read_gakei_meta(files[0].read_bytes()) is None


def test_withheld_output_is_not_saved(
    tmp_path: Path,
    db_session_factory,
    local_store,  # noqa: ANN001
) -> None:
    runtime, worker = _setup(tmp_path, db_session_factory, local_store)
    run_id = _generated_run(db_session_factory, local_store)
    with db_session_factory() as db:
        asset = db.execute(select(Asset).where(Asset.produced_by_run_id == run_id)).scalar_one()
        asset.content_withheld_at = datetime.now(UTC)
        db.commit()
    _finish(runtime, worker, run_id)
    assert _delivery(db_session_factory).status == "done"
    assert _files(tmp_path / "out") == []


def test_failure_is_a_delivery_failure(
    tmp_path: Path,
    db_session_factory,
    local_store,  # noqa: ANN001
) -> None:
    runtime, worker = _setup(tmp_path, db_session_factory, local_store, filename="../{index}.{ext}")
    run_id = _generated_run(db_session_factory, local_store)
    _finish(runtime, worker, run_id)
    row = _delivery(db_session_factory)
    # 例外は配達の失敗(送り直しを待つ)。
    assert (row.status, row.attempts) == ("queued", 1)
    assert "ExportError" in (row.last_error or "")
    assert not (tmp_path / "out").exists() or _files(tmp_path / "out") == []
    assert list(tmp_path.glob("*.png")) == []


def test_asset_ingested_saves_selected_sources(
    tmp_path: Path,
    db_session_factory,
    local_store,  # noqa: ANN001
) -> None:
    runtime, worker = _setup(
        tmp_path,
        db_session_factory,
        local_store,
        ingested_sources="upload",
        filename="{source}/{asset_id}.{ext}",
    )
    with db_session_factory() as db:
        upload = ingest(db, local_store, make_png_bytes(color=(1, 2, 3)), AssetKind.UPLOAD)
        sketch = ingest(db, local_store, make_png_bytes(color=(4, 5, 6)), AssetKind.UPLOAD)
        assert runtime.enqueue_asset_ingested(db, upload, "upload")
        assert runtime.enqueue_asset_ingested(db, sketch, "sketch")
        db.commit()
        upload_id = upload.id

    async def drain() -> None:
        while await worker.run_once():
            pass

    asyncio.run(drain())
    files = _files(tmp_path / "out")
    assert [p.relative_to(tmp_path / "out").as_posix() for p in files] == [
        f"upload/{upload_id}.png"
    ]
    with db_session_factory() as db:
        statuses = {r.status for r in db.execute(select(ModDelivery)).scalars()}
    assert statuses == {"done"}


def test_unknown_source_setting_fails(
    tmp_path: Path,
    db_session_factory,
    local_store,  # noqa: ANN001
) -> None:
    runtime, worker = _setup(tmp_path, db_session_factory, local_store, ingested_sources="upload,x")
    with db_session_factory() as db:
        upload = ingest(db, local_store, make_png_bytes(), AssetKind.UPLOAD)
        assert runtime.enqueue_asset_ingested(db, upload, "upload")
        db.commit()
    asyncio.run(worker.run_once())
    assert _delivery(db_session_factory).status == "queued"


def test_builtin_mod_uses_only_the_public_module() -> None:
    """サンプルを兼ねるので、GAKEI の内部を import しない(`gakei_mod` と標準ライブラリだけ)。"""
    import ast
    import sys

    folder = loader.BUILTIN_DIR / "local_export"
    for path in folder.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    continue
                names = [node.module or ""]
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                assert top == "gakei_mod" or top in sys.stdlib_module_names, (path, name)
