"""ADR-0037: 系列の持ち出し(エクスポート)と取り込み(インポート)。

- 書き出し: 範囲ごとの manifest、見える範囲(ADR-0025)、論理削除、失敗した Run の扱い、
  原本がバイト単位で同じこと、オブジェクトストレージ(S3。moto)。
- 取り込み: 同じインスタンスの別の利用者への往復(続きの Edit まで)、別のインスタンスへの往復、
  二重取り込み、書き換えた ZIP、上限、途中の失敗で何も残らないこと、MCP の上限に数えないこと、
  `run_import` が追記のみであること。
"""

from __future__ import annotations

import hashlib
import io
import json
import uuid
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select

import app.main as app_main
from app.domain import agent_images
from app.domain import lineage_import as import_domain
from app.domain.mcp_settings import count_recent_mcp_runs
from app.domain.models import (
    RUN_ORIGIN_IMPORT,
    Asset,
    AssetKind,
    Run,
    RunImport,
    RunInput,
    RunInputRole,
    RunOperation,
    RunStatus,
)
from tests.conftest import (
    _fake_settings,
    extra_database_url,
    login_as,
    make_png_bytes,
    wait_for_run_terminal,
)

pytestmark = pytest.mark.windows

_MODEL = "gpt-image-2.5-sunburst"


# -- 準備 ----------------------------------------------------------------------


def _run(
    client: TestClient,
    prompt: str,
    inputs: list[str] | None = None,
    n: int = 1,
    expect: str = "succeeded",
) -> dict:
    body: dict[str, Any] = {
        "operation": "edit" if inputs else "generate",
        "model": _MODEL,
        "prompt": prompt,
        "params": {"n": n, "quality": "low"},
    }
    if inputs:
        body["inputs"] = [
            {"asset_id": asset_id, "role": "image", "position": i}
            for i, asset_id in enumerate(inputs)
        ]
    response = client.post("/api/runs", json=body)
    assert response.status_code == 202, response.text
    detail = wait_for_run_terminal(client, response.json()["id"])
    assert detail["status"] == expect, detail
    return detail


def _out(detail: dict, index: int = 0) -> str:
    return detail["outputs"][index]["asset_id"]


