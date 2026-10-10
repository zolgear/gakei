"""画像の生成情報を SD WebUI のフォームに読み込む(ADR-0038 9章)。

- 対応付け(`build_form_values`): プロンプト、Template、数値、サンプラーとスケジューラー、サイズ、
  チェックポイントの名前とハッシュでの照合、VAE(A1111 と Forge)、読み込めなかった項目。
- `POST /api/sdwebui/import-params`: ファイルと asset_id、他人の Asset の 404、生成情報が無い・
  A1111 以外の 422、未接続の 409、**画像を保存しないこと**(ファイルも Asset も増えない。
  一時ファイルも作らない)。

偽の WebUI だけを使い、モデル名などはすべて架空のもの。
"""

from __future__ import annotations

import io
import json
import random
import tempfile
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image, PngImagePlugin
from sqlalchemy import func, select

from app.domain.generation_meta import extract_generation_meta
from app.domain.models import Asset
from app.providers.sdwebui.client import Catalog, Checkpoint, SdWebuiClient
from app.providers.sdwebui.import_params import build_form_values
from tests.conftest import login_as
from tests.sdwebui_fake import FakeSdWebui, install_fake_factories

LOCAL_URL = "http://127.0.0.1:7860"
PATH = "/api/sdwebui/import-params"

# 実物の WebUI の parameters の形に合わせた、架空の値
INFOTEXT = (
    "1girl, solo, smile, \\(series name\\),\n"
    "sitting, from above\n"
    "Negative prompt: nsfw, (worst quality, low quality:1.2), text\n"
    "Steps: 30, Sampler: Euler a, Schedule type: Automatic, CFG scale: 7, Seed: 1839556000, "
    "Size: 760x1024, Model hash: 0123456789, Model: model-a, Denoising strength: 0.33, "
    "Clip skip: 2, Hires Module 1: Use same choices, "
    'Hires prompt: "1girl, ...\\nmore", Hires CFG Scale: 5, Hires upscale: 1.5, '
    "Hires steps: 20, Hires upscaler: Latent, Version: f9.9.9-fake"
)


def png_with_parameters(text: str | None, *, size: tuple[int, int] = (16, 16)) -> bytes:
    image = Image.new("RGB", size, (40, 80, 120))
    info = PngImagePlugin.PngInfo()
    if text is not None:
        info.add_text("parameters", text)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", pnginfo=info)
    return buffer.getvalue()


def meta_of(text: str) -> dict[str, Any]:
    meta = extract_generation_meta(png_with_parameters(text))
    assert meta is not None and meta["tool"] == "a1111"
    return meta


def catalog_of(fake: FakeSdWebui) -> Catalog:
    return SdWebuiClient(LOCAL_URL, transport=fake.transport()).fetch_catalog()


def unapplied_names(result: Any) -> list[str]:
    return [name for name, _value in result.unapplied]


def note_codes(result: Any) -> list[str]:
    return [n.code for n in result.notes]


# -- 対応付け ----------------------------------------------------------------------------


def test_maps_full_example() -> None:
    result = build_form_values(meta_of(INFOTEXT), catalog_of(FakeSdWebui()))
    assert result.model == "model-a"
    assert result.prompt == "1girl, solo, smile, \\(series name\\),\nsitting, from above"
    assert result.params == {
        "negative_prompt": "nsfw, (worst quality, low quality:1.2), text",
        "steps": 30,
        "cfg_scale": 7,
        "seed": 1839556000,
        "sampler_name": "Euler a",
        "scheduler": "automatic",
        "size": "760x1024",
        # 高解像度補助(ADR-0038 10章)。Denoising strength は hires の2回目の強さ
        "hires": True,
        "hr_scale": 1.5,
        "hr_second_pass_steps": 20,
        "hr_upscaler": "Latent",
        "hr_cfg": 5,
        "hr_denoising_strength": 0.33,
        "clip_skip": 2,
        # 引用された値は JSON として読んだもの(改行を含む)。本体と違うので入れる
        "hr_prompt": "1girl, ...\nmore",
    }
    # Hires Module 1: Use same choices は GAKEI が常に送る値、Model hash は照合にだけ使う項目、
    # Version は読み込み元(software)なので、どれも並べない
    assert result.unapplied == []
    assert result.notes == []
    assert result.software == "f9.9.9-fake"


