"""既存の runner(worker/runner.py)と、モックした OpenAIImagesProvider を組み合わせた統合テスト。

実 API は呼ばない(httpx2.MockTransport)。`OpenAIImagesProvider` が `ImageProvider` プロトコル
どおりに動き、runner がそれを使って Run を succeeded にし、Asset を作れることを確認する。
"""

from __future__ import annotations

import httpx2
from fastapi.testclient import TestClient

from app.domain.api_key import write_file_key
from app.providers.openai_images import OpenAIImagesProvider
from tests.conftest import make_png_bytes, swapped_primary_provider, wait_for_run_terminal
from tests.openai_mock import generation_response_body, json_response, make_b64, make_client


def test_runner_succeeds_with_mocked_openai_provider(client: TestClient) -> None:
    output_png_bytes = make_png_bytes(width=64, height=64, color=(10, 20, 30))

    def handler(request: httpx2.Request) -> httpx2.Response:
        return json_response(
            generation_response_body(b64=make_b64(output_png_bytes)),
            headers={"x-request-id": "req_integration_1"},
        )

    mock_openai_client = make_client(handler)
    provider = OpenAIImagesProvider(client=mock_openai_client)

    # provider には client を注入済み(実 API は呼ばない)だが、POST /api/runs は
    # provider.requires_api_key を見てキーの有無を事前チェックする(ADR-0012)。
    # 注入済みクライアントは使われないダミーのキーで足りる。
    write_file_key(client.app.state.settings.data_dir, "sk-unused-injected-client")

    with swapped_primary_provider(client, provider):
        response = client.post(
            "/api/runs",
            json={
                "operation": "generate",
                "model": "gpt-image-2.5-sunburst",
                "prompt": "openai provider integration test",
                "params": {"n": 1, "size": "1024x1024"},
            },
        )
        assert response.status_code == 202, response.text
        run_id = response.json()["id"]

        detail = wait_for_run_terminal(client, run_id)
        assert detail["status"] == "succeeded"
        assert len(detail["outputs"]) == 1
        assert detail["usage"] is not None
        assert detail["provider_request_id"] == "req_integration_1"

        # 出力 Asset が実際に保存され、配信できること。
        output_asset_id = detail["outputs"][0]["asset_id"]
        content = client.get(
            f"/api/assets/{output_asset_id}/content", params={"variant": "original"}
        )
        assert content.status_code == 200
        assert content.content == output_png_bytes
