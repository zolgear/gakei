"""`app/domain/generation_meta.py` の単体テスト(ADR-0018)。DB や実 API には触れない。

各ツールの判定・値の取り出し・上限による切り詰め・どんな入力でも例外を投げないことを
確認する。JUMBF(C2PA)まわりはテスト内に小さなビルダーを用意し、実際のボックス列を
組み立てて検証する。
"""

from __future__ import annotations

import io
import json
import struct
import zlib
from typing import Any

import cbor2
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from app.domain.generation_meta import (
    MAX_PARAM_VALUE_CHARS,
    MAX_PARAMS,
    MAX_RAW_ITEM_CHARS,
    MAX_TOTAL_BYTES,
    SCHEMA,
    _finalize,
    extract_generation_meta,
)
from app.domain.schemas import EmbeddedGenerationMeta
from tests.comfyui_graphs import T2I_GRAPH

# -- PNG ビルダー -------------------------------------------------------------


def _png(text: dict[str, str], *, mode: str = "text") -> bytes:
    """`mode` に応じて tEXt(`text`)/ zTXt(`ztxt`)/ iTXt(`itxt`)で書き込む。"""
    image = Image.new("RGB", (48, 32), (120, 60, 30))
    info = PngInfo()
    for keyword, value in text.items():
        if mode == "text":
            info.add_text(keyword, value)
        elif mode == "ztxt":
            info.add_text(keyword, value, zip=True)
        elif mode == "itxt":
            info.add_itxt(keyword, value)
        else:  # pragma: no cover - テストの書き間違い検出用
            raise ValueError(f"未知の mode: {mode}")
    buffer = io.BytesIO()
    image.save(buffer, "PNG", pnginfo=info)
    return buffer.getvalue()


def _png_with_chunk(png: bytes, chunk_type: bytes, payload: bytes) -> bytes:
    """IEND の直前に、任意のチャンクをそのまま挿入する(`tests/test_embedded_meta.py` の手法)。"""
    crc = struct.pack(">I", zlib.crc32(chunk_type + payload) & 0xFFFFFFFF)
    chunk = struct.pack(">I", len(payload)) + chunk_type + payload + crc
    iend_index = png.index(b"IEND") - 4
    return png[:iend_index] + chunk + png[iend_index:]


def _jpeg(
    *,
    user_comment: bytes | None = None,
    software: str | None = None,
    description: str | None = None,
    xmp: bytes | None = None,
) -> bytes:
    image = Image.new("RGB", (32, 32), (10, 40, 90))
    exif = Image.Exif()
    if software is not None:
        exif[0x0131] = software
    if description is not None:
        exif[0x010E] = description
    if user_comment is not None:
        exif.get_ifd(0x8769)[0x9286] = user_comment
    buffer = io.BytesIO()
    kwargs: dict[str, Any] = {"exif": exif.tobytes()}
    if xmp is not None:
        kwargs["xmp"] = xmp
    image.save(buffer, "JPEG", **kwargs)
    return buffer.getvalue()


def _webp(*, user_comment: bytes | None = None) -> bytes:
    image = Image.new("RGB", (32, 32), (10, 40, 90))
    exif = Image.Exif()
    if user_comment is not None:
        exif.get_ifd(0x8769)[0x9286] = user_comment
    buffer = io.BytesIO()
    image.save(buffer, "WEBP", exif=exif.tobytes())
    return buffer.getvalue()


def _plain_png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), (1, 2, 3)).save(buffer, "PNG")
    return buffer.getvalue()


def _plain_jpeg() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), (1, 2, 3)).save(buffer, "JPEG")
    return buffer.getvalue()


def _plain_webp() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), (1, 2, 3)).save(buffer, "WEBP")
    return buffer.getvalue()


# -- JUMBF(C2PA)ビルダー -----------------------------------------------------


