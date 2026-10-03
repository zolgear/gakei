"""埋め込みのトークナイザーと前処理(ADR-0033 2章・11章)。

CLIP の BPE は `tests/fixtures/clip_bpe/` の語彙(Xenova/clip-vit-base-patch32 の `vocab.json` と
`merges.txt` から、ここの文字列に要る項目だけを抜き出したもの。MIT)で確かめる。BPE はどの
組を結合するかを順位で決めるので、各文字列で候補になった組をすべて残せば、元の語彙と同じ
結果になる。期待する id は `tokenizers`(Rust 版)で出した値で、`<|endoftext|>` を本文に含む
1件だけは意図して違う(特殊トークンとして扱わない)。

LY の id は transformers 4.39 の T5Tokenizer で出した値(`tests/fixtures/ly_tokens.json`)。
`spiece.model`(約 800KB)はリポジトリに入れないので、本物での照合は環境変数
`GAKEI_TEST_LY_SPIECE`(`spiece.model` のパス)があるときだけ行う。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from app.embedding.preprocess import (
    CLIP_MEAN,
    CLIP_STD,
    IMAGENET_MEAN,
    IMAGENET_STD,
    preprocess_clip,
    preprocess_ly,
)
from app.embedding.tokenization import (
    CLIP_BOS,
    CLIP_EOS,
    ClipBpeTokenizer,
    LyTokenizer,
    clip_pre_tokenize,
)

_FIXTURES = Path(__file__).parent / "fixtures"
_CLIP_DIR = _FIXTURES / "clip_bpe"


@pytest.fixture(scope="module")
def clip_tokenizer() -> ClipBpeTokenizer:
    return ClipBpeTokenizer(_CLIP_DIR / "vocab.json", _CLIP_DIR / "merges.txt")


def _clip_cases() -> list[dict]:
    return json.loads((_CLIP_DIR / "expected.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", _clip_cases(), ids=lambda c: c["text"][:20] or "<empty>")
def test_clip_bpe_matches_reference_ids(clip_tokenizer: ClipBpeTokenizer, case: dict) -> None:
    assert clip_tokenizer.encode(case["text"]) == case["ids"]


def test_clip_reference_cases_cover_the_interesting_inputs() -> None:
    cases = _clip_cases()
    texts = [c["text"] for c in cases]
    # 日本語、77 を超える長文、特殊トークンの文字列そのものを含む。
    assert any("夕焼け" in text for text in texts)
    assert any(len(text) > 200 for text in texts)
    different = [c["text"] for c in cases if not c["same_as_tokenizers"]]
    assert different == ["<|endoftext|> inside"]


def test_clip_encode_shape_bos_eos_and_truncation(clip_tokenizer: ClipBpeTokenizer) -> None:
    short = clip_tokenizer.encode("a photo of a cat")
    assert len(short) == 77
    assert short[0] == CLIP_BOS
    # 末尾は EOS で詰める。
    assert short[-1] == CLIP_EOS
    long_text = next(c["text"] for c in _clip_cases() if len(c["text"]) > 200)
    # 2回つなげれば確実に 77 を超える(語彙は同じものだけで済む)。
    ids = clip_tokenizer.encode(long_text + " " + long_text)
    assert len(clip_tokenizer.token_ids(long_text + " " + long_text)) > 77
    assert len(ids) == 77
    assert ids[0] == CLIP_BOS
    assert ids[-1] == CLIP_EOS
    # 切り詰めても EOS 以外の本文で埋まっている(詰め物ではない)。
    assert ids[-2] != CLIP_EOS
    batch = clip_tokenizer(["a photo of a cat", ""])
    assert batch["input_ids"].dtype == np.int64
    assert batch["input_ids"].shape == (2, 77)


def test_clip_special_token_strings_are_plain_text(clip_tokenizer: ClipBpeTokenizer) -> None:
    ids = clip_tokenizer.encode("<|endoftext|> inside")
    # 本文の中に EOS(特殊トークン)は出てこない。
    body = ids[1 : ids.index(CLIP_EOS)]
    assert CLIP_EOS not in body and CLIP_BOS not in body
    assert len(body) > 2


def test_clip_pre_tokenize_rules() -> None:
    assert clip_pre_tokenize("It's  2026!!") == ["it", "'s", "2", "0", "2", "6", "!!"]
    assert clip_pre_tokenize("ΣΑΣ") == ["σασ"]  # 1文字ずつ小文字にする(語末の ς にしない)
    assert clip_pre_tokenize("a\tb\nc") == ["a", "b", "c"]
    assert clip_pre_tokenize("") == []


# -- LY ---------------------------------------------------------------------------


class _FakeSentencePiece:
    """`encode` は文字ごとに 100 + 位置、`piece_to_id` は [CLS]=4、[PAD]=3。"""

    def __init__(self) -> None:
        self.seen: list[str] = []

    def encode(self, text: str) -> list[int]:
        self.seen.append(text)
        return [100 + i for i, _ in enumerate(text)]

    def piece_to_id(self, piece: str) -> int:
        return {"[CLS]": 4, "[PAD]": 3}[piece]


def test_ly_tokenizer_lowercases_adds_cls_pads_and_masks() -> None:
    processor = _FakeSentencePiece()
    tokenizer = LyTokenizer(processor=processor)
    feeds = tokenizer(["AB", "abcd"])
    assert processor.seen == ["ab", "abcd"]
    assert feeds["input0"].tolist() == [[4, 100, 101, 3, 3], [4, 100, 101, 102, 103]]
    assert feeds["input1"].tolist() == [[1, 1, 1, 0, 0], [1, 1, 1, 1, 1]]
    assert feeds["input2"].tolist() == [[0, 1, 2, 3, 4], [0, 1, 2, 3, 4]]
    assert all(v.dtype == np.int64 for v in feeds.values())


def test_ly_tokenizer_truncates_to_77() -> None:
    tokenizer = LyTokenizer(processor=_FakeSentencePiece())
    ids = tokenizer.ids("x" * 200)
    assert len(ids) == 77
    assert ids[0] == 4


_LY_SPIECE = os.environ.get("GAKEI_TEST_LY_SPIECE", "").strip()


@pytest.mark.skipif(not _LY_SPIECE, reason="GAKEI_TEST_LY_SPIECE(spiece.model のパス)が無い")
def test_ly_tokenizer_matches_transformers_reference() -> None:
    cases = json.loads((_FIXTURES / "ly_tokens.json").read_text(encoding="utf-8"))
    tokenizer = LyTokenizer(Path(_LY_SPIECE))
    assert (tokenizer.cls_id, tokenizer.pad_id) == (4, 3)
    for case in cases:
        assert tokenizer.ids(case["text"]) == case["ids"], case["text"]
    different = [c["text"] for c in cases if not c["same_as_transformers_4_39"]]
    # 特殊トークンの文字列を含む1件だけが transformers と違う(意図どおり)。
    assert different == ["[CLS] と </s> と [PAD] を含む文"]


# -- 前処理 ---------------------------------------------------------------------------


def _channel_value(chw: np.ndarray, mean: np.ndarray, std: np.ndarray) -> np.ndarray:
    """CHW から、各チャンネルの値を 0〜255 に戻す。"""
    return (chw * std[:, None, None] + mean[:, None, None]) * 255.0


def test_preprocess_clip_shape_and_center_crop() -> None:
    image = Image.new("RGB", (400, 200), (255, 0, 0))
    # 中央 200 × 200 だけ青にする。横長なので短辺 224 に縮めて中央を切り出すと、左右は青になる。
    image.paste((0, 0, 255), (100, 0, 300, 200))
    chw = preprocess_clip(image)
    assert chw.shape == (3, 224, 224)
    assert chw.dtype == np.float32
    restored = _channel_value(chw, CLIP_MEAN, CLIP_STD)
    assert restored[2, 112, 112] == pytest.approx(255, abs=1)
    assert restored[0, 112, 112] == pytest.approx(0, abs=1)


def test_preprocess_ly_pads_with_black() -> None:
    image = Image.new("RGB", (448, 224), (255, 255, 255))
    chw = preprocess_ly(image)
    assert chw.shape == (3, 224, 224)
    restored = _channel_value(chw, IMAGENET_MEAN, IMAGENET_STD)
    # 長辺を 224 に縮めると 224 × 112。上下に黒の余白が付く。
    assert restored[:, 0, 112] == pytest.approx([0, 0, 0], abs=1)
    assert restored[:, 112, 112] == pytest.approx([255, 255, 255], abs=1)


@pytest.mark.parametrize("preprocess", [preprocess_clip, preprocess_ly])
def test_transparent_pixels_become_white(preprocess) -> None:  # noqa: ANN001
    image = Image.new("RGBA", (224, 224), (0, 0, 0, 0))
    chw = preprocess(image)
    mean, std = (
        (CLIP_MEAN, CLIP_STD) if preprocess is preprocess_clip else (IMAGENET_MEAN, IMAGENET_STD)
    )
    restored = _channel_value(chw, mean, std)
    assert restored[:, 100, 100] == pytest.approx([255, 255, 255], abs=1)
