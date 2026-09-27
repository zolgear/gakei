"""失敗した Run も証跡として残ることの確認。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import wait_for_run_terminal


def test_content_filter_rejection_is_recorded_as_failed(client: TestClient) -> None:
    response = client.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": "gpt-image-2.5-sunburst",
            "prompt": "[[fail:contentFilter]] 何か危険なプロンプト",
            "params": {"n": 1},
        },
    )
    assert response.status_code == 202
    run_id = response.json()["id"]

    detail = wait_for_run_terminal(client, run_id)
    assert detail["status"] == "failed"
    assert detail["error_code"] == "contentFilter"
    assert detail["error_message"]
    assert detail["outputs"] == []

    # 一覧からも失敗が見えること
    listed = client.get("/api/runs").json()
    matched = next(item for item in listed["items"] if item["id"] == run_id)
    assert matched["status"] == "failed"
    assert matched["error_code"] == "contentFilter"
