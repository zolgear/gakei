"""ADR-0029: ログイン不要の共有リンク。

範囲の計算(この1枚 / 祖先まで / 祖先と子孫)と固定、本人だけの一覧・取り消し、管理者設定の
有効・無効、公開の API で見せるもの・見せないもの、応答ヘッダー、認証なしで応答するルートの
洗い出しを確かめる。
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.domain.embedded_meta import read_gakei_meta
from app.domain.models import (
    Asset,
    Run,
    RunInput,
    RunInputRole,
    RunOperation,
    RunStatus,
    Share,
)
from app.domain.shares import public_params
from tests.conftest import login_as, make_png_bytes, wait_for_run_terminal

_MODEL = "gpt-image-2.5-sunburst"
_PATH_PARAM_RE = re.compile(r"\{[^}]+\}")
_HTTP_METHODS = ("get", "post", "put", "patch", "delete")


# -- 準備 ----------------------------------------------------------------------


def _enable(client: TestClient, enabled: bool = True) -> None:
    response = client.patch("/api/settings/share", json={"enabled": enabled})
    assert response.status_code == 200, response.text
    assert response.json() == {"enabled": enabled}


def _run(client: TestClient, prompt: str, inputs: list[str] | None = None, n: int = 1) -> dict:
    body: dict[str, Any] = {
        "operation": "edit" if inputs else "generate",
        "model": _MODEL,
        "prompt": prompt,
        "params": {"n": n, "quality": "low"},
    }
    if inputs:
        body["inputs"] = [
            {"asset_id": asset_id, "role": "image", "position": i}
            for i, asset_id in enumerate(inputs)
        ]
    response = client.post("/api/runs", json=body)
    assert response.status_code == 202, response.text
    detail = wait_for_run_terminal(client, response.json()["id"])
    assert detail["status"] == "succeeded", detail
    return detail


def _outputs(detail: dict) -> list[str]:
    return [o["asset_id"] for o in detail["outputs"]]


def _create(client: TestClient, asset_id: str, scope: str, **extra: Any) -> dict:
    response = client.post("/api/shares", json={"asset_id": asset_id, "scope": scope, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def _token(share: dict) -> str:
    return share["url"].rsplit("/s/", 1)[1]


def _public(client: TestClient, token: str):  # noqa: ANN202
    # ログインの Cookie を送らない(個人モードは Cookie が無くても同じだが、明示する)。
    client.cookies.clear()
    return client.get(f"/api/public/shares/{token}")


def _content(client: TestClient, token: str, asset_id: str, variant: str = "thumb", **qs: str):  # noqa: ANN202
    client.cookies.clear()
    query = "&".join([f"variant={variant}", *(f"{k}={v}" for k, v in qs.items())])
    return client.get(f"/api/public/shares/{token}/assets/{asset_id}/content?{query}")


class Chain:
    """G(generate, n=2: g0, g1) → E(edit, 入力 g0: e0) → F(edit, 入力 e0: f0)。起点は e0。"""

    def __init__(self, client: TestClient) -> None:
        self.g = _run(client, "base zebra", n=2)
        self.g0, self.g1 = _outputs(self.g)
        self.e = _run(client, "edit zebra", inputs=[self.g0])
        self.e0 = _outputs(self.e)[0]
        self.f = _run(client, "child zebra", inputs=[self.e0])
        self.f0 = _outputs(self.f)[0]


@pytest.fixture
def chain(client: TestClient) -> Chain:
    _enable(client)
    return Chain(client)


# -- 範囲の計算 ------------------------------------------------------------------


def _preview_ids(client: TestClient, asset_id: str, scope: str) -> set[str]:
    response = client.post("/api/shares/preview", json={"asset_id": asset_id, "scope": scope})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["asset_count"] == len(body["assets"])
    return {a["id"] for a in body["assets"]}


def test_scopes(client: TestClient, chain: Chain) -> None:
    c = chain
    # 兄弟の出力(g1)は祖先でも子孫でもないので、どの範囲にも入らない。
    assert _preview_ids(client, c.e0, "single") == {c.e0}
    assert _preview_ids(client, c.e0, "ancestors") == {c.e0, c.g0}
    assert _preview_ids(client, c.e0, "lineage") == {c.e0, c.g0, c.f0}

    for scope, expected_assets, expected_runs in (
        ("single", {c.e0}, {c.e["id"]}),
        ("ancestors", {c.e0, c.g0}, {c.e["id"], c.g["id"]}),
        ("lineage", {c.e0, c.g0, c.f0}, {c.e["id"], c.g["id"], c.f["id"]}),
    ):
        share = _create(client, c.e0, scope)
        assert share["asset_count"] == len(expected_assets)
        body = _public(client, _token(share)).json()
        assert {a["id"] for a in body["assets"]} == expected_assets
        assert {r["id"] for r in body["runs"]} == expected_runs
        # 辺は範囲内のノードどうしだけ。
        nodes = expected_assets | expected_runs
        for edge in body["edges"]:
            assert edge["source"] in nodes and edge["target"] in nodes, edge

    body = _public(client, _token(_create(client, c.e0, "lineage"))).json()
    depths = {a["id"]: a["depth"] for a in body["assets"]}
    assert depths == {c.g0: -2, c.e0: 0, c.f0: 2}
    edges = {(e["source"], e["target"], e["kind"]) for e in body["edges"]}
    assert edges == {
        (c.g["id"], c.g0, "output"),
        (c.g0, c.e["id"], "input"),
        (c.e["id"], c.e0, "output"),
        (c.e0, c.f["id"], "input"),
        (c.f["id"], c.f0, "output"),
    }
    runs = {r["id"]: r for r in body["runs"]}
    assert runs[c.e["id"]]["prompt"] == "edit zebra"
    assert runs[c.e["id"]]["operation"] == "edit"
    assert runs[c.e["id"]]["model"] == _MODEL
    assert runs[c.e["id"]]["params"]["quality"] == "low"


def test_scope_is_fixed_at_creation(client: TestClient, chain: Chain) -> None:
    share = _create(client, chain.e0, "lineage")
    later = _outputs(_run(client, "later child", inputs=[chain.e0]))[0]
    body = _public(client, _token(share)).json()
    assert later not in {a["id"] for a in body["assets"]}
    assert _content(client, _token(share), later).status_code == 404
    # 新しく作れば載る。
    assert later in _preview_ids(client, chain.e0, "lineage")


def test_failed_runs_are_not_shown(client: TestClient, chain: Chain) -> None:
    """起点を入力にした失敗の Run は、子孫の範囲にも出ない(出力が無いので載らない)。"""
    with client.app.state.session_factory() as db:
        run = Run(
            provider="fake",
            model=_MODEL,
            operation=RunOperation.EDIT,
            prompt="failed zebra",
            params={},
            status=RunStatus.FAILED,
            error_code="contentFilter",
            finished_at=datetime.now(UTC),
        )
        db.add(run)
        db.flush()
        db.add(
            RunInput(
                run_id=run.id,
                asset_id=uuid.UUID(chain.e0),
                role=RunInputRole.IMAGE,
                position=0,
            )
        )
        db.commit()
        failed_id = str(run.id)
    body = _public(client, _token(_create(client, chain.e0, "lineage"))).json()
    assert failed_id not in {r["id"] for r in body["runs"]}
    assert "failed zebra" not in json.dumps(body)
    assert "contentFilter" not in json.dumps(body)


def test_deleted_assets_are_hidden_and_root_deletion_404s(client: TestClient, chain: Chain) -> None:
    share = _create(client, chain.e0, "lineage")
    token = _token(share)
    assert client.delete(f"/api/runs/{chain.f['id']}").status_code == 204  # f0 も論理削除される
    body = _public(client, token).json()
    assert chain.f0 not in {a["id"] for a in body["assets"]}
    assert chain.f["id"] not in {r["id"] for r in body["runs"]}
    assert _content(client, token, chain.f0).status_code == 404

    assert client.delete(f"/api/assets/{chain.e0}").status_code == 204
    assert _public(client, token).status_code == 404
    assert _content(client, token, chain.g0).status_code == 404
    # 一覧には残り、起点が削除されたことが分かる(取り消せるように)。
    rows = {r["id"]: r for r in client.get("/api/shares").json()["items"]}
    assert rows[share["id"]]["root_deleted"] is True
    # 削除済みの画像からは作れない。
    response = client.post("/api/shares", json={"asset_id": chain.e0, "scope": "single"})
    assert response.status_code == 404


# -- 公開の API ------------------------------------------------------------------


def test_public_content_and_scope_checks(client: TestClient, chain: Chain) -> None:
    share = _create(client, chain.e0, "ancestors")
    token = _token(share)
    for variant in ("thumb", "preview", "original"):
        response = _content(client, token, chain.g0, variant)
        assert response.status_code == 200, (variant, response.text)
        assert response.headers["cache-control"] == "private, max-age=300"
        assert "immutable" not in response.headers["cache-control"]
        assert response.headers["x-robots-tag"] == "noindex"
        assert response.headers["referrer-policy"] == "no-referrer"
    # 範囲外(兄弟の出力、子孫)と、存在しない id。
    assert _content(client, token, chain.g1).status_code == 404
    assert _content(client, token, chain.f0).status_code == 404
    assert _content(client, token, str(uuid.uuid4())).status_code == 404
    assert _content(client, token, "not-a-uuid").status_code == 404
    # 不明なトークン。
    assert _public(client, "x" * 43).status_code == 404
    assert _content(client, "x" * 43, chain.e0).status_code == 404


def test_original_download_has_no_lineage_chunk(client: TestClient, chain: Chain) -> None:
    token = _token(_create(client, chain.e0, "single"))
    response = _content(client, token, chain.e0, "original", download="1")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["content-type"] == "image/png"
    assert read_gakei_meta(response.content) is None
    # 本人のダウンロードには埋め込まれる(比較のため)。
    own = client.get(f"/api/assets/{chain.e0}/content?variant=original&download=1")
    assert read_gakei_meta(own.content) is not None


def test_disallowed_original_is_404(client: TestClient, chain: Chain) -> None:
    share = _create(client, chain.e0, "single", allow_original=False)
    assert share["allow_original"] is False
    token = _token(share)
    assert _content(client, token, chain.e0, "preview").status_code == 200
    assert _content(client, token, chain.e0, "original").status_code == 404
    assert _content(client, token, chain.e0, "original", download="1").status_code == 404
    assert _public(client, token).json()["allow_original"] is False


def test_disabled_feature_404s_everything(client: TestClient, chain: Chain) -> None:
    share = _create(client, chain.e0, "single")
    token = _token(share)
    _enable(client, False)
    assert _public(client, token).status_code == 404
    assert _content(client, token, chain.e0).status_code == 404
    response = client.post("/api/shares", json={"asset_id": chain.e0, "scope": "single"})
    assert response.status_code == 409
    response = client.post("/api/shares/preview", json={"asset_id": chain.e0, "scope": "single"})
    assert response.status_code == 409
    # 一覧と取り消しは無効のあいだもできる。
    assert [r["id"] for r in client.get("/api/shares").json()["items"]] == [share["id"]]
    # 有効に戻せば、取り消していないリンクはまた使える。
    _enable(client)
    assert _public(client, token).status_code == 200


def test_disabled_by_default(client: TestClient) -> None:
    assert client.get("/api/settings/share").json() == {"enabled": False}
    asset_id = _outputs(_run(client, "zebra"))[0]
    response = client.post("/api/shares", json={"asset_id": asset_id, "scope": "single"})
    assert response.status_code == 409


def test_revoke(client: TestClient, chain: Chain) -> None:
    share = _create(client, chain.e0, "single")
    token = _token(share)
    assert _public(client, token).status_code == 200
    assert client.delete(f"/api/shares/{share['id']}").status_code == 204
    assert _public(client, token).status_code == 404
    assert _content(client, token, chain.e0).status_code == 404
    assert client.get("/api/shares").json()["items"] == []
    # 取り消しは元に戻せない(もう一度取り消そうとしても 404)。
    assert client.delete(f"/api/shares/{share['id']}").status_code == 404
    # 同じ画像から作り直すと、新しいトークンになる。
    again = _create(client, chain.e0, "single")
    assert _token(again) != token


def test_access_is_recorded_on_page_only(client: TestClient, chain: Chain) -> None:
    share = _create(client, chain.e0, "single")
    token = _token(share)
    assert share["access_count"] == 0 and share["last_accessed_at"] is None
    _public(client, token)
    _public(client, token)
    _content(client, token, chain.e0)
    row = client.get("/api/shares").json()["items"][0]
    assert row["access_count"] == 2
    assert row["last_accessed_at"] is not None


def test_public_headers_on_errors_and_page(client: TestClient) -> None:
    response = _public(client, "unknown")
    assert response.status_code == 404
    assert response.headers["x-robots-tag"] == "noindex"
    assert response.headers["referrer-policy"] == "no-referrer"
    # 共有のページ(SPA)。フロントのビルドはテストから切り離してあるので 503 でも、ヘッダーは付く。
    page = client.get("/s/unknown")
    assert page.headers["x-robots-tag"] == "noindex"
    assert page.headers["referrer-policy"] == "no-referrer"
    # ほかのパスには付けない。
    assert "x-robots-tag" not in client.get("/api/settings/share").headers


def test_share_url_uses_public_base_url(client: TestClient, chain: Chain) -> None:
    share = _create(client, chain.e0, "single")
    assert share["url"].startswith("http://testserver/s/")
    assert len(_token(share)) >= 43


# -- 見せないもの ------------------------------------------------------------------

_PUBLIC_KEYS = {"root_asset_id", "scope", "allow_original", "created_at", "assets", "runs", "edges"}
_ASSET_KEYS = {"id", "kind", "mime", "width", "height", "created_at", "title", "run_id", "depth"}
_RUN_KEYS = {"id", "operation", "model", "prompt", "params", "created_at", "text_outputs"}


def test_public_response_hides_private_fields(client_oidc: TestClient) -> None:
    client = client_oidc
    login_as(client, "admin@example.com", "Admin Person")
    _enable(client)
    login_as(client, "alice@example.com", "Alice Secretname")
    g = _run(client, "base zebra", n=2)
    g0, g1 = _outputs(g)
    e0 = _outputs(_run(client, "edit zebra", inputs=[g0]))[0]
    assert client.patch(f"/api/assets/{e0}/title", json={"title": "My title"}).status_code == 200
    assert client.post(f"/api/assets/{e0}/tags", json={"name": "secrettag"}).status_code == 200
    group = client.post("/api/asset-groups", json={"name": "secret group"}).json()
    client.post(f"/api/asset-groups/{group['id']}/assets", json={"asset_ids": [e0]})
    share = _create(client, e0, "ancestors")

    response = _public(client, _token(share))
    assert response.status_code == 200
    body = response.json()
    assert set(body) == _PUBLIC_KEYS
    for asset in body["assets"]:
        assert set(asset) == _ASSET_KEYS
    for run in body["runs"]:
        assert set(run) == _RUN_KEYS
    assert {a["id"]: a["title"] for a in body["assets"]}[e0] == "My title"

    text = response.text
    with client.app.state.session_factory() as db:
        user_id = str(db.get(Share, uuid.UUID(share["id"])).created_by_user_id)
    for secret in (
        "alice@example.com",
        "Alice Secretname",
        user_id,
        "secrettag",
        "secret group",
        g1,  # 範囲外の兄弟
        "usage",
        "cost",
        "avatar",
        "embedded_meta",
        "created_by",
    ):
        assert secret not in text, secret


def test_public_params_filter() -> None:
    params = {
        "n": 1,
        "size": "1024x1024",
        "quality": "low",
        "background": None,
        "nested": {"asset_id": str(uuid.uuid4())},
        "list": [1, 2],
        "ref": f"asset {uuid.uuid4()}",
        "image": "gakei_" + "a" * 64 + ".png",
        "comfyui_workflow": {"id": str(uuid.uuid4()), "name": "wf"},
        "comfyui_prompt": {"1": {"inputs": {}}},
        "comfyui_uploads": {"images": ["gakei_" + "b" * 64 + ".png"]},
        "comfyui_seed": 42,
        "negative_prompt": "blurry",
    }
    assert public_params(params) == {
        "n": 1,
        "size": "1024x1024",
        "quality": "low",
        "background": None,
        "negative_prompt": "blurry",
        "seed": 42,
    }


# -- 本人だけ(認証モード) --------------------------------------------------------


def test_only_owner_can_list_and_revoke(client_oidc: TestClient) -> None:
    client = client_oidc
    login_as(client, "admin@example.com", "Admin")
    admin_cookie = client.cookies.get("gakei_session")
    _enable(client)
    login_as(client, "alice@example.com", "Alice")
    alice_cookie = client.cookies.get("gakei_session")
    asset_id = _outputs(_run(client, "alice zebra"))[0]
    share = _create(client, asset_id, "single")
    login_as(client, "bob@example.com", "Bob")
    bob_cookie = client.cookies.get("gakei_session")

    for cookie in (bob_cookie, admin_cookie):
        client.cookies.clear()
        client.cookies.set("gakei_session", cookie)
        assert client.get("/api/shares").json()["items"] == []
        assert client.delete(f"/api/shares/{share['id']}").status_code == 404
        # 見えない Asset からは作れない・範囲も見られない(存在しないものと同じ 404)。
        body = {"asset_id": asset_id, "scope": "single"}
        assert client.post("/api/shares", json=body).status_code == 404
        assert client.post("/api/shares/preview", json=body).status_code == 404

    client.cookies.clear()
    client.cookies.set("gakei_session", alice_cookie)
    assert [r["id"] for r in client.get("/api/shares").json()["items"]] == [share["id"]]
    # 公開のページは Cookie なしで見られる。
    assert _public(client, _token(share)).status_code == 200


def test_share_settings_update_requires_admin(client_oidc: TestClient) -> None:
    client = client_oidc
    login_as(client, "user@example.com", "User")
    assert client.patch("/api/settings/share", json={"enabled": True}).status_code == 403
    assert client.get("/api/settings/share").json() == {"enabled": False}


# -- 認証なしで応答するルートの洗い出し ----------------------------------------------

# ログインなしで 401 以外を返してよいルート。増やすときは、見せる範囲を検討してからここに足す。
_UNAUTHENTICATED_PREFIXES = (
    "/api/auth/",
    # 1回限りのアップロード・ダウンロード URL(URL のトークン自体が認可。ADR-0023 7章・8章)。
    "/api/uploads/",
    "/api/downloads/",
    # ログイン不要の共有リンク(ADR-0029 6章)。
    "/api/public/",
)
# 前方一致ではなく完全一致で許すルート。生存確認(Issue #43)は `{"status": "ok"}` だけを返す。
_UNAUTHENTICATED_PATHS = frozenset({"/api/health"})


def test_only_known_routes_answer_without_login(client_oidc: TestClient) -> None:
    """認証モードで Cookie なしのとき、401 以外を返すルートは上の一覧の下だけ。また、
    `/api/public/` の下は共有リンクの2つのルート(いずれも GET)だけ。"""
    client = client_oidc
    unexpected: list[str] = []
    public_routes: set[tuple[str, str]] = set()
    for path, operations in client.app.openapi()["paths"].items():
        for method in _HTTP_METHODS:
            if method not in operations:
                continue
            if path.startswith("/api/public/"):
                public_routes.add((method, path))
            if path.startswith("/api/auth/"):
                # ログインの流れそのもの(callback は IdP の応答を前提にする)。呼ばずに許す。
                continue
            client.cookies.clear()
            filled = _PATH_PARAM_RE.sub("00000000-0000-4000-8000-000000000000", path)
            response = client.request(method, filled)
            if (
                response.status_code != 401
                and not path.startswith(_UNAUTHENTICATED_PREFIXES)
                and path not in _UNAUTHENTICATED_PATHS
            ):
                unexpected.append(f"{method.upper()} {path} -> {response.status_code}")
    assert not unexpected, "ログインなしで応答するルート:\n" + "\n".join(unexpected)
    assert public_routes == {
        ("get", "/api/public/shares/{token}"),
        ("get", "/api/public/shares/{token}/assets/{asset_id}/content"),
    }


def test_upload_png_asset_can_be_shared(client: TestClient) -> None:
    """アップロードした画像(Run なし)も共有できる。Run は返さない。"""
    _enable(client)
    response = client.post(
        "/api/assets",
        files={"file": ("x.png", make_png_bytes(), "image/png")},
        data={"kind": "upload"},
    )
    asset_id = response.json()["id"]
    body = _public(client, _token(_create(client, asset_id, "ancestors"))).json()
    assert [a["id"] for a in body["assets"]] == [asset_id]
    assert body["assets"][0]["run_id"] is None
    assert body["runs"] == []
    with client.app.state.session_factory() as db:
        assert db.get(Asset, uuid.UUID(asset_id)) is not None


def test_public_run_includes_text_outputs(client: TestClient) -> None:
    """ADR-0030 4章: 公開の API の Run に最終プロンプト(PE の出力)を出す。"""
    _enable(client)
    detail = _run(client, "pe share")
    asset_id = _outputs(detail)[0]
    share = _create(client, asset_id, "single")
    runs = _public(client, _token(share)).json()["runs"]
    assert runs[0]["text_outputs"] is None

    item = {
        "role": "final_prompt",
        "node_id": "472",
        "class_type": "PreviewAny",
        "title": None,
        "text": "A warm, cozy room",
        "truncated": True,
    }
    with client.app.state.session_factory() as db:
        run = db.get(Run, uuid.UUID(detail["id"]))
        assert run is not None
        run.text_outputs = [item]
        db.commit()
    runs = _public(client, _token(share)).json()["runs"]
    assert runs[0]["text_outputs"] == [item]


# -- Run の詳細(ADR-0029 3章、2026-09-30 追記) --------------------------------------


def test_run_detail_edges_only_cover_shared_inputs_and_outputs(client: TestClient) -> None:
    """共有のページの Run の詳細は `edges` から入力・出力を組み立てる。範囲外の入力の辺は返さず、
    主たる親(`position = 0` の `image`)には `primary` と役割・位置が付く。"""
    _enable(client)
    g0 = _outputs(_run(client, "base zebra"))[0]
    x0 = _outputs(_run(client, "other zebra"))[0]
    e = _run(client, "edit zebra", inputs=[g0, x0], n=2)
    e0, e1 = _outputs(e)
    # g0 を起点に子孫まで: E の出力(e0, e1)は入るが、E のもう一つの入力 x0 は祖先でも子孫でもない。
    body = _public(client, _token(_create(client, g0, "lineage"))).json()
    assert {a["id"] for a in body["assets"]} == {g0, e0, e1}
    inputs = [ed for ed in body["edges"] if ed["kind"] == "input" and ed["target"] == e["id"]]
    assert inputs == [
        {
            "source": g0,
            "target": e["id"],
            "kind": "input",
            "role": "image",
            "position": 0,
            "output_index": None,
            "primary": True,
        }
    ]
    outputs = {
        ed["target"]: ed["output_index"]
        for ed in body["edges"]
        if ed["kind"] == "output" and ed["source"] == e["id"]
    }
    assert outputs == {e0: 0, e1: 1}
    assert x0 not in json.dumps(body)


def test_canceled_run_is_hidden_from_run_detail(client: TestClient, chain: Chain) -> None:
    """取り消した Run は、出力の画像が共有に含まれていても Run としては返さない(Run の詳細の
    直リンクも、画面では「このリンクは無効です」になる)。"""
    share = _create(client, chain.e0, "ancestors")
    with client.app.state.session_factory() as db:
        run = db.get(Run, uuid.UUID(chain.e["id"]))
        assert run is not None
        run.status = RunStatus.CANCELED
        db.commit()
    body = _public(client, _token(share)).json()
    assert chain.e["id"] not in {r["id"] for r in body["runs"]}
    assert "edit zebra" not in json.dumps(body)
    assert all(chain.e["id"] not in (ed["source"], ed["target"]) for ed in body["edges"])
    assert {a["id"]: a["run_id"] for a in body["assets"]}[chain.e0] is None


def test_run_detail_path_serves_spa(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, data_dir: Path
) -> None:
    """`/s/{トークン}/runs/{run_id}` と `/s/{トークン}/lineage`(全画面の系列グラフ)も
    SPA の index.html を返す(画面側で解釈する)。"""
    import app.main as app_main
    from app.main import create_app

    dist_dir = tmp_path / "frontend-dist"
    dist_dir.mkdir()
    (dist_dir / "index.html").write_text("<html>gakei spa</html>", encoding="utf-8")
    monkeypatch.setattr(app_main, "_FRONTEND_DIST", dist_dir)
    app = create_app(Settings(_env_file=None, data_dir=data_dir, fake_provider=True))
    with TestClient(app) as test_client:
        for path in ("/s/abc", f"/s/abc/runs/{uuid.uuid4()}", "/s/abc/lineage"):
            response = test_client.get(path)
            assert response.status_code == 200, path
            assert "gakei spa" in response.text
            assert response.headers["x-robots-tag"] == "noindex"
