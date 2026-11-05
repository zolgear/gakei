"""SVG の出力(ADR-0062)。

Fake プロバイダーで、検証・無害化・保存・派生・配信・入力の断りを確かめる。
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.domain import svg
from app.domain.pricing import cost_from_usage, estimate_cost
from app.providers.openai_spec import build_capabilities
from tests.conftest import wait_for_run_terminal

MODEL_26 = "gpt-image-2.6-flare"


def _params(caps, model: str, operation: str) -> dict:
    model_caps = next(m for m in caps.models if m.model == model)
    op = next(o for o in model_caps.operations if o.operation == operation)
    return {p.name: p for p in op.params}


def _post_generate(client: TestClient, params: dict):
    return client.post(
        "/api/runs",
        json={"operation": "generate", "model": MODEL_26, "prompt": "a cup icon", "params": params},
    )


# -- capabilities -------------------------------------------------------------


def test_svg_is_offered_only_for_26_generate() -> None:
    caps = build_capabilities("fake")
    gen = _params(caps, MODEL_26, "generate")
    assert "svg" in (gen["output_format"].choices or [])
    # 書き手のモデルは管理者設定(フォームに出さない)。
    assert "svg_model" not in gen
    assert "max" in (gen["svg_effort"].choices or [])
    assert "svg" not in (_params(caps, MODEL_26, "edit")["output_format"].choices or [])
    gen_25 = _params(caps, "gpt-image-2.5-flare", "generate")
    assert "svg" not in (gen_25["output_format"].choices or [])
    conditional = {c.field: c.depends_on_values for c in caps.conditional_params}
    assert "svg" not in conditional["n"]
    assert "svg" not in conditional["partial_images"]
    assert conditional["svg_effort"] == ["svg"]


def test_svg_only_params_need_svg(client: TestClient) -> None:
    assert _post_generate(client, {"svg_effort": "low"}).status_code == 422
    # 書き手のモデルはフォームから送れない(管理者設定)。
    assert _post_generate(client, {"output_format": "svg", "svg_model": "x"}).status_code == 422
    # SVG は1枚だけ(`n` は送らない)。
    assert _post_generate(client, {"output_format": "svg", "n": 2}).status_code == 422


# -- 保存・派生・配信 ---------------------------------------------------------------


def test_svg_output_is_stored_and_served_safely(client: TestClient) -> None:
    # 管理者設定の書き手のモデルを、Run の確定で入れて記録する。
    saved = client.patch("/api/settings/general", json={"svg_author_model": "gpt-6-luna"})
    assert saved.status_code == 200, saved.text
    assert saved.json()["svg_author_model"] == {
        "value": "gpt-6-luna",
        "source": "setting",
        "default": "gpt-5.6-luna",
    }
    response = _post_generate(client, {"size": "1024x1024", "output_format": "svg"})
    assert response.status_code == 202, response.text
    detail = wait_for_run_terminal(client, response.json()["id"])
    assert detail["status"] == "succeeded", detail
    asset_id = detail["outputs"][0]["asset_id"]

    asset = client.get(f"/api/assets/{asset_id}").json()
    assert asset["mime"] == "image/svg+xml"
    assert (asset["width"], asset["height"]) == (1024, 1024)

    original = client.get(f"/api/assets/{asset_id}/content", params={"variant": "original"})
    assert original.status_code == 200
    assert original.headers["content-type"].startswith("image/svg+xml")
    assert "sandbox" in original.headers["content-security-policy"]
    assert original.headers["x-content-type-options"] == "nosniff"
    assert original.content.startswith(b"<svg")

    download = client.get(
        f"/api/assets/{asset_id}/content", params={"variant": "original", "download": 1}
    )
    assert download.headers["content-disposition"].endswith('.svg"')

    thumb = client.get(f"/api/assets/{asset_id}/content", params={"variant": "thumb"})
    assert thumb.headers["content-type"] == "image/webp"
    with Image.open(io.BytesIO(thumb.content)) as image:
        assert image.size == (512, 512)
        assert image.mode == "RGBA"

    # 実行後の料金は、画像の分に SVG を書くモデルの分を足す。
    run = client.get(f"/api/runs/{detail['id']}").json()
    assert run["params"]["svg_model"] == "gpt-6-luna"
    assert run["usage"]["svg_usage"]["model"] == "gpt-6-luna"

    # ADR-0062: SVG は入力にしない。
    edit = client.post(
        "/api/runs",
        json={
            "operation": "edit",
            "model": MODEL_26,
            "prompt": "make it blue",
            "params": {},
            "inputs": [{"asset_id": asset_id, "role": "image", "position": 0}],
        },
    )
    assert edit.status_code == 422
    assert "SVG" in edit.json()["detail"]


def test_svg_upload_is_rejected(client: TestClient) -> None:
    doc = b'<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><rect/></svg>'
    response = client.post("/api/assets", files={"file": ("x.svg", doc, "image/svg+xml")})
    assert response.status_code in (400, 415, 422), response.text


# -- 無害化 -------------------------------------------------------------------------


def test_sanitize_removes_scripts_and_external_references() -> None:
    doc = (
        b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"'
        b' onload="alert(1)" width="10" height="10">'
        b"<script>alert(1)</script>"
        b'<a href="javascript:alert(1)"><rect/></a>'
        b'<image href="https://example.com/x.png"/>'
        b'<use xlink:href="https://example.com/x.svg#a"/><use href="#ok"/>'
        b'<rect style="fill:url(https://example.com/x)" fill="url(#g)" onclick="x()"/>'
        b'<foreignObject><div xmlns="http://www.w3.org/1999/xhtml">x</div></foreignObject>'
        b"<style>@import url(https://example.com/x.css)</style>"
        b"<!-- comment --></svg>"
    )
    cleaned = svg.sanitize(doc)
    for needle in (b"script", b"alert", b"javascript", b"example.com", b"foreignObject", b"on"):
        assert needle not in cleaned.replace(b"href", b"").replace(b"xmlns", b""), needle
    assert b'href="#ok"' in cleaned
    assert b'fill="url(#g)"' in cleaned
    # もう一度無害化しても変わらない(系列の ZIP の取り込みで sha256 が変わらない)。
    assert svg.sanitize(cleaned) == cleaned


@pytest.mark.parametrize(
    "doc",
    [
        b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY a "aaaa">]><svg xmlns="http://www.w3.org/2000/svg">&a;</svg>',
        b"<svg><rect/></svg>",  # 名前空間の無い文書は SVG として扱わない
        b"<html></html>",
        b"<svg xmlns='http://www.w3.org/2000/svg'",
    ],
)
def test_sanitize_rejects_unsafe_or_broken_documents(doc: bytes) -> None:
    with pytest.raises(svg.SvgError):
        svg.sanitize(doc)


def test_intrinsic_size_reads_width_height_or_viewbox() -> None:
    assert svg.intrinsic_size(
        b'<svg xmlns="http://www.w3.org/2000/svg" width="300" height="200"/>'
    ) == (300, 200)
    assert svg.intrinsic_size(b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 32"/>') == (
        64,
        32,
    )
    assert svg.intrinsic_size(b'<svg xmlns="http://www.w3.org/2000/svg"/>') == (1024, 1024)


# -- 参考価格 -------------------------------------------------------------------


def test_svg_price_is_not_estimated_but_actual_cost_adds_the_author() -> None:
    result = estimate_cost(
        model=MODEL_26, quality="low", size="1024x1024", n=1, prompt_length=10, output_format="svg"
    )
    assert result.total_usd is None
    assert result.unavailable_reason == "svg_output"

    image_only = {"input_tokens": 84, "output_tokens": 824}
    base = cost_from_usage(MODEL_26, image_only)
    assert base is not None
    with_author = cost_from_usage(
        MODEL_26,
        {
            **image_only,
            "svg_usage": {"model": "gpt-6-luna", "input_tokens": 1556, "output_tokens": 931},
        },
    )
    assert with_author == pytest.approx(base + (1556 * 0.10 + 931 * 0.50) / 1_000_000, abs=1e-6)
    unknown = {
        **image_only,
        "svg_usage": {"model": "unknown", "input_tokens": 1, "output_tokens": 1},
    }
    assert cost_from_usage(MODEL_26, unknown) is None


def test_svg_author_model_setting(client: TestClient) -> None:
    # 未設定なら組み込みの既定を使う(LLM を使う他の機能と同じ)。
    assert client.get("/api/settings/general").json()["svg_author_model"] == {
        "value": "gpt-5.6-luna",
        "source": "default",
        "default": "gpt-5.6-luna",
    }
    bad = client.patch("/api/settings/general", json={"svg_author_model": "gpt 6; rm"})
    assert bad.status_code == 422
    response = _post_generate(client, {"size": "1024x1024", "output_format": "svg"})
    detail = wait_for_run_terminal(client, response.json()["id"])
    assert detail["params"]["svg_model"] == "gpt-5.6-luna"
    assert client.patch("/api/settings/general", json={"svg_author_model": None}).status_code == 200
