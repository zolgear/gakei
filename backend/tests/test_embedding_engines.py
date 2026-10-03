"""埋め込みのエンジン(ADR-0033 2章)。ONNX のセッションは差し替え、実際のモデルは使わない。"""

from __future__ import annotations

import base64
import io
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import numpy as np
import pytest
from PIL import Image

from app.embedding import catalog
from app.embedding.base import EmbeddingError, blob_to_vector, l2_normalize, vector_to_blob
from app.embedding.fake import FakeEmbeddingEngine
from app.embedding.onnx_engine import OnnxClipEngine
from app.embedding.remote import InfinityEngine, remote_model_key
from app.model_store.residency import ModelResidency

_FIXTURES = Path(__file__).parent / "fixtures"


def _write_model_files(data_dir: Path, name: str) -> Path:
    directory = catalog.model_dir(data_dir, name)
    directory.mkdir(parents=True)
    for remote in catalog.CLIP_MODELS[name].files:
        (directory / remote.name).write_bytes(b"dummy")
    if catalog.CLIP_MODELS[name].family == "openai_clip":
        for filename in ("vocab.json", "merges.txt"):
            (directory / filename).write_bytes((_FIXTURES / "clip_bpe" / filename).read_bytes())
    return directory


class _FakeSession:
    """入力の形を記録し、行ごとに入力の和で決まる(正規化していない)ベクトルを返す。"""

    def __init__(self, path: str, sess_options: Any, providers: list[str], created: list) -> None:
        self.path = path
        self.sess_options = sess_options
        self.providers = providers
        self.calls: list[tuple[list[str] | None, dict[str, np.ndarray]]] = []
        created.append(self)

    def run(self, output_names: Any, feeds: dict[str, np.ndarray]) -> list[np.ndarray]:
        self.calls.append((output_names, feeds))
        first = next(iter(feeds.values()))
        batch = first.shape[0]
        flat = first.reshape(batch, -1).astype(np.float64)
        rows = np.stack([np.full(512, 3.0) + np.arange(512) * (row.sum() % 7) for row in flat])
        return [rows.astype(np.float32)]


@pytest.fixture
def fake_sessions(monkeypatch: pytest.MonkeyPatch) -> list[_FakeSession]:
    import onnxruntime

    created: list[_FakeSession] = []

    def factory(path: str, sess_options: Any, providers: list[str]) -> _FakeSession:
        return _FakeSession(path, sess_options, providers, created)

    monkeypatch.setattr(onnxruntime, "InferenceSession", factory)
    return created


def _engine(tmp_path: Path, name: str, **kwargs: Any) -> OnnxClipEngine:
    kwargs.setdefault("memory_probe", lambda: None)
    kwargs.setdefault("residency", ModelResidency())
    return OnnxClipEngine(tmp_path, catalog.CLIP_MODELS[name], **kwargs)


def test_clip_u8_engine_uses_input_names_batch_one_and_normalizes(
    tmp_path: Path, fake_sessions: list[_FakeSession]
) -> None:
    _write_model_files(tmp_path, "clip-vit-b32-u8")
    engine = _engine(tmp_path, "clip-vit-b32-u8")
    assert engine.model_key == f"onnx:clip-vit-b32-u8@{catalog.XENOVA_CLIP_REVISION}"
    assert engine.image_batch_size == 1

    images = [Image.new("RGB", (64, 32), (i * 40, 10, 10)) for i in range(3)]
    vectors = engine.embed_images(images)
    assert vectors.shape == (3, 512)
    assert vectors.dtype == np.float32
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-5)
    # 文章側はまだ読み込まない(画像だけを計算するとき)。
    assert engine.loaded_parts == ("vision",)
    vision = fake_sessions[0]
    assert vision.path.endswith("vision_model_uint8.onnx")
    # 量子化したモデルは1枚ずつ計算する。
    assert len(vision.calls) == 3
    for output_names, feeds in vision.calls:
        assert output_names == ["image_embeds"]
        assert list(feeds) == ["pixel_values"]
        assert feeds["pixel_values"].shape == (1, 3, 224, 224)
    # セッションの作り方は WD Tagger と同じ。
    assert vision.sess_options.enable_mem_pattern is False
    assert vision.sess_options.get_session_config_entry("session.disable_prepacking") == "1"
    assert vision.providers == ["CPUExecutionProvider"]


