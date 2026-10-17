"""合成のワークフロー S1〜S12(ADR-0052 の読み取りの規則を確かめる)。

標準の class_type と架空の値(プロンプトは無害な英文、ファイル名は `model.safetensors` など)
だけで作ってある。`SYNTH_GT` は各項目の正解(許される答えの並び)、`SYNTH_EXPOSED` は公開
パラメーターの候補に最低限含まれてほしいもの。
"""

from __future__ import annotations

from typing import Any


def W(nid: str, port: int = 0) -> list:
    return [nid, port]


def N(class_type: str, title: str | None = None, **inputs: Any) -> dict[str, Any]:
    node: dict[str, Any] = {"class_type": class_type, "inputs": inputs}
    node["_meta"] = {"title": title or class_type}
    return node


SYNTH: dict[str, dict[str, Any]] = {
    # SD1.5 の標準の t2i
    "S1_sd15_t2i": {
        "4": N("CheckpointLoaderSimple", ckpt_name="model.safetensors"),
        "6": N(
            "CLIPTextEncode",
            "CLIP Text Encode (Prompt)",
            text="a cat sitting on a sofa",
            clip=W("4", 1),
        ),
        "7": N(
            "CLIPTextEncode",
            "CLIP Text Encode (Prompt)",
            text="blurry, low quality",
            clip=W("4", 1),
        ),
        "5": N("EmptyLatentImage", width=512, height=512, batch_size=1),
        "3": N(
            "KSampler",
            seed=123,
            steps=20,
            cfg=7.0,
            sampler_name="euler",
            scheduler="normal",
            denoise=1.0,
            model=W("4"),
            positive=W("6"),
            negative=W("7"),
            latent_image=W("5"),
        ),
        "8": N("VAEDecode", samples=W("3"), vae=W("4", 2)),
        "9": N("SaveImage", images=W("8"), filename_prefix="ComfyUI"),
    },
    # 本物のインペイント: LoadImage の MASK を VAEEncodeForInpaint に渡す → image_alpha が正解
    "S2_inpaint_alpha": {
        "1": N("CheckpointLoaderSimple", ckpt_name="inpaint.safetensors"),
        "2": N("LoadImage", image="example.png"),
        "3": N("VAEEncodeForInpaint", pixels=W("2"), vae=W("1", 2), mask=W("2", 1), grow_mask_by=6),
        "4": N("CLIPTextEncode", text="a red hat", clip=W("1", 1)),
        "5": N("CLIPTextEncode", text="", clip=W("1", 1)),
        "6": N(
            "KSampler",
            seed=7,
            steps=25,
            cfg=6.5,
            sampler_name="dpmpp_2m",
            scheduler="karras",
            denoise=1.0,
            model=W("1"),
            positive=W("4"),
            negative=W("5"),
            latent_image=W("3"),
        ),
        "7": N("VAEDecode", samples=W("6"), vae=W("1", 2)),
        "8": N("SaveImage", images=W("7"), filename_prefix="inpaint"),
    },
    # 別ファイルのマスク(LoadImageMask)+ InpaintModelConditioning。
    # 入力画像の確認用の PreviewImage つき
    "S3_inpaint_maskfile": {
        "1": N("CheckpointLoaderSimple", ckpt_name="model.safetensors"),
        "2": N("LoadImage", "Input image", image="photo.png"),
        "3": N("LoadImageMask", "Mask", image="mask.png", channel="red"),
        "4": N("GrowMask", mask=W("3"), expand=8, tapered_corners=True),
        "5": N("CLIPTextEncode", text="replace with a wooden table", clip=W("1", 1)),
        "6": N("CLIPTextEncode", text="artifacts", clip=W("1", 1)),
        "7": N(
            "InpaintModelConditioning",
            positive=W("5"),
            negative=W("6"),
            vae=W("1", 2),
            pixels=W("2"),
            mask=W("4"),
            noise_mask=True,
        ),
        "8": N(
            "KSampler",
            seed=99,
            steps=30,
            cfg=5.0,
            sampler_name="euler",
            scheduler="simple",
            denoise=1.0,
            model=W("1"),
            positive=W("7", 0),
            negative=W("7", 1),
            latent_image=W("7", 2),
        ),
        "9": N("VAEDecode", samples=W("8"), vae=W("1", 2)),
        "10": N("SaveImage", images=W("9"), filename_prefix="fill"),
        "11": N("PreviewImage", "Preview input", images=W("2")),
    },
    # Flux 系のカスタムサンプラー。ネガティブなし、シードは RandomNoise、Preview も並ぶ
    "S4_flux_custom": {
        "1": N("UNETLoader", unet_name="flux.safetensors", weight_dtype="default"),
        "2": N(
            "DualCLIPLoader",
            clip_name1="clip_l.safetensors",
            clip_name2="t5.safetensors",
            type="flux",
            device="default",
        ),
        "3": N("VAELoader", vae_name="ae.safetensors"),
        "4": N("CLIPTextEncode", text="a lighthouse at dusk", clip=W("2")),
        "5": N("FluxGuidance", conditioning=W("4"), guidance=3.5),
        "6": N("BasicGuider", model=W("1"), conditioning=W("5")),
        "7": N("RandomNoise", noise_seed=42),
        "8": N("KSamplerSelect", sampler_name="euler"),
        "9": N("BasicScheduler", model=W("1"), scheduler="simple", steps=20, denoise=1.0),
        "10": N("EmptySD3LatentImage", width=1024, height=1024, batch_size=1),
        "11": N(
            "SamplerCustomAdvanced",
            noise=W("7"),
            guider=W("6"),
            sampler=W("8"),
            sigmas=W("9"),
            latent_image=W("10"),
        ),
        "12": N("VAEDecode", samples=W("11"), vae=W("3")),
        "13": N("PreviewImage", images=W("12")),
        "14": N("SaveImage", images=W("12"), filename_prefix="flux"),
    },
    # SDXL のベース + リファイナー。プロンプト・ネガティブ・シードを Primitive で共有
    "S5_sdxl_refiner": {
        "1": N("CheckpointLoaderSimple", "Base", ckpt_name="base.safetensors"),
        "2": N("CheckpointLoaderSimple", "Refiner", ckpt_name="refiner.safetensors"),
        "3": N("PrimitiveStringMultiline", "Positive", value="a portrait of an astronaut"),
        "4": N("PrimitiveStringMultiline", "Negative", value="text, watermark"),
        "5": N("CLIPTextEncode", text=W("3"), clip=W("1", 1)),
        "6": N("CLIPTextEncode", text=W("4"), clip=W("1", 1)),
        "7": N("CLIPTextEncode", text=W("3"), clip=W("2", 1)),
        "8": N("CLIPTextEncode", text=W("4"), clip=W("2", 1)),
        "9": N("EmptyLatentImage", width=1024, height=1024, batch_size=1),
        "10": N("PrimitiveInt", value=1234),
        "11": N(
            "KSamplerAdvanced",
            "Base sampler",
            model=W("1"),
            add_noise="enable",
            noise_seed=W("10"),
            steps=25,
            cfg=7.0,
            sampler_name="euler",
            scheduler="normal",
            positive=W("5"),
            negative=W("6"),
            latent_image=W("9"),
            start_at_step=0,
            end_at_step=20,
            return_with_leftover_noise="enable",
        ),
        "12": N(
            "KSamplerAdvanced",
            "Refiner sampler",
            model=W("2"),
            add_noise="disable",
            noise_seed=W("10"),
            steps=25,
            cfg=7.0,
            sampler_name="euler",
            scheduler="normal",
            positive=W("7"),
            negative=W("8"),
            latent_image=W("11"),
            start_at_step=20,
            end_at_step=10000,
            return_with_leftover_noise="disable",
        ),
        "13": N("VAEDecode", samples=W("12"), vae=W("2", 2)),
        "14": N("SaveImage", images=W("13"), filename_prefix="sdxl"),
    },
    # Qwen Image Edit Plus の3枚入力。ノード ID の順(10,11,12)と枠の番号
    # (image1=11, image2=10, image3=12)がずれる
    "S6_qwen_edit_plus_3img": {
        "1": N("UNETLoader", unet_name="qwen_edit.safetensors", weight_dtype="default"),
        "2": N("CLIPLoader", clip_name="qwen_vl.safetensors", type="qwen_image", device="default"),
        "3": N("VAELoader", vae_name="qwen_vae.safetensors"),
        "10": N("LoadImage", image="b.png"),
        "11": N("LoadImage", image="a.png"),
        "12": N("LoadImage", image="c.png"),
        "13": N(
            "ImageScaleToTotalPixels",
            image=W("11"),
            upscale_method="lanczos",
            megapixels=1.0,
            resolution_steps=1,
        ),
        "14": N(
            "TextEncodeQwenImageEditPlus",
            clip=W("2"),
            prompt="put the person from image 2 into image 1",
            vae=W("3"),
            image1=W("13"),
            image2=W("10"),
            image3=W("12"),
        ),
        "15": N(
            "TextEncodeQwenImageEditPlus",
            clip=W("2"),
            prompt="",
            vae=W("3"),
            image1=W("13"),
            image2=W("10"),
            image3=W("12"),
        ),
        "16": N("VAEEncode", pixels=W("13"), vae=W("3")),
        "20": N("ModelSamplingAuraFlow", model=W("1"), shift=3.0),
        "17": N(
            "KSampler",
            seed=5,
            steps=8,
            cfg=1.0,
            sampler_name="euler",
            scheduler="simple",
            denoise=1.0,
            model=W("20"),
            positive=W("14"),
            negative=W("15"),
            latent_image=W("16"),
        ),
        "18": N("VAEDecode", samples=W("17"), vae=W("3")),
        "19": N("SaveImage", images=W("18"), filename_prefix="edit"),
    },
}


