"""ADR-0025: 認証モードでは本人のものだけを見せる。

利用者 A・B と管理者 C を作り、A のデータが B にも C にも見えない・操作できないことを、
REST の全エンドポイント(`app.openapi()` から機械的に列挙)と MCP の各ツールで確かめる。
所有者が記録されていない(個人モードの頃の)データは C にだけ見える。個人モードは従来どおり全件。
"""

from __future__ import annotations

import io
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.domain.models import (
    AppUser,
    Asset,
    AssetGroup,
    AssetKind,
    PromptSet,
    PromptSetItem,
    Run,
    RunInput,
    RunInputRole,
    RunOperation,
    RunStatus,
)
from tests.conftest import login_as, make_png_bytes, wait_for_run_terminal
from tests.test_mcp import _call, _enable, _error_text, _ok, _rpc

_PATH_PARAM_RE = re.compile(r"\{([^}]+)\}")
_HTTP_METHODS = ("get", "post", "put", "patch", "delete")
_MODEL = "gpt-image-2.5-sunburst"


# -- 準備 ----------------------------------------------------------------------


def _session_cookie(client: TestClient) -> str:
    value = client.cookies.get("gakei_session")
    assert value, "ログインの Cookie がありません"
    return value


def _use(client: TestClient, cookie: str | None) -> None:
    """Cookie を差し替えて、以後のリクエストをその利用者として送る(None は未ログイン)。"""
    client.cookies.clear()
    if cookie is not None:
        client.cookies.set("gakei_session", cookie)


