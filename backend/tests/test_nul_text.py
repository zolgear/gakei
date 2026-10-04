"""NUL(`\\u0000`)を含む文字列の扱い(ADR-0027 2章の追記)。

PostgreSQL は NUL を `TEXT` / `JSONB` に保存できない(SQLite は通す)。利用者のリクエストは
422(MCP はツールのエラー)で拒み、外部から来るテキストは NUL を除いて保存する。
`GAKEI_TEST_DATABASE_URL` を付けて PostgreSQL でも回す。
"""

from __future__ import annotations

import io
import time
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from PIL.PngImagePlugin import PngInfo
from sqlalchemy import func, select

from app.annotation.engines import AnnotationEngineError, FakeEngines, VlmResult
from app.domain.generation_meta import extract_generation_meta
from app.domain.models import Asset, ComfyWorkflow, PromptSet, Run
from app.domain.text_safety import find_nul, sanitize_external, sanitize_external_text
from app.providers.base import ProviderError
from tests.comfyui_graphs import T2I_GRAPH, clone
from tests.conftest import make_png_bytes, wait_for_run_terminal

NUL = "\x00"


def _count(client: TestClient, model: type) -> int:
    with client.app.state.session_factory() as db:
        return db.execute(select(func.count()).select_from(model)).scalar_one()


def _assert_nul_rejected(response: Any, location: str) -> None:
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert isinstance(detail, str)
    assert "NUL" in detail
    assert location in detail


def _upload(client: TestClient, data: bytes | None = None) -> dict:
    response = client.post(
        "/api/assets",
        files={"file": ("a.png", data or make_png_bytes(48, 32), "image/png")},
        data={"kind": "upload"},
    )
    assert response.status_code == 201, response.text
    return response.json()


# -- 共通の関数 ----------------------------------------------------------------


def test_find_nul_walks_nested_values_and_keys() -> None:
    assert find_nul({"a": ["x", {"b": "ok"}]}) is None
    assert find_nul({"a": ["x", {"b": f"n{NUL}g"}]}) == ("a", 1, "b")
    assert find_nul({"a": {f"k{NUL}ey": 1}}) == ("a", "key")
    assert find_nul(f"x{NUL}") == ()
    assert find_nul(123) is None


def test_sanitize_external_removes_nul_and_lone_surrogates() -> None:
    assert sanitize_external_text(f"a{NUL}b") == "ab"
    assert sanitize_external_text("a\ud800b") == "a?b"
    assert sanitize_external({f"k{NUL}": [f"v{NUL}", 1, None, ("t\udfff",)]}) == {
        "k": ["v", 1, None, ["t?"]]
    }


# -- REST: リクエストは 422 ---------------------------------------------------------


def _workflow_body(template: dict) -> dict:
    return {
        "name": "t2i",
        "operation": "generate",
        "template": template,
        "bindings": {
            "prompt": {"node": "6", "input": "text"},
            "outputs": ["9"],
        },
        "exposed_params": [],
    }


def test_workflow_template_value_with_nul_is_422(client: TestClient) -> None:
    template = clone(T2I_GRAPH)
    template["6"]["inputs"]["text"] = f"a cat{NUL}"
    response = client.post("/api/comfyui/workflows", json=_workflow_body(template))
    _assert_nul_rejected(response, "body.template.6.inputs.text")
    assert _count(client, ComfyWorkflow) == 0


def test_workflow_template_key_with_nul_is_422(client: TestClient) -> None:
    template = clone(T2I_GRAPH)
    template["6"]["inputs"][f"extra{NUL}"] = 1
    response = client.post("/api/comfyui/workflows", json=_workflow_body(template))
    _assert_nul_rejected(response, "body.template.6.inputs.extra")
    assert _count(client, ComfyWorkflow) == 0


