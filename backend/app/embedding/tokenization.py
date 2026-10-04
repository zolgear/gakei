"""検索の文章をトークンの id にする(ADR-0033 2章・11章)。

transformers、torch、`tokenizers` は使わない。

**OpenAI CLIP(`ClipBpeTokenizer`)**: `vocab.json` と `merges.txt` を読む純 Python の BPE。
`tokenizer.json` の設定(Xenova の配布物)と同じ手順にする。

1. NFC に正規化し、空白の連続を1つの空白にし、1文字ずつ小文字にする。
2. 次の規則で切り出す(元は `regex` の
   `<\\|startoftext\\|>|<\\|endoftext\\|>|'s|'t|'re|'ve|'m|'ll|'d|[\\p{L}]+|[\\p{N}]|[^\\s\\p{L}\\p{N}]+`)。
   標準ライブラリの `re` は `\\p{L}` を持たないので、`unicodedata` の分類で同じ規則を書く
   (`regex` は実行時の依存に無いため)。空白は捨てる。
3. 切り出したものを UTF-8 のバイトにし、バイトを GPT-2 と同じ表で文字に写して、末尾に `</w>` を
   付けた BPE で分割する。語彙に無い断片は未知語(`<|endoftext|>`)にする。
4. 先頭に BOS(49406)、末尾に EOS(49407)を付け、77 に切り詰め(EOS は残す)、EOS で 77 まで
   詰める。文章側のモデルの入力は `input_ids` だけ(attention_mask は無い)。

文章に `<|endoftext|>` などの文字列そのものがあっても、特殊トークンとしては扱わない(ふつうの
文字として BPE で分割する)。`tokenizers` とはこの点だけが違う(意図どおり)。

**LY clip-japanese-base(`LyTokenizer`)**: `sentencepiece` と `spiece.model`。

1. 小文字にする(小文字にしないと未知語になる)。
2. `sentencepiece` で分割して 76 個に切り詰め、先頭に [CLS](=4)を付ける(最大 77)。
3. バッチ内の最長に合わせて [PAD](=3)で右に詰める。attention_mask は実トークンが 1。
   position_ids は 0 から。
4. 入力は `input0`(input_ids)、`input1`(attention_mask)、`input2`(position_ids)。int64。

文字列 `[CLS]` などは、小文字にしたうえで `sentencepiece` がふつうに分割する(特殊トークンに
しない)。transformers 4.39 の T5Tokenizer とは、この1点だけが違う(意図どおり)。
"""

from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

CLIP_MAX_LENGTH = 77
CLIP_BOS = 49406
CLIP_EOS = 49407

LY_MAX_LENGTH = 77

_WHITESPACE_RE = re.compile(r"\s+")
_SPECIAL_STRINGS = ("<|startoftext|>", "<|endoftext|>")
_CONTRACTIONS = ("'s", "'t", "'re", "'ve", "'m", "'ll", "'d")


def _bytes_to_unicode() -> dict[int, str]:
    """GPT-2 / CLIP のバイト → 文字の表(表示できる文字はそのまま、他は 256 以降に写す)。"""
    printable = (
        list(range(ord("!"), ord("~") + 1))
        + list(range(ord("¡"), ord("¬") + 1))
        + list(range(ord("®"), ord("ÿ") + 1))
    )
    codes = printable[:]
    extra = 0
    for byte in range(256):
        if byte not in printable:
            printable.append(byte)
            codes.append(256 + extra)
            extra += 1
    return dict(zip(printable, (chr(c) for c in codes), strict=True))


_BYTE_ENCODER = _bytes_to_unicode()


def _is_letter(ch: str) -> bool:
    return unicodedata.category(ch).startswith("L")


def _is_number(ch: str) -> bool:
    return unicodedata.category(ch).startswith("N")


def _is_other(ch: str) -> bool:
    """`[^\\s\\p{L}\\p{N}]`。"""
    return not ch.isspace() and not _is_letter(ch) and not _is_number(ch)


