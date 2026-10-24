"""領域の指示(ADR-0053)。四角を「画像と文」(5章)に変える。

- 四角を描いた画像: 主たる入力に、四角と番号だけを重ねた別の画像(PNG)。Asset の種類 `regions`
  として保存し、Run の入力(`role = image`)として利用者の入力の後ろに足す。入力画像の無い Run では、
  出力の大きさの白紙に描いた配置図を唯一の入力にする(`render_layout`)。
- 文: 番号ごとの指示を、利用者の言語の文言(`regions.prompt.*`)でプロンプトの後ろに足す。

どちらも同じ入力からは同じ結果になる(再実行・テストで同じ画像と文を作れるように)。DB と保存先には
触れない(呼び出し側の `run_create` が検証と保存を受け持つ)。
"""

from __future__ import annotations

import io
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from app.domain.schemas import RegionBox
from app.i18n import t

# `run.regions` の変換の版。四角を画像と文に変える作り方を変えたら上げる。
VERSION = 1
DELIVERY_IMAGE_AND_TEXT = "image_and_text"
# ComfyUI の Create Bounding Boxes に差し込む(ADR-0053 6章。変換は `comfy_bounding_boxes`)。
DELIVERY_COMFYUI_BBOXES = "comfyui_bboxes"
# 入力画像の無い Run の配置図の大きさ(出力サイズが `auto`・未指定のとき。フロントの白紙の
# キャンバスと同じ規則。ADR-0053 5章)。
BLANK_AUTO_SIZE = (1024, 1024)

# 四角の色(番号順に使い、9 個目からは繰り返す。番号で見分ける)。`key` は文言のキー
# (`regions.colors.{key}`)。互いに見分けやすく、写真・イラストの上でも目立つ色。
PALETTE: tuple[tuple[str, tuple[int, int, int]], ...] = (
    ("red", (230, 25, 75)),
    ("blue", (0, 130, 200)),
    ("green", (60, 180, 75)),
    ("yellow", (255, 225, 25)),
    ("magenta", (240, 50, 230)),
    ("cyan", (70, 240, 240)),
    ("orange", (245, 130, 48)),
    ("purple", (145, 30, 180)),
)


def color_for(index: int) -> tuple[str, tuple[int, int, int]]:
    """並び順(0 から)の四角の色。"""
    return PALETTE[index % len(PALETTE)]


def ordered(regions: Sequence[RegionBox]) -> list[RegionBox]:
    """番号の順に並べる(色・文・画像の番号はこの順で決める)。"""
    return sorted(regions, key=lambda r: r.number)


# -- 四角を描いた画像 -------------------------------------------------------------------


def _pixel_box(box: Sequence[float], width: int, height: int) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = box
    return (
        round(x0 * (width - 1)),
        round(y0 * (height - 1)),
        round(x1 * (width - 1)),
        round(y1 * (height - 1)),
    )