HIRES_TEXT = "p\nSteps: 20, Seed: 1, Size: 512x768, Model: model-a"


@pytest.mark.parametrize(
    ("size", "expected"),
    [("803x601", "800x600"), ("516x772", "512x768"), ("1023x1023", "1016x1016")],
)
def test_size_not_multiple_of_8_is_floored(size: str, expected: str) -> None:
    """8 の倍数でないサイズは 8 の倍数に切り捨てて入れ、注意を出す(ADR-0038 2章)。"""
    text = f"p\nSteps: 20, Seed: 1, Size: {size}, Model: model-a"
    result = build_form_values(meta_of(text), catalog_of(FakeSdWebui()))
    assert result.params["size"] == expected
    assert note_codes(result) == ["sizeFloored"]
    assert size in result.notes[0].message and expected in result.notes[0].message
    assert result.unapplied == []


def test_size_multiple_of_8_has_no_note() -> None:
    text = "p\nSteps: 20, Seed: 1, Size: 800x600, Model: model-a"
    result = build_form_values(meta_of(text), catalog_of(FakeSdWebui()))
    assert result.params["size"] == "800x600"
    assert result.notes == []


@pytest.mark.parametrize("size", ["2056x1024", "260x255", "abc"])
def test_size_out_of_range_after_floor_is_not_applied(size: str) -> None:
    """切り捨てた後もサイズの制約(長辺・下限・縦横比)に収まらなければ入れない。"""
    text = f"p\nSteps: 20, Seed: 1, Size: {size}, Model: model-a"
    result = build_form_values(meta_of(text), catalog_of(FakeSdWebui()))
    assert "size" not in result.params
    assert note_codes(result) == ["sizeOutOfRange"]
    assert ("Size", size) in result.unapplied


def test_clip_skip() -> None:
    text = "p\nSteps: 20, Seed: 1, Model: model-a, Clip skip: 12"
    result = build_form_values(meta_of(text), catalog_of(FakeSdWebui()))
    assert result.params["clip_skip"] == 12
    assert result.unapplied == []
    for value in ("0", "13", "abc"):
        text = f"p\nSteps: 20, Seed: 1, Model: model-a, Clip skip: {value}"
        result = build_form_values(meta_of(text), catalog_of(FakeSdWebui()))
        assert "clip_skip" not in result.params
        assert note_codes(result) == ["invalidValue"]
        assert ("Clip skip", value) in result.unapplied


def test_hires_prompts_only_when_different_from_main() -> None:
    text = (
        "1girl, smile\nNegative prompt: blurry\n"
        "Steps: 20, Seed: 1, Size: 512x768, Model: model-a, Hires upscale: 2, "
        'Hires prompt: " 1girl, smile ", Hires negative prompt: "blurry,\\nlowres"'
    )
    result = build_form_values(meta_of(text), catalog_of(FakeSdWebui()))
    # 本体と同じ(前後の空白を無視)なら入れない(空 = 本体と同じ)が、読み込めた項目として扱う
    assert "hr_prompt" not in result.params
    # 本体と違えばそのまま入れる(引用の中の改行も解く)
    assert result.params["hr_negative_prompt"] == "blurry,\nlowres"
    assert result.unapplied == []


def test_hires_prompt_same_as_template_is_not_applied() -> None:
    """Dynamic Prompts の展開後のプロンプトと同じなら、本体と同じとみなす。"""
    fake = FakeSdWebui()
    fake.enable_dynamic_prompts()
    text = (
        "red hat, 1girl\n"
        'Steps: 20, Seed: 5, Model: model-a, Template: "{red|blue} hat, 1girl", '
        'Hires upscale: 2, Hires prompt: "red hat, 1girl"'
    )
    result = build_form_values(meta_of(text), catalog_of(fake))
    assert result.prompt == "{red|blue} hat, 1girl"
    assert "hr_prompt" not in result.params
    assert result.unapplied == []