def _box(tbox: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + tbox + payload


def _jumd(label: str) -> bytes:
    return _box(b"jumd", b"\x00" * 16 + bytes([0x02]) + label.encode("utf-8") + b"\x00")


def _jumb(label: str, children: bytes) -> bytes:
    return _box(b"jumb", _jumd(label) + children)


def _cbor_box(obj: Any) -> bytes:
    return _box(b"cbor", cbor2.dumps(obj))


def _build_c2pa_manifest(
    claim: dict[str, Any], *, actions: list[dict[str, Any]] | None = None
) -> bytes:
    """`jumb(c2pa) > jumb(urn:uuid:...) > jumb(c2pa.claim) > cbor` を組み立てる。
    `actions` を渡すと `c2pa.assertions > c2pa.actions > cbor` も足す。
    """
    claim_box = _jumb("c2pa.claim", _cbor_box(claim))
    manifest_children = claim_box
    if actions is not None:
        actions_box = _jumb("c2pa.actions", _cbor_box({"actions": actions}))
        manifest_children += _jumb("c2pa.assertions", actions_box)
    manifest_box = _jumb("urn:uuid:11111111-1111-1111-1111-111111111111", manifest_children)
    return _jumb("c2pa", manifest_box)


def _split_app11(jumbf: bytes, chunk_size: int) -> list[bytes]:
    """JUMBF ボックスを JPEG APP11 のセグメント列(`b"JP" | En | Z | ...`)に分割する。
    最初のセグメントは LBox/TBox を含めて先頭から、2つ目以降は LBox/TBox を除いた続きを運ぶ。
    """
    en = 1
    first_len = min(chunk_size, len(jumbf))
    segments = [b"JP" + struct.pack(">H", en) + struct.pack(">I", 1) + jumbf[:first_len]]
    remaining = jumbf[first_len:]
    lbox_tbox = jumbf[:8]
    z = 2
    while remaining:
        chunk = remaining[:chunk_size]
        remaining = remaining[chunk_size:]
        segments.append(b"JP" + struct.pack(">H", en) + struct.pack(">I", z) + lbox_tbox + chunk)
        z += 1
    return segments


def _jpeg_with_app11(segments: list[bytes]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), (5, 5, 5)).save(buffer, "JPEG")
    data = buffer.getvalue()
    assert data[:2] == b"\xff\xd8"
    markers = b""
    for segment in segments:
        length = len(segment) + 2
        markers += b"\xff\xeb" + struct.pack(">H", length) + segment
    return data[:2] + markers + data[2:]


# -- AUTOMATIC1111 / Forge / SD.Next -----------------------------------------

_A1111_TEXT = (
    "masterpiece, 1girl\n"
    "Negative prompt: lowres, bad anatomy\n"
    "Steps: 20, Sampler: DPM++ 2M, Schedule type: Karras, CFG scale: 7, Seed: 123, "
    "Size: 512x768, Model hash: abc123, Model: foo_v1, VAE: x.safetensors, "
    "Denoising strength: 0.5, Clip skip: 2, "
    'Hashes: {"model": "abc123"}, Lora hashes: "a: 1, b: 2", Version: v1.10.1'
)


def test_a1111_from_png_text_chunk() -> None:
    meta = extract_generation_meta(_png({"parameters": _A1111_TEXT}))
    assert meta is not None
    assert meta["tool"] == "a1111"
    assert meta["prompt"] == "masterpiece, 1girl"
    assert meta["negative_prompt"] == "lowres, bad anatomy"
    assert meta["model"] == "foo_v1"
    assert meta["seed"] == 123
    assert meta["software"] == "v1.10.1"
    assert meta["params"]["Steps"] == "20"
    assert meta["params"]["Sampler"] == "DPM++ 2M"
    assert meta["params"]["Version"] == "v1.10.1"
    # 引用された JSON 風の値、引用されたカンマ入りの値もそのまま文字列で入る。
    assert meta["params"]["Hashes"] == '{"model": "abc123"}'
    assert meta["params"]["Lora hashes"] == "a: 1, b: 2"
    assert meta["raw"] == {"parameters": _A1111_TEXT}