def R(node: str, inp: str) -> tuple[str, str]:
    return (node, inp)


def gt(
    op, prompt, neg, seeds, w=None, h=None, b=None, images=(), mask=None, outputs=(), final=None
):
    return {
        "operation": [op],
        "prompt": [prompt],
        "negative_prompt": [neg],
        "seed": [frozenset(s) for s in seeds],
        "width": [w],
        "height": [h],
        "batch_size": [b],
        "images": [tuple(images)],
        "mask": mask if isinstance(mask, list) else [mask],
        "outputs": [frozenset(outputs)],
        "final_prompt": [final],
    }


SYNTH_GT = {
    "S1_sd15_t2i": gt(
        "generate",
        R("6", "text"),
        R("7", "text"),
        [{R("3", "seed")}],
        R("5", "width"),
        R("5", "height"),
        R("5", "batch_size"),
        outputs={"9"},
    ),
    "S2_inpaint_alpha": gt(
        "edit",
        R("4", "text"),
        R("5", "text"),
        [{R("6", "seed")}],
        images=[R("2", "image")],
        mask=("image_alpha", None, None),
        outputs={"8"},
    ),
    "S3_inpaint_maskfile": gt(
        "edit",
        R("5", "text"),
        R("6", "text"),
        [{R("8", "seed")}],
        images=[R("2", "image")],
        mask=("load_image_mask", "3", "image"),
        outputs={"10"},
    ),
    "S4_flux_custom": gt(
        "generate",
        R("4", "text"),
        None,
        [{R("7", "noise_seed")}],
        R("10", "width"),
        R("10", "height"),
        R("10", "batch_size"),
        outputs={"14"},
    ),
    "S5_sdxl_refiner": gt(
        "generate",
        R("3", "value"),
        R("4", "value"),
        [{R("10", "value")}],
        R("9", "width"),
        R("9", "height"),
        R("9", "batch_size"),
        outputs={"14"},
    ),
    "S6_qwen_edit_plus_3img": gt(
        "edit",
        R("14", "prompt"),
        R("15", "prompt"),
        [{R("17", "seed")}],
        images=[R("11", "image"), R("10", "image"), R("12", "image")],
        outputs={"19"},
    ),
}

