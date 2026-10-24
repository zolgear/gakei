"""センシティブなタグの判定(`app/domain/sensitive_tags.py`。ADR-0056「名前の候補」)。

語の単位で一致させること、一部に含むだけの語は一致しないこと、1語に詰めた複合語、レーティングの
引き上げの表(ADR-0049)の x 以上のタグを全部含み、r 段階のタグを含まないことを確かめる。
"""

from __future__ import annotations

import pytest

from app.domain import content_rating
from app.domain.sensitive_tags import is_sensitive


@pytest.mark.parametrize(
    "tag",
    [
        "nipples",
        "Nude",
        "completely nude",
        "completely_nude",
        "cum on body",
        "after-sex",
        "sex (act)",
        "NSFW",
        "cumdrip",
        "cumshot",
        "creampie",
        "sex_toy",
        "sex toy",
        "group_sex",
        "cum_in_pussy",
        "yuri",
        "bondage",
    ],
)
def test_is_sensitive_matches_words(tag: str) -> None:
    assert is_sensitive(tag)


@pytest.mark.parametrize(
    "tag",
    [
        "cumulonimbus",
        "class",
        "classroom",
        "analog clock",
        "sexy",
        "nudes_(food)",
        "swimsuit",
        "bikini",
        "underwear",
        "cleavage",
        "1girl",
        "aru (blue archive)",
        "",
        "  ",
    ],
)
def test_is_sensitive_avoids_false_positives(tag: str) -> None:
    assert not is_sensitive(tag)


def test_is_sensitive_includes_rating_boost_x_and_above() -> None:
    above = {content_rating.LEVEL_X, content_rating.LEVEL_XX, content_rating.LEVEL_XXX}
    for rule in content_rating.BOOST_RULES:
        for tag in rule.tags:
            if rule.level in above:
                assert is_sensitive(tag), tag
                assert is_sensitive(tag.replace("_", " ")), tag
            else:
                # r 段階(水着など)は外さない。
                assert not is_sensitive(tag), tag
