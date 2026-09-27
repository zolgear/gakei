"""GET /api/search の統合テスト(ADR-0009 6章)。

Run/Asset/プロンプトセットを横断したテキスト部分一致検索。実際にRunを実行して
できたAssetのprompt経由の一致、AND、大文字小文字、`%`のエスケープ、削除済みの除外、
limit/truncated、空クエリの422を確認する。加えて、アップロード画像に他ツールが
埋め込んだ生成情報(`asset.embedded_meta`)経由の一致(ADR-0018、2026-09-27 追記)も確認する。
"""

from __future__ import annotations

import io

from fastapi.testclient import TestClient
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from tests.conftest import make_png_bytes, wait_for_run_terminal

# AUTOMATIC1111 が書き込む `parameters` tEXt チャンクの最小例(3項目以上、
# `backend/tests/test_generation_meta.py` の書き方に合わせる)。
_A1111_TEXT = (
    "masterpiece, mejiro ardan, autumn\n"
    "Negative prompt: lowres\n"
    "Steps: 30, Sampler: Euler a, CFG scale: 7, Seed: 1546138853, Size: 592x1280"
)


def _a1111_png_bytes() -> bytes:
    """`parameters` tEXt チャンクに A1111 形式の生成情報を埋め込んだ PNG を作る。"""
    image = Image.new("RGB", (48, 32), (120, 60, 30))
    info = PngInfo()
    info.add_text("parameters", _A1111_TEXT)
    buffer = io.BytesIO()
    image.save(buffer, "PNG", pnginfo=info)
    return buffer.getvalue()


def _generate(client: TestClient, prompt: str) -> dict:
    response = client.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": "gpt-image-2.5-sunburst",
            "prompt": prompt,
            "params": {"n": 1, "size": "1024x1024"},
        },
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]
    return wait_for_run_terminal(client, run_id)


def _create_prompt_set(client: TestClient, name: str, items: list[dict] | None = None) -> dict:
    response = client.post("/api/prompt-sets", json={"name": name, "items": items or []})
    assert response.status_code == 201, response.text
    return response.json()


# -- Run / Asset --------------------------------------------------------


def test_run_hit_includes_snippet_and_summary_fields(client: TestClient) -> None:
    detail = _generate(client, "a red apple on a wooden table")

    response = client.get("/api/search", params={"q": "apple"})
    assert response.status_code == 200
    body = response.json()

    matched = [r for r in body["runs"] if r["id"] == detail["id"]]
    assert len(matched) == 1
    hit = matched[0]
    assert "apple" in hit["snippet"].lower()
    # RunSummary と同じ項目が乗っていること。
    assert hit["status"] == "succeeded"
    assert hit["outputs"]
    assert body["query"] == "apple"


def test_asset_hit_references_producing_run_prompt(client: TestClient) -> None:
    detail = _generate(client, "a blue bicycle in the park")
    output_asset_id = detail["outputs"][0]["asset_id"]

    response = client.get("/api/search", params={"q": "bicycle"})
    assert response.status_code == 200
    body = response.json()

    matched = [a for a in body["assets"] if a["id"] == output_asset_id]
    assert len(matched) == 1
    hit = matched[0]
    assert hit["produced_by_run_id"] == detail["id"]
    assert hit["prompt_source"] == "run"
    assert "bicycle" in hit["prompt_snippet"].lower()


def test_only_generated_kind_assets_are_searchable(client: TestClient) -> None:
    data = make_png_bytes()
    upload_response = client.post(
        "/api/assets",
        files={"file": ("mountain.png", data, "image/png")},
        data={"kind": "upload"},
    )
    assert upload_response.status_code == 201
    upload_id = upload_response.json()["id"]

    detail = _generate(client, "a snowy mountain peak")
    output_asset_id = detail["outputs"][0]["asset_id"]

    response = client.get("/api/search", params={"q": "mountain"})
    ids = [a["id"] for a in response.json()["assets"]]
    assert output_asset_id in ids
    assert upload_id not in ids


def test_multi_word_query_requires_all_words_and(client: TestClient) -> None:
    _generate(client, "a red apple on a table")
    _generate(client, "a red car in a garage")

    response = client.get("/api/search", params={"q": "red apple"})
    prompts = [r["prompt"] for r in response.json()["runs"]]
    assert any("apple" in p for p in prompts)
    assert not any("car" in p for p in prompts)


