"""ADR-0056「名前の候補」: LLM へ送る文面、応答の読み取り、設定、
候補の API(無効時、外部・ローカルの接続先、`ai.before_send` の目的 `group_naming`、拒否・失敗)。
実 API は呼ばない(LLM は `FakeStructuredEngine`)。
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.assistant.engine import AssistantEngineError
from app.assistant.structured import FakeStructuredEngine
from app.domain import group_naming as naming
from app.domain import group_naming_settings as naming_settings
from app.domain import llm_connections
from app.domain.group_suggestions import NamingCluster, NamingInput
from tests.conftest import login_as
from tests.test_group_suggestions import _add_tag, _group, _setup, _suggestions
from tests.test_mods_hooks import _Hook, _install

SETTINGS = "/api/settings/group-naming"
NAMES = "/api/asset-groups/suggestions/names"
CONNECTIONS = "/api/settings/llm-connections"


# -- 文面と読み取り ----------------------------------------------------------------------


def _naming_input() -> NamingInput:
    members = tuple(
        frozenset({"red hair", "nude", "aru (blue archive)", "blue archive"} if i < 4 else {"x"})
        for i in range(6)
    )
    counts: dict[str, int] = {}
    for tags in members:
        for tag in tags:
            counts[tag] = counts.get(tag, 0) + 1
    return NamingInput(
        clusters=(NamingCluster("s1", 6, counts, members),),
        global_counts={**counts, "1girl": 30, "other": 20},
        total=40,
        categories={"aru (blue archive)": 4, "blue archive": 3},
    )


def test_build_input_external_drops_sensitive_tags() -> None:
    data = _naming_input()
    external = naming.build_input(data, external=True)
    local = naming.build_input(data, external=False)
    assert "### 集まり 1(6枚)" in external
    assert "- red hair: 集まり 67% / 全体 10%" in external
    assert "aru (blue archive) [キャラ]" in external
    assert "blue archive [作品]" in external
    assert "作品(キャラクター・作品のタグから、画像ごとに集計): blue archive 67%" in external
    assert "nude" not in external
    assert "nude" in local
    assert external.startswith("画像生成ツールの利用者が")
    assert "各集まりに候補を3つ、良い順に。" in external


def test_build_input_franchise_skips_sensitive_qualifiers() -> None:
    members = tuple(frozenset({"someone (nsfw)"}) for _ in range(5))
    data = NamingInput(
        clusters=(NamingCluster("s1", 5, {"someone (nsfw)": 5}, members),),
        global_counts={"someone (nsfw)": 5, "1girl": 20},
        total=25,
        categories={"someone (nsfw)": 4},
    )
    assert "nsfw" not in naming.build_input(data, external=True)
    assert "nsfw 100%" in naming.build_input(data, external=False)


def test_build_input_fills_up_to_limit_after_dropping() -> None:
    """外した分は次のタグで埋める(上位 15 個)。"""
    tags = [f"tag{i:02d}" for i in range(20)]
    sensitive = ["nude", "nipples", "topless"]
    members = tuple(frozenset(tags + sensitive) for _ in range(10))
    counts = {tag: 10 for tag in tags + sensitive}
    data = NamingInput(
        clusters=(NamingCluster("s1", 10, counts, members),),
        global_counts={**counts, "1girl": 100, "o1": 50, "o2": 50, "o3": 50},
        total=110,
        categories={},
    )
    text = naming.build_input(data, external=True)
    lines = [line for line in text.splitlines() if line.startswith("- tag")]
    assert len(lines) == naming.TAG_LIMIT
    assert not any(word in text for word in sensitive)


def test_interpret_cleans_candidates() -> None:
    data = {
        "clusters": [
            {"id": 1, "candidates": ["  赤い  髪 ", "赤い 髪", "", "x" * 31, "青", "緑", "黄"]},
            {"id": 1, "candidates": ["重複"]},
            {"id": 3, "candidates": ["範囲外"]},
            {"id": True, "candidates": ["真偽値"]},
            {"id": 2, "candidates": "not a list"},
            "bad",
        ]
    }
    assert naming.interpret(data, 2) == {1: ["赤い 髪", "青", "緑"]}
    assert naming.interpret({"x": 1}, 2) == {}
    assert naming.interpret(None, 2) == {}


def test_fake_response_shape() -> None:
    fake = naming.fake_response(2)
    assert [c["id"] for c in fake["clusters"]] == [1, 2]
    assert all(len(c["candidates"]) == 3 for c in fake["clusters"])


# -- 設定 ------------------------------------------------------------------------------


def _enable(client: TestClient, **extra: Any) -> None:
    response = client.patch(SETTINGS, json={"enabled": True, **extra})
    assert response.status_code == 200, response.text


def _engine(client: TestClient) -> FakeStructuredEngine:
    engine = client.app.state.assistant.structured
    assert isinstance(engine, FakeStructuredEngine)
    return engine


def test_settings_defaults_and_validation(client: TestClient) -> None:
    body = client.get(SETTINGS).json()
    assert body["enabled"] is False
    assert body["connection_id"] == "openai"
    assert body["model"] == naming_settings.DEFAULT_MODEL
    assert body["usable"] is False and body["unusable_reason"] == "disabled"
    for bad in ({"enabled": None}, {"connection_id": "missing"}, {"model": " "}):
        assert client.patch(SETTINGS, json=bad).status_code == 422, bad
    assert client.patch(SETTINGS, json={"enabled": True, "model": ""}).status_code == 422
    assert client.get(SETTINGS).json()["enabled"] is False
    _enable(client, model=" llm-x ")
    body = client.get(SETTINGS).json()
    assert body["enabled"] is True and body["model"] == "llm-x" and body["usable"] is True
    with client.app.state.session_factory() as db:
        assert "group_naming" in llm_connections.features_using(db)["openai"]


def test_settings_update_requires_admin(client_oidc: TestClient) -> None:
    login_as(client_oidc, "user@example.com")
    assert client_oidc.get(SETTINGS).status_code == 200
    assert client_oidc.patch(SETTINGS, json={"enabled": True}).status_code == 403
    login_as(client_oidc, "admin@example.com")
    assert client_oidc.patch(SETTINGS, json={"enabled": True}).status_code == 200


# -- 候補の API ------------------------------------------------------------------------


def _local_connection(client: TestClient, locality: str) -> str:
    created = client.post(
        CONNECTIONS,
        json={
            "name": f"llm-{locality}",
            "base_url": "http://127.0.0.1:11434/v1",
            "api_style": "chat",
            "locality": locality,
        },
    )
    assert created.status_code == 201, created.text
    return created.json()["connections"][-1]["id"]


def _prepare(client: TestClient) -> tuple[list[str], list[str], list[str]]:
    """新しいグループの提案2つと、既存のグループへの追加の提案1つ。"""
    first, second, third, _lonely = _setup(client, [8, 6, 6])
    _group(client, "Existing", first[:5])
    _add_tag(client, second, "red hair")
    _add_tag(client, second, "nude")
    _add_tag(client, third, "blue sky")
    _add_tag(client, first + second + third, "1girl")
    return first, second, third


def test_names_rejected_when_disabled(client: TestClient) -> None:
    _prepare(client)
    response = client.post(NAMES, json={})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "disabled"
    assert _engine(client).requests == []


def test_names_require_embeddings(client: TestClient) -> None:
    _enable(client)
    response = client.post(NAMES, json={})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "embeddings_unavailable"


def test_names_with_fake_engine_skip_target_groups(client: TestClient) -> None:
    first, second, third = _prepare(client)
    _enable(client)
    body = _suggestions(client)
    new_ids = [s["id"] for s in body["suggestions"] if s["target_group"] is None]
    target_ids = [s["id"] for s in body["suggestions"] if s["target_group"] is not None]
    assert len(new_ids) == 2 and len(target_ids) == 1

    response = client.post(NAMES, json={})
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [i["suggestion_id"] for i in items] == new_ids
    assert all(len(i["candidates"]) == 3 for i in items)
    request = _engine(client).requests[-1]
    assert request.schema_name == naming.SCHEMA_NAME
    assert request.input_text.count("### 集まり") == 2

    # 選んだ提案だけ。既存のグループへの追加の提案を選んでも送らない。
    response = client.post(NAMES, json={"suggestion_ids": [new_ids[1], *target_ids]})
    assert [i["suggestion_id"] for i in response.json()["items"]] == [new_ids[1]]
    assert _engine(client).requests[-1].input_text.count("### 集まり") == 1

    # 送る提案が無ければ LLM を呼ばない。
    count = len(_engine(client).requests)
    response = client.post(NAMES, json={"suggestion_ids": target_ids})
    assert response.status_code == 200 and response.json()["items"] == []
    assert len(_engine(client).requests) == count


def test_names_external_connection_drops_sensitive_tags(client: TestClient) -> None:
    _prepare(client)
    _enable(client)  # 組み込みの接続先は既定で外部。
    hook = _Hook({"decision": "allow"})
    _install(client, hook, filters={"purposes": ["group_naming"]})
    response = client.post(NAMES, json={})
    assert response.status_code == 200, response.text
    sent = _engine(client).requests[-1].input_text
    assert "red hair" in sent and "nude" not in sent
    subject = hook.events[0]["subject"]
    assert subject["purpose"] == "group_naming"
    assert subject["target"]["locality"] == "external"
    assert subject["target"]["connection"]["id"] == "openai"
    texts = {part["role"]: part["text"] for part in subject["text"]}
    assert texts["user"] == sent
    assert "nude" not in texts["user"]
    assert subject["params"]["sensitive_tags_removed"] is True
    assert subject["images"] == []


def test_names_local_connection_sends_all_tags(client: TestClient) -> None:
    _prepare(client)
    _enable(client, connection_id=_local_connection(client, "local"))
    hook = _Hook({"decision": "allow"})
    _install(client, hook)
    response = client.post(NAMES, json={})
    assert response.status_code == 200, response.text
    sent = _engine(client).requests[-1].input_text
    assert "nude" in sent
    subject = hook.events[0]["subject"]
    assert subject["target"]["locality"] == "local"
    assert subject["params"]["sensitive_tags_removed"] is False


def test_names_external_custom_connection_drops_sensitive_tags(client: TestClient) -> None:
    _prepare(client)
    _enable(client, connection_id=_local_connection(client, "external"))
    response = client.post(NAMES, json={})
    assert response.status_code == 200, response.text
    assert "nude" not in _engine(client).requests[-1].input_text


def test_names_denied_by_hook(client: TestClient) -> None:
    _prepare(client)
    _enable(client)
    hook = _Hook({"decision": "deny", "reason": "送れません"})
    _install(client, hook, filters={"purposes": ["group_naming"]})
    response = client.post(NAMES, json={})
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "hook_denied" and "送れません" in detail["message"]
    assert _engine(client).requests == []


def test_names_not_checked_for_other_purposes(client: TestClient) -> None:
    _prepare(client)
    _enable(client)
    hook = _Hook({"decision": "deny", "reason": "x"})
    _install(client, hook, filters={"purposes": ["assistant"]})
    assert client.post(NAMES, json={}).status_code == 200
    assert hook.events == []


def test_names_llm_error_and_bad_response(client: TestClient) -> None:
    _prepare(client)
    _enable(client)
    _engine(client).responses.append(AssistantEngineError("boom"))
    response = client.post(NAMES, json={})
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "llm_error"
    # 形の合わない応答なら、候補は空(ページはそのまま使える)。
    _engine(client).responses.append({"clusters": "nope"})
    response = client.post(NAMES, json={})
    assert response.status_code == 200 and response.json()["items"] == []


def test_names_unsupported_connection(client: TestClient) -> None:
    _prepare(client)
    _enable(client, connection_id=_local_connection(client, "local"))

    class _RealLike(FakeStructuredEngine):
        requires_target = True

    client.app.state.assistant.structured = _RealLike()
    response = client.post(NAMES, json={})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "unsupported"
    assert client.get(SETTINGS).json()["usable"] is False
