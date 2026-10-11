"""タグ辞書(ADR-0041)。タグの補完と日本語訳に使う、管理者が登録する辞書。

- **形式:** ヘッダーなしの CSV。4 列(タグ名, カテゴリー, 件数, 別名)ならタグの一覧、2 列
  (タグ名, 訳)なら訳。別名と訳は 1 つの欄にカンマ区切りで複数(訳は先頭が代表)。3 列(別名なし)
  もタグの一覧として読む。種類は先頭の行の列の数の多数決で決め、決まらなければ断る。
  CSV を含む zip も受ける(CSV だけを読み、ほかは無視する。展開はメモリの上で、合計と個数に
  上限を設ける)。文字コードは UTF-8(BOM があってもよい)。改行は LF でも CRLF でもよい。
- **保存:** 辞書ごとに `tag_dictionary` の1行と、中身(`tag_dictionary_entry` / `_alias` /
  `_translation`)。アップロードしたファイルそのものは保存しない。取り込みは行数が多いので、
  `app.worker.tag_dictionary_importer` がプロセス内のスレッドでまとめて INSERT する。
- **検索:** タグ名と別名は、正規化した検索用の列(`*_key`)の主キーの索引で、前方一致を範囲検索
  (`>= 'abc' AND < 'abd'`)で引く。訳は `,訳1,訳2,` の形の列を LIKE で走査する。
- **使う辞書:** 取り込みを終え(`ready`)、有効なものだけ。複数あれば合わせる(同じタグは件数の
  多い方。訳は先に登録したものを優先)。
"""

from __future__ import annotations

import csv
import io
import unicodedata
import uuid
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import and_, delete, select
from sqlalchemy.orm import Session

from app.domain.models import (
    TagDictionary,
    TagDictionaryAlias,
    TagDictionaryEntry,
    TagDictionaryTranslation,
)

KIND_TAGS = "tags"
KIND_TRANSLATIONS = "translations"
Kind = Literal["tags", "translations"]

STATUS_IMPORTING = "importing"
STATUS_READY = "ready"
STATUS_FAILED = "failed"

# カテゴリーの番号の体系。danbooru は 0 一般、1 作者、3 作品、4 キャラクター、5 メタ。
# other はそれ以外(番号のまま出す)。
CATEGORY_SCHEMES = ("danbooru", "other")
DEFAULT_CATEGORY_SCHEME = "danbooru"

# アップロードの本文(ファイル1つ)の上限。zip を展開した後の合計の上限(zip 爆弾を防ぐ)。
UPLOAD_MAX_BYTES = 100 * 1024 * 1024
EXTRACTED_MAX_BYTES = 100 * 1024 * 1024
# zip の中の項目の数(ディレクトリやほかのファイルも数える)と、読む CSV の数の上限。
ZIP_MEMBERS_MAX = 1000
ZIP_CSV_MAX = 16

# 種類の判定に使う先頭の行の数と、判定できた行の割合の下限。
DETECT_SAMPLE_ROWS = 500
DETECT_MIN_RATIO = 0.9

SUGGESTION_MAX = 20

# エラーの種類(`tag_dictionary.error` に保存し、API が文言にする)。
ERROR_INTERRUPTED = "interrupted"
ERROR_IMPORT_FAILED = "importFailed"
ERROR_NO_ROWS = "noRows"

# WD Tagger の語彙と同じく、`_` を空白にしない顔文字のタグ(`^_^` など)。
_KAOMOJI = frozenset(
    {
        "0_0",
        "(o)_(o)",
        "+_+",
        "+_-",
        "._.",
        "<o>_<o>",
        "<|>_<|>",
        "=_=",
        ">_<",
        "3_3",
        "6_9",
        ">_o",
        "@_@",
        "^_^",
        "o_o",
        "u_u",
        "x_x",
        "|_|",
        "||_||",
    }
)


class DictionaryFileError(ValueError):
    """アップロードを断る理由。`code` は文言のキー(`tagDictionaries.<code>`)、`params` は
    その引数。"""

    def __init__(self, code: str, **params: object) -> None:
        super().__init__(code)
        self.code = code
        self.params = params


# -- 正規化 ------------------------------------------------------------------------


def make_key(raw: str) -> str:
    """検索用の形。NFKC、小文字、前後の空白を除き、空白の連続を `_` 1つに。"""
    text = unicodedata.normalize("NFKC", raw).lower()
    return "_".join(text.split())


