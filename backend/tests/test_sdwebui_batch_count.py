"""SD WebUI のバッチ回数(`n_iter`。ADR-0038 2章)のテスト。偽の WebUI だけを使う。

WebUI は `batch_size`(GAKEI の枚数 `n`)枚を1回として `n_iter` 回くり返す。1つの Run の出力は
最大 `n × n_iter` 枚(上限 64)。組み合わせ生成(Dynamic Prompts)ではバッチ回数を使わない。
"""

from __future__ import annotations

import asyncio
import io
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.orm import sessionmaker

from app.domain.run_validation import RunValidationError
from app.providers.base import RunDraft, RunRequest
from app.providers.sdwebui.client import SdWebuiClient
from app.providers.sdwebui.import_params import build_form_values
from app.providers.sdwebui.provider import MAX_TOTAL_OUTPUTS, N_ITER_MAX, SdWebuiProvider
from tests.conftest import wait_for_run_terminal
from tests.sdwebui_fake import FakeSdWebui, install_fake_factories
from tests.test_sdwebui_import_params import meta_of

URL = "http://127.0.0.1:7860"


def _provider(fake: FakeSdWebui, session_factory: sessionmaker) -> SdWebuiProvider:
    return SdWebuiProvider(
        URL,
        session_factory,
        30.0,
        credentials_loader=lambda: None,
        transport=fake.transport(),
        progress_interval=0.01,
    )


def _draft(prompt: str = "a cat", **params: Any) -> RunDraft:
    return RunDraft(operation="generate", model="model-a", prompt=prompt, params=params, inputs=[])


def _request(params: dict[str, Any], prompt: str = "a cat") -> RunRequest:
    return RunRequest(
        run_id=uuid.uuid4(), operation="generate", model="model-a", prompt=prompt, params=params
    )


async def _noop(_event: Any) -> None:
    return None


def _finalize(provider: SdWebuiProvider, session_factory: sessionmaker, draft: RunDraft):
    with session_factory() as db:
        return provider.finalize_params(db, draft)


# -- capabilities --------------------------------------------------------------------


def test_capabilities_have_batch_count_after_count(db_session_factory: sessionmaker) -> None:
    caps = _provider(FakeSdWebui(), db_session_factory).capabilities()
    for operation in caps.models[0].operations:
        names = [p.name for p in operation.params]
        assert names.index("n_iter") == names.index("n") + 1, operation.operation
        n_iter = next(p for p in operation.params if p.name == "n_iter")
        assert (n_iter.type, n_iter.minimum, n_iter.maximum) == ("int", 1, N_ITER_MAX)
        assert (n_iter.default, n_iter.form_default) == (1, 1)
        assert n_iter.label == "バッチ回数"


# -- finalize_params -----------------------------------------------------------------


def test_finalize_sends_n_iter(db_session_factory: sessionmaker) -> None:
    provider = _provider(FakeSdWebui(), db_session_factory)
    params = _finalize(provider, db_session_factory, _draft(n=2, n_iter=3))
    request = params["sdwebui_request"]
    assert (request["batch_size"], request["n_iter"]) == (2, 3)
    assert params["n_iter"] == 3
    # 指定が無ければ 1
    default = _finalize(provider, db_session_factory, _draft())
    assert default["sdwebui_request"]["n_iter"] == 1


def test_finalize_allows_exactly_the_limit(db_session_factory: sessionmaker) -> None:
    provider = _provider(FakeSdWebui(), db_session_factory)
    params = _finalize(provider, db_session_factory, _draft(n=8, n_iter=8))
    assert params["sdwebui_request"]["batch_size"] * params["sdwebui_request"]["n_iter"] == 64


def test_finalize_rejects_too_many_outputs(db_session_factory: sessionmaker) -> None:
    provider = _provider(FakeSdWebui(), db_session_factory)
    with db_session_factory() as db, pytest.raises(RunValidationError) as exc:
        provider.finalize_params(db, _draft(n=5, n_iter=13))
    assert str(MAX_TOTAL_OUTPUTS) in str(exc.value)


def test_combinatorial_sends_n_iter_1(db_session_factory: sessionmaker) -> None:
    """組み合わせ生成では WebUI がバッチ回数を使わないので、1 で送る(ADR-0038 7章)。"""
    fake = FakeSdWebui()
    fake.enable_dynamic_prompts()
    provider = _provider(fake, db_session_factory)
    params = _finalize(
        provider,
        db_session_factory,
        _draft("a {red|blue|green} cat", n_iter=16, n=8, dynamic_prompts_combinatorial=True),
    )
    assert params["sdwebui_request"]["n_iter"] == 1
    result = asyncio.run(provider.execute(_request(params, "a {red|blue|green} cat"), _noop))
    assert len(result.outputs) == 3


# -- execute ---------------------------------------------------------------------------


