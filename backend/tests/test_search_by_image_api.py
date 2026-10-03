"""画像で探す(`POST /api/search/similar-image`。ADR-0033 6章・7章、2026-10-04 追記)。

手元の画像を Asset にせず、その場で1件だけベクトルを計算して近い画像を返す。画像もベクトルも
保存しない(DB の行もファイルも作らない)ことを確かめる。FAKE のエンジンは、似た画像が似た
ベクトルになる。
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select

from app.domain.models import Asset, AssetEmbedding
from app.embedding.base import EmbeddingError
from app.embedding.fake import FakeEmbeddingEngine
from tests.conftest import login_as
from tests.test_semantic_search_api import (
    ACTIVE_KEY,
    _enable,
    _png,
    _three_images,
    _upload,
    _wait_embedded,
)

PATH = "/api/search/similar-image"


def _search(client: TestClient, data: bytes, name: str = "q.png", **params: object):  # noqa: ANN202
    return client.post(PATH, files={"file": (name, data, "image/png")}, params=params)


def _counts(client: TestClient) -> tuple[int, int]:
    with client.app.state.session_factory() as db:
        assets = db.execute(select(func.count()).select_from(Asset)).scalar_one()
        vectors = db.execute(select(func.count()).select_from(AssetEmbedding)).scalar_one()
    return assets, vectors


def _files(data_dir: Path) -> set[Path]:
    return {p for p in data_dir.rglob("*") if p.is_file() and p.suffix not in {".db", ".db-wal"}}


def test_disabled_returns_409(client: TestClient) -> None:
    response = _search(client, _png((1, 2, 3)))
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "embeddings_unavailable"
    assert client.get("/api/capabilities").json()["embeddings"]["engine"] is None
    _enable(client)
    # 画面が「推論サーバーに送る」と書くかどうかに使う。
    assert client.get("/api/capabilities").json()["embeddings"]["engine"] == "onnx"


def test_identical_image_ranks_first_and_nothing_is_stored(
    client: TestClient, data_dir: Path
) -> None:
    _enable(client)
    red, red2, other = _three_images(client)
    before_counts = _counts(client)
    before_files = _files(data_dir)

    response = _search(client, _png((220, 30, 30)))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["model_key"] == ACTIVE_KEY
    ids = [a["id"] for a in body["assets"]]
    assert ids == [red, red2, other]
    assert body["assets"][0]["score"] == pytest.approx(1.0, abs=1e-4)
    assert {"kind", "width", "height", "created_at", "title", "mime", "score"} <= set(
        body["assets"][0]
    )

    # 画像も、そのベクトルも保存しない(Asset の行、埋め込みの行、ファイルのどれも増えない)。
    assert _counts(client) == before_counts
    assert _files(data_dir) == before_files

    # 利用者が待っている1件なので、優先の区間で推論する。
    engine = client.app.state.embedder.engines._fakes[ACTIVE_KEY]
    assert engine.priority_image_calls == 1

    # 絞り込み(limit と種類)。
    limited = _search(client, _png((220, 30, 30)), limit=1).json()
    assert [a["id"] for a in limited["assets"]] == [red]
    generated = _search(client, _png((220, 30, 30)), kind="generated").json()
    assert generated["assets"] == []


def test_repeatable_tags_are_and(client: TestClient) -> None:
    _enable(client)
    red, red2, _other = _three_images(client)
    for asset_id, names in ((red, ["warm", "square"]), (red2, ["warm"])):
        for name in names:
            response = client.post(f"/api/assets/{asset_id}/tags", json={"name": name})
            assert response.status_code in (200, 201), response.text
    response = client.post(
        PATH,
        files={"file": ("q.png", _png((220, 30, 30)), "image/png")},
        params=[("tag", "warm"), ("tag", "square")],
    )
    assert [a["id"] for a in response.json()["assets"]] == [red]


def test_transparent_parts_are_composited_on_white(client: TestClient) -> None:
    _enable(client)
    white = _upload(client, _png((255, 255, 255)))
    black = _upload(client, _png((0, 0, 0)))
    _wait_embedded(client, white, black)

    transparent = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    buffer = io.BytesIO()
    transparent.save(buffer, format="PNG")
    body = _search(client, buffer.getvalue()).json()
    assert [a["id"] for a in body["assets"]] == [white, black]
    assert body["assets"][0]["score"] == pytest.approx(1.0, abs=1e-4)


def test_jpeg_and_webp_are_accepted(client: TestClient) -> None:
    _enable(client)
    red = _upload(client, _png((220, 30, 30)))
    _wait_embedded(client, red)
    for fmt in ("JPEG", "WEBP"):
        buffer = io.BytesIO()
        Image.new("RGB", (64, 64), (220, 30, 30)).save(buffer, format=fmt)
        response = _search(client, buffer.getvalue(), name=f"q.{fmt.lower()}")
        assert response.status_code == 200, response.text
        assert response.json()["assets"][0]["id"] == red


def test_bad_uploads(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(client)
    # 画像でない、空、対応していない形式は 422。
    assert _search(client, b"not an image").status_code == 422
    assert _search(client, b"").status_code == 422
    gif = io.BytesIO()
    Image.new("RGB", (8, 8)).save(gif, format="GIF")
    response = _search(client, gif.getvalue(), name="q.gif")
    assert response.status_code == 422
    assert "GIF" in response.json()["detail"]
    # 壊れた PNG(ヘッダーは読めるが展開できない)も 422。
    broken = _png((1, 2, 3))
    assert _search(client, broken[: len(broken) // 2]).status_code == 422
    # ファイルが無い。
    assert client.post(PATH).status_code == 422

    # バイト数の上限(取り込みと同じ `MAX_UPLOAD_BYTES`)を超えると 413。
    from app.api import embeddings as api_embeddings
    from app.domain import semantic_search

    monkeypatch.setattr(api_embeddings, "MAX_UPLOAD_BYTES", 100)
    monkeypatch.setattr(semantic_search, "MAX_UPLOAD_BYTES", 100)
    response = _search(client, _png((1, 2, 3)) + b"\0" * 200)
    assert response.status_code == 413
    monkeypatch.undo()

    # 画素数の上限を超えると、展開する前に 413。
    _enable(client)
    monkeypatch.setattr(semantic_search, "QUERY_IMAGE_MAX_PIXELS", 64 * 64 - 1)
    response = _search(client, _png((1, 2, 3)))
    assert response.status_code == 413
    assert response.json()["detail"]


def test_engine_failure_is_503(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(client)

    def _fail(self: FakeEmbeddingEngine, images: list, *, priority: bool = False):  # noqa: ANN202
        raise EmbeddingError("メモリが足りません")

    monkeypatch.setattr(FakeEmbeddingEngine, "embed_images", _fail)
    response = _search(client, _png((1, 2, 3)))
    assert response.status_code == 503
    assert "メモリが足りません" in response.json()["detail"]


def test_oidc_other_users_assets_never_appear(client_oidc: TestClient) -> None:
    login_as(client_oidc, "admin@example.com")
    _enable(client_oidc)
    client_oidc.cookies.clear()

    login_as(client_oidc, "alice@example.com")
    alice_red = _upload(client_oidc, _png((220, 30, 30)))
    client_oidc.cookies.clear()

    login_as(client_oidc, "bob@example.com")
    bob_blue = _upload(client_oidc, _png((20, 40, 230)))
    _wait_embedded(client_oidc, alice_red, bob_blue)

    # アリスの画像と同じ画像で探しても、ボブにはボブの画像だけが出る。
    body = _search(client_oidc, _png((220, 30, 30))).json()
    assert [a["id"] for a in body["assets"]] == [bob_blue]

    client_oidc.cookies.clear()
    assert _search(client_oidc, _png((220, 30, 30))).status_code == 401
