"""SD WebUI の Edit(img2img と inpaint。ADR-0038 1〜4章)のテスト。偽の WebUI だけを使う。

- capabilities の Edit の操作と項目
- `finalize_params`: サイズの既定(入力画像の寸法)、記録に base64 を入れない、マスクの項目
- `execute`: 入力画像とマスクを base64 にして `/sdapi/v1/img2img` に送る(マスクは白が描き直す範囲)
- `POST /api/runs` の通し: `run_input` と系列(主たる親は入力画像)、入力の枚数の 422
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.orm import sessionmaker

from app.domain.run_validation import RunValidationError
from app.providers.base import (
    InputImage,
    ProviderError,
    RunDraft,
    RunInputMeta,
    RunRequest,
)
from app.providers.sdwebui.provider import (
    SdWebuiProvider,
    mask_to_webui_png,
    size_from_input,
)
from tests.conftest import wait_for_run_terminal
from tests.sdwebui_fake import (
    DYNAMIC_PROMPTS_ARGS,
    DYNAMIC_PROMPTS_NAME,
    FakeSdWebui,
    install_fake_factories,
)

URL = "http://127.0.0.1:7860"
MASK_PARAMS = {"mask_blur", "inpainting_fill", "inpaint_full_res", "inpaint_full_res_padding"}


# -- 画像を作る -------------------------------------------------------------------------


def _png(size: tuple[int, int], color: tuple[int, int, int] = (200, 30, 30)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def _image_bytes(size: tuple[int, int], fmt: str, mode: str = "RGB") -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, size, (10, 120, 200)[: len(mode)] if mode != "L" else 128).save(
        buffer, format=fmt
    )
    return buffer.getvalue()


def _mask_png(size: tuple[int, int], alpha_rows: list[int] | None = None) -> bytes:
    """GAKEI のマスク(RGBA。alpha = 0 が編集範囲)。`alpha_rows` は上から順の行ごとの alpha
    (足りない行は 255 = 残す)。省けば左半分が透明(編集範囲)。"""
    image = Image.new("RGBA", size, (0, 0, 0, 255))
    width, height = size
    if alpha_rows is None:
        for x in range(width // 2):
            for y in range(height):
                image.putpixel((x, y), (0, 0, 0, 0))
    else:
        for y, alpha in enumerate(alpha_rows):
            for x in range(width):
                image.putpixel((x, y), (0, 0, 0, alpha))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _meta(role: str, data: bytes, size: tuple[int, int], mime: str = "image/png") -> RunInputMeta:
    return RunInputMeta(
        asset_id=uuid.uuid4(),
        role=role,
        position=0,
        width=size[0],
        height=size[1],
        sha256=_sha(data),
        mime=mime,
    )


# -- プロバイダー ------------------------------------------------------------------------


def _provider(fake: FakeSdWebui, session_factory: sessionmaker) -> SdWebuiProvider:
    return SdWebuiProvider(
        URL,
        session_factory,
        30.0,
        credentials_loader=lambda: None,
        transport=fake.transport(),
        progress_interval=0.01,
    )


def _draft(inputs: list[RunInputMeta], model: str = "model-a", **params: Any) -> RunDraft:
    return RunDraft(operation="edit", model=model, prompt="a cat", params=params, inputs=inputs)


def _finalize(provider: SdWebuiProvider, session_factory: sessionmaker, draft: RunDraft) -> dict:
    with session_factory() as db:
        return provider.finalize_params(db, draft)


def _request(
    params: dict[str, Any], inputs: list[InputImage], model: str = "model-a"
) -> RunRequest:
    return RunRequest(
        run_id=uuid.uuid4(),
        operation="edit",
        model=model,
        prompt="a cat",
        params=params,
        inputs=inputs,
    )


async def _noop(_event: Any) -> None:
    return None


def test_capabilities_offer_edit_with_one_image_and_optional_mask(
    db_session_factory: sessionmaker,
) -> None:
    caps = _provider(FakeSdWebui(), db_session_factory).capabilities()
    assert caps.max_input_images == 1
    edit = next(o for o in caps.models[0].operations if o.operation == "edit")
    assert (edit.min_input_images, edit.max_input_images) == (1, 1)
    assert edit.supports_mask is True
    assert edit.requires_mask is False
    params = {p.name: p for p in edit.params}
    names = list(params)
    # ネガティブプロンプトの次に img2img の項目、その後に共通の項目
    assert names[:7] == [
        "negative_prompt",
        "denoising_strength",
        "resize_mode",
        "mask_blur",
        "inpainting_fill",
        "inpaint_full_res",
        "inpaint_full_res_padding",
    ]
    assert {"sampler_name", "scheduler", "steps", "cfg_scale", "seed", "vae", "n"} <= set(names)
    denoise = params["denoising_strength"]
    assert (denoise.minimum, denoise.maximum, denoise.default) == (0, 1, 0.75)
    assert params["resize_mode"].choices == [
        "just_resize",
        "crop_and_resize",
        "resize_and_fill",
        "latent_upscale",
    ]
    assert set(params["resize_mode"].choice_labels or {}) == set(params["resize_mode"].choices)
    assert params["inpainting_fill"].default == "original"
    assert (params["mask_blur"].minimum, params["mask_blur"].maximum) == (0, 64)
    assert params["mask_blur"].default == 4
    assert params["inpaint_full_res"].default is False
    assert params["inpaint_full_res_padding"].maximum == 256
    assert params["inpaint_full_res_padding"].default == 32
    # マスクに関する項目だけが mask_only
    assert {p.name for p in edit.params if p.mask_only} == MASK_PARAMS
    # Generate には img2img の項目を出さない
    generate = next(o for o in caps.models[0].operations if o.operation == "generate")
    assert not {"denoising_strength", "resize_mode"} & {p.name for p in generate.params}


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        ((768, 768), (768, 768)),
        ((1001, 603), (1001, 603)),  # 8 の倍数には丸めない(ADR-0038 2章 2026-10-10 改訂)
        ((803, 601), (803, 601)),
        ((4096, 2048), (2048, 1024)),  # 長辺 2048 に収める
        ((1500, 3000), (1024, 2048)),
        ((3001, 2003), (2048, 1367)),  # 長辺に収めた後も丸めない
        ((3, 5), (3, 5)),
    ],
)
def test_size_from_input(size: tuple[int, int], expected: tuple[int, int]) -> None:
    assert size_from_input(*size) == expected


def test_finalize_edit_sends_clip_skip(db_session_factory: sessionmaker) -> None:
    provider = _provider(FakeSdWebui(), db_session_factory)
    image = _png((803, 601))
    meta = _meta("image", image, (803, 601))
    request = _finalize(provider, db_session_factory, _draft([meta], clip_skip=3))[
        "sdwebui_request"
    ]
    assert request["override_settings"]["CLIP_stop_at_last_layers"] == 3
    # サイズの指定が無ければ入力画像の寸法のまま(8 の倍数に丸めない)
    assert (request["width"], request["height"]) == (803, 601)
    request = _finalize(provider, db_session_factory, _draft([meta]))["sdwebui_request"]
    assert request["override_settings"]["CLIP_stop_at_last_layers"] == 1


def test_finalize_uses_input_size_and_records_sha256(db_session_factory: sessionmaker) -> None:
    provider = _provider(FakeSdWebui(), db_session_factory)
    image = _png((1001, 603))
    params = _finalize(
        provider,
        db_session_factory,
        _draft([_meta("image", image, (1001, 603))], denoising_strength=0.4),
    )
    request = params["sdwebui_request"]
    assert (request["width"], request["height"]) == (1001, 603)
    assert request["init_images"] == [{"asset_sha256": _sha(image)}]
    assert request["denoising_strength"] == 0.4
    assert request["resize_mode"] == 0
    # マスクが無ければマスクの項目は送らない
    assert "mask" not in request
    assert not MASK_PARAMS & set(request)
    assert "inpainting_mask_invert" not in request
    # 記録に base64 は入らない
    assert len(json.dumps(params)) < 2000


def test_finalize_explicit_size_wins(db_session_factory: sessionmaker) -> None:
    provider = _provider(FakeSdWebui(), db_session_factory)
    image = _png((300, 200))
    params = _finalize(
        provider,
        db_session_factory,
        _draft([_meta("image", image, (300, 200))], size="512x768", resize_mode="crop_and_resize"),
    )
    request = params["sdwebui_request"]
    assert (request["width"], request["height"]) == (512, 768)
    assert request["resize_mode"] == 1


def test_finalize_with_mask_records_inpaint_fields(db_session_factory: sessionmaker) -> None:
    provider = _provider(FakeSdWebui(), db_session_factory)
    image = _png((64, 64))
    mask = _mask_png((64, 64))
    params = _finalize(
        provider,
        db_session_factory,
        _draft(
            [_meta("image", image, (64, 64)), _meta("mask", mask, (64, 64))],
            mask_blur=8,
            inpaint_full_res=True,
        ),
    )
    request = params["sdwebui_request"]
    assert request["init_images"] == [{"asset_sha256": _sha(image)}]
    assert request["mask"] == {"asset_sha256": _sha(mask)}
    assert request["mask_blur"] == 8
    assert request["inpainting_fill"] == 1  # 既定は元の画像(original)
    assert request["inpaint_full_res"] is True
    assert request["inpaint_full_res_padding"] == 32
    assert request["inpainting_mask_invert"] == 0


@pytest.mark.parametrize("count", [0, 2])
def test_finalize_rejects_wrong_image_count(db_session_factory: sessionmaker, count: int) -> None:
    provider = _provider(FakeSdWebui(), db_session_factory)
    inputs = [_meta("image", _png((64, 64), (i, 0, 0)), (64, 64)) for i in range(count)]
    with pytest.raises(RunValidationError):
        _finalize(provider, db_session_factory, _draft(inputs))


def test_finalize_dynamic_prompts_uses_img2img_script_info(
    db_session_factory: sessionmaker,
) -> None:
    # img2img 用の引数の並びは txt2img 用と違う(並びが違っても label で組み立てる)
    img2img_args = [("Some img2img option", "keep"), *reversed(DYNAMIC_PROMPTS_ARGS)]
    fake = FakeSdWebui()
    fake.enable_dynamic_prompts(img2img_args=img2img_args)
    provider = _provider(fake, db_session_factory)

    caps = provider.capabilities()
    edit = next(o for o in caps.models[0].operations if o.operation == "edit")
    assert {"dynamic_prompts", "dynamic_prompts_combinatorial"} <= {p.name for p in edit.params}

    image = _png((64, 64))
    params = _finalize(
        provider,
        db_session_factory,
        _draft([_meta("image", image, (64, 64))], dynamic_prompts_combinatorial=True),
    )
    args = params["sdwebui_request"]["alwayson_scripts"][DYNAMIC_PROMPTS_NAME]["args"]
    assert len(args) == len(img2img_args)
    values = {label: value for (label, _), value in zip(img2img_args, args, strict=True)}
    assert values["Some img2img option"] == "keep"
    assert values["Dynamic Prompts enabled"] is True
    assert values["Combinatorial generation"] is True
    assert values["Magic prompt"] is False


def test_edit_hides_dynamic_prompts_without_img2img_script(
    db_session_factory: sessionmaker,
) -> None:
    fake = FakeSdWebui()
    fake.enable_dynamic_prompts()
    # img2img 用のスクリプトが無い(txt2img 用だけ)
    fake.scripts["img2img"] = []
    caps = _provider(fake, db_session_factory).capabilities()
    ops = {o.operation: {p.name for p in o.params} for o in caps.models[0].operations}
    assert "dynamic_prompts" in ops["generate"]
    assert "dynamic_prompts" not in ops["edit"]


# -- マスクの変換 ------------------------------------------------------------------------


def test_mask_conversion_inverts_alpha_and_keeps_soft_edges() -> None:
    mask = _mask_png((4, 4), alpha_rows=[0, 128, 255, 64])
    converted = Image.open(io.BytesIO(mask_to_webui_png(mask, (4, 4))))
    assert converted.format == "PNG"
    assert converted.mode == "L"
    assert converted.size == (4, 4)
    # alpha = 0(編集範囲)→ 白、alpha = 255(残す)→ 黒、半透明は 255 - alpha
    assert [converted.getpixel((0, y)) for y in range(4)] == [255, 127, 0, 191]


def test_mask_conversion_resizes_to_input() -> None:
    mask = _mask_png((32, 16))  # 左半分が編集範囲
    converted = Image.open(io.BytesIO(mask_to_webui_png(mask, (64, 32))))
    assert converted.size == (64, 32)
    assert converted.getpixel((2, 10)) == 255
    assert converted.getpixel((60, 10)) == 0


# -- execute ---------------------------------------------------------------------------


def test_execute_sends_base64_image_and_converted_mask(db_session_factory: sessionmaker) -> None:
    fake = FakeSdWebui()
    provider = _provider(fake, db_session_factory)
    image = _png((64, 48))
    mask = _mask_png((64, 48))
    params = _finalize(
        provider,
        db_session_factory,
        _draft([_meta("image", image, (64, 48)), _meta("mask", mask, (64, 48))], seed=5),
    )
    inputs = [
        InputImage(role="image", position=0, data=image, mime="image/png"),
        InputImage(role="mask", position=0, data=mask, mime="image/png"),
    ]
    result = asyncio.run(provider.execute(_request(params, inputs), _noop))

    assert len(result.outputs) == 1
    assert fake.txt2img_bodies == []
    assert len(fake.img2img_bodies) == 1
    # 入力画像は原本のまま
    assert fake.img2img_init_images == [[image]]
    sent_mask = Image.open(io.BytesIO(fake.img2img_masks[0] or b""))
    assert sent_mask.mode == "L"
    assert sent_mask.size == (64, 48)
    assert sent_mask.getpixel((5, 5)) == 255  # 透明だったところが白
    assert sent_mask.getpixel((60, 5)) == 0
    # base64 以外は記録と同じ本文を送る(記録は変えない)
    body = dict(fake.img2img_bodies[0])
    record = params["sdwebui_request"]
    assert record["init_images"] == [{"asset_sha256": _sha(image)}]
    assert record["mask"] == {"asset_sha256": _sha(mask)}
    body["init_images"] = record["init_images"]
    body["mask"] = record["mask"]
    assert body == record


def test_execute_without_mask(db_session_factory: sessionmaker) -> None:
    fake = FakeSdWebui()
    provider = _provider(fake, db_session_factory)
    image = _png((64, 64))
    params = _finalize(provider, db_session_factory, _draft([_meta("image", image, (64, 64))]))
    inputs = [InputImage(role="image", position=0, data=image, mime="image/png")]
    asyncio.run(provider.execute(_request(params, inputs), _noop))
    assert "mask" not in fake.img2img_bodies[0]
    assert fake.img2img_masks == [None]
    assert fake.img2img_init_images == [[image]]


@pytest.mark.parametrize(
    ("fmt", "mime", "mode", "converted"),
    [
        ("JPEG", "image/jpeg", "RGB", False),
        ("WEBP", "image/webp", "RGB", False),
        ("GIF", "image/gif", "P", True),
        ("BMP", "image/bmp", "RGB", True),
    ],
)
def test_execute_converts_unsupported_formats_to_png(
    db_session_factory: sessionmaker, fmt: str, mime: str, mode: str, converted: bool
) -> None:
    fake = FakeSdWebui()
    provider = _provider(fake, db_session_factory)
    data = _image_bytes((16, 16), fmt, mode)
    params = _finalize(provider, db_session_factory, _draft([_meta("image", data, (16, 16), mime)]))
    inputs = [InputImage(role="image", position=0, data=data, mime=mime)]
    asyncio.run(provider.execute(_request(params, inputs), _noop))
    sent = fake.img2img_init_images[0][0]
    if converted:
        assert Image.open(io.BytesIO(sent)).format == "PNG"
    else:
        assert sent == data


def test_execute_dynamic_prompts_expands_in_img2img(db_session_factory: sessionmaker) -> None:
    fake = FakeSdWebui()
    fake.enable_dynamic_prompts()
    provider = _provider(fake, db_session_factory)
    image = _png((64, 64))
    draft = RunDraft(
        operation="edit",
        model="model-a",
        prompt="a {red|blue} cat",
        params={"dynamic_prompts_combinatorial": True},
        inputs=[_meta("image", image, (64, 64))],
    )
    params = _finalize(provider, db_session_factory, draft)
    request = RunRequest(
        run_id=uuid.uuid4(),
        operation="edit",
        model="model-a",
        prompt="a {red|blue} cat",
        params=params,
        inputs=[InputImage(role="image", position=0, data=image, mime="image/png")],
    )
    result = asyncio.run(provider.execute(request, _noop))
    assert len(result.outputs) == 2
    assert result.usage is not None
    assert result.usage["all_prompts"] == ["a red cat", "a blue cat"]


def test_execute_model_mismatch_fails(db_session_factory: sessionmaker) -> None:
    fake = FakeSdWebui()
    provider = _provider(fake, db_session_factory)
    image = _png((64, 64))
    params = _finalize(
        provider, db_session_factory, _draft([_meta("image", image, (64, 64))], model="model-b")
    )
    # 記録の後に WebUI から model-b が消えた(WebUI は黙って今の model-a で描く)
    fake.checkpoints = [c for c in fake.checkpoints if c["model_name"] != "model-b"]
    inputs = [InputImage(role="image", position=0, data=image, mime="image/png")]
    with pytest.raises(ProviderError) as excinfo:
        asyncio.run(provider.execute(_request(params, inputs, model="model-b"), _noop))
    assert excinfo.value.code == "sdwebuiModelMismatch"


# -- POST /api/runs の通し -----------------------------------------------------------------


LOCAL_URL = "http://127.0.0.1:7860"


def _connect(client: TestClient, monkeypatch: pytest.MonkeyPatch, fake: FakeSdWebui) -> None:
    install_fake_factories(monkeypatch, fake)
    response = client.put("/api/sdwebui/connection", json={"url": LOCAL_URL})
    assert response.status_code == 200, response.text


def _upload(client: TestClient, data: bytes, kind: str = "upload") -> str:
    response = client.post(
        "/api/assets",
        files={"file": ("in.png", data, "image/png")},
        data={"kind": kind},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _post_edit(client: TestClient, inputs: list[dict], **params: Any):
    return client.post(
        "/api/runs",
        json={
            "operation": "edit",
            "provider": "sdwebui",
            "model": "model-a",
            "prompt": "a cat",
            "params": params,
            "inputs": inputs,
        },
    )


def test_inpaint_run_records_inputs_and_lineage(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeSdWebui()
    _connect(client, monkeypatch, fake)
    image = _png((96, 64))
    mask = _mask_png((96, 64))
    image_id = _upload(client, image)
    mask_id = _upload(client, mask, kind="mask")

    response = _post_edit(
        client,
        [
            {"asset_id": image_id, "role": "image", "position": 0},
            {"asset_id": mask_id, "role": "mask", "position": 0},
        ],
        denoising_strength=0.8,
        inpainting_fill="original",
    )
    assert response.status_code == 202, response.text
    detail = wait_for_run_terminal(client, response.json()["id"])
    assert detail["status"] == "succeeded", detail
    assert detail["operation"] == "edit"

    # run_input に入力画像とマスクが残る
    roles = {(i["role"], i["position"]): i["asset_id"] for i in detail["inputs"]}
    assert roles == {("image", 0): image_id, ("mask", 0): mask_id}

    # 記録には base64 を入れず sha256 だけ
    request = detail["params"]["sdwebui_request"]
    assert request["init_images"] == [{"asset_sha256": _sha(image)}]
    assert request["mask"] == {"asset_sha256": _sha(mask)}
    assert (request["width"], request["height"]) == (96, 64)
    assert request["denoising_strength"] == 0.8
    assert "base64" not in json.dumps(detail["params"])

    # 実際に送ったもの: 入力画像の原本と、白が描き直す範囲のマスク
    assert fake.img2img_init_images == [[image]]
    sent_mask = Image.open(io.BytesIO(fake.img2img_masks[0] or b""))
    assert sent_mask.getpixel((5, 5)) == 255
    assert sent_mask.getpixel((90, 5)) == 0

    # 系列: 出力の主たる親は入力画像、マスクは主たる親ではない
    output_id = detail["outputs"][0]["asset_id"]
    lineage = client.get(f"/api/assets/{output_id}/lineage").json()
    run_id = detail["id"]
    edges = {
        (e["source"], e["kind"]): e
        for e in lineage["edges"]
        if e["target"] == run_id and e["kind"] == "input"
    }
    assert edges[(image_id, "input")]["primary"] is True
    assert edges[(mask_id, "input")]["primary"] is False
    assert edges[(mask_id, "input")]["role"] == "mask"


def test_img2img_without_mask_run(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeSdWebui()
    _connect(client, monkeypatch, fake)
    image_id = _upload(client, _png((64, 64)))
    response = _post_edit(client, [{"asset_id": image_id, "role": "image", "position": 0}])
    assert response.status_code == 202, response.text
    detail = wait_for_run_terminal(client, response.json()["id"])
    assert detail["status"] == "succeeded", detail
    assert "mask" not in fake.img2img_bodies[0]
    assert fake.img2img_bodies[0]["denoising_strength"] == 0.75


@pytest.mark.parametrize("count", [0, 2])
def test_wrong_input_count_is_422(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, count: int
) -> None:
    fake = FakeSdWebui()
    _connect(client, monkeypatch, fake)
    inputs = [
        {"asset_id": _upload(client, _png((64, 64), (i, 9, 9))), "role": "image", "position": i}
        for i in range(count)
    ]
    response = _post_edit(client, inputs)
    assert response.status_code == 422, response.text
    assert fake.img2img_bodies == []


@pytest.mark.parametrize("param", sorted(MASK_PARAMS))
def test_mask_params_without_mask_are_422(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, param: str
) -> None:
    fake = FakeSdWebui()
    _connect(client, monkeypatch, fake)
    image_id = _upload(client, _png((64, 64)))
    value: Any = {
        "mask_blur": 8,
        "inpainting_fill": "fill",
        "inpaint_full_res": True,
        "inpaint_full_res_padding": 16,
    }[param]
    response = _post_edit(
        client, [{"asset_id": image_id, "role": "image", "position": 0}], **{param: value}
    )
    assert response.status_code == 422, response.text
    assert param in response.text


@pytest.mark.parametrize(
    "params",
    [
        {"denoising_strength": 1.5},
        {"denoising_strength": -0.1},
        {"resize_mode": "stretch"},
    ],
)
def test_invalid_edit_params_are_422(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, params: dict
) -> None:
    fake = FakeSdWebui()
    _connect(client, monkeypatch, fake)
    image_id = _upload(client, _png((64, 64)))
    response = _post_edit(
        client, [{"asset_id": image_id, "role": "image", "position": 0}], **params
    )
    assert response.status_code == 422, response.text