def test_a1111_from_png_ztxt_chunk() -> None:
    meta = extract_generation_meta(_png({"parameters": _A1111_TEXT}, mode="ztxt"))
    assert meta is not None
    assert meta["tool"] == "a1111"
    assert meta["prompt"] == "masterpiece, 1girl"


def test_a1111_from_png_itxt_chunk_with_japanese_prompt() -> None:
    text = (
        "夕焼けの中を歩く猫、水彩画風\n"
        "Negative prompt: 低品質、ぼやけ\n"
        "Steps: 20, Sampler: Euler a, CFG scale: 7"
    )
    meta = extract_generation_meta(_png({"parameters": text}, mode="itxt"))
    assert meta is not None
    assert meta["tool"] == "a1111"
    assert meta["prompt"] == "夕焼けの中を歩く猫、水彩画風"
    assert meta["negative_prompt"] == "低品質、ぼやけ"


def test_a1111_negative_prompt_can_span_multiple_lines() -> None:
    text = (
        "a cat\nsitting on a chair\n"
        "Negative prompt: lowres\nbad anatomy\n"
        "Steps: 20, Sampler: Euler, CFG scale: 7"
    )
    meta = extract_generation_meta(_png({"parameters": text}))
    assert meta is not None
    assert meta["prompt"] == "a cat\nsitting on a chair"
    assert meta["negative_prompt"] == "lowres\nbad anatomy"


def test_a1111_needs_at_least_three_key_value_pairs() -> None:
    text = "a cat\nNegative prompt: lowres\nSteps: 20, Sampler: Euler"
    assert extract_generation_meta(_png({"parameters": text})) is None


def test_a1111_json_starting_parameters_is_not_treated_as_a1111() -> None:
    # `{` で始まる parameters は(SwarmUI などの JSON 埋め込みかもしれないので)対象外。
    text = '{"not": "a1111"}'
    assert extract_generation_meta(_png({"parameters": text})) is None


# 先頭にサロゲートペアの誤読を誘発する文字(U+01D8)を置くことで、UTF-16 の BOM 無し
# BE/LE 判定(スコアリング)が方向を取り違えないようにしたテスト専用の文字列。
_A1111_UNICODE_TEXT = (
    "ǘcat, masterpiece\nNegative prompt: lowres\nSteps: 20, Sampler: Euler, CFG scale: 7"
)


def test_a1111_from_jpeg_user_comment_ascii() -> None:
    comment = b"ASCII\x00\x00\x00" + _A1111_TEXT.encode("ascii")
    meta = extract_generation_meta(_jpeg(user_comment=comment))
    assert meta is not None
    assert meta["tool"] == "a1111"
    assert meta["prompt"] == "masterpiece, 1girl"
    assert meta["raw"] == {"UserComment": _A1111_TEXT}


def test_a1111_from_jpeg_user_comment_utf16_be_without_bom() -> None:
    comment = b"UNICODE\x00" + _A1111_UNICODE_TEXT.encode("utf-16-be")
    meta = extract_generation_meta(_jpeg(user_comment=comment))
    assert meta is not None
    assert meta["tool"] == "a1111"
    assert meta["prompt"] == "ǘcat, masterpiece"


def test_a1111_from_jpeg_user_comment_utf16_le_without_bom() -> None:
    comment = b"UNICODE\x00" + _A1111_UNICODE_TEXT.encode("utf-16-le")
    meta = extract_generation_meta(_jpeg(user_comment=comment))
    assert meta is not None
    assert meta["tool"] == "a1111"
    assert meta["prompt"] == "ǘcat, masterpiece"


def test_a1111_from_jpeg_user_comment_utf16_with_bom() -> None:
    comment_be = b"UNICODE\x00" + b"\xfe\xff" + _A1111_UNICODE_TEXT.encode("utf-16-be")
    comment_le = b"UNICODE\x00" + b"\xff\xfe" + _A1111_UNICODE_TEXT.encode("utf-16-le")
    for comment in (comment_be, comment_le):
        meta = extract_generation_meta(_jpeg(user_comment=comment))
        assert meta is not None
        assert meta["prompt"] == "ǘcat, masterpiece"