def _upload(client: TestClient, kind: str = "upload", data: bytes | None = None, **form: str):
    response = client.post(
        "/api/assets",
        files={"file": ("x.png", io.BytesIO(data or make_png_bytes()), "image/png")},
        data={"kind": kind, **form},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _generate(client: TestClient, prompt: str, **extra: Any) -> dict:
    response = client.post(
        "/api/runs",
        json={"operation": "generate", "model": _MODEL, "prompt": prompt, "params": {"n": 1}}
        | extra,
    )
    assert response.status_code == 202, response.text
    detail = wait_for_run_terminal(client, response.json()["id"])
    assert detail["status"] == "succeeded", detail
    return detail


@dataclass
class World:
    client: TestClient
    cookies: dict[str, str]
    user_ids: dict[str, uuid.UUID]
    tokens: dict[str, str]
    # A のデータ
    a_run: str = ""
    a_output: str = ""
    a_upload: str = ""
    a_mask: str = ""
    a_group: str = ""
    a_prompt_set: str = ""
    a_prompt_item: str = ""
    a_token_id: str = ""
    # B のデータ
    b_group: str = ""
    b_upload: str = ""
    # 網羅テストで asset_id に入れる値(生成 Asset とアップロード Asset の両方で確かめる)。
    sweep_asset: str = ""
    # 所有者 NULL(個人モードの頃)のデータ
    legacy: dict[str, str] = field(default_factory=dict)

    def as_(self, who: str | None) -> TestClient:
        _use(self.client, None if who is None else self.cookies[who])
        return self.client

    def bearer(self, who: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.tokens[who]}"}


def _insert_legacy(client: TestClient, template_asset_id: str) -> dict[str, str]:
    """所有者が記録されていない(`created_by_user_id` が NULL)データを DB に直接入れる。"""
    with client.app.state.session_factory() as db:
        template = db.get(Asset, uuid.UUID(template_asset_id))
        assert template is not None

        def _asset(kind: AssetKind, run_id: uuid.UUID | None = None) -> Asset:
            return Asset(
                kind=kind,
                sha256=template.sha256,
                blob_key=template.blob_key,
                mime=template.mime,
                width=template.width,
                height=template.height,
                bytes=template.bytes,
                produced_by_run_id=run_id,
                output_index=0 if run_id else None,
            )

        upload = _asset(AssetKind.UPLOAD)
        run = Run(
            provider="fake",
            model=_MODEL,
            operation=RunOperation.GENERATE,
            prompt="legacy zebra prompt",
            params={},
            status=RunStatus.SUCCEEDED,
            finished_at=datetime.now(UTC),
        )
        db.add_all([upload, run])
        db.flush()
        output = _asset(AssetKind.GENERATED, run.id)
        group = AssetGroup(name="legacy group")
        prompt_set = PromptSet(name="legacy zebra set")
        db.add_all([output, group, prompt_set])
        db.commit()
        return {
            "upload": str(upload.id),
            "run": str(run.id),
            "output": str(output.id),
            "group": str(group.id),
            "prompt_set": str(prompt_set.id),
        }


@pytest.fixture
def world(client_oidc: TestClient) -> World:
    client = client_oidc
    cookies: dict[str, str] = {}
    tokens: dict[str, str] = {}
    emails = {"A": "alice@example.com", "B": "bob@example.com", "C": "admin@example.com"}

    login_as(client, emails["C"], "Admin C")
    _enable(client)
    for who, email in emails.items():
        login_as(client, email, f"User {who}")
        cookies[who] = _session_cookie(client)
        response = client.post("/api/users/me/api-tokens", json={"name": "agent"})
        assert response.status_code == 201, response.text
        tokens[who] = response.json()["token"]

    with client.app.state.session_factory() as db:
        user_ids = {
            who: db.query(AppUser).filter(AppUser.email == email).one().id
            for who, email in emails.items()
        }

    w = World(client=client, cookies=cookies, user_ids=user_ids, tokens=tokens)

    c = w.as_("A")
    detail = _generate(c, "alice secret zebra")
    w.a_run = detail["id"]
    w.a_output = detail["outputs"][0]["asset_id"]
    w.a_upload = _upload(c)["id"]
    w.a_mask = _upload(c, "mask")["id"]
    group = c.post("/api/asset-groups", json={"name": "alice group"})
    assert group.status_code == 201
    w.a_group = group.json()["id"]
    assert (
        c.post(f"/api/asset-groups/{w.a_group}/assets", json={"asset_ids": [w.a_upload]})
    ).status_code == 200
    prompt_set = c.post(
        "/api/prompt-sets",
        json={"name": "alice zebra set", "items": [{"label": "l", "text": "zebra text"}]},
    )
    assert prompt_set.status_code == 201, prompt_set.text
    w.a_prompt_set = prompt_set.json()["id"]
    w.a_prompt_item = prompt_set.json()["items"][0]["id"]
    w.a_token_id = c.get("/api/users/me/api-tokens").json()["items"][0]["id"]
    # タイトルとタグ(ADR-0024)。タグ名 alicetag は A の Asset にだけ付ける。
    assert (
        c.patch(f"/api/assets/{w.a_output}/title", json={"title": "alice title"})
    ).status_code == 200
    assert (c.post(f"/api/assets/{w.a_output}/tags", json={"name": "alicetag"})).status_code == 200

    c = w.as_("B")
    w.b_upload = _upload(c, data=make_png_bytes(32, 32, (1, 2, 3)))["id"]
    group = c.post("/api/asset-groups", json={"name": "bob group"})
    w.b_group = group.json()["id"]
    assert (c.post(f"/api/assets/{w.b_upload}/tags", json={"name": "bobtag"})).status_code == 200

    w.legacy = _insert_legacy(client, w.a_upload)
    return w


# -- REST: リソース id を取る全ルートが他人の id で 404 ---------------------------

# パスの引数名 → A のどのデータの id を入れるか。新しい引数名のルートが増えたら、この表に
# 無いのでテストが落ちる(可視性の適用を検討してから足す)。
_PARAM_SOURCES = {
    "run_id": "a_run",
    "asset_id": "sweep_asset",
    "group_id": "a_group",
    "prompt_set_id": "a_prompt_set",
    "item_id": "a_prompt_item",
    "token_id": "a_token_id",
    "user_id": "user:A",
    "index": "literal:0",
    # タグ名(ADR-0024)。A の Asset に付けたタグ(`world` で付ける)。
    "name": "literal:alicetag",
}

# 利用者のデータではないので対象外のルート(ADR-0025 5章: 管理者設定は利用者のデータと別)。
_EXCLUDED_PREFIXES = (
    "/api/auth/",
    "/api/uploads/",
    "/api/comfyui/",
    "/api/settings/annotation/onnx/",
)


def _bodies(w: World) -> dict[tuple[str, str], Any]:
    """本文の検証(422)より先に 404 が確かめられるよう、正しい形の本文を渡す。"""
    return {
        ("post", "/api/asset-groups/{group_id}/assets"): {"asset_ids": [w.b_upload]},
        ("post", "/api/asset-groups/{group_id}/assets/remove"): {"asset_ids": [w.b_upload]},
        ("patch", "/api/asset-groups/{group_id}"): {"name": "hijack"},
        ("patch", "/api/prompt-sets/{prompt_set_id}"): {"name": "hijack"},
        ("post", "/api/prompt-sets/{prompt_set_id}/items"): {"text": "hijack"},
        ("patch", "/api/prompt-sets/{prompt_set_id}/items/{item_id}"): {"text": "hijack"},
        ("patch", "/api/assets/{asset_id}/title"): {"title": "hijack"},
        ("post", "/api/assets/{asset_id}/tags"): {"name": "hijack"},
    }


def _id_routes(client: TestClient) -> list[tuple[str, str, list[str]]]:
    routes: list[tuple[str, str, list[str]]] = []
    for path, operations in client.app.openapi()["paths"].items():
        params = _PATH_PARAM_RE.findall(path)
        if not params or path.startswith(_EXCLUDED_PREFIXES):
            continue
        for method in _HTTP_METHODS:
            if method in operations:
                routes.append((method, path, params))
    return routes


def _fill(w: World, path: str) -> str:
    def _value(match: re.Match[str]) -> str:
        source = _PARAM_SOURCES.get(match.group(1))
        assert source is not None, f"引数 {match.group(1)} の扱いが決まっていません: {path}"
        if source.startswith("literal:"):
            return source.removeprefix("literal:")
        if source.startswith("user:"):
            return str(w.user_ids[source.removeprefix("user:")])
        return getattr(w, source)

    return _PATH_PARAM_RE.sub(_value, path)


def test_every_id_route_returns_404_for_others(world: World) -> None:
    """A のリソース id を取る全 (method, path) は、B と管理者 C には 404。A 本人は 404 以外
    (存在は本人にだけ分かる)。書き換え系を先に呼んでも A のデータが変わらないことも確かめる。"""
    w = world
    bodies = _bodies(w)
    routes = _id_routes(w.client)
    assert len(routes) >= 20, routes  # 列挙できていること

    failures: list[str] = []
    for who, asset in (("B", w.a_output), ("C", w.a_output), ("B", w.a_upload), ("C", w.a_mask)):
        w.sweep_asset = asset
        c = w.as_(who)
        for method, path, _ in routes:
            response = c.request(method, _fill(w, path), json=bodies.get((method, path)))
            if response.status_code != 404:
                failures.append(f"{who}: {method.upper()} {path} -> {response.status_code}")
    assert not failures, "他人の id で 404 にならないルート:\n" + "\n".join(failures)

    # 他人の操作で A のデータは何も変わっていない。
    c = w.as_("A")
    assert c.get(f"/api/runs/{w.a_run}").json()["deleted_at"] is None
    a_output = c.get(f"/api/assets/{w.a_output}").json()
    assert a_output["deleted_at"] is None
    # タイトルとタグ(ADR-0024)も他人には変えられない。
    assert a_output["title"] == "alice title"
    assert [t["name"] for t in a_output["tags"]] == ["alicetag"]
    groups = {g["id"]: g for g in c.get("/api/asset-groups").json()["items"]}
    assert groups[w.a_group]["name"] == "alice group"
    assert groups[w.a_group]["member_count"] == 1
    sets = {s["id"]: s for s in c.get("/api/prompt-sets").json()["items"]}
    assert sets[w.a_prompt_set]["name"] == "alice zebra set"
    assert [i["text"] for i in sets[w.a_prompt_set]["items"]] == ["zebra text"]
    assert len(c.get("/api/users/me/api-tokens").json()["items"]) == 1

    # 本人には見える(404 にならない)ことを代表的なルートで確かめる。
    for path in (
        f"/api/runs/{w.a_run}",
        f"/api/assets/{w.a_output}",
        f"/api/assets/{w.a_output}/content?variant=thumb",
        f"/api/assets/{w.a_output}/lineage",
        f"/api/runs/{w.a_run}/events",
    ):
        assert c.get(path).status_code == 200, path


def test_lists_and_search_show_only_own(world: World) -> None:
    w = world
    a_ids = {w.a_run, w.a_output, w.a_upload, w.a_mask, w.a_group, w.a_prompt_set}
    legacy_ids = set(w.legacy.values())

    def _collect(c: TestClient) -> set[str]:
        ids: set[str] = set()
        ids |= {r["id"] for r in c.get("/api/runs?limit=200").json()["items"]}
        for r in c.get("/api/runs?limit=200").json()["items"]:
            ids |= {o["asset_id"] for o in r["outputs"]}
        ids |= {a["id"] for a in c.get("/api/assets?limit=200").json()["items"]}
        ids |= {a["id"] for a in c.get("/api/assets?limit=200&ungrouped=true").json()["items"]}
        ids |= {g["id"] for g in c.get("/api/asset-groups").json()["items"]}
        ids |= {g["cover_asset_id"] for g in c.get("/api/asset-groups").json()["items"]}
        ids |= {p["id"] for p in c.get("/api/prompt-sets").json()["items"]}
        found = c.get("/api/search", params={"q": "zebra"}).json()
        ids |= {r["id"] for r in found["runs"]}
        ids |= {a["id"] for a in found["assets"]}
        ids |= {p["id"] for p in found["prompt_sets"]}
        return ids

    seen_a = _collect(w.as_("A"))
    assert a_ids <= seen_a
    assert not (legacy_ids & seen_a)

    seen_b = _collect(w.as_("B"))
    assert not (a_ids & seen_b)
    assert not (legacy_ids & seen_b)
    assert w.b_upload in seen_b

    # 管理者 C: 他人のものは見えず、所有者 NULL のものだけ見える。
    seen_c = _collect(w.as_("C"))
    assert not (a_ids & seen_c)
    assert w.b_upload not in seen_c
    assert {w.legacy["run"], w.legacy["output"], w.legacy["upload"]} <= seen_c
    assert {w.legacy["group"], w.legacy["prompt_set"]} <= seen_c

    # グループ指定の一覧: 他人のグループは 404。
    c = w.as_("B")
    assert c.get(f"/api/assets?group_id={w.a_group}").status_code == 404


def test_null_owned_data_is_admin_only(world: World) -> None:
    w = world
    paths = [
        f"/api/runs/{w.legacy['run']}",
        f"/api/assets/{w.legacy['upload']}",
        f"/api/assets/{w.legacy['output']}",
        f"/api/assets/{w.legacy['output']}/content?variant=thumb",
        f"/api/assets/{w.legacy['output']}/lineage",
    ]
    for who, expected in (("A", 404), ("B", 404), ("C", 200)):
        c = w.as_(who)
        for path in paths:
            assert c.get(path).status_code == expected, (who, path)
    c = w.as_("B")
    assert c.patch(f"/api/asset-groups/{w.legacy['group']}", json={"name": "x"}).status_code == 404
    assert (
        c.patch(f"/api/prompt-sets/{w.legacy['prompt_set']}", json={"name": "x"}).status_code == 404
    )
    c = w.as_("C")
    assert c.patch(f"/api/asset-groups/{w.legacy['group']}", json={"name": "ok"}).status_code == 200


def test_inputs_and_references_to_others_are_rejected_like_missing(world: World) -> None:
    w = world
    c = w.as_("B")
    missing = str(uuid.uuid4())

    def _edit(asset_id: str, **extra: Any):
        return c.post(
            "/api/runs",
            json={
                "operation": "edit",
                "model": _MODEL,
                "prompt": "steal",
                "params": {},
                "inputs": [{"asset_id": asset_id, "role": "image", "position": 0}],
            }
            | extra,
        )

    other = _edit(w.a_upload)
    absent = _edit(missing)
    assert other.status_code == absent.status_code == 422
    assert other.json()["detail"] == absent.json()["detail"].replace(missing, w.a_upload)

    # 他人のマスク。
    mask = c.post(
        "/api/runs",
        json={
            "operation": "edit",
            "model": _MODEL,
            "prompt": "steal",
            "params": {},
            "inputs": [
                {"asset_id": w.b_upload, "role": "image", "position": 0},
                {"asset_id": w.a_mask, "role": "mask", "position": 0},
            ],
        },
    )
    assert mask.status_code == 422

    # 他人のグループを出力先に。
    to_group = c.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": _MODEL,
            "prompt": "x",
            "params": {},
            "asset_group_id": w.a_group,
        },
    )
    assert to_group.status_code == 404

    # 自分のグループに他人の Asset を入れる。
    add = c.post(f"/api/asset-groups/{w.b_group}/assets", json={"asset_ids": [w.a_upload]})
    assert add.status_code == 404

    # 並べ替えに他人のグループ id を混ぜる。
    reorder = c.put("/api/asset-groups/order", json={"group_ids": [w.b_group, w.a_group]})
    assert reorder.status_code == 422

    # スケッチの下地・マスクの置き換え元に他人の Asset。
    sketch = c.post(
        "/api/assets",
        files={"file": ("s.png", io.BytesIO(make_png_bytes()), "image/png")},
        data={"kind": "sketch", "source_asset_id": w.a_upload},
    )
    assert sketch.status_code == 422
    replace = c.post(
        "/api/assets",
        files={"file": ("m.png", io.BytesIO(make_png_bytes()), "image/png")},
        data={"kind": "mask", "replaces_asset_id": w.a_mask},
    )
    assert replace.status_code == 422
    assert w.as_("A").get(f"/api/assets/{w.a_mask}").json()["deleted_at"] is None

    # アバターの元にする。
    c = w.as_("B")
    avatar = c.post("/api/users/me/avatar/from-asset", json={"asset_id": w.a_upload})
    assert avatar.status_code == 422

    # 料金の見積もりで他人の Asset の大きさを数えない。
    estimate = c.get(
        "/api/pricing/estimate",
        params={
            "model": _MODEL,
            "operation": "edit",
            "size": "1024x1024",
            "quality": "low",
            "input_asset_ids": w.a_upload,
        },
    ).json()
    assert estimate["input_images"] == []