def test_run_prompt_and_params_with_nul_are_422(client: TestClient) -> None:
    body = {
        "operation": "generate",
        "model": "gpt-image-2.5-sunburst",
        "prompt": f"a cat{NUL}",
        "params": {"n": 1},
    }
    _assert_nul_rejected(client.post("/api/runs", json=body), "body.prompt")
    body = {**body, "prompt": "a cat", "params": {"n": 1, "quality": f"low{NUL}"}}
    _assert_nul_rejected(client.post("/api/runs", json=body), "body.params.quality")
    assert _count(client, Run) == 0


def test_prompt_set_with_nul_is_422(client: TestClient) -> None:
    response = client.post(
        "/api/prompt-sets",
        json={"name": "set", "items": [{"label": "l", "text": f"t{NUL}"}]},
    )
    _assert_nul_rejected(response, "body.items.0.text")
    assert _count(client, PromptSet) == 0


def test_tag_and_title_with_nul_are_422(client: TestClient) -> None:
    asset_id = _upload(client)["id"]
    response = client.post(f"/api/assets/{asset_id}/tags", json={"name": f"blue{NUL}"})
    _assert_nul_rejected(response, "body.name")
    response = client.patch(f"/api/assets/{asset_id}/title", json={"title": f"t{NUL}"})
    _assert_nul_rejected(response, "body.title")
    assert client.get(f"/api/assets/{asset_id}").json()["tags"] == []


def test_query_and_path_with_nul_are_422(client: TestClient) -> None:
    _assert_nul_rejected(client.get("/api/search", params={"q": f"cat{NUL}"}), "query.q")
    asset_id = _upload(client)["id"]
    response = client.delete(f"/api/assets/{asset_id}/tags/x%00y")
    _assert_nul_rejected(response, "path.name")


def test_multipart_field_with_nul_is_422(client: TestClient) -> None:
    response = client.post(
        "/api/assets",
        files={"file": ("a.png", make_png_bytes(48, 32), "image/png")},
        data={"kind": "upload", "source_asset_id": f"x{NUL}"},
    )
    _assert_nul_rejected(response, "body.source_asset_id")
    assert _count(client, Asset) == 0


def test_nul_message_is_localized(client: TestClient) -> None:
    body = {"name": f"g{NUL}"}
    ja = client.post("/api/asset-groups", json=body, headers={"Accept-Language": "ja"})
    en = client.post("/api/asset-groups", json=body, headers={"Accept-Language": "en"})
    assert "NUL 文字" in ja.json()["detail"]
    assert "NUL character" in en.json()["detail"]


def test_text_without_nul_still_passes(client: TestClient) -> None:
    # `\\u0000` という6文字(バックスラッシュ付き)は NUL ではないので通す。
    response = client.post(
        "/api/prompt-sets", json={"name": "\\u0000 literal", "items": [{"text": "ok"}]}
    )
    assert response.status_code == 201, response.text
    assert response.json()["name"] == "\\u0000 literal"


# -- MCP: ツールのエラー ------------------------------------------------------------

_MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "MCP-Protocol-Version": "2025-11-25",
}


