"""他の画像生成ツール(Stable Diffusion WebUI 系、ComfyUI、NovelAI、InvokeAI、SwarmUI)や
C2PA(Content Credentials)が画像に埋め込んだ生成メタ情報を読む(ADR-0018)。

`kind=upload` の Asset だけが対象(呼び出し側で判定する。ここでは判定しない)。純粋関数のみで
DB にもプロバイダーにも触れない。埋め込みには署名が無く誰でも書き換えられるので、ここで読み
取った内容は自己申告として扱う(画面では常に「未検証」)。どんな入力(空バイト、ゴミ、壊れた
PNG、巨大な zlib ストリームなど)でも例外を投げず、読めなければ None を返す。

判定順は comfyui → swarmui → a1111 → novelai → invokeai → c2pa → generic。最初に当たった
ものを採用する。ツール固有の情報が当たった後(generic を含む)も C2PA のマニフェストがあれば
`params["c2pa"]` に claim generator(読めなければ "present")を添える。
"""

from __future__ import annotations

import html
import io
import json
import logging
import re
import struct
import zlib
from dataclasses import dataclass, field
from typing import Any

import cbor2
from PIL import Image, UnidentifiedImageError

from app.domain.comfy_workflow import is_api_format_template
from app.domain.embedded_meta import PNG_SIGNATURE, iter_png_chunks, split_itxt
from app.domain.schemas import EmbeddedGenerationMeta

logger = logging.getLogger(__name__)

SCHEMA = "gakei.embedded/1"
MAX_RAW_ITEM_CHARS = 64 * 1024
MAX_TOTAL_BYTES = 256 * 1024
MAX_PARAMS = 200
MAX_PARAM_VALUE_CHARS = 4 * 1024
# 一般(generic)判定は、説明系の項目が1つも無ければ None にする(ADR-0018 2章の表の注記)。
_GENERIC_REQUIRES_DESCRIPTION = True

_GAKEI_KEYWORD = "gakei"
_MAX_ZLIB_DECOMPRESS = 1024 * 1024  # 1 MiB(zTXt / 圧縮 iTXt の展開上限)

_CHECKPOINT_CLASS_TYPES = {
    "CheckpointLoaderSimple",
    "CheckpointLoader",
    "UNETLoader",
    "UnetLoaderGGUF",
    "ImageOnlyCheckpointLoader",
    "unCLIPCheckpointLoader",
    "DiffusersLoader",
}
_MODEL_NAME_KEYS = ("ckpt_name", "unet_name", "model_name")
_TEXT_VALUE_KEYS = ("text", "string", "value", "prompt")

_A1111_LINE_RE = re.compile(r'\s*(\w[\w \-/]+):\s*("(?:\\.|[^\\"])+"|[^,]*)(?:,|$)')
_XMP_DESCRIPTION_RE = re.compile(r"<dc:description>(.*?)</dc:description>", re.DOTALL)
_XMP_LI_RE = re.compile(r"<rdf:li[^>]*>(.*?)</rdf:li>", re.DOTALL)

# ConditioningZeroOut に当たったことを表す番人(negative を明示的に None にする)。
_ZERO_OUT = object()


# -- 公開関数 -----------------------------------------------------------


def extract_generation_meta(data: bytes) -> dict[str, Any] | None:
    """画像バイト列から生成メタ情報を読む。何も無ければ None。

    純粋関数で DB に触れない。どんな入力でも例外を投げない(最外で捕まえて None を返す)。
    """
    try:
        return _extract(data)
    except Exception:  # noqa: BLE001 - どんな入力でも取り込みを止めないための最終防波堤
        logger.warning("画像の生成メタ情報の解析中に例外が発生したため無視した", exc_info=True)
        return None


# -- 材料の収集 ----------------------------------------------------------


@dataclass
class _Materials:
    """解析対象の画像から集めた、加工前の材料。"""

    png_texts: dict[str, str] = field(default_factory=dict)
    c2pa_bytes: bytes | None = None
    software: str | None = None
    description: str | None = None
    user_comment: str | None = None
    artist: str | None = None
    xmp_description: str | None = None


def _is_png(data: bytes) -> bool:
    return data[:8] == PNG_SIGNATURE


def _decompress_limited(data: bytes, max_length: int = _MAX_ZLIB_DECOMPRESS) -> bytes | None:
    try:
        return zlib.decompressobj().decompress(data, max_length)
    except zlib.error:
        return None


def _parse_text_chunk(data: bytes) -> tuple[str, str] | None:
    """tEXt: `keyword\\0text`(Latin-1)。"""
    try:
        nul = data.index(b"\x00")
    except ValueError:
        return None
    keyword = data[:nul].decode("latin-1", errors="replace")
    text = data[nul + 1 :].decode("latin-1", errors="replace")
    return keyword, text


def _parse_ztxt_chunk(data: bytes) -> tuple[str, str] | None:
    """zTXt: `keyword\\0` + 圧縮方式(1バイト) + zlib データ(Latin-1)。"""
    try:
        nul = data.index(b"\x00")
    except ValueError:
        return None
    keyword = data[:nul].decode("latin-1", errors="replace")
    rest = data[nul + 1 :]
    if len(rest) < 1:
        return None
    decompressed = _decompress_limited(rest[1:])
    if decompressed is None:
        return None
    return keyword, decompressed.decode("latin-1", errors="replace")