def test_clip_fp32_engine_batches_images(tmp_path: Path, fake_sessions: list[_FakeSession]) -> None:
    _write_model_files(tmp_path, "clip-vit-b32")
    engine = _engine(tmp_path, "clip-vit-b32")
    assert engine.image_batch_size == 8
    engine.embed_images([Image.new("RGB", (32, 32))] * 10)
    shapes = [feeds["pixel_values"].shape[0] for _, feeds in fake_sessions[0].calls]
    assert shapes == [8, 2]


def test_clip_text_only_loads_text_session(
    tmp_path: Path, fake_sessions: list[_FakeSession]
) -> None:
    _write_model_files(tmp_path, "clip-vit-b32-u8")
    engine = _engine(tmp_path, "clip-vit-b32-u8")
    vectors = engine.embed_texts(["a photo of a cat", "夕焼け"])
    assert vectors.shape == (2, 512)
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-5)
    assert engine.loaded_parts == ("text",)
    text = fake_sessions[0]
    assert text.path.endswith("text_model.onnx")
    output_names, feeds = text.calls[0]
    assert output_names == ["text_embeds"]
    assert list(feeds) == ["input_ids"]
    assert feeds["input_ids"].shape == (2, 77)


def test_ly_engine_io_names_and_batch_one(
    tmp_path: Path, fake_sessions: list[_FakeSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.embedding import onnx_engine

    _write_model_files(tmp_path, "clip-japanese-base")

    class _Sp:
        def encode(self, text: str) -> list[int]:
            return [10 + len(text)]

        def piece_to_id(self, piece: str) -> int:
            return {"[CLS]": 4, "[PAD]": 3}[piece]

    real_ly = onnx_engine.LyTokenizer
    monkeypatch.setattr(onnx_engine, "LyTokenizer", lambda path: real_ly(processor=_Sp()))

    engine = _engine(tmp_path, "clip-japanese-base")
    assert engine.image_batch_size == 1
    engine.embed_images([Image.new("RGBA", (40, 20))] * 2)
    vision = fake_sessions[0]
    assert vision.path.endswith("clyp_visual.onnx")
    assert [list(feeds) for _, feeds in vision.calls] == [["input"], ["input"]]
    assert vision.calls[0][0] == ["output"]

    engine.embed_texts(["猫", "犬の写真"])
    text = fake_sessions[1]
    assert text.path.endswith("clyp_textual.onnx")
    output_names, feeds = text.calls[0]
    assert output_names == ["output"]
    assert set(feeds) == {"input0", "input1", "input2"}
    assert feeds["input0"][:, 0].tolist() == [4, 4]


def test_missing_model_files_raise_embedding_error(tmp_path: Path) -> None:
    engine = _engine(tmp_path, "clip-vit-b32-u8")
    with pytest.raises(EmbeddingError, match="clip-vit-b32-u8"):
        engine.embed_images([Image.new("RGB", (8, 8))])


def test_insufficient_memory_is_checked_per_part(
    tmp_path: Path, fake_sessions: list[_FakeSession]
) -> None:
    _write_model_files(tmp_path, "clip-japanese-base")
    model = catalog.CLIP_MODELS["clip-japanese-base"]
    # 画像側の目安ちょうどの空き → 画像側は読める。
    free = {"bytes": model.memory_vision_bytes}
    engine = _engine(tmp_path, "clip-japanese-base", memory_probe=lambda: free["bytes"])
    engine.embed_images([Image.new("RGB", (8, 8))])
    # 文章側を足すときは「両方の目安 − 画像側の目安」と比べる。
    free["bytes"] = model.memory_bytes - model.memory_vision_bytes - 1
    with pytest.raises(EmbeddingError, match="clip-japanese-base"):
        engine._ensure_text()
    assert len(fake_sessions) == 1


def test_release_idle_and_close(tmp_path: Path, fake_sessions: list[_FakeSession]) -> None:
    _write_model_files(tmp_path, "clip-vit-b32-u8")
    residency = ModelResidency()
    engine = _engine(tmp_path, "clip-vit-b32-u8", residency=residency)
    engine.embed_images([Image.new("RGB", (8, 8))])
    assert residency.holder == "embedding"
    assert engine.release_idle(idle_seconds=3600) is False
    assert engine.release_idle(idle_seconds=0) is True
    assert engine.loaded_parts == ()
    assert residency.holder is None


# -- WD Tagger と同時に載せない ------------------------------------------------------


def test_residency_evicts_idle_holder_and_waits_for_busy_one() -> None:
    clock = {"now": 100.0}
    residency = ModelResidency(grace_seconds=2.0, max_hold_seconds=30.0, clock=lambda: clock["now"])
    dropped: list[str] = []
    residency.register("wd", lambda: dropped.append("wd"))
    residency.register("embedding", lambda: dropped.append("embedding"))

    with residency.use("wd"):
        pass
    assert residency.holder == "wd"
    # 使い終わって grace を過ぎていれば、待たずに手放させる。
    clock["now"] += 5
    with residency.use("embedding"):
        assert residency.holder == "embedding"
    assert dropped == ["wd"]

    # 推論の最中(busy)は手放させない。終わってから入れ替わる。
    order: list[str] = []
    entered = threading.Event()
    release = threading.Event()

    def hold() -> None:
        with residency.use("embedding"):
            entered.set()
            release.wait(5)
            order.append("embedding-done")

    thread = threading.Thread(target=hold)
    thread.start()
    entered.wait(5)
    clock["now"] += 100

    def other() -> None:
        with residency.use("wd"):
            order.append("wd")

    waiter = threading.Thread(target=other)
    waiter.start()
    time.sleep(0.3)
    assert order == []
    release.set()
    thread.join(5)
    waiter.join(5)
    assert order == ["embedding-done", "wd"]
    assert dropped == ["wd", "embedding"]


def test_residency_keeps_recent_holder_until_max_hold() -> None:
    clock = {"now": 0.0}
    residency = ModelResidency(grace_seconds=2.0, max_hold_seconds=30.0, clock=lambda: clock["now"])
    residency.register("wd", lambda: None)
    residency.register("embedding", lambda: None)
    with residency.use("wd"):
        pass
    assert not residency._can_evict_locked(1.0)
    # 使われ続けていても、max_hold を超えたら(推論の合間に)手放させる。
    residency._last_used = 31.0
    assert residency._can_evict_locked(31.5)


def test_wd_tagger_and_clip_engine_are_not_resident_together(
    tmp_path: Path, fake_sessions: list[_FakeSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.annotation import wd_models
    from app.annotation.wd_tagger import WdTagger

    wd_dir = wd_models.model_dir(tmp_path, "wd-vit-tagger-v3")
    wd_dir.mkdir(parents=True)
    (wd_dir / "model.onnx").write_bytes(b"dummy")
    (wd_dir / "selected_tags.csv").write_text(
        "tag_id,name,category,count\n1,a,0,1\n", encoding="utf-8"
    )
    _write_model_files(tmp_path, "clip-vit-b32-u8")

    class _WdSession(_FakeSession):
        def get_inputs(self) -> list[Any]:
            return [SimpleNamespace(name="input_1", shape=["batch", 16, 16, 3])]

        def run(self, output_names: Any, feeds: dict[str, np.ndarray]) -> list[np.ndarray]:
            return [np.array([[0.9]], dtype=np.float32)]

    import onnxruntime

    created: list[Any] = []

    def factory(path: str, sess_options: Any, providers: list[str]) -> Any:
        cls = _WdSession if path.endswith("model.onnx") else _FakeSession
        return cls(path, sess_options, providers, created)

    monkeypatch.setattr(onnxruntime, "InferenceSession", factory)
    residency = ModelResidency(grace_seconds=0.0)
    tagger = WdTagger(tmp_path, memory_probe=lambda: None, residency=residency)
    engine = _engine(tmp_path, "clip-vit-b32-u8", residency=residency)

    tagger.tag(Image.new("RGB", (8, 8)), "wd-vit-tagger-v3", 0.5)
    assert tagger.loaded
    engine.embed_images([Image.new("RGB", (8, 8))])
    # 埋め込みを読み込む前に WD Tagger を手放した。
    assert not tagger.loaded
    assert engine.loaded_parts == ("vision",)
    tagger.tag(Image.new("RGB", (8, 8)), "wd-vit-tagger-v3", 0.5)
    assert engine.loaded_parts == ()
    assert tagger.loaded


# -- リモート(Infinity) --------------------------------------------------------------


def _infinity_handler(requests: list[httpx.Request], dim: int = 4, status: int = 200):  # noqa: ANN202
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if status != 200:
            return httpx.Response(status, text="model not loaded\nsecond line")
        body = json.loads(request.content)
        data = [
            {"index": i, "embedding": [float(i + 1)] * dim, "object": "embedding"}
            for i in range(len(body["input"]))
        ]
        # 順不同で返しても index で並べ直す。
        return httpx.Response(200, json={"object": "list", "data": list(reversed(data))})

    return handler


def _infinity(requests: list[httpx.Request], api_key: str | None = "sk-local", **kw: Any):  # noqa: ANN202
    handler = _infinity_handler(requests, **kw)
    return InfinityEngine(
        connection_id="abc123",
        base_url="http://infer.local:7997/",
        model="jinaai/jina-clip-v2",
        api_key=api_key,
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_infinity_image_request_shape_and_auth() -> None:
    requests: list[httpx.Request] = []
    engine = _infinity(requests)
    assert engine.model_key == remote_model_key("abc123", "jinaai/jina-clip-v2")
    assert engine.model_key == "remote:abc123:jinaai/jina-clip-v2"
    assert engine.dim is None
    images = [Image.new("RGBA", (1024, 600), (0, 0, 0, 0))] * 10
    vectors = engine.embed_images(images)
    assert vectors.shape == (10, 4)
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0)
    assert engine.dim == 4
    # 1回に送る件数の上限(8)で分ける。
    assert [len(json.loads(r.content)["input"]) for r in requests] == [8, 2]
    request = requests[0]
    assert str(request.url) == "http://infer.local:7997/embeddings"
    assert request.headers["authorization"] == "Bearer sk-local"
    body = json.loads(request.content)
    assert body["model"] == "jinaai/jina-clip-v2"
    assert body["modality"] == "image"
    uri = body["input"][0]
    assert uri.startswith("data:image/jpeg;base64,")
    sent = Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1])))
    # thumb の大きさ(512px)以内の JPEG。透明な部分は白。
    assert sent.format == "JPEG" and max(sent.size) <= 512
    assert sent.convert("RGB").getpixel((10, 10)) == pytest.approx((255, 255, 255), abs=3)


