"""ComfyUI の「Export (API)」で書き出した形(API 形式)を模したテスト用グラフ。

配線は ComfyUI の実際の API 形式のとおり `["ノードID", 出力番号]` で表す。
タイトル(`gakei:*`)は付けていない(クラス名からの推定を確かめるための素の形)。
"""

from __future__ import annotations

import copy
from typing import Any

T2I_GRAPH: dict[str, Any] = {
    "4": {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"},
    },
    "5": {
        "class_type": "EmptyLatentImage",
        "inputs": {"width": 512, "height": 512, "batch_size": 1},
    },
    "6": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "a photo of a cat, studio lighting", "clip": ["4", 1]},
    },
    "7": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "blurry, low quality", "clip": ["4", 1]},
    },
    "3": {
        "class_type": "KSampler",
        "inputs": {
            "seed": 156680208700286,
            "steps": 20,
            "cfg": 8.0,
            "sampler_name": "euler",
            "scheduler": "normal",
            "denoise": 1.0,
            "model": ["4", 0],
            "positive": ["6", 0],
            "negative": ["7", 0],
            "latent_image": ["5", 0],
        },
    },
    "8": {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["3", 0], "vae": ["4", 2]},
    },
    "9": {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": "gakei", "images": ["8", 0]},
    },
}


IMG2IMG_GRAPH: dict[str, Any] = {
    "4": {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"},
    },
    "6": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "improve the details, sharp focus", "clip": ["4", 1]},
    },
    "7": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "blurry, low quality", "clip": ["4", 1]},
    },
    "10": {
        "class_type": "LoadImage",
        "inputs": {"image": "input.png"},
    },
    "11": {
        "class_type": "VAEEncode",
        "inputs": {"pixels": ["10", 0], "vae": ["4", 2]},
    },
    "3": {
        "class_type": "KSampler",
        "inputs": {
            "seed": 1,
            "steps": 20,
            "cfg": 7.5,
            "sampler_name": "euler",
            "scheduler": "normal",
            "denoise": 0.6,
            "model": ["4", 0],
            "positive": ["6", 0],
            "negative": ["7", 0],
            "latent_image": ["11", 0],
        },
    },
    "8": {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["3", 0], "vae": ["4", 2]},
    },
    "9": {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": "gakei", "images": ["8", 0]},
    },
}


INPAINT_GRAPH: dict[str, Any] = {
    "4": {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"},
    },
    "6": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "fill the masked area with flowers", "clip": ["4", 1]},
    },
    "7": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "blurry, low quality", "clip": ["4", 1]},
    },
    "10": {
        "class_type": "LoadImage",
        "inputs": {"image": "input.png"},
    },
    "12": {
        "class_type": "LoadImageMask",
        "inputs": {"image": "mask.png", "channel": "alpha"},
    },
    "13": {
        "class_type": "VAEEncode",
        "inputs": {"pixels": ["10", 0], "vae": ["4", 2]},
    },
    "14": {
        "class_type": "SetLatentNoiseMask",
        "inputs": {"samples": ["13", 0], "mask": ["12", 0]},
    },
    "3": {
        "class_type": "KSampler",
        "inputs": {
            "seed": 1,
            "steps": 20,
            "cfg": 7.5,
            "sampler_name": "euler",
            "scheduler": "normal",
            "denoise": 1.0,
            "model": ["4", 0],
            "positive": ["6", 0],
            "negative": ["7", 0],
            "latent_image": ["14", 0],
        },
    },
    "8": {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["3", 0], "vae": ["4", 2]},
    },
    "9": {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": "gakei", "images": ["8", 0]},
    },
}


def clone(graph: dict[str, Any]) -> dict[str, Any]:
    """テストがミューテートしても元のグラフを壊さないための deep copy。"""
    return copy.deepcopy(graph)


