"""センシティブなタグの判定(ADR-0056「名前の候補」)。

外部の接続先へタグを送るときに、性的な内容を表すタグを外すために使う。レーティングの引き上げの
表(ADR-0049、`content_rating.BOOST_RULES`)の x 以上のタグと、同じ系統の語を含むタグを
センシティブとみなす。r 段階のタグ(水着など)はセンシティブにしない。

判定は語の単位で行う。タグを空白・`_`・括弧・`-` などで区切った語のどれかが一覧の語に一致すれば
真(大文字小文字は区別しない)。`cumulonimbus` や `class` のように、一覧の語を部分に含むだけの
語は一致しない。区切らずに1語に詰めて書く複合語は、別の一覧で一致させる。
"""

from __future__ import annotations

import re

from app.domain import content_rating

# 語の単位で一致させる語。
_WORDS: frozenset[str] = frozenset(
    {
        "nipples",
        "nipple",
        "areola",
        "areolae",
        "topless",
        "bottomless",
        "nude",
        "nudity",
        "naked",
        "pussy",
        "vagina",
        "penis",
        "testicles",
        "anus",
        "clitoris",
        "erection",
        "pubic",
        "genitals",
        "sex",
        "vaginal",
        "anal",
        "oral",
        "fellatio",
        "cunnilingus",
        "paizuri",
        "handjob",
        "footjob",
        "masturbation",
        "rape",
        "cum",
        "semen",
        "ejaculation",
        "bukkake",
        "orgasm",
        "ahegao",
        "dildo",
        "vibrator",
        "hetero",
        "yaoi",
        "yuri",
        "futanari",
        "bdsm",
        "bondage",
        "lactation",
        "censored",
        "uncensored",
        "nsfw",
        "explicit",
        "questionable",
    }
)

# 1語に詰めて書く複合語(区切った語、またはタグ全体(区切りを `_` にそろえたもの)で一致させる)。
_COMPOUNDS: frozenset[str] = frozenset({"cumdrip", "cumshot", "creampie", "sex_toy"})

_X_OR_ABOVE = frozenset({content_rating.LEVEL_X, content_rating.LEVEL_XX, content_rating.LEVEL_XXX})


def _canonical(tag: str) -> str:
    """大文字小文字と区切り(空白・`-`)をそろえた形(`completely nude` → `completely_nude`)。"""
    return re.sub(r"[\s\-]+", "_", tag.strip().lower())


# レーティングの引き上げの表の x 以上のタグ(タグ全体で一致させる。語の一覧からも外れないように)。
_BOOST_X_TAGS: frozenset[str] = frozenset(
    _canonical(tag)
    for rule in content_rating.BOOST_RULES
    if rule.level in _X_OR_ABOVE
    for tag in rule.tags
)

_SPLIT = re.compile(r"[\s_()\[\]{}\-:,./]+")


def is_sensitive(tag: str) -> bool:
    """センシティブなタグか。空のタグは偽。"""
    canonical = _canonical(tag)
    if not canonical:
        return False
    if canonical in _BOOST_X_TAGS or canonical in _COMPOUNDS:
        return True
    return any(word in _WORDS or word in _COMPOUNDS for word in _SPLIT.split(canonical) if word)


__all__ = ["is_sensitive"]