def _collect_png_texts(data: bytes) -> tuple[dict[str, str], bytes | None]:
    """PNG のテキストチャンク(tEXt/zTXt/iTXt)を集める。`gakei` キーワードは無視し、
    同じキーワードは最初のものを採用する。`caBX` チャンクのデータ(C2PA/JUMBF 候補、
    複数あれば連結)も返す。チャンク列が壊れていれば空を返す(例外は投げない)。
    """
    texts: dict[str, str] = {}
    cabx_parts: list[bytes] = []
    try:
        chunks = iter_png_chunks(data)
    except ValueError:
        return texts, None

    for chunk in chunks:
        keyword_text: tuple[str, str] | None = None
        if chunk.type == b"tEXt":
            keyword_text = _parse_text_chunk(chunk.data)
        elif chunk.type == b"zTXt":
            keyword_text = _parse_ztxt_chunk(chunk.data)
        elif chunk.type == b"iTXt":
            parsed = split_itxt(chunk.data)
            if parsed is not None:
                keyword_b, compression_flag, _method, _lang, _trans, text_bytes = parsed
                keyword = keyword_b.decode("latin-1", errors="replace")
                if compression_flag == 1:
                    decompressed = _decompress_limited(text_bytes)
                    if decompressed is None:
                        continue
                    text_bytes = decompressed
                keyword_text = (keyword, text_bytes.decode("utf-8", errors="replace"))
        elif chunk.type == b"caBX":
            cabx_parts.append(chunk.data)
            continue
        else:
            continue

        if keyword_text is None:
            continue
        keyword, text = keyword_text
        if keyword == _GAKEI_KEYWORD:
            continue
        if keyword not in texts:
            texts[keyword] = text

    cabx = b"".join(cabx_parts) if cabx_parts else None
    return texts, cabx


def _decode_exif_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            value = value.decode("latin-1", errors="replace")
    if not isinstance(value, str):
        return None
    value = value.rstrip("\x00").strip()
    return value or None


def _decode_user_comment(raw: Any) -> str | None:
    """EXIF UserComment(0x9286)のデコード。先頭8バイトの符号方式タグに従う。"""
    if isinstance(raw, str):
        text = raw
    elif isinstance(raw, bytes):
        if raw[:8] == b"UNICODE\x00":
            body = raw[8:]
            if body[:2] in (b"\xff\xfe", b"\xfe\xff"):
                text = body.decode("utf-16", errors="replace")
            else:
                be_text = body.decode("utf-16-be", errors="replace")
                le_text = body.decode("utf-16-le", errors="replace")

                def _score(s: str) -> int:
                    return s.count("\x00") + s.count("�")

                text = be_text if _score(be_text) <= _score(le_text) else le_text
        elif raw[:8] == b"ASCII\x00\x00\x00":
            text = raw[8:].decode("ascii", errors="replace")
        else:
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("latin-1", errors="replace")
    else:
        return None
    text = text.rstrip("\x00").strip()
    return text or None


def _extract_xmp_description(xmp_bytes: bytes) -> str | None:
    try:
        text = xmp_bytes.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return None
    match = _XMP_DESCRIPTION_RE.search(text)
    if match is None:
        return None
    li_match = _XMP_LI_RE.search(match.group(1))
    if li_match is None:
        return None
    value = html.unescape(li_match.group(1)).strip()
    return value or None


def _safe_open_image(data: bytes) -> Image.Image | None:
    try:
        return Image.open(io.BytesIO(data))
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        return None


def _reassemble_jpeg_app11(segments: list[bytes]) -> bytes | None:
    """JPEG APP11(JUMBF)セグメント列を1つの `jumb` ボックスに再結合する。
    `b"JP"(2) | En(2,BE) | Z(4,BE) | ...` で始まらないセグメントは無視する。
    """
    groups: dict[int, list[tuple[int, bytes]]] = {}
    for seg in segments:
        if len(seg) < 8 or seg[:2] != b"JP":
            continue
        en = struct.unpack(">H", seg[2:4])[0]
        z = struct.unpack(">I", seg[4:8])[0]
        groups.setdefault(en, []).append((z, seg))
    if not groups:
        return None
    # 複数の En(複数の JUMBF ボックス)があっても、この実装では最初のものだけを使う。
    parts = sorted(next(iter(groups.values())), key=lambda item: item[0])
    out = bytearray()
    for index, (_z, seg) in enumerate(parts):
        out += seg[8:] if index == 0 else seg[16:]
    return bytes(out) if out else None


def _collect_webp_c2pa(data: bytes) -> bytes | None:
    """WebP の RIFF チャンク `C2PA` を探す(任意)。"""
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None
    offset = 12
    total = len(data)
    while offset + 8 <= total:
        fourcc = data[offset : offset + 4]
        size = struct.unpack("<I", data[offset + 4 : offset + 8])[0]
        chunk_start = offset + 8
        chunk_end = chunk_start + size
        if chunk_end > total:
            break
        if fourcc == b"C2PA":
            return data[chunk_start:chunk_end]
        offset = chunk_end + (size % 2)
    return None


