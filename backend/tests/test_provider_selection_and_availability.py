"""ADR-0013: `POST /api/runs` の provider 解決(省略時は主プロバイダー、未知は 422)と、
`availability()` が False のプロバイダーへの Run 作成(409、Run 行を作らない)。
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.domain.models import Run
from app.providers import openai_spec
from app.providers.base import ProviderCapabilities
from tests.conftest import swapped_primary_provider, wait_for_run_terminal


def _post_generate_run(client: TestClient, *, provider: str | None = None, prompt: str = "p"):
    body: dict = {
        "operation": "generate",
        "model": "gpt-image-2.5-sunburst",
        "prompt": prompt,
        "params": {"n": 1},
    }
    if provider is not None:
        body["provider"] = provider
    return client.post("/api/runs", json=body)


def test_provider_omitted_uses_primary_provider(client: TestClient) -> None:
    response = _post_generate_run(client, prompt="no provider specified")
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]

    detail = wait_for_run_terminal(client, run_id)
    assert detail["status"] == "succeeded"

    with client.app.state.session_factory() as session:
        row = session.get(Run, uuid.UUID(run_id))
        assert row is not None
        assert row.provider == client.app.state.registry.primary == "fake"


def test_unknown_provider_returns_422_and_creates_no_run(client: TestClient) -> None:
    before = client.get("/api/runs").json()["items"]

    response = _post_generate_run(client, provider="no-such-provider", prompt="unknown provider")
    assert response.status_code == 422, response.text

    after = client.get("/api/runs").json()["items"]
    assert len(after) == len(before)


class _UnavailableProvider:
    """`availability()` が常に False を返すスタブ。実行されないことを確認するのが目的。"""

    name = "fake"
    label = "Fake"
    requires_api_key = False
    supports_pricing = True

    def capabilities(self) -> ProviderCapabilities:
        return openai_spec.build_capabilities(self.name, self.label)

    def availability(self) -> tuple[bool, str | None]:
        return False, "テスト用に利用不可にしてある"

    def finalize_params(self, db, draft) -> dict:  # noqa: ANN001
        raise AssertionError("availability=False のときは finalize_params まで到達しないはず")

    async def execute(self, run, on_progress):  # noqa: ANN001
        raise AssertionError("availability=False のときは execute まで到達しないはず")


def test_unavailable_provider_returns_409_and_creates_no_run(client: TestClient) -> None:
    before = client.get("/api/runs").json()["items"]

    with swapped_primary_provider(client, _UnavailableProvider()):
        response = _post_generate_run(client, prompt="provider unavailable")
        assert response.status_code == 409, response.text
        assert "テスト用に利用不可にしてある" in response.json()["detail"]

    after = client.get("/api/runs").json()["items"]
    assert len(after) == len(before)