# 公開パラメーターの「最低限あってほしいもの」(再現率だけ見る)。
SYNTH_EXPOSED = {
    "S1_sd15_t2i": {R("3", "steps"), R("3", "cfg")},
    "S2_inpaint_alpha": {R("6", "steps"), R("6", "cfg"), R("6", "denoise")},
    "S3_inpaint_maskfile": {R("8", "steps"), R("8", "cfg"), R("4", "expand")},
    "S4_flux_custom": {R("5", "guidance"), R("9", "steps")},
    "S5_sdxl_refiner": {R("11", "steps"), R("11", "cfg")},
    "S6_qwen_edit_plus_3img": {R("17", "steps"), R("17", "cfg")},
}

SYNTH.update(
    {
        # 2段のハイレゾ修正。サンプラーごとに別の定数シード、保存が2つ(途中と最終)
        "S7_hiresfix_2pass": {
            "1": N("CheckpointLoaderSimple", ckpt_name="model.safetensors"),
            "2": N(
                "LoraLoader",
                model=W("1"),
                clip=W("1", 1),
                lora_name="style.safetensors",
                strength_model=0.8,
                strength_clip=0.8,
            ),
            "3": N("CLIPTextEncode", text="a castle on a hill", clip=W("2", 1)),
            "4": N("CLIPTextEncode", text="lowres", clip=W("2", 1)),
            "5": N("EmptyLatentImage", width=768, height=512, batch_size=2),
            "6": N(
                "KSampler",
                "First pass",
                seed=11,
                steps=20,
                cfg=7.0,
                sampler_name="euler",
                scheduler="normal",
                denoise=1.0,
                model=W("2"),
                positive=W("3"),
                negative=W("4"),
                latent_image=W("5"),
            ),
            "7": N("VAEDecode", samples=W("6"), vae=W("1", 2)),
            "8": N("SaveImage", "Save first pass", images=W("7"), filename_prefix="base"),
            "9": N("LatentUpscaleBy", samples=W("6"), upscale_method="nearest-exact", scale_by=1.5),
            "10": N(
                "KSampler",
                "Second pass",
                seed=22,
                steps=15,
                cfg=7.0,
                sampler_name="euler",
                scheduler="normal",
                denoise=0.5,
                model=W("2"),
                positive=W("3"),
                negative=W("4"),
                latent_image=W("9"),
            ),
            "11": N("VAEDecode", samples=W("10"), vae=W("1", 2)),
            "12": N("SaveImage", "Save final", images=W("11"), filename_prefix="hires"),
        },
        # ControlNet: LoadImage はあるが、構図の参照として使う(edit になる。マスクなし)
        "S8_controlnet": {
            "1": N("CheckpointLoaderSimple", ckpt_name="model.safetensors"),
            "2": N("CLIPTextEncode", text="a modern living room", clip=W("1", 1)),
            "3": N("CLIPTextEncode", text="ugly", clip=W("1", 1)),
            "4": N("LoadImage", "Control image", image="pose.png"),
            "5": N("Canny", image=W("4"), low_threshold=0.4, high_threshold=0.8),
            "6": N("ControlNetLoader", control_net_name="canny.safetensors"),
            "7": N(
                "ControlNetApplyAdvanced",
                positive=W("2"),
                negative=W("3"),
                control_net=W("6"),
                image=W("5"),
                strength=0.9,
                start_percent=0.0,
                end_percent=1.0,
            ),
            "8": N("EmptyLatentImage", width=1024, height=768, batch_size=1),
            "9": N(
                "KSampler",
                seed=3,
                steps=25,
                cfg=6.0,
                sampler_name="euler",
                scheduler="normal",
                denoise=1.0,
                model=W("1"),
                positive=W("7", 0),
                negative=W("7", 1),
                latent_image=W("8"),
            ),
            "10": N("VAEDecode", samples=W("9"), vae=W("1", 2)),
            "11": N("SaveImage", images=W("10"), filename_prefix="cn"),
        },
        # 保存ノードが無く PreviewImage だけ(出力は空が正解。GAKEI では登録できない)
        "S9_preview_only": {
            "1": N("CheckpointLoaderSimple", ckpt_name="model.safetensors"),
            "2": N("CLIPTextEncode", text="a bowl of fruit", clip=W("1", 1)),
            "3": N("CLIPTextEncode", text="", clip=W("1", 1)),
            "4": N("EmptyLatentImage", width=512, height=512, batch_size=1),
            "5": N(
                "KSampler",
                seed=8,
                steps=20,
                cfg=7.0,
                sampler_name="euler",
                scheduler="normal",
                denoise=1.0,
                model=W("1"),
                positive=W("2"),
                negative=W("3"),
                latent_image=W("4"),
            ),
            "6": N("VAEDecode", samples=W("5"), vae=W("1", 2)),
            "7": N("PreviewImage", images=W("6")),
        },
        # 幅・高さ・枚数を PrimitiveInt から配線(Primitive の value が差し込み先。null ではない)
        "S10_primitive_size": {
            "1": N("CheckpointLoaderSimple", ckpt_name="model.safetensors"),
            "2": N("CLIPTextEncode", text="a mountain lake", clip=W("1", 1)),
            "3": N("CLIPTextEncode", text="haze", clip=W("1", 1)),
            "20": N("PrimitiveInt", "Width", value=832),
            "21": N("PrimitiveInt", "Height", value=1216),
            "22": N("PrimitiveInt", "Batch", value=1),
            "4": N("EmptyLatentImage", width=W("20"), height=W("21"), batch_size=W("22")),
            "5": N(
                "KSampler",
                seed=8,
                steps=20,
                cfg=7.0,
                sampler_name="euler",
                scheduler="normal",
                denoise=1.0,
                model=W("1"),
                positive=W("2"),
                negative=W("3"),
                latent_image=W("4"),
            ),
            "6": N("VAEDecode", samples=W("5"), vae=W("1", 2)),
            "7": N("SaveImage", images=W("6"), filename_prefix="size"),
        },
        # インペイント: LoadImage の MASK を反転して SetLatentNoiseMask と合成に使う → image_alpha
        "S11_inpaint_inverted": {
            "1": N("CheckpointLoaderSimple", ckpt_name="model.safetensors"),
            "2": N("LoadImage", image="room.png"),
            "3": N("InvertMask", mask=W("2", 1)),
            "4": N("VAEEncode", pixels=W("2"), vae=W("1", 2)),
            "5": N("SetLatentNoiseMask", samples=W("4"), mask=W("3")),
            "6": N("CLIPTextEncode", text="a potted plant", clip=W("1", 1)),
            "7": N("CLIPTextEncode", text="", clip=W("1", 1)),
            "8": N(
                "KSampler",
                seed=1,
                steps=25,
                cfg=6.0,
                sampler_name="euler",
                scheduler="normal",
                denoise=0.8,
                model=W("1"),
                positive=W("6"),
                negative=W("7"),
                latent_image=W("5"),
            ),
            "9": N("VAEDecode", samples=W("8"), vae=W("1", 2)),
            "10": N(
                "ImageCompositeMasked",
                destination=W("2"),
                source=W("9"),
                x=0,
                y=0,
                resize_source=False,
                mask=W("3"),
            ),
            "11": N("SaveImage", images=W("10"), filename_prefix="inpaint"),
        },
        # SD3 風: ネガティブは ConditioningZeroOut。プロンプトは固定の前置きと利用者の文の連結
        "S12_concat_zero_negative": {
            "1": N("CheckpointLoaderSimple", ckpt_name="sd3.safetensors"),
            "2": N("ModelSamplingSD3", model=W("1"), shift=3.0),
            "3": N("PrimitiveString", "Style preset", value="cinematic photo, 35mm, film grain, "),
            "4": N("PrimitiveStringMultiline", "Prompt", value="an old man reading a newspaper"),
            "5": N("StringConcatenate", string_a=W("3"), string_b=W("4"), delimiter=""),
            "6": N("CLIPTextEncode", text=W("5"), clip=W("1", 1)),
            "7": N("ConditioningZeroOut", conditioning=W("6")),
            "8": N("EmptySD3LatentImage", width=1024, height=1024, batch_size=1),
            "9": N(
                "KSampler",
                seed=77,
                steps=28,
                cfg=4.5,
                sampler_name="dpmpp_2m",
                scheduler="sgm_uniform",
                denoise=1.0,
                model=W("2"),
                positive=W("6"),
                negative=W("7"),
                latent_image=W("8"),
            ),
            "10": N("VAEDecode", samples=W("9"), vae=W("1", 2)),
            "11": N("SaveImage", images=W("10"), filename_prefix="sd3"),
        },
    }
)

