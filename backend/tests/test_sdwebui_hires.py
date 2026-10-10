"""SD WebUI の高解像度補助(hires fix。ADR-0038 10章)のテスト。偽の WebUI だけを使う。

- capabilities: Generate だけに出す。Forge だけ `hr_cfg`。アップスケーラーの一覧
  (latent → upscalers、`None` は除く)。一覧が取れなければ項目ごと出さない。
- finalize_params: 本文の `enable_hr` と各値、Forge の `hr_additional_modules` と `hr_cfg` の既定、
  拡大後の長辺 4096 超の 422、無効のときは何も送らない(他の項目の指定は 422)、Edit では 422。
- 実行: 偽の WebUI は拡大後の寸法の画像を返す。Forge 風の偽物は `hr_additional_modules` が
  無いと 500。
"""

from __future__ import annotations

import asyncio
import io
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.domain.models import Asset
from app.domain.run_validation import RunValidationError
from app.providers.base import ProviderError, RunDraft, RunInputMeta, RunRequest
from app.providers.sdwebui.provider import SdWebuiProvider, hires_target_size
from tests.conftest import wait_for_run_terminal
from tests.sdwebui_fake import FakeSdWebui, install_fake_factories

URL = "http://127.0.0.1:7860"
HIRES_NAMES = {
    "hires",
    "hr_scale",
    "hr_upscaler",
    "hr_second_pass_steps",
    "hr_denoising_strength",
    "hr_cfg",
    "hr_prompt",
    "hr_negative_prompt",
}


def _provider(fake: FakeSdWebui, session_factory: sessionmaker) -> SdWebuiProvider:
    return SdWebuiProvider(
        URL,
        session_factory,
        30.0,
        credentials_loader=lambda: None,
        transport=fake.transport(),
        progress_interval=0.01,
    )


def _draft(operation: str = "generate", **params: Any) -> RunDraft:
    inputs: list[RunInputMeta] = []
    if operation == "edit":
        inputs = [
            RunInputMeta(
                asset_id=uuid.uuid4(),
                role="image",
                position=0,
                sha256="a" * 64,
                width=512,
                height=512,
                mime="image/png",
            )
        ]
    return RunDraft(
        operation=operation, model="model-a", prompt="a cat", params=params, inputs=inputs
    )


def _finalize(provider: SdWebuiProvider, session_factory: sessionmaker, **params: Any):
    with session_factory() as db:
        return provider.finalize_params(db, _draft(**params))


def _params_of(provider: SdWebuiProvider, operation: str) -> dict[str, Any]:
    model = provider.capabilities().models[0]
    op = next(o for o in model.operations if o.operation == operation)
    return {p.name: p for p in op.params}


async def _noop(_event: Any) -> None:
    return None


# -- capabilities ------------------------------------------------------------------


def test_capabilities_forge(db_session_factory: sessionmaker) -> None:
    provider = _provider(FakeSdWebui(flavor="forge"), db_session_factory)
    params = _params_of(provider, "generate")
    names = list(params)
    assert HIRES_NAMES <= set(names)
    # 本体の項目(枚数・バッチ回数まで)のあとに並ぶ
    assert names.index("hires") == names.index("n_iter") + 1
    assert params["hires"].type == "bool"
    assert params["hires"].default is False
    assert params["hires"].form_default is False
    # latent の方式 → upscalers の順。`None` は除く。
    assert params["hr_upscaler"].choices == [
        "Latent",
        "Latent (antialiased)",
        "Lanczos",
        "ESRGAN-a",
    ]
    assert params["hr_upscaler"].default == "Latent"
    scale = params["hr_scale"]
    assert (scale.minimum, scale.maximum, scale.step, scale.default) == (1.0, 4.0, 0.05, 2.0)
    steps = params["hr_second_pass_steps"]
    assert (steps.minimum, steps.maximum, steps.default) == (0, 150, 0)
    strength = params["hr_denoising_strength"]
    assert (strength.minimum, strength.maximum, strength.default) == (0, 1, 0.5)
    cfg = params["hr_cfg"]
    assert (cfg.minimum, cfg.maximum, cfg.default) == (1.0, 30.0, None)
    # hr_ の項目は初期値を持たない(hires が無効のあいだはフォームが未指定に戻すため)
    assert all(params[n].form_default is None for n in HIRES_NAMES - {"hires"})
    assert params["hires"].label == "高解像度補助"
    # 2回目のプロンプトは text(タグモードの切り替えの対象になる名前)。空なら本体と同じ
    for name in ("hr_prompt", "hr_negative_prompt"):
        assert params[name].type == "text"
        assert params[name].default == ""
        assert "空なら本体" in (params[name].description or "")
    assert params["hr_scale"].label == "アップスケール倍率"


