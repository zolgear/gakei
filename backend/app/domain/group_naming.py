"""グループ名の候補(ADR-0056「名前の候補」)。LLM への文面、応答の JSON のスキーマ、応答の読み取り、
Fake の応答。

- 送るのはタグだけ。集まりごとの特徴的なタグ(英語の元の表記)と、集まりの中・対象全体での
  出現率、作品・キャラクターのタグの印(タグ辞書のカテゴリー 3・4)、作品の集計1行。画像、
  プロンプト、自動タイトル、既存のグループの名前は送らない。
- 接続先が外部(`locality = external`)なら、送るタグからセンシティブなタグ
  (`sensitive_tags.is_sensitive`)を外す。ローカルなら外さない。外した分は次のタグで埋める。
- 表示中の提案(新しいグループの提案)をまとめて1回で送り、集まりごとに候補を3つ返させる。
- 候補は人が選んで直す前提。自動では確定しない。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.domain.group_suggestions import (
    NamingCluster,
    NamingInput,
    distinctive_tags,
    franchise_of_tag,
)
from app.domain.sensitive_tags import is_sensitive
from app.i18n import t

SCHEMA_NAME = "gakei_group_naming"
REASONING_EFFORT = "low"

# 集まりごとに送る特徴的なタグの数(作品・キャラクターのタグは別に足す)。
TAG_LIMIT = 15
# 特徴的なタグに入らなくても足す、作品・キャラクターのタグの条件と数。
EXTRA_MIN_RATE = 0.1
EXTRA_LIMIT = 5
# 作品の集計の行に載せる条件と数。
FRANCHISE_LINE_MIN_RATE = 0.1
FRANCHISE_LINE_LIMIT = 3
# 候補の数と長さの上限(超える候補は捨てる)。
CANDIDATE_COUNT = 3
CANDIDATE_MAX_LENGTH = 30
# 一度に送る集まりの数の上限。
MAX_CLUSTERS = 100

_CATEGORY_MARKS = {3: "作品", 4: "キャラ"}

INSTRUCTIONS = "あなたは画像管理ツールの整理を手伝うアシスタントです。"

_PROMPT = """\
画像生成ツールの利用者が、画像をグループ(フォルダ)に整理しています。
画像の埋め込みで見つけた複数の集まりについて、それぞれ全体と比べて多く出るタグ(英語)を示します。\
[作品] [キャラ] はタグ辞書で作品名・キャラクター名と分かっているタグです。

{blocks}