def test_a1111_from_webp_user_comment() -> None:
    comment = b"ASCII\x00\x00\x00" + _A1111_TEXT.encode("ascii")
    meta = extract_generation_meta(_webp(user_comment=comment))
    assert meta is not None
    assert meta["tool"] == "a1111"
    assert meta["prompt"] == "masterpiece, 1girl"


# -- ComfyUI ------------------------------------------------------------------


def test_comfyui_from_t2i_graph() -> None:
    prompt_json = json.dumps(T2I_GRAPH)
    workflow_json = json.dumps({"nodes": [], "links": []})
    meta = extract_generation_meta(_png({"prompt": prompt_json, "workflow": workflow_json}))
    assert meta is not None
    assert meta["tool"] == "comfyui"
    assert meta["software"] == "ComfyUI"
    assert meta["prompt"] == "a photo of a cat, studio lighting"
    assert meta["negative_prompt"] == "blurry, low quality"
    assert meta["model"] == "sd_xl_base_1.0.safetensors"
    assert meta["seed"] == 156680208700286
    assert meta["params"]["steps"] == 20
    assert meta["params"]["cfg"] == 8.0
    assert meta["params"]["sampler"] == "euler"
    assert meta["params"]["scheduler"] == "normal"
    # `workflow` チャンクは raw に入れない。
    assert list(meta["raw"].keys()) == ["prompt"]
    assert "workflow" not in meta["raw"]["prompt"]


def test_comfyui_positive_resolves_through_pass_through_node() -> None:
    graph = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "flux1-dev.gguf"}},
        "2": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "a robot in a garden", "clip": ["1", 1]},
        },
        "3": {"class_type": "FluxGuidance", "inputs": {"conditioning": ["2", 0], "guidance": 3.5}},
        "4": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["2", 0]}},
        "5": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": 1024, "height": 1024, "batch_size": 1},
        },
        "6": {
            "class_type": "KSampler",
            "inputs": {
                "seed": 42,
                "steps": 25,
                "cfg": 1.0,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 1.0,
                "model": ["1", 0],
                "positive": ["3", 0],
                "negative": ["4", 0],
                "latent_image": ["5", 0],
            },
        },
    }
    meta = extract_generation_meta(_png({"prompt": json.dumps(graph)}))
    assert meta is not None
    assert meta["prompt"] == "a robot in a garden"
    # ConditioningZeroOut に当たった negative は明示的に None。
    assert meta["negative_prompt"] is None
    assert meta["model"] == "flux1-dev.gguf"
    assert meta["params"]["size"] == "1024x1024"


def test_comfyui_accepts_integer_node_ids() -> None:
    graph = {
        1: {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "model.safetensors"}},
        2: {"class_type": "CLIPTextEncode", "inputs": {"text": "int ids work", "clip": [1, 1]}},
        3: {"class_type": "CLIPTextEncode", "inputs": {"text": "bad", "clip": [1, 1]}},
        4: {
            "class_type": "KSampler",
            "inputs": {
                "seed": 7,
                "steps": 10,
                "cfg": 5.0,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 1.0,
                "model": [1, 0],
                "positive": [2, 0],
                "negative": [3, 0],
                "latent_image": [1, 0],
            },
        },
    }
    meta = extract_generation_meta(_png({"prompt": json.dumps(graph)}))
    assert meta is not None
    assert meta["prompt"] == "int ids work"
    assert meta["negative_prompt"] == "bad"
    assert meta["model"] == "model.safetensors"


def test_comfyui_requires_api_format_prompt_chunk() -> None:
    # UI 形式(nodes/links)や、ただの JSON は ComfyUI と判定しない(他の判定にも当たらなければ None)。
    ui_format = json.dumps({"nodes": [], "links": []})
    assert extract_generation_meta(_png({"prompt": ui_format})) is None


# -- NovelAI --------------------------------------------------------------


