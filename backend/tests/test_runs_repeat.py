"""繰り返し回数(ADR-0042)のテスト。`POST /api/runs` の `repeat`。

同じ設定の Run を `repeat` 個まとめて積むこと、検証と作成が全部か無しかであること、seed の
進め方(SD WebUI は 枚数 × バッチ回数、ComfyUI は 1、指定なしならそれぞれ、上限で 0 に戻る)、
応答の形、runner への通知が1回であることを確かめる。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.domain.models import Run
from app.providers.comfyui.provider import ComfyUIProvider
from app.providers.fake import FakeProvider
from app.providers.sdwebui.provider import SEED_MAX as SDWEBUI_SEED_MAX
from app.providers.sdwebui.provider import SdWebuiProvider
from tests.comfyui_fake import FakeComfyUI
from tests.conftest import wait_for_run_terminal
from tests.sdwebui_fake import FakeSdWebui, install_fake_factories
from tests.test_comfyui_runs_integration import (  # noqa: F401  (フィクスチャの取り込み)
    _configure_success,
    _create_t2i_workflow,
    _install_fake_comfyui,
    _mark_available,
    client_with_comfyui,
)


def _post_fake(client: TestClient, **extra) -> object:
    body = {"operation": "generate", "model": "gpt-image-2", "prompt": "a cat", "params": {}}
    body.update(extra)
    return client.post("/api/runs", json=body)


def _run_count(client: TestClient) -> int:
    with client.app.state.session_factory() as db:
        return db.execute(select(func.count()).select_from(Run)).scalar_one()


def _fake_model(client: TestClient) -> str:
    caps = client.get("/api/capabilities").json()
    entry = next(p for p in caps["providers"] if p["provider"] == "fake")
    return entry["models"][0]["model"]


# -- まとめて作る ---------------------------------------------------------------------


def test_repeat_creates_runs_in_order_and_response_shape(client_no_runner: TestClient) -> None:
    model = _fake_model(client_no_runner)
    response = _post_fake(client_no_runner, model=model, repeat=3)
    assert response.status_code == 202, response.text
    body = response.json()
    assert len(body["runs"]) == 3
    # 既存の形(1件目の id と status)を保つ
    assert body["id"] == body["runs"][0]["id"]
    assert body["status"] == "queued"
    assert all(r["status"] == "queued" for r in body["runs"])
    assert len({r["id"] for r in body["runs"]}) == 3

    # 積んだ順に queued_at が並ぶ(runner も履歴もこの順)
    with client_no_runner.app.state.session_factory() as db:
        rows = db.execute(select(Run).order_by(Run.queued_at.asc(), Run.id.asc())).scalars().all()
        assert [str(r.id) for r in rows] == [r["id"] for r in body["runs"]]
        # 繰り返し回数は run.params に入れない(ADR-0003 ルール4)
        assert all("repeat" not in r.params for r in rows)


def test_repeat_defaults_to_one(client_no_runner: TestClient) -> None:
    model = _fake_model(client_no_runner)
    response = _post_fake(client_no_runner, model=model)
    assert response.status_code == 202, response.text
    body = response.json()
    assert [r["id"] for r in body["runs"]] == [body["id"]]
    assert _run_count(client_no_runner) == 1


@pytest.mark.parametrize("repeat", [0, 21, -1])
def test_repeat_out_of_range_is_422(client_no_runner: TestClient, repeat: int) -> None:
    model = _fake_model(client_no_runner)
    response = _post_fake(client_no_runner, model=model, repeat=repeat)
    assert response.status_code == 422, response.text
    assert _run_count(client_no_runner) == 0


def test_repeat_twenty_is_allowed(client_no_runner: TestClient) -> None:
    model = _fake_model(client_no_runner)
    response = _post_fake(client_no_runner, model=model, repeat=20)
    assert response.status_code == 202, response.text
    assert len(response.json()["runs"]) == 20
    assert _run_count(client_no_runner) == 20


def test_repeat_creates_nothing_when_any_finalize_fails(
    client_no_runner: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.domain.run_validation import RunValidationError

    original = FakeProvider.finalize_params
    calls: list[int] = []

    def failing_on_third(self, db, draft):  # noqa: ANN001, ANN202
        calls.append(1)
        if len(calls) == 3:
            raise RunValidationError("3つ目で断る")
        return original(self, db, draft)

    monkeypatch.setattr(FakeProvider, "finalize_params", failing_on_third)
    model = _fake_model(client_no_runner)
    response = _post_fake(client_no_runner, model=model, repeat=5)
    assert response.status_code == 422, response.text
    assert "3つ目で断る" in response.text
    assert _run_count(client_no_runner) == 0


def test_repeat_notifies_runner_once(
    client_no_runner: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = client_no_runner.app.state.runner
    count: list[int] = []
    monkeypatch.setattr(runner, "notify", lambda: count.append(1))
    model = _fake_model(client_no_runner)
    response = _post_fake(client_no_runner, model=model, repeat=4)
    assert response.status_code == 202, response.text
    assert count == [1]


def test_repeated_queued_runs_can_be_canceled_individually(client_no_runner: TestClient) -> None:
    model = _fake_model(client_no_runner)
    runs = _post_fake(client_no_runner, model=model, repeat=3).json()["runs"]
    for r in runs[1:]:
        response = client_no_runner.post(f"/api/runs/{r['id']}/cancel")
        assert response.status_code == 200, response.text
    statuses = [client_no_runner.get(f"/api/runs/{r['id']}").json()["status"] for r in runs]
    assert statuses == ["queued", "canceled", "canceled"]


# -- seed: SD WebUI -----------------------------------------------------------------------


def _connect_sdwebui(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, fake: FakeSdWebui
) -> None:
    install_fake_factories(monkeypatch, fake)
    response = client.put("/api/sdwebui/connection", json={"url": "http://127.0.0.1:7860"})
    assert response.status_code == 200, response.text


def _post_sdwebui(client: TestClient, repeat: int, **params) -> list[dict]:
    response = client.post(
        "/api/runs",
        json={
            "operation": "generate",
            "provider": "sdwebui",
            "model": "model-a",
            "prompt": "a cat",
            "params": params,
            "repeat": repeat,
        },
    )
    assert response.status_code == 202, response.text
    return [wait_for_run_terminal(client, r["id"]) for r in response.json()["runs"]]


def test_sdwebui_fixed_seed_advances_by_batch_total(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeSdWebui()
    _connect_sdwebui(client, monkeypatch, fake)
    details = _post_sdwebui(client, 3, n=2, n_iter=3, seed=10)
    # 1回で 2 × 3 = 6 枚、seed は1枚ごとに 1 増えるので、次の Run は 6 先から
    assert [d["params"]["sdwebui_seed"] for d in details] == [10, 16, 22]
    assert [d["params"]["sdwebui_request"]["seed"] for d in details] == [10, 16, 22]
    # 利用者が指定した seed の欄も、各 Run が使った値になる
    assert [d["params"]["seed"] for d in details] == [10, 16, 22]
    # task_id は Run ごとに別
    assert len({d["params"]["sdwebui_task_id"] for d in details}) == 3
    assert all("repeat" not in d["params"] for d in details)


def test_sdwebui_fixed_seed_wraps_to_zero(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeSdWebui()
    _connect_sdwebui(client, monkeypatch, fake)
    details = _post_sdwebui(client, 3, n=2, seed=SDWEBUI_SEED_MAX - 1)
    assert [d["params"]["sdwebui_seed"] for d in details] == [SDWEBUI_SEED_MAX - 1, 0, 2]


def test_sdwebui_random_seed_is_decided_per_run(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeSdWebui()
    _connect_sdwebui(client, monkeypatch, fake)
    details = _post_sdwebui(client, 3)
    seeds = [d["params"]["sdwebui_seed"] for d in details]
    assert all(0 <= s <= SDWEBUI_SEED_MAX for s in seeds)
    # 指定が無ければ seed の欄は記録に足さない(今と同じ)
    assert all("seed" not in d["params"] for d in details)
    assert len(set(seeds)) == 3  # 2^32 の範囲から3つ選んで重なる確率は無視できる


def test_sdwebui_repeat_seed_uses_sent_values() -> None:
    first = {"sdwebui_seed": 100, "sdwebui_request": {"batch_size": 4, "n_iter": 2}}
    # self を使わないので、インスタンスを作らずに呼ぶ
    assert SdWebuiProvider.repeat_seed(None, first, 0) == 100  # type: ignore[arg-type]
    assert SdWebuiProvider.repeat_seed(None, first, 2) == 116  # type: ignore[arg-type]


# -- seed: ComfyUI ------------------------------------------------------------------------


def test_comfyui_fixed_seed_advances_by_one(
    client_with_comfyui: TestClient,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mark_available(monkeypatch)
    workflow = _create_t2i_workflow(client_with_comfyui)
    fake = FakeComfyUI()
    _configure_success(fake)
    _install_fake_comfyui(client_with_comfyui, fake)
    response = client_with_comfyui.post(
        "/api/runs",
        json={
            "operation": "generate",
            "provider": "comfyui",
            "model": workflow["id"],
            "prompt": "a cat",
            "params": {"seed": 5},
            "repeat": 3,
        },
    )
    assert response.status_code == 202, response.text
    details = [wait_for_run_terminal(client_with_comfyui, r["id"]) for r in response.json()["runs"]]
    assert [d["params"]["comfyui_seed"] for d in details] == [5, 6, 7]
    # ワークフローのグラフにも各 Run の seed が入る
    assert [d["params"]["comfyui_prompt"]["3"]["inputs"]["seed"] for d in details] == [5, 6, 7]


def test_comfyui_repeat_seed_wraps_to_zero() -> None:
    from app.domain.comfy_workflow import SEED_MAX

    first = {"comfyui_seed": SEED_MAX}
    assert ComfyUIProvider.repeat_seed(None, first, 1) == 0  # type: ignore[arg-type]
    assert ComfyUIProvider.repeat_seed(None, first, 3) == 2  # type: ignore[arg-type]


def test_openai_and_fake_have_no_repeat_seed() -> None:
    from app.providers.openai_images import OpenAIImagesProvider

    assert OpenAIImagesProvider.repeat_seed(None, {}, 1) is None  # type: ignore[arg-type]
    assert FakeProvider.repeat_seed(None, {}, 1) is None  # type: ignore[arg-type]


# -- MCP には足さない ----------------------------------------------------------------------


def test_create_run_for_mcp_ignores_repeat(client_no_runner: TestClient) -> None:
    """MCP が使う `create_run` は、本文の repeat に関わらず1つだけ作る(ADR-0042 3章)。"""
    from app.auth.identity import LOCAL_ADMIN
    from app.domain import run_create
    from app.domain.schemas import RunCreateRequest

    app = client_no_runner.app
    model = _fake_model(client_no_runner)
    body = RunCreateRequest(operation="generate", model=model, prompt="a cat", repeat=5)
    with app.state.session_factory() as db:
        run = run_create.create_run(
            db, app.state.registry, app.state.runner, app.state.settings, body, viewer=LOCAL_ADMIN
        )
        assert run.id is not None
    assert _run_count(client_no_runner) == 1