def test_reupload_does_not_match_others_asset(world: World) -> None:
    """ADR-0025 4章: ダウンロードした PNG の再アップロードの重複判定は本人の Asset の中だけ。"""
    w = world
    c = w.as_("A")
    downloaded = c.get(f"/api/assets/{w.a_output}/content?variant=original&download=1")
    assert downloaded.status_code == 200
    data = downloaded.content

    # 本人なら既存の Asset が返る(従来どおり)。
    mine = _upload(c, data=data)
    assert mine["ingest_outcome"] == "matched_existing"
    assert mine["id"] == w.a_output

    for who in ("B", "C"):
        c = w.as_(who)
        theirs = _upload(c, data=data)
        assert theirs["ingest_outcome"] == "created"
        assert theirs["id"] != w.a_output
        origin = theirs["origin"]
        assert origin["asset_id"] is None
        assert origin["asset_hidden"] is True
        assert origin["same_instance"] is True
        assert c.get(f"/api/assets/{theirs['id']}").json()["origin"]["asset_hidden"] is True

        # 系列グラフに A の Asset・Run をローカルのノードとして含めない。
        lineage = c.get(f"/api/assets/{theirs['id']}/lineage").json()
        for node in lineage["nodes"]:
            if node["id"] in (w.a_output, w.a_run):
                assert node["embedded"] is True
                assert node["local_hidden"] is True
            assert node["resolved_asset_id"] not in (w.a_output, w.a_upload)

        # 見えないものを指す由来は、ダウンロードに埋め込む系列にもローカルの行として入らない。
        again = c.get(f"/api/assets/{theirs['id']}/content?variant=original&download=1")
        assert again.status_code == 200

    # MCP の upload_image も同じ規則。
    import base64

    result = _ok(
        _call(
            w.client,
            "upload_image",
            {"data_base64": base64.b64encode(data).decode("ascii")},
            w.bearer("B"),
        )
    )
    assert result["ingest_outcome"] == "created"
    assert result["asset_id"] != w.a_output