# QWEN_LIKE_GRAPH: サブグラフ展開後のノード ID("459:452" のような形)、
# TextEncodeQwenImage21(prompt/negative_prompt が同じノードの出力 0/1)、
# ResolutionSelector -> EmptyLatentImage の width/height、SaveImageAdvanced、
# PreviewImage(出力の候補にならないことを確かめる)を持つ最小のグラフ。
# ノードのタイトル(gakei:*)は付けていない(ヒューリスティックからの推定を確かめるため)。
# モデル名はこの PC 固有の情報を避け、架空の名前にしてある。
QWEN_LIKE_GRAPH: dict[str, Any] = {
    "13": {
        "class_type": "ResolutionSelector",
        "inputs": {"aspect_ratio": "1:1 (Square)", "megapixels": 1.0, "multiple": 8},
    },
    "459:451": {
        "class_type": "UnetLoaderGGUF",
        "inputs": {"unet_name": "model.safetensors"},
    },
    "459:452": {
        "class_type": "TextEncodeQwenImage21",
        "inputs": {
            "prompt": "a cat sitting on a windowsill, soft afternoon light",
            "negative_prompt": "blurry, low quality",
            "resolution": 1024,
            "clip": ["459:453", 0],
        },
    },
    "459:453": {
        "class_type": "CLIPLoader",
        "inputs": {"clip_name": "clip.safetensors", "type": "qwen_image", "device": "default"},
    },
    "459:454": {
        "class_type": "VAELoader",
        "inputs": {"vae_name": "vae.safetensors"},
    },
    "459:456": {
        "class_type": "EmptyLatentImage",
        "inputs": {"width": ["13", 0], "height": ["13", 1], "batch_size": 1},
    },
    "459:457": {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["459:458", 0], "vae": ["459:454", 0]},
    },
    "459:458": {
        "class_type": "KSampler",
        "inputs": {
            "seed": 12345,
            "steps": 25,
            "cfg": 0.7,
            "sampler_name": "euler",
            "scheduler": "simple",
            "denoise": 1,
            "model": ["459:451", 0],
            "positive": ["459:452", 0],
            "negative": ["459:452", 1],
            "latent_image": ["459:456", 0],
        },
    },
    "461": {
        "class_type": "SaveImageAdvanced",
        "inputs": {
            "filename_prefix": "gakei",
            "format": "png",
            "format.bit_depth": "8-bit",
            "format.input_color_space": "sRGB",
            "images": ["459:457", 0],
        },
    },
    "999": {
        "class_type": "PreviewImage",
        "inputs": {"images": ["459:457", 0]},
    },
}


# QWEN_LIKE_GRAPH の各ノードに対応する `/object_info` の抜粋。ComfyUI の新形式(V3)の
# COMBO・COMFY_DYNAMICCOMBO_V3 と、`control_after_generate` を持つ seed を含む。
QWEN_LIKE_OBJECT_INFO: dict[str, Any] = {
    "ResolutionSelector": {
        "input": {
            "required": {
                "aspect_ratio": [
                    "COMBO",
                    {
                        "default": "1:1 (Square)",
                        "multiselect": False,
                        "options": [
                            "1:1 (Square)",
                            "2:3 (Portrait Photo)",
                            "16:9 (Widescreen)",
                        ],
                    },
                ],
                "megapixels": ["FLOAT", {"default": 1.0, "min": 0.1, "max": 16.0, "step": 0.1}],
                "multiple": ["INT", {"default": 8, "min": 8, "max": 128, "step": 4}],
            }
        },
        "output_node": False,
    },
    "TextEncodeQwenImage21": {
        "input": {
            "required": {
                "clip": ["CLIP", {}],
                "prompt": ["STRING", {"multiline": True}],
                "negative_prompt": ["STRING", {"multiline": True}],
                "resolution": ["INT", {"default": 1024, "min": 64, "max": 8192}],
            }
        },
        "output_node": False,
    },
    "SaveImageAdvanced": {
        "input": {
            "required": {
                "filename_prefix": ["STRING", {"default": "ComfyUI"}],
                "format": [
                    "COMFY_DYNAMICCOMBO_V3",
                    {
                        "options": [
                            {"key": "png", "inputs": {}},
                            {"key": "jpeg", "inputs": {}},
                            {"key": "webp", "inputs": {}},
                        ]
                    },
                ],
                "images": ["IMAGE", {}],
            }
        },
        "output_node": True,
    },
    "KSampler": {
        "input": {
            "required": {
                "seed": [
                    "INT",
                    {
                        "default": 0,
                        "min": 0,
                        "max": 18446744073709551615,
                        "control_after_generate": True,
                    },
                ],
                "sampler_name": [["euler", "euler_ancestral", "dpmpp_2m"], {}],
            }
        },
        "output_node": False,
    },
    "PreviewImage": {
        "input": {"required": {"images": ["IMAGE", {}]}},
        "output_node": True,
    },
}


