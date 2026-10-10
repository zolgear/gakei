"""SD WebUI プロバイダーを使った `POST /api/runs` の通しテスト(ADR-0038)。Generate。
Edit(img2img)の通しテストは `test_sdwebui_img2img.py`。

画面と同じく `PUT /api/sdwebui/connection` で接続し(実行レーンもそこで起動する)、
プロバイダーの通信先だけを偽の WebUI(`tests/sdwebui_fake.py`)に差し替える。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import Asset
from tests.conftest import wait_for_run_terminal
from tests.sdwebui_fake import FakeSdWebui, install_fake_factories

LOCAL_URL = "http://127.0.0.1:7860"


def _connect(client: TestClient, monkeypatch: pytest.MonkeyPatch, fake: FakeSdWebui) -> None:
    install_fake_factories(monkeypatch, fake)
    response = client.put("/api/sdwebui/connection", json={"url": LOCAL_URL})
    assert response.status_code == 200, response.text


def _post_run(client: TestClient, model: str = "model-a", **params) -> object:
    return client.post(
        "/api/runs",
        json={
            "operation": "generate",
            "provider": "sdwebui",
            "model": model,
            "prompt": "a cat",
            "params": params,
        },
    )


def test_txt2img_succeeds_and_records_request(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeSdWebui()
    _connect(client, monkeypatch, fake)

    response = _post_run(client, "model-b", n=2, size="512x768", steps=12, seed=99)
    assert response.status_code == 202, response.text
    detail = wait_for_run_terminal(client, response.json()["id"])
    assert detail["status"] == "succeeded", detail
    assert len(detail["outputs"]) == 2
    params = detail["params"]
    assert params["sdwebui_seed"] == 99
    request = params["sdwebui_request"]
    assert request["override_settings"]["sd_model_checkpoint"] == "model-b"
    assert request["override_settings_restore_afterwards"] is True
    assert request["save_images"] is False
    assert (request["width"], request["height"], request["batch_size"]) == (512, 768, 2)
    assert detail["provider_request_id"] == params["sdwebui_task_id"]
    # 実際に送った本文は記録と同じ(ADR-0003 ルール4)
    assert fake.txt2img_bodies == [request]

    # 原本の保存先は assets/sdwebui/{チェックポイント名}/...(ADR-0026)
    session_factory = client.app.state.session_factory
    with session_factory() as db:
        keys = db.execute(select(Asset.blob_key).where(Asset.produced_by_run_id.isnot(None)))
        assert all(k.startswith("assets/sdwebui/model-b/") for (k,) in keys)


def test_seed_is_decided_by_server(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeSdWebui()
    _connect(client, monkeypatch, fake)
    response = _post_run(client)
    assert response.status_code == 202, response.text
    detail = wait_for_run_terminal(client, response.json()["id"])
    seed = detail["params"]["sdwebui_seed"]
    assert 0 <= seed < 2**32
    assert fake.txt2img_bodies[0]["seed"] == seed


@pytest.mark.parametrize(
    ("flavor", "vae", "expected"),
    [
        ("forge", None, {"forge_additional_modules": []}),
        ("forge", "vae-a.safetensors", {"forge_additional_modules": ["vae-a.safetensors"]}),
        ("a1111", None, {"sd_vae": "None"}),
        ("a1111", "vae-b.safetensors", {"sd_vae": "vae-b.safetensors"}),
    ],
)
def test_vae_is_sent_per_flavor(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    flavor: str,
    vae: str | None,
    expected: dict,
) -> None:
    fake = FakeSdWebui(flavor=flavor)
    _connect(client, monkeypatch, fake)
    params = {"vae": vae} if vae else {}
    response = _post_run(client, **params)
    assert response.status_code == 202, response.text
    detail = wait_for_run_terminal(client, response.json()["id"])
    assert detail["status"] == "succeeded", detail
    assert fake.txt2img_bodies[0]["override_settings"] == {
        "sd_model_checkpoint": "model-a",
        "CLIP_stop_at_last_layers": 1,
        **expected,
    }


def test_model_mismatch_fails_without_assets(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeSdWebui()
    _connect(client, monkeypatch, fake)
    # 一覧には載っているが、WebUI が読み込めず今のもの(model-a)で描いた場合
    original = fake._txt2img

    def drawn_with_current(request):  # noqa: ANN001, ANN202
        fake.checkpoints = [c for c in fake.checkpoints if c["model_name"] == "model-a"]
        try:
            return original(request)
        finally:
            fake.checkpoints = FakeSdWebui().checkpoints

    monkeypatch.setattr(fake, "_txt2img", drawn_with_current)

    response = _post_run(client, "model-b")
    assert response.status_code == 202, response.text
    detail = wait_for_run_terminal(client, response.json()["id"])
    assert detail["status"] == "failed"
    assert detail["error_code"] == "sdwebuiModelMismatch"
    assert detail["outputs"] == []


def test_unknown_checkpoint_is_422(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _connect(client, monkeypatch, FakeSdWebui())
    response = _post_run(client, "model-z")
    assert response.status_code == 422


@pytest.mark.parametrize(
    "params",
    [
        {"sdwebui_seed": 1},
        {"sdwebui_request": {"prompt": "x"}},
        {"sdwebui_task_id": "gakei-x"},
        {"size": "auto"},
        {"size": "2048x256"},  # 縦横比が 4:1 を超える
        {"size": "2056x1024"},  # 長辺 2048 を超える
        {"n": 9},
        {"steps": 0},
        {"vae": "vae-z.safetensors"},
        {"sampler_name": "No Such Sampler"},
        {"seed": -1},
    ],
)
def test_invalid_params_are_422(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, params: dict
) -> None:
    fake = FakeSdWebui()
    _connect(client, monkeypatch, fake)
    response = _post_run(client, **params)
    assert response.status_code == 422, response.text
    assert fake.txt2img_bodies == []


def test_small_sizes_are_allowed(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """OpenAI の総画素数の下限(655,360)ではなく、SD WebUI の制約で検証する。"""
    _connect(client, monkeypatch, FakeSdWebui())
    response = _post_run(client, size="512x512")
    assert response.status_code == 202, response.text


def test_size_not_multiple_of_8_is_422(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """8 の倍数でないサイズは Run を作らずに 422(ADR-0038 2章 2026-10-10 改訂。フォームは
    切り捨ててから送る)。"""
    fake = FakeSdWebui()
    _connect(client, monkeypatch, fake)
    for size in ("803x601", "800x601", "803x600"):
        response = _post_run(client, size=size)
        assert response.status_code == 422, response.text
    assert fake.txt2img_bodies == []


def test_sent_size_matches_asset_size(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """8 の倍数のサイズなら、送った値・Run の記録・画像の寸法が一致する(ADR-0038 2章)。"""
    fake = FakeSdWebui()
    fake.honor_size = True
    _connect(client, monkeypatch, fake)
    response = _post_run(client, size="800x600")
    assert response.status_code == 202, response.text
    detail = wait_for_run_terminal(client, response.json()["id"])
    assert detail["status"] == "succeeded", detail
    assert detail["params"]["size"] == "800x600"
    request = detail["params"]["sdwebui_request"]
    assert (request["width"], request["height"]) == (800, 600)
    assert "Size: 800x600" in detail["usage"]["infotext"]
    with client.app.state.session_factory() as db:
        sizes = db.execute(
            select(Asset.width, Asset.height).where(Asset.produced_by_run_id.isnot(None))
        ).all()
    assert [tuple(s) for s in sizes] == [(800, 600)]


def test_clip_skip_is_sent_every_time(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeSdWebui()
    _connect(client, monkeypatch, fake)
    response = _post_run(client, clip_skip=2)
    assert response.status_code == 202, response.text
    detail = wait_for_run_terminal(client, response.json()["id"])
    assert detail["status"] == "succeeded", detail
    assert fake.txt2img_bodies[-1]["override_settings"]["CLIP_stop_at_last_layers"] == 2
    assert fake.txt2img_bodies[-1]["override_settings_restore_afterwards"] is True
    # 範囲外は Run を作らずに 422
    for value in (0, 13):
        assert _post_run(client, clip_skip=value).status_code == 422
    assert len(fake.txt2img_bodies) == 1


def test_unavailable_is_409(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeSdWebui()
    _connect(client, monkeypatch, fake)
    fake.api_enabled = False
    provider = client.app.state.registry.get("sdwebui")
    provider.invalidate_cache()
    response = _post_run(client)
    assert response.status_code == 409