def test_hires_prompt_without_hires_is_unapplied() -> None:
    text = 'p\nSteps: 20, Seed: 1, Model: model-a, Hires prompt: "q"'
    result = build_form_values(meta_of(text), catalog_of(FakeSdWebui()))
    assert "hr_prompt" not in result.params
    assert result.unapplied == [("Hires prompt", "q")]


def test_version_and_hashes_are_never_unapplied() -> None:
    text = (
        "p\nSteps: 20, Seed: 1, Model hash: 9999999999, Model: model-zzz, "
        "VAE hash: 0011223344, VAE: vae-z.safetensors, Version: v9.9.9-fake"
    )
    result = build_form_values(meta_of(text), catalog_of(FakeSdWebui(flavor="a1111")))
    assert result.software == "v9.9.9-fake"
    names = unapplied_names(result)
    assert not {"Model hash", "VAE hash", "Version"} & set(names)
    # 見つからなかったものは注意で知らせる
    assert note_codes(result) == ["modelNotFound", "vaeNotFound"]


def test_hires_mapping_a1111_ignores_hires_cfg() -> None:
    text = (
        HIRES_TEXT + ", Denoising strength: 0.5, Hires upscale: 2, Hires steps: 6, "
        "Hires upscaler: lanczos, Hires CFG Scale: 4.5"
    )
    result = build_form_values(meta_of(text), catalog_of(FakeSdWebui(flavor="a1111")))
    assert result.params["hires"] is True
    assert result.params["hr_scale"] == 2
    assert result.params["hr_second_pass_steps"] == 6
    # 大文字小文字を無視して一覧の名前にする
    assert result.params["hr_upscaler"] == "Lanczos"
    assert result.params["hr_denoising_strength"] == 0.5
    # A1111 には Hires CFG Scale の送り先が無い
    assert "hr_cfg" not in result.params
    assert unapplied_names(result) == ["Hires CFG Scale"]
    assert result.notes == []


def test_hires_unknown_upscaler_keeps_hires_with_note() -> None:
    text = HIRES_TEXT + ", Hires upscale: 1.5, Hires upscaler: upscaler-x"
    result = build_form_values(meta_of(text), catalog_of(FakeSdWebui()))
    assert result.params["hires"] is True
    assert "hr_upscaler" not in result.params
    assert note_codes(result) == ["hiresUpscalerNotFound"]
    assert "upscaler-x" in result.notes[0].message
    assert unapplied_names(result) == ["Hires upscaler"]


def test_hires_resize_and_second_pass_overrides_are_unapplied() -> None:
    # 拡大後の寸法の指定(Hires resize)では倍率が無いので、hires を有効にしない
    text = (
        HIRES_TEXT + ", Denoising strength: 0.4, Hires resize: 1024x1536, Hires steps: 10, "
        "Hires upscaler: Latent, Hires checkpoint: model-b, Hires sampler: Euler, "
        "Hires negative prompt: bad, Hires Module 1: vae-b"
    )
    result = build_form_values(meta_of(text), catalog_of(FakeSdWebui()))
    assert "hires" not in result.params
    assert not any(name.startswith("hr_") for name in result.params)
    assert unapplied_names(result) == [
        "Denoising strength",
        "Hires resize",
        "Hires steps",
        "Hires upscaler",
        "Hires checkpoint",
        "Hires sampler",
        "Hires negative prompt",
        "Hires Module 1",
    ]


def test_hires_with_overrides_keeps_them_unapplied() -> None:
    text = (
        HIRES_TEXT + ", Hires upscale: 2, Hires checkpoint: model-b, Hires sampler: Euler, "
        "Hires Module 1: vae-b"
    )
    result = build_form_values(meta_of(text), catalog_of(FakeSdWebui()))
    assert result.params["hires"] is True
    assert unapplied_names(result) == ["Hires checkpoint", "Hires sampler", "Hires Module 1"]