# -- ComfyUI 公式テンプレート 22 本の検証で見つかった問題(2026-09-23)を再現する
# 最小のグラフ。モデル名は架空のもの(`model.safetensors` など)、プロンプトは無害な
# 英文にしてある。実物のテンプレートやこの PC のモデル名は使わない。


# PrimitiveInt に切り出された steps と、同じく PrimitiveInt に切り出された seed を持つ
# グラフ。KSampler.seed が配線(Primitive ノードへの配線)になっているケースと、
# steps 用の汎用 PrimitiveInt が seed と誤認されないことの両方を確かめる。
PRIMITIVE_SEED_GRAPH: dict[str, Any] = {
    "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "model.safetensors"}},
    "6": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "a cup of coffee on a wooden table", "clip": ["4", 1]},
    },
    "7": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "blurry, low quality", "clip": ["4", 1]},
    },
    "5": {
        "class_type": "EmptyLatentImage",
        "inputs": {"width": 1024, "height": 1024, "batch_size": 1},
    },
    "20": {"class_type": "PrimitiveInt", "inputs": {"value": 20}},  # steps 用に切り出した値
    "21": {"class_type": "PrimitiveInt", "inputs": {"value": 123456}},  # seed 用に切り出した値
    "3": {
        "class_type": "KSampler",
        "inputs": {
            "seed": ["21", 0],
            "steps": ["20", 0],
            "cfg": 8.0,
            "sampler_name": "euler",
            "scheduler": "normal",
            "denoise": 1.0,
            "model": ["4", 0],
            "positive": ["6", 0],
            "negative": ["7", 0],
            "latent_image": ["5", 0],
        },
    },
    "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
    "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "gakei", "images": ["8", 0]}},
}


# PRIMITIVE_SEED_GRAPH に対応する /object_info の抜粋。PrimitiveInt.value は
# control_after_generate: "fixed"(文字列。real ComfyUI の挙動)、KSampler.seed は
# control_after_generate: true(真偽値)。
PRIMITIVE_SEED_OBJECT_INFO: dict[str, Any] = {
    "PrimitiveInt": {
        "input": {
            "required": {
                "value": [
                    "INT",
                    {
                        "default": 0,
                        "min": -(2**31),
                        "max": 2**31 - 1,
                        "control_after_generate": "fixed",
                    },
                ]
            }
        },
        "output_node": False,
    },
    "KSampler": {
        "input": {
            "required": {
                "seed": [
                    "INT",
                    {
                        "default": 0,
                        "min": 0,
                        "max": 18446744073709551615,
                        "control_after_generate": True,
                    },
                ],
                "steps": ["INT", {"default": 20, "min": 1, "max": 10000}],
            }
        },
        "output_node": False,
    },
}


# class_type が EmptySD3LatentImage(完全一致ではないので EmptyLatentImage 以外も
# 対象になることを確かめる)で、width/height が PrimitiveInt への配線になっているグラフ。
PRIMITIVE_LATENT_SIZE_GRAPH: dict[str, Any] = {
    "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "model.safetensors"}},
    "6": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "a lighthouse at sunset", "clip": ["4", 1]},
    },
    "7": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "blurry, low quality", "clip": ["4", 1]},
    },
    "30": {"class_type": "PrimitiveInt", "inputs": {"value": 1024}},
    "31": {"class_type": "PrimitiveInt", "inputs": {"value": 768}},
    "5": {
        "class_type": "EmptySD3LatentImage",
        "inputs": {"width": ["30", 0], "height": ["31", 0], "batch_size": 1},
    },
    "3": {
        "class_type": "KSampler",
        "inputs": {
            "seed": 1,
            "steps": 20,
            "cfg": 8.0,
            "sampler_name": "euler",
            "scheduler": "normal",
            "denoise": 1.0,
            "model": ["4", 0],
            "positive": ["6", 0],
            "negative": ["7", 0],
            "latent_image": ["5", 0],
        },
    },
    "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
    "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "gakei", "images": ["8", 0]}},
}