def clip_pre_tokenize(text: str) -> list[str]:
    """CLIP の正規化と切り出し(モジュールの docstring の 1・2)。"""
    text = _WHITESPACE_RE.sub(" ", unicodedata.normalize("NFC", text))
    # 1文字ずつ小文字にする(`tokenizers` と同じ。文字列全体の `lower()` は語末の Σ を ς にする)。
    text = "".join(ch.lower() for ch in text)
    pieces: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        matched = next((s for s in _SPECIAL_STRINGS if text.startswith(s, i)), None)
        if matched is None:
            matched = next((c for c in _CONTRACTIONS if text.startswith(c, i)), None)
        if matched is not None:
            pieces.append(matched)
            i += len(matched)
            continue
        ch = text[i]
        if _is_letter(ch):
            j = i + 1
            while j < n and _is_letter(text[j]):
                j += 1
        elif _is_number(ch):
            j = i + 1
        elif _is_other(ch):
            j = i + 1
            while j < n and _is_other(text[j]):
                j += 1
        else:  # 空白は捨てる
            i += 1
            continue
        pieces.append(text[i:j])
        i = j
    return pieces


class ClipBpeTokenizer:
    def __init__(self, vocab_path: Path, merges_path: Path) -> None:
        self.encoder: dict[str, int] = json.loads(vocab_path.read_text(encoding="utf-8"))
        lines = merges_path.read_text(encoding="utf-8").split("\n")
        # 1行目は版の注記(`#version: ...`)。
        merges = [tuple(line.split()) for line in lines[1:] if line.strip()]
        self.ranks: dict[tuple[str, ...], int] = {pair: i for i, pair in enumerate(merges)}
        self.unknown_id = self.encoder.get("<|endoftext|>", CLIP_EOS)
        self._bpe = lru_cache(maxsize=10000)(self._bpe_uncached)

    def _bpe_uncached(self, token: str) -> tuple[str, ...]:
        word = [*token[:-1], token[-1] + "</w>"]
        while len(word) > 1:
            pairs = {(word[i], word[i + 1]) for i in range(len(word) - 1)}
            best = min(pairs, key=lambda pair: self.ranks.get(pair, len(self.ranks) + 1))
            if best not in self.ranks:
                break
            first, second = best
            merged: list[str] = []
            i = 0
            while i < len(word):
                if i < len(word) - 1 and word[i] == first and word[i + 1] == second:
                    merged.append(first + second)
                    i += 2
                else:
                    merged.append(word[i])
                    i += 1
            word = merged
        return tuple(word)

    def token_ids(self, text: str) -> list[int]:
        """BOS・EOS を付けず、切り詰めもしない id の列。"""
        ids: list[int] = []
        for piece in clip_pre_tokenize(text):
            mapped = "".join(_BYTE_ENCODER[b] for b in piece.encode("utf-8"))
            ids.extend(self.encoder.get(part, self.unknown_id) for part in self._bpe(mapped))
        return ids

    def encode(self, text: str, max_length: int = CLIP_MAX_LENGTH) -> list[int]:
        """BOS・EOS を付け、`max_length` に切り詰めて EOS で詰めた id の列。"""
        ids = [CLIP_BOS, *self.token_ids(text)[: max_length - 2], CLIP_EOS]
        return ids + [CLIP_EOS] * (max_length - len(ids))

    def __call__(self, texts: list[str]) -> dict[str, np.ndarray]:
        return {"input_ids": np.array([self.encode(t) for t in texts], dtype=np.int64)}


class LyTokenizer:
    def __init__(self, model_path: Path | None = None, processor: Any = None) -> None:
        """`processor` はテスト用(`SentencePieceProcessor` と同じ `encode` と `piece_to_id`)。"""
        if processor is None:
            import sentencepiece

            assert model_path is not None
            processor = sentencepiece.SentencePieceProcessor(model_file=str(model_path))
        self.processor = processor
        self.cls_id = int(processor.piece_to_id("[CLS]"))
        self.pad_id = int(processor.piece_to_id("[PAD]"))

    def ids(self, text: str) -> list[int]:
        pieces = self.processor.encode(text.lower())
        return [self.cls_id, *[int(i) for i in pieces][: LY_MAX_LENGTH - 1]]

    def __call__(self, texts: list[str]) -> dict[str, np.ndarray]:
        rows = [self.ids(t) for t in texts]
        length = max(len(r) for r in rows)
        input_ids = np.full((len(rows), length), self.pad_id, dtype=np.int64)
        mask = np.zeros((len(rows), length), dtype=np.int64)
        for index, row in enumerate(rows):
            input_ids[index, : len(row)] = row
            mask[index, : len(row)] = 1
        positions = np.tile(np.arange(length, dtype=np.int64), (len(rows), 1))
        return {"input0": input_ids, "input1": mask, "input2": positions}