def test_capabilities_a1111_has_no_hr_cfg(db_session_factory: sessionmaker) -> None:
    params = _params_of(_provider(FakeSdWebui(flavor="a1111"), db_session_factory), "generate")
    assert HIRES_NAMES - {"hr_cfg"} <= set(params)
    assert "hr_cfg" not in params


def test_capabilities_edit_has_no_hires(db_session_factory: sessionmaker) -> None:
    params = _params_of(_provider(FakeSdWebui(), db_session_factory), "edit")
    assert not HIRES_NAMES & set(params)


def test_capabilities_without_upscaler_list(db_session_factory: sessionmaker) -> None:
    fake = FakeSdWebui()
    fake.upscalers = None
    fake.latent_upscale_modes = None
    params = _params_of(_provider(fake, db_session_factory), "generate")
    assert not HIRES_NAMES & set(params)


def test_capabilities_default_upscaler_without_latent(db_session_factory: sessionmaker) -> None:
    fake = FakeSdWebui()
    fake.latent_upscale_modes = None
    params = _params_of(_provider(fake, db_session_factory), "generate")
    assert params["hr_upscaler"].choices == ["Lanczos", "ESRGAN-a"]
    assert params["hr_upscaler"].default == "Lanczos"


def test_upscaler_list_is_reread_after_invalidate(db_session_factory: sessionmaker) -> None:
    fake = FakeSdWebui()
    provider = _provider(fake, db_session_factory)
    assert "ESRGAN-b" not in _params_of(provider, "generate")["hr_upscaler"].choices
    fake.upscalers = ["None", "ESRGAN-b"]
    provider.invalidate_cache()
    assert _params_of(provider, "generate")["hr_upscaler"].choices == [
        "Latent",
        "Latent (antialiased)",
        "ESRGAN-b",
    ]


# -- finalize_params ----------------------------------------------------------------


def test_finalize_forge_defaults(db_session_factory: sessionmaker) -> None:
    provider = _provider(FakeSdWebui(flavor="forge"), db_session_factory)
    params = _finalize(provider, db_session_factory, hires=True, size="512x768", cfg_scale=6.5)
    request = params["sdwebui_request"]
    assert request["enable_hr"] is True
    assert request["hr_scale"] == 2.0
    assert request["hr_upscaler"] == "Latent"
    assert request["hr_second_pass_steps"] == 0
    assert request["denoising_strength"] == 0.5
    # Forge: 送らないと 500 になる項目と、既定 1.0 の hr_cfg は本体の CFG に合わせる
    assert request["hr_additional_modules"] == ["Use same choices"]
    assert request["hr_cfg"] == 6.5
    # 1回目の寸法はそのまま
    assert (request["width"], request["height"]) == (512, 768)
    for key in ("hr_resize_x", "hr_resize_y", "hr_checkpoint_name", "hr_prompt"):
        assert key not in request


def test_finalize_forge_explicit_values(db_session_factory: sessionmaker) -> None:
    provider = _provider(FakeSdWebui(flavor="forge"), db_session_factory)
    params = _finalize(
        provider,
        db_session_factory,
        hires=True,
        hr_scale=1.5,
        hr_upscaler="ESRGAN-a",
        hr_second_pass_steps=10,
        hr_denoising_strength=0.35,
        hr_cfg=4.0,
    )
    request = params["sdwebui_request"]
    assert {k: request[k] for k in request if k.startswith("hr_") or k == "enable_hr"} == {
        "enable_hr": True,
        "hr_scale": 1.5,
        "hr_upscaler": "ESRGAN-a",
        "hr_second_pass_steps": 10,
        "hr_additional_modules": ["Use same choices"],
        "hr_cfg": 4.0,
    }
    assert request["denoising_strength"] == 0.35
    # 利用者の指定はそのまま params に残る
    assert params["hires"] is True
    assert params["hr_scale"] == 1.5


def test_finalize_a1111_has_no_forge_fields(db_session_factory: sessionmaker) -> None:
    provider = _provider(FakeSdWebui(flavor="a1111"), db_session_factory)
    request = _finalize(provider, db_session_factory, hires=True)["sdwebui_request"]
    assert request["enable_hr"] is True
    assert "hr_additional_modules" not in request
    assert "hr_cfg" not in request
    with db_session_factory() as db, pytest.raises(RunValidationError):
        provider.finalize_params(db, _draft(hires=True, hr_cfg=5.0))


def test_finalize_hires_off_sends_nothing(db_session_factory: sessionmaker) -> None:
    provider = _provider(FakeSdWebui(), db_session_factory)
    for params in ({}, {"hires": False}):
        request = _finalize(provider, db_session_factory, **params)["sdwebui_request"]
        assert not any(k.startswith("hr_") or k == "enable_hr" for k in request)
        assert "denoising_strength" not in request