def test_legacy_cross_user_edges_are_hidden(world: World) -> None:
    """以前(全員全件のころ)に B が A の Asset を入力にした Run があっても、A と B の
    どちらの画面にも相手の Asset・Run は出ない。"""
    w = world
    with w.client.app.state.session_factory() as db:
        template = db.get(Asset, uuid.UUID(w.a_upload))
        run = Run(
            provider="fake",
            model=_MODEL,
            operation=RunOperation.EDIT,
            prompt="bob used alice",
            params={},
            status=RunStatus.SUCCEEDED,
            created_by_user_id=w.user_ids["B"],
            finished_at=datetime.now(UTC),
        )
        db.add(run)
        db.flush()
        db.add(
            RunInput(
                run_id=run.id,
                asset_id=uuid.UUID(w.a_upload),
                role=RunInputRole.IMAGE,
                position=0,
            )
        )
        output = Asset(
            kind=AssetKind.GENERATED,
            sha256=template.sha256,
            blob_key=template.blob_key,
            mime=template.mime,
            width=template.width,
            height=template.height,
            bytes=template.bytes,
            produced_by_run_id=run.id,
            output_index=0,
        )
        db.add(output)
        db.commit()
        b_run, b_output = str(run.id), str(output.id)

    c = w.as_("A")
    lineage = c.get(f"/api/assets/{w.a_upload}/lineage").json()
    node_ids = {n["id"] for n in lineage["nodes"]}
    assert b_run not in node_ids and b_output not in node_ids

    c = w.as_("B")
    detail = c.get(f"/api/runs/{b_run}").json()
    assert detail["inputs"] == []
    assert detail["primary_parent_asset_id"] is None
    assert detail["input_count"] == 1
    lineage = c.get(f"/api/assets/{b_output}/lineage").json()
    assert w.a_upload not in {n["id"] for n in lineage["nodes"]}
    listed = next(r for r in c.get("/api/runs").json()["items"] if r["id"] == b_run)
    assert listed["primary_parent_asset_id"] is None