それぞれの集まりに、人が付けそうなグループ名を付けてください。
- タグを訳して並べた名前にしない。作品名・キャラクター・テーマ・シーン・衣装・画風・用途など、\
人が整理に使う観点で1つにまとめる。
- 作品やキャラクターのタグが集まりの大半に付いているなら使ってよい。一部だけなら使わない。
- 集まり同士を見分けられる名前にする(同じ名前を付けない)。
- 2〜14文字程度の日本語。各集まりに候補を3つ、良い順に。"""

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "clusters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "candidates": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "candidates"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["clusters"],
    "additionalProperties": False,
}


def _percent(rate: float) -> str:
    return f"{rate:.0%}"


@dataclass(frozen=True)
class _TagLine:
    tag: str
    category: int | None
    rate: float
    global_rate: float


def _tag_lines(
    cluster: NamingCluster, naming: NamingInput, allowed: Callable[[str], bool]
) -> list[_TagLine]:
    """送るタグ。特徴的なタグの上位 TAG_LIMIT 個と、そこに入らない作品・キャラクターのタグ。"""
    scores = distinctive_tags(
        cluster.tag_counts,
        cluster.size,
        naming.global_counts,
        naming.total,
        limit=len(cluster.tag_counts),
    )
    lines = [
        _TagLine(s.tag, naming.categories.get(s.tag), s.rate, s.global_rate)
        for s in scores
        if allowed(s.tag)
    ][:TAG_LIMIT]
    seen = {line.tag for line in lines}
    extras: list[_TagLine] = []
    if cluster.size > 0 and naming.total > 0:
        for tag, count in cluster.tag_counts.items():
            category = naming.categories.get(tag)
            rate = count / cluster.size
            if (
                tag in seen
                or category not in _CATEGORY_MARKS
                or rate < EXTRA_MIN_RATE
                or not allowed(tag)
            ):
                continue
            global_rate = naming.global_counts.get(tag, count) / naming.total
            extras.append(_TagLine(tag, category, rate, global_rate))
    extras.sort(key=lambda line: (-line.rate, line.tag))
    return lines + extras[:EXTRA_LIMIT]


def _franchise_line(
    cluster: NamingCluster, naming: NamingInput, allowed: Callable[[str], bool]
) -> str | None:
    """作品の集計(キャラクターのタグの括弧書きと作品のタグを、画像ごとに1回数える)。"""
    if cluster.size <= 0:
        return None
    counts: Counter[str] = Counter()
    for tags in cluster.member_tags:
        names = set()
        for tag in tags:
            if not allowed(tag):
                continue
            name = franchise_of_tag(tag, naming.categories.get(tag), tag in naming.categories)
            if name and allowed(name):
                names.add(name)
        counts.update(names)
    rows = [
        (name, count / cluster.size)
        for name, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        if count / cluster.size >= FRANCHISE_LINE_MIN_RATE
    ][:FRANCHISE_LINE_LIMIT]
    if not rows:
        return None
    joined = ", ".join(f"{name} {_percent(rate)}" for name, rate in rows)
    return f"作品(キャラクター・作品のタグから、画像ごとに集計): {joined}"


def _block(number: int, cluster: NamingCluster, naming: NamingInput, external: bool) -> str:
    def allowed(tag: str) -> bool:
        return not (external and is_sensitive(tag))

    lines = [f"### 集まり {number}({cluster.size}枚)"]
    for line in _tag_lines(cluster, naming, allowed):
        mark = _CATEGORY_MARKS.get(line.category) if line.category is not None else None
        label = f"{line.tag} [{mark}]" if mark else line.tag
        lines.append(f"- {label}: 集まり {_percent(line.rate)} / 全体 {_percent(line.global_rate)}")
    franchise = _franchise_line(cluster, naming, allowed)
    if franchise:
        lines.append(franchise)
    return "\n".join(lines)


def build_input(naming: NamingInput, *, external: bool) -> str:
    """LLM へ送る文面。集まりの番号は 1 から `naming.clusters` の順。`external` なら
    センシティブなタグを外す。"""
    blocks = [
        _block(i, cluster, naming, external) for i, cluster in enumerate(naming.clusters, start=1)
    ]
    return _PROMPT.format(blocks="\n\n".join(blocks))


def _clean(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text or len(text) > CANDIDATE_MAX_LENGTH:
        return None
    return text


def interpret(data: Any, count: int) -> dict[int, list[str]]:
    """応答を読む。集まりの番号(1〜count)→ 候補(空白を整え、空・重複・長すぎるものを除き、
    最大 CANDIDATE_COUNT 個)。番号が範囲外・重複したもの、形の合わないものは捨てる。"""
    items = data.get("clusters") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return {}
    result: dict[int, list[str]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        number = item.get("id")
        if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= count:
            continue
        if number in result:
            continue
        raw = item.get("candidates")
        if not isinstance(raw, list):
            continue
        candidates: list[str] = []
        for value in raw:
            text = _clean(value)
            if text is not None and text not in candidates:
                candidates.append(text)
            if len(candidates) >= CANDIDATE_COUNT:
                break
        if candidates:
            result[number] = candidates
    return result


def fake_response(count: int) -> dict[str, Any]:
    """LLM の応答と同じ形の、決まった候補(FAKE_PROVIDER=1)。"""
    return {
        "clusters": [
            {
                "id": i,
                "candidates": [
                    t("groupNaming.fakeName", cluster=i, n=n) for n in range(1, CANDIDATE_COUNT + 1)
                ],
            }
            for i in range(1, count + 1)
        ]
    }


__all__ = [
    "INSTRUCTIONS",
    "MAX_CLUSTERS",
    "RESPONSE_SCHEMA",
    "SCHEMA_NAME",
    "build_input",
    "fake_response",
    "interpret",
]
