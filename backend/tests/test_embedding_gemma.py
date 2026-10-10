"""EmbeddingGemma 2(ADR-0044)のトークナイザー、画像の前処理、ONNX エンジンの2段の計算。

実際のモデルが要らないものは、いつも回す。

- 前処理: リサイズ後の大きさ、パッチの並び、位置 ID、詰め物、透明な部分の白の合成
- トークナイザー: `<bos>` / `<eos>` の付け方、1,024 トークンでの切り方(sentencepiece は偽物)
- エンジン: ONNX のセッションを偽物に差し替え、渡す入力(名前、形、空の配列、プレフィックス)、
  読み込む側(画像は両方、文章は文章側だけ)、メモリの確認を確かめる

実際のモデルでの照合は、環境変数 `GAKEI_TEST_EG2_DIR`(5 つのファイルを置いたディレクトリ)が
あるときだけ行う(LY の `GAKEI_TEST_LY_SPIECE` と同じ)。照合用の id 列は、Windows の検証環境で
transformers のトークナイザーから出した値。
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from app.embedding.base import EmbeddingError
from app.embedding.catalog import CLIP_MODELS, model_dir
from app.embedding.onnx_engine import OnnxClipEngine
from app.embedding.preprocess import (
    EG2_MAX_PATCHES,
    EG2_PATCH_DIM,
    eg2_target_size,
    preprocess_eg2,
)
from app.embedding.tokenization import (
    EG2_BOI,
    EG2_BOS,
    EG2_EOI,
    EG2_EOS,
    EG2_IMAGE,
    EG2_MULTIMODAL_PIECES,
    Eg2Tokenizer,
    eg2_image_input_ids,
)
from app.model_store.residency import ModelResidency

EG2 = CLIP_MODELS["embeddinggemma-2-q8"]
_MB = 1000 * 1000


# -- 画像の前処理 -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("size", "target", "soft_tokens"),
    [
        # (幅, 高さ) → (幅, 高さ)。ソフトトークン数は検証環境の transformers の値。
        ((1024, 1024), (768, 768), 256),
        ((832, 1216), (624, 960), 260),
        ((1216, 832), (960, 624), 260),
        ((1664, 960), (1056, 576), 264),
        ((1920, 1216), (1008, 624), 273),
        # 小さい画像は拡大する(thumb の 512px では足りない。ADR-0044)。
        ((512, 512), (768, 768), 256),
        ((2048, 2048), (768, 768), 256),
    ],
)
def test_eg2_target_size(size: tuple[int, int], target: tuple[int, int], soft_tokens: int) -> None:
    width, height = size
    target_height, target_width = eg2_target_size(height, width)
    assert (target_width, target_height) == target
    assert target_height % 48 == 0 and target_width % 48 == 0
    patches = (target_height // 16) * (target_width // 16)
    assert patches <= EG2_MAX_PATCHES
    assert patches // 9 == soft_tokens


def test_eg2_target_size_for_extreme_aspect_ratios() -> None:
    # 片方の辺が 48 に満たなくなるときは 48 にし、もう片方を縦横比で決める(上限あり)。
    assert eg2_target_size(1, 5000) == (48, 13440)
    assert eg2_target_size(5000, 1) == (13440, 48)
    for height, width in ((1, 5000), (20, 5000), (5000, 20), (3, 7)):
        th, tw = eg2_target_size(height, width)
        assert (th // 16) * (tw // 16) <= EG2_MAX_PATCHES


def test_preprocess_eg2_patches_positions_and_padding() -> None:
    # 768×768 はリサイズしない。パッチ (行 r, 列 c) を値 (r, c, 7) で塗る。
    side = 768
    grid = side // 16
    array = np.zeros((side, side, 3), dtype=np.uint8)
    for r in range(grid):
        for c in range(grid):
            array[r * 16 : (r + 1) * 16, c * 16 : (c + 1) * 16] = (r, c, 7)
    pixels, positions, soft_tokens = preprocess_eg2(Image.fromarray(array, "RGB"))

    assert pixels.shape == (1, EG2_MAX_PATCHES, EG2_PATCH_DIM) and pixels.dtype == np.float32
    assert positions.shape == (1, EG2_MAX_PATCHES, 2) and positions.dtype == np.int64
    patches = grid * grid
    assert soft_tokens == patches // 9 == 256

    # 行優先。位置 ID は (列, 行)。値は 0〜1 にするだけ(mean/std の正規化はしない)。
    for index in (0, 1, grid, patches - 1):
        r, c = divmod(index, grid)
        assert positions[0, index].tolist() == [c, r]
        patch = pixels[0, index].reshape(16, 16, 3)
        np.testing.assert_allclose(patch[..., 0], r / 255.0, rtol=1e-6)
        np.testing.assert_allclose(patch[..., 1], c / 255.0, rtol=1e-6)
        np.testing.assert_allclose(patch[..., 2], 7 / 255.0, rtol=1e-6)
    # パッチの中は (y, x, チャンネル) の順に並ぶ。
    array2 = np.zeros((side, side, 3), dtype=np.uint8)
    array2[0, 1] = (10, 20, 30)
    pixels2, _, _ = preprocess_eg2(Image.fromarray(array2, "RGB"))
    np.testing.assert_allclose(pixels2[0, 0, 3:6], np.array([10, 20, 30]) / 255.0, rtol=1e-6)

    # 詰め物は 0、位置 ID は -1。
    assert not pixels[0, patches:].any()
    assert (positions[0, patches:] == -1).all()


def test_preprocess_eg2_resizes_with_bicubic_and_composites_on_white() -> None:
    image = Image.new("RGBA", (1024, 1024), (255, 0, 0, 0))  # 全部透明
    pixels, positions, soft_tokens = preprocess_eg2(image)
    assert soft_tokens == 256
    used = pixels[0, : 256 * 9]
    np.testing.assert_allclose(used, 1.0)  # 白
    assert (positions[0, : 256 * 9] >= 0).all()

    # Pillow の bicubic でリサイズした結果と同じ値。
    rng = np.random.default_rng(0)
    source = Image.fromarray(rng.integers(0, 256, (600, 900, 3), dtype=np.uint8), "RGB")
    th, tw = eg2_target_size(600, 900)
    resized = np.asarray(source.resize((tw, th), Image.Resampling.BICUBIC), dtype=np.float32)
    pixels, _, _ = preprocess_eg2(source)
    np.testing.assert_allclose(pixels[0, 0].reshape(16, 16, 3), resized[:16, :16] / 255.0)


# -- トークナイザー -----------------------------------------------------------------


class _FakeSentencePiece:
    """1文字を1つの id(コードポイント + 1000)にする。"""

    def encode(self, text: str) -> list[int]:
        return [ord(ch) + 1000 for ch in text]


def test_eg2_tokenizer_adds_bos_eos_and_cuts_keeping_eos() -> None:
    tokenizer = Eg2Tokenizer(processor=_FakeSentencePiece())
    assert tokenizer.ids("ab", 1024) == [EG2_BOS, 1097, 1098, EG2_EOS]
    assert tokenizer.ids("", 1024) == [EG2_BOS, EG2_EOS]
    # `<bos>` と `<eos>` を含めて上限に切り、末尾の `<eos>` は残す。
    long = tokenizer.ids("x" * 5000, 1024)
    assert len(long) == 1024
    assert long[0] == EG2_BOS and long[-1] == EG2_EOS
    assert long[1:-1] == [ord("x") + 1000] * 1022
    assert len(tokenizer.ids("x" * 1022, 1024)) == 1024
    assert tokenizer.ids("x" * 1023, 1024)[-2:] == [ord("x") + 1000, EG2_EOS]

    feeds = tokenizer.feeds("ab", 1024)
    assert set(feeds) == {"input_ids", "attention_mask"}
    assert feeds["input_ids"].tolist() == [[EG2_BOS, 1097, 1098, EG2_EOS]]
    assert feeds["input_ids"].dtype == np.int64
    assert feeds["attention_mask"].tolist() == [[1, 1, 1, 1]]
    assert feeds["attention_mask"].dtype == np.int64


def test_eg2_tokenizer_splits_multimodal_markers_as_plain_text() -> None:
    class _Markers(_FakeSentencePiece):
        """`<|image|>` だけをユーザー定義のピースとして1つの id にする。"""

        def encode(self, text: str) -> list[int]:
            ids: list[int] = []
            while text:
                if text.startswith("<|image|>"):
                    ids.append(EG2_IMAGE)
                    text = text[len("<|image|>") :]
                else:
                    ids.append(ord(text[0]) + 1000)
                    text = text[1:]
            return ids

    tokenizer = Eg2Tokenizer(processor=_Markers())
    ids = tokenizer.ids("a<|image|>", 1024)
    assert ids == [EG2_BOS, *[ord(ch) + 1000 for ch in "a<|image|>"], EG2_EOS]


def test_eg2_image_input_ids() -> None:
    ids = eg2_image_input_ids(3)
    assert ids.dtype == np.int64
    assert ids.tolist() == [[EG2_BOS, EG2_BOI, EG2_IMAGE, EG2_IMAGE, EG2_IMAGE, EG2_EOI, EG2_EOS]]
    # 画像のトークン列は上限で切らない。
    assert eg2_image_input_ids(280).shape == (1, 284)
    with pytest.raises(ValueError):
        eg2_image_input_ids(0)


# -- エンジン(セッションは偽物) ---------------------------------------------------------


class _FakeSession:
    def __init__(self, kind: str, calls: list[tuple[str, list[str], dict[str, np.ndarray]]]):
        self.kind = kind
        self.calls = calls

    def run(self, outputs: list[str], feeds: dict[str, np.ndarray]) -> list[np.ndarray]:
        self.calls.append((self.kind, outputs, feeds))
        if self.kind == "vision":
            # 詰め物ではないパッチ / 9 個のソフトトークン。
            used = int((feeds["pixel_position_ids"][0, :, 0] >= 0).sum())
            return [np.full((used // 9, 512), 0.5, dtype=np.float32)]
        vector = np.zeros((1, 768), dtype=np.float32)
        vector[0, feeds["input_ids"].shape[1] % 768] = 1.0
        return [vector]


class _StubEngine(OnnxClipEngine):
    def __init__(self, data_dir: Path, **kwargs: Any) -> None:
        super().__init__(data_dir, EG2, residency=ModelResidency(), **kwargs)
        self.calls: list[tuple[str, list[str], dict[str, np.ndarray]]] = []
        self.created: list[str] = []
        self._tokenizer = Eg2Tokenizer(processor=_FakeSentencePiece())

    def _create_session(self, path: Path) -> Any:
        kind = "vision" if path.name == EG2.vision_file else "text"
        self.created.append(kind)
        return _FakeSession(kind, self.calls)


@pytest.fixture
def eg2_dir(tmp_path: Path) -> Path:
    directory = model_dir(tmp_path, EG2.name)
    directory.mkdir(parents=True)
    for remote in EG2.files:
        (directory / remote.name).write_bytes(b"stub")
    return tmp_path


def _assert_empty_features(feeds: dict[str, np.ndarray], names: tuple[str, ...]) -> None:
    for name in names:
        assert feeds[name].shape == (0, 512) and feeds[name].dtype == np.float32


def test_eg2_engine_texts_load_only_the_text_side(eg2_dir: Path) -> None:
    engine = _StubEngine(eg2_dir)
    vectors = engine.embed_texts(["ab", "c"])
    assert vectors.shape == (2, 768)
    assert engine.loaded_parts == ("text",)
    assert engine.created == ["text"]
    # 1件ずつ、SearchQuery のプレフィックスを付けて計算する。
    assert [kind for kind, _, _ in engine.calls] == ["text", "text"]
    prefix = [ord(ch) + 1000 for ch in EG2.query_prefix]
    for (_, outputs, feeds), text in zip(engine.calls, ("ab", "c"), strict=True):
        assert outputs == ["sentence_embedding"]
        assert set(feeds) == {
            "input_ids",
            "attention_mask",
            "image_features",
            "video_features",
            "audio_features",
        }
        body = [ord(ch) + 1000 for ch in text]
        assert feeds["input_ids"].tolist() == [[EG2_BOS, *prefix, *body, EG2_EOS]]
        _assert_empty_features(feeds, ("image_features", "video_features", "audio_features"))
    # 長い文章は 1,024 トークンで切る。
    engine.calls.clear()
    engine.embed_texts(["x" * 3000])
    assert engine.calls[0][2]["input_ids"].shape == (1, 1024)


def test_eg2_engine_images_run_vision_then_text(eg2_dir: Path) -> None:
    engine = _StubEngine(eg2_dir)
    images = [Image.new("RGB", (1024, 1024), "red"), Image.new("RGB", (1216, 832), "blue")]
    vectors = engine.embed_images(images)
    assert vectors.shape == (2, 768)
    # 画像の計算は両方を読み込む(文章側が先)。
    assert engine.created == ["text", "vision"]
    assert set(engine.loaded_parts) == {"vision", "text"}
    # 1枚ずつ、画像側 → 文章側。
    assert [kind for kind, _, _ in engine.calls] == ["vision", "text", "vision", "text"]
    for index, soft_tokens in ((0, 256), (2, 260)):
        _, outputs, vision_feeds = engine.calls[index]
        assert outputs == ["image_features"]
        assert set(vision_feeds) == {"pixel_values", "pixel_position_ids"}
        assert vision_feeds["pixel_values"].shape == (1, EG2_MAX_PATCHES, EG2_PATCH_DIM)
        _, outputs, text_feeds = engine.calls[index + 1]
        assert outputs == ["sentence_embedding"]
        assert text_feeds["input_ids"].tolist() == eg2_image_input_ids(soft_tokens).tolist()
        assert text_feeds["attention_mask"].shape == (1, soft_tokens + 4)
        assert text_feeds["image_features"].shape == (soft_tokens, 512)
        _assert_empty_features(text_feeds, ("video_features", "audio_features"))
    # 文章の検索は、読み込み済みの文章側を使い回す。
    engine.embed_texts(["a"])
    assert engine.created == ["text", "vision"]


def test_eg2_engine_rejects_mismatched_soft_tokens(eg2_dir: Path) -> None:
    class _Broken(_StubEngine):
        def _create_session(self, path: Path) -> Any:
            session = super()._create_session(path)
            if session.kind == "vision":
                session.run = lambda outputs, feeds: [np.zeros((3, 512), dtype=np.float32)]  # noqa: ARG005
            return session

    with pytest.raises(EmbeddingError):
        _Broken(eg2_dir).embed_images([Image.new("RGB", (64, 64))])


@pytest.mark.parametrize(
    ("available", "texts_ok", "images_ok"),
    [
        (540 * _MB, False, False),
        (560 * _MB, True, True),  # 文章側(550MB)を読み込んだあとの画像側は差の 200MB
        (190 * _MB, False, False),
    ],
)
def test_eg2_engine_memory_estimates(
    eg2_dir: Path, available: int, texts_ok: bool, images_ok: bool
) -> None:
    def run(kind: str) -> bool:
        engine = _StubEngine(eg2_dir, memory_probe=lambda: available)
        try:
            if kind == "text":
                engine.embed_texts(["a"])
            else:
                engine.embed_images([Image.new("RGB", (64, 64))])
        except EmbeddingError:
            return False
        return True

    assert run("text") is texts_ok
    assert run("image") is images_ok


def test_eg2_engine_releases_both_sessions_when_idle(eg2_dir: Path) -> None:
    engine = _StubEngine(eg2_dir)
    engine.embed_images([Image.new("RGB", (64, 64))])
    assert engine.release_idle(idle_seconds=3600) is False
    assert engine.release_idle(idle_seconds=0) is True
    assert engine.loaded_parts == ()


# -- 実際のモデル(GAKEI_TEST_EG2_DIR があるときだけ) -------------------------------------

_EG2_DIR = os.environ.get("GAKEI_TEST_EG2_DIR", "").strip()
requires_eg2 = pytest.mark.skipif(
    not _EG2_DIR, reason="GAKEI_TEST_EG2_DIR(EmbeddingGemma 2 の 5 ファイルのディレクトリ)が無い"
)

# Windows の検証環境で transformers のトークナイザーから出した id 列(bos / eos 込み)。
_REFERENCE_IDS = {
    "赤い髪の少女": [2, 234830, 241219, 236945, 76641, 1],
    "a girl with red hair": [2, 236746, 3953, 607, 2604, 5324, 1],
    "task: search result | query: 赤い髪の少女": [
        2,
        8071,
        236787,
        3927,
        1354,
        1109,
        7609,
        236787,
        84113,
        236985,
        241219,
        236945,
        76641,
        1,
    ],  # fmt: skip
    "title: none | text: a girl with red hair": [
        2,
        3250,
        236787,
        7293,
        1109,
        1816,
        236787,
        496,
        3953,
        607,
        2604,
        5324,
        1,
    ],  # fmt: skip
}


@pytest.fixture(scope="module")
def real_eg2_data_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """`DATA_DIR/models/clip/embeddinggemma-2-q8/` に、指定のディレクトリへのリンクを置く。"""
    data_dir = tmp_path_factory.mktemp("eg2-data")
    directory = model_dir(data_dir, EG2.name)
    directory.parent.mkdir(parents=True)
    directory.symlink_to(Path(_EG2_DIR).resolve(), target_is_directory=True)
    return data_dir


@requires_eg2
def test_real_eg2_tokenizer_matches_reference_ids() -> None:
    tokenizer = Eg2Tokenizer(Path(_EG2_DIR) / "tokenizer.model")
    for text, expected in _REFERENCE_IDS.items():
        assert tokenizer.ids(text, 1024) == expected, text
    assert (
        tokenizer.ids(EG2.query_prefix + "赤い髪の少女", 1024)
        == _REFERENCE_IDS["task: search result | query: 赤い髪の少女"]
    )
    # 特殊トークンの id がモデルの語彙と合う。本文中の `<|image|>` は特殊扱いしない。
    processor = tokenizer.processor
    assert processor.piece_to_id("<bos>") == EG2_BOS
    assert processor.piece_to_id("<eos>") == EG2_EOS
    assert processor.piece_to_id("<|image>") == EG2_BOI
    assert processor.piece_to_id("<|image|>") == EG2_IMAGE
    assert processor.piece_to_id("<image|>") == EG2_EOI
    # 本文中の画像などの目印の文字列は、ふつうの文字として分割する(特徴の差し込み先にしない)。
    for text in ("<|image|>", "a <|image> b <image|>", "<|audio|><|video|>"):
        ids = tokenizer.ids(text, 1024)
        assert not set(ids) & set(EG2_MULTIMODAL_PIECES), (text, ids)
    assert tokenizer.ids("<|image|>", 1024)[1:-1] == (
        tokenizer.processor.encode("<") + tokenizer.processor.encode("|image|>")
    )


@requires_eg2
def test_real_eg2_engine_vectors(real_eg2_data_dir: Path) -> None:
    engine = OnnxClipEngine(real_eg2_data_dir, EG2, residency=ModelResidency())
    texts = engine.embed_texts(["赤い髪の少女", "a girl with red hair", "x " * 3000])
    assert engine.loaded_parts == ("text",)
    assert texts.shape == (3, 768)
    np.testing.assert_allclose(np.linalg.norm(texts, axis=1), 1.0, atol=1e-4)
    # 同じ意味の日本語と英語は、無関係な長い文章より近い。
    assert texts[0] @ texts[1] > texts[0] @ texts[2]

    red = Image.new("RGB", (1024, 1024), (200, 30, 30))
    blue = Image.new("RGBA", (832, 1216), (30, 30, 200, 255))
    started = time.perf_counter()
    images = engine.embed_images([red, blue])
    elapsed = time.perf_counter() - started
    assert set(engine.loaded_parts) == {"vision", "text"}
    assert images.shape == (2, 768)
    np.testing.assert_allclose(np.linalg.norm(images, axis=1), 1.0, atol=1e-4)
    # 1枚ずつ計算するので、同じ画像は同じベクトル。
    np.testing.assert_allclose(engine.embed_images([red])[0], images[0], atol=1e-6)
    print(f"EG2 画像 2 枚: {elapsed:.1f} 秒")


@requires_eg2
@pytest.mark.parametrize("scene", ["landscape", "portrait"])
def test_real_eg2_duplicate_threshold_keeps_degraded_copies(
    real_eg2_data_dir: Path, scene: str
) -> None:
    """ADR-0033 12章の画像の組のうち、強く劣化させた重複も、EG2 の既定のしきい値以上になる
    (ADR-0044 5章)。実際の流れと同じく preview にしてから計算する。全部の組(6 テーマ ×
    劣化 13・色違い 7・別の画像 3)で測った値は catalog.py の `EG2_DUPLICATE_THRESHOLD` の横。"""
    import io

    from app.domain.derivatives import make_preview
    from tests import perceptual_images as pi

    def preview(image: Image.Image) -> Image.Image:
        out = Image.open(io.BytesIO(make_preview(image)))
        out.load()
        return out

    engine = OnnxClipEngine(real_eg2_data_dir, EG2, residency=ModelResidency())
    base_image = pi.scene(scene, 1)
    base = engine.embed_images([preview(base_image)])[0]
    for label in ("s25_q25", "crop3_s25_q25", "s50_q60"):
        other = engine.embed_images([preview(pi.DEGRADATIONS[label](base_image))])[0]
        assert float(base @ other) >= EG2.duplicate_threshold, (scene, label, float(base @ other))