@pytest.mark.parametrize(
    "extra",
    [
        "Hires upscale: 4.5",  # 倍率の範囲外
        "Hires upscale: 0.5",
        "Hires upscale: x",
    ],
)
def test_hires_invalid_scale(extra: str) -> None:
    result = build_form_values(meta_of(f"{HIRES_TEXT}, {extra}"), catalog_of(FakeSdWebui()))
    assert "hires" not in result.params
    assert note_codes(result) == ["invalidValue"]
    assert "Hires upscale" in unapplied_names(result)


def test_hires_too_large_after_upscale() -> None:
    # 倍率は範囲内でも、拡大後の長辺(2048 × 2.5 = 5120)が 4096 を超える
    text = "p\nSteps: 20, Seed: 1, Size: 2048x1024, Hires upscale: 2.5"
    result = build_form_values(meta_of(text), catalog_of(FakeSdWebui()))
    assert "hires" not in result.params
    assert note_codes(result) == ["invalidValue"]


def test_hires_not_available_on_server() -> None:
    fake = FakeSdWebui()
    fake.upscalers = None
    fake.latent_upscale_modes = None
    text = HIRES_TEXT + ", Denoising strength: 0.5, Hires upscale: 2"
    result = build_form_values(meta_of(text), catalog_of(fake))
    assert "hires" not in result.params
    assert unapplied_names(result) == ["Denoising strength", "Hires upscale"]


def test_model_by_hash_when_name_differs() -> None:
    text = INFOTEXT.replace("Model: model-a", "Model: renamed-model")
    result = build_form_values(meta_of(text), catalog_of(FakeSdWebui()))
    assert result.model == "model-a"
    assert note_codes(result) == ["modelMatchedByHash"]
    assert "Model" not in unapplied_names(result)
    # 照合にだけ使う項目は並べない
    assert "Model hash" not in unapplied_names(result)


def test_model_hash_from_sha256_and_short_hash() -> None:
    catalog = Catalog(
        flavor="a1111",
        checkpoints=[
            Checkpoint(title="x", model_name="model-x", hash=None),
            Checkpoint(title="y", model_name="model-y", hash="abcdef0123"),
        ],
    )
    text = "p\nSteps: 20, Model hash: abcdef01, Model: unknown-model, Seed: 1"
    result = build_form_values(meta_of(text), catalog)
    # 古い 8 桁のハッシュでも、10 桁の先頭と一致すれば照合する
    assert result.model == "model-y"


def test_model_hash_fallback_to_sha256_in_client() -> None:
    fake = FakeSdWebui()
    fake.checkpoints[1]["sha256"] = "fedcba9876" + "0" * 54
    catalog = catalog_of(fake)
    assert {c.model_name: c.hash for c in catalog.checkpoints} == {
        "model-a": "0123456789",
        "model-b": "fedcba9876",
    }


def test_model_not_found() -> None:
    text = INFOTEXT.replace("Model hash: 0123456789", "Model hash: 9999999999").replace(
        "Model: model-a", "Model: model-zzz"
    )
    result = build_form_values(meta_of(text), catalog_of(FakeSdWebui()))
    assert result.model is None
    assert note_codes(result) == ["modelNotFound"]
    assert "model-zzz" in result.notes[0].message
    assert "Model" in unapplied_names(result)
    # 見つからなくても Model hash は並べない(注意で知らせる)
    assert "Model hash" not in unapplied_names(result)


def test_model_name_case_and_extension() -> None:
    text = "p\nSteps: 20, Seed: 1, Model: Model-A.safetensors"
    assert build_form_values(meta_of(text), catalog_of(FakeSdWebui())).model == "model-a"


def test_template_used_when_dynamic_prompts_available() -> None:
    fake = FakeSdWebui()
    fake.enable_dynamic_prompts()
    text = (
        "red hat, 1girl\nNegative prompt: blurry\n"
        'Steps: 20, Seed: 5, Model: model-a, Template: "{red|blue} hat, 1girl", '
        "Negative Template: blurry"
    )
    result = build_form_values(meta_of(text), catalog_of(fake))
    assert result.prompt == "{red|blue} hat, 1girl"
    assert result.params["negative_prompt"] == "blurry"
    assert result.params["dynamic_prompts"] is True
    assert "Template" not in unapplied_names(result)
    assert "Negative Template" not in unapplied_names(result)