def _mcp_call(client: TestClient, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    response = client.post("/mcp", json=body, headers=_MCP_HEADERS)
    assert response.status_code == 200, response.text
    return response.json()["result"]


def test_mcp_tool_arguments_with_nul_are_errors(client: TestClient) -> None:
    response = client.patch("/api/settings/mcp", json={"enabled": True})
    assert response.status_code == 200, response.text

    result = _mcp_call(client, "generate_image", {"prompt": f"a cat{NUL}"})
    assert result["isError"] is True
    assert "NUL" in result["content"][0]["text"]
    assert "prompt" in result["content"][0]["text"]

    result = _mcp_call(
        client, "generate_image", {"prompt": "a cat", "params": {f"size{NUL}": "1024x1024"}}
    )
    assert result["isError"] is True
    assert "params.size" in result["content"][0]["text"]
    assert _count(client, Run) == 0

    result = _mcp_call(client, "create_group", {"name": f"g{NUL}"})
    assert result["isError"] is True

    # NUL が無ければこれまでどおり通る。
    result = _mcp_call(client, "create_group", {"name": "ok"})
    assert not result.get("isError"), result


# -- 外部由来: NUL を除いて保存する ----------------------------------------------------


def _png_with_itxt(keyword: str, text: str) -> bytes:
    image = Image.new("RGB", (48, 32), (120, 60, 30))
    info = PngInfo()
    info.add_itxt(keyword, text)
    info.add_text("Comment", "plain", zip=True)
    buffer = io.BytesIO()
    image.save(buffer, "PNG", pnginfo=info)
    return buffer.getvalue()


_A1111_WITH_NUL = (
    f"a red{NUL} fox\nNegative prompt: low{NUL}res\nSteps: 20, Sampler: Euler, CFG scale: 7"
)


def test_generation_meta_strips_nul() -> None:
    meta = extract_generation_meta(_png_with_itxt("parameters", _A1111_WITH_NUL))
    assert meta is not None
    assert meta["tool"] == "a1111"
    assert meta["prompt"] == "a red fox"
    assert find_nul(meta) is None


def test_uploaded_png_embedded_meta_with_nul_is_saved(client: TestClient) -> None:
    uploaded = _upload(client, _png_with_itxt("parameters", _A1111_WITH_NUL))
    detail = client.get(f"/api/assets/{uploaded['id']}").json()
    embedded = detail["embedded_meta"]
    assert embedded is not None
    assert embedded["prompt"] == "a red fox"


def test_runner_strips_nul_from_provider_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = client.app.state.registry.get("fake")

    async def _raise(run, on_progress):  # noqa: ANN001, ANN202
        raise ProviderError(f"bad{NUL}Request", f"broken{NUL} response", f"req{NUL}1")

    monkeypatch.setattr(provider, "execute", _raise)
    response = client.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": "gpt-image-2.5-sunburst",
            "prompt": "a cat",
            "params": {"n": 1},
        },
    )
    assert response.status_code == 202, response.text
    detail = wait_for_run_terminal(client, response.json()["id"])
    assert detail["status"] == "failed"
    assert detail["error_code"] == "badRequest"
    assert detail["error_message"] == "broken response"
    with client.app.state.session_factory() as db:
        run = db.get(Run, uuid.UUID(detail["id"]))
        assert run is not None
        assert run.provider_request_id == "req1"


def _wait_annotation(client: TestClient, asset_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/api/assets/{asset_id}").json()
        annotation = body.get("annotation")
        if annotation and annotation["status"] in ("succeeded", "failed"):
            return body
        time.sleep(0.05)
    raise TimeoutError(f"asset {asset_id} の推定が終わりませんでした")


def _enable_vlm(client: TestClient) -> None:
    response = client.patch(
        "/api/settings/annotation", json={"auto_on_ingest": True, "vlm_enabled": True}
    )
    assert response.status_code == 200, response.text


def test_annotator_strips_nul_from_engine_output(client: TestClient) -> None:
    class NulEngines(FakeEngines):
        async def describe_image(self, image_jpeg, prompt, want_title, ctx, known_tags=None):  # noqa: ANN001, ANN201
            return VlmResult(tags=[f"sea{NUL}side"], title=f"海{NUL}辺", translations={})

    client.app.state.annotator.engines = NulEngines()
    _enable_vlm(client)
    body = _wait_annotation(client, _upload(client)["id"])
    assert body["annotation"]["status"] == "succeeded", body["annotation"]
    assert body["title"] == "海辺"
    assert {t["name"] for t in body["tags"]} == {"seaside"}


def test_annotator_strips_nul_from_engine_error(client: TestClient) -> None:
    class Broken(FakeEngines):
        async def describe_image(self, image_jpeg, prompt, want_title, ctx, known_tags=None):  # noqa: ANN001, ANN201
            raise AnnotationEngineError(f"VLM が{NUL}壊れた")

    client.app.state.annotator.engines = Broken()
    _enable_vlm(client)
    body = _wait_annotation(client, _upload(client)["id"])
    assert body["annotation"]["status"] == "failed"
    assert body["annotation"]["error"] == "VLM が壊れた"
