"""VLM での四角の検出(ADR-0053 7章)。指示、応答の JSON のスキーマ、応答の読み取り、Fake の応答。

- モデルには座標を 0〜1000 の整数で、左上を原点にした `[x0, y0, x1, y1]` で答えさせる。
- 読み取りでは、範囲外の値・空の四角・形の合わない要素を捨て、同じ名前と四角の重複を除き、
  上限の数で切る。残った四角は 3章の形(0〜1 の割合、左上が原点の `[x0, y0, x1, y1]`)に直す。
- 縦に長い物の下の端が短くなりがちだが、四角を自動で広げることはしない(候補は手で直す前提)。
- 検出の結果は保存しない。
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any, Literal

from PIL import Image

from app.annotation.engines import image_to_jpeg
from app.i18n import t, use_locale

SCHEMA_NAME = "gakei_region_detection"

# モデルに答えさせる座標の範囲(0〜COORD_MAX)。
COORD_MAX = 1000
# 挙げる数の上限(ADR-0053 7章)。
MAX_ITEMS = 20
# 探す言葉の長さの上限。
QUERY_MAX = 200
# 名前の長さの上限(超えたら切る)。
LABEL_MAX = 100
# VLM に送る画像の長辺。小さな物も見えるよう、自動タイトル・タグ(1024px)より大きくする。
IMAGE_MAX_SIDE = 1536
# 画像の細かさの指定(OpenAI の `detail`)。
IMAGE_DETAIL: Literal["high"] = "high"
REASONING_EFFORT = "low"

_LANGUAGES = {"ja": "日本語", "en": "英語"}

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "objects": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "box": {
                        "type": "array",
                        "description": "[x0, y0, x1, y1], integers 0-1000, top-left origin",
                        "items": {"type": "integer"},
                    },
                },
                "required": ["label", "box"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["objects"],
    "additionalProperties": False,
}

_INSTRUCTIONS = """\
あなたは画像の中の物の位置を答える。添えられた1枚の画像を見て、物ごとに名前と四角(バウンディング\
ボックス)を JSON で返す。

- 座標は画像の幅と高さをそれぞれ 0〜1000 とした整数で、左上を原点にした [x0, y0, x1, y1] \
(x0 < x1、y0 < y1)。
- 四角は物の全体を囲む。細い部分・長い部分(紐、しっぽ、脚、棒、鉢の下の端など)も切らずに含める。\
特に縦に長い物は、下の端まで含める。
- 名前(label)は{language}で、短く(数語まで)。同じ種類の物が複数あるときは、区別できる名前にする\
(「左の人」「右の人」など)。
- 挙げるのは多くても {max_items} 個。
{target}"""

_TARGET_ALL = "- 画像の主な物を、目立つ順に一通り挙げる。細かすぎる部品は挙げない。"
_TARGET_QUERY = (
    "- 利用者が探している物だけを挙げる(下の「探す物」)。当てはまる物が複数あれば、それぞれを"
    "挙げる。見つからなければ空の配列を返す。"
)


def instructions(locale: str, *, query: str | None, max_items: int) -> str:
    """VLM への指示。名前の言語を画面の言語に合わせる。"""
    return _INSTRUCTIONS.format(
        language=_LANGUAGES.get(locale, "日本語"),
        max_items=max_items,
        target=_TARGET_QUERY if query else _TARGET_ALL,
    )


def build_input(query: str | None) -> str:
    """指示に添える文(画像はこの後ろに添える)。"""
    if query:
        return f"探す物: {query}"
    return "画像の主な物を挙げてください。"


def prepare_image(data: bytes) -> bytes:
    """VLM に送る画像。長辺 IMAGE_MAX_SIDE 以内に縮め、透過は白で埋めて JPEG にする。"""
    with Image.open(io.BytesIO(data)) as image:
        return image_to_jpeg(image, IMAGE_MAX_SIDE)


@dataclass(frozen=True)
class DetectedBox:
    label: str
    # 0〜1 の割合、左上が原点の [x0, y0, x1, y1]。
    box: tuple[float, float, float, float]


def _coords(value: Any) -> tuple[int, int, int, int] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    result: list[int] = []
    for item in value:
        # 整数で答えさせている。小数で返った値は整数に丸める(真偽値は数ではない)。
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            return None
        if isinstance(item, float):
            if item != item:  # NaN
                return None
            item = round(item)
        if not 0 <= item <= COORD_MAX:
            return None
        result.append(int(item))
    x0, y0, x1, y1 = result
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1, y1


def interpret(data: Any, max_items: int) -> list[DetectedBox]:
    """応答を読み、使える四角だけを返す(順序は応答のまま)。"""
    objects = data.get("objects") if isinstance(data, dict) else None
    if not isinstance(objects, list):
        return []
    result: list[DetectedBox] = []
    seen: set[tuple[str, tuple[int, int, int, int]]] = set()
    for item in objects:
        if len(result) >= max_items:
            break
        if not isinstance(item, dict):
            continue
        label = item.get("label")
        if not isinstance(label, str) or not label.strip():
            continue
        label = label.strip()[:LABEL_MAX]
        coords = _coords(item.get("box"))
        if coords is None:
            continue
        key = (label, coords)
        if key in seen:
            continue
        seen.add(key)
        x0, y0, x1, y1 = (c / COORD_MAX for c in coords)
        result.append(DetectedBox(label=label, box=(x0, y0, x1, y1)))
    return result


# Fake の検出(FAKE_PROVIDER=1)の決まった四角(0〜1000)。
_FAKE_BOXES: tuple[list[int], ...] = (
    [120, 80, 520, 960],
    [600, 560, 760, 760],
    [580, 60, 940, 420],
)
_FAKE_QUERY_BOXES: tuple[list[int], ...] = ([150, 150, 450, 550], [550, 300, 850, 700])


def _fake_labels() -> tuple[str, ...]:
    return (
        t("regionDetection.fake.person"),
        t("regionDetection.fake.cup"),
        t("regionDetection.fake.window"),
    )


def fake_response(locale: str, *, query: str | None, max_items: int) -> dict[str, Any]:
    """VLM の応答と同じ形の、決まった検出。言葉があれば、その言葉を名前にした四角を返す。"""
    if query:
        objects = [{"label": query, "box": list(box)} for box in _FAKE_QUERY_BOXES]
    else:
        with use_locale(locale):  # type: ignore[arg-type]
            labels = _fake_labels()
        objects = [
            {"label": label, "box": list(box)}
            for label, box in zip(labels, _FAKE_BOXES, strict=True)
        ]
    return {"objects": objects[:max_items]}
