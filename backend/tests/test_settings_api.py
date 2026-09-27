"""`/api/settings/openai-key`(ADR-0012 Decision 4)。

有効性確認(`get_key_validator`)は Depends 経由で差し替え、実 API は一切呼ばない。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import httpx
import openai
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api import settings as settings_api
from app.domain.api_key import read_file_key, write_file_key


@pytest.fixture
def client_fake(monkeypatch: pytest.MonkeyPatch, data_dir: Path) -> Iterator[TestClient]:
    """FAKE_PROVIDER=1(required=False になる側)。"""
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("FAKE_PROVIDER", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    from app.worker.runner import Runner

    async def _noop_start(self: Runner) -> int:  # noqa: ANN001
        return 0

    monkeypatch.setattr(Runner, "start", _noop_start)

    from app.main import create_app

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def client_openai_no_key(monkeypatch: pytest.MonkeyPatch, data_dir: Path) -> Iterator[TestClient]:
    """既定(openai)、キー未設定(サーバーはキーが無くても起動できることの確認も兼ねる)。"""
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    from app.worker.runner import Runner

    async def _noop_start(self: Runner) -> int:  # noqa: ANN001
        return 0

    monkeypatch.setattr(Runner, "start", _noop_start)

    from app.main import create_app

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def client_openai_env_key(monkeypatch: pytest.MonkeyPatch, data_dir: Path) -> Iterator[TestClient]:
    """既定(openai)、環境変数にキーあり(source=env、画面から変更できない側)。"""
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env-1234")

    from app.worker.runner import Runner

    async def _noop_start(self: Runner) -> int:  # noqa: ANN001
        return 0

    monkeypatch.setattr(Runner, "start", _noop_start)

    from app.main import create_app

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


async def _validator_ok(_api_key: str, _base_url: str | None) -> None:
    return None


async def _validator_invalid(_api_key: str, _base_url: str | None) -> None:
    raise HTTPException(status_code=400, detail="キーが無効です")


async def _validator_network_error(_api_key: str, _base_url: str | None) -> None:
    raise HTTPException(status_code=502, detail="OpenAI への確認に失敗しました: connection refused")


# -- GET ---------------------------------------------------------------


def test_get_status_fake_provider_not_required(client_fake: TestClient) -> None:
    response = client_fake.get("/api/settings/openai-key")
    assert response.status_code == 200
    body = response.json()
    assert body == {"required": False, "configured": False, "source": None, "hint": None}


def test_get_status_openai_provider_no_key(client_openai_no_key: TestClient) -> None:
    response = client_openai_no_key.get("/api/settings/openai-key")
    assert response.status_code == 200
    body = response.json()
    assert body == {"required": True, "configured": False, "source": None, "hint": None}


def test_get_status_openai_provider_env_key(client_openai_env_key: TestClient) -> None:
    response = client_openai_env_key.get("/api/settings/openai-key")
    assert response.status_code == 200
    body = response.json()
    assert body["required"] is True
    assert body["configured"] is True
    assert body["source"] == "env"
    assert body["hint"] == "…1234"


def test_get_status_openai_provider_file_key(client_openai_no_key: TestClient) -> None:
    write_file_key(client_openai_no_key.app.state.settings.data_dir, "sk-file-key-5678")
    response = client_openai_no_key.get("/api/settings/openai-key")
    body = response.json()
    assert body["configured"] is True
    assert body["source"] == "file"
    assert body["hint"] == "…5678"


# -- PUT -----------------------------------------------------------------


def test_put_saves_key_on_successful_validation(client_openai_no_key: TestClient) -> None:
    client_openai_no_key.app.dependency_overrides[settings_api.get_key_validator] = lambda: (
        _validator_ok
    )
    try:
        response = client_openai_no_key.put(
            "/api/settings/openai-key", json={"api_key": "  sk-new-key-9999  "}
        )
    finally:
        client_openai_no_key.app.dependency_overrides.pop(settings_api.get_key_validator, None)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["configured"] is True
    assert body["source"] == "file"
    assert body["hint"] == "…9999"
    # 前後の空白を trim して保存する。
    assert read_file_key(client_openai_no_key.app.state.settings.data_dir) == "sk-new-key-9999"


def test_put_rejects_empty_key_without_calling_validator(client_openai_no_key: TestClient) -> None:
    calls: list[str] = []

    async def _tracking_validator(api_key: str, base_url: str | None) -> None:
        calls.append(api_key)

    client_openai_no_key.app.dependency_overrides[settings_api.get_key_validator] = lambda: (
        _tracking_validator
    )
    try:
        response = client_openai_no_key.put("/api/settings/openai-key", json={"api_key": "   "})
    finally:
        client_openai_no_key.app.dependency_overrides.pop(settings_api.get_key_validator, None)

    assert response.status_code == 400
    assert calls == []
    assert read_file_key(client_openai_no_key.app.state.settings.data_dir) is None


def test_put_invalid_key_returns_400_and_does_not_save(client_openai_no_key: TestClient) -> None:
    client_openai_no_key.app.dependency_overrides[settings_api.get_key_validator] = lambda: (
        _validator_invalid
    )
    try:
        response = client_openai_no_key.put(
            "/api/settings/openai-key", json={"api_key": "sk-bad-key"}
        )
    finally:
        client_openai_no_key.app.dependency_overrides.pop(settings_api.get_key_validator, None)

    assert response.status_code == 400
    assert response.json() == {"detail": "キーが無効です"}
    assert read_file_key(client_openai_no_key.app.state.settings.data_dir) is None


def test_put_network_error_returns_502_and_does_not_save(client_openai_no_key: TestClient) -> None:
    client_openai_no_key.app.dependency_overrides[settings_api.get_key_validator] = lambda: (
        _validator_network_error
    )
    try:
        response = client_openai_no_key.put(
            "/api/settings/openai-key", json={"api_key": "sk-network-issue"}
        )
    finally:
        client_openai_no_key.app.dependency_overrides.pop(settings_api.get_key_validator, None)

    assert response.status_code == 502
    assert read_file_key(client_openai_no_key.app.state.settings.data_dir) is None


def test_put_rejected_when_env_key_active(client_openai_env_key: TestClient) -> None:
    calls: list[str] = []

    async def _tracking_validator(api_key: str, base_url: str | None) -> None:
        calls.append(api_key)

    client_openai_env_key.app.dependency_overrides[settings_api.get_key_validator] = lambda: (
        _tracking_validator
    )
    try:
        response = client_openai_env_key.put(
            "/api/settings/openai-key", json={"api_key": "sk-should-not-be-used"}
        )
    finally:
        client_openai_env_key.app.dependency_overrides.pop(settings_api.get_key_validator, None)

    assert response.status_code == 409
    assert calls == []  # env が優先されるので、有効性確認まで到達しない。


# -- DELETE ----------------------------------------------------------------


def test_delete_removes_file_key(client_openai_no_key: TestClient) -> None:
    write_file_key(client_openai_no_key.app.state.settings.data_dir, "sk-to-delete")

    response = client_openai_no_key.delete("/api/settings/openai-key")
    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is False

    assert read_file_key(client_openai_no_key.app.state.settings.data_dir) is None


def test_delete_when_nothing_configured_is_idempotent(client_openai_no_key: TestClient) -> None:
    response = client_openai_no_key.delete("/api/settings/openai-key")
    assert response.status_code == 200
    assert response.json()["configured"] is False


def test_delete_rejected_when_env_key_active(client_openai_env_key: TestClient) -> None:
    response = client_openai_env_key.delete("/api/settings/openai-key")
    assert response.status_code == 409


# --- 確認処理そのもの(OpenAI の応答ごとの扱い)。実 API は呼ばない ---


def _openai_error(cls: type[openai.APIStatusError], status: int) -> openai.APIStatusError:
    request = httpx.Request("GET", "https://api.openai.com/v1/models")
    return cls("error", response=httpx.Response(status, request=request), body=None)


def _patch_models_list(monkeypatch: pytest.MonkeyPatch, error: Exception | None) -> None:
    class _Models:
        async def list(self) -> None:
            if error is not None:
                raise error

    class _Client:
        def __init__(self, **kwargs: object) -> None:
            self.models = _Models()

    monkeypatch.setattr(settings_api, "AsyncOpenAI", _Client)


def test_validate_accepts_a_key_that_lists_models(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_models_list(monkeypatch, None)
    asyncio.run(settings_api._validate_key_live("sk-test", None))


def test_validate_accepts_a_restricted_key_without_model_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """画像の権限だけの制限付きキーはモデル一覧が 403 になる。キーは有効なので受け入れる。"""
    _patch_models_list(monkeypatch, _openai_error(openai.PermissionDeniedError, 403))
    asyncio.run(settings_api._validate_key_live("sk-test", None))


def test_validate_rejects_an_unauthenticated_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_models_list(monkeypatch, _openai_error(openai.AuthenticationError, 401))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(settings_api._validate_key_live("sk-test", None))
    assert exc.value.status_code == 400


def test_validate_passes_base_url_to_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR-0017: キー確認は、渡された Base URL に対して行う。"""
    captured: dict = {}

    class _Models:
        async def list(self) -> None:
            return None

    class _Client:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)
            self.models = _Models()

    monkeypatch.setattr(settings_api, "AsyncOpenAI", _Client)
    asyncio.run(settings_api._validate_key_live("sk-test", "http://127.0.0.1:4000/v1"))
    assert captured["base_url"] == "http://127.0.0.1:4000/v1"