def test_novelai_v3_comment_with_uc() -> None:
    comment = {
        "steps": 28,
        "sampler": "k_euler",
        "scale": 5.0,
        "uc": "lowres, bad anatomy",
        "seed": 999,
    }
    meta = extract_generation_meta(
        _png(
            {
                "Software": "NovelAI",
                "Source": "NovelAI Diffusion V3",
                "Description": "1girl, masterpiece",
                "Comment": json.dumps(comment),
            }
        )
    )
    assert meta is not None
    assert meta["tool"] == "novelai"
    assert meta["software"] == "NovelAI"
    assert meta["prompt"] == "1girl, masterpiece"
    assert meta["negative_prompt"] == "lowres, bad anatomy"
    assert meta["model"] == "NovelAI Diffusion V3"
    assert meta["seed"] == 999
    assert meta["params"]["steps"] == 28
    assert meta["params"]["sampler"] == "k_euler"
    assert "uc" not in meta["params"]


def test_novelai_v4_uses_structured_prompt_and_negative() -> None:
    comment = {
        "v4_prompt": {"caption": {"base_caption": "a fox in the snow"}},
        "v4_negative_prompt": {"caption": {"base_caption": "lowres"}},
        "seed": 555,
    }
    meta = extract_generation_meta(
        _png(
            {
                "Software": "NovelAI",
                "Source": "NovelAI Diffusion V4",
                "Comment": json.dumps(comment),
            }
        )
    )
    assert meta is not None
    assert meta["tool"] == "novelai"
    assert meta["prompt"] == "a fox in the snow"
    assert meta["negative_prompt"] == "lowres"
    assert meta["seed"] == 555


# -- InvokeAI ---------------------------------------------------------------


def test_invokeai_metadata_with_dict_model_and_loras() -> None:
    metadata = {
        "positive_prompt": "a castle on a hill",
        "negative_prompt": "blurry",
        "seed": 42,
        "model": {"name": "sd1.5", "base": "sd-1"},
        "steps": 30,
        "cfg_scale": 7.5,
        "scheduler": "euler",
        "app_version": "4.2.0",
        "loras": [{"model": {"name": "lora-one"}}, {"lora": {"name": "lora-two"}}],
    }
    meta = extract_generation_meta(_png({"invokeai_metadata": json.dumps(metadata)}))
    assert meta is not None
    assert meta["tool"] == "invokeai"
    assert meta["software"] == "InvokeAI 4.2.0"
    assert meta["prompt"] == "a castle on a hill"
    assert meta["negative_prompt"] == "blurry"
    assert meta["model"] == "sd1.5"
    assert meta["seed"] == 42
    assert meta["params"]["steps"] == 30
    assert meta["params"]["cfg_scale"] == 7.5
    assert meta["params"]["loras"] == "lora-one, lora-two"
    assert meta["raw"] == {"invokeai_metadata": json.dumps(metadata)}


# -- SwarmUI ------------------------------------------------------------------

_SWARM_PARAMS = {
    "sui_image_params": {
        "prompt": "a dragon over the mountains",
        "negativeprompt": "bad quality",
        "model": "swarm_model.safetensors",
        "seed": 7,
        "steps": 20,
        "cfgscale": 7.0,
    },
    "swarm_version": "0.9.3",
}


def test_swarmui_from_png_parameters() -> None:
    meta = extract_generation_meta(_png({"parameters": json.dumps(_SWARM_PARAMS)}))
    assert meta is not None
    assert meta["tool"] == "swarmui"
    assert meta["software"] == "SwarmUI 0.9.3"
    assert meta["prompt"] == "a dragon over the mountains"
    assert meta["negative_prompt"] == "bad quality"
    assert meta["model"] == "swarm_model.safetensors"
    assert meta["seed"] == 7
    assert meta["params"]["steps"] == 20
    assert meta["params"]["cfgscale"] == 7.0