def test_sse_and_partials_of_others_are_404(world: World) -> None:
    w = world
    c = w.as_("B")
    assert c.get(f"/api/runs/{w.a_run}/events").status_code == 404
    assert c.get(f"/api/runs/{w.a_run}/partials/0").status_code == 404
    # 本人には従来どおり流れる。
    c = w.as_("A")
    with c.stream("GET", f"/api/runs/{w.a_run}/events") as stream:
        assert stream.status_code == 200


def test_content_by_bearer_token_is_owner_only(world: World) -> None:
    w = world
    w.as_(None)
    path = f"/api/assets/{w.a_output}/content?variant=original"
    assert w.client.get(path, headers=w.bearer("A")).status_code == 200
    assert w.client.get(path, headers=w.bearer("B")).status_code == 404
    assert w.client.get(path, headers=w.bearer("C")).status_code == 404


def test_mcp_tools_are_owner_only(world: World) -> None:
    w = world
    w.as_(None)
    b = w.bearer("B")

    def _err(name: str, args: dict[str, Any]) -> str:
        return _error_text(_call(w.client, name, args, b))

    assert "not found" in _err("get_asset", {"asset_id": w.a_output})
    assert "not found" in _err("get_run", {"run_id": w.a_run})
    assert "not found" in _err("cancel_run", {"run_id": w.a_run})
    assert "not found" in _err("move_to_group", {"group_id": w.a_group, "asset_ids": [w.b_upload]})
    assert (
        "not found"
        in _err("move_to_group", {"group_id": w.b_group, "asset_ids": [w.a_upload]}).lower()
    )
    assert "not found" in _err("search_assets", {"group_id": w.a_group})
    # 他人の入力・グループは、存在しないものを指定したときと同じエラー(Run も作らない)。
    missing = str(uuid.uuid4())

    def _edit_error(asset_id: str) -> str:
        args = {"prompt": "steal", "operation": "edit", "input_asset_ids": [asset_id]}
        return _err("generate_image", args).replace(asset_id, "<id>")

    assert _edit_error(w.a_upload) == _edit_error(missing)
    assert _err("generate_image", {"prompt": "x", "group_id": w.a_group}).replace(
        w.a_group, "<id>"
    ) == _err("generate_image", {"prompt": "x", "group_id": missing}).replace(missing, "<id>")

    listed = _ok(_call(w.client, "search_assets", {"limit": 50}, b))["items"]
    assert w.a_output not in {i["asset_id"] for i in listed}
    found = _ok(_call(w.client, "search_assets", {"query": "zebra"}, b))["items"]
    assert found == []
    runs = _ok(_call(w.client, "list_runs", {"limit": 50}, b))["items"]
    assert w.a_run not in {r["run_id"] for r in runs}
    groups = _ok(_call(w.client, "list_groups", {}, b))["items"]
    assert {g["id"] for g in groups} == {w.b_group}
    sets = _ok(_call(w.client, "list_prompt_sets", {}, b))["items"]
    assert sets == []
    estimate = _ok(
        _call(
            w.client,
            "estimate_cost",
            {"params": {"size": "1024x1024", "quality": "low"}, "input_asset_ids": [w.a_upload]},
            b,
        )
    )
    assert estimate["input_image_tokens"] == 0

    # A 本人には見える。
    a = w.bearer("A")
    assert _ok(_call(w.client, "get_asset", {"asset_id": w.a_output}, a))["asset_id"] == w.a_output
    found = _ok(_call(w.client, "search_assets", {"query": "zebra"}, a))["items"]
    assert w.a_output in {i["asset_id"] for i in found}
    assert {s["id"] for s in _ok(_call(w.client, "list_prompt_sets", {}, a))["items"]} == {
        w.a_prompt_set
    }

    # 管理者 C のトークンでも他人のものは見えず、所有者 NULL のものは見える。
    cc = w.bearer("C")
    assert "not found" in _error_text(_call(w.client, "get_asset", {"asset_id": w.a_output}, cc))
    legacy = _ok(_call(w.client, "get_asset", {"asset_id": w.legacy["output"]}, cc))
    assert legacy["asset_id"] == w.legacy["output"]
    assert "not found" in _error_text(
        _call(w.client, "get_asset", {"asset_id": w.legacy["output"]}, b)
    )


