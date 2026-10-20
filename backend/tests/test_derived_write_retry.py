"""ローカルFSの派生の置き換えが PermissionError になるとき(Windows で置き換え先が開かれている)。

`os.replace` を差し替えて Windows の振る舞いを再現するので、Linux でも回る。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

from app.domain import storage
from app.domain.assets import ingest
from app.domain.models import AssetKind
from app.domain.storage import LocalFsStore, derived_key
from tests.conftest import make_png_bytes

pytestmark = pytest.mark.windows

SHA = "a" * 64


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """やり直しの待ち時間を記録し、実際には待たない。"""
    recorded: list[float] = []
    monkeypatch.setattr(storage.time, "sleep", recorded.append)
    return recorded


def _deny_replace(monkeypatch: pytest.MonkeyPatch, failures: int | None) -> list[int]:
    """`os.replace` を `failures` 回(None なら常に)PermissionError にする。呼んだ回数を返す。"""
    calls = [0]
    real_replace = os.replace

    def fake_replace(src: os.PathLike[str] | str, dst: os.PathLike[str] | str) -> None:
        calls[0] += 1
        if failures is None or calls[0] <= failures:
            raise PermissionError(5, "Access is denied")
        real_replace(src, dst)

    monkeypatch.setattr(storage.os, "replace", fake_replace)
    return calls


def _tmp_files(store: LocalFsStore) -> list[Path]:
    return list((store.root / "derived" / SHA).glob(".tmp-*"))


def test_retries_and_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    store = LocalFsStore(tmp_path)
    store.write_derived(SHA, "thumb", b"old")
    calls = _deny_replace(monkeypatch, failures=2)

    store.write_derived(SHA, "thumb", b"new")

    assert calls[0] == 3
    assert sleeps == list(storage._DERIVED_REPLACE_RETRY_DELAYS[:2])
    assert (tmp_path / derived_key(SHA, "thumb")).read_bytes() == b"new"
    assert _tmp_files(store) == []


def test_gives_up_with_warning_when_target_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sleeps: list[float],
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = LocalFsStore(tmp_path)
    store.write_derived(SHA, "thumb", b"old")
    calls = _deny_replace(monkeypatch, failures=None)

    with caplog.at_level(logging.WARNING, logger="app.domain.storage"):
        store.write_derived(SHA, "thumb", b"new")  # 例外にしない

    delays = storage._DERIVED_REPLACE_RETRY_DELAYS
    assert calls[0] == len(delays) + 1
    assert sleeps == list(delays)
    assert sum(delays) <= 1.0
    # 既存の派生はそのまま残る
    assert (tmp_path / derived_key(SHA, "thumb")).read_bytes() == b"old"
    assert any("派生を置き換えられない" in r.getMessage() for r in caplog.records)
    assert _tmp_files(store) == []


def test_raises_when_target_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    store = LocalFsStore(tmp_path)
    _deny_replace(monkeypatch, failures=None)

    with pytest.raises(PermissionError):
        store.write_derived(SHA, "thumb", b"new")

    assert not (tmp_path / derived_key(SHA, "thumb")).exists()
    assert _tmp_files(store) == []


def test_ingest_same_content_twice_does_not_rewrite_derived(
    db_session_factory: sessionmaker,
    local_store: LocalFsStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同じ内容を取り込み直すとき、今の版の派生が既にあれば書き直さない。"""
    data = make_png_bytes()
    written: list[str] = []
    real_write = LocalFsStore.write_derived

    def recording_write(self: LocalFsStore, sha256: str, variant: str, payload: bytes) -> None:
        written.append(variant)
        real_write(self, sha256, variant, payload)

    monkeypatch.setattr(LocalFsStore, "write_derived", recording_write)

    with db_session_factory() as session:
        first = ingest(session, local_store, data, AssetKind.UPLOAD)
        assert written == ["thumb", "preview"]

        written.clear()
        second = ingest(session, local_store, data, AssetKind.UPLOAD)
        session.commit()

    assert first.id != second.id
    assert first.blob_key == second.blob_key
    assert written == []


def test_ingest_shared_original_fills_missing_derived(
    db_session_factory: sessionmaker,
    local_store: LocalFsStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """原本を共有していても、欠けている派生だけは作る。"""
    data = make_png_bytes()
    written: list[str] = []
    real_write = LocalFsStore.write_derived

    def recording_write(self: LocalFsStore, sha256: str, variant: str, payload: bytes) -> None:
        written.append(variant)
        real_write(self, sha256, variant, payload)

    monkeypatch.setattr(LocalFsStore, "write_derived", recording_write)

    with db_session_factory() as session:
        first = ingest(session, local_store, data, AssetKind.UPLOAD)
        (local_store.root / derived_key(first.sha256, "preview")).unlink()

        written.clear()
        ingest(session, local_store, data, AssetKind.UPLOAD)
        session.commit()

    assert written == ["preview"]
    assert (local_store.root / derived_key(first.sha256, "preview")).is_file()
