"""`app/providers/comfyui/provider.py`(`ComfyUIProvider`)のテスト(ADR-0013)。

実物の ComfyUI には一切接続しない。HTTP は `tests/comfyui_fake.py` の `FakeComfyUI`、
WebSocket は `fake_ws_connect` / `unavailable_ws_connect` で組み立てる。
"""

from __future__ import annotations

import copy
import hashlib
import io
import uuid
from datetime import UTC, datetime

import pytest
from PIL import Image, ImageDraw
from sqlalchemy.orm import sessionmaker

from app.domain.comfy_workflow import (
    SEED_MAX,
    Bindings,
    ExposedParam,
    InputRef,
    MaskBinding,
    compute_template_sha256,
    resolve_prompt,
)
from app.domain.models import ComfyWorkflow
from app.domain.run_validation import RunValidationError
from app.providers.base import (
    InputImage,
    PartialImageEvent,
    ProviderError,
    RunDraft,
    RunInputMeta,
    RunRequest,
    StepProgressEvent,
)
from app.providers.comfyui.client import (
    ComfyUIClient,
    WsMessage,
    collect_output_texts,
    sanitize_text,
)
from app.providers.comfyui.provider import ClientFactory, ComfyUIProvider
from tests.comfyui_fake import (
    FakeComfyUI,
    encode_preview_frame,
    fake_ws_connect,
    unavailable_ws_connect,
)
from tests.comfyui_graphs import IMG2IMG_GRAPH, INPAINT_GRAPH, QWEN_EDIT_GRAPH, T2I_GRAPH, clone
from tests.conftest import make_png_bytes
from tests.openai_mock import run_async

