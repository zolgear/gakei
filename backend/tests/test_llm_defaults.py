"""LLM・VLM の既定の送り先(ADR-0063)。

- 外部・内部の2組の読み書き(`PUT /api/settings/llm-connections/defaults`)と検証
- 機能ごとの上書きが無いときに、区分に合う組を使うこと(アシスタント、領域の検出、グループの
  名前の提案、SVG を書くモデル)
- 画像の区分(Run の区分、Run の無い Asset は外部、複数なら内部が1枚でもあれば内部)と、待ち行列
  で使う SQL の条件が同じ決め方であること
- 自動タイトル・タグの ADR-0063 より前の組(`default` / `comfyui`)の読み替え
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain import annotation_settings, assistant_settings, llm_defaults
from app.domain import group_naming_settings as naming_settings
from app.domain import locality as locality_domain
from app.domain import region_detection_settings as detection_settings
from app.domain.general_settings import _save
from app.domain.models import Asset, Run
from tests.test_annotations import _generate, _upload
from tests.test_llm_connections import add_llm_connection

BASE = "/api/settings/llm-connections"
DEFAULTS = f"{BASE}/defaults"


def _put(client: TestClient, body: dict[str, Any]) -> dict:
    response = client.put(DEFAULTS, json=body)
    assert response.status_code == 200, response.text
    return response.json()["defaults"]


def _set_provider(client: TestClient, run_id: str, provider: str) -> None:
    with client.app.state.session_factory() as db:
        run = db.get(Run, uuid.UUID(run_id))
        assert run is not None
        run.provider = provider
        db.commit()


# -- 読み書き ------------------------------------------------------------------------


def test_defaults_roundtrip(client: TestClient) -> None:
    body = client.get(BASE).json()["defaults"]
    assert body == {
        "external": {"connection_id": "openai", "model": llm_defaults.BUILTIN_DEFAULT_MODEL},
        "external_saved": False,
        "local": None,
        "builtin_model": llm_defaults.BUILTIN_DEFAULT_MODEL,
    }
    local = add_llm_connection(client, "Ollama")
    body = _put(
        client,
        {
            "external": {"connection_id": "openai", "model": " gpt-6-sol "},
            "local": {"connection_id": local, "model": "qwen3-vl"},
        },
    )
    assert body["external"] == {"connection_id": "openai", "model": "gpt-6-sol"}
    assert body["external_saved"] is True
    assert body["local"] == {"connection_id": local, "model": "qwen3-vl"}
    # 既定の送り先が使う接続先は削除できない。
    connection = next(c for c in client.get(BASE).json()["connections"] if c["id"] == local)
    assert connection["used_by"] == ["llm_defaults"]
    assert client.delete(f"{BASE}/{local}").status_code == 409

    # 書いた項目だけ変える。null は保存した値を消す。
    body = _put(client, {"local": None})
    assert body["local"] is None
    assert body["external"]["model"] == "gpt-6-sol"
    body = _put(client, {"external": None})
    assert body["external"]["model"] == llm_defaults.BUILTIN_DEFAULT_MODEL
    assert body["external_saved"] is False
    assert client.delete(f"{BASE}/{local}").status_code == 200


def test_defaults_validation(client: TestClient) -> None:
    for bad in (
        {"external": {"connection_id": "missing", "model": "m"}},
        {"external": {"connection_id": "openai", "model": " "}},
        {"local": {"connection_id": "openai", "model": "m" * 201}},
        {"local": {"connection_id": "openai"}},
    ):
        assert client.put(DEFAULTS, json=bad).status_code == 422, bad
    # 一部が不正なら何も保存しない。
    response = client.put(
        DEFAULTS,
        json={
            "external": {"connection_id": "openai", "model": "ok"},
            "local": {"connection_id": "missing", "model": "m"},
        },
    )
    assert response.status_code == 422
    assert client.get(BASE).json()["defaults"]["external_saved"] is False


def test_defaults_require_admin(client: TestClient) -> None:
    # 個人モードは暗黙の管理者。PUT が管理者の依存を通ることだけ確かめる(権限の網羅は
    # test_llm_connections の _ADMIN_CASES)。
    assert client.put(DEFAULTS, json={}).status_code == 200


# -- 機能ごとの送り先 ------------------------------------------------------------------


def test_features_without_override_follow_defaults(client: TestClient) -> None:
    local = add_llm_connection(client, "Ollama")
    _put(
        client,
        {
            "external": {"connection_id": "openai", "model": "gpt-ext"},
            "local": {"connection_id": local, "model": "local-vlm"},
        },
    )
    factory = client.app.state.session_factory
    with factory() as db:
        assistant = assistant_settings.load(db)
        assert (assistant.connection_id, assistant.model) == ("openai", "gpt-ext")
        assert assistant.override is None
        external = detection_settings.load(db, llm_defaults.EXTERNAL)
        assert (external.connection_id, external.model) == ("openai", "gpt-ext")
        internal = detection_settings.load(db, llm_defaults.LOCAL)
        assert (internal.connection_id, internal.model) == (local, "local-vlm")
        naming = naming_settings.load(db, llm_defaults.LOCAL)
        assert (naming.connection_id, naming.model) == (local, "local-vlm")
        annotation = annotation_settings.load(db)
        assert annotation.choice("local", "vlm") == llm_defaults.TargetChoice(local, "local-vlm")
        assert annotation.choice("external", "llm") == llm_defaults.TargetChoice(
            "openai", "gpt-ext"
        )

    # 上書きは区分に関わらず使う。null を送ると上書きを消す。
    response = client.patch(
        "/api/settings/region-detection", json={"connection_id": "openai", "model": "vlm-x"}
    )
    assert response.status_code == 200, response.text
    assert (response.json()["connection_id"], response.json()["model"]) == ("openai", "vlm-x")
    with factory() as db:
        config = detection_settings.load(db, llm_defaults.LOCAL)
        assert (config.connection_id, config.model) == ("openai", "vlm-x")
    response = client.patch("/api/settings/region-detection", json={"connection_id": None})
    assert (response.json()["connection_id"], response.json()["model"]) == (None, None)
    with factory() as db:
        assert detection_settings.load(db, llm_defaults.LOCAL).model == "local-vlm"


def test_override_saved_before_adr_0063_keeps_builtin_connection(client: TestClient) -> None:
    """ADR-0063 より前はモデル名だけを保存できた(接続先は組み込みが既定)。片方だけの上書きは、
    もう片方を以前の既定で埋める。"""
    with client.app.state.session_factory() as db:
        _save(db, "assistant.model", "gpt-6-luna")
        config = assistant_settings.load(db)
    assert config.override == llm_defaults.TargetChoice("openai", "gpt-6-luna")
    body = client.get("/api/settings/assistant").json()
    assert (body["connection_id"], body["model"]) == ("openai", "gpt-6-luna")


def test_svg_author_default_follows_external_default(client: TestClient) -> None:
    general = "/api/settings/general"
    _put(client, {"external": {"connection_id": "openai", "model": "gpt-6-sol"}})
    assert client.get(general).json()["svg_author_model"]["default"] == "gpt-6-sol"
    # 外部の既定が組み込みの接続先でなければ、書き手は組み込みの既定(OpenAI の画像の API が呼ぶ)。
    other = add_llm_connection(client, "Relay")
    _put(client, {"external": {"connection_id": other, "model": "relay-model"}})
    assert (
        client.get(general).json()["svg_author_model"]["default"]
        == llm_defaults.BUILTIN_DEFAULT_MODEL
    )


# -- 画像の区分 ------------------------------------------------------------------------


def test_asset_locality(client: TestClient) -> None:
    uploaded = _upload(client)["id"]
    external_run = _generate(client, "a")
    local_run = _generate(client, "b")
    _set_provider(client, local_run["id"], "comfyui")
    factory = client.app.state.session_factory
    with factory() as db:
        locality_domain.save_provider_localities(db, {"comfyui": "local"})
        settings = client.app.state.settings

        def locality_of(asset_id: str) -> str:
            return llm_defaults.asset_locality(db, settings, db.get(Asset, uuid.UUID(asset_id)))

        assert locality_of(uploaded) == "external"
        assert locality_of(external_run["outputs"][0]["asset_id"]) == "external"
        local_asset = local_run["outputs"][0]["asset_id"]
        assert locality_of(local_asset) == "local"
        ids = [uuid.UUID(uploaded), uuid.UUID(external_run["outputs"][0]["asset_id"])]
        assert llm_defaults.assets_locality(db, settings, ids) == "external"
        assert llm_defaults.assets_locality(db, settings, [*ids, uuid.UUID(local_asset)]) == "local"

        # 待ち行列の SQL の条件も同じ Run を内部とみなす。
        local_ids = set(
            db.execute(
                select(Run.id).where(llm_defaults.local_run_condition(db, settings))
            ).scalars()
        )
        assert local_ids == {uuid.UUID(local_run["id"])}


# -- 自動タイトル・タグの以前の組 ----------------------------------------------------------


def test_annotation_profiles_before_adr_0063_are_read_as_overrides(client: TestClient) -> None:
    """`default` は外部の画像、`comfyui` は内部の画像の上書きになる。`comfyui` の null は
    「既定と同じ」だったので、送り先が変わらないよう `default` の組で埋める。"""
    local = add_llm_connection(client, "Ollama")
    with client.app.state.session_factory() as db:
        _save(
            db,
            "annotation.profiles",
            {
                "default": {
                    "llm": {"connection_id": "openai", "model": "gpt-llm"},
                    "vlm": {"connection_id": "openai", "model": "gpt-vlm"},
                },
                "comfyui": {"llm": {"connection_id": local, "model": "local-llm"}, "vlm": None},
            },
        )
    profiles = client.get("/api/settings/annotation").json()["profiles"]
    assert profiles == {
        "external": {
            "llm": {"connection_id": "openai", "model": "gpt-llm"},
            "vlm": {"connection_id": "openai", "model": "gpt-vlm"},
        },
        "local": {
            "llm": {"connection_id": local, "model": "local-llm"},
            "vlm": {"connection_id": "openai", "model": "gpt-vlm"},
        },
    }
