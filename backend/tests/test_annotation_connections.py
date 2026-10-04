"""推定の接続先の一覧と、用途ごとの「接続先 + モデル名」の組(ADR-0024 8章)。

- 用途ごとの組の読み書き(接続先の追加・変更・削除とキーは `test_llm_connections.py`)
- 今の設定(接続先1組の形)からの移行(推定専用の Base URL あり・なし、冪等、送り先が変わらない)
- 画像ごとの組の選び方(ComfyUI の Run の出力、アップロード、OpenAI の Run、ComfyUI 用が null)
- 1時間の上限を接続先ごとに数える、失敗しても既定の組に切り替えない、記録するモデル名、
  タグの訳の LLM の組

エンジンは FakeEngines を元にした記録用のもので、実 API は呼ばない。
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.annotation.engines import AnnotationEngineError, EngineContext, FakeEngines, VlmResult
from app.config import Settings
from app.domain import annotation_settings as s
from app.domain import api_key as api_key_domain
from app.domain import llm_connections
from app.domain.general_settings import _get_raw_value, _save
from app.domain.models import Run
from tests.test_annotations import _generate, _patch_settings, _upload, _wait_annotation
from tests.test_llm_connections import add_llm_connection

LOCAL_URL = "http://127.0.0.1:11434/v1"


class RecordingEngines(FakeEngines):
    """呼ばれた用途と送り先(接続先 id とモデル名)を記録する。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None, str | None]] = []

    def _record(self, kind: str, target: s.Target | None) -> None:
        self.calls.append(
            (kind, target.connection_id if target else None, target.model if target else None)
        )

    async def title_from_prompt(self, prompt: str, ctx: EngineContext) -> str:
        self._record("title", ctx.llm)
        return await super().title_from_prompt(prompt, ctx)

    async def describe_image(
        self,
        image_jpeg: bytes,
        prompt: str | None,
        want_title: bool,
        ctx: EngineContext,
        known_tags: list[str] | None = None,
    ) -> VlmResult:
        self._record("vlm", ctx.vlm)
        return await super().describe_image(image_jpeg, prompt, want_title, ctx, known_tags)

    async def translate_tags(self, tags: list[str], ctx: EngineContext) -> dict[str, str]:
        self._record("translate", ctx.llm)
        return await super().translate_tags(tags, ctx)


@pytest.fixture
def recording(client: TestClient) -> RecordingEngines:
    engines = RecordingEngines()
    client.app.state.annotator.engines = engines
    return engines


def _add_connection(client: TestClient, name: str = "Ollama", **extra: Any) -> str:
    return add_llm_connection(client, name, base_url=LOCAL_URL, api_style="chat", **extra)


def _connection_calls(body: dict, connection_id: str) -> int:
    return next(
        c["calls_last_hour"]
        for c in body["connection_calls"]
        if c["connection_id"] == connection_id
    )


def _set_provider(client: TestClient, run_id: str, provider: str) -> None:
    """Run のプロバイダーを書き換える(ComfyUI を用意せずに ComfyUI の Run の出力を作る)。"""
    with client.app.state.session_factory() as session:
        run = session.get(Run, uuid.UUID(run_id))
        assert run is not None
        run.provider = provider
        session.commit()


def _comfyui_asset(client: TestClient, prompt: str = "a lighthouse") -> str:
    run = _generate(client, prompt)
    _set_provider(client, run["id"], "comfyui")
    return run["outputs"][0]["asset_id"]


def _annotate(client: TestClient, asset_id: str) -> dict:
    response = client.post(f"/api/assets/{asset_id}/annotate")
    assert response.status_code == 200, response.text
    return _wait_annotation(client, asset_id)


def _auto_models(client: TestClient, asset_id: str) -> dict | None:
    from app.domain.models import AssetAnnotation

    with client.app.state.session_factory() as session:
        row = session.get(AssetAnnotation, uuid.UUID(asset_id))
        return row.auto_models if row is not None else None


# -- 用途ごとの組 ----------------------------------------------------------------