def test_finalize_hires_prompts(db_session_factory: sessionmaker) -> None:
    provider = _provider(FakeSdWebui(flavor="forge"), db_session_factory)
    request = _finalize(
        provider,
        db_session_factory,
        hires=True,
        hr_prompt="a cat, detailed",
        hr_negative_prompt="blurry",
    )["sdwebui_request"]
    assert request["hr_prompt"] == "a cat, detailed"
    assert request["hr_negative_prompt"] == "blurry"
    assert request["prompt"] == "a cat"

    # 空なら送らない(本体と同じ)
    request = _finalize(
        provider, db_session_factory, hires=True, hr_prompt="", hr_negative_prompt=""
    )["sdwebui_request"]
    assert "hr_prompt" not in request
    assert "hr_negative_prompt" not in request


def test_finalize_empty_hires_prompt_without_hires_is_allowed(
    db_session_factory: sessionmaker,
) -> None:
    """空の2回目のプロンプトは未指定と同じなので、hires が無効でも断らない。"""
    provider = _provider(FakeSdWebui(), db_session_factory)
    request = _finalize(provider, db_session_factory, hires=False, hr_prompt="")["sdwebui_request"]
    assert not any(k.startswith("hr_") or k == "enable_hr" for k in request)


def test_finalize_edit_rejects_hires_prompt(db_session_factory: sessionmaker) -> None:
    provider = _provider(FakeSdWebui(), db_session_factory)
    with db_session_factory() as db, pytest.raises(RunValidationError):
        provider.finalize_params(db, _draft("edit", hr_prompt="x"))


@pytest.mark.parametrize(
    "key", ["hr_scale", "hr_upscaler", "hr_cfg", "hr_prompt", "hr_negative_prompt"]
)
def test_finalize_hires_params_without_hires_is_422(
    db_session_factory: sessionmaker, key: str
) -> None:
    provider = _provider(FakeSdWebui(), db_session_factory)
    value = {
        "hr_scale": 1.5,
        "hr_upscaler": "Latent",
        "hr_cfg": 5.0,
        "hr_prompt": "x",
        "hr_negative_prompt": "y",
    }[key]
    with db_session_factory() as db, pytest.raises(RunValidationError) as excinfo:
        provider.finalize_params(db, _draft(hires=False, **{key: value}))
    assert key in str(excinfo.value)


@pytest.mark.parametrize(
    ("size", "scale", "expected"),
    [
        ((512, 768), 1.5, (768, 1152)),
        ((512, 768), 2.0, (1024, 1536)),
        ((1000, 600), 1.3, (1296, 776)),  # int(1300) と int(780) を 8 の倍数に切り捨て
        ((2048, 1024), 2.003, (4096, 2048)),
    ],
)
def test_hires_target_size_floors_to_multiple_of_8(
    size: tuple[int, int], scale: float, expected: tuple[int, int]
) -> None:
    """拡大後の寸法は WebUI の実際の出力と同じく、倍率を掛けて切り捨て、8 の倍数に切り捨てる。"""
    assert hires_target_size(*size, scale) == expected


@pytest.mark.parametrize(
    ("size", "scale", "ok"),
    [
        ("2048x1024", 2.0, True),  # 長辺 4096 ちょうど
        ("2048x1024", 2.05, False),
        # int(2048 * 2.003) = 4102 だが、8 の倍数に切り捨てた実際の出力は 4096
        ("2048x1024", 2.003, True),
        ("1024x1024", 4.0, True),
        ("1032x1024", 4.0, False),
    ],
)
def test_finalize_upscaled_long_edge_limit(
    db_session_factory: sessionmaker, size: str, scale: float, ok: bool
) -> None:
    provider = _provider(FakeSdWebui(), db_session_factory)
    draft = _draft(hires=True, hr_scale=scale, size=size)
    with db_session_factory() as db:
        if ok:
            assert provider.finalize_params(db, draft)["sdwebui_request"]["enable_hr"] is True
        else:
            with pytest.raises(RunValidationError) as excinfo:
                provider.finalize_params(db, draft)
            assert "4096" in str(excinfo.value)


def test_finalize_unknown_upscaler_is_422(db_session_factory: sessionmaker) -> None:
    provider = _provider(FakeSdWebui(), db_session_factory)
    with db_session_factory() as db, pytest.raises(RunValidationError):
        provider.finalize_params(db, _draft(hires=True, hr_upscaler="None"))