def _text_color(rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    """バッジの数字の色(明るい色には黒、暗い色には白)。"""
    r, g, b = rgb
    return (0, 0, 0) if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else (255, 255, 255)


def _rectangle(
    draw: ImageDraw.ImageDraw,
    rect: tuple[int, int, int, int],
    color: tuple[int, int, int],
    line: int,
) -> None:
    """白い縁取りを付けた太い枠(どんな下地でも見えるように)。"""
    edge = max(1, line // 3)
    x0, y0, x1, y1 = rect
    draw.rectangle(
        (x0 - edge, y0 - edge, x1 + edge, y1 + edge), outline=(255, 255, 255), width=line + 2 * edge
    )
    draw.rectangle(rect, outline=color, width=line)


def _dashed_rectangle(
    draw: ImageDraw.ImageDraw,
    rect: tuple[int, int, int, int],
    color: tuple[int, int, int],
    line: int,
) -> None:
    """点線の枠(「動かす」の元の位置)。"""
    x0, y0, x1, y1 = rect
    dash = max(line * 3, 6)
    gap = max(line * 2, 4)
    edges = (
        ((x0, y0), (x1, y0)),
        ((x1, y0), (x1, y1)),
        ((x1, y1), (x0, y1)),
        ((x0, y1), (x0, y0)),
    )
    for (ax, ay), (bx, by) in edges:
        length = math.hypot(bx - ax, by - ay)
        if length == 0:
            continue
        ux, uy = (bx - ax) / length, (by - ay) / length
        pos = 0.0
        while pos < length:
            end = min(pos + dash, length)
            start_pt = (ax + ux * pos, ay + uy * pos)
            end_pt = (ax + ux * end, ay + uy * end)
            draw.line((start_pt, end_pt), fill=(255, 255, 255), width=line + 2 * max(1, line // 3))
            draw.line((start_pt, end_pt), fill=color, width=line)
            pos = end + gap


def _arrow(
    draw: ImageDraw.ImageDraw,
    start: tuple[float, float],
    end: tuple[float, float],
    color: tuple[int, int, int],
    line: int,
) -> None:
    """元の位置の中心から移す先の中心への矢印。"""
    sx, sy = start
    ex, ey = end
    length = math.hypot(ex - sx, ey - sy)
    if length < line * 4:
        return
    ux, uy = (ex - sx) / length, (ey - sy) / length
    head = max(line * 4, 12)
    base = (ex - ux * head, ey - uy * head)
    draw.line((start, base), fill=color, width=line)
    left = (base[0] - uy * head / 2, base[1] + ux * head / 2)
    right = (base[0] + uy * head / 2, base[1] - ux * head / 2)
    draw.polygon((end, left, right), fill=color)


def _badge(
    draw: ImageDraw.ImageDraw,
    corner: tuple[int, int],
    number: int,
    color: tuple[int, int, int],
    size: int,
    bounds: tuple[int, int],
) -> None:
    """番号のバッジ。四角の左上の内側に置く(画像の外にはみ出さないように寄せる)。"""
    font = ImageFont.load_default(size=max(10, round(size * 0.7)))
    label = str(number)
    left, top, right, bottom = draw.textbbox((0, 0), label, font=font)
    text_w, text_h = right - left, bottom - top
    pad = max(2, size // 6)
    width = max(size, text_w + 2 * pad)
    height = max(size, text_h + 2 * pad)
    x = min(max(0, corner[0]), max(0, bounds[0] - width))
    y = min(max(0, corner[1]), max(0, bounds[1] - height))
    draw.rectangle(
        (x, y, x + width, y + height), fill=color, outline=(255, 255, 255), width=max(1, size // 12)
    )
    draw.text(
        (x + (width - text_w) / 2 - left, y + (height - text_h) / 2 - top),
        label,
        font=font,
        fill=_text_color(color),
    )


def render_overlay(image: Image.Image, regions: Sequence[RegionBox]) -> bytes:
    """主たる入力に、四角と番号を重ねた PNG を返す。大きさは主たる入力と同じ。

    四角の名前(`label`)は描かない。Pillow の既定のフォントには日本語の字形が無く、名前は
    足す文で番号と並べて伝える。"""
    base = image.convert("RGBA")
    canvas = Image.new("RGBA", base.size, (255, 255, 255, 255))
    canvas.alpha_composite(base)
    canvas = canvas.convert("RGB")
    width, height = canvas.size
    short = min(width, height)
    line = max(3, round(short * 0.008))
    badge = max(16, round(short * 0.045))
    draw = ImageDraw.Draw(canvas)

    for index, region in enumerate(ordered(regions)):
        _key, color = color_for(index)
        target = _pixel_box(region.box, width, height)
        if region.kind == "move" and region.source_box is not None:
            source = _pixel_box(region.source_box, width, height)
            _dashed_rectangle(draw, source, color, line)
            _arrow(
                draw,
                ((source[0] + source[2]) / 2, (source[1] + source[3]) / 2),
                ((target[0] + target[2]) / 2, (target[1] + target[3]) / 2),
                color,
                line,
            )
            _badge(
                draw,
                (source[0] + line, source[1] + line),
                region.number,
                color,
                badge,
                (width, height),
            )
        _rectangle(draw, target, color, line)
        _badge(
            draw, (target[0] + line, target[1] + line), region.number, color, badge, (width, height)
        )

    out = io.BytesIO()
    canvas.save(out, format="PNG")
    return out.getvalue()


def render_layout(width: int, height: int, regions: Sequence[RegionBox]) -> bytes:
    """入力画像の無い Run の配置図(ADR-0053 5章)。

    出力の大きさの白紙に、四角と番号だけを描いた PNG。"""
    with Image.new("RGB", (width, height), (255, 255, 255)) as blank:
        return render_overlay(blank, regions)


# -- 文 -----------------------------------------------------------------------------


def _percent(value: float) -> int:
    return round(value * 100)


def _where(box: Sequence[float]) -> str:
    x0, y0, x1, y1 = box
    return t(
        "runRegions.prompt.where",
        left=_percent(x0),
        top=_percent(y0),
        right=_percent(x1),
        bottom=_percent(y1),
    )


def _color_name(key: str) -> str:
    # 文言のキーは文字列のリテラルで書く(キーの欠けをテストで見つけるため)。
    names = {
        "red": lambda: t("runRegions.colors.red"),
        "blue": lambda: t("runRegions.colors.blue"),
        "green": lambda: t("runRegions.colors.green"),
        "yellow": lambda: t("runRegions.colors.yellow"),
        "magenta": lambda: t("runRegions.colors.magenta"),
        "cyan": lambda: t("runRegions.colors.cyan"),
        "orange": lambda: t("runRegions.colors.orange"),
        "purple": lambda: t("runRegions.colors.purple"),
    }
    return names[key]()


def _area(box: Sequence[float]) -> str:
    """名前の付いた目印(指示の無い「変える」「足す」)の文。場所だけを示す。"""
    x0, y0, x1, y1 = box
    return t(
        "runRegions.prompt.area",
        left=_percent(x0),
        top=_percent(y0),
        right=_percent(x1),
        bottom=_percent(y1),
    )


def _kind_text(region: RegionBox, *, blank: bool) -> str:
    """種類の文。「変える」「足す」で指示・描く文字・参照画像のどれも無ければ、場所だけを示す
    (四角の定義と指示は分ける。全体のプロンプトから番号か名前で指すための目印)。

    `blank` は白紙の配置図(入力画像の無い Run)。変える物が無いので、「変える」は「描く」にする。
    名前だけの四角も、白紙では名前の物を「描く」とする(目印にする相手が無いため)。"""
    kind = region.kind
    if kind == "move" and region.source_box is not None:
        return t(
            "runRegions.prompt.kind.move",
            source=_where(region.source_box),
            target=_where(region.box),
        )
    if kind == "keep":
        return t("runRegions.prompt.kind.keep", where=_where(region.box))
    if kind == "remove":
        return t("runRegions.prompt.kind.remove", where=_where(region.box))
    if not region.instruction and region.text is None and region.reference_asset_id is None:
        # 白紙では名前が描く物そのもの。場所だけの文では描かれにくかったので「描く」と伝える。
        if blank and region.label:
            return t("runRegions.prompt.kind.changeBlank", where=_where(region.box))
        return _area(region.box)
    if kind == "add":
        return t("runRegions.prompt.kind.add", where=_where(region.box))
    if blank:
        return t("runRegions.prompt.kind.changeBlank", where=_where(region.box))
    return t("runRegions.prompt.kind.change", where=_where(region.box))


def build_prompt_text(
    regions: Sequence[RegionBox],
    *,
    overlay_image_number: int,
    reference_image_numbers: dict[Any, int],
    blank: bool = False,
) -> str:
    """プロンプトの後ろに足す文(前の改行を含む)。

    `overlay_image_number` は四角を描いた画像が何枚目の入力か(1 から)。
    `reference_image_numbers` は参照画像の Asset id → 何枚目の入力か。
    `blank` は入力画像の無い Run(白紙の配置図が1枚目。ADR-0053 5章)。見出しと結びを分ける。
    """
    if blank:
        header = t("runRegions.prompt.headerBlank")
    else:
        header = t("runRegions.prompt.header", overlay=overlay_image_number)
    lines = [header]
    for index, region in enumerate(ordered(regions)):
        color_key, _rgb = color_for(index)
        color = _color_name(color_key)
        parts: list[str] = [_kind_text(region, blank=blank)]
        if region.instruction:
            parts.append(t("runRegions.prompt.instruction", instruction=region.instruction))
        if region.text is not None:
            parts.append(t("runRegions.prompt.text", text=region.text))
        if region.reference_asset_id is not None:
            number = reference_image_numbers.get(region.reference_asset_id)
            if number is not None:
                parts.append(t("runRegions.prompt.reference", image=number))
        body = " ".join(parts)
        if region.label:
            lines.append(
                t(
                    "runRegions.prompt.itemNamed",
                    number=region.number,
                    color=color,
                    label=region.label,
                    body=body,
                )
            )
        else:
            lines.append(t("runRegions.prompt.item", number=region.number, color=color, body=body))
    if blank:
        lines.append(t("runRegions.prompt.footerBlank"))
    else:
        lines.append(t("runRegions.prompt.footer", overlay=overlay_image_number))
    return "\n\n" + "\n".join(lines)


# -- 記録 ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RegionsRecord:
    """`run.regions` に書く値の材料。"""

    regions: list[RegionBox]
    appended_text: str
    overlay_asset_id: Any
    # 座標が何に対する割合か(`input`: 主たる入力、`output`: 出力の大きさ。入力画像の無い Run の
    # 白紙の配置図)。
    frame: str = "input"

    def to_json(self) -> dict[str, Any]:
        return {
            "version": VERSION,
            "delivery": DELIVERY_IMAGE_AND_TEXT,
            "regions": [r.model_dump(mode="json") for r in self.regions],
            # プロンプトの後ろに足した文。再実行では、プロンプトからこの文を除いて戻す。
            "appended_text": self.appended_text,
            "overlay_asset_id": str(self.overlay_asset_id),
            "frame": self.frame,
        }


def comfyui_record(regions: Sequence[RegionBox], *, frame: str) -> dict[str, Any]:
    """ComfyUI に差し込んだ Run の `run.regions`(ADR-0053 6章)。重ねた画像も足す文も無い。

    `frame` は座標が何に対する割合か(`input`: 主たる入力、`output`: 出力の大きさ)。
    """
    return {
        "version": VERSION,
        "delivery": DELIVERY_COMFYUI_BBOXES,
        "regions": [r.model_dump(mode="json") for r in ordered(regions)],
        "frame": frame,
    }