def test_profiles_roundtrip(client: TestClient) -> None:
    local = _add_connection(client)
    body = _patch_settings(
        client,
        profiles={
            "default": {"vlm": {"connection_id": local, "model": "llava"}},
            "comfyui": {
                "llm": {"connection_id": local, "model": "qwen3"},
                "vlm": {"connection_id": "openai", "model": "gpt-x"},
            },
        },
    )
    assert body["profiles"] == {
        "default": {
            "llm": {"connection_id": "openai", "model": "gpt-5.6-luna"},
            "vlm": {"connection_id": local, "model": "llava"},
        },
        "comfyui": {
            "llm": {"connection_id": local, "model": "qwen3"},
            "vlm": {"connection_id": "openai", "model": "gpt-x"},
        },
    }
    body = _patch_settings(client, profiles={"comfyui": {"vlm": None}})
    assert body["profiles"]["comfyui"] == {
        "llm": {"connection_id": local, "model": "qwen3"},
        "vlm": None,
    }


# -- 今の設定からの移行 --------------------------------------------------------------


def _settings(data_dir: Path, **extra: Any) -> Settings:
    return Settings(_env_file=None, data_dir=data_dir, fake_provider=True, **extra)


def _write_legacy(factory: sessionmaker, values: dict[str, Any]) -> None:
    with factory() as db:
        for name, value in values.items():
            _save(db, f"annotation.{name}", value)


def _targets(factory: sessionmaker, settings: Settings) -> tuple[s.Target, s.Target]:
    with factory() as db:
        config = s.load(db)
    return (
        s.resolve_target(config, settings, "default", "llm"),
        s.resolve_target(config, settings, "default", "vlm"),
    )


def test_migration_with_dedicated_base_url(
    db_session_factory: sessionmaker, tmp_path: Path
) -> None:
    data_dir = tmp_path / "d1"
    data_dir.mkdir()
    settings = _settings(data_dir)
    api_key_domain.write_file_key(data_dir, "sk-openai")
    _write_legacy(
        db_session_factory,
        {
            "base_url": LOCAL_URL,
            "api_style": "chat",
            "llm_model": "qwen3:8b",
            "vlm_model": "llava:13b",
            "llm_enabled": True,
        },
    )
    api_key_domain.write_secret_field(data_dir, "annotation_api_key", "sk-local")

    with db_session_factory() as db:
        assert s.migrate_legacy(db, settings) is True
        config = s.load(db)
        # 古い項目は消え、ほかの項目(llm_enabled)は残る。
        for name in ("base_url", "api_style", "llm_model", "vlm_model"):
            assert _get_raw_value(db, f"annotation.{name}") is None
        assert config.llm_enabled is True
    assert len(config.connections) == 1
    migrated = config.connections[0]
    assert (migrated.name, migrated.base_url, migrated.api_style) == ("推定専用", LOCAL_URL, "chat")
    assert api_key_domain.read_secret_field(data_dir, "annotation_api_key") is None
    assert llm_connections.read_connection_key(data_dir, migrated.id) == "sk-local"

    # 送り先は移行前(推定専用の Base URL と推定専用キー、chat)と同じ。
    llm, vlm = _targets(db_session_factory, settings)
    assert (llm.api_key, llm.base_url, llm.api_style, llm.model) == (
        "sk-local",
        LOCAL_URL,
        "chat",
        "qwen3:8b",
    )
    assert (vlm.api_key, vlm.base_url, vlm.api_style, vlm.model) == (
        "sk-local",
        LOCAL_URL,
        "chat",
        "llava:13b",
    )

    # 2回目は何もしない(冪等)。
    with db_session_factory() as db:
        assert s.migrate_legacy(db, settings) is False
        assert len(s.load(db).connections) == 1


def test_migration_with_dedicated_base_url_without_key(
    db_session_factory: sessionmaker, tmp_path: Path
) -> None:
    data_dir = tmp_path / "d2"
    data_dir.mkdir()
    settings = _settings(data_dir)
    api_key_domain.write_file_key(data_dir, "sk-openai")
    _write_legacy(db_session_factory, {"base_url": LOCAL_URL})
    with db_session_factory() as db:
        assert s.migrate_legacy(db, settings) is True
    llm, vlm = _targets(db_session_factory, settings)
    # OpenAI のキーは送らず、ダミーのキー(移行前と同じ)。モデルは既定のまま。
    assert (llm.api_key, llm.base_url, llm.api_style, llm.model) == (
        llm_connections.PLACEHOLDER_API_KEY,
        LOCAL_URL,
        "responses",
        s.DEFAULT_LLM_MODEL,
    )
    assert vlm.connection_id == llm.connection_id != "openai"