def test_list_runs_tool_has_no_created_by_argument(world: World) -> None:
    world.as_(None)
    tools = _rpc(world.client, "tools/list", {}, world.bearer("A")).json()["result"]["tools"]
    list_runs = next(t for t in tools if t["name"] == "list_runs")
    assert "created_by" not in list_runs["inputSchema"]["properties"]


# -- 個人モードは従来どおり全件 --------------------------------------------------


def test_none_mode_still_sees_everything(client: TestClient) -> None:
    """個人モードでは、認証モードで作られた(所有者のある)データも含めて全件が見える。"""
    detail = _generate(client, "none mode zebra")
    with client.app.state.session_factory() as db:
        user = AppUser(issuer="https://idp.test", subject="someone", email="x@example.com")
        db.add(user)
        db.flush()
        run = Run(
            provider="fake",
            model=_MODEL,
            operation=RunOperation.GENERATE,
            prompt="owned zebra",
            params={},
            status=RunStatus.SUCCEEDED,
            created_by_user_id=user.id,
        )
        prompt_set = PromptSet(name="owned zebra set", created_by_user_id=user.id)
        db.add_all([run, prompt_set])
        db.flush()
        db.add(PromptSetItem(prompt_set_id=prompt_set.id, text="t", position=0))
        db.commit()
        run_id, set_id = str(run.id), str(prompt_set.id)

    run_ids = {r["id"] for r in client.get("/api/runs").json()["items"]}
    assert {detail["id"], run_id} <= run_ids
    assert client.get(f"/api/runs/{run_id}").status_code == 200
    assert set_id in {p["id"] for p in client.get("/api/prompt-sets").json()["items"]}
    found = client.get("/api/search", params={"q": "zebra"}).json()
    assert {detail["id"], run_id} <= {r["id"] for r in found["runs"]}