# サンプラーの positive が FluxGuidance(素通り)経由で CLIPTextEncode に、negative が
# ConditioningZeroOut(空にするノード)経由になっているグラフ(flux_dev 系のテンプレート
# 相当)。edit(LoadImage あり)。
FLUX_GUIDANCE_GRAPH: dict[str, Any] = {
    "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "model.safetensors"}},
    "6": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "a red bicycle leaning on a brick wall", "clip": ["4", 1]},
    },
    "40": {"class_type": "FluxGuidance", "inputs": {"conditioning": ["6", 0], "guidance": 3.5}},
    "41": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["6", 0]}},
    "10": {"class_type": "LoadImage", "inputs": {"image": "input.png"}},
    "11": {"class_type": "VAEEncode", "inputs": {"pixels": ["10", 0], "vae": ["4", 2]}},
    "3": {
        "class_type": "KSampler",
        "inputs": {
            "seed": 1,
            "steps": 20,
            "cfg": 1.0,
            "sampler_name": "euler",
            "scheduler": "simple",
            "denoise": 1.0,
            "model": ["4", 0],
            "positive": ["40", 0],
            "negative": ["41", 0],
            "latent_image": ["11", 0],
        },
    },
    "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
    "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "gakei", "images": ["8", 0]}},
}


# CLIPTextEncode.text が直値ではなく PrimitiveStringMultiline への配線になっている
# グラフ(「値をノードとして切り出す」新しい流儀のテンプレート相当)。
PRIMITIVE_TEXT_GRAPH: dict[str, Any] = {
    "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "model.safetensors"}},
    "50": {
        "class_type": "PrimitiveStringMultiline",
        "inputs": {"value": "a snowy mountain village at dawn"},
    },
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": ["50", 0], "clip": ["4", 1]}},
    "7": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "blurry, low quality", "clip": ["4", 1]},
    },
    "5": {
        "class_type": "EmptyLatentImage",
        "inputs": {"width": 1024, "height": 1024, "batch_size": 1},
    },
    "3": {
        "class_type": "KSampler",
        "inputs": {
            "seed": 1,
            "steps": 20,
            "cfg": 8.0,
            "sampler_name": "euler",
            "scheduler": "normal",
            "denoise": 1.0,
            "model": ["4", 0],
            "positive": ["6", 0],
            "negative": ["7", 0],
            "latent_image": ["5", 0],
        },
    },
    "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
    "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "gakei", "images": ["8", 0]}},
}


# サンプラーが positive/negative ではなく cond1/negative を持つ DualCFGGuider
# (OmniGen2 系のテンプレート相当)。
DUAL_CFG_GRAPH: dict[str, Any] = {
    "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "model.safetensors"}},
    "6": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "a bowl of ramen with steam rising", "clip": ["4", 1]},
    },
    "7": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "blurry, low quality", "clip": ["4", 1]},
    },
    "3": {
        "class_type": "DualCFGGuider",
        "inputs": {
            "model": ["4", 0],
            "cond1": ["6", 0],
            "cond2": ["6", 0],
            "negative": ["7", 0],
            "cfg_conds": 2.0,
        },
    },
    "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "gakei", "images": ["3", 0]}},
}


