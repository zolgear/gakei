"""POST /api/runs は、キーを必要とするプロバイダーでキー未設定なら Run を作らず断る
(ADR-0012 Decision 4)。実 API は呼ばない: `requires_api_key = True` のスタブ provider を使う。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.domain.api_key import write_file_key
from app.providers import openai_spec
from app.providers.base import ProviderCapabilities, RunOutputImage, RunResult
from tests.conftest import make_png_bytes, swapped_primary_provider, wait_for_run_terminal


class _KeyRequiringProvider:
    """`requires_api_key = True` のスタブ。実 API は一切呼ばない。"""

    name = "openai"
    label = "OpenAI"
    requires_api_key = True
    supports_pricing = True

    def capabilities(self) -> ProviderCapabilities:
        return openai_spec.build_capabilities(self.name, self.label)

    def availability(self) -> tuple[bool, str | None]:
        return True, None

    def finalize_params(self, db, draft) -> dict:  # noqa: ANN001
        return dict(draft.params)

    async def execute(self, run, on_progress) -> RunResult:  # noqa: ANN001
        return RunResult(
            outputs=[RunOutputImage(data=make_png_bytes(), mime="image/png")],
            usage=None,
            provider_request_id="stub-request-id",
        )


def _post_generate_run(client: TestClient, prompt: str = "needs a key"):
    return client.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": "gpt-image-2.5-sunburst",
            "prompt": prompt,
            "params": {"n": 1},
        },
    )


def test_create_run_rejected_when_key_missing(client: TestClient) -> None:
    with swapped_primary_provider(client, _KeyRequiringProvider()):
        before = client.get("/api/runs").json()["items"]

        response = _post_generate_run(client)
        assert response.status_code == 409, response.text
        detail = response.json()["detail"]
        assert "設定" in detail

        after = client.get("/api/runs").json()["items"]
        assert len(after) == len(before)  # Run が作られていない。


def test_create_run_succeeds_once_file_key_is_configured(client: TestClient) -> None:
    with swapped_primary_provider(client, _KeyRequiringProvider()):
        write_file_key(client.app.state.settings.data_dir, "sk-configured-via-ui")

        response = _post_generate_run(client, "now has a key")
        assert response.status_code == 202, response.text
        run_id = response.json()["id"]

        detail = wait_for_run_terminal(client, run_id)
        assert detail["status"] == "succeeded"


def test_create_run_not_rejected_for_provider_without_requires_api_key(
    client: TestClient,
) -> None:
    """既定の fake provider(requires_api_key=False)は、キー未設定でも通常どおり作れる。"""
    response = _post_generate_run(client, "fake provider never needs a key")
    assert response.status_code == 202, response.text
