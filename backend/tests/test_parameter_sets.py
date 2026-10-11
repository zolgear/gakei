"""パラメーターセットの CRUD、論理削除、検証エラー(ADR-0040)。

本人だけに見えること(他人の id は 404、一覧に出ない)は、全ルートを機械的に確かめる
`tests/test_visibility.py` で確かめる。
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.domain.models import ParameterSet

_BASE = "/api/parameter-sets"


def _create(client: TestClient, **body: object) -> dict:
    payload = {"name": "セットA", "provider": "sdwebui", "params": {}} | body
    response = client.post(_BASE, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_create_get_and_list(client: TestClient) -> None:
    created = _create(
        client,
        name="  夕景の設定  ",
        model="model-a",
        prompt="a {red|blue} bird",
        params={"steps": 28, "cfg_scale": 6.5, "hires_fix": True, "sampler_name": "Euler a"},
    )
    assert created["name"] == "夕景の設定"  # 前後の空白は落とす
    assert created["provider"] == "sdwebui"
    assert created["model"] == "model-a"
    assert created["prompt"] == "a {red|blue} bird"
    # 型はそのまま戻る(bool を int に、int を float にしない)。
    assert created["params"] == {
        "steps": 28,
        "cfg_scale": 6.5,
        "hires_fix": True,
        "sampler_name": "Euler a",
    }
    assert isinstance(created["params"]["steps"], int)
    assert created["params"]["hires_fix"] is True

    fetched = client.get(f"{_BASE}/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json() == created

    listed = client.get(_BASE).json()["items"]
    assert [s["id"] for s in listed] == [created["id"]]


def test_model_and_prompt_are_optional(client: TestClient) -> None:
    created = _create(client, provider="openai", params={"quality": "low"})
    assert created["model"] is None
    assert created["prompt"] is None


def test_provider_is_not_checked_against_registry(client: TestClient) -> None:
    """今は無効なプロバイダーでも保存できる(後で無効になることもあるため。ADR-0040)。"""
    created = _create(client, provider="not-registered")
    assert created["provider"] == "not-registered"


def test_list_is_ordered_by_updated_at_desc(client: TestClient) -> None:
    first = _create(client, name="先")
    second = _create(client, name="後")
    ids = [s["id"] for s in client.get(_BASE).json()["items"]]
    assert ids.index(second["id"]) < ids.index(first["id"])

    client.patch(f"{_BASE}/{first['id']}", json={"name": "更新"})
    ids = [s["id"] for s in client.get(_BASE).json()["items"]]
    assert ids.index(first["id"]) < ids.index(second["id"])


def test_update_only_sent_fields(client: TestClient) -> None:
    created = _create(client, model="model-a", prompt="cat", params={"steps": 20})

    renamed = client.patch(f"{_BASE}/{created['id']}", json={"name": "新しい名前"}).json()
    assert renamed["name"] == "新しい名前"
    assert renamed["model"] == "model-a"
    assert renamed["prompt"] == "cat"
    assert renamed["params"] == {"steps": 20}

    # 上書き(フォームの「既存のセットに上書き」)。model と prompt は null で「保存しない」に戻る。
    overwritten = client.patch(
        f"{_BASE}/{created['id']}",
        json={"provider": "openai", "model": None, "prompt": None, "params": {"quality": "high"}},
    ).json()
    assert overwritten["name"] == "新しい名前"
    assert overwritten["provider"] == "openai"
    assert overwritten["model"] is None
    assert overwritten["prompt"] is None
    assert overwritten["params"] == {"quality": "high"}


def test_delete_is_logical(client: TestClient) -> None:
    created = _create(client)
    assert client.delete(f"{_BASE}/{created['id']}").status_code == 204
    assert created["id"] not in [s["id"] for s in client.get(_BASE).json()["items"]]
    assert client.get(f"{_BASE}/{created['id']}").status_code == 404
    assert client.patch(f"{_BASE}/{created['id']}", json={"name": "x"}).status_code == 404
    assert client.delete(f"{_BASE}/{created['id']}").status_code == 404

    # 行は残り、deleted_at が入る。
    with client.app.state.session_factory() as db:
        row = db.get(ParameterSet, uuid.UUID(created["id"]))
        assert row is not None
        assert row.deleted_at is not None


def test_unknown_id_is_404(client: TestClient) -> None:
    missing = uuid.uuid4()
    assert client.get(f"{_BASE}/{missing}").status_code == 404
    assert client.patch(f"{_BASE}/{missing}", json={"name": "x"}).status_code == 404
    assert client.delete(f"{_BASE}/{missing}").status_code == 404


def test_server_only_params_are_rejected(client: TestClient) -> None:
    for key in ("comfyui_workflow", "comfyui_seed", "sdwebui_seed", "sdwebui_request"):
        response = client.post(_BASE, json={"name": "x", "provider": "sdwebui", "params": {key: 1}})
        assert response.status_code == 422, (key, response.text)
        assert key in response.json()["detail"]

    created = _create(client)
    response = client.patch(f"{_BASE}/{created['id']}", json={"params": {"sdwebui_seed": 1}})
    assert response.status_code == 422
    # 拒んだ更新は何も変えない。
    assert client.get(f"{_BASE}/{created['id']}").json()["params"] == {}


def test_validation_errors(client: TestClient) -> None:
    def post(body: dict) -> int:
        payload = {"name": "x", "provider": "sdwebui", "params": {}} | body
        return client.post(_BASE, json=payload).status_code

    assert post({"name": ""}) == 422
    assert post({"name": "   "}) == 422
    assert post({"name": "a" * 101}) == 422
    assert post({"name": "a" * 100}) == 201
    assert post({"provider": ""}) == 422
    assert post({"provider": "p" * 33}) == 422
    assert post({"model": ""}) == 422
    assert post({"prompt": "p" * 32_001}) == 422
    # 入れ子・null の値は受け付けない(フォームの値は文字列・数値・真偽値だけ)。
    assert post({"params": {"a": {"b": 1}}}) == 422
    assert post({"params": {"a": [1]}}) == 422
    assert post({"params": {"a": None}}) == 422
    assert post({"params": {"": 1}}) == 422
    assert post({"params": {f"k{i}": i for i in range(201)}}) == 422

    created = _create(client)
    assert client.patch(f"{_BASE}/{created['id']}", json={"name": ""}).status_code == 422
    assert client.patch(f"{_BASE}/{created['id']}", json={"name": "  "}).status_code == 422


def test_none_mode_records_no_owner(client: TestClient) -> None:
    """個人モードでは作成者は null(ADR-0025。認証モードに切り替えると管理者だけに見える)。"""
    created = _create(client)
    with client.app.state.session_factory() as db:
        row = db.get(ParameterSet, uuid.UUID(created["id"]))
        assert row is not None
        assert row.created_by_user_id is None
