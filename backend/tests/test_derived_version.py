"""派生画像の版(ADR-0036)。版付きのキー、その場での作り直し、ETag、各読み出しの経路。

版を上げたときの動きは、`derivatives.DERIVED_VERSION` を 2 に差し替えて確かめる(版 1 の
派生はそのまま残り、版 2 の派生がその場で作られる)。
"""

from __future__ import annotations

import io
import re
import time
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.domain import agent_images, derivatives
from app.domain.models import Asset
from app.domain.storage import LocalFsStore, derived_key, parse_derived_key
from tests.conftest import make_png_bytes, wait_for_background_reads

pytestmark = pytest.mark.windows

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _upload(client: TestClient, color: tuple[int, int, int] = (200, 30, 30)) -> dict:
    response = client.post(
        "/api/assets",
        files={"file": ("a.png", make_png_bytes(800, 400, color), "image/png")},
        data={"kind": "upload"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _asset(client: TestClient, asset_id: str) -> Asset:
    with client.app.state.session_factory() as db:
        asset = db.get(Asset, uuid.UUID(asset_id))
        assert asset is not None
        db.expunge(asset)
        return asset


@pytest.fixture
def make_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """派生を作った回数(variant ごと)を数える。"""
    calls: list[str] = []
    original = derivatives.make_derived

    def counting(image: Image.Image, variant: Any) -> bytes:
        calls.append(variant)
        return original(image, variant)

    monkeypatch.setattr(derivatives, "make_derived", counting)
    return calls


def _bump_version(monkeypatch: pytest.MonkeyPatch, version: int = 2) -> None:
    monkeypatch.setattr(derivatives, "DERIVED_VERSION", version)
    agent_images.clear_cache()


# -- キー ------------------------------------------------------------------------


def test_version_1_keys_are_unchanged() -> None:
    sha = "ab" * 32
    assert derivatives.DERIVED_VERSION == 1
    assert derived_key(sha, "thumb") == f"derived/{sha}/thumb.webp"
    assert derived_key(sha, "preview") == f"derived/{sha}/preview.webp"
    assert derived_key(sha, "thumb", 1) == f"derived/{sha}/thumb.webp"
    assert derived_key(sha, "thumb", 2) == f"derived/{sha}/thumb.v2.webp"
    assert derived_key(sha, "preview", 12) == f"derived/{sha}/preview.v12.webp"


def test_current_version_follows_constant(monkeypatch: pytest.MonkeyPatch) -> None:
    sha = "cd" * 32
    _bump_version(monkeypatch)
    assert derived_key(sha, "thumb") == f"derived/{sha}/thumb.v2.webp"
    assert derived_key(sha, "preview") == f"derived/{sha}/preview.v2.webp"


def test_parse_derived_key() -> None:
    sha = "ef" * 32
    assert parse_derived_key(f"derived/{sha}/thumb.webp") == (sha, "thumb", 1)
    assert parse_derived_key(f"derived/{sha}/preview.v3.webp") == (sha, "preview", 3)
    for key in (
        f"derived/{sha}/.tmp-abc.webp",
        f"derived/{sha}/thumb.v0.webp",
        f"derived/{sha}/other.webp",
        f"assets/{sha}/thumb.webp",
        "derived/thumb.webp",
    ):
        assert parse_derived_key(key) is None, key


def test_frontend_version_matches_backend() -> None:
    """フロントの `DERIVED_VERSION`(assetUrl.ts)とバックエンドの版が一致する(ADR-0036 3章)。"""
    source = (_REPO_ROOT / "frontend" / "src" / "api" / "assetUrl.ts").read_text(encoding="utf-8")
    match = re.search(r"export\s+const\s+DERIVED_VERSION\s*(?::\s*number\s*)?=\s*(\d+)", source)
    assert match is not None, "assetUrl.ts に DERIVED_VERSION がありません"
    assert int(match.group(1)) == derivatives.DERIVED_VERSION


# -- 配信 ------------------------------------------------------------------------


def test_etag_contains_version(client: TestClient) -> None:
    uploaded = _upload(client)
    sha = _asset(client, uploaded["id"]).sha256
    for variant in ("thumb", "preview"):
        response = client.get(f"/api/assets/{uploaded['id']}/content?variant={variant}&dv=1")
        assert response.status_code == 200
        assert response.headers["etag"] == f'"{sha}-{variant}-d1"'
    original = client.get(f"/api/assets/{uploaded['id']}/content?variant=original")
    assert original.headers["etag"] == f'"{sha}-original"'


def test_bumped_version_generates_new_derivative(
    client: TestClient, data_dir: Path, monkeypatch: pytest.MonkeyPatch, make_calls: list[str]
) -> None:
    uploaded = _upload(client)
    asset_id = uploaded["id"]
    sha = _asset(client, asset_id).sha256
    v1_thumb = data_dir / f"derived/{sha}/thumb.webp"
    v1_bytes = v1_thumb.read_bytes()
    v1_mtime = v1_thumb.stat().st_mtime_ns
    make_calls.clear()

    _bump_version(monkeypatch)
    first = client.get(f"/api/assets/{asset_id}/content?variant=thumb")
    assert first.status_code == 200
    assert first.headers["content-type"] == "image/webp"
    assert first.headers["etag"] == f'"{sha}-thumb-d2"'
    v2_thumb = data_dir / f"derived/{sha}/thumb.v2.webp"
    assert first.content == v2_thumb.read_bytes()
    with Image.open(io.BytesIO(first.content)) as image:
        assert image.size == (512, 256)
    assert make_calls == ["thumb"]
    # 版 1 の派生には触れない。
    assert v1_thumb.read_bytes() == v1_bytes
    assert v1_thumb.stat().st_mtime_ns == v1_mtime

    # 2 回目は作り直さない。
    second = client.get(f"/api/assets/{asset_id}/content?variant=thumb")
    assert second.status_code == 200
    assert second.content == first.content
    assert make_calls == ["thumb"]

    preview = client.get(f"/api/assets/{asset_id}/content?variant=preview")
    assert preview.status_code == 200
    assert preview.headers["etag"] == f'"{sha}-preview-d2"'
    assert (data_dir / f"derived/{sha}/preview.v2.webp").is_file()
    assert make_calls == ["thumb", "preview"]

    # 古い版の ETag では 304 にならない(新しい中身を返す)。
    stale = client.get(
        f"/api/assets/{asset_id}/content?variant=thumb",
        headers={"If-None-Match": f'"{sha}-thumb-d1"'},
    )
    assert stale.status_code == 200


def test_missing_derivative_is_regenerated(client: TestClient, data_dir: Path) -> None:
    uploaded = _upload(client)
    asset_id = uploaded["id"]
    asset = _asset(client, asset_id)
    thumb = data_dir / f"derived/{asset.sha256}/thumb.webp"
    expected = thumb.read_bytes()
    wait_for_background_reads(client)
    thumb.unlink()

    # 304 の確かめ: 派生が消えていても、原本があれば同じ版を作り直せるので 304。
    cached = client.get(
        f"/api/assets/{asset_id}/content?variant=thumb",
        headers={"If-None-Match": f'"{asset.sha256}-thumb-d1"'},
    )
    assert cached.status_code == 304
    assert not thumb.exists()

    response = client.get(f"/api/assets/{asset_id}/content?variant=thumb")
    assert response.status_code == 200
    assert response.content == expected
    assert thumb.read_bytes() == expected


def test_missing_original_and_derivative_is_404(client: TestClient, data_dir: Path) -> None:
    uploaded = _upload(client)
    asset_id = uploaded["id"]
    asset = _asset(client, asset_id)
    wait_for_background_reads(client)
    (data_dir / f"derived/{asset.sha256}/thumb.webp").unlink()
    (data_dir / asset.blob_key).unlink()

    missing = client.get(f"/api/assets/{asset_id}/content?variant=thumb")
    assert missing.status_code == 404
    assert (
        missing.json()["detail"]
        == client.get(f"/api/assets/{asset_id}/content?variant=original").json()["detail"]
    )
    cached = client.get(
        f"/api/assets/{asset_id}/content?variant=thumb",
        headers={"If-None-Match": f'"{asset.sha256}-thumb-d1"'},
    )
    assert cached.status_code == 404
    # preview は残っているので、原本が無くても配信できる。
    assert client.get(f"/api/assets/{asset_id}/content?variant=preview").status_code == 200


def test_unreadable_original_is_404(client: TestClient, data_dir: Path) -> None:
    uploaded = _upload(client)
    asset = _asset(client, uploaded["id"])
    wait_for_background_reads(client)
    (data_dir / f"derived/{asset.sha256}/preview.webp").unlink()
    (data_dir / asset.blob_key).write_bytes(b"not an image")
    response = client.get(f"/api/assets/{uploaded['id']}/content?variant=preview")
    assert response.status_code == 404


def test_ensure_derived_preserves_alpha(tmp_path: Path) -> None:
    """透過のある原本から作り直しても、取り込みと同じく透過を保つ。"""
    store = LocalFsStore(tmp_path / "store")
    image = Image.new("RGBA", (40, 20), (10, 20, 30, 0))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    key = "assets/uploads/2026-10/a.png"
    (store.root / "assets/uploads/2026-10").mkdir(parents=True)
    (store.root / key).write_bytes(buffer.getvalue())

    content = derivatives.ensure_derived(store, key, "12" * 32, "thumb")
    assert content is not None
    data = content.read_all()
    assert data == derivatives.make_thumb(image)
    with Image.open(io.BytesIO(data)) as thumb:
        assert thumb.mode == "RGBA"
        assert thumb.getpixel((0, 0))[3] == 0
    assert (store.root / derived_key("12" * 32, "thumb")).read_bytes() == data
    assert derivatives.ensure_derived(store, "assets/missing.png", "34" * 32, "thumb") is None


# -- 共有リンク・MCP・埋め込み・自動タグ ------------------------------------------


def test_public_share_regenerates_missing_derivative(
    client: TestClient, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert client.patch("/api/settings/share", json={"enabled": True}).status_code == 200
    uploaded = _upload(client)
    asset_id = uploaded["id"]
    sha = _asset(client, asset_id).sha256
    created = client.post("/api/shares", json={"asset_id": asset_id, "scope": "single"})
    assert created.status_code == 201, created.text
    token = created.json()["url"].rsplit("/s/", 1)[1]

    _bump_version(monkeypatch)
    client.cookies.clear()
    response = client.get(f"/api/public/shares/{token}/assets/{asset_id}/content?variant=thumb")
    assert response.status_code == 200, response.text
    assert response.headers["etag"] == f'"{sha}-thumb-d2"'
    assert (data_dir / f"derived/{sha}/thumb.v2.webp").read_bytes() == response.content


def test_agent_images_regenerate_missing_derivative(
    client: TestClient, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    uploaded = _upload(client)
    asset = _asset(client, uploaded["id"])
    store = client.app.state.store
    _bump_version(monkeypatch)

    image = agent_images.render_asset(store, asset, "small")
    assert image is not None
    assert (image.width, image.height) == (512, 256)
    assert (data_dir / f"derived/{asset.sha256}/thumb.v2.webp").is_file()

    # 原本も派生も無ければ None(MCP は画像を付けない)。
    wait_for_background_reads(client)
    (data_dir / asset.blob_key).unlink()
    (data_dir / f"derived/{asset.sha256}/thumb.v2.webp").unlink()
    agent_images.clear_cache()
    assert agent_images.render_asset(store, asset, "small") is None


def _wait(predicate: Any, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise TimeoutError


def test_embedder_regenerates_missing_thumb(
    client: TestClient, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    uploaded = _upload(client)
    sha = _asset(client, uploaded["id"]).sha256
    _bump_version(monkeypatch)
    response = client.patch("/api/settings/embeddings", json={"enabled": True})
    assert response.status_code == 200, response.text
    assert client.post("/api/settings/embeddings/backfill").json() == {"queued": 1}

    def done() -> bool:
        body = client.get("/api/settings/embeddings").json()
        return body["pending_count"] == 0 and sum(s["count"] for s in body["stored"]) == 1

    _wait(done)
    assert client.get("/api/settings/embeddings").json()["failed_count"] == 0
    assert (data_dir / f"derived/{sha}/thumb.v2.webp").is_file()


def test_annotator_regenerates_missing_preview(
    client: TestClient, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    uploaded = _upload(client)
    asset_id = uploaded["id"]
    sha = _asset(client, asset_id).sha256
    _bump_version(monkeypatch)
    response = client.patch(
        "/api/settings/annotation", json={"vlm_enabled": True, "auto_on_ingest": False}
    )
    assert response.status_code == 200, response.text
    assert client.post(f"/api/assets/{asset_id}/annotate").status_code in (200, 202)

    def done() -> bool:
        annotation = client.get(f"/api/assets/{asset_id}").json().get("annotation")
        return bool(annotation) and annotation["status"] in ("succeeded", "failed")

    _wait(done)
    body = client.get(f"/api/assets/{asset_id}").json()
    assert body["annotation"]["status"] == "succeeded", body["annotation"]
    assert (data_dir / f"derived/{sha}/preview.v2.webp").is_file()
