"""起動時に running のままの Run を failed/interrupted にすること(ADR-0008)。"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.domain.models import Run, RunStatus


def test_running_run_becomes_failed_interrupted_on_restart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("FAKE_PROVIDER", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    from app.worker.runner import Runner

    async def _noop_start(self: Runner) -> int:  # noqa: ANN001
        return 0

    # 1回目の起動: マイグレーションだけ済ませ、running な Run を直接作る(runner は動かさない)。
    monkeypatch.setattr(Runner, "start", _noop_start)
    from app.main import create_app

    app1 = create_app()
    with TestClient(app1) as client1:
        response = client1.post(
            "/api/runs",
            json={
                "operation": "generate",
                "model": "gpt-image-2.5-sunburst",
                "prompt": "will be interrupted",
                "params": {"n": 1},
            },
        )
        run_id = response.json()["id"]

        session_factory = client1.app.state.session_factory
        with session_factory() as session:
            run = session.get(Run, uuid.UUID(run_id))
            run.status = RunStatus.RUNNING
            session.commit()

    # 2回目の起動: 本物の Runner.start() を使い、reset_interrupted_runs が働くことを確認する。
    monkeypatch.undo()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("FAKE_PROVIDER", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    app2 = create_app()
    with TestClient(app2) as client2:
        detail = client2.get(f"/api/runs/{run_id}").json()
        assert detail["status"] == "failed"
        assert detail["error_code"] == "interrupted"


def test_unregistered_provider_queued_run_becomes_failed_on_restart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ADR-0013: 起動時、登録されていない provider の queued な Run は
    failed + providerUnavailable になる(例: 前回投入時は有効だった provider を、
    設定変更や無効化のあとの再起動で見つけられなくなったケース)。"""
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("FAKE_PROVIDER", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    from app.worker.runner import Runner

    async def _noop_start(self: Runner) -> int:  # noqa: ANN001
        return 0

    monkeypatch.setattr(Runner, "start", _noop_start)
    from app.main import create_app

    app1 = create_app()
    with TestClient(app1) as client1:
        response = client1.post(
            "/api/runs",
            json={
                "operation": "generate",
                "model": "gpt-image-2.5-sunburst",
                "prompt": "will lose its provider",
                "params": {"n": 1},
            },
        )
        run_id = response.json()["id"]

        session_factory = client1.app.state.session_factory
        with session_factory() as session:
            run = session.get(Run, uuid.UUID(run_id))
            run.provider = "ghost-provider"  # もう登録されていない provider を装う
            session.commit()

    monkeypatch.undo()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("FAKE_PROVIDER", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    app2 = create_app()
    with TestClient(app2) as client2:
        detail = client2.get(f"/api/runs/{run_id}").json()
        assert detail["status"] == "failed"
        assert detail["error_code"] == "providerUnavailable"