def test_execute_takes_batch_size_times_n_iter(db_session_factory: sessionmaker) -> None:
    fake = FakeSdWebui()
    fake.return_grid = True  # バッチ回数が 2 以上だと先頭にグリッドが付きやすい
    fake.extra_images = 1  # 末尾の補助の画像は捨てる
    provider = _provider(fake, db_session_factory)
    params = _finalize(provider, db_session_factory, _draft(seed=7, n=2, n_iter=3))

    result = asyncio.run(provider.execute(_request(params), _noop))

    assert len(result.outputs) == 6
    # グリッド(16x16)は取り込まない
    sizes = {Image.open(io.BytesIO(o.data)).size for o in result.outputs}
    assert sizes == {(8, 8)}
    usage = result.usage or {}
    assert usage["all_seeds"] == [7, 8, 9, 10, 11, 12]
    assert len(usage["all_prompts"]) == 6
    # infotext はグリッドではなく1枚目のもの
    assert "Seed: 7" in usage["infotext"]


def test_execute_prompts_follow_output_order(db_session_factory: sessionmaker) -> None:
    """展開後のプロンプトと seed が出力の順に合う(Dynamic Prompts を1枚ずつ展開)。"""
    fake = FakeSdWebui()
    fake.enable_dynamic_prompts()
    fake.return_grid = True
    provider = _provider(fake, db_session_factory)
    prompt = "a {red|blue|green} cat"
    params = _finalize(provider, db_session_factory, _draft(prompt, seed=100, n=2, n_iter=2))

    result = asyncio.run(provider.execute(_request(params, prompt), _noop))

    assert len(result.outputs) == 4
    usage = result.usage or {}
    assert usage["all_seeds"] == [100, 101, 102, 103]
    assert usage["all_prompts"] == ["a red cat", "a blue cat", "a green cat", "a red cat"]
    final_prompts = [
        (item["output_index"], item["text"])
        for item in result.text_outputs or []
        if item["role"] == "final_prompt"
    ]
    assert final_prompts == [
        (0, "a red cat"),
        (1, "a blue cat"),
        (2, "a green cat"),
        (3, "a red cat"),
    ]


# -- POST /api/runs ------------------------------------------------------------------


def _connect(client: TestClient, monkeypatch: pytest.MonkeyPatch, fake: FakeSdWebui) -> None:
    install_fake_factories(monkeypatch, fake)
    response = client.put("/api/sdwebui/connection", json={"url": URL})
    assert response.status_code == 200, response.text


def _post_run(client: TestClient, **params: Any):  # noqa: ANN202
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


def test_run_with_batch_count(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeSdWebui()
    _connect(client, monkeypatch, fake)
    response = _post_run(client, n=2, n_iter=2, seed=5)
    assert response.status_code == 202, response.text
    detail = wait_for_run_terminal(client, response.json()["id"])
    assert detail["status"] == "succeeded", detail
    assert [o["output_index"] for o in detail["outputs"]] == [0, 1, 2, 3]
    assert detail["usage"]["all_seeds"] == [5, 6, 7, 8]
    assert detail["params"]["n_iter"] == 2


@pytest.mark.parametrize(("n", "n_iter"), [(8, 9), (1, N_ITER_MAX + 1), (1, 0)])
def test_run_rejects_invalid_batch_count(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, n: int, n_iter: int
) -> None:
    fake = FakeSdWebui()
    _connect(client, monkeypatch, fake)
    response = _post_run(client, n=n, n_iter=n_iter)
    assert response.status_code == 422, response.text
    assert fake.txt2img_bodies == []


# -- 画像の生成情報の読み込み(ADR-0038 9章) ---------------------------------------------


def _catalog():  # noqa: ANN202
    return SdWebuiClient(URL, transport=FakeSdWebui().transport()).fetch_catalog()


def test_import_reads_batch_size_and_count() -> None:
    text = "p\nSteps: 20, Seed: 1, Batch size: 4, Batch pos: 2, Batch count: 3, Model: model-a"
    result = build_form_values(meta_of(text), _catalog())
    assert result.params["n"] == 4
    assert result.params["n_iter"] == 3
    # Batch pos はフォームの値ではないので「読み込めなかった項目」に並べない
    assert result.unapplied == []


def test_import_without_batch_keys_leaves_them_out() -> None:
    result = build_form_values(meta_of("p\nSteps: 20, Seed: 1, Model: model-a"), _catalog())
    assert "n" not in result.params
    assert "n_iter" not in result.params


def test_import_out_of_range_batch_count_is_noted() -> None:
    text = f"p\nSteps: 20, Seed: 1, Batch count: {N_ITER_MAX + 1}, Model: model-a"
    result = build_form_values(meta_of(text), _catalog())
    assert "n_iter" not in result.params
    assert ("Batch count", str(N_ITER_MAX + 1)) in result.unapplied
