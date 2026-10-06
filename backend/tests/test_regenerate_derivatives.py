"""今の版の派生を前もって作るツール(ADR-0036 2章・4章)。ローカルFS と S3(moto)で回す。

Azurite があれば Azure Blob でも回す(`azure_blob_store` が無ければ skip)。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.main as app_main
import app.tools.regenerate_derivatives as tool
from app.config import Settings
from app.domain import derivatives
from app.domain.models import Asset
from app.domain.storage import LocalFsStore
from tests.conftest import _fake_settings, make_png_bytes

pytestmark = pytest.mark.windows


@pytest.fixture(params=["local", "s3", "azure_blob"])
def env(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, data_dir: Path
) -> Iterator[tuple[TestClient, Any]]:
    """(アプリ, 保存先)。ツールもアプリと同じ保存先を使う。"""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    if request.param == "local":
        store: Any = LocalFsStore(data_dir)
    else:
        store = request.getfixturevalue(f"{request.param}_store")
    monkeypatch.setattr(app_main, "open_store", lambda _settings: store)
    monkeypatch.setattr(tool, "open_store", lambda _settings: store)
    with TestClient(app_main.create_app(_fake_settings(data_dir))) as client:
        yield client, store


def _settings(data_dir: Path) -> Settings:
    return Settings(_env_file=None, data_dir=data_dir)


def _upload(client: TestClient, color: tuple[int, int, int]) -> str:
    response = client.post(
        "/api/assets",
        files={"file": ("a.png", make_png_bytes(color=color), "image/png")},
        data={"kind": "upload"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _assets(client: TestClient) -> list[Asset]:
    with client.app.state.session_factory() as db:
        rows = list(db.scalars(select(Asset)).all())
        for row in rows:
            db.expunge(row)
        return rows


def _derived(store: Any) -> set[str]:
    return set(store.list_keys("derived/"))


def _populate(client: TestClient) -> list[Asset]:
    """アップロード3枚(うち1枚は論理削除、1枚は同じ内容をもう1回)。"""
    first = _upload(client, (10, 20, 30))
    _upload(client, (10, 20, 30))  # 同じ内容(派生は共有)
    deleted = _upload(client, (40, 50, 60))
    assert client.delete(f"/api/assets/{deleted}").status_code in (200, 204)
    assert first
    return _assets(client)


def test_regenerate_and_prune(
    env: tuple[TestClient, Any], data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, store = env
    assets = _populate(client)
    shas = sorted({a.sha256 for a in assets})
    assert len(shas) == 2
    originals = {a.blob_key: store.read(a.blob_key) for a in assets}
    v1 = {f"derived/{sha}/{v}.webp" for sha in shas for v in ("thumb", "preview")}
    assert _derived(store) == v1

    # 今の版(1)の派生はそろっている。
    report = tool.regenerate(_settings(data_dir), dry_run=True)
    assert (report.version, report.images, report.missing) == (1, 2, 0)

    monkeypatch.setattr(derivatives, "DERIVED_VERSION", 2)
    v2 = {f"derived/{sha}/{v}.v2.webp" for sha in shas for v in ("thumb", "preview")}

    # --dry-run は数えるだけ。
    report = tool.regenerate(_settings(data_dir), dry_run=True, prune=True)
    assert (report.version, report.images, report.missing, report.stale) == (2, 2, 4, 4)
    assert (report.generated, report.pruned) == (0, 0)
    assert _derived(store) == v1

    # 作る(論理削除済みも含む)。版 1 の派生は残す。
    report = tool.regenerate(_settings(data_dir))
    assert (report.missing, report.generated, report.failed) == (4, 4, [])
    assert _derived(store) == v1 | v2
    for sha in shas:
        assert store.read(f"derived/{sha}/thumb.v2.webp")[:4] == b"RIFF"

    # 2 回目は作るものが無い。
    report = tool.regenerate(_settings(data_dir))
    assert (report.missing, report.generated) == (0, 0)

    # --prune は今の版以外の派生だけを消す。原本と今の版には触れない。
    report = tool.regenerate(_settings(data_dir), prune=True)
    assert (report.stale, report.pruned) == (4, 4)
    assert _derived(store) == v2
    for key, data in originals.items():
        assert store.read(key) == data


def test_missing_original_is_reported(
    env: tuple[TestClient, Any], data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, store = env
    asset_id = _upload(client, (70, 80, 90))
    other_id = _upload(client, (1, 2, 3))
    by_id = {str(a.id): a for a in _assets(client)}
    gone = by_id[asset_id]
    store.delete(gone.blob_key)

    monkeypatch.setattr(derivatives, "DERIVED_VERSION", 2)
    report = tool.regenerate(_settings(data_dir))
    assert report.failed == [gone.sha256]
    assert report.generated == 2
    assert store.exists(f"derived/{by_id[other_id].sha256}/thumb.v2.webp")
    assert not store.exists(f"derived/{gone.sha256}/thumb.v2.webp")


def test_prune_keeps_unknown_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """派生のキーの形でないもの(一時ファイルなど)は、--prune でも消さない。"""
    store = LocalFsStore(tmp_path / "store")
    sha = "ab" * 32
    folder = store.root / "derived" / sha
    folder.mkdir(parents=True)
    for name in ("thumb.webp", "preview.v3.webp", ".tmp-x.webp", "notes.txt"):
        (folder / name).write_bytes(b"x")
    report = tool.RegenerateReport(version=3, dry_run=False, prune=True)
    tool._prune(store, report)
    assert (report.stale, report.pruned) == (1, 1)
    assert sorted(p.name for p in folder.iterdir()) == [
        ".tmp-x.webp",
        "notes.txt",
        "preview.v3.webp",
    ]


def test_empty_database(tmp_path: Path) -> None:
    data_dir = tmp_path / "empty"
    with pytest.raises(tool.RegenerateAbortedError):
        tool.regenerate(_settings(data_dir), dry_run=True)


def test_main_prints_report(
    env: tuple[TestClient, Any],
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client, _store = env
    _upload(client, (5, 5, 5))
    monkeypatch.setattr(tool, "get_settings", lambda: _settings(data_dir))
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LC_MESSAGES", raising=False)
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    tool.main(["--dry-run", "--prune"])
    out = capsys.readouterr().out
    assert "Derived image version: 1" in out
    assert "Images: 1" in out
    assert "--dry-run" in out
