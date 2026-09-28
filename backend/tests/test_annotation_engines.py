"""推定エンジンの部品(ADR-0024 3章): WD Tagger の前処理・後処理、モデルのダウンロード、
VLM の応答の読み取り、OpenAI 互換 API の呼び分け。実ネットワークと実モデルは使わない。"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import numpy as np
import pytest
from PIL import Image

from app.annotation import wd_models, wd_tagger
from app.annotation.engines import (
    AnnotationEngineError,
    EngineContext,
    OpenAIEngines,
    clean_title,
    image_to_jpeg,
    parse_vlm_json,
)
from app.annotation.wd_models import RemoteFile, WdModel, WdModelDownloader
from app.annotation.wd_tagger import Label, WdTagger, load_labels, postprocess, preprocess
from app.domain.annotation_settings import AnnotationConfig, Connection

# -- WD Tagger の前処理・後処理 ------------------------------------------------------


def test_preprocess_pads_to_square_with_white_and_returns_bgr_nhwc() -> None:
    image = Image.new("RGB", (40, 20), (255, 0, 0))  # 赤、横長
    batch = preprocess(image, 40)
    assert batch.shape == (1, 40, 40, 3)
    assert batch.dtype == np.float32
    # 上下が白でパディングされる(中央寄せ)。
    assert batch[0, 0, 20].tolist() == [255.0, 255.0, 255.0]
    # 中央は赤。BGR なので [0, 0, 255]。
    assert batch[0, 20, 20].tolist() == [0.0, 0.0, 255.0]
    assert batch.max() <= 255.0


def test_preprocess_composites_alpha_on_white_and_resizes() -> None:
    image = Image.new("RGBA", (100, 100), (0, 0, 0, 0))  # 全面透明
    batch = preprocess(image, 32)
    assert batch.shape == (1, 32, 32, 3)
    assert np.all(batch == 255.0)


def test_postprocess_filters_categories_threshold_and_orders() -> None:
    labels = [
        Label("general", 9),
        Label("long_hair", 0),
        Label("smile", 0),
        Label("hatsune_miku", 4),
        Label("low_score", 0),
        Label("artist_x", 1),
    ]
    probs = np.array([0.99, 0.6, 0.8, 0.7, 0.1, 0.95], dtype=np.float32)
    assert postprocess(probs, labels, 0.35) == [
        ("smile", pytest.approx(0.8)),
        ("hatsune miku", pytest.approx(0.7)),
        ("long hair", pytest.approx(0.6)),
    ]


def test_postprocess_caps_at_20() -> None:
    labels = [Label(f"tag_{i}", 0) for i in range(30)]
    probs = np.linspace(0.5, 0.99, 30, dtype=np.float32)
    result = postprocess(probs, labels, 0.35)
    assert len(result) == 20
    assert result[0][0] == "tag 29"


def _write_model_dir(data_dir: Path, name: str) -> Path:
    directory = wd_models.model_dir(data_dir, name)
    directory.mkdir(parents=True)
    (directory / "model.onnx").write_bytes(b"dummy")
    (directory / "selected_tags.csv").write_text(
        "tag_id,name,category,count\n"
        "1,general,9,10\n"
        "2,1girl,0,10\n"
        '3,"tag,with_comma",0,10\n'
        "4,some_character,4,10\n",
        encoding="utf-8",
    )
    return directory


def test_load_labels_reads_csv(tmp_path: Path) -> None:
    directory = _write_model_dir(tmp_path, "wd-vit-tagger-v3")
    labels = load_labels(directory / "selected_tags.csv")
    assert [(label.name, label.category) for label in labels] == [
        ("general", 9),
        ("1girl", 0),
        ("tag,with_comma", 0),
        ("some_character", 4),
    ]


def test_wd_tagger_with_mocked_session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import onnxruntime

    _write_model_dir(tmp_path, "wd-vit-tagger-v3")
    created: list[Any] = []

    class FakeSession:
        def __init__(self, path: str, sess_options: Any, providers: list[str]) -> None:
            self.path = path
            self.sess_options = sess_options
            self.providers = providers
            self.inputs_seen: list[np.ndarray] = []
            created.append(self)

        def get_inputs(self) -> list[Any]:
            return [SimpleNamespace(name="input_1", shape=["batch", 16, 16, 3])]

        def run(self, output_names: Any, feeds: dict[str, np.ndarray]) -> list[np.ndarray]:
            self.inputs_seen.append(feeds["input_1"])
            return [np.array([[0.9, 0.8, 0.2, 0.5]], dtype=np.float32)]

    monkeypatch.setattr(onnxruntime, "InferenceSession", FakeSession)
    tagger = WdTagger(tmp_path)
    tags = tagger.tag(Image.new("RGB", (30, 30)), "wd-vit-tagger-v3", 0.35)
    assert tags == [("1girl", pytest.approx(0.8)), ("some character", pytest.approx(0.5))]
    assert created[0].path.endswith("model.onnx")
    assert created[0].providers == ["CPUExecutionProvider"]
    # 推論のスレッド数は CPU コア数の半分に抑える(API の応答を妨げないため)。
    assert created[0].sess_options.intra_op_num_threads == wd_tagger.inference_threads()
    assert created[0].sess_options.inter_op_num_threads == 1
    assert created[0].inputs_seen[0].shape == (1, 16, 16, 3)

    # 2回目は同じセッションを使う。
    tagger.tag(Image.new("RGB", (30, 30)), "wd-vit-tagger-v3", 0.35)
    assert len(created) == 1

    # しばらく使わなければ解放する。
    assert tagger.release_if_idle(idle_seconds=3600) is False
    assert tagger.release_if_idle(idle_seconds=0) is True
    assert tagger.loaded is False


def test_onnx_engine_reports_missing_model(tmp_path: Path) -> None:
    engines = OpenAIEngines(WdTagger(tmp_path))
    ctx = EngineContext(AnnotationConfig(onnx_enabled=True), Connection("k", None))
    with pytest.raises(AnnotationEngineError):
        engines.onnx_tags(Image.new("RGB", (8, 8)), ctx)


# -- モデルのダウンロード ------------------------------------------------------------


def _small_model(content: dict[str, bytes], sha_override: dict[str, str] | None = None) -> WdModel:
    files = tuple(
        RemoteFile(
            name,
            len(data),
            (sha_override or {}).get(name, hashlib.sha256(data).hexdigest()),
        )
        for name, data in content.items()
    )
    return WdModel(name="wd-vit-tagger-v3", revision="abc123", files=files)


def _run_download(tmp_path: Path, model: WdModel, served: dict[str, bytes], monkeypatch) -> Any:  # noqa: ANN001
    monkeypatch.setitem(wd_models.WD_MODELS, "wd-vit-tagger-v3", model)
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        filename = request.url.path.rsplit("/", 1)[-1]
        if filename not in served:
            return httpx.Response(404)
        return httpx.Response(200, content=served[filename])

    downloader = WdModelDownloader(
        tmp_path, client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )

    async def go() -> None:
        downloader.start("wd-vit-tagger-v3")
        await downloader.wait("wd-vit-tagger-v3")

    asyncio.run(go())
    return downloader, requested


def test_download_verifies_sha256_and_places_files(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    content = {"model.onnx": b"onnx-bytes", "selected_tags.csv": b"tag_id,name,category,count\n"}
    downloader, requested = _run_download(tmp_path, _small_model(content), content, monkeypatch)
    state = downloader.state("wd-vit-tagger-v3")
    assert (state.status, state.error) == ("idle", None)
    assert wd_models.is_downloaded(tmp_path, "wd-vit-tagger-v3")
    directory = wd_models.model_dir(tmp_path, "wd-vit-tagger-v3")
    assert (directory / "model.onnx").read_bytes() == b"onnx-bytes"
    assert requested[0] == (
        "https://huggingface.co/SmilingWolf/wd-vit-tagger-v3/resolve/abc123/model.onnx"
    )
    assert not list(directory.glob(".*.part"))


def test_download_fails_on_sha256_mismatch(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    content = {"model.onnx": b"onnx-bytes", "selected_tags.csv": b"csv"}
    model = _small_model(content, sha_override={"model.onnx": "0" * 64})
    downloader, _ = _run_download(tmp_path, model, content, monkeypatch)
    state = downloader.state("wd-vit-tagger-v3")
    assert state.status == "failed"
    assert "model.onnx" in (state.error or "")
    directory = wd_models.model_dir(tmp_path, "wd-vit-tagger-v3")
    assert not (directory / "model.onnx").exists()
    assert not list(directory.glob(".*.part"))
    assert not wd_models.is_downloaded(tmp_path, "wd-vit-tagger-v3")


def test_download_fails_on_http_error(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    content = {"model.onnx": b"onnx-bytes", "selected_tags.csv": b"csv"}
    downloader, _ = _run_download(
        tmp_path, _small_model(content), {"model.onnx": b"onnx-bytes"}, monkeypatch
    )
    state = downloader.state("wd-vit-tagger-v3")
    assert state.status == "failed"
    assert "404" in (state.error or "")


def test_pinned_catalog_is_complete() -> None:
    assert set(wd_models.WD_MODELS) == {
        "wd-vit-tagger-v3",
        "wd-swinv2-tagger-v3",
        "wd-eva02-large-tagger-v3",
    }
    for model in wd_models.WD_MODELS.values():
        assert len(model.revision) == 40
        assert {f.name for f in model.files} == {"model.onnx", "selected_tags.csv"}
        assert all(len(f.sha256) == 64 for f in model.files)


# -- VLM・LLM ----------------------------------------------------------------------


def test_parse_vlm_json_variants() -> None:
    assert parse_vlm_json('{"tags": ["cat", " dog ", 3, ""]}').tags == ["cat", "dog"]
    fenced = '```json\n{"title": "「夕焼け」", "tags": ["sky"]}\n```'
    result = parse_vlm_json(fenced)
    assert (result.title, result.tags) == ("夕焼け", ["sky"])
    assert len(parse_vlm_json(json.dumps({"tags": [str(i) for i in range(20)]})).tags) == 10
    for bad in ("", "no json here", '{"tags": "cat"}', "[1, 2]", "{broken"):
        with pytest.raises(AnnotationEngineError):
            parse_vlm_json(bad)


def test_clean_title() -> None:
    assert clean_title('"Sunset over the sea"\nexplanation') == "Sunset over the sea"
    assert clean_title("Title: 海辺の灯台") == "海辺の灯台"
    assert clean_title("   ") is None
    assert len(clean_title("あ" * 500) or "") == 100


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 実機で見つかった例(Issue #5): 末尾に強調の記号が残る。
        ("赤ずきんと魔女の少女の抱擁**", "赤ずきんと魔女の少女の抱擁"),
        ("**赤ずきんと魔女**", "赤ずきんと魔女"),
        ("__夜の港__", "夜の港"),
        ("*夜の港*", "夜の港"),
        ("_夜の港_", "夜の港"),
        ("# 夜の港", "夜の港"),
        ("## **夜の港**", "夜の港"),
        ("「夜の港」", "夜の港"),
        ("『夜の港』", "夜の港"),
        ("'Harbor at night'", "Harbor at night"),
        ('**"Harbor at night."**', "Harbor at night"),
        ("夜の港。", "夜の港"),
        ("タイトル: 「夜の港」。", "夜の港"),
        ("Title: **Harbor**", "Harbor"),
        ("snake_case の名前", "snake_case の名前"),
        ("Wait...", "Wait..."),
        ("**\n夜の港", "夜の港"),
    ],
)
def test_clean_title_strips_markdown_and_quotes(raw: str, expected: str) -> None:
    assert clean_title(raw) == expected


def test_inference_threads_is_half_of_cpus() -> None:
    assert wd_tagger.inference_threads(8) == 4
    assert wd_tagger.inference_threads(1) == 1
    assert wd_tagger.inference_threads(3) == 1


def test_image_to_jpeg_fits_within_1024() -> None:
    data = image_to_jpeg(Image.new("RGBA", (3000, 1500), (0, 0, 0, 0)))
    with Image.open(io.BytesIO(data)) as image:
        assert image.format == "JPEG"
        assert image.size == (1024, 512)


class _FakeClient:
    def __init__(self, text: str) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

        async def responses_create(**kwargs: Any) -> Any:
            self.calls.append(("responses", kwargs))
            return SimpleNamespace(output_text=text)

        async def chat_create(**kwargs: Any) -> Any:
            self.calls.append(("chat", kwargs))
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])

        self.responses = SimpleNamespace(create=responses_create)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=chat_create))


def _engines_with(client: _FakeClient, tmp_path: Path) -> OpenAIEngines:
    engines = OpenAIEngines(WdTagger(tmp_path))
    engines._client = client  # type: ignore[assignment]
    engines._client_key = ("k", None)
    return engines


def test_openai_engines_responses_api(tmp_path: Path) -> None:
    client = _FakeClient('{"title": "猫", "tags": ["cat"]}')
    engines = _engines_with(client, tmp_path)
    ctx = EngineContext(AnnotationConfig(vlm_model="vlm-x"), Connection("k", None))
    result = asyncio.run(engines.describe_image(b"\xff\xd8jpeg", None, True, ctx))
    assert (result.title, result.tags) == ("猫", ["cat"])
    style, kwargs = client.calls[0]
    assert style == "responses"
    assert kwargs["model"] == "vlm-x"
    parts = kwargs["input"][0]["content"]
    assert parts[1]["type"] == "input_image"
    assert parts[1]["image_url"].startswith("data:image/jpeg;base64,")


def test_openai_engines_chat_api_and_title(tmp_path: Path) -> None:
    client = _FakeClient("A quiet harbor")
    engines = _engines_with(client, tmp_path)
    ctx = EngineContext(
        AnnotationConfig(api_style="chat", llm_model="llm-x", language="en"), Connection("k", None)
    )
    assert asyncio.run(engines.title_from_prompt("harbor at dawn", ctx)) == "A quiet harbor"
    style, kwargs = client.calls[0]
    assert style == "chat"
    assert kwargs["model"] == "llm-x"
    assert kwargs["messages"][1]["content"] == "harbor at dawn"


def test_openai_engines_vlm_title_is_dropped_when_prompt_exists(tmp_path: Path) -> None:
    client = _FakeClient('{"title": "ignored", "tags": ["cat"]}')
    engines = _engines_with(client, tmp_path)
    ctx = EngineContext(AnnotationConfig(api_style="chat"), Connection("k", None))
    result = asyncio.run(engines.describe_image(b"jpeg", "a cat", False, ctx))
    assert result.title is None
    content = client.calls[0][1]["messages"][1]["content"]
    assert content[1]["type"] == "image_url"
    assert "a cat" in content[0]["text"]


def test_openai_engines_without_key_fail(tmp_path: Path) -> None:
    engines = OpenAIEngines(WdTagger(tmp_path))
    ctx = EngineContext(AnnotationConfig(), Connection(None, None))
    with pytest.raises(AnnotationEngineError):
        asyncio.run(engines.title_from_prompt("x", ctx))


def test_tagger_module_default_size() -> None:
    assert wd_tagger.DEFAULT_INPUT_SIZE == 448
