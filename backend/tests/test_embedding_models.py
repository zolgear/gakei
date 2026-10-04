"""埋め込みのモデルの一覧とダウンロード(ADR-0033 2章・11章)。WD Tagger と同じダウンローダーを
使い、リポジトリの中の `onnx/` のパスから取って、ファイル名だけで置く。"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.embedding import catalog
from app.embedding.catalog import ClipModel, ClipModelDownloader
from app.model_store.downloader import RemoteFile

pytestmark = pytest.mark.windows


def test_pinned_catalog_is_complete() -> None:
    assert set(catalog.CLIP_MODELS) == {"clip-vit-b32-u8", "clip-vit-b32", "clip-japanese-base"}
    assert catalog.DEFAULT_MODEL == "clip-vit-b32-u8"
    for model in catalog.CLIP_MODELS.values():
        assert len(model.revision) == 40
        assert model.dim == 512
        assert all(len(f.sha256) == 64 and f.size > 0 for f in model.files)
        names = {f.name for f in model.files}
        assert {model.vision_file, model.text_file} <= names
        assert model.memory_vision_bytes < model.memory_bytes
        assert model.memory_text_bytes < model.memory_bytes
    u8 = catalog.CLIP_MODELS["clip-vit-b32-u8"]
    assert {f.name for f in u8.files} == {
        "vision_model_uint8.onnx",
        "text_model.onnx",
        "vocab.json",
        "merges.txt",
    }
    assert round(u8.size_bytes / 1e6) == 344
    assert round(catalog.CLIP_MODELS["clip-vit-b32"].size_bytes / 1e6) == 607
    assert round(catalog.CLIP_MODELS["clip-japanese-base"].size_bytes / 1e6) == 791
    assert catalog.CLIP_MODELS["clip-japanese-base"].languages == ("ja", "en")
    assert u8.languages == ("en",)
    # 1枚ずつ計算するもの(量子化した画像側と LY の画像側)。
    assert u8.image_batch_size == 1
    assert catalog.CLIP_MODELS["clip-japanese-base"].image_batch_size == 1
    assert catalog.CLIP_MODELS["clip-vit-b32"].image_batch_size == 8


def _small_model(
    content: dict[str, bytes], sha_override: dict[str, str] | None = None
) -> ClipModel:
    files = tuple(
        RemoteFile(
            name,
            len(data),
            (sha_override or {}).get(name, hashlib.sha256(data).hexdigest()),
            path=f"onnx/{name}" if name.endswith(".onnx") else None,
        )
        for name, data in content.items()
    )
    base = catalog.CLIP_MODELS["clip-vit-b32-u8"]
    return ClipModel(
        name=base.name,
        family=base.family,
        repo=base.repo,
        revision="abc123",
        files=files,
        vision_file="vision_model_uint8.onnx",
        text_file="text_model.onnx",
        languages=base.languages,
        dim=512,
        memory_vision_bytes=1,
        memory_text_bytes=1,
        memory_bytes=2,
        license="MIT",
    )


def _run_download(
    tmp_path: Path, model: ClipModel, served: dict[str, bytes], monkeypatch: pytest.MonkeyPatch
) -> tuple[ClipModelDownloader, list[str]]:
    monkeypatch.setitem(catalog.CLIP_MODELS, "clip-vit-b32-u8", model)
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        filename = request.url.path.rsplit("/", 1)[-1]
        if filename not in served:
            return httpx.Response(404)
        return httpx.Response(200, content=served[filename])

    downloader = ClipModelDownloader(
        tmp_path, client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )

    async def go() -> None:
        downloader.start("clip-vit-b32-u8")
        await downloader.wait("clip-vit-b32-u8")

    asyncio.run(go())
    return downloader, requested


_CONTENT = {
    "vision_model_uint8.onnx": b"vision",
    "text_model.onnx": b"text",
    "vocab.json": b"{}",
    "merges.txt": b"#version\n",
}


def test_download_places_files_by_name_from_onnx_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    downloader, requested = _run_download(tmp_path, _small_model(_CONTENT), _CONTENT, monkeypatch)
    state: Any = downloader.state("clip-vit-b32-u8")
    assert (state.status, state.error) == ("idle", None)
    assert catalog.is_downloaded(tmp_path, "clip-vit-b32-u8")
    directory = tmp_path / "models" / "clip" / "clip-vit-b32-u8"
    assert (directory / "vision_model_uint8.onnx").read_bytes() == b"vision"
    assert requested[0] == (
        "https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/abc123/"
        "onnx/vision_model_uint8.onnx"
    )
    assert requested[2].endswith("/resolve/abc123/vocab.json")
    assert not list(directory.glob(".*.part"))


def test_download_fails_on_sha256_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    model = _small_model(_CONTENT, sha_override={"text_model.onnx": "0" * 64})
    downloader, _ = _run_download(tmp_path, model, _CONTENT, monkeypatch)
    state = downloader.state("clip-vit-b32-u8")
    assert state.status == "failed"
    assert "text_model.onnx" in (state.error or "")
    directory = tmp_path / "models" / "clip" / "clip-vit-b32-u8"
    assert not (directory / "text_model.onnx").exists()
    assert not list(directory.glob(".*.part"))
    assert not catalog.is_downloaded(tmp_path, "clip-vit-b32-u8")
