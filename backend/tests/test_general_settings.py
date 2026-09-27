"""`GET`/`PATCH /api/settings/general`(ADR-0009、ADR-0013 7章)。

moderation(Generate 専用)と ComfyUI のタイムアウトを画面から変える。優先順位(画面で保存
した値 > 環境変数 > 組み込みの既定値)と、再起動なしで次の Run / 実行から反映されることを
確かめる。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.domain import general_settings
from app.providers.comfyui.provider import ComfyUIProvider
from tests.conftest import wait_for_run_terminal

# -- GET: 既定値 -----------------------------------------------------------------


def test_get_general_settings_returns_built_in_defaults(client: TestClient) -> None:
    """何も保存・環境変数指定していなければ、組み込みの既定値(low / 1800秒)。"""
    response = client.get("/api/settings/general")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["moderation"] == {"value": "low", "source": "default", "default": "low"}
    assert body["comfyui_timeout_seconds"] == {
        "value": 1800,
        "source": "default",
        "default": 1800,
    }


def test_get_general_settings_follows_env_when_not_saved(
    monkeypatch: pytest.MonkeyPatch, data_dir: Path
) -> None:
    """MODERATION / COMFYUI_TIMEOUT_SECONDS を与えたアプリでは、保存が無い間 source=env。"""
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("FAKE_PROVIDER", "1")
    monkeypatch.setenv("MODERATION", "auto")
    monkeypatch.setenv("COMFYUI_TIMEOUT_SECONDS", "900")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    from app.main import create_app

    app = create_app()
    with TestClient(app) as test_client:
        response = test_client.get("/api/settings/general")
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["moderation"] == {"value": "auto", "source": "env", "default": "auto"}
        assert body["comfyui_timeout_seconds"] == {
            "value": 900,
            "source": "env",
            "default": 900,
        }


# -- PATCH: 保存して上書き、null でリセット ---------------------------------------


def test_patch_general_settings_saves_moderation_and_timeout(client: TestClient) -> None:
    response = client.patch(
        "/api/settings/general",
        json={"moderation": "auto", "comfyui_timeout_seconds": 120},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["moderation"] == {"value": "auto", "source": "setting", "default": "low"}
    assert body["comfyui_timeout_seconds"] == {
        "value": 120,
        "source": "setting",
        "default": 1800,
    }

    # GET でも保存内容が読める。
    get_response = client.get("/api/settings/general")
    assert get_response.json() == body


def test_patch_general_settings_omitted_field_is_unchanged(client: TestClient) -> None:
    client.patch("/api/settings/general", json={"moderation": "auto"})

    response = client.patch("/api/settings/general", json={"comfyui_timeout_seconds": 300})
    assert response.status_code == 200, response.text
    body = response.json()

    # moderation は本文に含めなかったので変わらない(auto のまま)。
    assert body["moderation"]["value"] == "auto"
    assert body["comfyui_timeout_seconds"]["value"] == 300


def test_patch_general_settings_null_resets_to_default(client: TestClient) -> None:
    client.patch(
        "/api/settings/general",
        json={"moderation": "auto", "comfyui_timeout_seconds": 120},
    )

    response = client.patch(
        "/api/settings/general",
        json={"moderation": None, "comfyui_timeout_seconds": None},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["moderation"] == {"value": "low", "source": "default", "default": "low"}
    assert body["comfyui_timeout_seconds"] == {
        "value": 1800,
        "source": "default",
        "default": 1800,
    }


def test_patch_general_settings_null_resets_to_env(
    monkeypatch: pytest.MonkeyPatch, data_dir: Path
) -> None:
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("FAKE_PROVIDER", "1")
    monkeypatch.setenv("MODERATION", "auto")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    from app.main import create_app

    app = create_app()
    with TestClient(app) as test_client:
        test_client.patch("/api/settings/general", json={"moderation": "low"})
        response = test_client.patch("/api/settings/general", json={"moderation": None})
        assert response.status_code == 200, response.text
        assert response.json()["moderation"] == {
            "value": "auto",
            "source": "env",
            "default": "auto",
        }


# -- 検証エラー(422、i18n) -------------------------------------------------------


def test_patch_general_settings_rejects_invalid_moderation(client: TestClient) -> None:
    response = client.patch("/api/settings/general", json={"moderation": "strict"})
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "moderation は auto または low である必要があります"


def test_patch_general_settings_rejects_invalid_moderation_english(client: TestClient) -> None:
    response = client.patch(
        "/api/settings/general",
        json={"moderation": "strict"},
        headers={"Accept-Language": "en"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "moderation must be auto or low"


@pytest.mark.parametrize("value", [59, 10801, 0, -1])
def test_patch_general_settings_rejects_out_of_range_timeout(
    client: TestClient, value: int
) -> None:
    response = client.patch("/api/settings/general", json={"comfyui_timeout_seconds": value})
    assert response.status_code == 422, response.text
    assert "60" in response.json()["detail"]
    assert "10800" in response.json()["detail"]


@pytest.mark.parametrize("value", [60, 10800])
def test_patch_general_settings_accepts_boundary_timeout(client: TestClient, value: int) -> None:
    response = client.patch("/api/settings/general", json={"comfyui_timeout_seconds": value})
    assert response.status_code == 200, response.text
    assert response.json()["comfyui_timeout_seconds"]["value"] == value


def test_patch_general_settings_rejects_invalid_body_type_for_timeout(client: TestClient) -> None:
    """整数でない値は Pydantic の型検証(422)で弾かれる。"""
    response = client.patch(
        "/api/settings/general", json={"comfyui_timeout_seconds": "not-a-number"}
    )
    assert response.status_code == 422, response.text


# -- PATCH の失敗は保存しない ------------------------------------------------------


def test_patch_general_settings_failure_does_not_save_other_field(client: TestClient) -> None:
    """1リクエストに複数項目があり、片方が不正なら 422 全体で失敗し、通った方も保存しない
    (半端な保存を避ける)。"""
    response = client.patch(
        "/api/settings/general",
        json={"moderation": "auto", "comfyui_timeout_seconds": 1},
    )
    assert response.status_code == 422, response.text

    get_response = client.get("/api/settings/general")
    body = get_response.json()
    assert body["moderation"] == {"value": "low", "source": "default", "default": "low"}


# -- 再起動なしで反映: 次の Run から moderation --------------------------------------


def test_generate_run_uses_moderation_saved_after_patch(client: TestClient) -> None:
    patch_response = client.patch("/api/settings/general", json={"moderation": "auto"})
    assert patch_response.status_code == 200, patch_response.text

    response = client.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": "gpt-image-2.5-sunburst",
            "prompt": "a red apple on a table",
            "params": {"n": 1},
        },
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]

    detail = wait_for_run_terminal(client, run_id)
    assert detail["params"]["moderation"] == "auto"


def test_generate_run_reverts_to_default_after_reset(client: TestClient) -> None:
    client.patch("/api/settings/general", json={"moderation": "auto"})
    client.patch("/api/settings/general", json={"moderation": None})

    response = client.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": "gpt-image-2.5-sunburst",
            "prompt": "a red apple on a table",
            "params": {"n": 1},
        },
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]

    detail = wait_for_run_terminal(client, run_id)
    assert detail["params"]["moderation"] == "low"


# -- 再起動なしで反映: 次の実行から ComfyUI のタイムアウト(unit-level) ------------------


def test_comfyui_provider_uses_constructor_fallback_when_nothing_saved(
    db_session_factory: sessionmaker,
) -> None:
    provider = ComfyUIProvider("http://127.0.0.1:8188", db_session_factory, 1800.0)
    assert provider._effective_timeout_seconds() == 1800.0  # noqa: SLF001


def test_comfyui_provider_uses_saved_timeout_over_constructor_fallback(
    db_session_factory: sessionmaker,
) -> None:
    with db_session_factory() as session:
        general_settings.save_timeout_seconds(session, 120)

    provider = ComfyUIProvider("http://127.0.0.1:8188", db_session_factory, 1800.0)
    assert provider._effective_timeout_seconds() == 120.0  # noqa: SLF001


def test_comfyui_provider_reflects_timeout_saved_after_construction(
    db_session_factory: sessionmaker,
) -> None:
    """既に組み立て済みのプロバイダーでも、次に呼んだときには新しい保存値を使う
    (`execute` の開始時に毎回解決するため、作り直しは不要)。"""
    provider = ComfyUIProvider("http://127.0.0.1:8188", db_session_factory, 1800.0)
    assert provider._effective_timeout_seconds() == 1800.0  # noqa: SLF001

    with db_session_factory() as session:
        general_settings.save_timeout_seconds(session, 60)

    assert provider._effective_timeout_seconds() == 60.0  # noqa: SLF001


# -- ドメイン層: 保存された値が壊れているときは未設定と同じに扱う -----------------------


def test_get_saved_moderation_ignores_malformed_row(db_session_factory: sessionmaker) -> None:
    from app.domain.models import AppSetting

    with db_session_factory() as session:
        session.add(AppSetting(key=general_settings.MODERATION_KEY, value={"value": "bogus"}))
        session.commit()

        assert general_settings.get_saved_moderation(session) is None


def test_get_saved_timeout_seconds_ignores_malformed_row(db_session_factory: sessionmaker) -> None:
    from app.domain.models import AppSetting

    with db_session_factory() as session:
        session.add(AppSetting(key=general_settings.TIMEOUT_KEY, value={"value": "not-an-int"}))
        session.commit()

        assert general_settings.get_saved_timeout_seconds(session) is None
