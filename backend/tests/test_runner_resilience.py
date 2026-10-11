"""runner が「1件の Run の失敗」で死なないことの確認(レビュー指摘1)。"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.domain.models import Asset
from app.providers import openai_spec
from app.providers.base import ProviderCapabilities, RunOutputImage, RunResult
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

    # runner を元の(正常な)providerに戻した後も、生きていることを確認する。
    next_run_id = _create_simple_generate_run(client, "runner is still alive after ingest failure")
    next_detail = wait_for_run_terminal(client, next_run_id)
    assert next_detail["status"] == "succeeded"