def _gather_materials(data: bytes) -> _Materials:
    png_texts: dict[str, str] = {}
    c2pa_bytes: bytes | None = None
    if _is_png(data):
        png_texts, c2pa_bytes = _collect_png_texts(data)

    software: str | None = None
    description: str | None = None
    user_comment: str | None = None
    artist: str | None = None
    xmp_description: str | None = None

    image = _safe_open_image(data)
    if image is not None:
        try:
            exif = image.getexif()
        except Exception:  # noqa: BLE001
            exif = None
        if exif:
            software = _decode_exif_str(exif.get(0x0131))
            description = _decode_exif_str(exif.get(0x010E))
            artist = _decode_exif_str(exif.get(0x013B))
            try:
                ifd = exif.get_ifd(0x8769)
            except Exception:  # noqa: BLE001
                ifd = {}
            raw_user_comment = ifd.get(0x9286) if isinstance(ifd, dict) else None
            if raw_user_comment is not None:
                user_comment = _decode_user_comment(raw_user_comment)

        xmp_bytes = image.info.get("xmp") if hasattr(image, "info") else None
        if isinstance(xmp_bytes, bytes):
            xmp_description = _extract_xmp_description(xmp_bytes)

        if c2pa_bytes is None and image.format == "JPEG":
            app11_segments = [
                segment for marker, segment in getattr(image, "applist", []) if marker == "APP11"
            ]
            if app11_segments:
                c2pa_bytes = _reassemble_jpeg_app11(app11_segments)

    if c2pa_bytes is None:
        c2pa_bytes = _collect_webp_c2pa(data)

    return _Materials(
        png_texts=png_texts,
        c2pa_bytes=c2pa_bytes,
        software=software,
        description=description,
        user_comment=user_comment,
        artist=artist,
        xmp_description=xmp_description,
    )


# -- JUMBF(C2PA)ボックス走査 --------------------------------------------


def _iter_boxes(buf: bytes, start: int, end: int, depth: int = 0) -> list[tuple[bytes, int, int]]:
    """[start, end) の範囲にある兄弟ボックス列を (TBox, payload_start, payload_end) の
    リストで返す。壊れていれば途中までを返す(例外は投げない)。再帰の深さは6まで。
    """
    if depth > 6:
        return []
    boxes: list[tuple[bytes, int, int]] = []
    offset = start
    while offset < end:
        if offset + 8 > end:
            break
        lbox = struct.unpack(">I", buf[offset : offset + 4])[0]
        tbox = buf[offset + 4 : offset + 8]
        header_len = 8
        if lbox == 1:
            if offset + 16 > end:
                break
            lbox = struct.unpack(">Q", buf[offset + 8 : offset + 16])[0]
            header_len = 16
        if lbox == 0:
            boxes.append((tbox, offset + header_len, end))
            break
        if lbox < header_len:
            break
        box_end = offset + lbox
        if box_end > end:
            break
        boxes.append((tbox, offset + header_len, box_end))
        offset = box_end
    return boxes


def _parse_jumd_label(buf: bytes, start: int, end: int) -> str | None:
    """`jumd` ボックスの中身から label だけを取り出す。
    `UUID(16) | toggles(1) | [label: NUL 終端 UTF-8 if toggles & 0x02] | …`
    """
    if end - start < 17:
        return None
    toggles = buf[start + 16]
    pos = start + 17
    if not toggles & 0x02:
        return None
    try:
        nul = buf.index(b"\x00", pos, end)
    except ValueError:
        return None
    return buf[pos:nul].decode("utf-8", errors="replace")


def _jumb_label(buf: bytes, payload_start: int, payload_end: int, depth: int) -> str | None:
    """`jumb` スーパーボックスの最初の子(`jumd`)から label を読む。"""
    children = _iter_boxes(buf, payload_start, payload_end, depth)
    if not children:
        return None
    tbox, cstart, cend = children[0]
    if tbox != b"jumd":
        return None
    return _parse_jumd_label(buf, cstart, cend)


def _find_jumb_by_labels(
    buf: bytes, start: int, end: int, labels: set[str], depth: int, *, take_last: bool = False
) -> tuple[int, int] | None:
    matches: list[tuple[int, int]] = []
    for tbox, cstart, cend in _iter_boxes(buf, start, end, depth):
        if tbox != b"jumb":
            continue
        label = _jumb_label(buf, cstart, cend, depth + 1)
        if label in labels:
            matches.append((cstart, cend))
    if not matches:
        return None
    return matches[-1] if take_last else matches[0]


def _find_manifest_box(buf: bytes, start: int, end: int, depth: int) -> tuple[int, int] | None:
    """`c2pa` ボックス直下の manifest(`urn:uuid:…` / `urn:c2pa:…`)を探す。複数あれば最後。"""
    matches: list[tuple[int, int]] = []
    for tbox, cstart, cend in _iter_boxes(buf, start, end, depth):
        if tbox != b"jumb":
            continue
        label = _jumb_label(buf, cstart, cend, depth + 1)
        if label is not None and (label.startswith("urn:uuid:") or label.startswith("urn:c2pa:")):
            matches.append((cstart, cend))
    return matches[-1] if matches else None


def _find_content_box(buf: bytes, start: int, end: int, tbox_name: bytes) -> tuple[int, int] | None:
    for tbox, cstart, cend in _iter_boxes(buf, start, end, depth=0):
        if tbox == tbox_name:
            return cstart, cend
    return None


