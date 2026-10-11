"""ComfyUI プロバイダーを使った `POST /api/runs` の通しテスト(ADR-0013)。

`TestClient` を使い、ワークフロー登録 → capabilities への反映 → Run の作成・実行 →
ワークフローを編集・削除しても Run の記録(`params`)が変わらないこと、を確認する。

実物の ComfyUI には一切接続しない。`app.providers.comfyui.provider.check_available` を
monkeypatch し、実行はレジストリの `comfyui` プロバイダーを `tests/comfyui_fake.py` の
偽サーバーを使うものに差し替える(`app.worker.runner.Runner` は実行のたびに
`registry.get(name)` で引くため、差し替え後の Run にも反映される)。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.providers.comfyui.client import ComfyUIClient
from app.providers.comfyui.provider import ComfyUIProvider
from tests.comfyui_fake import FakeComfyUI, unavailable_ws_connect
from tests.comfyui_graphs import T2I_GRAPH, clone
from tests.conftest import make_png_bytes, wait_for_run_terminal


@pytest.fixture
def client_with_comfyui(monkeypatch: pytest.MonkeyPatch, data_dir: Path) -> Iterator[TestClient]:
    """`COMFYUI_URL` を設定した状態でアプリを起動する(設定は起動時に固定されるため)。"""
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("FAKE_PROVIDER", "1")
    monkeypatch.setenv("COMFYUI_URL", "http://127.0.0.1:8188")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    from app.main import create_app

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


def _mark_available(monkeypatch: pytest.MonkeyPatch, *, available: bool = True) -> None:
    monkeypatch.setattr(
        "app.providers.comfyui.provider.check_available",
        lambda base_url, timeout=1.0: (available, None if available else "使用できません", {}),
    )


def _install_fake_comfyui(
    client: TestClient, fake: FakeComfyUI, *, ws_connect=None
) -> ComfyUIProvider:
    """レジストリの `comfyui` プロバイダーを、偽サーバーに接続するものへ差し替える。"""
    settings = client.app.state.settings
    session_factory = client.app.state.session_factory

    def client_factory() -> ComfyUIClient:
        return ComfyUIClient(
            settings.comfyui_url,
            http=fake.make_async_client(),
            ws_connect=ws_connect or unavailable_ws_connect(),
        )

    provider = ComfyUIProvider(
        settings.comfyui_url,
        session_factory,
        settings.comfyui_timeout_seconds,
        client_factory=client_factory,
    )
    client.app.state.registry.providers["comfyui"] = provider
    return provider


def _t2i_bindings_body() -> dict:
    return {
        "prompt": {"node": "6", "input": "text"},
        "negative_prompt": {"node": "7", "input": "text"},
        "seed": [{"node": "3", "input": "seed"}],
        "width": {"node": "5", "input": "width"},
        "height": {"node": "5", "input": "height"},
        "batch_size": {"node": "5", "input": "batch_size"},
        "outputs": ["9"],
    }


def _create_t2i_workflow(client: TestClient, name: str = "t2i サンプル") -> dict:
    response = client.post(
        "/api/comfyui/workflows",
        json={
            "name": name,
            "operation": "generate",
            "template": clone(T2I_GRAPH),
            "bindings": _t2i_bindings_body(),
            "exposed_params": [],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _configure_success(fake: FakeComfyUI) -> None:
    fake.set_history(
        "prompt-1",
        {
            "outputs": {
                "9": {"images": [{"filename": "out.png", "subfolder": "", "type": "output"}]}
            },
            "status": {"status_str": "success", "completed": True},
        },
    )
    fake.add_output_file("out.png", "", "output", make_png_bytes())


def test_comfyui_workflow_appears_in_capabilities(
    client_with_comfyui: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mark_available(monkeypatch)
    workflow = _create_t2i_workflow(client_with_comfyui)

    response = client_with_comfyui.get("/api/capabilities")
    assert response.status_code == 200, response.text
    body = response.json()

    comfyui_entry = next(p for p in body["providers"] if p["provider"] == "comfyui")
    assert comfyui_entry["available"] is True
    model = next(m for m in comfyui_entry["models"] if m["model"] == workflow["id"])
    assert model["label"] == workflow["name"]


def test_run_with_comfyui_provider_succeeds_and_creates_output_asset(
    client_with_comfyui: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mark_available(monkeypatch)
    workflow = _create_t2i_workflow(client_with_comfyui)
    fake = FakeComfyUI()
    _configure_success(fake)
    _install_fake_comfyui(client_with_comfyui, fake)

    response = client_with_comfyui.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": workflow["id"],
            "prompt": "a cat",
            "provider": "comfyui",
            "params": {},
        },
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]

    detail = wait_for_run_terminal(client_with_comfyui, run_id)
    assert detail["status"] == "succeeded", detail
    assert len(detail["outputs"]) == 1
    assert detail["model_label"] == workflow["name"]


def test_editing_or_deleting_workflow_does_not_change_past_run_params(
    client_with_comfyui: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mark_available(monkeypatch)
    workflow = _create_t2i_workflow(client_with_comfyui, name="編集前")
    fake = FakeComfyUI()
    _configure_success(fake)
    _install_fake_comfyui(client_with_comfyui, fake)

    response = client_with_comfyui.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": workflow["id"],
            "prompt": "a cat",
            "provider": "comfyui",
            "params": {},
        },
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]
    detail_before = wait_for_run_terminal(client_with_comfyui, run_id)
    assert detail_before["status"] == "succeeded"
    params_before = detail_before["params"]
    assert params_before["comfyui_workflow"]["name"] == "編集前"
    assert isinstance(params_before["comfyui_prompt"], dict)

    patch_response = client_with_comfyui.patch(
        f"/api/comfyui/workflows/{workflow['id']}", json={"name": "編集後"}
    )
    assert patch_response.status_code == 200, patch_response.text
    delete_response = client_with_comfyui.delete(f"/api/comfyui/workflows/{workflow['id']}")
    assert delete_response.status_code == 204

    detail_after = client_with_comfyui.get(f"/api/runs/{run_id}").json()
    assert detail_after["params"] == params_before
    assert detail_after["model_label"] == "編集前"


def test_run_with_deleted_workflow_returns_422(
    client_with_comfyui: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mark_available(monkeypatch)
    workflow = _create_t2i_workflow(client_with_comfyui)
    delete_response = client_with_comfyui.delete(f"/api/comfyui/workflows/{workflow['id']}")
    assert delete_response.status_code == 204

    response = client_with_comfyui.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": workflow["id"],
            "prompt": "a cat",
            "provider": "comfyui",
            "params": {},
        },
    )
    assert response.status_code == 422


def test_run_returns_409_when_comfyui_unavailable_and_creates_no_run(
    client_with_comfyui: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mark_available(monkeypatch, available=False)
    workflow = _create_t2i_workflow(client_with_comfyui)

    before = client_with_comfyui.get("/api/runs").json()["items"]

    response = client_with_comfyui.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": workflow["id"],
            "prompt": "a cat",
            "provider": "comfyui",
            "params": {},
        },
    )
    assert response.status_code == 409

    after = client_with_comfyui.get("/api/runs").json()["items"]
    assert len(after) == len(before)


def test_run_rejects_client_supplied_comfyui_prompt_param(
    client_with_comfyui: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mark_available(monkeypatch)
    workflow = _create_t2i_workflow(client_with_comfyui)

    response = client_with_comfyui.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": workflow["id"],
            "prompt": "a cat",
            "provider": "comfyui",
            "params": {"comfyui_prompt": {"1": {"class_type": "X", "inputs": {}}}},
        },
    )
    assert response.status_code == 422


def _output_blob_key(client: TestClient, asset_id: str) -> str:
    import uuid

    from app.domain.models import Asset

    with client.app.state.session_factory() as session:
        asset = session.get(Asset, uuid.UUID(asset_id))
        assert asset is not None
        return asset.blob_key


def test_comfyui_output_is_saved_under_workflow_name_folder(
    client_with_comfyui: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0026: ComfyUI の出力は `assets/comfyui/{ワークフロー名}/{YYYY-MM}/` に保存する。
    Run の model(ワークフローの id)ではなく、保存時点の名前を正規化して使う。"""
    import re

    _mark_available(monkeypatch)
    workflow = _create_t2i_workflow(client_with_comfyui, name="Anime 線画 v2/final")
    fake = FakeComfyUI()
    _configure_success(fake)
    _install_fake_comfyui(client_with_comfyui, fake)

    response = client_with_comfyui.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": workflow["id"],
            "prompt": "a cat",
            "provider": "comfyui",
            "params": {},
        },
    )
    assert response.status_code == 202, response.text
    detail = wait_for_run_terminal(client_with_comfyui, response.json()["id"])
    assert detail["status"] == "succeeded", detail

    key = _output_blob_key(client_with_comfyui, detail["outputs"][0]["asset_id"])
    assert re.fullmatch(
        r"assets/comfyui/Anime_v2_final/\d{4}-\d{2}/\d{8}-\d{6}_[0-9a-f]{8}\.png", key
    ), key


