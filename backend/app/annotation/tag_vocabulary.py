"""プロンプトのタグの語彙(ADR-0039 2章)。WD Tagger の `selected_tags.csv` を読む。

- `DATA_DIR/models/wd/<モデル>/selected_tags.csv` のうち、最初に見つかったものを使う
  (どのモデルのものでも語彙は同じ)。一般(category 0)とキャラクター(4)だけを取る。
- 名前は WD Tagger の出力と同じく `_` を空白にし、タグ名の正規化(`normalize_tag_name`)を
  通した形で持つ(`asset_tag` のタグ名と比べられるようにする)。
- 必要になったとき(候補の API が初めて呼ばれたとき)に一度だけ読み、プロセス内に置く。
  ファイルが無ければ語彙なしとして動く。候補のためにダウンロードはしない。後から WD Tagger の
  モデルを入れた場合に備え、無かったときだけ `RECHECK_SECONDS` ごとに見直す。
"""

from __future__ import annotations

import csv
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from app.annotation.wd_models import TAGS_FILE, WD_MODELS, models_root
from app.domain.annotations import normalize_tag_name_or_none

VOCABULARY_CATEGORIES = frozenset({0, 4})
RECHECK_SECONDS = 60.0


@dataclass(frozen=True)
class VocabularyEntry:
    name: str
    count: int


class TagVocabulary:
    """語彙。`entries` は count の多い順(同数は名前順)。"""

    def __init__(self, entries: list[VocabularyEntry]) -> None:
        self.entries = sorted(entries, key=lambda e: (-e.count, e.name))
        self._names = {e.name for e in self.entries}

    def __contains__(self, name: object) -> bool:
        return name in self._names

    def __len__(self) -> int:
        return len(self.entries)


def load_vocabulary(csv_path: Path) -> TagVocabulary:
    """`selected_tags.csv`(tag_id,name,category,count)から語彙を作る。読めない行は飛ばす。"""
    seen: dict[str, VocabularyEntry] = {}
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            try:
                category = int(row["category"])
                count = int(row.get("count") or 0)
            except (KeyError, TypeError, ValueError):
                continue
            if category not in VOCABULARY_CATEGORIES:
                continue
            name = normalize_tag_name_or_none((row.get("name") or "").replace("_", " "))
            if name is None:
                continue
            previous = seen.get(name)
            if previous is None or previous.count < count:
                seen[name] = VocabularyEntry(name=name, count=count)
    return TagVocabulary(list(seen.values()))


def find_vocabulary_file(data_dir: Path) -> Path | None:
    """語彙のファイル。WD Tagger のモデルの並び順で、最初に見つかったもの。"""
    root = models_root(data_dir)
    for name in WD_MODELS:
        path = root / name / TAGS_FILE
        if path.is_file():
            return path
    return None


@dataclass
class _Cached:
    vocabulary: TagVocabulary | None
    checked_at: float


_cache: dict[Path, _Cached] = {}
_lock = threading.Lock()


def get_vocabulary(data_dir: Path, now: float | None = None) -> TagVocabulary | None:
    """`data_dir` の語彙。無ければ None。読み込みは一度だけ(無かったときは間をおいて見直す)。"""
    current = time.monotonic() if now is None else now
    key = data_dir.resolve()
    with _lock:
        cached = _cache.get(key)
        if cached is not None and (
            cached.vocabulary is not None or current - cached.checked_at < RECHECK_SECONDS
        ):
            return cached.vocabulary
        vocabulary: TagVocabulary | None = None
        path = find_vocabulary_file(data_dir)
        if path is not None:
            try:
                vocabulary = load_vocabulary(path)
            except (OSError, UnicodeDecodeError, csv.Error):
                vocabulary = None
        _cache[key] = _Cached(vocabulary=vocabulary, checked_at=current)
        return vocabulary


def clear_cache() -> None:
    """テスト用。読み込んだ語彙を忘れる。"""
    with _lock:
        _cache.clear()