def test_migration_without_dedicated_base_url_uses_openai(
    db_session_factory: sessionmaker, tmp_path: Path
) -> None:
    data_dir = tmp_path / "d3"
    data_dir.mkdir()
    settings = _settings(data_dir)
    api_key_domain.write_file_key(data_dir, "sk-openai")
    api_key_domain.write_file_base_url(data_dir, "http://127.0.0.1:4000/v1")
    _write_legacy(
        db_session_factory, {"api_style": "chat", "llm_model": "gpt-a", "vlm_model": "gpt-b"}
    )
    with db_session_factory() as db:
        assert s.migrate_legacy(db, settings) is True
        config = s.load(db)
    assert config.connections == ()
    assert config.openai_api_style == "chat"
    llm, vlm = _targets(db_session_factory, settings)
    assert (llm.connection_id, llm.api_key, llm.base_url, llm.api_style, llm.model) == (
        "openai",
        "sk-openai",
        "http://127.0.0.1:4000/v1",
        "chat",
        "gpt-a",
    )
    assert (vlm.connection_id, vlm.model) == ("openai", "gpt-b")
    with db_session_factory() as db:
        assert s.migrate_legacy(db, settings) is False


def test_migration_with_only_dedicated_key_keeps_sending_it_to_openai_base(
    db_session_factory: sessionmaker, tmp_path: Path
) -> None:
    """推定専用の Base URL が無く推定専用キーだけがあった(OpenAI の Base URL にそのキーで
    送っていた)場合は、その時点の OpenAI の Base URL で接続先「推定専用」を作る。"""
    data_dir = tmp_path / "d4"
    data_dir.mkdir()
    settings = _settings(data_dir)
    api_key_domain.write_file_key(data_dir, "sk-openai")
    api_key_domain.write_secret_field(data_dir, "annotation_api_key", "sk-own")
    with db_session_factory() as db:
        assert s.migrate_legacy(db, settings) is True
    llm, _ = _targets(db_session_factory, settings)
    assert (llm.api_key, llm.base_url) == ("sk-own", llm_connections.OPENAI_DEFAULT_BASE_URL)


def test_migration_is_noop_on_fresh_install(
    db_session_factory: sessionmaker, tmp_path: Path
) -> None:
    data_dir = tmp_path / "d5"
    data_dir.mkdir()
    with db_session_factory() as db:
        assert s.migrate_legacy(db, _settings(data_dir)) is False
        assert _get_raw_value(db, "annotation.profiles") is None
        assert s.load(db).profiles == s.Profiles()