SYNTH_GT.update(
    {
        "S7_hiresfix_2pass": {
            **gt(
                "generate",
                R("3", "text"),
                R("4", "text"),
                [{R("6", "seed"), R("10", "seed")}],
                R("5", "width"),
                R("5", "height"),
                R("5", "batch_size"),
                outputs={"12"},
            ),
            # 2つ目のシードを省いても実行はできる。保存は最終だけか、両方。
            "seed": [frozenset({R("6", "seed"), R("10", "seed")}), frozenset({R("6", "seed")})],
            "outputs": [frozenset({"12"}), frozenset({"8", "12"})],
        },
        "S8_controlnet": gt(
            "edit",
            R("2", "text"),
            R("3", "text"),
            [{R("9", "seed")}],
            R("8", "width"),
            R("8", "height"),
            R("8", "batch_size"),
            images=[R("4", "image")],
            outputs={"11"},
        ),
        "S9_preview_only": gt(
            "generate",
            R("2", "text"),
            R("3", "text"),
            [{R("5", "seed")}],
            R("4", "width"),
            R("4", "height"),
            R("4", "batch_size"),
            outputs=set(),
        ),
        "S10_primitive_size": gt(
            "generate",
            R("2", "text"),
            R("3", "text"),
            [{R("5", "seed")}],
            R("20", "value"),
            R("21", "value"),
            R("22", "value"),
            outputs={"7"},
        ),
        "S11_inpaint_inverted": gt(
            "edit",
            R("6", "text"),
            R("7", "text"),
            [{R("8", "seed")}],
            images=[R("2", "image")],
            mask=("image_alpha", None, None),
            outputs={"11"},
        ),
        "S12_concat_zero_negative": gt(
            "generate",
            R("4", "value"),
            None,
            [{R("9", "seed")}],
            R("8", "width"),
            R("8", "height"),
            R("8", "batch_size"),
            outputs={"11"},
        ),
    }
)

SYNTH_EXPOSED.update(
    {
        "S7_hiresfix_2pass": {R("6", "steps"), R("10", "denoise"), R("9", "scale_by")},
        "S8_controlnet": {R("7", "strength"), R("9", "steps")},
        "S9_preview_only": {R("5", "steps")},
        "S10_primitive_size": {R("5", "steps")},
        "S11_inpaint_inverted": {R("8", "denoise")},
        "S12_concat_zero_negative": {R("9", "steps"), R("3", "value")},
    }
)