def test_infinity_text_request_has_no_modality_and_no_key() -> None:
    requests: list[httpx.Request] = []
    engine = _infinity(requests, api_key=None)
    engine.embed_texts(["夕焼けの海"])
    body = json.loads(requests[0].content)
    assert "modality" not in body
    assert body["input"] == ["夕焼けの海"]
    assert "authorization" not in requests[0].headers


def test_infinity_errors_become_embedding_errors_without_key() -> None:
    requests: list[httpx.Request] = []
    engine = _infinity(requests, status=503)
    with pytest.raises(EmbeddingError) as excinfo:
        engine.embed_images([Image.new("RGB", (8, 8))])
    message = str(excinfo.value)
    assert "503" in message and "model not loaded" in message
    assert "sk-local" not in message

    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    engine = InfinityEngine(
        connection_id="abc",
        base_url="http://x",
        model="m",
        api_key="sk-secret",
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(broken)),
    )
    with pytest.raises(EmbeddingError) as excinfo:
        engine.embed_texts(["a"])
    assert "sk-secret" not in str(excinfo.value)

    def bad_json(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0}]})

    engine = InfinityEngine(
        connection_id="abc",
        base_url="http://x",
        model="m",
        api_key=None,
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(bad_json)),
    )
    with pytest.raises(EmbeddingError):
        engine.embed_texts(["a"])