def test_migration_keeps_legacy_key_when_profiles_already_exist(
    db_session_factory: sessionmaker, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """組が既にあるのに旧い行と旧キーが残っているとき、旧い行は消すが、旧キーは黙って消さずに
    残して警告を出す。"""
    data_dir = tmp_path / "d6"
    data_dir.mkdir()
    settings = _settings(data_dir)
    with db_session_factory() as db:
        s.save_profiles(db, s.Profiles(default_llm=s.TargetChoice("openai", "m-now")))
    _write_legacy(db_session_factory, {"base_url": LOCAL_URL})
    api_key_domain.write_secret_field(data_dir, "annotation_api_key", "sk-old")

    with caplog.at_level("WARNING", logger="app.domain.annotation_settings"):
        with db_session_factory() as db:
            assert s.migrate_legacy(db, settings) is True
            assert _get_raw_value(db, "annotation.base_url") is None
            assert s.load(db).profiles.default_llm == s.TargetChoice("openai", "m-now")
    assert api_key_domain.read_secret_field(data_dir, "annotation_api_key") == "sk-old"
    assert any("annotation_api_key" in r.getMessage() for r in caplog.records)

    # 次の起動でも消さない(行はもう無いので、変更なし)。
    with db_session_factory() as db:
        assert s.migrate_legacy(db, settings) is False
    assert api_key_domain.read_secret_field(data_dir, "annotation_api_key") == "sk-old"


def test_migration_commit_failure_then_rerun(
    db_session_factory: sessionmaker, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """commit に失敗したらキーは書かず(旧キーは残る)、次の起動で最初からやり直せる。"""
    data_dir = tmp_path / "d7"
    data_dir.mkdir()
    settings = _settings(data_dir)
    _write_legacy(db_session_factory, {"base_url": LOCAL_URL})
    api_key_domain.write_secret_field(data_dir, "annotation_api_key", "sk-local")

    with db_session_factory() as db:

        def broken_commit() -> None:
            raise RuntimeError("commit に失敗")

        monkeypatch.setattr(db, "commit", broken_commit)
        with pytest.raises(RuntimeError):
            s.migrate_legacy(db, settings)
    secrets = json.loads((data_dir / "secrets.json").read_text(encoding="utf-8"))
    assert secrets == {"annotation_api_key": "sk-local"}

    with db_session_factory() as db:
        assert s.migrate_legacy(db, settings) is True
        config = s.load(db)
        assert _get_raw_value(db, "annotation.legacy_key_connection") is None
    assert len(config.connections) == 1
    assert llm_connections.read_connection_key(data_dir, config.connections[0].id) == "sk-local"
    assert api_key_domain.read_secret_field(data_dir, "annotation_api_key") is None


def test_migration_key_write_failure_after_commit_then_rerun(
    db_session_factory: sessionmaker, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """commit の後、キーを書く前に落ちても、次の起動で目印と残った旧キーから書き直す。"""
    data_dir = tmp_path / "d8"
    data_dir.mkdir()
    settings = _settings(data_dir)
    _write_legacy(db_session_factory, {"base_url": LOCAL_URL})
    api_key_domain.write_secret_field(data_dir, "annotation_api_key", "sk-local")

    def broken_write(*args: Any) -> None:
        raise OSError("書き込みに失敗")

    with monkeypatch.context() as m:
        m.setattr(llm_connections, "write_connection_key", broken_write)
        with db_session_factory() as db, pytest.raises(OSError):
            s.migrate_legacy(db, settings)
    with db_session_factory() as db:
        assert isinstance(_get_raw_value(db, "annotation.legacy_key_connection"), str)
    assert api_key_domain.read_secret_field(data_dir, "annotation_api_key") == "sk-local"

    with db_session_factory() as db:
        assert s.migrate_legacy(db, settings) is True
        config = s.load(db)
        assert _get_raw_value(db, "annotation.legacy_key_connection") is None
    assert len(config.connections) == 1
    assert llm_connections.read_connection_key(data_dir, config.connections[0].id) == "sk-local"
    assert api_key_domain.read_secret_field(data_dir, "annotation_api_key") is None
    with db_session_factory() as db:
        assert s.migrate_legacy(db, settings) is False


def test_migration_runs_on_startup(data_dir: Path) -> None:
    from app.main import create_app

    app = create_app(_settings(data_dir))
    with TestClient(app):
        _write_legacy(app.state.session_factory, {"base_url": LOCAL_URL, "llm_model": "m1"})
    with TestClient(app) as client:
        body = client.get("/api/settings/annotation").json()
        connections = client.get("/api/settings/llm-connections").json()["connections"]
    assert [c["name"] for c in connections] == ["OpenAI の設定", "推定専用"]
    local = connections[1]["id"]
    assert body["profiles"]["default"]["llm"] == {"connection_id": local, "model": "m1"}


# -- 画像ごとの組の選び方 --------------------------------------------------------------


def _setup_comfyui_override(client: TestClient, **scalars: Any) -> str:
    local = _add_connection(client)
    _patch_settings(
        client,
        profiles={
            "default": {
                "llm": {"connection_id": "openai", "model": "gpt-llm"},
                "vlm": {"connection_id": "openai", "model": "gpt-vlm"},
            },
            "comfyui": {
                "llm": {"connection_id": local, "model": "local-llm"},
                "vlm": {"connection_id": local, "model": "local-vlm"},
            },
        },
        **scalars,
    )
    return local


def test_comfyui_output_uses_comfyui_profile(
    client: TestClient, recording: RecordingEngines
) -> None:
    local = _setup_comfyui_override(client)
    asset_id = _comfyui_asset(client)
    _patch_settings(client, llm_enabled=True, vlm_enabled=True)
    body = _annotate(client, asset_id)
    assert body["annotation"]["status"] == "succeeded", body["annotation"]
    assert sorted(recording.calls) == [
        ("title", local, "local-llm"),
        ("vlm", local, "local-vlm"),
    ]
    assert _auto_models(client, asset_id) == {"llm": "local-llm", "vlm": "local-vlm"}


def test_upload_and_openai_run_use_default_profile(
    client: TestClient, recording: RecordingEngines
) -> None:
    _setup_comfyui_override(client)
    run = _generate(client, "a fox")
    uploaded = _upload(client)["id"]
    _patch_settings(client, llm_enabled=True, vlm_enabled=True)

    _annotate(client, run["outputs"][0]["asset_id"])
    assert sorted(recording.calls) == [("title", "openai", "gpt-llm"), ("vlm", "openai", "gpt-vlm")]
    recording.calls.clear()
    _annotate(client, uploaded)
    assert recording.calls == [("vlm", "openai", "gpt-vlm")]
    assert _auto_models(client, uploaded) == {"vlm": "gpt-vlm"}


def test_comfyui_null_slot_falls_back_to_default(
    client: TestClient, recording: RecordingEngines
) -> None:
    local = _add_connection(client)
    _patch_settings(
        client,
        profiles={
            "default": {
                "llm": {"connection_id": "openai", "model": "gpt-llm"},
                "vlm": {"connection_id": "openai", "model": "gpt-vlm"},
            },
            # タイトルだけ上書きし、タグは既定と同じ。
            "comfyui": {"llm": {"connection_id": local, "model": "local-llm"}, "vlm": None},
        },
    )
    asset_id = _comfyui_asset(client)
    _patch_settings(client, llm_enabled=True, vlm_enabled=True)
    _annotate(client, asset_id)
    assert sorted(recording.calls) == [("title", local, "local-llm"), ("vlm", "openai", "gpt-vlm")]


def test_translation_llm_uses_title_pair_of_the_image(
    client: TestClient, recording: RecordingEngines
) -> None:
    local = _setup_comfyui_override(client)
    asset_id = _comfyui_asset(client)
    # VLM は無効、LLM と ONNX が有効、localized(ja)→ ONNX のタグを LLM で訳す(6章)。
    _patch_settings(client, llm_enabled=True, onnx_enabled=True, tag_language="localized")
    body = _annotate(client, asset_id)
    assert body["annotation"]["status"] == "succeeded"
    assert sorted(recording.calls) == [
        ("title", local, "local-llm"),
        ("translate", local, "local-llm"),
    ]
    assert _auto_models(client, asset_id) == {"llm": "local-llm", "onnx": "wd-vit-tagger-v3"}


def test_failure_on_comfyui_pair_does_not_fall_back_to_default(client: TestClient) -> None:
    class BrokenLocal(RecordingEngines):
        async def title_from_prompt(self, prompt: str, ctx: EngineContext) -> str:
            self._record("title", ctx.llm)
            assert ctx.llm is not None
            if ctx.llm.connection_id != "openai":
                raise AnnotationEngineError("ローカルの LLM に繋がらない")
            return "既定のタイトル"

    engines = BrokenLocal()
    client.app.state.annotator.engines = engines
    local = _setup_comfyui_override(client)
    asset_id = _comfyui_asset(client)
    _patch_settings(client, llm_enabled=True)
    body = _annotate(client, asset_id)
    assert body["annotation"]["status"] == "failed"
    assert body["annotation"]["error"] == "ローカルの LLM に繋がらない"
    assert engines.calls == [("title", local, "local-llm")]
    assert body["title"] is None


def test_missing_connection_fails_without_fallback(
    client: TestClient, recording: RecordingEngines
) -> None:
    # API では消せない(使用中)ので、壊れた設定を直接書いて確かめる。
    with client.app.state.session_factory() as db:
        s.save_profiles(
            db,
            s.Profiles(comfyui_llm=s.TargetChoice("gone", "m")),
        )
    asset_id = _comfyui_asset(client)
    _patch_settings(client, llm_enabled=True)
    body = _annotate(client, asset_id)
    assert body["annotation"]["status"] == "failed"
    assert "接続先が見つかりません" in body["annotation"]["error"]
    assert recording.calls == []


def test_user_connection_without_key_sends_placeholder(client: TestClient, data_dir: Path) -> None:
    """キーの無い接続先にはダミーのキーを送る(OpenAI のキーは送らない)。"""
    api_key_domain.write_file_key(data_dir, "sk-openai")
    local = _add_connection(client)
    settings = client.app.state.settings
    _patch_settings(client, profiles={"comfyui": {"llm": {"connection_id": local, "model": "m"}}})
    with client.app.state.session_factory() as db:
        config = s.load(db)
    target = s.resolve_target(config, settings, "comfyui", "llm")
    assert (target.api_key, target.base_url, target.api_style) == (
        llm_connections.PLACEHOLDER_API_KEY,
        LOCAL_URL,
        "chat",
    )
    builtin = s.resolve_target(config, settings, "comfyui", "vlm")
    assert (builtin.connection_id, builtin.api_key) == ("openai", "sk-openai")


# -- 1時間の上限(接続先ごと) --------------------------------------------------------


def test_hourly_limit_is_counted_per_connection(
    client: TestClient, recording: RecordingEngines
) -> None:
    local = _add_connection(client)
    _patch_settings(
        client,
        vlm_enabled=True,
        hourly_limit=1,
        profiles={"comfyui": {"vlm": {"connection_id": local, "model": "local-vlm"}}},
    )
    comfy_asset = _comfyui_asset(client)
    first = _upload(client, color=(1, 1, 1))["id"]
    second = _upload(client, color=(2, 2, 2))["id"]

    assert _annotate(client, first)["annotation"]["status"] == "succeeded"
    # OpenAI の枠を使い切ったので、既定の組の画像は待つ。
    client.post(f"/api/assets/{second}/annotate")
    time.sleep(1.5)
    assert client.get(f"/api/assets/{second}").json()["annotation"]["status"] == "queued"

    # ローカルの接続先は別に数えるので、ComfyUI の画像は先に進む(既定の組の行に塞がれない)。
    assert _annotate(client, comfy_asset)["annotation"]["status"] == "succeeded"
    body = client.get("/api/settings/annotation").json()
    assert _connection_calls(body, "openai") == 1
    assert _connection_calls(body, local) == 1
    assert body["calls_last_hour"] == 2
    assert client.get(f"/api/assets/{second}").json()["annotation"]["status"] == "queued"
    assert recording.calls == [("vlm", "openai", "gpt-5.6-luna"), ("vlm", local, "local-vlm")]

    _patch_settings(client, hourly_limit=10)
    assert _wait_annotation(client, second)["annotation"]["status"] == "succeeded"


def test_head_row_needing_more_than_left_does_not_block_other_profile(
    client: TestClient, recording: RecordingEngines
) -> None:
    """使用数が上限−1 のとき、1件に2回要る行が先頭にあっても、ほかの組の行は進む。
    先頭の行は取っては戻すのを繰り返さない(画像も読み直さない)。"""
    local = _setup_comfyui_override(client, hourly_limit=2)
    uploaded = _upload(client, color=(3, 3, 3))["id"]
    run = _generate(client, "a fox")
    head = run["outputs"][0]["asset_id"]
    comfy_asset = _comfyui_asset(client)

    # OpenAI の枠を1回使う(プロンプトの無いアップロードは VLM だけ)。
    _patch_settings(client, vlm_enabled=True)
    assert _annotate(client, uploaded)["annotation"]["status"] == "succeeded"
    _patch_settings(client, llm_enabled=True)

    store = client.app.state.annotator.store
    original_open = store.open_content
    opened: list[str] = []

    def counting_open(blob_key: str, sha256: str, variant: str):  # type: ignore[no-untyped-def]
        opened.append(sha256)
        return original_open(blob_key, sha256, variant)

    store.open_content = counting_open  # type: ignore[method-assign]
    try:
        # 既定の組の行(タイトルと VLM の2回が要る。1 + 2 > 2)が先頭。
        client.post(f"/api/assets/{head}/annotate")
        time.sleep(1.5)
        assert client.get(f"/api/assets/{head}").json()["annotation"]["status"] == "queued"
        # ComfyUI の組(別の接続先)の行は先頭の行に塞がれない。
        assert _annotate(client, comfy_asset)["annotation"]["status"] == "succeeded"
        time.sleep(1.5)
        assert client.get(f"/api/assets/{head}").json()["annotation"]["status"] == "queued"
        # 枠に収まらない行のために画像を読んでいない(読んだのは ComfyUI の画像の1回だけ)。
        assert len(opened) == 1
    finally:
        store.open_content = original_open  # type: ignore[method-assign]

    assert recording.calls[0] == ("vlm", "openai", "gpt-vlm")
    assert sorted(recording.calls[1:]) == [
        ("title", local, "local-llm"),
        ("vlm", local, "local-vlm"),
    ]

    # 上限を上げれば、止めていた組も再開する。
    _patch_settings(client, hourly_limit=10)
    assert _wait_annotation(client, head)["annotation"]["status"] == "succeeded"
