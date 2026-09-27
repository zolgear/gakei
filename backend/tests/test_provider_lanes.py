"""ADR-0013: プロバイダーごとに1レーン(直列)。異なるプロバイダーの Run は並行に進む。

2つ目のプロバイダーは、このテストで `app.main.build_registry` を差し替えて注入する
(実物の ComfyUI は使わない、実 API も呼ばない)。
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.providers import openai_spec
from app.providers.base import ProviderCapabilities, RunOutputImage, RunResult
from tests.conftest import make_png_bytes, wait_for_run_terminal


class _StubLaneProvider:
    """`execute` が完了するまでの時間をテストから制御できるスタブ。"""

    def __init__(self, name: str, *, gate: threading.Event | None = None) -> None:
        self.name = name
        self.label = name
        self.requires_api_key = False
        self.supports_pricing = False
        self._gate = gate

    def capabilities(self) -> ProviderCapabilities:
        return openai_spec.build_capabilities(self.name, self.label)

    def availability(self) -> tuple[bool, str | None]:
        return True, None

    def finalize_params(self, db, draft) -> dict:  # noqa: ANN001
        return dict(draft.params)

    async def execute(self, run, on_progress) -> RunResult:  # noqa: ANN001
        if self._gate is not None:
            # asyncio.to_thread でブロッキング待ちを別スレッドに逃がす。
            # イベントループ自体は止めないので、他レーンのタスクは進み続けられる。
            await asyncio.to_thread(self._gate.wait)
        return RunResult(outputs=[RunOutputImage(data=make_png_bytes(), mime="image/png")])


def test_two_provider_lanes_run_concurrently(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("FAKE_PROVIDER", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    import app.main as app_main
    from app.providers.registry import build_registry as real_build_registry

    slow_gate = threading.Event()

    def fake_build_registry(settings, session_factory):  # noqa: ANN001
        registry = real_build_registry(settings, session_factory)
        registry.providers["fast"] = _StubLaneProvider("fast")
        registry.providers["slow"] = _StubLaneProvider("slow", gate=slow_gate)
        return registry

    monkeypatch.setattr(app_main, "build_registry", fake_build_registry)

    from app.main import create_app

    app = create_app()
    with TestClient(app) as client:
        slow_response = client.post(
            "/api/runs",
            json={
                "operation": "generate",
                "model": "gpt-image-2.5-sunburst",
                "prompt": "slow lane",
                "provider": "slow",
                "params": {"n": 1},
            },
        )
        assert slow_response.status_code == 202, slow_response.text
        slow_id = slow_response.json()["id"]

        fast_response = client.post(
            "/api/runs",
            json={
                "operation": "generate",
                "model": "gpt-image-2.5-sunburst",
                "prompt": "fast lane",
                "provider": "fast",
                "params": {"n": 1},
            },
        )
        assert fast_response.status_code == 202, fast_response.text
        fast_id = fast_response.json()["id"]

        # 遅いレーン(slow)が gate で止まっている間に、別レーン(fast)は先に終わる。
        fast_detail = wait_for_run_terminal(client, fast_id, timeout=5.0)
        assert fast_detail["status"] == "succeeded"

        slow_detail = client.get(f"/api/runs/{slow_id}").json()
        assert slow_detail["status"] in ("queued", "running")

        slow_gate.set()
        slow_final = wait_for_run_terminal(client, slow_id, timeout=5.0)
        assert slow_final["status"] == "succeeded"
