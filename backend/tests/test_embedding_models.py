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
    assert set(catalog.CLIP_MODELS) == {
        "clip-vit-b32-u8",
        "clip-vit-b32",
        "clip-japanese-base",
        "embeddinggemma-2-q8",
    }
    assert catalog.DEFAULT_MODEL == "clip-vit-b32-u8"
    for model in catalog.CLIP_MODELS.values():
        assert len(model.revision) == 40
        assert all(len(f.sha256) == 64 and f.size > 0 for f in model.files)
        assert all(f.revision is None or len(f.revision) == 40 for f in model.files)
        names = {f.name for f in model.files}
        assert len(names) == len(model.files)
        assert {model.vision_file, model.text_file} <= names
        assert model.memory_text_bytes < model.memory_bytes
        if model.vision_needs_text:
            # 画像の計算には文章側も読み込む(ADR-0044 3章)。
            assert model.memory_vision_bytes == model.memory_bytes
        else:
            assert model.dim == 512
            assert model.memory_vision_bytes < model.memory_bytes
        assert 0.5 <= model.duplicate_threshold <= 1.0
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


# -- EmbeddingGemma 2(ADR-0044) ---------------------------------------------------------


def test_embeddinggemma2_catalog() -> None:
    model = catalog.CLIP_MODELS["embeddinggemma-2-q8"]
    assert model.family == "embeddinggemma2"
    assert (model.repo, model.revision) == (
        "onnx-community/embeddinggemma-2-ONNX",
        "daa72c51243991dfcaf9f9137d2c573d8f7790c0",
    )
    files = {f.name: f for f in model.files}
    assert set(files) == {
        "vision_encoder_quantized.onnx",
        "vision_encoder_quantized.onnx_data",
        "model_quantized.onnx",
        "model_quantized.onnx_data",
        "tokenizer.model",
    }
    # `.onnx` と `.onnx_data` は同じディレクトリに同じ基底名で置く(onnxruntime が相対パスで探す)。
    for name in ("vision_encoder_quantized", "model_quantized"):
        assert files[f"{name}.onnx"].remote_path == f"onnx/{name}.onnx"
        assert files[f"{name}.onnx_data"].remote_path == f"onnx/{name}.onnx_data"
        assert files[f"{name}.onnx"].repo is None and files[f"{name}.onnx"].revision is None
    # `tokenizer.model` だけ google のリポジトリから、別のリビジョンで取る。
    tokenizer = files["tokenizer.model"]
    assert (tokenizer.repo, tokenizer.revision, tokenizer.remote_path) == (
        "google/embeddinggemma-2",
        "914f7f89142e33e77833254d9c9b90c3cef7303b",
        "tokenizer.model",
    )
    assert round(model.size_bytes / 2**20, 1) == 490.5
    assert (model.vision_file, model.text_file) == (
        "vision_encoder_quantized.onnx",
        "model_quantized.onnx",
    )
    assert model.dim == 768
    assert model.languages == ("ja", "en")
    assert model.license == "Apache-2.0"
    assert (model.memory_bytes, model.memory_vision_bytes, model.memory_text_bytes) == (
        750_000_000,
        750_000_000,
        550_000_000,
    )
    assert model.image_batch_size == 1
    assert model.vision_needs_text and model.heavy and model.many_languages
    assert model.input_variant == "preview"
    assert model.query_prefix == "task: search result | query: "
    assert model.max_text_tokens == 1024
    assert catalog.onnx_model_key(model) == (
        "onnx:embeddinggemma-2-q8@daa72c51243991dfcaf9f9137d2c573d8f7790c0"
    )
    # ほかのモデルは thumb から計算し、プレフィックスを付けない。
    for other in catalog.CLIP_MODELS.values():
        if other is not model:
            assert other.input_variant == "thumb" and other.query_prefix == ""
            assert not other.heavy and not other.vision_needs_text and not other.many_languages


def test_download_takes_files_from_their_own_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`RemoteFile` の `repo`・`revision` を指定したファイルは、そのリポジトリから取る。"""
    content = {"vision_model_uint8.onnx": b"vision", "text_model.onnx": b"text"}
    model = _small_model(content)
    tokenizer = RemoteFile(
        "tokenizer.model",
        3,
        hashlib.sha256(b"tok").hexdigest(),
        repo="google/other",
        revision="def456",
    )
    model = ClipModel(**{**model.__dict__, "files": (*model.files, tokenizer)})
    served = {**content, "tokenizer.model": b"tok"}
    downloader, requested = _run_download(tmp_path, model, served, monkeypatch)
    assert downloader.state("clip-vit-b32-u8").status == "idle"
    assert requested == [
        "https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/abc123/"
        "onnx/vision_model_uint8.onnx",
        "https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/abc123/onnx/text_model.onnx",
        "https://huggingface.co/google/other/resolve/def456/tokenizer.model",
    ]
    directory = tmp_path / "models" / "clip" / "clip-vit-b32-u8"
    assert (directory / "tokenizer.model").read_bytes() == b"tok"