def test_search_is_case_insensitive(client: TestClient) -> None:
    _generate(client, "A Beautiful SUNSET over the Ocean")

    response = client.get("/api/search", params={"q": "sunset ocean"})
    assert len(response.json()["runs"]) >= 1


def test_percent_is_escaped_and_not_treated_as_wildcard(client: TestClient) -> None:
    _generate(client, "flash sale: 50% off everything")
    _generate(client, "we sold 500 units last month")

    response = client.get("/api/search", params={"q": "50%"})
    prompts = [r["prompt"] for r in response.json()["runs"]]
    assert any("50%" in p for p in prompts)
    assert not any("500 units" in p for p in prompts)


def test_underscore_is_escaped_and_not_treated_as_wildcard(client: TestClient) -> None:
    _generate(client, "file_name convention guide")
    _generate(client, "fileXname unrelated prompt")

    response = client.get("/api/search", params={"q": "file_name"})
    prompts = [r["prompt"] for r in response.json()["runs"]]
    assert any("file_name" in p for p in prompts)
    assert not any("fileXname" in p for p in prompts)


def test_deleted_run_and_its_output_asset_excluded_from_search(client: TestClient) -> None:
    detail = _generate(client, "a unique giraffe wearing sunglasses")
    run_id = detail["id"]
    asset_id = detail["outputs"][0]["asset_id"]

    before = client.get("/api/search", params={"q": "giraffe"}).json()
    assert any(r["id"] == run_id for r in before["runs"])
    assert any(a["id"] == asset_id for a in before["assets"])

    assert client.delete(f"/api/runs/{run_id}").status_code == 204

    after = client.get("/api/search", params={"q": "giraffe"}).json()
    assert not any(r["id"] == run_id for r in after["runs"])
    assert not any(a["id"] == asset_id for a in after["assets"])


def test_limit_and_truncated_flag_for_runs(client: TestClient) -> None:
    for i in range(3):
        _generate(client, f"unique-marker-zzz item number {i}")

    limited = client.get("/api/search", params={"q": "unique-marker-zzz", "limit": 2}).json()
    assert len(limited["runs"]) == 2
    assert limited["truncated"]["runs"] is True

    full = client.get("/api/search", params={"q": "unique-marker-zzz", "limit": 10}).json()
    assert len(full["runs"]) == 3
    assert full["truncated"]["runs"] is False


def test_empty_or_blank_query_returns_422(client: TestClient) -> None:
    assert client.get("/api/search", params={"q": ""}).status_code == 422
    assert client.get("/api/search", params={"q": "   "}).status_code == 422


def test_missing_query_param_returns_422(client: TestClient) -> None:
    assert client.get("/api/search").status_code == 422


def test_types_filter_limits_categories(client: TestClient) -> None:
    _generate(client, "a filtered category test prompt")
    _create_prompt_set(client, "filtered category test set")

    response = client.get("/api/search", params={"q": "filtered", "types": "run"})
    body = response.json()
    assert len(body["runs"]) >= 1
    assert body["assets"] == []
    assert body["prompt_sets"] == []


def test_unknown_type_returns_422(client: TestClient) -> None:
    response = client.get("/api/search", params={"q": "x", "types": "bogus"})
    assert response.status_code == 422


# -- Prompt sets ----------------------------------------------------------


def test_prompt_set_name_only_match_has_empty_matched_items(client: TestClient) -> None:
    created = _create_prompt_set(
        client, "landscape prompts", items=[{"text": "totally unrelated text"}]
    )

    body = client.get("/api/search", params={"q": "landscape"}).json()
    hits = [ps for ps in body["prompt_sets"] if ps["id"] == created["id"]]
    assert len(hits) == 1
    assert hits[0]["matched_items"] == []


def test_prompt_set_item_text_match_includes_snippet(client: TestClient) -> None:
    created = _create_prompt_set(
        client,
        "misc set",
        items=[
            {"label": "one", "text": "a cinematic shot of a dragon"},
            {"label": "two", "text": "nothing relevant here"},
        ],
    )

    body = client.get("/api/search", params={"q": "dragon"}).json()
    hits = [ps for ps in body["prompt_sets"] if ps["id"] == created["id"]]
    assert len(hits) == 1
    matched = hits[0]["matched_items"]
    assert len(matched) == 1
    assert matched[0]["label"] == "one"
    assert "dragon" in matched[0]["snippet"].lower()