def test_tags_and_titles_are_only_for_own_assets(world: World) -> None:
    """ADR-0024 × ADR-0025: タグの一覧・件数、タグの絞り込み、検索、MCP のタイトルとタグは、
    本人に見える Asset だけで決まる。他人のタグ名は漏れない。"""
    w = world

    def _tag_names(who: str, q: str | None = None) -> dict[str, int]:
        params = {"q": q} if q else {}
        response = w.as_(who).get("/api/tags", params=params)
        assert response.status_code == 200, response.text
        return {item["name"]: item["count"] for item in response.json()["items"]}

    assert _tag_names("A") == {"alicetag": 1}
    assert _tag_names("B") == {"bobtag": 1}
    assert _tag_names("C") == {}  # 管理者にも他人のタグは見えない
    assert _tag_names("B", "alice") == {}

    # 同じタグ名を B も付けると、件数はそれぞれ自分の分だけ。
    c = w.as_("B")
    assert (c.post(f"/api/assets/{w.b_upload}/tags", json={"name": "alicetag"})).status_code == 200
    assert _tag_names("B") == {"bobtag": 1, "alicetag": 1}
    assert _tag_names("A") == {"alicetag": 1}

    # ?tag= の絞り込みも本人の Asset だけ。
    listed = w.as_("B").get("/api/assets", params={"tag": "alicetag"}).json()["items"]
    assert [a["id"] for a in listed] == [w.b_upload]
    listed = w.as_("A").get("/api/assets", params={"tag": "alicetag"}).json()["items"]
    assert [a["id"] for a in listed] == [w.a_output]

    # 検索(タイトル・タグ名も対象)に他人の Asset は出ない。
    found = w.as_("B").get("/api/search", params={"q": "alice", "types": "asset"}).json()
    assert w.a_output not in [hit["id"] for hit in found["assets"]]
    found = w.as_("A").get("/api/search", params={"q": "alice title", "types": "asset"}).json()
    assert [hit["id"] for hit in found["assets"]] == [w.a_output]

    # MCP: search_assets の tag と、get_asset のタイトル・タグ。
    items = _ok(_call(w.client, "search_assets", {"tag": "alicetag"}, w.bearer("B")))["items"]
    assert [i["asset_id"] for i in items] == [w.b_upload]
    items = _ok(_call(w.client, "search_assets", {"tag": "alicetag"}, w.bearer("A")))["items"]
    assert [i["asset_id"] for i in items] == [w.a_output]
    assert items[0]["title"] == "alice title"
    got = _ok(_call(w.client, "get_asset", {"asset_id": w.a_output}, w.bearer("A")))
    assert got["title"] == "alice title"
    assert [t["name"] for t in got["tags"]] == ["alicetag"]
    assert "not found" in _error_text(
        _call(w.client, "get_asset", {"asset_id": w.a_output}, w.bearer("B"))
    )
