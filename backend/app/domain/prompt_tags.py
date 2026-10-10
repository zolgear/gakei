"""プロンプトをタグで編集するための、候補と画像のタグ(ADR-0039)。

- 候補(`suggest_tags`): WD Tagger の語彙(count の多い順)と、GAKEI のタグのうち英数字の
  もの(本人に見える Asset のタグだけ。ADR-0025)を合わせ、前方一致を先に、次に部分一致を出す。
  同じ一致の中では語彙を先に(count の多い順)、続けて語彙に無い GAKEI のタグ(件数の多い順)。
- 画像のタグ(`prompt_tags_for_asset`): 人が消したもの(removed)を除き、語彙にあるものを
  score の高い順に、続けて score の無い(人が付けた)語彙にあるタグを付けた順に並べる。
  語彙が無い環境では、語彙の代わりに英数字だけのタグを使う。

どちらも名前はタグ名のまま(`_` は空白、括弧はエスケープしない)返す。プロンプトに入れるときの
括弧のエスケープは、送り先のフォームで決まるのでクライアントが行う。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.annotation.tag_vocabulary import TagVocabulary
from app.domain.annotations import list_tags, normalize_tag_name_or_none
from app.domain.models import AssetTag, Tag

if TYPE_CHECKING:
    from app.auth.identity import CurrentUser

SUGGESTION_MAX = 20
# GAKEI のタグは部分一致で多めに取り、英数字のものに絞ってから並べる。
_GAKEI_FETCH = 200


def is_prompt_tag_name(name: str) -> bool:
    """英数字(ASCII の印字できる文字)だけのタグか。日本語のタグ(訳や VLM)を除くため。"""
    return bool(name) and name.isascii() and name.isprintable()


def _normalize_query(q: str) -> str | None:
    """候補の検索語。タグ名と同じ正規化をし、`_` は空白、括弧のエスケープは外して比べる。"""
    cleaned = q.replace("\\(", "(").replace("\\)", ")").replace("_", " ")
    return normalize_tag_name_or_none(cleaned)


@dataclass(frozen=True)
class Suggestion:
    name: str
    source: Literal["vocabulary", "tag"]
    count: int


def suggest_tags(
    db: Session,
    user: CurrentUser,
    vocabulary: TagVocabulary | None,
    q: str,
    limit: int = SUGGESTION_MAX,
) -> list[Suggestion]:
    needle = _normalize_query(q)
    if needle is None:
        return []
    limit = max(1, min(limit, SUGGESTION_MAX))

    vocab_prefix: list[Suggestion] = []
    vocab_partial: list[Suggestion] = []
    if vocabulary is not None:
        for entry in vocabulary.entries:
            if len(vocab_prefix) >= limit:
                break
            if entry.name.startswith(needle):
                vocab_prefix.append(Suggestion(entry.name, "vocabulary", entry.count))
            elif len(vocab_partial) < limit and needle in entry.name:
                vocab_partial.append(Suggestion(entry.name, "vocabulary", entry.count))

    seen = {s.name for s in vocab_prefix} | {s.name for s in vocab_partial}
    tag_prefix: list[Suggestion] = []
    tag_partial: list[Suggestion] = []
    # list_tags は件数の多い順。語彙にあるもの(上で出したもの)は重ねない。
    for name, n in list_tags(db, user, needle, _GAKEI_FETCH):
        if name in seen or not is_prompt_tag_name(name):
            continue
        if vocabulary is not None and name in vocabulary:
            # 語彙にあるのに上で出ていない = 件数の上限で切れたもの。順位は語彙に従う。
            continue
        target = tag_prefix if name.startswith(needle) else tag_partial
        target.append(Suggestion(name, "tag", n))

    return (vocab_prefix + tag_prefix + vocab_partial + tag_partial)[:limit]


def prompt_tags_for_asset(
    db: Session, asset_id: uuid.UUID, vocabulary: TagVocabulary | None
) -> list[str]:
    rows = db.execute(
        select(Tag.name, AssetTag.score, AssetTag.created_at)
        .join(Tag, Tag.id == AssetTag.tag_id)
        .where(AssetTag.asset_id == asset_id, AssetTag.removed.is_(False))
    ).all()
    picked = [
        (name, score, created_at)
        for name, score, created_at in rows
        if (name in vocabulary if vocabulary is not None else is_prompt_tag_name(name))
    ]
    # score のあるもの(WD Tagger)を高い順に、続けて score の無いもの(人)を付けた順に。
    picked.sort(
        key=lambda r: (
            r[1] is None,
            -(r[1] or 0.0),
            r[2],
            r[0],
        )
    )
    return [name for name, _, _ in picked]
