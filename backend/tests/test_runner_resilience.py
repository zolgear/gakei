"""runner が「1件の Run の失敗」で死なないことの確認(レビュー指摘1)。"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.domain.models import Asset
from app.i18n import t
from app.providers import openai_spec
from app.providers.base import ProviderCapabilities, RunOutputImage, RunResult
from app.worker import runner as runner_module
from tests.conftest import (
    make_png_bytes,
    swapped_primary_provider,
    wait_for_background_reads,
    wait_for_run_terminal,
)


def _create_simple_generate_run(client: TestClient, prompt: str = "simple") -> str:
    response = client.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": "gpt-image-2.5-sunburst",
            "prompt": prompt,
            "params": {"n": 1},
        },
    )
    assert response.status_code == 202, response.text
    return response.json()["id"]


def test_missing_input_file_fails_run_but_runner_keeps_processing(client: TestClient) -> None:
    # 入力画像をアップロードしてから、原本ファイルを消してしまう(壊れた前提条件)。
    data = make_png_bytes(width=128, height=128)
    upload = client.post(
        "/api/assets",
        files={"file": ("base.png", data, "image/png")},
        data={"kind": "upload"},
    )
    assert upload.status_code == 201, upload.text
    asset_id = upload.json()["id"]

    session_factory = client.app.state.session_factory
    store = client.app.state.store
    with session_factory() as session:
        asset = session.get(Asset, uuid.UUID(asset_id))
        blob_path = store.local_path(asset.blob_key, asset.sha256, "original")
    wait_for_background_reads(client)
    blob_path.unlink()

    response = client.post(
        "/api/runs",
        json={
            "operation": "edit",
            "model": "gpt-image-2.5-sunburst",
            "prompt": "edit with missing input file",
            "params": {"n": 1},
            "inputs": [{"asset_id": asset_id, "role": "image", "position": 0}],
        },
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]

    detail = wait_for_run_terminal(client, run_id)
    assert detail["status"] == "failed"
    assert detail["error_code"] == "internalError"
    # Issue #166: 想定外の例外の文言(ファイルのパスなど)は画面に出さない。種類の名前だけ。
    assert str(blob_path.parent) not in detail["error_message"]
    assert asset.sha256 not in detail["error_message"]
    assert "Error" in detail["error_message"]

    # runner が生きていることを、次の(正常な)Runで確認する。
    next_run_id = _create_simple_generate_run(client, "runner is still alive")
    next_detail = wait_for_run_terminal(client, next_run_id)
    assert next_detail["status"] == "succeeded"


class _BrokenProvider:
    """壊れたバイト列を返す provider。ingest 失敗経路を確認するためのテスト専用実装。"""

    name = "fake"
    label = "Fake"
    requires_api_key = False
    supports_pricing = True

    def capabilities(self) -> ProviderCapabilities:
        return openai_spec.build_capabilities(self.name, self.label)

    def availability(self) -> tuple[bool, str | None]:
        return True, None

    def finalize_params(self, db, draft) -> dict:  # noqa: ANN001
        return dict(draft.params)

    async def execute(self, run, on_progress) -> RunResult:  # noqa: ANN001
        return RunResult(
            outputs=[RunOutputImage(data=b"this-is-not-a-real-image", mime="image/png")],
            usage=None,
            provider_request_id="broken-provider-request",
        )


def test_ingest_failure_on_success_path_fails_run_but_runner_keeps_processing(
    client: TestClient,
) -> None:
    with swapped_primary_provider(client, _BrokenProvider()):
        run_id = _create_simple_generate_run(client, "will fail during ingest")
        detail = wait_for_run_terminal(client, run_id)
        assert detail["status"] == "failed"
        assert detail["error_code"] == "internalError"
        assert detail["outputs"] == []
        # 取り込みの検証の文言(固定の i18n)はそのまま見せる。
        assert detail["error_message"] == t("assets.cannotReadImage")

    # runner を元の(正常な)providerに戻した後も、生きていることを確認する。
    next_run_id = _create_simple_generate_run(client, "runner is still alive after ingest failure")
    next_detail = wait_for_run_terminal(client, next_run_id)
    assert next_detail["status"] == "succeeded"


def _db_locked() -> OperationalError:
    return OperationalError("UPDATE run", {}, Exception("database is locked"))


def test_failed_state_is_retried_until_recorded_when_db_is_busy(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """failed の記録が DB の都合で失敗しても、やり直して記録する(記録しないまま次へ進むと、
    Run が running のまま残り、再起動するまで画面で「実行中」のままになる)。"""
    original = runner_module._finish_run_failed
    calls = 0

    def flaky(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        nonlocal calls
        calls += 1
        if calls <= 2:
            raise _db_locked()
        return original(*args, **kwargs)

    monkeypatch.setattr(runner_module, "_finish_run_failed", flaky)
    monkeypatch.setattr(runner_module, "_RECORD_RETRY_DELAYS_SECONDS", (0.01,))

    with swapped_primary_provider(client, _BrokenProvider()):
        run_id = _create_simple_generate_run(client, "db is busy while recording the failure")
        detail = wait_for_run_terminal(client, run_id)
    assert detail["status"] == "failed"
    assert detail["error_message"] == t("assets.cannotReadImage")
    assert calls == 3


def test_unexpected_error_while_recording_failure_does_not_block_the_lane(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DB の都合ではない失敗はやり直さない(同じ失敗を繰り返してレーンを止めないため)。"""
    original = runner_module._finish_run_failed
    calls = 0

    def broken_once(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("unexpected")
        return original(*args, **kwargs)

    monkeypatch.setattr(runner_module, "_finish_run_failed", broken_once)

    with swapped_primary_provider(client, _BrokenProvider()):
        run_id = _create_simple_generate_run(client, "recording the failure breaks")
        # 次の Run は失敗として記録される(レーンは止まっていない)。
        next_run_id = _create_simple_generate_run(client, "the lane keeps going")
        next_detail = wait_for_run_terminal(client, next_run_id)
    assert next_detail["status"] == "failed"
    assert calls == 2
    assert client.get(f"/api/runs/{run_id}").json()["status"] == "running"