# SamplerCustomAdvanced が guider 経由で BasicGuider(conditioning のみ。negative の
# 概念が無い)を指すグラフ。
GUIDER_BASIC_GRAPH: dict[str, Any] = {
    "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "model.safetensors"}},
    "6": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "an antique pocket watch on a map", "clip": ["4", 1]},
    },
    "30": {"class_type": "BasicGuider", "inputs": {"model": ["4", 0], "conditioning": ["6", 0]}},
    "31": {"class_type": "SamplerCustomAdvanced", "inputs": {"guider": ["30", 0]}},
    "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "gakei", "images": ["31", 0]}},
}


# 出力ノードの候補に SaveImage と ImageCompare(比較用の UI ノード。output_node: true を
# 返すが画像は保存しない)が両方ある場合。Save を含むものが優先されるべきケース。
OUTPUT_FILTER_GRAPH: dict[str, Any] = clone(T2I_GRAPH)
OUTPUT_FILTER_GRAPH["70"] = {
    "class_type": "ImageCompare",
    "inputs": {"image_a": ["8", 0], "image_b": ["8", 0]},
}

OUTPUT_FILTER_OBJECT_INFO: dict[str, Any] = {
    "SaveImage": {
        "input": {"required": {"images": ["IMAGE", {}], "filename_prefix": ["STRING", {}]}},
        "output_node": True,
    },
    "ImageCompare": {
        "input": {"required": {"image_a": ["IMAGE", {}], "image_b": ["IMAGE", {}]}},
        "output_node": True,
    },
}


# positive が2段の素通りノード(ReferenceLatent → FluxKontextMultiReferenceLatentMethod)
# を経由して CLIPTextEncode に届き、negative は ConditioningZeroOut で空にされている
# グラフ(image_qwen_image_edit_2511 相当)。reference_latents_method という enum の
# 入力をプロンプトと誤認しないことも確かめる。
MULTI_HOP_PROMPT_GRAPH: dict[str, Any] = {
    "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "model.safetensors"}},
    "6": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "swap the fabric texture to denim", "clip": ["4", 1]},
    },
    "90": {
        "class_type": "ReferenceLatent",
        "inputs": {"conditioning": ["6", 0], "latent": ["91", 0]},
    },
    "92": {
        "class_type": "FluxKontextMultiReferenceLatentMethod",
        "inputs": {"conditioning": ["90", 0], "reference_latents_method": "uxo"},
    },
    "93": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["6", 0]}},
    "10": {"class_type": "LoadImage", "inputs": {"image": "input.png"}},
    "11": {"class_type": "VAEEncode", "inputs": {"pixels": ["10", 0], "vae": ["4", 2]}},
    "3": {
        "class_type": "KSampler",
        "inputs": {
            "seed": 1,
            "steps": 20,
            "cfg": 1.0,
            "sampler_name": "euler",
            "scheduler": "simple",
            "denoise": 1.0,
            "model": ["4", 0],
            "positive": ["92", 0],
            "negative": ["93", 0],
            "latent_image": ["11", 0],
        },
    },
    "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
    "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "gakei", "images": ["8", 0]}},
}


# MULTI_HOP_PROMPT_GRAPH に対応する /object_info の抜粋。型(CONDITIONING / STRING /
# 選択肢の COMBO)から辿ることを確かめる。
MULTI_HOP_OBJECT_INFO: dict[str, Any] = {
    "ReferenceLatent": {
        "input": {"required": {"conditioning": ["CONDITIONING", {}], "latent": ["LATENT", {}]}},
        "output_node": False,
    },
    "FluxKontextMultiReferenceLatentMethod": {
        "input": {
            "required": {
                "conditioning": ["CONDITIONING", {}],
                "reference_latents_method": [["uxo", "index"], {}],
            }
        },
        "output_node": False,
    },
    "ConditioningZeroOut": {
        "input": {"required": {"conditioning": ["CONDITIONING", {}]}},
        "output_node": False,
    },
    "CLIPTextEncode": {
        "input": {"required": {"text": ["STRING", {"multiline": True}], "clip": ["CLIP", {}]}},
        "output_node": False,
    },
}