def test_template_without_dynamic_prompts_uses_expanded_prompt() -> None:
    text = 'red hat, 1girl\nSteps: 20, Seed: 5, Model: model-a, Template: "{red|blue} hat, 1girl"'
    result = build_form_values(meta_of(text), catalog_of(FakeSdWebui()))
    assert result.prompt == "red hat, 1girl"
    assert "dynamic_prompts" not in result.params
    assert note_codes(result) == ["dynamicPromptsUnavailable"]
    assert "Template" in unapplied_names(result)


def test_size_out_of_range() -> None:
    for size in ("4096x4096", "100x100", "2048x256", "abc"):
        text = f"p\nSteps: 20, Seed: 1, Size: {size}, Model: model-a"
        result = build_form_values(meta_of(text), catalog_of(FakeSdWebui()))
        assert "size" not in result.params, size
        assert note_codes(result) == ["sizeOutOfRange"], size
        assert ("Size", size) in result.unapplied


def test_schedule_type_label_and_unknown() -> None:
    fake = FakeSdWebui()
    text = "p\nSteps: 20, Seed: 1, Schedule type: KARRAS, Model: model-a"
    assert build_form_values(meta_of(text), catalog_of(fake)).params["scheduler"] == "karras"

    text = "p\nSteps: 20, Seed: 1, Schedule type: Unknown Thing, Model: model-a"
    result = build_form_values(meta_of(text), catalog_of(fake))
    assert "scheduler" not in result.params
    assert note_codes(result) == ["schedulerNotFound"]
    assert ("Schedule type", "Unknown Thing") in result.unapplied


def test_legacy_sampler_with_scheduler_suffix() -> None:
    text = "p\nSteps: 20, Sampler: DPM++ 2M Karras, Seed: 1, Model: model-a"
    result = build_form_values(meta_of(text), catalog_of(FakeSdWebui()))
    assert result.params["sampler_name"] == "DPM++ 2M"
    assert result.params["scheduler"] == "karras"


def test_unknown_sampler() -> None:
    text = "p\nSteps: 20, Sampler: Mystery, Seed: 1, Model: model-a"
    result = build_form_values(meta_of(text), catalog_of(FakeSdWebui()))
    assert "sampler_name" not in result.params
    assert note_codes(result) == ["samplerNotFound"]


@pytest.mark.parametrize(
    ("line", "key"),
    [
        ("Steps: 0", "Steps"),
        ("Steps: 999", "Steps"),
        ("Steps: many", "Steps"),
        ("CFG scale: 0.5", "CFG scale"),
        ("CFG scale: nan", "CFG scale"),
        ("CFG scale: x", "CFG scale"),
        ("Seed: -1", "Seed"),
        ("Seed: 99999999999", "Seed"),
    ],
)
def test_invalid_values_are_unapplied(line: str, key: str) -> None:
    text = f"p\n{line}, Sampler: Euler, Model: model-a, Size: 512x512"
    result = build_form_values(meta_of(text), catalog_of(FakeSdWebui()))
    assert "invalidValue" in note_codes(result)
    assert key in unapplied_names(result)


def test_cfg_scale_float() -> None:
    text = "p\nSteps: 20, CFG scale: 6.5, Seed: 1, Model: model-a"
    assert build_form_values(meta_of(text), catalog_of(FakeSdWebui())).params["cfg_scale"] == 6.5


def test_vae_a1111_with_extension() -> None:
    fake = FakeSdWebui(flavor="a1111")
    text = "p\nSteps: 20, Seed: 1, Model: model-a, VAE hash: 0011223344, VAE: vae-b.safetensors"
    result = build_form_values(meta_of(text), catalog_of(fake))
    assert result.params["vae"] == "vae-b.safetensors"
    # VAE hash は照合にだけ使う項目なので並べない
    assert unapplied_names(result) == []