def query_key(q: str) -> str:
    """候補の検索語。括弧のエスケープ(`\\(`)を外してから `make_key`。"""
    return make_key(q.replace("\\(", "(").replace("\\)", ")"))


def display_name(name: str) -> str:
    """候補に出す名前。`_` を空白に(顔文字のタグはそのまま)。"""
    if name in _KAOMOJI:
        return name
    return name.replace("_", " ")


def _upper_bound(prefix: str) -> str | None:
    """`prefix` で始まる文字列の範囲の上端(含まない)。最後の文字を1つ進める。"""
    last = ord(prefix[-1])
    if last >= 0x10FFFF:
        return None
    return prefix[:-1] + chr(last + 1)


def _prefix_clause(column, prefix: str):  # noqa: ANN001, ANN202
    upper = _upper_bound(prefix)
    if upper is None:
        return column >= prefix
    return and_(column >= prefix, column < upper)


def _split_list(value: str) -> list[str]:
    """カンマ区切りの欄を分ける(前後の空白を除き、空のものは捨てる)。"""
    return [part.strip() for part in value.split(",") if part.strip()]


# -- ファイルの読み取り ------------------------------------------------------------


@dataclass(frozen=True)
class DictionarySource:
    """取り込む CSV 1つ(種類は判定済み)。`text` は読み取った本文。"""

    filename: str
    kind: Kind
    text: str


def _is_zip(filename: str, data: bytes) -> bool:
    return data[:4] == b"PK\x03\x04" or filename.lower().endswith(".zip")


def _clean_filename(name: str) -> str:
    """表示用のファイル名(パスの部分を除き、制御文字を落とし、長さを抑える)。"""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    base = "".join(ch for ch in base if ch.isprintable()).strip()
    return base[:200] or "dictionary.csv"