# マスクの渡し方が LoadImage / LoadImageMask のどちらでもない、専用の Painter ノード
# (キャンバスに直接描く方式)を使うグラフ。マスクの提案は見つからないはず。
PAINTER_MASK_GRAPH: dict[str, Any] = {
    "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "model.safetensors"}},
    "6": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "restore the torn photograph", "clip": ["4", 1]},
    },
    "7": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "blurry, low quality", "clip": ["4", 1]},
    },
    "10": {"class_type": "LoadImage", "inputs": {"image": "input.png"}},
    "80": {"class_type": "MaskEditorPainter", "inputs": {"image": ["10", 0]}},
    "11": {"class_type": "VAEEncode", "inputs": {"pixels": ["10", 0], "vae": ["4", 2]}},
    "3": {
        "class_type": "KSampler",
        "inputs": {
            "seed": 1,
            "steps": 20,
            "cfg": 7.5,
            "sampler_name": "euler",
            "scheduler": "normal",
            "denoise": 1.0,
            "model": ["4", 0],
            "positive": ["6", 0],
            "negative": ["7", 0],
            "latent_image": ["11", 0],
        },
    },
    "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
    "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "gakei", "images": ["8", 0]}},
}


# LoadImage が2枚あるグラフ(参照画像2枚を取るテンプレート相当。可変長入力の番号もタイトルも
# 無いので、テンプレート内の出現順で2つの枠として提案されるはず)。
TWO_LOAD_IMAGE_GRAPH: dict[str, Any] = clone(IMG2IMG_GRAPH)
TWO_LOAD_IMAGE_GRAPH["15"] = {"class_type": "LoadImage", "inputs": {"image": "reference.png"}}


# Qwen Image 2.1 の画像編集テンプレート相当(ADR-0013 3節の例)。TextEncodeQwenImage21 の
# 可変長の画像入力(`images.image_1`、`images.image_2`)が2枚の LoadImage につながる。
# image_2 の LoadImage(id="410")の方が image_1 の LoadImage(id="480")より id が小さい
# ことで、並び順が id ではなく `_1`/`_2` の番号で決まることを確かめられるようにしてある。
# モデル名・画像名はこの PC 固有の情報を避け、汎用のものにしてある。
QWEN_EDIT_GRAPH: dict[str, Any] = {
    "480": {"class_type": "LoadImage", "inputs": {"image": "person.png"}},
    "410": {"class_type": "LoadImage", "inputs": {"image": "garment.png"}},
    "451": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": "model.safetensors"}},
    "453": {
        "class_type": "CLIPLoader",
        "inputs": {"clip_name": "clip.safetensors", "type": "qwen_image", "device": "default"},
    },
    "454": {"class_type": "VAELoader", "inputs": {"vae_name": "vae.safetensors"}},
    "452": {
        "class_type": "TextEncodeQwenImage21",
        "inputs": {
            "clip": ["453", 0],
            "prompt": "put the garment on the person",
            "negative_prompt": "blurry, low quality",
            "resolution": 1024,
            "images.image_1": ["480", 0],
            "images.image_2": ["410", 0],
        },
    },
    "456": {
        "class_type": "EmptyLatentImage",
        "inputs": {"width": 1024, "height": 1024, "batch_size": 1},
    },
    "458": {
        "class_type": "KSampler",
        "inputs": {
            "seed": 12345,
            "steps": 25,
            "cfg": 0.7,
            "sampler_name": "euler",
            "scheduler": "simple",
            "denoise": 1,
            "model": ["451", 0],
            "positive": ["452", 0],
            "negative": ["452", 1],
            "latent_image": ["456", 0],
        },
    },
    "457": {"class_type": "VAEDecode", "inputs": {"samples": ["458", 0], "vae": ["454", 0]}},
    "461": {
        "class_type": "SaveImageAdvanced",
        "inputs": {"filename_prefix": "gakei", "images": ["457", 0]},
    },
    "462": {
        "class_type": "ImageCompare",
        "inputs": {"image_a": ["457", 0], "image_b": ["480", 0]},
    },
}