# -- ヘルパー -------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _create_workflow(
    session_factory: sessionmaker,
    *,
    name: str = "wf",
    operation: str = "generate",
    template: dict,
    bindings: Bindings,
    exposed_params: list[ExposedParam] | None = None,
) -> uuid.UUID:
    wf = ComfyWorkflow(
        name=name,
        operation=operation,
        template=template,
        bindings=bindings.model_dump(mode="json"),
        exposed_params=[p.model_dump(mode="json") for p in (exposed_params or [])],
        template_sha256=compute_template_sha256(template),
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    with session_factory() as session:
        session.add(wf)
        session.commit()
        return wf.id


def _t2i_bindings(outputs: tuple[str, ...] = ("9",)) -> Bindings:
    return Bindings(
        prompt=InputRef(node="6", input="text"),
        negative_prompt=InputRef(node="7", input="text"),
        seed=[InputRef(node="3", input="seed")],
        width=InputRef(node="5", input="width"),
        height=InputRef(node="5", input="height"),
        batch_size=InputRef(node="5", input="batch_size"),
        outputs=list(outputs),
    )


def _inpaint_bindings() -> Bindings:
    return Bindings(
        prompt=InputRef(node="6", input="text"),
        negative_prompt=InputRef(node="7", input="text"),
        seed=[InputRef(node="3", input="seed")],
        images=[InputRef(node="10", input="image")],
        mask=MaskBinding(mode="load_image_mask", node="12", input="image"),
        outputs=["9"],
    )


def _image_alpha_bindings() -> Bindings:
    return Bindings(
        prompt=InputRef(node="6", input="text"),
        negative_prompt=InputRef(node="7", input="text"),
        seed=[InputRef(node="3", input="seed")],
        images=[InputRef(node="10", input="image")],
        mask=MaskBinding(mode="image_alpha"),
        outputs=["9"],
    )


def _two_slot_bindings() -> Bindings:
    """QWEN_EDIT_GRAPH(person/garment の2枠)向けのバインディング。マスクなし。"""
    return Bindings(
        prompt=InputRef(node="452", input="prompt"),
        negative_prompt=InputRef(node="452", input="negative_prompt"),
        seed=[InputRef(node="458", input="seed")],
        images=[
            InputRef(node="480", input="image"),
            InputRef(node="410", input="image"),
        ],
        outputs=["461"],
    )


def _image_meta(*, sha256: str, mime: str = "image/png", position: int = 0) -> RunInputMeta:
    return RunInputMeta(
        asset_id=uuid.uuid4(),
        role="image",
        position=position,
        width=64,
        height=64,
        sha256=sha256,
        mime=mime,
    )


def _mask_meta(*, sha256: str, mime: str = "image/png") -> RunInputMeta:
    return RunInputMeta(
        asset_id=uuid.uuid4(),
        role="mask",
        position=0,
        width=64,
        height=64,
        sha256=sha256,
        mime=mime,
    )


def _client_factory(fake: FakeComfyUI, ws_connect) -> ClientFactory:  # noqa: ANN001
    def factory() -> ComfyUIClient:
        return ComfyUIClient(
            "http://127.0.0.1:8188", http=fake.make_async_client(), ws_connect=ws_connect
        )

    return factory


async def _noop_progress(event: object) -> None:
    del event


def _finalize(
    provider: ComfyUIProvider,
    session_factory: sessionmaker,
    *,
    workflow_id: uuid.UUID,
    operation: str = "generate",
    prompt: str = "p",
    params: dict | None = None,
    inputs: list[RunInputMeta] | None = None,
) -> dict:
    with session_factory() as db:
        return provider.finalize_params(
            db,
            RunDraft(
                operation=operation,
                model=str(workflow_id),
                prompt=prompt,
                params=params or {},
                inputs=inputs or [],
            ),
        )


# -- finalize_params ------------------------------------------------------------


def test_finalize_params_uses_client_seed_when_provided(db_session_factory: sessionmaker) -> None:
    workflow_id = _create_workflow(
        db_session_factory, template=clone(T2I_GRAPH), bindings=_t2i_bindings()
    )
    provider = ComfyUIProvider("http://127.0.0.1:8188", db_session_factory, 30.0)

    result = _finalize(
        provider, db_session_factory, workflow_id=workflow_id, params={"seed": 12345}
    )

    assert result["comfyui_seed"] == 12345
    assert result["seed"] == 12345
    assert result["comfyui_prompt"]["3"]["inputs"]["seed"] == 12345


def test_finalize_params_generates_random_seed_within_range_when_absent(
    db_session_factory: sessionmaker,
) -> None:
    workflow_id = _create_workflow(
        db_session_factory, template=clone(T2I_GRAPH), bindings=_t2i_bindings()
    )
    provider = ComfyUIProvider("http://127.0.0.1:8188", db_session_factory, 30.0)

    result = _finalize(provider, db_session_factory, workflow_id=workflow_id)

    assert "seed" not in result  # クライアントが指定しなければ params に "seed" は増えない
    seed = result["comfyui_seed"]
    assert isinstance(seed, int)
    assert 0 <= seed <= SEED_MAX
    assert result["comfyui_prompt"]["3"]["inputs"]["seed"] == seed


def test_finalize_params_upload_names_derived_from_sha256_and_mime(
    db_session_factory: sessionmaker,
) -> None:
    workflow_id = _create_workflow(
        db_session_factory,
        operation="edit",
        template=clone(INPAINT_GRAPH),
        bindings=_inpaint_bindings(),
    )
    provider = ComfyUIProvider("http://127.0.0.1:8188", db_session_factory, 30.0)
    image_sha = "a" * 64
    mask_sha = "b" * 64

    result = _finalize(
        provider,
        db_session_factory,
        workflow_id=workflow_id,
        operation="edit",
        inputs=[
            _image_meta(sha256=image_sha, mime="image/jpeg"),
            _mask_meta(sha256=mask_sha, mime="image/png"),
        ],
    )

    assert result["comfyui_uploads"] == {
        "images": [f"gakei_{image_sha}.jpg"],
        "mask": f"gakei_{mask_sha}.png",
    }
    assert result["comfyui_mask_mode"] == "load_image_mask"
    assert result["comfyui_prompt"]["10"]["inputs"]["image"] == f"gakei_{image_sha}.jpg"
    assert result["comfyui_prompt"]["12"]["inputs"]["image"] == f"gakei_{mask_sha}.png"


def test_finalize_params_two_slots_uploads_and_resolves_in_order(
    db_session_factory: sessionmaker,
) -> None:
    workflow_id = _create_workflow(
        db_session_factory,
        operation="edit",
        template=clone(QWEN_EDIT_GRAPH),
        bindings=_two_slot_bindings(),
    )
    provider = ComfyUIProvider("http://127.0.0.1:8188", db_session_factory, 30.0)
    person_sha = "e" * 64
    garment_sha = "f" * 64

    result = _finalize(
        provider,
        db_session_factory,
        workflow_id=workflow_id,
        operation="edit",
        inputs=[
            _image_meta(sha256=person_sha, position=0),
            _image_meta(sha256=garment_sha, position=1),
        ],
    )

    assert result["comfyui_uploads"] == {
        "images": [f"gakei_{person_sha}.png", f"gakei_{garment_sha}.png"]
    }
    assert result["comfyui_prompt"]["480"]["inputs"]["image"] == f"gakei_{person_sha}.png"
    assert result["comfyui_prompt"]["410"]["inputs"]["image"] == f"gakei_{garment_sha}.png"


def test_finalize_params_image_alpha_combines_both_sha256_into_composite_name(
    db_session_factory: sessionmaker,
) -> None:
    workflow_id = _create_workflow(
        db_session_factory,
        operation="edit",
        template=clone(IMG2IMG_GRAPH),
        bindings=_image_alpha_bindings(),
    )
    provider = ComfyUIProvider("http://127.0.0.1:8188", db_session_factory, 30.0)
    image_sha = "c" * 64
    mask_sha = "d" * 64

    result = _finalize(
        provider,
        db_session_factory,
        workflow_id=workflow_id,
        operation="edit",
        inputs=[_image_meta(sha256=image_sha), _mask_meta(sha256=mask_sha)],
    )

    composite_name = f"gakei_{image_sha}_{mask_sha}.png"
    assert result["comfyui_uploads"] == {"images": [composite_name]}
    assert result["comfyui_mask_mode"] == "image_alpha"
    assert result["comfyui_prompt"]["10"]["inputs"]["image"] == composite_name


def test_finalize_params_non_uuid_model_raises_validation_error(
    db_session_factory: sessionmaker,
) -> None:
    provider = ComfyUIProvider("http://127.0.0.1:8188", db_session_factory, 30.0)
    with db_session_factory() as db, pytest.raises(RunValidationError):
        provider.finalize_params(
            db, RunDraft(operation="generate", model="not-a-uuid", prompt="p", params={}, inputs=[])
        )


def test_finalize_params_deleted_workflow_raises_validation_error(
    db_session_factory: sessionmaker,
) -> None:
    workflow_id = _create_workflow(
        db_session_factory, template=clone(T2I_GRAPH), bindings=_t2i_bindings()
    )
    with db_session_factory() as session:
        wf = session.get(ComfyWorkflow, workflow_id)
        assert wf is not None
        wf.deleted_at = _utcnow()
        session.commit()

    provider = ComfyUIProvider("http://127.0.0.1:8188", db_session_factory, 30.0)
    with db_session_factory() as db, pytest.raises(RunValidationError):
        provider.finalize_params(
            db,
            RunDraft(
                operation="generate", model=str(workflow_id), prompt="p", params={}, inputs=[]
            ),
        )


def test_finalize_params_operation_mismatch_raises_validation_error(
    db_session_factory: sessionmaker,
) -> None:
    workflow_id = _create_workflow(
        db_session_factory,
        operation="generate",
        template=clone(T2I_GRAPH),
        bindings=_t2i_bindings(),
    )
    provider = ComfyUIProvider("http://127.0.0.1:8188", db_session_factory, 30.0)
    with db_session_factory() as db, pytest.raises(RunValidationError):
        provider.finalize_params(
            db, RunDraft(operation="edit", model=str(workflow_id), prompt="p", params={}, inputs=[])
        )


def test_finalize_params_includes_comfyui_fields(db_session_factory: sessionmaker) -> None:
    workflow_id = _create_workflow(
        db_session_factory, name="サンプル", template=clone(T2I_GRAPH), bindings=_t2i_bindings()
    )
    provider = ComfyUIProvider("http://127.0.0.1:8188", db_session_factory, 30.0)

    result = _finalize(provider, db_session_factory, workflow_id=workflow_id, params={"seed": 7})

    workflow_info = result["comfyui_workflow"]
    assert workflow_info == {
        "id": str(workflow_id),
        "name": "サンプル",
        "template_sha256": result["comfyui_workflow"]["template_sha256"],
    }
    assert result["comfyui_outputs"] == ["9"]
    assert isinstance(result["comfyui_prompt"], dict)


def test_finalize_params_is_deterministic(db_session_factory: sessionmaker) -> None:
    workflow_id = _create_workflow(
        db_session_factory, template=clone(T2I_GRAPH), bindings=_t2i_bindings()
    )
    provider = ComfyUIProvider("http://127.0.0.1:8188", db_session_factory, 30.0)
    params = {"seed": 42, "width": 768}

    result1 = _finalize(provider, db_session_factory, workflow_id=workflow_id, params=dict(params))
    result2 = _finalize(provider, db_session_factory, workflow_id=workflow_id, params=dict(params))

    assert result1 == result2


# -- capabilities / availability -------------------------------------------------


def test_capabilities_defaults_when_no_workflow_registered(
    db_session_factory: sessionmaker,
) -> None:
    provider = ComfyUIProvider("http://127.0.0.1:8188", db_session_factory, 30.0)
    caps = provider.capabilities()
    assert caps.models == []
    assert caps.default_model == ""
    assert caps.size is None
    assert caps.max_input_images == 1
    assert caps.n_min == 1
    assert caps.n_max == 1


def test_availability_is_cached_for_5_seconds(
    monkeypatch: pytest.MonkeyPatch, db_session_factory: sessionmaker
) -> None:
    calls: list[str] = []

    def fake_check_available(base_url: str, timeout: float = 1.0) -> tuple[bool, str | None, dict]:
        calls.append(base_url)
        return True, None, {}

    monkeypatch.setattr("app.providers.comfyui.provider.check_available", fake_check_available)

    provider = ComfyUIProvider("http://127.0.0.1:8188", db_session_factory, 30.0)
    assert provider.availability() == (True, None)
    assert provider.availability() == (True, None)
    assert len(calls) == 1


# -- execute ----------------------------------------------------------------


@run_async
async def test_execute_success_reports_progress_and_outputs_in_order(
    db_session_factory: sessionmaker,
) -> None:
    template = clone(T2I_GRAPH)
    template["10"] = copy.deepcopy(template["9"])
    workflow_id = _create_workflow(
        db_session_factory, template=template, bindings=_t2i_bindings(outputs=("9", "10"))
    )

    fake = FakeComfyUI()
    png_a = make_png_bytes(color=(10, 10, 10))
    png_b = make_png_bytes(color=(20, 20, 20))
    fake.set_history(
        "prompt-1",
        {
            "outputs": {
                "9": {"images": [{"filename": "a.png", "subfolder": "", "type": "output"}]},
                "10": {"images": [{"filename": "b.png", "subfolder": "", "type": "output"}]},
            },
            "status": {"status_str": "success", "completed": True},
        },
    )
    fake.add_output_file("a.png", "", "output", png_a)
    fake.add_output_file("b.png", "", "output", png_b)

    ws_frames = [
        WsMessage("executing", {"prompt_id": "prompt-1", "node": "3"}),
        WsMessage("progress", {"prompt_id": "prompt-1", "value": 5, "max": 20, "node": "3"}),
        WsMessage("executing", {"prompt_id": "prompt-1", "node": None}),
    ]
    provider = ComfyUIProvider(
        "http://127.0.0.1:8188",
        db_session_factory,
        30.0,
        client_factory=_client_factory(fake, fake_ws_connect(ws_frames)),
    )
    params = _finalize(provider, db_session_factory, workflow_id=workflow_id, params={"seed": 1})
    run = RunRequest(
        run_id=uuid.uuid4(), operation="generate", model=str(workflow_id), prompt="p", params=params
    )

    progress_events: list[StepProgressEvent] = []

    async def on_progress(event: object) -> None:
        if isinstance(event, StepProgressEvent):
            progress_events.append(event)

    result = await provider.execute(run, on_progress)

    assert [e.value for e in progress_events] == [5]
    assert progress_events[0].max == 20
    assert progress_events[0].node == "3"
    assert [o.data for o in result.outputs] == [png_a, png_b]
    assert result.usage == {"prompt_id": "prompt-1", "duration_ms": result.usage["duration_ms"]}
    assert isinstance(result.usage["duration_ms"], int)
    assert result.provider_request_id == "prompt-1"


@run_async
async def test_execute_previews_become_png_partial_events_and_are_throttled(
    db_session_factory: sessionmaker,
) -> None:
    workflow_id = _create_workflow(
        db_session_factory, template=clone(T2I_GRAPH), bindings=_t2i_bindings()
    )
    fake = FakeComfyUI()
    fake.set_history(
        "prompt-1",
        {
            "outputs": {
                "9": {"images": [{"filename": "out.png", "subfolder": "", "type": "output"}]}
            },
            "status": {"status_str": "success"},
        },
    )
    fake.add_output_file("out.png", "", "output", make_png_bytes())

    preview_bytes = make_png_bytes(width=8, height=8)
    ws_frames = [
        WsMessage("executing", {"prompt_id": "prompt-1", "node": "3"}),
        encode_preview_frame(preview_bytes, image_type=2),
        encode_preview_frame(preview_bytes, image_type=2),
        encode_preview_frame(preview_bytes, image_type=2),
        WsMessage("execution_success", {"prompt_id": "prompt-1"}),
    ]
    clock_values = iter([0.0, 0.2, 0.6])
    provider = ComfyUIProvider(
        "http://127.0.0.1:8188",
        db_session_factory,
        30.0,
        client_factory=_client_factory(fake, fake_ws_connect(ws_frames)),
        clock=lambda: next(clock_values),
    )
    params = _finalize(provider, db_session_factory, workflow_id=workflow_id, params={"seed": 1})
    run = RunRequest(
        run_id=uuid.uuid4(), operation="generate", model=str(workflow_id), prompt="p", params=params
    )

    partials: list[PartialImageEvent] = []

    async def on_progress(event: object) -> None:
        if isinstance(event, PartialImageEvent):
            partials.append(event)

    await provider.execute(run, on_progress)

    # 0.0秒と0.6秒の2枚だけ通る。0.2秒(前回から0.5秒未満)は間引かれる。
    assert [p.partial_index for p in partials] == [0, 1]
    assert all(p.mime == "image/png" for p in partials)
    assert all(p.output_index == 0 for p in partials)


@run_async
async def test_execute_prompt_validation_error_maps_to_comfyui_validation(
    db_session_factory: sessionmaker,
) -> None:
    workflow_id = _create_workflow(
        db_session_factory, template=clone(T2I_GRAPH), bindings=_t2i_bindings()
    )
    fake = FakeComfyUI()
    fake.queue_error(status_code=400)

    provider = ComfyUIProvider(
        "http://127.0.0.1:8188",
        db_session_factory,
        5.0,
        client_factory=_client_factory(fake, unavailable_ws_connect()),
    )
    params = _finalize(provider, db_session_factory, workflow_id=workflow_id, params={"seed": 1})
    run = RunRequest(
        run_id=uuid.uuid4(), operation="generate", model=str(workflow_id), prompt="p", params=params
    )

    with pytest.raises(ProviderError) as exc_info:
        await provider.execute(run, _noop_progress)
    assert exc_info.value.code == "comfyuiValidation"


@run_async
async def test_execute_execution_error_event_maps_to_execution_error(
    db_session_factory: sessionmaker,
) -> None:
    workflow_id = _create_workflow(
        db_session_factory, template=clone(T2I_GRAPH), bindings=_t2i_bindings()
    )
    fake = FakeComfyUI()
    ws_frames = [
        WsMessage("executing", {"prompt_id": "prompt-1", "node": "3"}),
        WsMessage(
            "execution_error",
            {
                "prompt_id": "prompt-1",
                "node_id": "3",
                "exception_type": "ValueError",
                "exception_message": "boom",
            },
        ),
    ]
    provider = ComfyUIProvider(
        "http://127.0.0.1:8188",
        db_session_factory,
        5.0,
        client_factory=_client_factory(fake, fake_ws_connect(ws_frames)),
    )
    params = _finalize(provider, db_session_factory, workflow_id=workflow_id, params={"seed": 1})
    run = RunRequest(
        run_id=uuid.uuid4(), operation="generate", model=str(workflow_id), prompt="p", params=params
    )

    with pytest.raises(ProviderError) as exc_info:
        await provider.execute(run, _noop_progress)
    assert exc_info.value.code == "executionError"
    assert "boom" in exc_info.value.message
    assert "3" in exc_info.value.message
    assert exc_info.value.request_id == "prompt-1"


@run_async
async def test_execute_ignores_events_for_other_prompt_id(db_session_factory: sessionmaker) -> None:
    workflow_id = _create_workflow(
        db_session_factory, template=clone(T2I_GRAPH), bindings=_t2i_bindings()
    )
    fake = FakeComfyUI()
    out_bytes = make_png_bytes()
    fake.set_history(
        "prompt-1",
        {
            "outputs": {
                "9": {"images": [{"filename": "out.png", "subfolder": "", "type": "output"}]}
            },
            "status": {"status_str": "success"},
        },
    )
    fake.add_output_file("out.png", "", "output", out_bytes)

    ws_frames = [
        WsMessage("executing", {"prompt_id": "other-run", "node": "1"}),
        WsMessage(
            "execution_error",
            {
                "prompt_id": "other-run",
                "node_id": "1",
                "exception_type": "X",
                "exception_message": "should be ignored",
            },
        ),
        WsMessage("executing", {"prompt_id": "prompt-1", "node": "3"}),
        WsMessage("execution_success", {"prompt_id": "prompt-1"}),
    ]
    provider = ComfyUIProvider(
        "http://127.0.0.1:8188",
        db_session_factory,
        5.0,
        client_factory=_client_factory(fake, fake_ws_connect(ws_frames)),
    )
    params = _finalize(provider, db_session_factory, workflow_id=workflow_id, params={"seed": 1})
    run = RunRequest(
        run_id=uuid.uuid4(), operation="generate", model=str(workflow_id), prompt="p", params=params
    )

    result = await provider.execute(run, _noop_progress)
    assert result.outputs[0].data == out_bytes


@run_async
async def test_execute_falls_back_to_polling_after_ws_disconnect(
    db_session_factory: sessionmaker,
) -> None:
    workflow_id = _create_workflow(
        db_session_factory, template=clone(T2I_GRAPH), bindings=_t2i_bindings()
    )
    fake = FakeComfyUI()
    fake.set_history(
        "prompt-1",
        {
            "outputs": {
                "9": {"images": [{"filename": "out.png", "subfolder": "", "type": "output"}]}
            },
            "status": {"status_str": "success"},
        },
    )
    fake.add_output_file("out.png", "", "output", make_png_bytes())

    ws_frames = [
        WsMessage("executing", {"prompt_id": "prompt-1", "node": "3"}),
        ConnectionError("boom"),
    ]
    provider = ComfyUIProvider(
        "http://127.0.0.1:8188",
        db_session_factory,
        5.0,
        client_factory=_client_factory(fake, fake_ws_connect(ws_frames)),
    )
    params = _finalize(provider, db_session_factory, workflow_id=workflow_id, params={"seed": 1})
    run = RunRequest(
        run_id=uuid.uuid4(), operation="generate", model=str(workflow_id), prompt="p", params=params
    )

    result = await provider.execute(run, _noop_progress)
    assert result.provider_request_id == "prompt-1"


@run_async
async def test_execute_timeout_raises_provider_error(db_session_factory: sessionmaker) -> None:
    workflow_id = _create_workflow(
        db_session_factory, template=clone(T2I_GRAPH), bindings=_t2i_bindings()
    )
    fake = FakeComfyUI()  # /history に何も登録しない -> 完了しない

    provider = ComfyUIProvider(
        "http://127.0.0.1:8188",
        db_session_factory,
        0.05,
        client_factory=_client_factory(fake, unavailable_ws_connect()),
    )
    params = _finalize(provider, db_session_factory, workflow_id=workflow_id, params={"seed": 1})
    run = RunRequest(
        run_id=uuid.uuid4(), operation="generate", model=str(workflow_id), prompt="p", params=params
    )

    with pytest.raises(ProviderError) as exc_info:
        await provider.execute(run, _noop_progress)
    assert exc_info.value.code == "timeout"


@run_async
async def test_execute_no_output_raises_comfyui_no_output(db_session_factory: sessionmaker) -> None:
    workflow_id = _create_workflow(
        db_session_factory, template=clone(T2I_GRAPH), bindings=_t2i_bindings()
    )
    fake = FakeComfyUI()
    fake.set_history("prompt-1", {"outputs": {}, "status": {"status_str": "success"}})

    provider = ComfyUIProvider(
        "http://127.0.0.1:8188",
        db_session_factory,
        5.0,
        client_factory=_client_factory(fake, unavailable_ws_connect()),
    )
    params = _finalize(provider, db_session_factory, workflow_id=workflow_id, params={"seed": 1})
    run = RunRequest(
        run_id=uuid.uuid4(), operation="generate", model=str(workflow_id), prompt="p", params=params
    )

    with pytest.raises(ProviderError) as exc_info:
        await provider.execute(run, _noop_progress)
    assert exc_info.value.code == "comfyuiNoOutput"


@run_async
async def test_execute_image_alpha_uploads_composite_with_mask_alpha(
    db_session_factory: sessionmaker,
) -> None:
    workflow_id = _create_workflow(
        db_session_factory,
        operation="edit",
        template=clone(IMG2IMG_GRAPH),
        bindings=_image_alpha_bindings(),
    )
    fake = FakeComfyUI()
    fake.set_history(
        "prompt-1",
        {
            "outputs": {
                "9": {"images": [{"filename": "out.png", "subfolder": "", "type": "output"}]}
            },
            "status": {"status_str": "success"},
        },
    )
    fake.add_output_file("out.png", "", "output", make_png_bytes())

    provider = ComfyUIProvider(
        "http://127.0.0.1:8188",
        db_session_factory,
        5.0,
        client_factory=_client_factory(fake, unavailable_ws_connect()),
    )

    image_bytes = make_png_bytes(color=(200, 0, 0))
    mask_image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(mask_image)
    draw.rectangle([0, 0, 31, 63], fill=(0, 0, 0, 255))  # 左半分だけ alpha=255
    mask_buffer = io.BytesIO()
    mask_image.save(mask_buffer, format="PNG")
    mask_bytes = mask_buffer.getvalue()

    image_meta = _image_meta(sha256=hashlib.sha256(image_bytes).hexdigest())
    mask_meta = _mask_meta(sha256=hashlib.sha256(mask_bytes).hexdigest())

    params = _finalize(
        provider,
        db_session_factory,
        workflow_id=workflow_id,
        operation="edit",
        params={"seed": 1},
        inputs=[image_meta, mask_meta],
    )
    run = RunRequest(
        run_id=uuid.uuid4(),
        operation="edit",
        model=str(workflow_id),
        prompt="p",
        params=params,
        inputs=[
            InputImage(role="image", position=0, data=image_bytes, mime="image/png"),
            InputImage(role="mask", position=0, data=mask_bytes, mime="image/png"),
        ],
    )

    await provider.execute(run, _noop_progress)

    assert len(fake.uploads) == 1
    composite = Image.open(io.BytesIO(fake.uploads[0]["image_bytes"]))
    assert composite.mode == "RGBA"
    alpha = composite.split()[-1]
    assert alpha.getpixel((0, 0)) == 255
    assert alpha.getpixel((63, 0)) == 0


# -- execute: 画像の枠が複数(ADR-0013 3節) ---------------------------------------


@run_async
async def test_execute_two_slots_uploads_both_and_resolves_graph_in_order(
    db_session_factory: sessionmaker,
) -> None:
    workflow_id = _create_workflow(
        db_session_factory,
        operation="edit",
        template=clone(QWEN_EDIT_GRAPH),
        bindings=_two_slot_bindings(),
    )
    fake = FakeComfyUI()
    fake.set_history(
        "prompt-1",
        {
            "outputs": {
                "461": {"images": [{"filename": "out.png", "subfolder": "", "type": "output"}]}
            },
            "status": {"status_str": "success"},
        },
    )
    fake.add_output_file("out.png", "", "output", make_png_bytes())

    provider = ComfyUIProvider(
        "http://127.0.0.1:8188",
        db_session_factory,
        5.0,
        client_factory=_client_factory(fake, unavailable_ws_connect()),
    )

    person_bytes = make_png_bytes(color=(10, 20, 30))
    garment_bytes = make_png_bytes(color=(40, 50, 60))
    person_meta = _image_meta(sha256=hashlib.sha256(person_bytes).hexdigest(), position=0)
    garment_meta = _image_meta(sha256=hashlib.sha256(garment_bytes).hexdigest(), position=1)

    params = _finalize(
        provider,
        db_session_factory,
        workflow_id=workflow_id,
        operation="edit",
        params={"seed": 1},
        inputs=[person_meta, garment_meta],
    )
    run = RunRequest(
        run_id=uuid.uuid4(),
        operation="edit",
        model=str(workflow_id),
        prompt="p",
        params=params,
        inputs=[
            InputImage(role="image", position=0, data=person_bytes, mime="image/png"),
            InputImage(role="image", position=1, data=garment_bytes, mime="image/png"),
        ],
    )

    await provider.execute(run, _noop_progress)

    uploaded_names = {u["image_filename"] for u in fake.uploads}
    assert uploaded_names == {
        f"gakei_{person_meta.sha256}.png",
        f"gakei_{garment_meta.sha256}.png",
    }
    # 枠1(position=0)が LoadImage "480"、枠2(position=1)が LoadImage "410" に入る
    # (テンプレートの images.image_1 / images.image_2 の番号どおり。ADR-0013 3節)。
    queued_graph = fake.queued_prompts[-1]["prompt"]
    assert queued_graph["480"]["inputs"]["image"] == f"gakei_{person_meta.sha256}.png"
    assert queued_graph["410"]["inputs"]["image"] == f"gakei_{garment_meta.sha256}.png"


@run_async
async def test_execute_image_alpha_with_two_slots_composites_only_first_slot(
    db_session_factory: sessionmaker,
) -> None:
    bindings = _two_slot_bindings().model_copy(update={"mask": MaskBinding(mode="image_alpha")})
    workflow_id = _create_workflow(
        db_session_factory, operation="edit", template=clone(QWEN_EDIT_GRAPH), bindings=bindings
    )
    fake = FakeComfyUI()
    fake.set_history(
        "prompt-1",
        {
            "outputs": {
                "461": {"images": [{"filename": "out.png", "subfolder": "", "type": "output"}]}
            },
            "status": {"status_str": "success"},
        },
    )
    fake.add_output_file("out.png", "", "output", make_png_bytes())

    provider = ComfyUIProvider(
        "http://127.0.0.1:8188",
        db_session_factory,
        5.0,
        client_factory=_client_factory(fake, unavailable_ws_connect()),
    )

    person_bytes = make_png_bytes(color=(200, 0, 0))
    garment_bytes = make_png_bytes(color=(0, 200, 0))
    mask_image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(mask_image)
    draw.rectangle([0, 0, 31, 63], fill=(0, 0, 0, 255))  # 左半分だけ alpha=255
    mask_buffer = io.BytesIO()
    mask_image.save(mask_buffer, format="PNG")
    mask_bytes = mask_buffer.getvalue()

    person_meta = _image_meta(sha256=hashlib.sha256(person_bytes).hexdigest(), position=0)
    garment_meta = _image_meta(sha256=hashlib.sha256(garment_bytes).hexdigest(), position=1)
    mask_meta = _mask_meta(sha256=hashlib.sha256(mask_bytes).hexdigest())

    params = _finalize(
        provider,
        db_session_factory,
        workflow_id=workflow_id,
        operation="edit",
        params={"seed": 1},
        inputs=[person_meta, garment_meta, mask_meta],
    )
    run = RunRequest(
        run_id=uuid.uuid4(),
        operation="edit",
        model=str(workflow_id),
        prompt="p",
        params=params,
        inputs=[
            InputImage(role="image", position=0, data=person_bytes, mime="image/png"),
            InputImage(role="image", position=1, data=garment_bytes, mime="image/png"),
            InputImage(role="mask", position=0, data=mask_bytes, mime="image/png"),
        ],
    )

    await provider.execute(run, _noop_progress)

    # 枠1(人物)はマスクの alpha を合成したものをアップロードし、枠2(衣服)はそのまま
    # アップロードする(マスクは1枚目の画像に対するものだけ。ADR-0013 3節)。
    assert len(fake.uploads) == 2
    composite = Image.open(io.BytesIO(fake.uploads[0]["image_bytes"]))
    assert composite.mode == "RGBA"
    alpha = composite.split()[-1]
    assert alpha.getpixel((0, 0)) == 255
    assert alpha.getpixel((63, 0)) == 0

    second_upload = Image.open(io.BytesIO(fake.uploads[1]["image_bytes"])).convert("RGB")
    assert second_upload.getpixel((0, 0)) == (0, 200, 0)


@run_async
async def test_execute_legacy_single_image_uploads_key_still_works(
    db_session_factory: sessionmaker,
) -> None:
    """移行前にキューされた Run の `comfyui_uploads` は `{"image": name}`(単数)。
    `finalize_params` を経由しない、既にキューされている Run の再実行相当を再現する。
    """
    workflow_id = _create_workflow(
        db_session_factory,
        operation="edit",
        template=clone(INPAINT_GRAPH),
        bindings=_inpaint_bindings(),
    )
    fake = FakeComfyUI()
    fake.set_history(
        "prompt-1",
        {
            "outputs": {
                "9": {"images": [{"filename": "out.png", "subfolder": "", "type": "output"}]}
            },
            "status": {"status_str": "success"},
        },
    )
    fake.add_output_file("out.png", "", "output", make_png_bytes())

    provider = ComfyUIProvider(
        "http://127.0.0.1:8188",
        db_session_factory,
        5.0,
        client_factory=_client_factory(fake, unavailable_ws_connect()),
    )

    image_bytes = make_png_bytes()
    graph = resolve_prompt(
        clone(INPAINT_GRAPH),
        _inpaint_bindings(),
        [],
        prompt="p",
        params={},
        seed=1,
        image_names=["legacy.png"],
        mask_name=None,
    )
    run = RunRequest(
        run_id=uuid.uuid4(),
        operation="edit",
        model=str(workflow_id),
        prompt="p",
        params={
            "comfyui_prompt": graph,
            "comfyui_outputs": ["9"],
            "comfyui_uploads": {"image": "legacy.png"},  # 旧形式(単数)
        },
        inputs=[InputImage(role="image", position=0, data=image_bytes, mime="image/png")],
    )

    await provider.execute(run, _noop_progress)

    assert len(fake.uploads) == 1
    assert fake.uploads[0]["image_filename"] == "legacy.png"


# -- 最終プロンプト(PE の出力。ADR-0030 2章) --------------------------------------


def _pe_graph_and_bindings() -> tuple[dict, Bindings]:
    graph = clone(T2I_GRAPH)
    graph["20"] = {
        "class_type": "PreviewAny",
        "inputs": {"source": ["6", 0]},
        "_meta": {"title": "PE の出力"},
    }
    bindings = _t2i_bindings().model_copy(update={"final_prompt": "20"})
    return graph, bindings


def test_finalize_params_records_final_prompt_node(db_session_factory: sessionmaker) -> None:
    graph, bindings = _pe_graph_and_bindings()
    workflow_id = _create_workflow(db_session_factory, template=graph, bindings=bindings)
    provider = ComfyUIProvider("http://127.0.0.1:8188", db_session_factory, 30.0)
    result = _finalize(provider, db_session_factory, workflow_id=workflow_id, params={"seed": 1})
    assert result["comfyui_final_prompt"] == "20"


def test_finalize_params_omits_final_prompt_when_unset(db_session_factory: sessionmaker) -> None:
    workflow_id = _create_workflow(
        db_session_factory, template=clone(T2I_GRAPH), bindings=_t2i_bindings()
    )
    provider = ComfyUIProvider("http://127.0.0.1:8188", db_session_factory, 30.0)
    result = _finalize(provider, db_session_factory, workflow_id=workflow_id, params={"seed": 1})
    assert "comfyui_final_prompt" not in result


async def _execute_with_history(
    db_session_factory: sessionmaker,
    *,
    graph: dict,
    bindings: Bindings,
    outputs: dict,
):
    workflow_id = _create_workflow(db_session_factory, template=graph, bindings=bindings)
    fake = FakeComfyUI()
    fake.set_history("prompt-1", {"outputs": outputs, "status": {"status_str": "success"}})
    fake.add_output_file("out.png", "", "output", make_png_bytes())
    provider = ComfyUIProvider(
        "http://127.0.0.1:8188",
        db_session_factory,
        5.0,
        client_factory=_client_factory(fake, unavailable_ws_connect()),
    )
    params = _finalize(provider, db_session_factory, workflow_id=workflow_id, params={"seed": 1})
    run = RunRequest(
        run_id=uuid.uuid4(), operation="generate", model=str(workflow_id), prompt="p", params=params
    )
    return await provider.execute(run, _noop_progress)


_IMAGE_OUTPUT = {"9": {"images": [{"filename": "out.png", "subfolder": "", "type": "output"}]}}


@run_async
async def test_execute_records_final_prompt_text(db_session_factory: sessionmaker) -> None:
    graph, bindings = _pe_graph_and_bindings()
    result = await _execute_with_history(
        db_session_factory,
        graph=graph,
        bindings=bindings,
        outputs={**_IMAGE_OUTPUT, "20": {"text": ["A warm, ", "cozy room"]}},
    )
    assert len(result.outputs) == 1
    assert result.text_outputs == [
        {
            "role": "final_prompt",
            "node_id": "20",
            "class_type": "PreviewAny",
            "title": "PE の出力",
            "text": "A warm, \ncozy room",
        }
    ]


@run_async
async def test_execute_accepts_single_string_text(db_session_factory: sessionmaker) -> None:
    graph, bindings = _pe_graph_and_bindings()
    del graph["20"]["_meta"]
    result = await _execute_with_history(
        db_session_factory,
        graph=graph,
        bindings=bindings,
        outputs={**_IMAGE_OUTPUT, "20": {"text": "only one"}},
    )
    assert result.text_outputs is not None
    assert result.text_outputs[0]["text"] == "only one"
    assert result.text_outputs[0]["title"] is None


@run_async
async def test_execute_without_text_succeeds_with_no_text_outputs(
    db_session_factory: sessionmaker, caplog: pytest.LogCaptureFixture
) -> None:
    graph, bindings = _pe_graph_and_bindings()
    with caplog.at_level("WARNING", logger="app.providers.comfyui.provider"):
        result = await _execute_with_history(
            db_session_factory, graph=graph, bindings=bindings, outputs=dict(_IMAGE_OUTPUT)
        )
    assert len(result.outputs) == 1
    assert result.text_outputs is None
    assert any("20" in record.getMessage() for record in caplog.records)


@run_async
async def test_execute_truncates_long_final_prompt(db_session_factory: sessionmaker) -> None:
    from app.providers.comfyui.provider import TEXT_OUTPUT_MAX_CHARS

    graph, bindings = _pe_graph_and_bindings()
    long_text = "x" * (TEXT_OUTPUT_MAX_CHARS + 10)
    result = await _execute_with_history(
        db_session_factory,
        graph=graph,
        bindings=bindings,
        outputs={**_IMAGE_OUTPUT, "20": {"text": [long_text]}},
    )
    assert result.text_outputs is not None
    item = result.text_outputs[0]
    assert len(item["text"]) == TEXT_OUTPUT_MAX_CHARS
    assert item["truncated"] is True


@run_async
async def test_execute_ignores_text_when_final_prompt_unset(
    db_session_factory: sessionmaker,
) -> None:
    graph, _ = _pe_graph_and_bindings()
    result = await _execute_with_history(
        db_session_factory,
        graph=graph,
        bindings=_t2i_bindings(),
        outputs={**_IMAGE_OUTPUT, "20": {"text": ["ignored"]}},
    )
    assert result.text_outputs is None


def test_sanitize_text_removes_nul_and_replaces_lone_surrogate() -> None:
    """ADR-0030 2026-10-01 改訂: PostgreSQL の JSONB に書けない NUL は取り除き、対になって
    いないサロゲートは置き換える。ふつうの文字(絵文字を含む)はそのまま。"""
    assert sanitize_text("a\x00b\x00") == "ab"
    assert sanitize_text("x\ud800y") == "x?y"
    assert sanitize_text("猫 \U0001f408 cat") == "猫 \U0001f408 cat"


@pytest.mark.parametrize(
    "text",
    ["", [""], ["", ""], "  \n ", ["\x00"], []],
)
def test_collect_output_texts_returns_none_for_empty(text: object) -> None:
    """ADR-0030 2026-10-01 改訂: 空白を除いて空の最終プロンプトは記録しない。"""
    assert collect_output_texts({"outputs": {"20": {"text": text}}}, "20") is None


def test_collect_output_texts_sanitizes_joined_text() -> None:
    entry = {"outputs": {"20": {"text": ["a\x00b", "c\udc80"]}}}
    assert collect_output_texts(entry, "20") == "ab\nc?"


@run_async
async def test_execute_sanitizes_final_prompt(db_session_factory: sessionmaker) -> None:
    # 偽の ComfyUI は対になっていないサロゲートを JSON にできないので、ここでは NUL だけ
    # 確かめる(サロゲートは `sanitize_text` の単体テストで見る)。タイトルの NUL は、
    # PostgreSQL ではワークフローの登録(JSONB)の時点で拒まれるので SQLite のときだけ入れる。
    graph, bindings = _pe_graph_and_bindings()
    on_sqlite = db_session_factory.kw["bind"].dialect.name == "sqlite"
    if on_sqlite:
        graph["20"]["_meta"]["title"] = "PE\x00 title"
    result = await _execute_with_history(
        db_session_factory,
        graph=graph,
        bindings=bindings,
        outputs={**_IMAGE_OUTPUT, "20": {"text": ["warm\x00 room"]}},
    )
    assert result.text_outputs is not None
    assert result.text_outputs[0]["text"] == "warm room"
    if on_sqlite:
        assert result.text_outputs[0]["title"] == "PE title"


@run_async
async def test_execute_skips_empty_final_prompt(db_session_factory: sessionmaker) -> None:
    graph, bindings = _pe_graph_and_bindings()
    result = await _execute_with_history(
        db_session_factory,
        graph=graph,
        bindings=bindings,
        outputs={**_IMAGE_OUTPUT, "20": {"text": [""]}},
    )
    assert len(result.outputs) == 1
    assert result.text_outputs is None


@run_async
async def test_execute_text_only_still_raises_no_output(db_session_factory: sessionmaker) -> None:
    graph, bindings = _pe_graph_and_bindings()
    with pytest.raises(ProviderError) as exc_info:
        await _execute_with_history(
            db_session_factory,
            graph=graph,
            bindings=bindings,
            outputs={"20": {"text": ["only text"]}},
        )
    assert exc_info.value.code == "comfyuiNoOutput"