def _read_zip(filename: str, data: bytes) -> list[tuple[str, bytes]]:
    """zip の中の CSV を、メモリの上で読む。展開後の合計と個数に上限を設ける。

    中の名前はパスとして使わない(ディスクに展開しない)ので、`../` などの細工は表示名から
    パスの部分を落とすだけでよい。宣言された大きさは信用せず、読んだ量で上限を確かめる。
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError) as exc:
        raise DictionaryFileError("invalidZip") from exc
    with archive:
        members = archive.infolist()
        if len(members) > ZIP_MEMBERS_MAX:
            raise DictionaryFileError("zipTooManyFiles", max=ZIP_MEMBERS_MAX)
        csv_members = [m for m in members if not m.is_dir() and m.filename.lower().endswith(".csv")]
        if not csv_members:
            raise DictionaryFileError("zipNoCsv")
        if len(csv_members) > ZIP_CSV_MAX:
            raise DictionaryFileError("zipTooManyFiles", max=ZIP_CSV_MAX)
        total = 0
        result: list[tuple[str, bytes]] = []
        for member in csv_members:
            if member.flag_bits & 0x1:
                raise DictionaryFileError("zipEncrypted")
            if total + member.file_size > EXTRACTED_MAX_BYTES:
                raise DictionaryFileError(
                    "extractedTooLarge", mb=EXTRACTED_MAX_BYTES // (1024 * 1024)
                )
            chunks: list[bytes] = []
            try:
                with archive.open(member) as f:
                    while True:
                        chunk = f.read(1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > EXTRACTED_MAX_BYTES:
                            raise DictionaryFileError(
                                "extractedTooLarge", mb=EXTRACTED_MAX_BYTES // (1024 * 1024)
                            )
                        chunks.append(chunk)
            except (zipfile.BadZipFile, NotImplementedError, RuntimeError, OSError) as exc:
                raise DictionaryFileError("invalidZip") from exc
            result.append(
                (
                    f"{_clean_filename(filename)}/{_clean_filename(member.filename)}",
                    b"".join(chunks),
                )
            )
        return result


def _decode(filename: str, data: bytes) -> str:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DictionaryFileError("invalidEncoding", file=filename) from exc
    # PostgreSQL は NUL を保存できない(ADR-0027)。辞書の文字としても意味が無いので落とす。
    return text.replace("\x00", "")


def _row_kind(row: list[str]) -> Kind | None:
    """1行の種類。読めない行(列の数が違う、数値が読めない、タグ名が空)は None。"""
    if len(row) in (3, 4):
        if not row[0].strip():
            return None
        try:
            int(row[1])
            float(row[2])
        except ValueError:
            return None
        return KIND_TAGS
    if len(row) == 2:
        if not row[0].strip() or not row[1].strip():
            return None
        return KIND_TRANSLATIONS
    return None


def detect_kind(filename: str, text: str) -> Kind:
    """先頭の行の多数決で種類を決める。判定できない行が多ければ `DictionaryFileError`。"""
    counts: dict[Kind | None, int] = {}
    sampled = 0
    try:
        for row in csv.reader(io.StringIO(text)):
            if not row or not any(cell.strip() for cell in row):
                continue
            kind = _row_kind(row)
            counts[kind] = counts.get(kind, 0) + 1
            sampled += 1
            if sampled >= DETECT_SAMPLE_ROWS:
                break
    except csv.Error as exc:
        raise DictionaryFileError("undetermined", file=filename) from exc
    if sampled == 0:
        raise DictionaryFileError("emptyFile", file=filename)
    best = max((KIND_TAGS, KIND_TRANSLATIONS), key=lambda k: counts.get(k, 0))
    if counts.get(best, 0) / sampled < DETECT_MIN_RATIO:
        raise DictionaryFileError("undetermined", file=filename)
    return best


def read_upload(filename: str, data: bytes) -> list[DictionarySource]:
    """アップロードされた CSV か zip から、取り込む CSV とその種類を決める(ファイルは保存
    しない)。"""
    if not data:
        raise DictionaryFileError("emptyFile", file=_clean_filename(filename))
    if len(data) > UPLOAD_MAX_BYTES:
        raise DictionaryFileError("tooLarge", mb=UPLOAD_MAX_BYTES // (1024 * 1024))
    if _is_zip(filename, data):
        files = _read_zip(filename, data)
    else:
        files = [(_clean_filename(filename), data)]
    sources: list[DictionarySource] = []
    for name, raw in files:
        text = _decode(name, raw)
        sources.append(DictionarySource(filename=name, kind=detect_kind(name, text), text=text))
    return sources


# -- 行の読み取り ------------------------------------------------------------------


@dataclass(frozen=True)
class TagRow:
    name: str
    category: int | None
    post_count: int
    aliases: list[str]


@dataclass(frozen=True)
class TranslationRow:
    name: str
    translation: str
    translations: str
    terms: list[str]


def iter_tag_rows(text: str) -> Iterator[TagRow]:
    """タグの一覧の行。読めない行は飛ばす。件数は `45340.0` のような小数表記も読む。"""
    for row in csv.reader(io.StringIO(text)):
        if _row_kind(row) != KIND_TAGS:
            continue
        name = row[0].strip()
        try:
            count = int(float(row[2]))
        except (ValueError, OverflowError):
            continue
        # Integer の列に収める(実際の件数はこれより十分小さい)。
        count = max(0, min(count, 2**31 - 1))
        aliases = _split_list(row[3]) if len(row) == 4 else []
        yield TagRow(name=name, category=int(row[1]), post_count=count, aliases=aliases)


def iter_translation_rows(text: str) -> Iterator[TranslationRow]:
    """訳の行。読めない行は飛ばす。"""
    for row in csv.reader(io.StringIO(text)):
        if _row_kind(row) != KIND_TRANSLATIONS:
            continue
        terms = _split_list(row[1])
        if not terms:
            continue
        yield TranslationRow(
            name=row[0].strip(),
            translation=terms[0],
            translations=", ".join(terms),
            terms=terms,
        )


def translation_search_key(terms: Iterable[str]) -> str:
    """訳の検索用の列。`,訳1,訳2,` の形(訳の前方一致を `LIKE '%,q%'` で引く)。"""
    keys = [make_key(term) for term in terms]
    return "," + ",".join(k for k in keys if k) + ","


# -- 管理 --------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(UTC)


def list_dictionaries(db: Session) -> list[TagDictionary]:
    return list(db.scalars(select(TagDictionary).order_by(TagDictionary.created_at)))


def create_dictionary(
    db: Session,
    *,
    filename: str,
    kind: Kind,
    category_scheme: str | None,
    user_id: uuid.UUID | None,
) -> TagDictionary:
    """取り込み中(`importing`)の辞書の行を作る(コミットは呼び出し側)。"""
    dictionary = TagDictionary(
        id=uuid.uuid4(),
        filename=filename,
        kind=kind,
        category_scheme=category_scheme if kind == KIND_TAGS else None,
        row_count=0,
        enabled=True,
        status=STATUS_IMPORTING,
        created_by_user_id=user_id,
        created_at=_utcnow(),
    )
    db.add(dictionary)
    db.flush()
    return dictionary


def delete_contents(db: Session, dictionary_id: uuid.UUID) -> None:
    """辞書の中身の行を消す(辞書の行は残す)。"""
    for model in (TagDictionaryEntry, TagDictionaryAlias, TagDictionaryTranslation):
        db.execute(delete(model).where(model.dictionary_id == dictionary_id))


def delete_dictionary(db: Session, dictionary: TagDictionary) -> None:
    """辞書を物理削除する(中身の行も消す)。"""
    delete_contents(db, dictionary.id)
    db.delete(dictionary)


def recover_interrupted(db: Session) -> int:
    """起動時に、取り込み中のまま残った辞書を `failed` にし、途中まで入れた行を消す。"""
    rows = list(db.scalars(select(TagDictionary).where(TagDictionary.status == STATUS_IMPORTING)))
    for dictionary in rows:
        delete_contents(db, dictionary.id)
        dictionary.status = STATUS_FAILED
        dictionary.error = ERROR_INTERRUPTED
        dictionary.row_count = 0
        dictionary.finished_at = _utcnow()
    if rows:
        db.commit()
    return len(rows)


# -- 使う辞書 ----------------------------------------------------------------------


@dataclass(frozen=True)
class ActiveDictionaries:
    # タグの一覧の辞書の id → カテゴリーの体系
    tags: dict[uuid.UUID, str | None]
    # 訳の辞書の id(先に登録したものから)
    translations: list[uuid.UUID]


def active_dictionaries(db: Session) -> ActiveDictionaries:
    rows = db.execute(
        select(TagDictionary.id, TagDictionary.kind, TagDictionary.category_scheme)
        .where(TagDictionary.status == STATUS_READY, TagDictionary.enabled.is_(True))
        .order_by(TagDictionary.created_at, TagDictionary.id)
    ).all()
    return ActiveDictionaries(
        tags={i: scheme for i, kind, scheme in rows if kind == KIND_TAGS},
        translations=[i for i, kind, _ in rows if kind == KIND_TRANSLATIONS],
    )


# -- 訳 ----------------------------------------------------------------------------

_LOOKUP_CHUNK = 500


def _lookup_translations_by_key(
    db: Session, keys: set[str], dictionary_ids: list[uuid.UUID]
) -> dict[str, str]:
    """検索用の形のタグ名 → 代表の訳。先に登録した辞書の訳を優先する。"""
    if not keys or not dictionary_ids:
        return {}
    rank = {d: i for i, d in enumerate(dictionary_ids)}
    best: dict[str, tuple[int, str]] = {}
    ordered = sorted(keys)
    for start in range(0, len(ordered), _LOOKUP_CHUNK):
        chunk = ordered[start : start + _LOOKUP_CHUNK]
        rows = db.execute(
            select(
                TagDictionaryTranslation.dictionary_id,
                TagDictionaryTranslation.name_key,
                TagDictionaryTranslation.translation,
            ).where(
                TagDictionaryTranslation.dictionary_id.in_(dictionary_ids),
                TagDictionaryTranslation.name_key.in_(chunk),
            )
        ).all()
        for dictionary_id, key, translation in rows:
            r = rank[dictionary_id]
            current = best.get(key)
            if current is None or r < current[0]:
                best[key] = (r, translation)
    return {key: translation for key, (_, translation) in best.items()}


def lookup_translations(db: Session, names: Iterable[str]) -> dict[str, str]:
    """タグ名(空白でも `_` でもよい)→ 代表の訳。訳の無いタグは含めない。キーは渡した名前のまま。"""
    names = list(dict.fromkeys(names))
    if not names:
        return {}
    active = active_dictionaries(db)
    if not active.translations:
        return {}
    keys = {name: query_key(name) for name in names}
    found = _lookup_translations_by_key(db, {k for k in keys.values() if k}, active.translations)
    return {name: found[key] for name, key in keys.items() if key in found}


# -- 候補 --------------------------------------------------------------------------

MatchKind = Literal["name", "alias", "translation"]


@dataclass(frozen=True)
class DictionarySuggestion:
    name: str
    count: int
    category: int | None
    category_scheme: str | None
    translation: str | None
    match: MatchKind
    matched: str | None


@dataclass
class _Hit:
    key: str
    name: str
    count: int
    category: int | None
    dictionary_id: uuid.UUID
    match: MatchKind
    matched: str | None


def _entry_columns():  # noqa: ANN202
    return (
        TagDictionaryEntry.name_key,
        TagDictionaryEntry.name,
        TagDictionaryEntry.post_count,
        TagDictionaryEntry.category,
        TagDictionaryEntry.dictionary_id,
    )


def _matched_term(translations: str, needle: str, prefix: bool) -> str | None:
    for term in _split_list(translations):
        key = make_key(term)
        if key.startswith(needle) if prefix else needle in key:
            return term
    return None


def suggest(db: Session, q: str, limit: int = SUGGESTION_MAX) -> list[DictionarySuggestion] | None:
    """辞書での候補。有効なタグの一覧の辞書が無ければ None(呼び出し側は従来の候補に戻る)。

    順は、タグ名の前方一致 → 別名の前方一致 → 訳の前方一致 → 訳の部分一致。同じ段の中は件数の
    多い順。同じタグは先の段(同じ段なら件数の多い方)の1件だけ。
    """
    active = active_dictionaries(db)
    if not active.tags:
        return None
    needle = query_key(q)
    if not needle:
        return []
    limit = max(1, min(limit, SUGGESTION_MAX))
    tag_ids = list(active.tags)
    # 辞書が複数あると同じタグが重なるので、その分多めに取る。
    fetch = limit * len(tag_ids)

    hits: list[_Hit] = []
    seen: set[str] = set()

    def take(candidates: list[_Hit]) -> None:
        candidates.sort(key=lambda h: (-h.count, h.key))
        for hit in candidates:
            if len(hits) >= limit:
                return
            if hit.key in seen:
                continue
            seen.add(hit.key)
            hits.append(hit)

    # 1. タグ名の前方一致
    rows = db.execute(
        select(*_entry_columns())
        .where(
            TagDictionaryEntry.dictionary_id.in_(tag_ids),
            _prefix_clause(TagDictionaryEntry.name_key, needle),
        )
        .order_by(TagDictionaryEntry.post_count.desc())
        .limit(fetch)
    ).all()
    take([_Hit(k, n, c, cat, d, "name", None) for k, n, c, cat, d in rows])

    # 2. 別名の前方一致
    if len(hits) < limit:
        rows = db.execute(
            select(*_entry_columns(), TagDictionaryAlias.alias)
            .join(
                TagDictionaryEntry,
                and_(
                    TagDictionaryEntry.dictionary_id == TagDictionaryAlias.dictionary_id,
                    TagDictionaryEntry.name_key == TagDictionaryAlias.name_key,
                ),
            )
            .where(
                TagDictionaryAlias.dictionary_id.in_(tag_ids),
                _prefix_clause(TagDictionaryAlias.alias_key, needle),
            )
            .order_by(TagDictionaryEntry.post_count.desc())
            .limit(fetch + len(seen))
        ).all()
        take([_Hit(k, n, c, cat, d, "alias", a) for k, n, c, cat, d, a in rows])

    # 3・4. 訳の前方一致、部分一致(訳の辞書の名前で、タグの一覧の辞書の行を引く)
    for prefix in (True, False):
        if len(hits) >= limit or not active.translations:
            break
        pattern = "," + needle if prefix else needle
        rows = db.execute(
            select(*_entry_columns(), TagDictionaryTranslation.translations)
            .join(
                TagDictionaryEntry,
                TagDictionaryEntry.name_key == TagDictionaryTranslation.name_key,
            )
            .where(
                TagDictionaryTranslation.dictionary_id.in_(active.translations),
                TagDictionaryEntry.dictionary_id.in_(tag_ids),
                TagDictionaryTranslation.search_key.contains(pattern, autoescape=True),
            )
            .order_by(TagDictionaryEntry.post_count.desc())
            .limit((fetch + len(seen)) * len(active.translations))
        ).all()
        take(
            [
                _Hit(k, n, c, cat, d, "translation", _matched_term(tr, needle, prefix))
                for k, n, c, cat, d, tr in rows
            ]
        )

    translations = _lookup_translations_by_key(db, {h.key for h in hits}, active.translations)
    return [
        DictionarySuggestion(
            name=display_name(h.name),
            count=h.count,
            category=h.category,
            category_scheme=active.tags.get(h.dictionary_id),
            translation=translations.get(h.key),
            match=h.match,
            matched=h.matched,
        )
        for h in hits
    ]