def test_vae_forge_module_without_extension() -> None:
    fake = FakeSdWebui(flavor="forge")
    text = "p\nSteps: 20, Seed: 1, Model: model-a, Module 1: te-a, Module 2: vae-a"
    result = build_form_values(meta_of(text), catalog_of(fake))
    assert result.params["vae"] == "vae-a.safetensors"
    # テキストエンコーダーはフォームに入らない
    assert unapplied_names(result) == ["Module 1"]
    assert result.notes == []


def test_vae_not_found() -> None:
    text = "p\nSteps: 20, Seed: 1, Model: model-a, Module 1: other-vae"
    result = build_form_values(meta_of(text), catalog_of(FakeSdWebui()))
    assert "vae" not in result.params
    assert note_codes(result) == ["vaeNotFound"]
    assert unapplied_names(result) == ["Module 1"]

    text = "p\nSteps: 20, Seed: 1, Model: model-a, VAE: other.safetensors"
    result = build_form_values(meta_of(text), catalog_of(FakeSdWebui(flavor="a1111")))
    assert note_codes(result) == ["vaeNotFound"]


def test_no_negative_prompt_clears_it() -> None:
    result = build_form_values(
        meta_of("p\nSteps: 20, Seed: 1, Model: model-a"), catalog_of(FakeSdWebui())
    )
    assert result.prompt == "p"
    assert result.params["negative_prompt"] == ""


# -- API ---------------------------------------------------------------------------------


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeSdWebui:
    fake = FakeSdWebui()
    install_fake_factories(monkeypatch, fake)
    return fake


@pytest.fixture
def connected(client: TestClient, fake: FakeSdWebui) -> TestClient:
    response = client.put("/api/sdwebui/connection", json={"url": LOCAL_URL})
    assert response.status_code == 200, response.text
    return client


def _post_file(client: TestClient, data: bytes):  # noqa: ANN202
    return client.post(PATH, files={"file": ("x.png", data, "image/png")})


def _asset_count(client: TestClient) -> int:
    with client.app.state.session_factory() as db:
        return db.execute(select(func.count()).select_from(Asset)).scalar_one()


def _files(data_dir: Path) -> set[tuple[Path, int]]:
    return {
        (p, p.stat().st_size)
        for p in data_dir.rglob("*")
        if p.is_file() and not p.name.endswith((".db", ".db-wal", ".db-shm"))
    }


def _upload(client: TestClient, data: bytes) -> str:
    response = client.post(
        "/api/assets", files={"file": ("x.png", data, "image/png")}, data={"kind": "upload"}
    )
    assert response.status_code in (200, 201), response.text
    return response.json()["id"]


def test_not_connected_is_409(client: TestClient) -> None:
    assert _post_file(client, png_with_parameters(INFOTEXT)).status_code == 409


def test_unreachable_is_409(connected: TestClient, fake: FakeSdWebui) -> None:
    fake.api_enabled = False
    registry = connected.app.state.registry
    registry.get("sdwebui").invalidate_cache()
    assert _post_file(connected, png_with_parameters(INFOTEXT)).status_code == 409