# -- ダミー ------------------------------------------------------------------------


def test_fake_engine_similar_images_are_similar() -> None:
    engine = FakeEmbeddingEngine("fake:x")
    base = Image.new("RGB", (64, 64), (200, 30, 30))
    near = Image.new("RGB", (64, 64), (195, 35, 30))
    far = Image.new("RGB", (64, 64), (20, 60, 220))
    vectors = engine.embed_images([base, near, far])
    assert vectors.shape == (3, 512)
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-5)
    assert float(vectors[0] @ vectors[1]) > 0.98
    assert float(vectors[0] @ vectors[2]) < 0.5
    texts = engine.embed_texts(["cat", "cat", "dog"])
    assert np.allclose(texts[0], texts[1])
    assert float(texts[0] @ texts[2]) < 0.5


def test_vector_blob_roundtrip_and_normalize() -> None:
    vector = l2_normalize(np.array([3.0, 4.0]))[0]
    assert vector.tolist() == pytest.approx([0.6, 0.8])
    blob = vector_to_blob(vector)
    assert len(blob) == 8
    assert blob_to_vector(blob).tolist() == pytest.approx([0.6, 0.8])
    # 長さ 0 のベクトルは 0 のまま(0 で割らない)。
    assert l2_normalize(np.zeros((1, 3))).tolist() == [[0.0, 0.0, 0.0]]
