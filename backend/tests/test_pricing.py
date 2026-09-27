"""参考価格の見積もり(ADR-0009「参考価格」節)のテスト。

出力トークンの式は公式計算機の JS と、入力画像トークンの式は vision ガイドの patch 式と
それぞれ実績を突き合わせた既知の照合値を期待値にする(`app/domain/pricing.py` の
docstring 参照)。純粋関数(app/domain/pricing.py)の単体テストと、
GET /api/pricing/estimate・GET /api/runs/{id} の統合テスト。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.domain.pricing import (
    OUTPUT_GRID,
    cost_from_usage,
    estimate_cost,
    input_image_tokens,
    normalize_model,
    output_image_tokens,
    text_tokens,
)
from app.providers.openai_pricing import get_unit_prices
from tests.conftest import make_png_bytes, wait_for_run_terminal

MODEL_SUNBURST = "gpt-image-2.5-sunburst"
MODEL_FLARE = "gpt-image-2.5-flare"
MODEL_2 = "gpt-image-2"


# -- normalize_model ---------------------------------------------------------


def test_normalize_model_maps_25_variants() -> None:
    assert normalize_model("gpt-image-2.5-sunburst") == "gpt-image-2.5"
    assert normalize_model("gpt-image-2.5-flare") == "gpt-image-2.5"
    assert normalize_model("gpt-image-2.5-sunburst-2026-09-01") == "gpt-image-2.5"


def test_normalize_model_maps_2_variants() -> None:
    assert normalize_model("gpt-image-2") == "gpt-image-2"
    assert normalize_model("gpt-image-2-2026-01-01") == "gpt-image-2"


def test_normalize_model_unknown_is_none() -> None:
    assert normalize_model("gpt-image-9") is None
    assert normalize_model("dall-e-3") is None


# -- output_image_tokens ------------------------------------------------------


def test_output_image_tokens_matches_known_history_1024_square_low() -> None:
    assert output_image_tokens(MODEL_FLARE, 1024, 1024, "low") == 196


def test_output_image_tokens_matches_known_history_1536x1024_low() -> None:
    assert output_image_tokens(MODEL_FLARE, 1536, 1024, "low") == 158


def test_output_image_tokens_matches_known_history_2560x1440_low() -> None:
    assert output_image_tokens(MODEL_SUNBURST, 2560, 1440, "low") == 205


def test_output_image_tokens_high_quality_1024_square() -> None:
    # 48 * 48 * (2_000_000 + 1024*1024) / 4_000_000 = 1755.98... -> ceil = 1756
    assert output_image_tokens(MODEL_FLARE, 1024, 1024, "high") == 1756


def test_output_image_tokens_half_rounds_to_even() -> None:
    # 1024x736, low(L=16): m = 16 / (1024/736) = 11.5, floor=11(奇数) -> d = 11 + 1 = 12。
    # Python の round() は元々偶数丸めなので分岐が無くても同じ値になるが、この組は
    # floor が奇数のケースとして、原文の分岐(.5 は偶数へ)を実際に通る値になっている。
    assert output_image_tokens(MODEL_FLARE, 1024, 736, "low") == 133


def test_output_image_tokens_none_for_auto_quality() -> None:
    assert output_image_tokens(MODEL_FLARE, 1024, 1024, "auto") is None


def test_output_image_tokens_none_for_invalid_size() -> None:
    # 16の倍数でないため sizes.py の検証を通らない。
    assert output_image_tokens(MODEL_FLARE, 1000, 1000, "low") is None


def test_output_image_tokens_none_for_unknown_model() -> None:
    assert output_image_tokens("gpt-image-9", 1024, 1024, "low") is None


def test_output_grid_table() -> None:
    assert OUTPUT_GRID["gpt-image-2"] == {"low": 16, "medium": 48, "high": 96}
    assert OUTPUT_GRID["gpt-image-2.5"] == {
        "low": 16,
        "medium": 24,
        "high": 48,
        "xhigh": 64,
        "max": 96,
    }


# -- input_image_tokens --------------------------------------------------------


def test_input_image_tokens_matches_known_history() -> None:
    assert input_image_tokens(1024, 1024) == 1024
    assert input_image_tokens(1536, 1024) == 1536
    assert input_image_tokens(1024, 1536) == 1536
    assert input_image_tokens(341, 512) == 704
    assert input_image_tokens(1179, 2556) == 1482


# -- text_tokens ---------------------------------------------------------------


def test_text_tokens_is_16_plus_length() -> None:
    assert text_tokens(0) == 16
    assert text_tokens(20) == 36
    assert text_tokens(66) == 82


# -- estimate_cost --------------------------------------------------------------


def test_estimate_cost_total_matches_worked_example() -> None:
    result = estimate_cost(
        model=MODEL_FLARE,
        quality="low",
        size="1024x1024",
        n=1,
        prompt_length=20,
        input_images=[],
    )

    assert result.unavailable_reason is None
    assert result.output_tokens_per_image == 196
    assert result.text_tokens == 36
    assert result.input_image_tokens == 0
    assert result.total_usd is not None
    # (36 * 5 + 196 * 30) / 1e6
    assert result.total_usd == 0.00606


def test_estimate_cost_quality_auto_is_unavailable() -> None:
    result = estimate_cost(
        model=MODEL_FLARE, quality="auto", size="1024x1024", n=1, prompt_length=0, input_images=[]
    )
    assert result.unavailable_reason == "quality_auto"
    assert result.output_tokens_per_image is None
    assert result.total_usd is None


def test_estimate_cost_size_auto_is_unavailable() -> None:
    result = estimate_cost(
        model=MODEL_FLARE, quality="low", size="auto", n=1, prompt_length=0, input_images=[]
    )
    assert result.unavailable_reason == "size_auto"
    assert result.total_usd is None


def test_estimate_cost_invalid_size_is_unavailable() -> None:
    result = estimate_cost(
        model=MODEL_FLARE, quality="low", size="1000x1000", n=1, prompt_length=0, input_images=[]
    )
    assert result.unavailable_reason == "size_invalid"
    assert result.total_usd is None


def test_estimate_cost_unknown_model_is_unavailable() -> None:
    result = estimate_cost(
        model="gpt-image-9", quality="low", size="1024x1024", n=1, prompt_length=0, input_images=[]
    )
    assert result.unavailable_reason == "unknown_model"
    assert result.unit_prices is None
    assert result.total_usd is None
    # モデルが未知でも、他の欄(テキスト)は計算できる範囲で返す。
    assert result.text_tokens == 16


def test_estimate_cost_includes_per_input_image_breakdown() -> None:
    import uuid

    asset_a = uuid.uuid4()
    asset_b = uuid.uuid4()
    result = estimate_cost(
        model=MODEL_FLARE,
        quality="low",
        size="1024x1024",
        n=1,
        prompt_length=0,
        input_images=[(asset_a, 1024, 1024), (asset_b, 1536, 1024)],
    )
    assert result.input_image_tokens == 1024 + 1536
    assert [item.tokens for item in result.input_images] == [1024, 1536]
    assert [item.asset_id for item in result.input_images] == [asset_a, asset_b]


# -- cost_from_usage --------------------------------------------------------------


def test_cost_from_usage_uses_input_tokens_details_when_present() -> None:
    usage = {
        "input_tokens": 3111,
        "input_tokens_details": {"image_tokens": 3072, "text_tokens": 39},
        "output_tokens": 205,
    }
    # 3072 * 8 + 39 * 5 + 205 * 30 = 24576 + 195 + 6150 = 30921 -> / 1e6
    assert cost_from_usage(MODEL_SUNBURST, usage) == 0.030921


def test_cost_from_usage_treats_input_tokens_as_text_without_details() -> None:
    usage = {"input_tokens": 50, "output_tokens": 1000}
    # 50 * 5 + 1000 * 30 = 250 + 30000 = 30250 -> / 1e6
    assert cost_from_usage(MODEL_SUNBURST, usage) == 0.03025


def test_cost_from_usage_none_without_usage() -> None:
    assert cost_from_usage(MODEL_SUNBURST, None) is None


def test_cost_from_usage_none_for_unknown_model() -> None:
    assert cost_from_usage("gpt-image-9", {"input_tokens": 10, "output_tokens": 10}) is None


# -- 単価表 -------------------------------------------------------------


def test_unit_prices_known_models() -> None:
    for model in (MODEL_SUNBURST, MODEL_FLARE, MODEL_2):
        prices = get_unit_prices(model)
        assert prices is not None
        assert prices.text_input == 5.00
        assert prices.image_input == 8.00
        assert prices.image_output == 30.00


def test_get_unit_prices_unknown_model_is_none() -> None:
    assert get_unit_prices("nope") is None


# -- API: GET /api/pricing/estimate -----------------------------------------


def _upload(client: TestClient, width: int, height: int) -> str:
    data = make_png_bytes(width=width, height=height)
    response = client.post(
        "/api/assets",
        files={"file": ("input.png", data, "image/png")},
        data={"kind": "upload"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_estimate_api_returns_deterministic_output_tokens(client: TestClient) -> None:
    response = client.get(
        "/api/pricing/estimate",
        params={
            "model": MODEL_FLARE,
            "operation": "generate",
            "size": "1024x1024",
            "quality": "low",
            "n": 1,
            "prompt_length": 20,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["currency"] == "USD"
    assert body["unavailable_reason"] is None
    assert body["output_tokens_per_image"] == 196
    assert body["text_tokens"] == 36
    assert body["input_image_tokens"] == 0
    assert body["input_images"] == []
    assert body["total_usd"] == 0.00606
    assert body["unit_prices_per_1m"] == {
        "text_input": 5.0,
        "image_input": 8.0,
        "image_output": 30.0,
    }
    assert body["pricing_source"] == "https://developers.openai.com/api/docs/pricing"
    assert body["pricing_checked_at"] == "2026-09-22"


def test_estimate_api_uses_uploaded_asset_sizes_for_input_images(client: TestClient) -> None:
    asset_a = _upload(client, 1024, 1024)
    asset_b = _upload(client, 1536, 1024)

    response = client.get(
        "/api/pricing/estimate",
        params={
            "model": MODEL_FLARE,
            "operation": "edit",
            "size": "1024x1024",
            "quality": "low",
            "n": 1,
            "input_asset_ids": f"{asset_a},{asset_b}",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["input_image_tokens"] == 1024 + 1536
    assert [item["tokens"] for item in body["input_images"]] == [1024, 1536]
    assert [item["asset_id"] for item in body["input_images"]] == [asset_a, asset_b]


def test_estimate_api_ignores_deleted_asset_ids(client: TestClient) -> None:
    asset_a = _upload(client, 1024, 1024)
    asset_b = _upload(client, 1536, 1024)
    response = client.delete(f"/api/assets/{asset_b}")
    assert response.status_code == 204, response.text

    response = client.get(
        "/api/pricing/estimate",
        params={
            "model": MODEL_FLARE,
            "operation": "edit",
            "input_asset_ids": f"{asset_a},{asset_b}",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["input_image_tokens"] == 1024
    assert [item["asset_id"] for item in body["input_images"]] == [asset_a]


def test_estimate_api_quality_auto_returns_reason(client: TestClient) -> None:
    response = client.get(
        "/api/pricing/estimate",
        params={"model": MODEL_FLARE, "operation": "generate", "size": "1024x1024"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["unavailable_reason"] == "quality_auto"
    assert body["output_tokens_per_image"] is None
    assert body["total_usd"] is None


def test_estimate_api_size_auto_returns_reason(client: TestClient) -> None:
    response = client.get(
        "/api/pricing/estimate",
        params={"model": MODEL_FLARE, "operation": "generate", "quality": "low"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["unavailable_reason"] == "size_auto"
    assert body["total_usd"] is None


def test_estimate_api_invalid_size_returns_reason(client: TestClient) -> None:
    response = client.get(
        "/api/pricing/estimate",
        params={
            "model": MODEL_FLARE,
            "operation": "generate",
            "size": "1000x1000",
            "quality": "low",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["unavailable_reason"] == "size_invalid"
    assert body["total_usd"] is None


def test_estimate_api_unknown_model_returns_reason(client: TestClient) -> None:
    response = client.get(
        "/api/pricing/estimate",
        params={"model": "gpt-image-9", "operation": "generate"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["unavailable_reason"] == "unknown_model"
    assert body["total_usd"] is None
    assert body["unit_prices_per_1m"] is None


def test_estimate_api_validates_query_ranges(client: TestClient) -> None:
    response = client.get(
        "/api/pricing/estimate",
        params={"model": MODEL_SUNBURST, "operation": "generate", "n": 0},
    )
    assert response.status_code == 422

    response = client.get(
        "/api/pricing/estimate",
        params={"model": MODEL_SUNBURST, "operation": "bogus"},
    )
    assert response.status_code == 422


# -- API: GET /api/runs/{id} の cost_usd ---------------------------------------


def test_run_detail_includes_cost_usd_for_succeeded_run(client: TestClient) -> None:
    response = client.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": MODEL_SUNBURST,
            "prompt": "a red apple on a wooden table",
            "params": {"n": 1, "size": "1024x1024", "quality": "low", "output_format": "png"},
        },
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]

    detail = wait_for_run_terminal(client, run_id)
    assert detail["status"] == "succeeded"
    # FakeProvider の usage: input_tokens=50*1, output_tokens=1000*1, details 無し。
    # (50 * 5 + 1000 * 30) / 1e6
    assert detail["cost_usd"] == 0.03025

    # 履歴一覧(RunSummary)にも同じ値が出る。
    list_response = client.get("/api/runs")
    assert list_response.status_code == 200, list_response.text
    summary = next(item for item in list_response.json()["items"] if item["id"] == run_id)
    assert summary["cost_usd"] == 0.03025


def test_run_detail_cost_usd_is_none_before_usage_is_recorded(
    client_no_runner: TestClient,
) -> None:
    response = client_no_runner.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": MODEL_SUNBURST,
            "prompt": "a red apple on a wooden table",
            "params": {"n": 1, "size": "1024x1024", "quality": "low", "output_format": "png"},
        },
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]

    detail = client_no_runner.get(f"/api/runs/{run_id}").json()
    assert detail["status"] == "queued"
    assert detail["usage"] is None
    assert detail["cost_usd"] is None