def _c2pa_present_only() -> dict[str, Any]:
    return {"tool": "c2pa", "software": None, "params": {}, "raw": {"c2pa": "present"}}


def _keep_claim_value(value: Any) -> bool:
    if isinstance(value, (str, int, float, bool)):
        return True
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _extract_c2pa_from_jumbf(buf: bytes) -> dict[str, Any]:
    """C2PA の JUMBF ボックス(PNG `caBX` そのもの、または再結合した JPEG APP11、
    WebP `C2PA` チャンク)を読む。構造が読めなければ「C2PA あり(内容不明)」を返す。
    """
    try:
        top_boxes = _iter_boxes(buf, 0, len(buf), depth=0)
        if not top_boxes or top_boxes[0][0] != b"jumb":
            return _c2pa_present_only()
        _tbox, top_start, top_end = top_boxes[0]

        manifest = _find_manifest_box(buf, top_start, top_end, depth=1)
        if manifest is None:
            return _c2pa_present_only()

        claim_box = _find_jumb_by_labels(
            buf, manifest[0], manifest[1], {"c2pa.claim", "c2pa.claim.v2"}, depth=2
        )
        if claim_box is None:
            return _c2pa_present_only()

        claim_cbor = _find_content_box(buf, claim_box[0], claim_box[1], b"cbor")
        if claim_cbor is None:
            return _c2pa_present_only()

        claim = cbor2.loads(buf[claim_cbor[0] : claim_cbor[1]])
        if not isinstance(claim, dict):
            return _c2pa_present_only()

        software = claim.get("claim_generator")
        if not isinstance(software, str) or not software:
            info = claim.get("claim_generator_info")
            if isinstance(info, list) and info and isinstance(info[0], dict):
                name = info[0].get("name")
                version = info[0].get("version")
                if isinstance(name, str) and name:
                    software = f"{name} {version}" if isinstance(version, str) and version else name
            elif isinstance(info, dict):
                name = info.get("name")
                software = name if isinstance(name, str) and name else None
            else:
                software = None

        params: dict[str, Any] = {}
        title = claim.get("dc:title")
        if isinstance(title, str) and title:
            params["title"] = title

        # c2pa.assertions はマニフェスト(urn:uuid:… / urn:c2pa:…)の子(claim と兄弟)。
        assertions = _find_jumb_by_labels(
            buf, manifest[0], manifest[1], {"c2pa.assertions"}, depth=2
        )
        if assertions is not None:
            actions_box = _find_jumb_by_labels(
                buf, assertions[0], assertions[1], {"c2pa.actions", "c2pa.actions.v2"}, depth=2
            )
            if actions_box is not None:
                actions_cbor = _find_content_box(buf, actions_box[0], actions_box[1], b"cbor")
                if actions_cbor is not None:
                    try:
                        actions_obj = cbor2.loads(buf[actions_cbor[0] : actions_cbor[1]])
                    except Exception:  # noqa: BLE001
                        actions_obj = None
                    actions = actions_obj.get("actions") if isinstance(actions_obj, dict) else None
                    if isinstance(actions, list):
                        agents: list[str] = []
                        source_types: list[str] = []
                        for action in actions:
                            if not isinstance(action, dict):
                                continue
                            agent = action.get("softwareAgent")
                            if isinstance(agent, dict):
                                agent = agent.get("name")
                            if isinstance(agent, str) and agent and agent not in agents:
                                agents.append(agent)
                            source_type = action.get("digitalSourceType")
                            if (
                                isinstance(source_type, str)
                                and source_type
                                and source_type not in source_types
                            ):
                                source_types.append(source_type)
                        if agents:
                            params["softwareAgent"] = ", ".join(agents)
                        if source_types:
                            params["digitalSourceType"] = ", ".join(source_types)

        raw_claim = {
            key: value
            for key, value in claim.items()
            if isinstance(key, str) and _keep_claim_value(value)
        }
        raw = {"c2pa.claim": json.dumps(raw_claim, ensure_ascii=False, default=str)}

        return {"tool": "c2pa", "software": software, "params": params, "raw": raw}
    except Exception:  # noqa: BLE001 - 構造が読めなければ「あり(内容不明)」に落とすだけ
        return _c2pa_present_only()


def _detect_c2pa(materials: _Materials) -> dict[str, Any] | None:
    if materials.c2pa_bytes is None:
        return None
    return _extract_c2pa_from_jumbf(materials.c2pa_bytes)


def _augment_with_c2pa(result: dict[str, Any], materials: _Materials) -> None:
    """ツール固有の情報が当たった後(generic を含む)も C2PA があれば `params["c2pa"]` に
    claim generator を、読めなければ "present" を足す。
    """
    if materials.c2pa_bytes is None:
        return
    parsed = _extract_c2pa_from_jumbf(materials.c2pa_bytes)
    software = parsed.get("software")
    params = result.get("params")
    if not isinstance(params, dict):
        params = {}
        result["params"] = params
    params["c2pa"] = software if isinstance(software, str) and software else "present"


# -- ComfyUI --------------------------------------------------------------


def _is_wire(value: Any) -> bool:
    """配線([node_id, output_index] の形)かどうか。node_id は str か int(bool を除く)。"""
    return (
        isinstance(value, list)
        and len(value) == 2
        and isinstance(value[0], (str, int))
        and not isinstance(value[0], bool)
        and isinstance(value[1], int)
        and not isinstance(value[1], bool)
    )