def test_finalize_hires_not_available_is_422(db_session_factory: sessionmaker) -> None:
    fake = FakeSdWebui()
    fake.upscalers = None
    fake.latent_upscale_modes = None
    provider = _provider(fake, db_session_factory)
    with db_session_factory() as db, pytest.raises(RunValidationError):
        provider.finalize_params(db, _draft(hires=True))


def test_finalize_edit_rejects_hires(db_session_factory: sessionmaker) -> None:
    provider = _provider(FakeSdWebui(), db_session_factory)
    with db_session_factory() as db, pytest.raises(RunValidationError):
        provider.finalize_params(db, _draft("edit", hires=True))


# -- 実行 ------------------------------------------------------------------------------


def _image_size(data: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(data)) as image:
        return image.size


def test_execute_returns_upscaled_images(db_session_factory: sessionmaker) -> None:
    fake = FakeSdWebui(flavor="forge")
    provider = _provider(fake, db_session_factory)
    params = _finalize(provider, db_session_factory, hires=True, hr_scale=1.5, size="512x768", n=2)
    run = RunRequest(
        run_id=uuid.uuid4(), operation="generate", model="model-a", prompt="a cat", params=params
    )
    result = asyncio.run(provider.execute(run, _noop))
    assert [_image_size(o.data) for o in result.outputs] == [(768, 1152), (768, 1152)]
    assert fake.txt2img_bodies == [params["sdwebui_request"]]
    assert "Hires upscale: 1.5" in result.usage["infotext"]


def test_fake_forge_fails_without_additional_modules(db_session_factory: sessionmaker) -> None:
    """偽の Forge が実物と同じく 500 を返すこと(本文から項目を消して確かめる)。"""
    fake = FakeSdWebui(flavor="forge")
    provider = _provider(fake, db_session_factory)
    params = _finalize(provider, db_session_factory, hires=True, size="512x512")
    request = dict(params["sdwebui_request"])
    del request["hr_additional_modules"]
    params = {**params, "sdwebui_request": request}
    run = RunRequest(
        run_id=uuid.uuid4(), operation="generate", model="model-a", prompt="a cat", params=params
    )
    with pytest.raises(ProviderError) as excinfo:
        asyncio.run(provider.execute(run, _noop))
    assert excinfo.value.code == "executionError"


@pytest.mark.parametrize("flavor", ["forge", "a1111"])
def test_runs_api_hires(client: TestClient, monkeypatch: pytest.MonkeyPatch, flavor: str) -> None:
    fake = FakeSdWebui(flavor=flavor)
    install_fake_factories(monkeypatch, fake)
    assert client.put("/api/sdwebui/connection", json={"url": URL}).status_code == 200

    def post(**params: Any):
        return client.post(
            "/api/runs",
            json={
                "operation": "generate",
                "provider": "sdwebui",
                "model": "model-a",
                "prompt": "a cat",
                "params": params,
            },
        )

    response = post(hires=True, hr_scale=2, size="512x512")
    assert response.status_code == 202, response.text
    detail = wait_for_run_terminal(client, response.json()["id"])
    assert detail["status"] == "succeeded", detail
    request = detail["params"]["sdwebui_request"]
    assert request["enable_hr"] is True
    assert fake.txt2img_bodies == [request]
    with client.app.state.session_factory() as db:
        sizes = db.execute(
            select(Asset.width, Asset.height).where(Asset.produced_by_run_id.isnot(None))
        ).all()
    assert [tuple(s) for s in sizes] == [(1024, 1024)]

    # 拡大後の長辺が 4096 を超えるものと、範囲外の倍率は Run を作らずに 422
    assert post(hires=True, hr_scale=2.5, size="2048x1024").status_code == 422
    assert post(hires=True, hr_scale=4.5).status_code == 422
    # 2回目のプロンプトは送られ、hires が無効なら Run を作らずに 422
    response = post(hires=True, hr_prompt="a cat, detailed")
    assert response.status_code == 202, response.text
    assert wait_for_run_terminal(client, response.json()["id"])["status"] == "succeeded"
    assert fake.txt2img_bodies[-1]["hr_prompt"] == "a cat, detailed"
    count = len(fake.txt2img_bodies)
    assert post(hires=False, hr_prompt="a cat, detailed").status_code == 422
    assert post(hr_negative_prompt="blurry").status_code == 422
    assert len(fake.txt2img_bodies) == count
    # A1111 には hr_cfg が無い
    response = post(hires=True, hr_cfg=5)
    assert response.status_code == (202 if flavor == "forge" else 422)
    if flavor == "forge":
        detail = wait_for_run_terminal(client, response.json()["id"])
        assert detail["status"] == "succeeded", detail
        assert fake.txt2img_bodies[-1]["hr_cfg"] == 5