def test_file_import_does_not_store_anything(
    connected: TestClient,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # 1MB を超える画像(Starlette の既定なら一時ファイルに移る大きさ)でも、一時ファイルを作らない。
    def _no_temp_files(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("一時ファイルを作った")

    caplog.set_level("DEBUG")
    big_text = INFOTEXT.replace("sitting", "sitting " + "x" * 10)
    noise = Image.frombytes("RGB", (800, 800), random.Random(0).randbytes(800 * 800 * 3))
    info = PngImagePlugin.PngInfo()
    info.add_text("parameters", big_text)
    buffer = io.BytesIO()
    noise.save(buffer, format="PNG", pnginfo=info)
    data = buffer.getvalue()
    assert len(data) > 1024 * 1024

    before_assets = _asset_count(connected)
    before_files = _files(data_dir)
    monkeypatch.setattr(tempfile, "TemporaryFile", _no_temp_files)
    monkeypatch.setattr(tempfile, "NamedTemporaryFile", _no_temp_files)
    monkeypatch.setattr(tempfile, "mkstemp", _no_temp_files)

    response = _post_file(connected, data)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["model"] == "model-a"
    assert body["params"]["size"] == "760x1024"
    assert body["source"] == {"software": "f9.9.9-fake"}
    assert body["params"]["clip_skip"] == 2
    assert body["unapplied"] == []

    assert _asset_count(connected) == before_assets
    assert _files(data_dir) == before_files
    # ログにプロンプトや画像の中身を残さない
    assert "series name" not in caplog.text


def test_asset_id_import(connected: TestClient) -> None:
    asset_id = _upload(connected, png_with_parameters(INFOTEXT))
    before = _asset_count(connected)
    response = connected.post(PATH, json={"asset_id": asset_id})
    assert response.status_code == 200, response.text
    assert response.json()["prompt"].startswith("1girl, solo")
    # multipart のフィールドでも受ける
    response = connected.post(PATH, data={"asset_id": asset_id}, files={"x": ("", b"", "")})
    assert response.status_code == 200, response.text
    assert _asset_count(connected) == before


def test_asset_id_without_embedded_meta_reads_original(connected: TestClient) -> None:
    """取り込み時の `embedded_meta` が無い Asset(GAKEI が作った画像など)は原本から読む。"""
    asset_id = _upload(connected, png_with_parameters(INFOTEXT))
    with connected.app.state.session_factory() as db:
        asset = db.get(Asset, uuid.UUID(asset_id))
        asset.embedded_meta = None
        db.commit()
    response = connected.post(PATH, json={"asset_id": asset_id})
    assert response.status_code == 200, response.text
    assert response.json()["model"] == "model-a"


def test_unknown_asset_is_404(connected: TestClient) -> None:
    response = connected.post(PATH, json={"asset_id": "00000000-0000-0000-0000-000000000000"})
    assert response.status_code == 404


@pytest.mark.parametrize("payload", [{}, {"asset_id": "not-a-uuid"}, {"asset_id": 3}, [], "x"])
def test_bad_json_is_422(connected: TestClient, payload: object) -> None:
    response = connected.post(
        PATH, content=json.dumps(payload), headers={"content-type": "application/json"}
    )
    assert response.status_code == 422


def test_no_metadata_is_422(connected: TestClient) -> None:
    response = _post_file(connected, png_with_parameters(None))
    assert response.status_code == 422
    assert "生成情報" in response.json()["detail"]
    assert _post_file(connected, b"garbage").status_code == 422
    assert _post_file(connected, b"").status_code == 422


def test_non_a1111_is_422(connected: TestClient) -> None:
    image = Image.new("RGB", (16, 16))
    info = PngImagePlugin.PngInfo()
    workflow = {"1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "m"}}}
    info.add_text("prompt", json.dumps(workflow))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", pnginfo=info)
    response = _post_file(connected, buffer.getvalue())
    assert response.status_code == 422
    assert "A1111" in response.json()["detail"]


def test_too_large_is_413(connected: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.api.sdwebui._BODY_LIMIT", 1000)
    response = _post_file(connected, png_with_parameters(INFOTEXT) + b"\0" * 2000)
    assert response.status_code == 413


def test_other_users_asset_is_404(client_oidc: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeSdWebui()
    install_fake_factories(monkeypatch, fake)
    login_as(client_oidc, "admin@example.com")
    assert client_oidc.put("/api/sdwebui/connection", json={"url": LOCAL_URL}).status_code == 200
    client_oidc.cookies.clear()

    login_as(client_oidc, "alice@example.com")
    alice_asset = _upload(client_oidc, png_with_parameters(INFOTEXT))
    assert client_oidc.post(PATH, json={"asset_id": alice_asset}).status_code == 200
    client_oidc.cookies.clear()

    login_as(client_oidc, "bob@example.com")
    assert client_oidc.post(PATH, json={"asset_id": alice_asset}).status_code == 404
    client_oidc.cookies.clear()
    assert client_oidc.post(PATH, json={"asset_id": alice_asset}).status_code == 401