def _node_sort_key(node_id: str) -> tuple[int, Any]:
    try:
        return (0, int(node_id))
    except ValueError:
        return (1, node_id)


def _sorted_nodes(graph: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return sorted(graph.items(), key=lambda item: _node_sort_key(item[0]))


def _find_sampler_node(graph: dict[str, Any]) -> tuple[str, dict[str, Any]] | tuple[None, None]:
    samplers = [
        (nid, node)
        for nid, node in _sorted_nodes(graph)
        if node.get("class_type") in ("KSampler", "KSamplerAdvanced")
    ]
    if samplers:
        return samplers[0]
    dual = [
        (nid, node)
        for nid, node in _sorted_nodes(graph)
        if isinstance(node.get("inputs"), dict)
        and "positive" in node["inputs"]
        and "negative" in node["inputs"]
    ]
    if dual:
        return dual[0]
    return None, None


def _resolve_scalar_value(graph: dict[str, Any], value: Any, same_names: tuple[str, ...]) -> Any:
    """配線なら1ホップだけ辿り、先の inputs に同名か `value` の直値があれば使う。"""
    if value is None:
        return None
    if not _is_wire(value):
        return value
    node = graph.get(str(value[0]))
    if not isinstance(node, dict):
        return None
    target_inputs = node.get("inputs")
    if not isinstance(target_inputs, dict):
        return None
    for name in (*same_names, "value"):
        candidate = target_inputs.get(name)
        if candidate is not None and not _is_wire(candidate):
            return candidate
    return None


def _extract_sampler_params(
    graph: dict[str, Any], inputs: dict[str, Any]
) -> tuple[Any, dict[str, Any]]:
    seed_raw = inputs.get("seed", inputs.get("noise_seed"))
    seed = _resolve_scalar_value(graph, seed_raw, ("seed", "noise_seed"))
    steps = _resolve_scalar_value(graph, inputs.get("steps"), ("steps",))
    cfg = _resolve_scalar_value(graph, inputs.get("cfg"), ("cfg",))
    sampler_name = _resolve_scalar_value(graph, inputs.get("sampler_name"), ("sampler_name",))
    scheduler = _resolve_scalar_value(graph, inputs.get("scheduler"), ("scheduler",))
    denoise = _resolve_scalar_value(graph, inputs.get("denoise"), ("denoise",))

    params: dict[str, Any] = {}
    if steps is not None:
        params["steps"] = steps
    if cfg is not None:
        params["cfg"] = cfg
    if sampler_name is not None:
        params["sampler"] = sampler_name
    if scheduler is not None:
        params["scheduler"] = scheduler
    if denoise is not None:
        params["denoise"] = denoise
    return seed, params


def _resolve_conditioning_text(graph: dict[str, Any], value: Any, max_hops: int = 4) -> Any:
    """`positive` / `negative` の配線先を最大 `max_hops` ホップ辿り、`text` 系の直値 str を
    探す。`ConditioningZeroOut` に当たったら番人 `_ZERO_OUT` を返す。
    """
    current = value
    visited: set[str] = set()
    for _ in range(max_hops):
        if not _is_wire(current):
            return None
        node_id = str(current[0])
        if node_id in visited:
            return None
        visited.add(node_id)
        node = graph.get(node_id)
        if not isinstance(node, dict):
            return None
        if node.get("class_type") == "ConditioningZeroOut":
            return _ZERO_OUT
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            return None
        for key in _TEXT_VALUE_KEYS:
            candidate = inputs.get(key)
            if isinstance(candidate, str):
                return candidate

        # text 系の入力が無い素通りノード(FluxGuidance、ConditioningCombine 等)。
        # 入力キーの名前に "conditioning" を含むか、キー名が "positive" の配線を辿る
        # (定義順で最初に見つかったもの)。
        next_wire = None
        for input_key, input_value in inputs.items():
            if not _is_wire(input_value):
                continue
            if "conditioning" in input_key.lower() or input_key == "positive":
                next_wire = input_value
                break
        if next_wire is None:
            return None
        current = next_wire
    return None


def _resolve_side_text(graph: dict[str, Any], raw: Any) -> str | None:
    if isinstance(raw, str):
        return raw
    if _is_wire(raw):
        resolved = _resolve_conditioning_text(graph, raw)
        if resolved is _ZERO_OUT:
            return None
        return resolved if isinstance(resolved, str) else None
    return None


def _resolve_model(graph: dict[str, Any], sampler_inputs: dict[str, Any]) -> str | None:
    current = sampler_inputs.get("model")
    visited: set[str] = set()
    for _ in range(8):
        if not _is_wire(current):
            break
        node_id = str(current[0])
        if node_id in visited:
            break
        visited.add(node_id)
        node = graph.get(node_id)
        if not isinstance(node, dict):
            break
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            break
        for key in _MODEL_NAME_KEYS:
            value = inputs.get(key)
            if isinstance(value, str):
                return value
        next_wire = inputs.get("model")
        if next_wire is None:
            break
        current = next_wire

    for _nid, node in _sorted_nodes(graph):
        if node.get("class_type") not in _CHECKPOINT_CLASS_TYPES:
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        for key in _MODEL_NAME_KEYS:
            value = inputs.get(key)
            if isinstance(value, str):
                return value
    return None


def _extract_size_and_loras(graph: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for _nid, node in _sorted_nodes(graph):
        class_type = str(node.get("class_type", ""))
        if class_type.startswith("Empty") and "Latent" in class_type:
            inputs = node.get("inputs")
            if isinstance(inputs, dict):
                width = inputs.get("width")
                height = inputs.get("height")
                if (
                    isinstance(width, (int, float))
                    and not isinstance(width, bool)
                    and isinstance(height, (int, float))
                    and not isinstance(height, bool)
                ):
                    params["size"] = f"{int(width)}x{int(height)}"
                    break

    lora_names: list[str] = []
    for _nid, node in _sorted_nodes(graph):
        if node.get("class_type") not in ("LoraLoader", "LoraLoaderModelOnly"):
            continue
        inputs = node.get("inputs")
        if isinstance(inputs, dict):
            name = inputs.get("lora_name")
            if isinstance(name, str) and name:
                lora_names.append(name)
    if lora_names:
        params["loras"] = ", ".join(lora_names)
    return params


def _detect_comfyui(materials: _Materials) -> dict[str, Any] | None:
    prompt_text = materials.png_texts.get("prompt")
    if prompt_text is None:
        return None
    try:
        graph = json.loads(prompt_text)
    except (json.JSONDecodeError, ValueError, RecursionError):
        return None
    if not is_api_format_template(graph):
        return None

    _sampler_id, sampler_node = _find_sampler_node(graph)
    sampler_inputs: dict[str, Any] = {}
    seed: Any = None
    positive: str | None = None
    negative: str | None = None
    params: dict[str, Any] = {}
    if sampler_node is not None:
        raw_inputs = sampler_node.get("inputs")
        sampler_inputs = raw_inputs if isinstance(raw_inputs, dict) else {}
        seed, sampler_params = _extract_sampler_params(graph, sampler_inputs)
        params.update(sampler_params)
        positive = _resolve_side_text(graph, sampler_inputs.get("positive"))
        negative = _resolve_side_text(graph, sampler_inputs.get("negative"))

    model = _resolve_model(graph, sampler_inputs)
    params.update(_extract_size_and_loras(graph))

    if positive is None and negative is None and model is None and not params:
        return None

    return {
        "tool": "comfyui",
        "software": "ComfyUI",
        "prompt": positive,
        "negative_prompt": negative,
        "model": model,
        "seed": seed,
        "params": params,
        "raw": {"prompt": prompt_text},
    }


# -- AUTOMATIC1111 / Forge / SD.Next --------------------------------------


def _detect_a1111(materials: _Materials) -> dict[str, Any] | None:
    text: str | None = None
    raw_key: str | None = None

    params_text = materials.png_texts.get("parameters")
    if params_text is not None and not params_text.lstrip().startswith("{"):
        text = params_text
        raw_key = "parameters"
    elif materials.user_comment is not None:
        text = materials.user_comment
        raw_key = "UserComment"

    if text is None or raw_key is None:
        return None

    lines = text.strip().splitlines()
    if not lines:
        return None

    last_line = lines[-1]
    matches = _A1111_LINE_RE.findall(last_line)
    if len(matches) < 3:
        return None

    body_lines = lines[:-1]
    negative_index = next(
        (i for i, line in enumerate(body_lines) if line.startswith("Negative prompt:")), None
    )
    if negative_index is not None:
        prompt_lines = body_lines[:negative_index]
        negative_lines = [
            body_lines[negative_index][len("Negative prompt:") :],
            *body_lines[negative_index + 1 :],
        ]
        negative_prompt = "\n".join(negative_lines).strip() or None
    else:
        prompt_lines = body_lines
        negative_prompt = None
    prompt = "\n".join(prompt_lines).strip() or None

    params: dict[str, Any] = {}
    for raw_name, raw_value in matches:
        name = raw_name.strip()
        value = raw_value.strip()
        if value.startswith('"') and value.endswith('"') and len(value) >= 2:
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                value = value[1:-1]
        params[name] = value

    model = params.pop("Model", None)
    seed = params.pop("Seed", None)
    software = params.get("Version")

    return {
        "tool": "a1111",
        "software": software,
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "model": model,
        "seed": seed,
        "params": params,
        "raw": {raw_key: text},
    }


# -- NovelAI ---------------------------------------------------------------


def _detect_novelai(materials: _Materials) -> dict[str, Any] | None:
    software_text = materials.png_texts.get("Software")
    comment_text = materials.png_texts.get("Comment")
    comment_obj: dict[str, Any] | None = None
    if comment_text is not None:
        try:
            candidate = json.loads(comment_text)
        except json.JSONDecodeError:
            candidate = None
        if isinstance(candidate, dict):
            comment_obj = candidate

    if software_text != "NovelAI" and not (comment_obj is not None and "uc" in comment_obj):
        return None

    prompt = None
    description = materials.png_texts.get("Description")
    if isinstance(description, str) and description.strip():
        prompt = description.strip()
    if prompt is None and comment_obj is not None:
        v4_prompt = comment_obj.get("v4_prompt")
        if isinstance(v4_prompt, dict):
            caption = v4_prompt.get("caption")
            if isinstance(caption, dict):
                base = caption.get("base_caption")
                if isinstance(base, str) and base.strip():
                    prompt = base.strip()

    negative_prompt = None
    if comment_obj is not None:
        uc = comment_obj.get("uc")
        if isinstance(uc, str) and uc.strip():
            negative_prompt = uc.strip()
        if negative_prompt is None:
            v4_negative = comment_obj.get("v4_negative_prompt")
            if isinstance(v4_negative, dict):
                caption = v4_negative.get("caption")
                if isinstance(caption, dict):
                    base = caption.get("base_caption")
                    if isinstance(base, str) and base.strip():
                        negative_prompt = base.strip()

    model = materials.png_texts.get("Source")
    seed = comment_obj.get("seed") if comment_obj is not None else None

    params: dict[str, Any] = {}
    if comment_obj is not None:
        for key, value in comment_obj.items():
            if key in ("prompt", "uc", "seed"):
                continue
            if isinstance(value, (str, int, float, bool)):
                params[key] = value

    raw: dict[str, str] = {}
    for key in ("Title", "Description", "Software", "Source", "Comment", "Generation time"):
        value = materials.png_texts.get(key)
        if value is not None:
            raw[key] = value

    return {
        "tool": "novelai",
        "software": "NovelAI",
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "model": model,
        "seed": seed,
        "params": params,
        "raw": raw,
    }


# -- InvokeAI ---------------------------------------------------------------


def _detect_invokeai(materials: _Materials) -> dict[str, Any] | None:
    text = materials.png_texts.get("invokeai_metadata")
    if text is None:
        return None
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None

    prompt = obj.get("positive_prompt")
    prompt = prompt if isinstance(prompt, str) else None
    negative_prompt = obj.get("negative_prompt")
    negative_prompt = negative_prompt if isinstance(negative_prompt, str) else None
    seed = obj.get("seed")

    model_raw = obj.get("model")
    model: str | None = None
    if isinstance(model_raw, dict):
        name = model_raw.get("name") or model_raw.get("model_name")
        model = name if isinstance(name, str) else None
    elif isinstance(model_raw, str):
        model = model_raw

    software = "InvokeAI"
    app_version = obj.get("app_version")
    if isinstance(app_version, str) and app_version.strip():
        software = f"InvokeAI {app_version.strip()}"

    excluded = {"positive_prompt", "negative_prompt", "seed", "model", "app_version", "loras"}
    params: dict[str, Any] = {}
    for key, value in obj.items():
        if key in excluded:
            continue
        if isinstance(value, (str, int, float, bool)):
            params[key] = value

    loras = obj.get("loras")
    if isinstance(loras, list):
        names: list[str] = []
        for item in loras:
            if not isinstance(item, dict):
                continue
            model_ref = item.get("model")
            if isinstance(model_ref, dict) and isinstance(model_ref.get("name"), str):
                names.append(model_ref["name"])
                continue
            lora_ref = item.get("lora")
            if isinstance(lora_ref, dict) and isinstance(lora_ref.get("name"), str):
                names.append(lora_ref["name"])
        if names:
            params["loras"] = ", ".join(names)

    return {
        "tool": "invokeai",
        "software": software,
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "model": model,
        "seed": seed,
        "params": params,
        "raw": {"invokeai_metadata": text},
    }


# -- SwarmUI ----------------------------------------------------------------


def _detect_swarmui(materials: _Materials) -> dict[str, Any] | None:
    candidates: list[tuple[str, str]] = []
    params_text = materials.png_texts.get("parameters")
    if params_text is not None:
        candidates.append(("parameters", params_text))
    if materials.user_comment is not None:
        candidates.append(("UserComment", materials.user_comment))

    for raw_key, text in candidates:
        if not text.lstrip().startswith("{"):
            continue
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        params_obj = obj.get("sui_image_params")
        if not isinstance(params_obj, dict):
            continue

        prompt = params_obj.get("prompt")
        prompt = prompt if isinstance(prompt, str) else None
        negative_prompt = params_obj.get("negativeprompt")
        negative_prompt = negative_prompt if isinstance(negative_prompt, str) else None
        model = params_obj.get("model")
        model = model if isinstance(model, str) else None
        seed = params_obj.get("seed")

        excluded = {"prompt", "negativeprompt", "model", "seed"}
        params: dict[str, Any] = {}
        for key, value in params_obj.items():
            if key in excluded:
                continue
            if isinstance(value, (str, int, float, bool)):
                params[key] = value

        software = "SwarmUI"
        swarm_version = obj.get("swarm_version")
        if isinstance(swarm_version, str) and swarm_version.strip():
            software = f"SwarmUI {swarm_version.strip()}"

        return {
            "tool": "swarmui",
            "software": software,
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "model": model,
            "seed": seed,
            "params": params,
            "raw": {raw_key: text},
        }
    return None


# -- 一般(generic) ---------------------------------------------------------


def _detect_generic(materials: _Materials) -> dict[str, Any] | None:
    png_description = materials.png_texts.get("Description")
    png_comment = materials.png_texts.get("Comment")

    prompt = None
    for candidate in (
        materials.xmp_description,
        materials.description,
        png_description,
        materials.user_comment,
        png_comment,
    ):
        if isinstance(candidate, str) and candidate.strip():
            prompt = candidate.strip()
            break

    if _GENERIC_REQUIRES_DESCRIPTION and prompt is None:
        return None

    software = materials.software or materials.png_texts.get("Software")
    artist = materials.artist or materials.png_texts.get("Author")
    title = materials.png_texts.get("Title")

    params: dict[str, Any] = {}
    if isinstance(artist, str) and artist.strip():
        params["Artist"] = artist.strip()
    if isinstance(title, str) and title.strip():
        params["Title"] = title.strip()

    raw: dict[str, str] = {}
    if isinstance(materials.xmp_description, str) and materials.xmp_description.strip():
        raw["dc:description"] = materials.xmp_description
    if isinstance(materials.description, str) and materials.description.strip():
        raw["ImageDescription"] = materials.description
    if isinstance(materials.user_comment, str) and materials.user_comment.strip():
        raw["UserComment"] = materials.user_comment
    if isinstance(artist, str) and artist.strip():
        raw["Artist"] = artist
    if isinstance(software, str) and software.strip():
        raw["Software"] = software
    for key in ("Description", "Comment", "Title", "Author"):
        value = materials.png_texts.get(key)
        if value is not None:
            raw[key] = value

    return {
        "tool": "generic",
        "software": software,
        "prompt": prompt,
        "negative_prompt": None,
        "model": None,
        "seed": None,
        "params": params,
        "raw": raw,
    }


# -- 正規化 ------------------------------------------------------------------


def _truncate(value: str, limit: int) -> tuple[str, bool]:
    if len(value) > limit:
        return value[:limit], True
    return value, False


def _json_bytes_len(meta: dict[str, Any]) -> int:
    return len(json.dumps(meta, ensure_ascii=False).encode("utf-8"))


def _normalize_seed(seed: Any) -> Any:
    if seed is None:
        return None
    if isinstance(seed, bool):
        return str(seed)
    if isinstance(seed, int):
        return seed
    if isinstance(seed, str):
        stripped = seed.strip()
        if stripped.isdigit():
            return int(stripped)
        return seed
    return str(seed)


def _normalize_str_field(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


def _finalize(result: dict[str, Any]) -> dict[str, Any] | None:
    truncated = False

    prompt = _normalize_str_field(result.get("prompt"))
    if prompt is not None:
        prompt, cut = _truncate(prompt, MAX_RAW_ITEM_CHARS)
        truncated = truncated or cut

    negative_prompt = _normalize_str_field(result.get("negative_prompt"))
    if negative_prompt is not None:
        negative_prompt, cut = _truncate(negative_prompt, MAX_RAW_ITEM_CHARS)
        truncated = truncated or cut

    raw_in = result.get("raw") or {}
    raw: dict[str, str] = {}
    if isinstance(raw_in, dict):
        for key, value in raw_in.items():
            if not isinstance(value, str):
                continue
            clipped, cut = _truncate(value, MAX_RAW_ITEM_CHARS)
            raw[key] = clipped
            truncated = truncated or cut

    params_in = result.get("params") or {}
    params: dict[str, Any] = {}
    if isinstance(params_in, dict):
        if len(params_in) > MAX_PARAMS:
            truncated = True
        for key, value in list(params_in.items())[:MAX_PARAMS]:
            if value is None:
                continue
            if isinstance(value, bool) or isinstance(value, (int, float)):
                params[key] = value
            elif isinstance(value, str):
                clipped, cut = _truncate(value, MAX_PARAM_VALUE_CHARS)
                params[key] = clipped
                truncated = truncated or cut
            elif isinstance(value, (dict, list)):
                encoded = json.dumps(value, ensure_ascii=False)
                clipped, cut = _truncate(encoded, MAX_PARAM_VALUE_CHARS)
                params[key] = clipped
                truncated = truncated or cut
            else:
                params[key] = str(value)

    meta: dict[str, Any] = {
        "schema": SCHEMA,
        "tool": result["tool"],
        "software": _normalize_str_field(result.get("software")),
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "model": _normalize_str_field(result.get("model")),
        "seed": _normalize_seed(result.get("seed")),
        "params": params,
        "raw": raw,
        "truncated": truncated,
    }

    while _json_bytes_len(meta) > MAX_TOTAL_BYTES:
        if not meta["raw"]:
            break
        largest_key = max(meta["raw"], key=lambda k: len(meta["raw"][k]))
        current = meta["raw"][largest_key]
        if len(current) < 1024:
            del meta["raw"][largest_key]
        else:
            meta["raw"][largest_key] = current[: len(current) // 2]
        meta["truncated"] = True

    try:
        EmbeddedGenerationMeta.model_validate(meta)
    except Exception:  # noqa: BLE001
        logger.warning("正規化後の embedded_meta が型に合わないため無視した", exc_info=True)
        return None
    return meta


# -- ディスパッチ -------------------------------------------------------------


def _extract(data: bytes) -> dict[str, Any] | None:
    if not data:
        return None

    materials = _gather_materials(data)

    result: dict[str, Any] | None = None
    for detector in (
        _detect_comfyui,
        _detect_swarmui,
        _detect_a1111,
        _detect_novelai,
        _detect_invokeai,
    ):
        result = detector(materials)
        if result is not None:
            _augment_with_c2pa(result, materials)
            break

    if result is None:
        result = _detect_c2pa(materials)

    if result is None:
        result = _detect_generic(materials)
        if result is not None:
            _augment_with_c2pa(result, materials)

    if result is None:
        return None

    return _finalize(result)
