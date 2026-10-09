"""`SdWebuiProvider` のテスト(ADR-0038)。偽の WebUI だけを使う。Edit(img2img)は
`test_sdwebui_img2img.py`。"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from sqlalchemy.orm import sessionmaker

from app.domain import general_settings
from app.domain.run_validation import RunValidationError
from app.providers.base import (
    PartialImageEvent,
    ProviderError,
    ProviderUnavailableError,
    RunDraft,
    RunRequest,
    StepProgressEvent,
)
from app.providers.sdwebui.provider import SEED_MAX, SdWebuiProvider
from tests.sdwebui_fake import FakeSdWebui, jpeg_bytes, unreachable_transport

URL = "http://127.0.0.1:7860"


def _provider(
    fake: FakeSdWebui | None,
    session_factory: sessionmaker,
    *,
    timeout: float = 30.0,
    credentials: tuple[str, str] | None = None,
) -> SdWebuiProvider:
    transport = fake.transport() if fake is not None else unreachable_transport()
    return SdWebuiProvider(
        URL,
        session_factory,
        timeout,
        credentials_loader=lambda: credentials,
        transport=transport,
        progress_interval=0.01,
    )


def _draft(model: str = "model-a", **params: Any) -> RunDraft:
    return RunDraft(operation="generate", model=model, prompt="a cat", params=params, inputs=[])


def _request(params: dict[str, Any], model: str = "model-a") -> RunRequest:
    return RunRequest(
        run_id=uuid.uuid4(), operation="generate", model=model, prompt="a cat", params=params
    )


async def _noop(_event: Any) -> None:
    return None


# -- capabilities ------------------------------------------------------------------


def test_capabilities_lists_checkpoints_as_models(db_session_factory: sessionmaker) -> None:
    caps = _provider(FakeSdWebui(), db_session_factory).capabilities()
    assert caps.provider == "sdwebui"
    assert caps.label == "SD WebUI"
    assert [m.model for m in caps.models] == ["model-a", "model-b"]
    assert [m.label for m in caps.models] == ["model-a", "model-b"]
    assert caps.default_model == "model-a"
    assert caps.default_size == "1024x1024"
    assert caps.size is not None
    assert caps.size.multiple_of == 8
    assert caps.size.max_long_edge == 2048
    assert caps.size.allow_auto is False
    assert (caps.n_min, caps.n_max) == (1, 8)
    assert caps.partial_images_max == 0

    model = caps.models[0]
    assert [o.operation for o in model.operations] == ["generate", "edit"]
    params = {p.name: p for p in model.operations[0].params}
    assert set(params) == {
        "negative_prompt",
        "sampler_name",
        "scheduler",
        "steps",
        "cfg_scale",
        "seed",
        "vae",
        "n",
    }
    assert params["sampler_name"].choices == ["Euler a", "Euler", "DPM++ 2M"]
    assert params["sampler_name"].form_default == "Euler a"
    assert params["scheduler"].form_default == "automatic"
    assert params["seed"].widget == "seed"
    assert params["seed"].maximum == SEED_MAX
    assert params["vae"].choices == ["builtin", "vae-a.safetensors", "vae-b.safetensors"]
    assert params["vae"].default == "builtin"
    assert params["vae"].choice_labels == {"builtin": "チェックポイントに内蔵のもの"}
    assert (params["steps"].minimum, params["steps"].maximum, params["steps"].default) == (
        1,
        150,
        20,
    )


def test_capabilities_omit_sampler_and_scheduler_when_unavailable(
    db_session_factory: sessionmaker,
) -> None:
    fake = FakeSdWebui()
    fake.samplers = []
    fake.schedulers = None
    caps = _provider(fake, db_session_factory).capabilities()
    names = {p.name for p in caps.models[0].operations[0].params}
    assert "sampler_name" not in names
    assert "scheduler" not in names


def test_capabilities_empty_when_unreachable(db_session_factory: sessionmaker) -> None:
    provider = _provider(None, db_session_factory)
    caps = provider.capabilities()
    assert caps.models == []
    assert caps.default_model == ""
    available, reason = provider.availability()
    assert available is False
    assert reason


def test_catalog_is_cached_until_invalidated(db_session_factory: sessionmaker) -> None:
    fake = FakeSdWebui()
    provider = _provider(fake, db_session_factory)
    provider.capabilities()
    count = len(fake.requests)
    provider.capabilities()
    assert len(fake.requests) == count
    provider.invalidate_cache()
    provider.capabilities()
    assert len(fake.requests) > count


def test_availability_uses_credentials(db_session_factory: sessionmaker) -> None:
    fake = FakeSdWebui(credentials=("u", "p"))
    assert _provider(fake, db_session_factory).availability()[0] is False
    assert _provider(fake, db_session_factory, credentials=("u", "p")).availability() == (
        True,
        None,
    )


# -- finalize_params ----------------------------------------------------------------


def test_finalize_params_forge(db_session_factory: sessionmaker) -> None:
    provider = _provider(FakeSdWebui(flavor="forge"), db_session_factory)
    with db_session_factory() as db:
        params = provider.finalize_params(
            db,
            _draft(
                seed=42,
                steps=25,
                cfg_scale=6.5,
                size="512x768",
                n=2,
                sampler_name="Euler",
                negative_prompt="blurry",
                vae="vae-a.safetensors",
            ),
        )
    assert params["seed"] == 42
    assert params["sdwebui_seed"] == 42
    task_id = params["sdwebui_task_id"]
    assert task_id.startswith("gakei-")
    request = params["sdwebui_request"]
    assert request == {
        "prompt": "a cat",
        "negative_prompt": "blurry",
        "seed": 42,
        "steps": 25,
        "cfg_scale": 6.5,
        "width": 512,
        "height": 768,
        "batch_size": 2,
        "n_iter": 1,
        "sampler_name": "Euler",
        "save_images": False,
        "send_images": True,
        "force_task_id": task_id,
        "override_settings": {
            "sd_model_checkpoint": "model-a",
            "forge_additional_modules": ["vae-a.safetensors"],
        },
        "override_settings_restore_afterwards": True,
    }


def test_finalize_params_builtin_vae(db_session_factory: sessionmaker) -> None:
    forge = _provider(FakeSdWebui(flavor="forge"), db_session_factory)
    a1111 = _provider(FakeSdWebui(flavor="a1111"), db_session_factory)
    with db_session_factory() as db:
        forge_params = forge.finalize_params(db, _draft())
        a1111_params = a1111.finalize_params(db, _draft())
        a1111_vae = a1111.finalize_params(db, _draft(vae="vae-b.safetensors"))
    assert forge_params["sdwebui_request"]["override_settings"] == {
        "sd_model_checkpoint": "model-a",
        "forge_additional_modules": [],
    }
    assert a1111_params["sdwebui_request"]["override_settings"] == {
        "sd_model_checkpoint": "model-a",
        "sd_vae": "None",
    }
    assert a1111_vae["sdwebui_request"]["override_settings"]["sd_vae"] == "vae-b.safetensors"
    # 指定が無ければ既定(1024x1024、20 ステップ、1 枚)。サンプラーは送らない。
    request = forge_params["sdwebui_request"]
    assert (request["width"], request["height"], request["steps"], request["batch_size"]) == (
        1024,
        1024,
        20,
        1,
    )
    assert "sampler_name" not in request
    assert "scheduler" not in request


def test_finalize_params_assigns_seed_in_range(db_session_factory: sessionmaker) -> None:
    provider = _provider(FakeSdWebui(), db_session_factory)
    with db_session_factory() as db:
        params = provider.finalize_params(db, _draft())
    seed = params["sdwebui_seed"]
    assert 0 <= seed <= SEED_MAX
    assert params["sdwebui_request"]["seed"] == seed
    assert "seed" not in params  # 利用者の指定はそのまま(指定なし)


def test_finalize_params_rejects_unknown_checkpoint(db_session_factory: sessionmaker) -> None:
    provider = _provider(FakeSdWebui(), db_session_factory)
    with db_session_factory() as db, pytest.raises(RunValidationError):
        provider.finalize_params(db, _draft(model="model-z"))


def test_finalize_params_rejects_reserved_prefix(db_session_factory: sessionmaker) -> None:
    provider = _provider(FakeSdWebui(), db_session_factory)
    with db_session_factory() as db, pytest.raises(RunValidationError):
        provider.finalize_params(db, _draft(sdwebui_seed=1))


def test_finalize_params_unavailable_is_409(db_session_factory: sessionmaker) -> None:
    provider = _provider(None, db_session_factory)
    with db_session_factory() as db, pytest.raises(ProviderUnavailableError):
        provider.finalize_params(db, _draft())


# -- execute --------------------------------------------------------------------------


def _finalized(provider: SdWebuiProvider, db_session_factory: sessionmaker, **params: Any):
    with db_session_factory() as db:
        return provider.finalize_params(db, _draft(**params))


def test_execute_success(db_session_factory: sessionmaker) -> None:
    fake = FakeSdWebui()
    fake.extra_images = 1  # グリッドなどの補助の画像は捨てる
    provider = _provider(fake, db_session_factory)
    params = _finalized(provider, db_session_factory, seed=7, n=2)

    result = asyncio.run(provider.execute(_request(params), _noop))

    assert len(result.outputs) == 2
    assert all(o.mime == "image/png" for o in result.outputs)
    assert result.provider_request_id == params["sdwebui_task_id"]
    assert result.usage is not None
    assert result.usage["all_seeds"] == [7, 8]
    assert result.usage["task_id"] == params["sdwebui_task_id"]
    assert "Seed: 7" in result.usage["infotext"]
    assert isinstance(result.usage["duration_ms"], int)
    # 送った本文は記録と同じ
    assert fake.txt2img_bodies == [params["sdwebui_request"]]


def test_execute_model_mismatch_fails(db_session_factory: sessionmaker) -> None:
    fake = FakeSdWebui()
    provider = _provider(fake, db_session_factory)
    params = _finalized(provider, db_session_factory)
    # 記録後に WebUI からチェックポイントが消えた(WebUI は黙って今のもので描く)
    fake.checkpoints = [c for c in fake.checkpoints if c["model_name"] != "model-b"]
    fake.current_model = "model-a"
    params_b = dict(params)
    request = dict(params["sdwebui_request"])
    request["override_settings"] = {"sd_model_checkpoint": "model-b"}
    params_b["sdwebui_request"] = request

    with pytest.raises(ProviderError) as excinfo:
        asyncio.run(provider.execute(_request(params_b, model="model-b"), _noop))
    assert excinfo.value.code == "sdwebuiModelMismatch"


def test_execute_no_output(db_session_factory: sessionmaker) -> None:
    fake = FakeSdWebui()
    fake.return_no_images = True
    provider = _provider(fake, db_session_factory)
    params = _finalized(provider, db_session_factory)
    with pytest.raises(ProviderError) as excinfo:
        asyncio.run(provider.execute(_request(params), _noop))
    assert excinfo.value.code == "sdwebuiNoOutput"


def test_execute_webui_errors(db_session_factory: sessionmaker) -> None:
    fake = FakeSdWebui()
    provider = _provider(fake, db_session_factory)
    params = _finalized(provider, db_session_factory)
    fake.txt2img_status = 500
    fake.txt2img_error_body = {"error": "OutOfMemoryError", "errors": "out of memory"}
    with pytest.raises(ProviderError) as excinfo:
        asyncio.run(provider.execute(_request(params), _noop))
    assert excinfo.value.code == "executionError"
    assert excinfo.value.request_id == params["sdwebui_task_id"]


def test_execute_timeout_uses_saved_setting(db_session_factory: sessionmaker) -> None:
    fake = FakeSdWebui()
    fake.txt2img_delay = 5.0
    provider = _provider(fake, db_session_factory, timeout=0.2)
    params = _finalized(provider, db_session_factory)
    with pytest.raises(ProviderError) as excinfo:
        asyncio.run(provider.execute(_request(params), _noop))
    assert excinfo.value.code == "timeout"

    # 画面で保存した値があればそちらが勝つ(実行開始時に DB を引く)
    with db_session_factory() as db:
        general_settings.save_sdwebui_timeout_seconds(db, 60)
    assert provider._effective_timeout_seconds() == 60.0


def test_execute_streams_progress_and_preview(db_session_factory: sessionmaker) -> None:
    fake = FakeSdWebui()
    fake.txt2img_delay = 0.3
    fake.live_preview = jpeg_bytes()
    provider = _provider(fake, db_session_factory)
    params = _finalized(provider, db_session_factory)
    events: list[Any] = []

    async def on_progress(event: Any) -> None:
        events.append(event)

    asyncio.run(provider.execute(_request(params), on_progress))

    steps = [e for e in events if isinstance(e, StepProgressEvent)]
    previews = [e for e in events if isinstance(e, PartialImageEvent)]
    assert steps and steps[0].max == 100
    assert [s.value for s in steps][:2] == [25, 50]
    assert previews
    assert previews[0].mime == "image/png"
    assert previews[0].data.startswith(b"\x89PNG")
    # 自分の実行の ID で問い合わせ、次からは前回の id_live_preview を渡す
    assert fake.progress_bodies[0]["id_task"] == params["sdwebui_task_id"]
    assert fake.progress_bodies[0]["id_live_preview"] == -1
    if len(fake.progress_bodies) > 1:
        assert fake.progress_bodies[1]["id_live_preview"] == 1


def test_execute_falls_back_to_public_progress(db_session_factory: sessionmaker) -> None:
    fake = FakeSdWebui()
    fake.internal_progress = False
    fake.txt2img_delay = 0.2
    provider = _provider(fake, db_session_factory)
    params = _finalized(provider, db_session_factory)
    events: list[Any] = []

    async def on_progress(event: Any) -> None:
        events.append(event)

    asyncio.run(provider.execute(_request(params), on_progress))
    steps = [e for e in events if isinstance(e, StepProgressEvent)]
    assert steps
    assert steps[0].max == 20
    assert any(r.url.path == "/sdapi/v1/progress" for r in fake.requests)


# -- 共有ページと PNG 埋め込みでの params の扱い -------------------------------------------


def test_internal_params_are_not_public() -> None:
    from app.domain.embedded_meta import _EXCLUDED_PARAM_KEYS
    from app.domain.shares import public_params

    params = {
        "steps": 20,
        "seed": 1,
        "sdwebui_seed": 1,
        "sdwebui_task_id": "gakei-0123",
        "sdwebui_request": {"prompt": "a cat"},
    }
    assert public_params(params, "sdwebui") == {"steps": 20, "seed": 1, "sdwebui_seed": 1}
    assert "sdwebui_request" in _EXCLUDED_PARAM_KEYS