# 「プロンプト強化(prompt enhancer)」型のワークフロー: エンコーダーの prompt は直値では
# なく、正規表現置換(RegexReplace)の連鎖 → テキスト生成ノード(TextGenerate)→
# 文字列連結(StringConcatenate)を経て、複数の PrimitiveStringMultiline(固定のシステム
# プロンプト・利用者の編集指示・接尾辞)に行き着く。既存のヒューリスティックでは
# 素通りできない形なので、プロンプトの差し込み先は特定できず、手動選択の候補として
# 一致しそうな直値の文字列入力(PrimitiveStringMultiline の value)を提案できるはず。
# モデル名・正規表現の中身はこの PC 固有の情報を避け、汎用のものにしてある。
PROMPT_ENHANCER_GRAPH: dict[str, Any] = {
    "3": {
        "class_type": "KSampler",
        "inputs": {
            "seed": 1,
            "steps": 20,
            "cfg": 4.0,
            "sampler_name": "euler",
            "scheduler": "simple",
            "denoise": 1.0,
            "model": ["459:451", 0],
            "positive": ["459:474", 0],
            "negative": ["459:474", 0],
            "latent_image": ["5", 0],
        },
    },
    "5": {
        "class_type": "EmptyLatentImage",
        "inputs": {"width": 1024, "height": 1024, "batch_size": 1},
    },
    "459:451": {
        "class_type": "UnetLoaderGGUF",
        "inputs": {"unet_name": "model.safetensors"},
    },
    "459:453": {
        "class_type": "CLIPLoader",
        "inputs": {"clip_name": "clip.safetensors", "type": "qwen_image", "device": "default"},
    },
    "459:474": {
        "class_type": "TextEncodeQwenImage21",
        "inputs": {"prompt": ["487", 0], "clip": ["459:453", 0]},
    },
    "487": {
        "class_type": "RegexReplace",
        "inputs": {
            "string": ["486", 0],
            "regex_pattern": "pattern-a",
            "replace": "",
            "flags": "",
        },
    },
    "486": {
        "class_type": "RegexReplace",
        "inputs": {
            "string": ["485", 0],
            "regex_pattern": "pattern-b",
            "replace": "",
            "flags": "",
        },
    },
    "485": {
        "class_type": "RegexReplace",
        "inputs": {
            "string": ["483", 0],
            "regex_pattern": "pattern-c",
            "replace": "",
            "flags": "",
        },
    },
    "483": {
        "class_type": "TextGenerate",
        "inputs": {
            "prompt": ["482", 0],
            "model": "text-gen-model",
            "temperature": 0.7,
            "top_p": 0.9,
        },
    },
    "482": {
        "class_type": "StringConcatenate",
        "inputs": {"string_a": ["481", 0], "string_b": ["480", 0], "delimiter": ""},
    },
    "481": {
        "class_type": "StringConcatenate",
        "inputs": {"string_a": ["478", 0], "string_b": ["479", 0], "delimiter": ""},
    },
    "478": {
        "class_type": "PrimitiveStringMultiline",
        "inputs": {"value": "System prompt describing the enhancement task in detail."},
        "_meta": {"title": "PE system prompt (official, do not edit)"},
    },
    "479": {
        "class_type": "PrimitiveStringMultiline",
        "inputs": {"value": "the user's edit instruction goes here"},
        "_meta": {"title": "編集指示(日本語OK、ここを書き換えてください)"},
    },
    "480": {
        "class_type": "PrimitiveStringMultiline",
        "inputs": {"value": "Suffix appended after the instruction for formatting."},
        "_meta": {"title": "PE suffix(出力フォーマットの指定)"},
    },
    "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["459:453", 0]}},
    "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "gakei", "images": ["8", 0]}},
}