# -- 最終プロンプト(PE の出力。ADR-0030) --------------------------------------------


def _create_pe_workflow(client: TestClient) -> dict:
    graph = clone(T2I_GRAPH)
    graph["20"] = {"class_type": "PreviewAny", "inputs": {"source": ["6", 0]}}
    response = client.post(
        "/api/comfyui/workflows",
        json={
            "name": "PE 付き",
            "operation": "generate",
            "template": graph,
            "bindings": {**_t2i_bindings_body(), "final_prompt": "20"},
            "exposed_params": [],
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["bindings"]["final_prompt"] == "20"
    return response.json()


def _post_comfy_run(client: TestClient, workflow: dict, params: dict | None = None):  # noqa: ANN202
    return client.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": workflow["id"],
            "prompt": "a cat",
            "provider": "comfyui",
            "params": params or {},
        },
    )


def test_workflow_rejects_missing_final_prompt_node(
    client_with_comfyui: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mark_available(monkeypatch)
    response = client_with_comfyui.post(
        "/api/comfyui/workflows",
        json={
            "name": "bad",
            "operation": "generate",
            "template": clone(T2I_GRAPH),
            "bindings": {**_t2i_bindings_body(), "final_prompt": "999"},
            "exposed_params": [],
        },
    )
    assert response.status_code == 422


def test_successful_run_records_text_outputs(
    client_with_comfyui: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mark_available(monkeypatch)
    workflow = _create_pe_workflow(client_with_comfyui)
    fake = FakeComfyUI()
    fake.set_history(
        "prompt-1",
        {
            "outputs": {
                "9": {"images": [{"filename": "out.png", "subfolder": "", "type": "output"}]},
                "20": {"text": ["A warm, cozy room"]},
            },
            "status": {"status_str": "success", "completed": True},
        },
    )
    fake.add_output_file("out.png", "", "output", make_png_bytes())
    _install_fake_comfyui(client_with_comfyui, fake)

    response = _post_comfy_run(client_with_comfyui, workflow)
    assert response.status_code == 202, response.text
    detail = wait_for_run_terminal(client_with_comfyui, response.json()["id"])
    assert detail["status"] == "succeeded", detail
    assert detail["params"]["comfyui_final_prompt"] == "20"
    expected = [
        {
            "role": "final_prompt",
            "output_index": None,
            "node_id": "20",
            "class_type": "PreviewAny",
            "title": None,
            "text": "A warm, cozy room",
            "truncated": False,
        }
    ]
    assert detail["text_outputs"] == expected

    listed = client_with_comfyui.get("/api/runs").json()["items"]
    assert {item["id"]: item["text_outputs"] for item in listed}[detail["id"]] == expected

    asset_id = detail["outputs"][0]["asset_id"]
    asset = client_with_comfyui.get(f"/api/assets/{asset_id}").json()
    assert asset["produced_by_run"]["text_outputs"] == expected


def test_final_prompt_with_nul_still_succeeds(
    client_with_comfyui: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0030 2026-10-01 改訂: NUL を含む出力でも、完了時の commit が失敗せず
    (PostgreSQL の JSONB は `\\u0000` を受け付けない)Run は成功になる。"""
    _mark_available(monkeypatch)
    workflow = _create_pe_workflow(client_with_comfyui)
    fake = FakeComfyUI()
    fake.set_history(
        "prompt-1",
        {
            "outputs": {
                "9": {"images": [{"filename": "out.png", "subfolder": "", "type": "output"}]},
                "20": {"text": ["A warm\x00, cozy room"]},
            },
            "status": {"status_str": "success", "completed": True},
        },
    )
    fake.add_output_file("out.png", "", "output", make_png_bytes())
    _install_fake_comfyui(client_with_comfyui, fake)

    response = _post_comfy_run(client_with_comfyui, workflow)
    assert response.status_code == 202, response.text
    detail = wait_for_run_terminal(client_with_comfyui, response.json()["id"])
    assert detail["status"] == "succeeded", detail
    assert detail["text_outputs"][0]["text"] == "A warm, cozy room"


def test_failed_run_has_null_text_outputs(
    client_with_comfyui: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mark_available(monkeypatch)
    workflow = _create_pe_workflow(client_with_comfyui)
    fake = FakeComfyUI()
    # 画像が無くテキストだけ → comfyuiNoOutput で失敗し、テキストも書かない。
    fake.set_history(
        "prompt-1",
        {
            "outputs": {"20": {"text": ["only text"]}},
            "status": {"status_str": "success", "completed": True},
        },
    )
    _install_fake_comfyui(client_with_comfyui, fake)

    response = _post_comfy_run(client_with_comfyui, workflow)
    assert response.status_code == 202, response.text
    detail = wait_for_run_terminal(client_with_comfyui, response.json()["id"])
    assert detail["status"] == "failed", detail
    assert detail["error_code"] == "comfyuiNoOutput"
    assert detail["text_outputs"] is None


def test_run_rejects_client_supplied_comfyui_final_prompt_param(
    client_with_comfyui: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mark_available(monkeypatch)
    workflow = _create_pe_workflow(client_with_comfyui)
    response = _post_comfy_run(client_with_comfyui, workflow, {"comfyui_final_prompt": "20"})
    assert response.status_code == 422
