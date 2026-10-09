"""SD WebUI の LoRA の一覧(ADR-0038 8章)。偽の WebUI だけを使い、LoRA はすべて架空のもの。

- メタ情報の解析(JSON 文字列、壊れた JSON、無いとき)、ベースモデルの判定、トリガーの集計。
- `GET /api/sdwebui/loras` が name・alias・base_model・trigger_tags 以外(`path`、
  `ss_dataset_dirs`、`ss_training_comment`、データセットのフォルダー名など)を返さないこと。
- `POST /api/sdwebui/refresh` で `refresh-loras` を呼び、LoRA のキャッシュも捨てること。
"""

from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.providers.sdwebui.client import SdWebuiClient, SdWebuiError
from app.providers.sdwebui.loras import (
    TRIGGER_TAGS_MAX,
    detect_base_model,
    parse_lora,
    parse_loras,
    trigger_tags,
)
from tests.sdwebui_fake import (
    FAKE_PATH_ROOT,
    LORA_SECRET_MARKERS,
    FakeSdWebui,
    install_fake_factories,
    unreachable_transport,
)

LOCAL_URL = "http://127.0.0.1:7860"


# -- 解析 ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        ({"ss_base_model_version": "sdxl_base_v1-0"}, "sdxl"),
        ({"ss_base_model_version": "sd_v1"}, "sd1"),
        ({"ss_base_model_version": "sd_v2_768_v"}, None),
        ({"modelspec.architecture": "stable-diffusion-xl-v1-base/lora"}, "sdxl"),
        ({"modelspec.architecture": "stable-diffusion-v1/lora"}, "sd1"),
        ({"modelspec.architecture": "flux-1-dev/lora"}, None),
        # ss_base_model_version を優先する
        (
            {
                "ss_base_model_version": "sd_v1",
                "modelspec.architecture": "stable-diffusion-xl-v1-base/lora",
            },
            "sd1",
        ),
        ({"ss_base_model_version": "", "modelspec.architecture": "stable-diffusion-v1"}, "sd1"),
        ({"ss_v2": "True"}, None),
        ({}, None),
    ],
)
def test_detect_base_model(metadata: dict, expected: str | None) -> None:
    assert detect_base_model(metadata) == expected


def test_trigger_tags_sum_across_folders_and_sort() -> None:
    metadata = {
        "ss_tag_frequency": {
            "10_folder-x": {"style_a": 12, "1girl": 10, "outdoors": 3},
            "5_folder-y": {"1girl": 4, "long_hair": 6, "outdoors": "2"},
        }
    }
    assert trigger_tags(metadata) == ("1girl", "style a", "long hair", "outdoors")


def test_trigger_tags_from_json_string() -> None:
    metadata = {"ss_tag_frequency": json.dumps({"img": {"tag_b": 1, "tag_a": 1, "tag_c": 5}})}
    # 同じ数は名前の順
    assert trigger_tags(metadata) == ("tag c", "tag a", "tag b")


@pytest.mark.parametrize(
    "value",
    ["{not json", "", "[]", "null", 42, ["a"], {"img": "{broken"}, {"img": ["a", "b"]}],
)
def test_trigger_tags_broken_or_missing(value: object) -> None:
    assert trigger_tags({"ss_tag_frequency": value}) == ()
    assert trigger_tags({}) == ()


def test_trigger_tags_skip_invalid_entries_and_limit() -> None:
    tags = {f"tag_{i:02d}": 100 - i for i in range(30)}
    tags.update({"": 50, "zero": 0, "negative": -3, "bool": True, "x" * 101: 999, "  ": 9})
    result = trigger_tags({"ss_tag_frequency": {"img": tags}})
    assert len(result) == TRIGGER_TAGS_MAX
    assert result[0] == "tag 00"
    assert "zero" not in result and "negative" not in result and "bool" not in result
    assert all(len(tag) <= 100 for tag in result)


def test_parse_lora_alias_and_metadata_string() -> None:
    lora = parse_lora(
        {
            "name": "style-a",
            "alias": "style-a",
            "path": f"{FAKE_PATH_ROOT}/Lora/style-a.safetensors",
            "metadata": json.dumps({"ss_base_model_version": "sdxl_base_v1-0"}),
        }
    )
    assert lora is not None
    assert lora.alias is None  # name と同じなら持たない
    assert lora.base_model == "sdxl"

    lora = parse_lora({"name": "x", "alias": "y", "metadata": "{broken"})
    assert lora is not None
    assert (lora.alias, lora.base_model, lora.trigger_tags) == ("y", None, ())

    lora = parse_lora({"name": "x", "alias": None})
    assert lora is not None and lora.alias is None


@pytest.mark.parametrize("item", [None, "x", {}, {"name": ""}, {"name": 3}, {"alias": "a"}])
def test_parse_lora_skips_without_name(item: object) -> None:
    assert parse_lora(item) is None