def _upload(client: TestClient, data: bytes, kind: str = "upload", **extra: str) -> str:
    response = client.post(
        "/api/assets",
        files={"file": ("x.png", data, "image/png")},
        data={"kind": kind, **extra},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _export(client: TestClient, asset_id: str, scope: str = "ancestors", **params: Any) -> bytes:
    response = client.get(f"/api/assets/{asset_id}/export", params={"scope": scope, **params})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    return response.content


def _manifest(zip_bytes: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        return json.loads(zf.read("manifest.json"))


def _entries(zip_bytes: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        return {name: zf.read(name) for name in zf.namelist()}


def _rezip(entries: dict[str, bytes], compression: int = zipfile.ZIP_STORED) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compression) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buffer.getvalue()


def _import(client: TestClient, zip_bytes: bytes) -> Any:
    return client.post(
        "/api/imports/lineage",
        files={"file": ("lineage.zip", zip_bytes, "application/zip")},
    )


def _import_ok(client: TestClient, zip_bytes: bytes) -> dict:
    response = _import(client, zip_bytes)
    assert response.status_code == 201, response.text
    return response.json()


def _counts(client: TestClient) -> tuple[int, int, int, int]:
    with client.app.state.session_factory() as db:
        return (
            db.execute(select(func.count()).select_from(Asset)).scalar_one(),
            db.execute(select(func.count()).select_from(Run)).scalar_one(),
            db.execute(select(func.count()).select_from(RunInput)).scalar_one(),
            db.execute(select(func.count()).select_from(RunImport)).scalar_one(),
        )


def _chain(client: TestClient) -> tuple[dict, dict, dict]:
    """generate → edit → edit の3段の系列。"""
    r0 = _run(client, "root")
    r1 = _run(client, "child", [_out(r0)])
    r2 = _run(client, "grandchild", [_out(r1)])
    return r0, r1, r2


def _second_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str) -> TestClient:
    """別の `DATA_DIR`(と、PostgreSQL のときは別の DB)で動く、もう1つの GAKEI。"""
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


# -- 書き出し ----------------------------------------------------------------------


def test_export_manifest_ancestors_and_lineage(client: TestClient) -> None:
    r0, r1, r2 = _chain(client)
    a0, a1, a2 = _out(r0), _out(r1), _out(r2)

    zip_bytes = _export(client, a1, "ancestors")
    manifest = _manifest(zip_bytes)
    assert manifest["format"] == "gakei.lineage-export/1"
    assert manifest["scope"] == "ancestors"
    assert manifest["root_asset_id"] == a1
    assert manifest["gakei_version"]
    assert manifest["exported_at"]
    assert {a["id"] for a in manifest["assets"]} == {a0, a1}
    assert [r["id"] for r in manifest["runs"]] == [r0["id"], r1["id"]]
    run1 = next(r for r in manifest["runs"] if r["id"] == r1["id"])
    assert run1["inputs"] == [{"asset_id": a0, "role": "image", "position": 0}]
    assert run1["omitted_input_count"] == 0
    assert run1["prompt"] == "child"
    assert run1["params"] == r1["params"]
    assert run1["status"] == "succeeded"
    assert run1["operation"] == "edit"
    asset1 = next(a for a in manifest["assets"] if a["id"] == a1)
    assert asset1["kind"] == "generated"
    assert asset1["produced_by_run_id"] == r1["id"]
    assert asset1["output_index"] == 0
    assert asset1["file"].startswith(f"assets/{a1}.")
    names = set(_entries(zip_bytes))
    assert names == {"manifest.json"} | {a["file"] for a in manifest["assets"]}

    lineage = _manifest(_export(client, a1, "lineage"))
    assert lineage["scope"] == "lineage"
    assert {a["id"] for a in lineage["assets"]} == {a0, a1, a2}
    assert {r["id"] for r in lineage["runs"]} == {r0["id"], r1["id"], r2["id"]}


def test_export_headers_and_preview(client: TestClient) -> None:
    r0, r1, _ = _chain(client)
    response = client.get(f"/api/assets/{_out(r1)}/export", params={"scope": "ancestors"})
    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert f"gakei-lineage-{_out(r1)}-" in disposition
    assert disposition.endswith('.zip"')

    preview = client.get(
        f"/api/assets/{_out(r1)}/export/preview", params={"scope": "lineage"}
    ).json()
    assert preview["asset_count"] == 3
    assert preview["run_count"] == 3
    assert preview["total_bytes"] > 0
    assert preview["truncated"] is False

    assert client.get(f"/api/assets/{uuid.uuid4()}/export").status_code == 404
    bad_scope = client.get(f"/api/assets/{_out(r0)}/export", params={"scope": "single"})
    assert bad_scope.status_code == 422


def test_export_originals_are_byte_identical(client: TestClient) -> None:
    r0, r1, _ = _chain(client)
    upload_id = _upload(client, make_png_bytes(color=(1, 2, 3)))
    edit = _run(client, "with upload", [_out(r1), upload_id])
    zip_bytes = _export(client, _out(edit))
    manifest = _manifest(zip_bytes)
    entries = _entries(zip_bytes)
    with client.app.state.session_factory() as db:
        for item in manifest["assets"]:
            asset = db.get(Asset, uuid.UUID(item["id"]))
            assert asset is not None
            data = entries[item["file"]]
            assert hashlib.sha256(data).hexdigest() == asset.sha256 == item["sha256"]
            assert len(data) == asset.bytes == item["bytes"]
            stored = client.get(f"/api/assets/{item['id']}/content", params={"variant": "original"})
            assert stored.content == data
    upload = next(a for a in manifest["assets"] if a["id"] == upload_id)
    assert upload["kind"] == "upload"
    assert upload["produced_by_run_id"] is None
    edit_run = next(r for r in manifest["runs"] if r["id"] == edit["id"])
    assert {(i["asset_id"], i["position"]) for i in edit_run["inputs"]} == {
        (_out(r1), 0),
        (upload_id, 1),
    }


def test_export_excludes_deleted_assets_and_counts_omitted_inputs(client: TestClient) -> None:
    r0, r1, _ = _chain(client)
    assert client.delete(f"/api/assets/{_out(r0)}").status_code == 204
    manifest = _manifest(_export(client, _out(r1)))
    assert {a["id"] for a in manifest["assets"]} == {_out(r1)}
    assert [r["id"] for r in manifest["runs"]] == [r1["id"]]
    assert manifest["runs"][0]["inputs"] == []
    assert manifest["runs"][0]["omitted_input_count"] == 1
    assert _out(r0) not in json.dumps(manifest)


def test_export_includes_failed_run_only_when_it_produced_an_asset(client: TestClient) -> None:
    r0 = _run(client, "root")
    # 範囲の Asset を入力にした失敗の Run(何も生んでいない)は含めない。
    failed = _run(client, "[[fail:contentFilter]] x", [_out(r0)], expect="failed")

    # 出力を持つ失敗の Run(途中まで出力を取り込んだ記録など)は、その出力が範囲にあれば含める。
    store = client.app.state.store
    from app.domain import assets as assets_domain

    with client.app.state.session_factory() as db:
        run = Run(
            provider="fake",
            model=_MODEL,
            operation=RunOperation.EDIT,
            prompt="failed with output",
            params={"n": 2},
            status=RunStatus.FAILED,
            error_code="server_error",
            error_message="boom",
        )
        db.add(run)
        db.flush()
        db.add(RunInput(run_id=run.id, asset_id=uuid.UUID(_out(r0)), role="image", position=0))
        out = assets_domain.ingest(
            db,
            store,
            make_png_bytes(color=(9, 9, 9)),
            AssetKind.GENERATED,
            produced_by_run_id=run.id,
            output_index=0,
        )
        db.commit()
        failed_with_output_id = str(run.id)
        out_id = str(out.id)

    manifest = _manifest(_export(client, _out(r0), "lineage"))
    run_ids = {r["id"] for r in manifest["runs"]}
    assert failed["id"] not in run_ids
    assert failed_with_output_id in run_ids
    assert out_id in {a["id"] for a in manifest["assets"]}
    failed_run = next(r for r in manifest["runs"] if r["id"] == failed_with_output_id)
    assert failed_run["status"] == "failed"
    assert failed_run["error_code"] == "server_error"


def test_export_respects_visibility(client_oidc: TestClient) -> None:
    login_as(client_oidc, "b@example.com", name="B")
    b_upload = _upload(client_oidc, make_png_bytes(color=(4, 5, 6)))

    login_as(client_oidc, "a@example.com", name="Alice")
    r0 = _run(client_oidc, "root")
    r1 = _run(client_oidc, "child", [_out(r0)])
    # 以前のデータのように、他人(B)の Asset を入力にしていた Run を作る(ADR-0025 4章)。
    with client_oidc.app.state.session_factory() as db:
        db.add(
            RunInput(
                run_id=uuid.UUID(r1["id"]),
                asset_id=uuid.UUID(b_upload),
                role=RunInputRole.REFERENCE,
                position=1,
            )
        )
        db.commit()

    manifest = _manifest(_export(client_oidc, _out(r1)))
    assert {a["id"] for a in manifest["assets"]} == {_out(r0), _out(r1)}
    run1 = next(r for r in manifest["runs"] if r["id"] == r1["id"])
    assert run1["omitted_input_count"] == 1
    assert b_upload not in json.dumps(manifest)
    # 実行者の名前は、選んだときだけ入る(既定は入れない。ADR-0037 4章)。
    assert run1["created_by_name"] is None
    assert "Alice" not in json.dumps(manifest)
    named = _manifest(_export(client_oidc, _out(r1), include_creator_names="true"))
    run1_named = next(r for r in named["runs"] if r["id"] == r1["id"])
    assert run1_named["created_by_name"] == "Alice"
    assert "a@example.com" not in json.dumps(named)

    # B は A の画像を書き出せない(存在しないのと同じ 404)。
    login_as(client_oidc, "b@example.com", name="B")
    assert client_oidc.get(f"/api/assets/{_out(r1)}/export").status_code == 404
    assert client_oidc.get(f"/api/assets/{_out(r1)}/export/preview").status_code == 404


# -- 取り込み ----------------------------------------------------------------------


def test_round_trip_to_another_user_on_same_instance(client_oidc: TestClient) -> None:
    login_as(client_oidc, "a@example.com", name="Alice")
    upload_id = _upload(client_oidc, make_png_bytes(color=(10, 20, 30)))
    sketch_id = _upload(
        client_oidc, make_png_bytes(color=(11, 21, 31)), kind="sketch", source_asset_id=upload_id
    )
    r1 = _run(client_oidc, "edit sketch", [sketch_id])
    r2 = _run(client_oidc, "edit again", [_out(r1)])
    zip_bytes = _export(client_oidc, _out(r2), include_creator_names="true")
    source_manifest = _manifest(zip_bytes)
    assert len(source_manifest["assets"]) == 4

    login_as(client_oidc, "b@example.com", name="Bob")
    before = _counts(client_oidc)
    result = _import_ok(client_oidc, zip_bytes)
    assert result["asset_count"] == 4
    assert result["run_count"] == 2
    assert result["created_asset_count"] == 4
    assert result["created_run_count"] == 2
    root = result["root_asset_id"]
    assert root != _out(r2)
    after = _counts(client_oidc)
    assert after[0] - before[0] == 4
    assert after[1] - before[1] == 2
    assert after[3] - before[3] == 2

    # B に見え、系列グラフがつながっている。取り込んだ Run には印が付く。
    asset = client_oidc.get(f"/api/assets/{root}").json()
    assert asset["kind"] == "generated"
    lineage = client_oidc.get(f"/api/assets/{root}/lineage").json()
    run_nodes = [n for n in lineage["nodes"] if n["type"] == "run"]
    asset_nodes = [n for n in lineage["nodes"] if n["type"] == "asset"]
    assert len(run_nodes) == 2
    assert all(n["run"]["imported"] for n in run_nodes)
    assert len(asset_nodes) == 4
    assert any(e["kind"] == "sketch_source" for e in lineage["edges"])

    produced_by = asset["produced_by_run"]["id"]
    run = client_oidc.get(f"/api/runs/{produced_by}").json()
    assert run["origin"] == "import"
    assert run["status"] == "succeeded"
    assert run["prompt"] == "edit again"
    assert run["cost_usd"] is None
    assert run["created_by"]["email"] == "b@example.com"
    imported = run["imported"]
    assert imported["source_run_id"] == r2["id"]
    assert imported["source_creator_name"] == "Alice"
    assert imported["source_created_at"]
    assert imported["source_gakei_version"]
    assert run["primary_parent_asset_id"] is not None

    # 続きの Edit ができる(FAKE プロバイダー)。
    continued = _run(client_oidc, "continue", [root])
    lineage = client_oidc.get(f"/api/assets/{_out(continued)}/lineage").json()
    assert len([n for n in lineage["nodes"] if n["type"] == "run"]) == 3

    # 取り込んだ Run は実行中・待機中ではなく、通常の一覧にも出る。
    listed = client_oidc.get("/api/runs").json()["items"]
    imported_runs = [r for r in listed if r["origin"] == "import"]
    assert len(imported_runs) == 2
    assert all(r["status"] in ("succeeded", "failed", "canceled") for r in imported_runs)

    # A には B が取り込んだものは見えない。
    login_as(client_oidc, "a@example.com", name="Alice")
    assert client_oidc.get(f"/api/assets/{root}").status_code == 404
    assert client_oidc.get(f"/api/runs/{produced_by}").status_code == 404


def test_round_trip_to_fresh_instance(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    r0, r1, r2 = _chain(client)
    zip_bytes = _export(client, _out(r1), "lineage")

    with _second_client(monkeypatch, tmp_path, "other-instance") as other:
        assert _counts(other) == (0, 0, 0, 0)
        result = _import_ok(other, zip_bytes)
        assert result["created_asset_count"] == 3
        assert result["created_run_count"] == 3
        root = result["root_asset_id"]
        lineage = other.get(f"/api/assets/{root}/lineage").json()
        assert len([n for n in lineage["nodes"] if n["type"] == "run"]) == 3
        assert len([n for n in lineage["nodes"] if n["type"] == "asset"]) == 3
        # 原本は同じバイト列(sha256)のまま。
        source = {a["sha256"] for a in _manifest(zip_bytes)["assets"]}
        with other.app.state.session_factory() as db:
            assert set(db.execute(select(Asset.sha256)).scalars()) == source
        continued = _run(other, "continue", [root])
        assert continued["status"] == "succeeded"


def test_reimport_by_same_user_creates_nothing(client: TestClient) -> None:
    upload_id = _upload(client, make_png_bytes(color=(40, 50, 60)))
    r1 = _run(client, "edit", [upload_id])
    zip_bytes = _export(client, _out(r1))

    first = _import_ok(client, zip_bytes)
    # 個人モードの同じ利用者: アップロードは同じ内容の既存 Asset を使い、Run は新しく作る。
    assert first["created_run_count"] == 1
    assert first["created_asset_count"] == 1
    counts = _counts(client)
    with client.app.state.session_factory() as db:
        imported_at = db.execute(select(RunImport.imported_at)).scalar_one()

    second = _import_ok(client, zip_bytes)
    assert second["root_asset_id"] == first["root_asset_id"]
    assert second["created_run_count"] == 0
    assert second["created_asset_count"] == 0
    assert _counts(client) == counts
    with client.app.state.session_factory() as db:
        # 既存の run_import の行は書き換えない(追記のみ)。
        assert db.execute(select(RunImport.imported_at)).scalar_one() == imported_at


def test_reimport_by_another_user_creates_separate_copy(client_oidc: TestClient) -> None:
    login_as(client_oidc, "a@example.com", name="Alice")
    r0 = _run(client_oidc, "root")
    zip_bytes = _export(client_oidc, _out(r0))

    login_as(client_oidc, "b@example.com", name="Bob")
    b = _import_ok(client_oidc, zip_bytes)
    assert _import_ok(client_oidc, zip_bytes)["created_run_count"] == 0

    login_as(client_oidc, "c@example.com", name="Carol")
    c = _import_ok(client_oidc, zip_bytes)
    assert c["created_run_count"] == 1
    assert c["created_asset_count"] == 1
    assert c["root_asset_id"] != b["root_asset_id"]


def test_tampered_file_is_rejected_and_nothing_is_written(client: TestClient) -> None:
    r0, r1, _ = _chain(client)
    zip_bytes = _export(client, _out(r1))
    entries = _entries(zip_bytes)
    manifest = _manifest(zip_bytes)
    target = manifest["assets"][-1]["file"]
    # 同じ大きさの別の内容に差し替える(大きさの確認を通り、sha256 で断られる)。
    original = entries[target]
    entries[target] = original[:-1] + bytes([original[-1] ^ 0xFF])
    before = _counts(client)
    response = _import(client, _rezip(entries))
    assert response.status_code == 422, response.text
    assert target in response.json()["detail"]
    assert _counts(client) == before


def test_failure_midway_leaves_no_rows(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    r0, r1, _ = _chain(client)
    zip_bytes = _export(client, _out(r1))
    entries = _entries(zip_bytes)
    manifest = _manifest(zip_bytes)
    # 最後の Asset を「マスク」と偽り、中身を JPEG にする。Run と前の Asset を作った後の
    # `ingest` で断られる(マスクは PNG だけ)。
    jpeg = io.BytesIO()
    Image.new("RGB", (16, 16), (1, 1, 1)).save(jpeg, format="JPEG")
    data = jpeg.getvalue()
    bad = {
        "id": str(uuid.uuid4()),
        "sha256": hashlib.sha256(data).hexdigest(),
        "kind": "mask",
        "mime": "image/jpeg",
        "width": 16,
        "height": 16,
        "bytes": len(data),
        "file": "",
    }
    bad["file"] = f"assets/{bad['id']}.jpg"
    manifest["assets"].append(bad)
    entries[bad["file"]] = data
    entries["manifest.json"] = json.dumps(manifest).encode()

    calls: list[str] = []
    original_ingest = import_domain.assets_domain.ingest

    def counting_ingest(*args: Any, **kwargs: Any) -> Any:
        calls.append(str(args[3]))
        return original_ingest(*args, **kwargs)

    before = _counts(client)
    monkeypatch.setattr(import_domain.assets_domain, "ingest", counting_ingest)
    response = _import(client, _rezip(entries))
    assert response.status_code == 422, response.text
    # 途中まで作ってから失敗している。
    assert len(calls) == 3
    assert _counts(client) == before


@pytest.mark.parametrize(
    ("mutate", "status", "message"),
    [
        (lambda e, m: m.update(format="gakei.lineage-export/2"), 422, "形式"),
        (lambda e, m: m.pop("assets"), 422, "manifest.json"),
        (lambda e, m: m.update(root_asset_id=str(uuid.uuid4())), 422, "manifest.json"),
        (lambda e, m: e.pop(m["assets"][0]["file"]), 422, "ZIP に画像がありません"),
        (lambda e, m: m["runs"][0].update(status="running"), 422, "manifest.json"),
    ],
)
def test_invalid_manifest_is_rejected(
    client: TestClient, mutate: Any, status: int, message: str
) -> None:
    r0 = _run(client, "root")
    zip_bytes = _export(client, _out(r0))
    entries = _entries(zip_bytes)
    manifest = _manifest(zip_bytes)
    mutate(entries, manifest)
    entries["manifest.json"] = json.dumps(manifest).encode()
    before = _counts(client)
    response = _import(client, _rezip(entries))
    assert response.status_code == status, response.text
    assert message in response.json()["detail"]
    assert _counts(client) == before


def test_rejects_non_zip_and_missing_manifest(client: TestClient) -> None:
    response = _import(client, b"this is not a zip")
    assert response.status_code == 422
    assert "ZIP" in response.json()["detail"]
    response = _import(client, _rezip({"hello.txt": b"hi"}))
    assert response.status_code == 422
    assert "manifest.json" in response.json()["detail"]


def test_rejects_too_many_entries(client: TestClient) -> None:
    entries = {f"assets/{i}.png": b"x" for i in range(import_domain.MAX_ENTRIES + 1)}
    entries["manifest.json"] = b"{}"
    response = _import(client, _rezip(entries))
    assert response.status_code == 422
    assert "多すぎます" in response.json()["detail"]


def test_rejects_suspicious_compression_ratio(client: TestClient) -> None:
    entries = {"manifest.json": b"{}", "assets/bomb.png": b"\0" * (8 * 1024 * 1024)}
    response = _import(client, _rezip(entries, compression=zipfile.ZIP_DEFLATED))
    assert response.status_code == 422
    assert "圧縮率" in response.json()["detail"]


def test_rejects_oversized_zip_and_file(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    r0 = _run(client, "root")
    zip_bytes = _export(client, _out(r0))
    monkeypatch.setattr(import_domain, "MAX_ZIP_BYTES", len(zip_bytes) - 1)
    response = _import(client, zip_bytes)
    assert response.status_code == 413
    monkeypatch.setattr(import_domain, "MAX_ZIP_BYTES", 1024 * 1024 * 1024)

    monkeypatch.setattr(import_domain, "MAX_FILE_BYTES", 10)
    response = _import(client, zip_bytes)
    assert response.status_code == 422
    assert "大きすぎる" in response.json()["detail"]
    assert _counts(client)[3] == 0


def test_error_messages_follow_accept_language(client: TestClient) -> None:
    response = client.post(
        "/api/imports/lineage",
        files={"file": ("x.zip", b"nope", "application/zip")},
        headers={"Accept-Language": "en"},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "The file cannot be read as a ZIP"


def test_imported_runs_are_not_counted_in_mcp_quota(client: TestClient) -> None:
    r0, r1, _ = _chain(client)
    zip_bytes = _export(client, _out(r1))
    _import_ok(client, zip_bytes)
    with client.app.state.session_factory() as db:
        assert (
            db.execute(
                select(func.count()).select_from(Run).where(Run.origin == RUN_ORIGIN_IMPORT)
            ).scalar_one()
            == 2
        )
        assert count_recent_mcp_runs(db) == 0
        imported = db.execute(select(Run).where(Run.origin == RUN_ORIGIN_IMPORT)).scalars().all()
        # 実行していないのでキューに載らない(終了状態で作る)。
        assert all(r.status == RunStatus.SUCCEEDED for r in imported)
        assert all(r.started_at is None for r in imported)


def test_run_import_is_append_only_when_run_is_deleted(client: TestClient) -> None:
    r0 = _run(client, "root")
    result = _import_ok(client, _export(client, _out(r0)))
    asset = client.get(f"/api/assets/{result['root_asset_id']}").json()
    run_id = asset["produced_by_run"]["id"]
    with client.app.state.session_factory() as db:
        row = db.get(RunImport, uuid.UUID(run_id))
        assert row is not None
        snapshot = (
            row.source_run_id,
            row.source_creator_name,
            row.source_created_at,
            row.source_finished_at,
            row.source_gakei_version,
            row.imported_at,
        )
    assert client.delete(f"/api/runs/{run_id}").status_code == 204
    with client.app.state.session_factory() as db:
        row = db.get(RunImport, uuid.UUID(run_id))
        assert row is not None
        assert (
            row.source_run_id,
            row.source_creator_name,
            row.source_created_at,
            row.source_finished_at,
            row.source_gakei_version,
            row.imported_at,
        ) == snapshot
    # 削除した取り込み Run は使わず、もう一度取り込めば新しく作る。
    again = _import_ok(client, _export(client, _out(r0)))
    assert again["created_run_count"] == 1


# -- オブジェクトストレージ(ADR-0028) ------------------------------------------------


@pytest.fixture
def s3_client(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, data_dir: Path
) -> Iterator[TestClient]:
    store = request.getfixturevalue("s3_store")
    monkeypatch.setattr(app_main, "open_store", lambda _settings: store)
    agent_images.clear_cache()
    app = app_main.create_app(_fake_settings(data_dir))
    with TestClient(app) as test_client:
        assert test_client.app.state.store is store
        yield test_client


def test_export_and_import_with_object_storage(s3_client: TestClient) -> None:
    upload_id = _upload(s3_client, make_png_bytes(color=(70, 80, 90)))
    r1 = _run(s3_client, "edit", [upload_id])
    zip_bytes = _export(s3_client, _out(r1))
    manifest = _manifest(zip_bytes)
    entries = _entries(zip_bytes)
    for item in manifest["assets"]:
        assert hashlib.sha256(entries[item["file"]]).hexdigest() == item["sha256"]
    result = _import_ok(s3_client, zip_bytes)
    root = result["root_asset_id"]
    content = s3_client.get(f"/api/assets/{root}/content", params={"variant": "original"})
    assert content.status_code == 200
    root_item = next(a for a in manifest["assets"] if a["id"] == _out(r1))
    assert hashlib.sha256(content.content).hexdigest() == root_item["sha256"]


def test_export_redacts_comfyui_secret_params(client: TestClient) -> None:
    """ComfyUI の Run の秘密に見える値は、共有リンクと同じく `***` にして書き出す(ZIP は他人に
    渡すもの)。DB の `params` は変えない。"""
    r0, _r1, _r2 = _chain(client)
    graph = {
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "a cat"}},
        "12": {"class_type": "SomeApiNode", "inputs": {"api_key": "plain-secret", "n": 1}},
    }
    with client.app.state.session_factory() as db:
        run = db.get(Run, uuid.UUID(r0["id"]))
        assert run is not None
        run.provider = "comfyui"
        run.params = {"comfyui_prompt": graph, "negative_prompt": "blurry"}
        db.commit()

    manifest = _manifest(_export(client, _out(r0)))
    exported = next(r for r in manifest["runs"] if r["id"] == r0["id"])
    assert exported["params"]["comfyui_prompt"]["12"]["inputs"]["api_key"] == "***"
    assert exported["params"]["comfyui_prompt"]["12"]["inputs"]["n"] == 1
    assert exported["params"]["comfyui_prompt"]["6"] == graph["6"]
    assert exported["params"]["negative_prompt"] == "blurry"
    with client.app.state.session_factory() as db:
        run = db.get(Run, uuid.UUID(r0["id"]))
        assert run is not None
        assert run.params["comfyui_prompt"]["12"]["inputs"]["api_key"] == "plain-secret"
