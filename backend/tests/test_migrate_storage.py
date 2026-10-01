"""ローカルFSからオブジェクトストレージへの移行ツール(ADR-0028 6章)。移行先は S3(moto)。"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.main as app_main
import app.tools.migrate_storage as migrate_storage
from app.config import Settings
from app.domain.models import Asset
from app.domain.storage import legacy_original_key
from app.tools.migrate_storage import MigrationAbortedError, migrate
from tests.conftest import _fake_settings, make_png_bytes, wait_for_run_terminal


@pytest.fixture
def target(s3_store: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """移行先(テストごとの接頭辞の下の S3Store)。接続の設定は環境変数ではなくこれを使う。"""
    monkeypatch.setattr(migrate_storage, "open_store", lambda _settings: s3_store)
    return s3_store


def _settings(data_dir: Path) -> Settings:
    return Settings(_env_file=None, data_dir=data_dir, s3_bucket="gakei-test")


def _upload(client: TestClient, color: tuple[int, int, int]) -> str:
    response = client.post(
        "/api/assets",
        files={"file": ("f.png", make_png_bytes(color=color), "image/png")},
        data={"kind": "upload"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _generate(client: TestClient) -> str:
    response = client.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": "gpt-image-2.5-sunburst",
            "prompt": "移行ツールのテスト",
            "params": {"n": 1, "size": "1024x1024"},
        },
    )
    assert response.status_code == 202, response.text
    detail = wait_for_run_terminal(client, response.json()["id"])
    assert detail["status"] == "succeeded", detail
    return detail["outputs"][0]["asset_id"]


def _assets(client: TestClient) -> list[Asset]:
    with client.app.state.session_factory() as db:
        rows = db.scalars(select(Asset)).all()
        for row in rows:
            db.expunge(row)
        return list(rows)


def _local_files(data_dir: Path) -> dict[str, bytes]:
    return {
        p.relative_to(data_dir).as_posix(): p.read_bytes()
        for folder in ("assets", "derived")
        for p in (data_dir / folder).rglob("*")
        if p.is_file()
    }


@pytest.fixture
def populated(client: TestClient, data_dir: Path) -> TestClient:
    """生成1枚、アップロード2枚(うち1枚は論理削除)、古いキーの原本1枚。"""
    _generate(client)
    _upload(client, (1, 2, 3))
    deleted = _upload(client, (4, 5, 6))
    assert client.delete(f"/api/assets/{deleted}").status_code == 204

    # 1枚を古いキー(ADR-0026 より前)に置き直す
    legacy_id = _upload(client, (7, 8, 9))
    with client.app.state.session_factory() as db:
        asset = db.get(Asset, uuid.UUID(legacy_id))
        assert asset is not None
        legacy = legacy_original_key(asset.sha256, "png")
        (data_dir / legacy).parent.mkdir(parents=True, exist_ok=True)
        (data_dir / asset.blob_key).rename(data_dir / legacy)
        asset.blob_key = legacy
        db.commit()
    return client


def test_dry_run_counts_only(populated: TestClient, data_dir: Path, target: Any) -> None:
    files = _local_files(data_dir)
    report = migrate(_settings(data_dir), "s3", dry_run=True)

    originals = {a.blob_key for a in _assets(populated)}
    assert report.originals == len(originals) == 4
    assert report.derived == 8  # 4枚 × (thumb, preview)
    assert report.total_bytes == sum(len(v) for v in files.values())
    assert report.copied == 0
    assert target.size_of(next(iter(originals))) is None


def test_migrate_copies_with_same_keys(
    populated: TestClient, data_dir: Path, target: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = _assets(populated)
    files = _local_files(data_dir)

    report = migrate(_settings(data_dir), "s3")
    assert report.copied == len(files) == 12
    assert report.skipped == 0
    assert report.missing == []
    for key, data in files.items():
        assert target.read(key) == data, key

    # DB は書き換えず、ローカルのファイルも消さない
    assert [(a.id, a.blob_key) for a in _assets(populated)] == [(a.id, a.blob_key) for a in before]
    assert _local_files(data_dir) == files

    # もう一度実行すると、全部飛ばす(続きからコピーできる)
    again = migrate(_settings(data_dir), "s3")
    assert (again.copied, again.skipped) == (0, 12)

    # 移行先を保存先にしたアプリで、論理削除済み・古いキーを含めて配信できる
    monkeypatch.setattr(app_main, "open_store", lambda _settings: target)
    with TestClient(app_main.create_app(_fake_settings(data_dir))) as client:
        for asset in before:
            for variant in ("original", "thumb", "preview"):
                response = client.get(f"/api/assets/{asset.id}/content?variant={variant}")
                assert response.status_code == 200, (asset.blob_key, variant)


def test_resumes_after_partial_copy(populated: TestClient, data_dir: Path, target: Any) -> None:
    files = _local_files(data_dir)
    first_key = next(iter(files))
    target.write_new(first_key, files[first_key])

    report = migrate(_settings(data_dir), "s3")
    assert (report.copied, report.skipped) == (len(files) - 1, 1)


def test_aborts_on_size_mismatch(populated: TestClient, data_dir: Path, target: Any) -> None:
    asset = _assets(populated)[0]
    target.write_new(asset.blob_key, b"different")

    with pytest.raises(MigrationAbortedError) as exc_info:
        migrate(_settings(data_dir), "s3")
    assert asset.blob_key in str(exc_info.value)
    # 上書きしない
    assert target.read(asset.blob_key) == b"different"


def test_missing_local_original_is_reported(
    populated: TestClient, data_dir: Path, target: Any
) -> None:
    asset = _assets(populated)[0]
    (data_dir / asset.blob_key).unlink()

    report = migrate(_settings(data_dir), "s3")
    assert report.missing == [asset.blob_key]
    assert report.originals == 3
    assert target.size_of(asset.blob_key) is None


def test_missing_sqlite_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # SQLite のファイルが無い場合の確認なので、PostgreSQL で回すとき(ADR-0027 6章)の
    # DATABASE_URL は外す。
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(MigrationAbortedError):
        migrate(_settings(tmp_path / "empty"), "s3", dry_run=True)


def test_cli_dry_run_output(
    populated: TestClient,
    data_dir: Path,
    target: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(migrate_storage, "get_settings", lambda: _settings(data_dir))
    migrate_storage.main(["--to", "s3", "--dry-run"])
    out = capsys.readouterr().out
    assert "移行先: s3 (gakei-test)" in out
    assert "原本: 4 件" in out
    assert "--dry-run: 合計 12 件" in out

    with pytest.raises(SystemExit) as exc_info:
        migrate_storage.main(["--to", "local"])
    assert exc_info.value.code == 2


def test_derived_size_mismatch_is_overwritten_with_warning(
    populated: TestClient, data_dir: Path, target: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """派生は原本から作り直せるので、大きさが違っても止めずに警告して上書きする。"""
    files = _local_files(data_dir)
    derived_key = next(k for k in files if k.startswith("derived/"))
    target.write_new(derived_key, b"stale")

    report = migrate(_settings(data_dir), "s3")
    assert report.copied == len(files)
    assert target.read(derived_key) == files[derived_key]
    err = capsys.readouterr().err
    assert derived_key in err
    assert "上書き" in err


def test_storage_error_aborts_with_message(
    populated: TestClient,
    data_dir: Path,
    target: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """コピー中の保存先の失敗は、トレースバックではなく文言を出して終了する(鍵は出さない)。"""
    from app.domain.storage import StorageIOError

    def _fail(key: str, data: bytes) -> None:
        raise StorageIOError("EndpointConnectionError: Could not connect")

    monkeypatch.setattr(target, "write_new", _fail)
    with pytest.raises(MigrationAbortedError) as exc_info:
        migrate(_settings(data_dir), "s3")
    assert "EndpointConnectionError" in str(exc_info.value)
    assert exc_info.value.__cause__ is None

    monkeypatch.setattr(migrate_storage, "get_settings", lambda: _settings(data_dir))
    with pytest.raises(SystemExit) as exit_info:
        migrate_storage.main(["--to", "s3"])
    assert exit_info.value.code == 1
    err = capsys.readouterr().err
    assert "EndpointConnectionError" in err
    assert "Traceback" not in err


def test_size_check_error_aborts_with_message(
    populated: TestClient, data_dir: Path, target: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.domain.storage import StoragePermissionError

    def _forbidden(key: str) -> int | None:
        raise StoragePermissionError(f"403 s3:ListBucket {key}")

    monkeypatch.setattr(target, "size_of", _forbidden)
    with pytest.raises(MigrationAbortedError) as exc_info:
        migrate(_settings(data_dir), "s3")
    assert "s3:ListBucket" in str(exc_info.value)