# PROMPT_ENHANCER_GRAPH と同じく素通りできない形だが、辿った先に候補になりそうな直値の
# 文字列入力が1つも無い(数値だけ)。この場合は候補の一覧を出さず、これまで通りの
# 「手動で選択してください」に留まるはず。
NO_PROMPT_CANDIDATES_GRAPH: dict[str, Any] = {
    "3": {
        "class_type": "KSampler",
        "inputs": {
            "seed": 1,
            "steps": 20,
            "cfg": 8.0,
            "sampler_name": "euler",
            "scheduler": "normal",
            "denoise": 1.0,
            "model": ["4", 0],
            "positive": ["6", 0],
            "negative": ["6", 0],
            "latent_image": ["5", 0],
        },
    },
    "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "model.safetensors"}},
    "5": {
        "class_type": "EmptyLatentImage",
        "inputs": {"width": 1024, "height": 1024, "batch_size": 1},
    },
    "6": {
        "class_type": "TextEncodeQwenImage21",
        "inputs": {"prompt": ["7", 0], "clip": ["4", 1]},
    },
    "7": {"class_type": "SomeMathNode", "inputs": {"factor": ["8", 0]}},
    "8": {"class_type": "SomeConstant", "inputs": {"amount": 5}},
    "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "gakei", "images": ["3", 0]}},
}


# Qwen Image 2.1 の t2i テンプレート相当(ADR-0030)。Prompt Enhancer(TextGenerate)が
# 利用者のプロンプトを書き直し、切り替え(ComfySwitchNode)を経て PreviewAny(472)を
# 通ってからエンコーダーの prompt に入る。最終プロンプトのノードは 472 と推定されるはず。
# モデル名などは汎用のものにしてある。
QWEN21_PE_GRAPH: dict[str, Any] = {
    "1": {
        "class_type": "PrimitiveStringMultiline",
        "inputs": {"value": ""},
        "_meta": {"title": "prompt"},
    },
    "2": {"class_type": "PrimitiveInt", "inputs": {"value": 1}, "_meta": {"title": "seed"}},
    "3": {
        "class_type": "PrimitiveBoolean",
        "inputs": {"value": True},
        "_meta": {"title": "prompt_enhance"},
    },
    "4": {
        "class_type": "PrimitiveBoolean",
        "inputs": {"value": False},
        "_meta": {"title": "transparent"},
    },
    "451": {"class_type": "UNETLoader", "inputs": {"unet_name": "model.safetensors"}},
    "452": {
        "class_type": "TextEncodeQwenImage21",
        "inputs": {"clip": ["453", 0], "prompt": ["472", 0], "negative_prompt": ""},
    },
    "453": {"class_type": "CLIPLoader", "inputs": {"clip_name": "clip.safetensors"}},
    "454": {"class_type": "VAELoader", "inputs": {"vae_name": "vae.safetensors"}},
    "456": {
        "class_type": "EmptyLatentImage",
        "inputs": {"width": 1024, "height": 1024, "batch_size": 1},
    },
    "457": {"class_type": "VAEDecode", "inputs": {"samples": ["458", 0], "vae": ["454", 0]}},
    "458": {
        "class_type": "KSampler",
        "inputs": {
            "model": ["451", 0],
            "positive": ["452", 0],
            "negative": ["452", 1],
            "latent_image": ["456", 0],
            "seed": ["2", 0],
            "steps": 20,
            "cfg": 4.0,
            "sampler_name": "euler",
            "scheduler": "simple",
            "denoise": 1.0,
        },
    },
    "461": {
        "class_type": "SaveImageAdvanced",
        "inputs": {"images": ["457", 0], "filename_prefix": "gakei"},
    },
    "471": {
        "class_type": "TextGenerate",
        "inputs": {"clip": ["473", 0], "prompt": ["1", 0], "sampling_mode.seed": ["2", 0]},
    },
    "472": {"class_type": "PreviewAny", "inputs": {"source": ["482", 0]}},
    "473": {"class_type": "CLIPLoader", "inputs": {"clip_name": "pe.safetensors"}},
    "474": {
        "class_type": "ComfySwitchNode",
        "inputs": {"switch": ["3", 0], "on_false": ["1", 0], "on_true": ["471", 0]},
    },
    "481": {"class_type": "StringFormat", "inputs": {"values.a": ["1", 0]}},
    "482": {
        "class_type": "ComfySwitchNode",
        "inputs": {"switch": ["4", 0], "on_false": ["474", 0], "on_true": ["481", 0]},
    },
}