def test_swarmui_from_jpeg_user_comment() -> None:
    comment = b"ASCII\x00\x00\x00" + json.dumps(_SWARM_PARAMS).encode("ascii")
    meta = extract_generation_meta(_jpeg(user_comment=comment))
    assert meta is not None
    assert meta["tool"] == "swarmui"
    assert meta["prompt"] == "a dragon over the mountains"


# -- 一般(generic) -----------------------------------------------------------


def test_generic_from_xmp_description() -> None:
    xmp = (
        b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
        b'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
        b'xmlns:dc="http://purl.org/dc/elements/1.1/">'
        b"<rdf:Description><dc:description><rdf:Alt>"
        b'<rdf:li xml:lang="x-default">a description from XMP</rdf:li>'
        b"</rdf:Alt></dc:description></rdf:Description>"
        b"</rdf:RDF></x:xmpmeta>"
    )
    meta = extract_generation_meta(_jpeg(xmp=xmp))
    assert meta is not None
    assert meta["tool"] == "generic"
    assert meta["prompt"] == "a description from XMP"
    assert meta["raw"]["dc:description"] == "a description from XMP"


def test_generic_from_exif_image_description() -> None:
    meta = extract_generation_meta(
        _jpeg(description="a description from EXIF", software="Some App")
    )
    assert meta is not None
    assert meta["tool"] == "generic"
    assert meta["prompt"] == "a description from EXIF"
    assert meta["software"] == "Some App"


def test_generic_from_png_comment() -> None:
    meta = extract_generation_meta(_png({"Comment": "a hand-written note", "Software": "Paint"}))
    assert meta is not None
    assert meta["tool"] == "generic"
    assert meta["prompt"] == "a hand-written note"


def test_generic_software_only_is_none() -> None:
    assert extract_generation_meta(_png({"Software": "Some Camera App"})) is None
    assert extract_generation_meta(_jpeg(software="Some Camera App")) is None


# -- C2PA ---------------------------------------------------------------------


def test_c2pa_from_png_cabx_claim_generator_and_software_agent() -> None:
    manifest = _build_c2pa_manifest(
        {"claim_generator": "OpenAI ChatGPT/1.0", "dc:title": "A generated image"},
        actions=[
            {
                "action": "c2pa.created",
                "softwareAgent": "OpenAI ChatGPT/1.0",
                "digitalSourceType": "trainedAlgorithmicMedia",
            }
        ],
    )
    png = _png_with_chunk(_plain_png(), b"caBX", manifest)
    meta = extract_generation_meta(png)
    assert meta is not None
    assert meta["tool"] == "c2pa"
    assert meta["software"] == "OpenAI ChatGPT/1.0"
    assert meta["params"]["title"] == "A generated image"
    assert meta["params"]["softwareAgent"] == "OpenAI ChatGPT/1.0"
    assert meta["params"]["digitalSourceType"] == "trainedAlgorithmicMedia"


def test_c2pa_from_jpeg_app11_in_two_packets() -> None:
    manifest = _build_c2pa_manifest({"claim_generator": "JpegTool/2.0"})
    chunk_size = len(manifest) // 2 + 10
    segments = _split_app11(manifest, chunk_size)
    assert len(segments) == 2
    meta = extract_generation_meta(_jpeg_with_app11(segments))
    assert meta is not None
    assert meta["tool"] == "c2pa"
    assert meta["software"] == "JpegTool/2.0"


def test_c2pa_broken_cbor_falls_back_to_present_only() -> None:
    claim_box = _jumb("c2pa.claim", _box(b"cbor", b"\xff\xff\xff not valid cbor"))
    manifest_box = _jumb("urn:uuid:22222222-2222-2222-2222-222222222222", claim_box)
    root_box = _jumb("c2pa", manifest_box)
    png = _png_with_chunk(_plain_png(), b"caBX", root_box)
    meta = extract_generation_meta(png)
    assert meta is not None
    assert meta["tool"] == "c2pa"
    assert meta["software"] is None
    assert meta["raw"] == {"c2pa": "present"}