def test_prompt_set_and_matches_across_name_and_item_text(client: TestClient) -> None:
    created = _create_prompt_set(
        client, "cosmic theme", items=[{"text": "a lonely astronaut floating"}]
    )

    hit = client.get("/api/search", params={"q": "cosmic astronaut"}).json()
    assert any(ps["id"] == created["id"] for ps in hit["prompt_sets"])

    miss = client.get("/api/search", params={"q": "cosmic nonexistentword"}).json()
    assert not any(ps["id"] == created["id"] for ps in miss["prompt_sets"])


def test_deleted_prompt_set_excluded_from_search(client: TestClient) -> None:
    created = _create_prompt_set(client, "to be deleted set")
    assert client.delete(f"/api/prompt-sets/{created['id']}").status_code == 204

    body = client.get("/api/search", params={"q": "deleted"}).json()
    assert not any(ps["id"] == created["id"] for ps in body["prompt_sets"])


def test_deleted_prompt_set_item_excluded_from_matched_items(client: TestClient) -> None:
    created = _create_prompt_set(
        client, "item deletion set", items=[{"text": "a phoenix rising from ashes"}]
    )
    item_id = created["items"][0]["id"]

    before = client.get("/api/search", params={"q": "phoenix"}).json()
    assert any(ps["id"] == created["id"] for ps in before["prompt_sets"])

    assert client.delete(f"/api/prompt-sets/{created['id']}/items/{item_id}").status_code == 204

    after = client.get("/api/search", params={"q": "phoenix"}).json()
    assert not any(ps["id"] == created["id"] for ps in after["prompt_sets"])


# -- Asset (embedded generation metadata, ADR-0018) ------------------------


def test_asset_with_embedded_prompt_is_found_and_marked_embedded(client: TestClient) -> None:
    upload_response = client.post(
        "/api/assets",
        files={"file": ("a1111.png", _a1111_png_bytes(), "image/png")},
        data={"kind": "upload"},
    )
    assert upload_response.status_code == 201
    upload_id = upload_response.json()["id"]

    response = client.get("/api/search", params={"q": "mejiro"})
    assert response.status_code == 200
    body = response.json()

    matched = [a for a in body["assets"] if a["id"] == upload_id]
    assert len(matched) == 1
    hit = matched[0]
    assert hit["prompt_source"] == "embedded"
    assert hit["produced_by_run_id"] is None
    assert "mejiro" in hit["prompt_snippet"].lower()


def test_asset_with_embedded_negative_prompt_only_term_is_found(client: TestClient) -> None:
    upload_response = client.post(
        "/api/assets",
        files={"file": ("a1111-neg.png", _a1111_png_bytes(), "image/png")},
        data={"kind": "upload"},
    )
    assert upload_response.status_code == 201
    upload_id = upload_response.json()["id"]

    response = client.get("/api/search", params={"q": "lowres"})
    assert response.status_code == 200
    body = response.json()

    matched = [a for a in body["assets"] if a["id"] == upload_id]
    assert len(matched) == 1
    hit = matched[0]
    assert hit["prompt_source"] == "embedded"
    assert "lowres" in hit["prompt_snippet"].lower()


def test_generated_asset_still_reports_run_prompt_source(client: TestClient) -> None:
    detail = _generate(client, "a serene lake at dawn")
    output_asset_id = detail["outputs"][0]["asset_id"]

    response = client.get("/api/search", params={"q": "serene lake"})
    matched = [a for a in response.json()["assets"] if a["id"] == output_asset_id]
    assert len(matched) == 1
    assert matched[0]["prompt_source"] == "run"


def test_uploaded_png_without_metadata_is_not_found_by_embedded_search(
    client: TestClient,
) -> None:
    data = make_png_bytes()
    upload_response = client.post(
        "/api/assets",
        files={"file": ("plain.png", data, "image/png")},
        data={"kind": "upload"},
    )
    assert upload_response.status_code == 201
    upload_id = upload_response.json()["id"]

    response = client.get("/api/search", params={"q": "mejiro"})
    ids = [a["id"] for a in response.json()["assets"]]
    assert upload_id not in ids