def test_parse_loras_sorted_case_insensitive_and_deduplicated() -> None:
    body = [
        {"name": "beta"},
        {"name": "Alpha"},
        {"name": "gamma"},
        {"name": "alpha-2"},
        {"name": "beta", "alias": "dup"},
        "broken",
    ]
    loras = parse_loras(body)
    assert [lora.name for lora in loras] == ["Alpha", "alpha-2", "beta", "gamma"]
    assert next(lora for lora in loras if lora.name == "beta").alias is None
    assert parse_loras({"not": "a list"}) == []


# -- クライアント -------------------------------------------------------------------------


def test_client_fetch_loras() -> None:
    fake = FakeSdWebui()
    client = SdWebuiClient(LOCAL_URL, transport=fake.transport())
    loras = client.fetch_loras()
    assert [lora.name for lora in loras] == ["Character-B", "detail-c", "style-a"]
    by_name = {lora.name: lora for lora in loras}
    assert by_name["Character-B"].alias == "character-b-alias"
    assert by_name["Character-B"].base_model == "sd1"
    assert by_name["style-a"].base_model == "sdxl"
    assert by_name["style-a"].trigger_tags == ("1girl", "style a", "long hair", "outdoors")
    assert by_name["detail-c"].base_model is None
    assert by_name["detail-c"].trigger_tags == ()


def test_client_fetch_loras_not_supported_is_empty() -> None:
    fake = FakeSdWebui()
    fake.loras = None
    client = SdWebuiClient(LOCAL_URL, transport=fake.transport())
    assert client.fetch_loras() == []
    client.refresh_loras()  # 404 は何もしない
    assert fake.lora_refresh_count == 0


def test_client_fetch_loras_failures() -> None:
    client = SdWebuiClient(LOCAL_URL, transport=unreachable_transport())
    with pytest.raises(SdWebuiError) as exc_info:
        client.fetch_loras()
    assert exc_info.value.code == "sdwebuiUnavailable"

    fake = FakeSdWebui(credentials=("user", "pass"))
    client = SdWebuiClient(LOCAL_URL, transport=fake.transport())
    with pytest.raises(SdWebuiError):
        client.fetch_loras()
    client = SdWebuiClient(LOCAL_URL, credentials=("user", "pass"), transport=fake.transport())
    assert len(client.fetch_loras()) == 3


# -- API ------------------------------------------------------------------------------


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeSdWebui:
    fake = FakeSdWebui()
    install_fake_factories(monkeypatch, fake)
    return fake


def test_loras_not_connected(client: TestClient) -> None:
    response = client.get("/api/sdwebui/loras")
    assert response.status_code == 409


def test_loras_returns_only_allowed_fields(
    client: TestClient, fake: FakeSdWebui, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    client.put("/api/sdwebui/connection", json={"url": LOCAL_URL})
    response = client.get("/api/sdwebui/loras")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {
        "items": [
            {
                "name": "Character-B",
                "alias": "character-b-alias",
                "base_model": "sd1",
                "trigger_tags": ["character b", "smile"],
            },
            {"name": "detail-c", "alias": None, "base_model": None, "trigger_tags": []},
            {
                "name": "style-a",
                "alias": None,
                "base_model": "sdxl",
                "trigger_tags": ["1girl", "style a", "long hair", "outdoors"],
            },
        ]
    }
    # パスと学習時のメタ情報(フォルダー名を含む)は応答にもログにも出さない
    for marker in (*LORA_SECRET_MARKERS, FAKE_PATH_ROOT, "ss_", "other-folder"):
        assert marker not in response.text
        assert marker not in caplog.text


def test_loras_unreachable_is_409(client: TestClient, fake: FakeSdWebui) -> None:
    client.put("/api/sdwebui/connection", json={"url": LOCAL_URL})
    fake.api_enabled = False  # /sdapi/v1/loras も 404 になるが、それは「LoRA なし」
    assert client.get("/api/sdwebui/loras").json() == {"items": []}

    fake.api_enabled = True
    client.post("/api/sdwebui/refresh")
    fake.credentials = ("user", "pass")
    response = client.get("/api/sdwebui/loras")
    assert response.status_code == 409
    for marker in LORA_SECRET_MARKERS:
        assert marker not in response.text


def test_loras_cached_until_refresh(client: TestClient, fake: FakeSdWebui) -> None:
    client.put("/api/sdwebui/connection", json={"url": LOCAL_URL})
    assert len(client.get("/api/sdwebui/loras").json()["items"]) == 3

    assert fake.loras is not None
    fake.loras = fake.loras[:1]
    # キャッシュの間は WebUI に問い合わせない
    assert len(client.get("/api/sdwebui/loras").json()["items"]) == 3

    response = client.post("/api/sdwebui/refresh")
    assert response.status_code == 200
    assert fake.refresh_count == 1
    assert fake.lora_refresh_count == 1
    assert [i["name"] for i in client.get("/api/sdwebui/loras").json()["items"]] == ["style-a"]


def test_refresh_without_lora_support(client: TestClient, fake: FakeSdWebui) -> None:
    client.put("/api/sdwebui/connection", json={"url": LOCAL_URL})
    fake.loras = None
    response = client.post("/api/sdwebui/refresh")
    assert response.status_code == 200
    assert client.get("/api/sdwebui/loras").json() == {"items": []}