def test_a1111_plus_c2pa_adds_claim_generator_to_params() -> None:
    manifest = _build_c2pa_manifest({"claim_generator": "SomeTool/1.0"})
    png = _png({"parameters": _A1111_TEXT})
    png = _png_with_chunk(png, b"caBX", manifest)
    meta = extract_generation_meta(png)
    assert meta is not None
    assert meta["tool"] == "a1111"
    assert meta["params"]["c2pa"] == "SomeTool/1.0"


# -- 上限による切り詰め ---------------------------------------------------------


def test_prompt_over_64kib_is_truncated() -> None:
    big_prompt = "x" * (MAX_RAW_ITEM_CHARS + 100)
    text = f"{big_prompt}\nNegative prompt: y\nSteps: 20, Sampler: Euler, CFG scale: 7"
    meta = extract_generation_meta(_png({"parameters": text}))
    assert meta is not None
    assert meta["truncated"] is True
    assert len(meta["prompt"]) == MAX_RAW_ITEM_CHARS


def test_total_size_over_256kib_shrinks_raw() -> None:
    result = {
        "tool": "generic",
        "software": "X",
        "prompt": "p",
        "negative_prompt": None,
        "model": None,
        "seed": None,
        "params": {},
        "raw": {
            "a": "x" * MAX_RAW_ITEM_CHARS,
            "b": "y" * MAX_RAW_ITEM_CHARS,
            "c": "z" * MAX_RAW_ITEM_CHARS,
            "d": "w" * MAX_RAW_ITEM_CHARS,
            "e": "v" * MAX_RAW_ITEM_CHARS,
        },
    }
    meta = _finalize(result)
    assert meta is not None
    total = len(json.dumps(meta, ensure_ascii=False).encode("utf-8"))
    assert total <= MAX_TOTAL_BYTES
    assert meta["truncated"] is True


def test_params_are_capped_at_max_params_and_value_length() -> None:
    result = {
        "tool": "generic",
        "software": None,
        "prompt": "p",
        "negative_prompt": None,
        "model": None,
        "seed": None,
        "params": {f"key{i}": "v" for i in range(MAX_PARAMS + 10)},
        "raw": {},
    }
    result["params"]["big"] = "z" * (MAX_PARAM_VALUE_CHARS + 10)
    meta = _finalize(result)
    assert meta is not None
    assert len(meta["params"]) <= MAX_PARAMS
    assert meta["truncated"] is True


# -- 判定なし・例外を投げない --------------------------------------------------


def test_gakei_only_itxt_is_ignored() -> None:
    meta_json = json.dumps({"schema": "gakei.lineage/2", "root": "x"})
    assert extract_generation_meta(_png({"gakei": meta_json}, mode="itxt")) is None


def test_no_meta_png_jpeg_webp_returns_none() -> None:
    assert extract_generation_meta(_plain_png()) is None
    assert extract_generation_meta(_plain_jpeg()) is None
    assert extract_generation_meta(_plain_webp()) is None


def test_empty_and_garbage_bytes_return_none_without_raising() -> None:
    assert extract_generation_meta(b"") is None
    assert extract_generation_meta(b"not an image at all") is None
    assert extract_generation_meta(b"\x89PNG\r\n\x1a\n" + b"\x00" * 40) is None
    assert extract_generation_meta(bytes(range(256)) * 20) is None


# -- 出力の形 ------------------------------------------------------------------


def test_output_validates_against_embedded_generation_meta_schema() -> None:
    meta = extract_generation_meta(_png({"parameters": _A1111_TEXT}))
    assert meta is not None
    EmbeddedGenerationMeta.model_validate(meta)


def test_output_key_order() -> None:
    meta = extract_generation_meta(_png({"parameters": _A1111_TEXT}))
    assert meta is not None
    assert list(meta.keys()) == [
        "schema",
        "tool",
        "software",
        "prompt",
        "negative_prompt",
        "model",
        "seed",
        "params",
        "raw",
        "truncated",
    ]
    assert meta["schema"] == SCHEMA
