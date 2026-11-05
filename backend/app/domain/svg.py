"""SVG の原本(ADR-0062)。GPT Image 2.6 の Generate が出力する静的なベクター画像。

- 取り込みで無害化する(`sanitize`)。API はスクリプト・外部の参照・埋め込みのラスター画像を
  含まないと説明しているが、それに頼らず、許す要素と属性だけを残す。DTD(実体の定義)を持つ
  文書は断る。
- 派生(サムネイル・カード・プレビュー)と、画素を読む処理(焦点、埋め込み、自動タグ)は、
  `rasterize` で描いた画像を使う。描画は resvg(MIT)。
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET

from PIL import Image

SVG_MIME = "image/svg+xml"
SVG_EXT = "svg"
SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"

# 原本の大きさの上限(バイト)。アイコンやロゴは数 KB〜数百 KB に収まる。
MAX_SVG_BYTES = 8 * 1024 * 1024
# 大きさの属性が無い・読めないときの大きさ(API の出力は width / height を持つ)。
DEFAULT_SIZE = 1024
# 描くときの長辺の上限(派生の最大の preview と同じ)。
MAX_RASTER_LONG_EDGE = 2048

# 残す要素(静的な図形・文字・塗り・効果)。これ以外(script、foreignObject、image、a、
# animate 系、use の外部参照など)は子ごと除く。
_ALLOWED_ELEMENTS = frozenset(
    {
        "svg", "g", "defs", "title", "desc", "metadata", "symbol", "use",
        "path", "rect", "circle", "ellipse", "line", "polyline", "polygon",
        "text", "tspan", "textPath",
        "linearGradient", "radialGradient", "stop", "pattern", "clipPath", "mask", "marker",
        "filter", "feBlend", "feColorMatrix", "feComponentTransfer", "feComposite",
        "feConvolveMatrix", "feDiffuseLighting", "feDisplacementMap", "feDistantLight",
        "feDropShadow", "feFlood", "feFuncA", "feFuncB", "feFuncG", "feFuncR",
        "feGaussianBlur", "feMerge", "feMergeNode", "feMorphology", "feOffset",
        "fePointLight", "feSpecularLighting", "feSpotLight", "feTile", "feTurbulence",
        "style",
    }
)  # fmt: skip

# 文書の中を指す参照だけを許す(`#id`)。
_LOCAL_REF = re.compile(r"^\s*#")
# CSS の中の外部の参照(`url(` の先が `#` で始まらないもの)と `@import`。
_EXTERNAL_CSS = re.compile(r"url\(\s*['\"]?\s*(?!#)|@import|expression\(", re.IGNORECASE)


class SvgError(ValueError):
    """SVG として受け付けられない。"""


def looks_like_svg(data: bytes) -> bool:
    """先頭が XML で、`<svg` を含むか(形式の判定。中身の検証は `sanitize`)。"""
    head = data[:4096].lstrip(b"\xef\xbb\xbf \t\r\n")
    return head.startswith(b"<") and b"<svg" in head


def _local(tag: str) -> tuple[str | None, str]:
    if tag.startswith("{"):
        ns, _, name = tag[1:].partition("}")
        return ns, name
    return None, tag


def _clean(element: ET.Element) -> None:
    for child in list(element):
        if not isinstance(child.tag, str):
            # コメントと処理命令は残さない。
            element.remove(child)
            continue
        ns, name = _local(child.tag)
        if ns not in (SVG_NS, None) or name not in _ALLOWED_ELEMENTS:
            element.remove(child)
            continue
        if name == "style" and child.text and _EXTERNAL_CSS.search(child.text):
            element.remove(child)
            continue
        _clean_attributes(child)
        _clean(child)


def _clean_attributes(element: ET.Element) -> None:
    for key in list(element.attrib):
        ns, name = _local(key)
        value = element.attrib[key]
        lowered = name.lower()
        if lowered.startswith("on"):
            del element.attrib[key]
        elif lowered == "href" and ns in (None, XLINK_NS):
            if not _LOCAL_REF.match(value):
                del element.attrib[key]
        elif ns not in (None, XLINK_NS, "http://www.w3.org/XML/1998/namespace"):
            del element.attrib[key]
        elif _EXTERNAL_CSS.search(value):
            del element.attrib[key]


def sanitize(data: bytes) -> bytes:
    """許す要素と属性だけを残した SVG(UTF-8)を返す。受け付けられなければ `SvgError`。"""
    if len(data) > MAX_SVG_BYTES:
        raise SvgError("too large")
    # 実体の定義(展開の爆弾、外部の実体)を持つ文書は受けない。
    if re.search(rb"<!(DOCTYPE|ENTITY)", data, re.IGNORECASE):
        raise SvgError("doctype")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise SvgError("parse") from e
    if _local(root.tag) != (SVG_NS, "svg"):
        raise SvgError("not svg")
    _clean_attributes(root)
    _clean(root)
    ET.register_namespace("", SVG_NS)
    ET.register_namespace("xlink", XLINK_NS)
    return ET.tostring(root, encoding="utf-8", xml_declaration=False)


_LENGTH = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*(px)?\s*$")


def intrinsic_size(data: bytes) -> tuple[int, int]:
    """`width` / `height`(px)か `viewBox` から大きさを決める。読めなければ既定の大きさ。"""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return DEFAULT_SIZE, DEFAULT_SIZE
    width_match = _LENGTH.match(root.attrib.get("width", ""))
    height_match = _LENGTH.match(root.attrib.get("height", ""))
    if width_match and height_match:
        width, height = float(width_match.group(1)), float(height_match.group(1))
    else:
        parts = re.split(r"[\s,]+", root.attrib.get("viewBox", "").strip())
        try:
            width, height = float(parts[2]), float(parts[3])
        except (IndexError, ValueError):
            return DEFAULT_SIZE, DEFAULT_SIZE
    if width <= 0 or height <= 0:
        return DEFAULT_SIZE, DEFAULT_SIZE
    return max(1, round(width)), max(1, round(height))


def rasterize(data: bytes, long_edge: int = MAX_RASTER_LONG_EDGE) -> Image.Image:
    """SVG を透過の RGBA 画像に描く(長辺は `long_edge` 以下。小さい SVG は拡大しない)。"""
    import resvg_py

    width, height = intrinsic_size(data)
    scale = min(1.0, long_edge / max(width, height))
    target = (max(1, round(width * scale)), max(1, round(height * scale)))
    try:
        png = resvg_py.svg_to_bytes(
            svg_string=data.decode("utf-8"), width=target[0], height=target[1]
        )
    except (ValueError, UnicodeDecodeError) as e:
        raise SvgError("render") from e
    image = Image.open(io.BytesIO(bytes(png)))
    image.load()
    return image
