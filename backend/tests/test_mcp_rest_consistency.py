"""MCP のツールと REST の API の食い違いを見つける(Issue #82 の候補 5)。

ADR-0023 1章は「ツールは REST を HTTP で呼ばず、REST と同じドメイン関数を直接呼ぶ」と決めて
いる。そのため MCP 側に検証や可視性の判定の書き漏れがあると、REST では断られる操作が MCP では
通る(またはその逆)ことが起こりうる。ここでは同じデータを REST と MCP の両方から見て、結果が
一致することを確かめる。正は REST とする。

- 認証モード(oidc)で、利用者 A・B と管理者 C がそれぞれ自分のアクセストークンを持つ。
  所有者が NULL(個人モードの頃)のデータも入れ、ADR-0025 の可視性の違いが出るようにする。
- MCP のツールを足したら、末尾の `TOOL_CHECKS` か `NO_REST_COUNTERPART` のどちらかに
  載せないと `test_every_mcp_tool_is_mapped` が落ちる(突き合わせを考えさせるため)。
"""

from __future__ import annotations

import base64
import io
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.domain.models import ApiToken, AppUser, AssetEmbedding, Run, RunInput
from tests.conftest import login_as, make_png_bytes, wait_for_run_terminal
from tests.test_mcp import _call, _enable, _rpc
from tests.test_visibility import _insert_legacy

_MODEL = "gpt-image-2.5-sunburst"
_VIEWERS = ("A", "B", "C")


# -- 準備 ----------------------------------------------------------------------


def _png(color: tuple[int, int, int], size: int = 64) -> bytes:
    return make_png_bytes(size, size, color)


@dataclass
class World:
    client: TestClient
    cookies: dict[str, str]
    tokens: dict[str, str]
    user_ids: dict[str, uuid.UUID]
    token_ids: dict[str, uuid.UUID]
    ids: dict[str, str] = field(default_factory=dict)

    def rest(self, who: str, method: str, path: str, **kwargs: Any) -> Any:
        """`who` の Cookie で REST を呼ぶ。"""
        self.client.cookies.clear()
        self.client.cookies.set("gakei_session", self.cookies[who])
        return self.client.request(method, path, **kwargs)

    def mcp(self, who: str, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """`who` のアクセストークンで MCP のツールを呼ぶ(Cookie は送らない)。生の結果を返す。"""
        self.client.cookies.clear()
        return _call(
            self.client, name, arguments or {}, {"Authorization": f"Bearer {self.tokens[who]}"}
        )

    def mcp_ok(self, who: str, name: str, arguments: dict[str, Any] | None = None) -> Any:
        result = self.mcp(who, name, arguments)
        assert not result.get("isError"), (who, name, arguments, result)
        return result["structuredContent"]


def _is_error(result: dict[str, Any]) -> bool:
    return result.get("isError") is True


def _error_text(result: dict[str, Any]) -> str:
    assert _is_error(result), result
    return result["content"][0]["text"]


def _upload(w: World, who: str, data: bytes, kind: str = "upload") -> str:
    response = w.rest(
        who,
        "POST",
        "/api/assets",
        files={"file": ("x.png", io.BytesIO(data), "image/png")},
        data={"kind": kind},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _rest_generate(w: World, who: str, prompt: str) -> dict[str, Any]:
    response = w.rest(
        who,
        "POST",
        "/api/runs",
        json={"operation": "generate", "model": _MODEL, "prompt": prompt, "params": {"n": 1}},
    )
    assert response.status_code == 202, response.text
    w.client.cookies.set("gakei_session", w.cookies[who])
    detail = wait_for_run_terminal(w.client, response.json()["id"])
    assert detail["status"] == "succeeded", detail
    return detail


def _mcp_generate(w: World, who: str, prompt: str) -> dict[str, Any]:
    created = w.mcp_ok(who, "generate_image", {"prompt": prompt, "model": _MODEL})
    payload = w.mcp_ok(
        who,
        "get_run",
        {"run_id": created["run_id"], "wait_seconds": 10, "include_thumbnails": False},
    )
    assert payload["status"] == "succeeded", payload
    return payload


def _wait_embedded(w: World, *asset_ids: str) -> None:
    """有効なモデルで埋め込みが終わるまで待つ(文章での検索・似た画像の結果を固定するため)。"""
    model_key = w.rest("C", "GET", "/api/capabilities").json()["embeddings"]["model_key"]
    pending = {uuid.UUID(a) for a in asset_ids}
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        with w.client.app.state.session_factory() as db:
            done = db.execute(
                select(AssetEmbedding.asset_id).where(
                    AssetEmbedding.model_key == model_key,
                    AssetEmbedding.status == "succeeded",
                    AssetEmbedding.asset_id.in_(pending),
                )
            ).all()
        pending -= {row[0] for row in done}
        if not pending:
            return
        time.sleep(0.05)
    raise TimeoutError(f"埋め込みが終わりませんでした: {pending}")


@pytest.fixture
def world(client_oidc: TestClient) -> World:
    client = client_oidc
    emails = {"A": "alice@example.com", "B": "bob@example.com", "C": "admin@example.com"}
    cookies: dict[str, str] = {}
    tokens: dict[str, str] = {}

    login_as(client, emails["C"], "Admin C")
    _enable(client)
    response = client.patch("/api/settings/embeddings", json={"enabled": True})
    assert response.status_code == 200, response.text
    for who, email in emails.items():
        login_as(client, email, f"User {who}")
        cookies[who] = client.cookies.get("gakei_session")
        response = client.post("/api/users/me/api-tokens", json={"name": "agent"})
        assert response.status_code == 201, response.text
        tokens[who] = response.json()["token"]

    with client.app.state.session_factory() as db:
        user_ids = {
            who: db.execute(select(AppUser.id).where(AppUser.email == email)).scalar_one()
            for who, email in emails.items()
        }
        token_ids = {
            who: db.execute(select(ApiToken.id).where(ApiToken.user_id == uid)).scalar_one()
            for who, uid in user_ids.items()
        }
    w = World(client=client, cookies=cookies, tokens=tokens, user_ids=user_ids, token_ids=token_ids)
    ids = w.ids

    # A: 画面(REST)から生成、アップロード 2 枚(よく似た赤)とマスク、グループ、プロンプト
    # セット、タグ。
    detail = _rest_generate(w, "A", "alice zebra lighthouse")
    ids["a_run"] = detail["id"]
    ids["a_output"] = detail["outputs"][0]["asset_id"]
    ids["a_upload"] = _upload(w, "A", _png((220, 30, 30)))
    ids["a_upload2"] = _upload(w, "A", _png((222, 31, 30)))
    ids["a_mask"] = _upload(w, "A", _png((0, 0, 0)), kind="mask")
    group = w.rest("A", "POST", "/api/asset-groups", json={"name": "alice group"})
    assert group.status_code == 201, group.text
    ids["a_group"] = group.json()["id"]
    moved = w.rest(
        "A",
        "POST",
        f"/api/asset-groups/{ids['a_group']}/assets",
        json={"asset_ids": [ids["a_upload"]]},
    )
    assert moved.status_code == 200, moved.text
    prompt_set = w.rest(
        "A",
        "POST",
        "/api/prompt-sets",
        json={"name": "alice zebra set", "items": [{"label": "l", "text": "zebra text"}]},
    )
    assert prompt_set.status_code == 201, prompt_set.text
    tagged = w.rest("A", "POST", f"/api/assets/{ids['a_output']}/tags", json={"name": "alicetag"})
    assert tagged.status_code == 200, tagged.text

    # B: アップロード、グループ、MCP からの生成。
    ids["b_upload"] = _upload(w, "B", _png((20, 40, 230)))
    group = w.rest("B", "POST", "/api/asset-groups", json={"name": "bob group"})
    assert group.status_code == 201, group.text
    ids["b_group"] = group.json()["id"]
    payload = _mcp_generate(w, "B", "bob zebra mountain")
    ids["b_run"] = payload["run_id"]
    ids["b_output"] = payload["outputs"][0]["asset_id"]

    # 所有者 NULL(個人モードの頃)のデータ。管理者 C にだけ見える。
    legacy = _insert_legacy(client, ids["a_upload"])
    ids.update({f"legacy_{k}": v for k, v in legacy.items()})

    _wait_embedded(
        w, ids["a_output"], ids["a_upload"], ids["a_upload2"], ids["b_upload"], ids["b_output"]
    )
    return w


def _asset_universe(w: World) -> list[str]:
    keys = (
        "a_output",
        "a_upload",
        "a_upload2",
        "a_mask",
        "b_upload",
        "b_output",
        "legacy_upload",
        "legacy_output",
    )
    return [w.ids[k] for k in keys] + [str(uuid.uuid4())]


def _run_universe(w: World) -> list[str]:
    return [w.ids["a_run"], w.ids["b_run"], w.ids["legacy_run"], str(uuid.uuid4())]


def _run_count(w: World) -> int:
    with w.client.app.state.session_factory() as db:
        return db.execute(select(func.count()).select_from(Run)).scalar_one()


# -- 見える範囲(ADR-0025): 個別の取得 ---------------------------------------------


def test_get_asset_visibility_and_fields_match(world: World) -> None:
    """`GET /api/assets/{id}` と `get_asset` で、見える Asset と主な項目が一致する。"""
    w = world
    for who in _VIEWERS:
        for asset_id in _asset_universe(w):
            rest = w.rest(who, "GET", f"/api/assets/{asset_id}")
            mcp = w.mcp(
                who,
                "get_asset",
                {"asset_id": asset_id, "include_thumbnail": False, "include_lineage": False},
            )
            assert rest.status_code in (200, 404), rest.text
            assert (rest.status_code == 200) == (not _is_error(mcp)), (who, asset_id, mcp)
            if rest.status_code == 404:
                assert "not found" in _error_text(mcp)
                continue
            r, m = rest.json(), mcp["structuredContent"]
            for key in ("kind", "mime", "width", "height", "bytes", "title", "title_source"):
                assert r[key] == m[key], (who, asset_id, key)
            assert (r["deleted_at"] is not None) == m["deleted"]
            assert r["tags"] == m["tags"]
            assert (r["group"] or None) == (m["group"] or None)
            rest_run = (r["produced_by_run"] or {}).get("id")
            mcp_run = (m["produced_by_run"] or {}).get("run_id")
            assert rest_run == mcp_run, (who, asset_id)


def test_image_content_visibility_matches(world: World) -> None:
    """画像の本体(`GET /api/assets/{id}/content`)を取れる Asset と、`get_image`・
    `create_download_url` が応じる Asset が一致する。"""
    w = world
    for who in _VIEWERS:
        for asset_id in _asset_universe(w):
            rest = w.rest(who, "GET", f"/api/assets/{asset_id}/content?variant=thumb")
            assert rest.status_code in (200, 404), rest.text
            visible = rest.status_code == 200
            image = w.mcp(who, "get_image", {"asset_id": asset_id, "size": "small"})
            download = w.mcp(who, "create_download_url", {"asset_id": asset_id})
            assert visible == (not _is_error(image)), (who, asset_id, image)
            assert visible == (not _is_error(download)), (who, asset_id, download)
            if not visible:
                continue
            # 発行した URL で取れる原本は、REST の原本のダウンロードと同じ。
            url = download["structuredContent"]["download_url"]
            w.client.cookies.clear()
            fetched = w.client.get(url.removeprefix("http://testserver"))
            original = w.rest(
                who, "GET", f"/api/assets/{asset_id}/content?variant=original&download=1"
            )
            assert fetched.status_code == 200 and original.status_code == 200
            assert fetched.content == original.content
            assert fetched.headers["content-type"] == original.headers["content-type"]


def _comparable_run(rest: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": rest["status"],
        "provider": rest["provider"],
        "operation": rest["operation"],
        "model": rest["model"],
        "prompt": rest["prompt"],
        "params": rest["params"],
        "origin": rest["origin"],
        "error_code": rest["error_code"],
        "outputs": [o["asset_id"] for o in rest["outputs"]],
        "inputs": [(i["asset_id"], i["role"], i["position"]) for i in rest["inputs"]],
        "group": (rest["asset_group"] or {}).get("id"),
    }


def _comparable_mcp_run(mcp: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": mcp["status"],
        "provider": mcp["provider"],
        "operation": mcp["operation"],
        "model": mcp["model"],
        "prompt": mcp["prompt"],
        "params": mcp["params"],
        "origin": mcp["origin"],
        "error_code": mcp["error_code"],
        "outputs": [o["asset_id"] for o in mcp["outputs"]],
        "inputs": [(i["asset_id"], i["role"], i["position"]) for i in mcp["inputs"]],
        "group": (mcp["asset_group"] or {}).get("id"),
    }


def test_get_run_visibility_and_fields_match(world: World) -> None:
    """`GET /api/runs/{id}` と `get_run` で、見える Run と中身が一致する。"""
    w = world
    for who in _VIEWERS:
        for run_id in _run_universe(w):
            rest = w.rest(who, "GET", f"/api/runs/{run_id}")
            mcp = w.mcp(
                who,
                "get_run",
                {"run_id": run_id, "include_thumbnails": False, "include_lineage": False},
            )
            assert rest.status_code in (200, 404), rest.text
            assert (rest.status_code == 200) == (not _is_error(mcp)), (who, run_id, mcp)
            if rest.status_code == 404:
                assert "not found" in _error_text(mcp)
                continue
            assert _comparable_run(rest.json()) == _comparable_mcp_run(mcp["structuredContent"])


def test_cancel_run_refusals_match(world: World) -> None:
    """`POST /api/runs/{id}/cancel` と `cancel_run`: 他人の Run は両方とも「無い」、終わった
    Run は両方とも取り消せない。"""
    w = world
    for who in _VIEWERS:
        for run_id in _run_universe(w):
            rest = w.rest(who, "POST", f"/api/runs/{run_id}/cancel")
            mcp = w.mcp(who, "cancel_run", {"run_id": run_id})
            # FAKE の Run はすべて終わっているので、どちらも断る。
            assert rest.status_code in (404, 409), rest.text
            text = _error_text(mcp)
            if rest.status_code == 404:
                assert "not found" in text, (who, run_id, text)
            else:
                assert "cannot be canceled" in text, (who, run_id, text)


def test_find_similar_assets_matches_rest(world: World) -> None:
    """`GET /api/assets/{id}/similar` と `find_similar_assets` の結果(順序と類似度)が一致する。
    見えない Asset は両方とも「無い」、埋め込みが無い Asset は両方とも断る。"""
    w = world
    for who in _VIEWERS:
        for asset_id in _asset_universe(w):
            rest = w.rest(who, "GET", f"/api/assets/{asset_id}/similar?limit=50")
            mcp = w.mcp(who, "find_similar_assets", {"asset_id": asset_id, "limit": 50})
            assert rest.status_code in (200, 404, 409), rest.text
            if rest.status_code == 200:
                payload = mcp["structuredContent"]
                assert not _is_error(mcp), mcp
                rest_hits = [(h["id"], round(h["score"], 4)) for h in rest.json()["assets"]]
                mcp_hits = [(h["asset_id"], h["score"]) for h in payload["items"]]
                assert rest_hits == mcp_hits, (who, asset_id)
                assert rest.json()["model_key"] == payload["model_key"]
            elif rest.status_code == 404:
                assert "not found" in _error_text(mcp), (who, asset_id)
            else:
                assert _is_error(mcp), (who, asset_id, mcp)
                assert "not found" not in _error_text(mcp)


# -- 見える範囲(ADR-0025): 一覧と検索 ---------------------------------------------


def _rest_asset_ids(w: World, who: str, query: str = "") -> list[str] | int:
    response = w.rest(who, "GET", f"/api/assets?limit=200{query}")
    if response.status_code != 200:
        return response.status_code
    return [a["id"] for a in response.json()["items"]]


def _mcp_asset_ids(w: World, who: str, **args: Any) -> list[str] | str:
    result = w.mcp(who, "search_assets", {"limit": 50, **args})
    if _is_error(result):
        return _error_text(result)
    return [a["asset_id"] for a in result["structuredContent"]["items"]]


# 断るべきタグの名前(長すぎる)と、そのまま受けるもの。REST と MCP で扱いが揃うこと。
_TAG_CASES = ("alicetag", "bobtag", "a" * 300, "!!", "a,b")


def _assert_same_ids(rest: list[str] | int, mcp: list[str] | str, context: Any) -> None:
    """REST が 404 なら MCP は not found、4xx なら MCP もエラー、200 なら同じ並び。"""
    if rest == 404:
        assert isinstance(mcp, str) and "not found" in mcp, (context, mcp)
    elif isinstance(rest, int):
        assert 400 <= rest < 500, (context, rest)
        assert isinstance(mcp, str), (context, rest, mcp)
    else:
        assert rest == mcp, context


def test_stock_list_matches_search_assets(world: World) -> None:
    """ストックの一覧(`GET /api/assets`)と、query なしの `search_assets` が同じ並びになる
    (種類・グループ・タグで絞ったときも)。"""
    w = world
    for who in _VIEWERS:
        everything = _rest_asset_ids(w, who)
        assert everything, who  # 比べる中身があること(どの利用者にも何かは見える)
        _assert_same_ids(everything, _mcp_asset_ids(w, who), who)
        for kind in ("upload", "generated", "mask", "sketch"):
            rest = _rest_asset_ids(w, who, f"&kind={kind}")
            _assert_same_ids(rest, _mcp_asset_ids(w, who, kind=kind), (who, kind))
        for tag in _TAG_CASES:
            rest = _rest_asset_ids(w, who, f"&tag={tag}")
            _assert_same_ids(rest, _mcp_asset_ids(w, who, tag=tag), (who, tag))
        for group_key in ("a_group", "b_group", "legacy_group"):
            group_id = w.ids[group_key]
            rest = _rest_asset_ids(w, who, f"&group_id={group_id}")
            mcp = _mcp_asset_ids(w, who, group_id=group_id)
            _assert_same_ids(rest, mcp, (who, group_key))
    assert w.ids["a_output"] in _rest_asset_ids(w, "A", "&tag=alicetag")


def test_keyword_search_matches_rest_search(world: World) -> None:
    """キーワード検索(`GET /api/search?types=asset`)と `search_assets(query)` が一致する。"""
    w = world
    queries = ("zebra", "alice", "bob", "legacy", "alicetag")
    for who in _VIEWERS:
        for q in queries:
            for tag in (None, *_TAG_CASES):
                params = f"q={q}&types=asset&limit=50" + (f"&tag={tag}" if tag else "")
                rest = w.rest(who, "GET", f"/api/search?{params}")
                rest_ids = (
                    [h["id"] for h in rest.json()["assets"]]
                    if rest.status_code == 200
                    else rest.status_code
                )
                args: dict[str, Any] = {"query": q, **({"tag": tag} if tag else {})}
                _assert_same_ids(rest_ids, _mcp_asset_ids(w, who, **args), (who, q, tag))
    found = w.rest("A", "GET", "/api/search?q=zebra&types=asset").json()["assets"]
    assert w.ids["a_output"] in [h["id"] for h in found]


def test_semantic_search_matches_rest(world: World) -> None:
    """文章での検索(`GET /api/search/semantic`)と `search_assets(mode='semantic')` が、
    順序・類似度・絞り込み(種類、グループ、タグ)と断る条件まで一致する。"""
    w = world
    compared = 0
    for who in _VIEWERS:
        cases: list[tuple[str, dict[str, Any]]] = [
            ("", {}),
            ("&kind=upload", {"kind": "upload"}),
            ("&kind=generated", {"kind": "generated"}),
            ("&kind=mask", {"kind": "mask"}),
            (f"&group_id={w.ids['a_group']}", {"group_id": w.ids["a_group"]}),
            (f"&group_id={w.ids['b_group']}", {"group_id": w.ids["b_group"]}),
            (f"&group_id={uuid.uuid4()}", {"group_id": str(uuid.uuid4())}),
            *((f"&tag={tag}", {"tag": tag}) for tag in _TAG_CASES),
        ]
        for query, args in cases:
            rest = w.rest(who, "GET", f"/api/search/semantic?q=red+square&limit=50{query}")
            mcp = w.mcp(
                who,
                "search_assets",
                {"query": "red square", "mode": "semantic", "limit": 50, **args},
            )
            if rest.status_code != 200:
                _assert_same_ids(
                    rest.status_code, _error_text(mcp) if _is_error(mcp) else [], (who, query)
                )
                continue
            assert not _is_error(mcp), (who, query, mcp)
            rest_hits = [(h["id"], round(h["score"], 4)) for h in rest.json()["assets"]]
            mcp_hits = [(h["asset_id"], h["score"]) for h in mcp["structuredContent"]["items"]]
            assert rest_hits == mcp_hits, (who, query)
            compared += len(rest_hits)
    assert compared > 0


def test_list_runs_matches_rest(world: World) -> None:
    """`GET /api/runs` と `list_runs` で、見える Run と並びが一致する。"""
    w = world
    for who in _VIEWERS:
        rest = w.rest(who, "GET", "/api/runs?limit=200")
        assert rest.status_code == 200, rest.text
        rest_ids = [r["id"] for r in rest.json()["items"]]
        mcp = w.mcp_ok(who, "list_runs", {"limit": 50})
        assert rest_ids == [r["run_id"] for r in mcp["items"]], who
        # origin の絞り込みは REST の origin 列と同じ意味(NULL = 画面、mcp)。
        by_origin = {r["id"]: r["origin"] for r in rest.json()["items"]}
        for origin, expected in (("mcp", "mcp"), ("web", None)):
            listed = w.mcp_ok(who, "list_runs", {"limit": 50, "origin": origin})["items"]
            assert [r["run_id"] for r in listed] == [
                i for i in rest_ids if by_origin[i] == expected
            ], (who, origin)


def test_list_groups_matches_rest(world: World) -> None:
    w = world
    for who in _VIEWERS:
        rest = w.rest(who, "GET", "/api/asset-groups")
        assert rest.status_code == 200, rest.text
        assert rest.json()["items"] == w.mcp_ok(who, "list_groups")["items"], who


def test_list_prompt_sets_matches_rest(world: World) -> None:
    w = world
    for who in _VIEWERS:
        rest = w.rest(who, "GET", "/api/prompt-sets")
        assert rest.status_code == 200, rest.text
        assert rest.json() == w.mcp_ok(who, "list_prompt_sets"), who


# -- 論理削除 ------------------------------------------------------------------


def test_deleted_asset_disappears_from_both(world: World) -> None:
    """REST で削除した Asset は、REST の一覧・検索からも MCP の検索からも消える。詳細は
    どちらも削除済みとして返す(REST は論理削除済みでも 200。ADR-0008)。"""
    w = world
    target = w.ids["a_upload2"]
    assert w.rest("A", "DELETE", f"/api/assets/{target}").status_code == 204

    assert target not in _rest_asset_ids(w, "A")
    assert target not in _mcp_asset_ids(w, "A")
    assert _rest_asset_ids(w, "A") == _mcp_asset_ids(w, "A")

    rest = w.rest("A", "GET", "/api/search/semantic?q=red+square&limit=50")
    rest_ids = [h["id"] for h in rest.json()["assets"]]
    mcp_ids = _mcp_asset_ids(w, "A", query="red square", mode="semantic")
    assert target not in rest_ids
    assert rest_ids == mcp_ids
    # 似た画像の結果からも消える(起点にした場合と、結果に出る場合の両方)。
    similar = w.rest("A", "GET", f"/api/assets/{w.ids['a_upload']}/similar?limit=50")
    assert target not in [h["id"] for h in similar.json()["assets"]]
    mcp_similar = w.mcp_ok("A", "find_similar_assets", {"asset_id": w.ids["a_upload"]})
    assert target not in [h["asset_id"] for h in mcp_similar["items"]]

    detail = w.rest("A", "GET", f"/api/assets/{target}")
    assert detail.status_code == 200 and detail.json()["deleted_at"] is not None
    got = w.mcp_ok("A", "get_asset", {"asset_id": target, "include_thumbnail": False})
    assert got["deleted"] is True

    # 削除済みの Asset は、どちらからも Edit の入力にできない。
    before = _run_count(w)
    rest_run = w.rest(
        "A",
        "POST",
        "/api/runs",
        json={
            "operation": "edit",
            "model": _MODEL,
            "prompt": "x",
            "inputs": [{"asset_id": target, "role": "image", "position": 0}],
        },
    )
    assert rest_run.status_code == 422
    mcp_run = w.mcp(
        "A", "generate_image", {"prompt": "x", "operation": "edit", "input_asset_ids": [target]}
    )
    assert _is_error(mcp_run)
    assert _run_count(w) == before


def test_deleted_run_disappears_from_lists(world: World) -> None:
    """REST で削除した Run は、`GET /api/runs` からも `list_runs` からも消える。"""
    w = world
    run_id = w.ids["a_run"]
    assert w.rest("A", "DELETE", f"/api/runs/{run_id}").status_code == 204
    rest_ids = [r["id"] for r in w.rest("A", "GET", "/api/runs?limit=200").json()["items"]]
    mcp_ids = [r["run_id"] for r in w.mcp_ok("A", "list_runs", {"limit": 50})["items"]]
    assert run_id not in rest_ids
    assert rest_ids == mcp_ids
    # 出力の Asset も一緒に論理削除され、両方の一覧から消える。
    assert w.ids["a_output"] not in _rest_asset_ids(w, "A")
    assert _rest_asset_ids(w, "A") == _mcp_asset_ids(w, "A")


def test_deleted_run_detail_matches(world: World) -> None:
    """削除済みの Run の詳細。REST は論理削除済みでも 200 で返す(ADR-0008)が、MCP の
    `get_run` は「無い」とする。どちらに揃えるかは設計の判断が要る。"""
    w = world
    run_id = w.ids["a_run"]
    assert w.rest("A", "DELETE", f"/api/runs/{run_id}").status_code == 204
    rest = w.rest("A", "GET", f"/api/runs/{run_id}")
    assert rest.status_code == 200 and rest.json()["deleted_at"] is not None
    mcp = w.mcp("A", "get_run", {"run_id": run_id, "include_thumbnails": False})
    if _is_error(mcp):
        pytest.xfail(
            "削除済みの Run: REST の GET /api/runs/{id} は 200(deleted_at 付き)、MCP の "
            "get_run は not found。get_asset は削除済みでも返す(deleted: true)ので、Run だけ"
            "扱いが違う。どちらに揃えるかは設計の判断が要る"
        )


# -- 生成の検証(ADR-0023 1章: 検証は同じドメイン関数) -------------------------------


def _invalid_cases(w: World) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    """(名前, REST の本文, MCP の引数)。どれも REST では 4xx で断られる。"""
    ids = w.ids
    big = _upload(w, "A", _png((5, 5, 5), size=96))
    seventeen = [ids["a_upload"]] * 17

    def _edit(images: list[str], mask: str | None = None, **extra: Any) -> dict[str, Any]:
        inputs = [{"asset_id": a, "role": "image", "position": i} for i, a in enumerate(images)]
        if mask is not None:
            inputs.append({"asset_id": mask, "role": "mask", "position": 0})
        return {"operation": "edit", "model": _MODEL, "prompt": "x", "inputs": inputs, **extra}

    def _mcp_edit(images: list[str], mask: str | None = None, **extra: Any) -> dict[str, Any]:
        args: dict[str, Any] = {
            "operation": "edit",
            "model": _MODEL,
            "prompt": "x",
            "input_asset_ids": images,
            **extra,
        }
        if mask is not None:
            args["mask_asset_id"] = mask
        return args

    def _gen(**extra: Any) -> dict[str, Any]:
        return {"operation": "generate", "model": _MODEL, "prompt": "x", **extra}

    return [
        ("unknown_model", _gen(model="no-such-model"), _gen(model="no-such-model")),
        ("unknown_provider", _gen(provider="nope"), _gen(provider="nope")),
        ("bad_size_format", _gen(params={"size": "big"}), _gen(params={"size": "big"})),
        ("bad_size_value", _gen(params={"size": "13x13"}), _gen(params={"size": "13x13"})),
        (
            "bad_quality",
            _gen(params={"quality": "ultra"}),
            _gen(params={"quality": "ultra"}),
        ),
        ("n_zero", _gen(params={"n": 0}), _gen(params={"n": 0})),
        ("n_too_many", _gen(params={"n": 11}), _gen(params={"n": 11})),
        (
            "transparent_jpeg",
            _gen(params={"background": "transparent", "output_format": "jpeg"}),
            _gen(params={"background": "transparent", "output_format": "jpeg"}),
        ),
        (
            "unknown_param",
            _gen(params={"no_such_param": 1}),
            _gen(params={"no_such_param": 1}),
        ),
        (
            "generate_with_inputs",
            {**_edit([ids["a_upload"]]), "operation": "generate"},
            {**_mcp_edit([ids["a_upload"]]), "operation": "generate"},
        ),
        ("edit_without_inputs", _edit([]), _mcp_edit([])),
        ("mask_without_image", _edit([], mask=ids["a_mask"]), _mcp_edit([], mask=ids["a_mask"])),
        (
            "mask_size_mismatch",
            _edit([big], mask=ids["a_mask"]),
            _mcp_edit([big], mask=ids["a_mask"]),
        ),
        ("too_many_inputs", _edit(seventeen), _mcp_edit(seventeen)),
        ("missing_input", _edit([str(uuid.uuid4())]), _mcp_edit([str(uuid.uuid4())])),
        ("others_input", _edit([ids["b_upload"]]), _mcp_edit([ids["b_upload"]])),
        (
            "legacy_input",
            _edit([ids["legacy_upload"]]),
            _mcp_edit([ids["legacy_upload"]]),
        ),
        (
            "others_mask",
            _edit([ids["a_upload"]], mask=ids["b_upload"]),
            _mcp_edit([ids["a_upload"]], mask=ids["b_upload"]),
        ),
        (
            "others_group",
            _gen(asset_group_id=ids["b_group"]),
            _gen(group_id=ids["b_group"]),
        ),
        (
            "missing_group",
            _gen(asset_group_id=str(uuid.uuid4())),
            _gen(group_id=str(uuid.uuid4())),
        ),
    ]


def test_invalid_generate_requests_are_rejected_by_both(world: World) -> None:
    """REST で断られる生成の依頼は、MCP の `generate_image` でも断られ、どちらも Run を作らない。"""
    w = world
    cases = _invalid_cases(w)
    before = _run_count(w)
    mismatches: list[str] = []
    for name, rest_body, mcp_args in cases:
        rest = w.rest("A", "POST", "/api/runs", json={"params": {}, **rest_body})
        assert 400 <= rest.status_code < 500, (name, rest.status_code, rest.text)
        mcp = w.mcp("A", "generate_image", mcp_args)
        if not _is_error(mcp):
            mismatches.append(name)
    assert mismatches == [], f"REST は断るが MCP は受け付けた: {mismatches}"
    assert _run_count(w) == before


def test_valid_generate_creates_equivalent_runs(world: World) -> None:
    """同じ依頼を REST と MCP で出すと、来歴の列が同じ Run ができる(実行元だけが違う)。"""
    w = world
    ids = w.ids
    cases = [
        (
            {
                "operation": "generate",
                "model": _MODEL,
                "prompt": "same prompt",
                "params": {"size": "1024x1024", "quality": "low", "n": 2},
                "asset_group_id": ids["a_group"],
            },
            {
                "operation": "generate",
                "model": _MODEL,
                "prompt": "same prompt",
                "params": {"size": "1024x1024", "quality": "low", "n": 2},
                "group_id": ids["a_group"],
            },
        ),
        (
            {
                "operation": "edit",
                "model": _MODEL,
                "prompt": "edit both",
                "params": {"quality": "low"},
                "inputs": [
                    {"asset_id": ids["a_upload"], "role": "image", "position": 0},
                    {"asset_id": ids["a_output"], "role": "image", "position": 1},
                ],
            },
            {
                "operation": "edit",
                "model": _MODEL,
                "prompt": "edit both",
                "params": {"quality": "low"},
                "input_asset_ids": [ids["a_upload"], ids["a_output"]],
            },
        ),
    ]
    for rest_body, mcp_args in cases:
        rest = w.rest("A", "POST", "/api/runs", json=rest_body)
        assert rest.status_code == 202, rest.text
        mcp = w.mcp_ok("A", "generate_image", mcp_args)
        rest_id, mcp_id = uuid.UUID(rest.json()["id"]), uuid.UUID(mcp["run_id"])
        with w.client.app.state.session_factory() as db:
            runs = {
                r.id: r
                for r in db.execute(select(Run).where(Run.id.in_([rest_id, mcp_id]))).scalars()
            }
            inputs = {
                run_id: [
                    (i.asset_id, str(i.role), i.position)
                    for i in db.execute(
                        select(RunInput)
                        .where(RunInput.run_id == run_id)
                        .order_by(RunInput.role, RunInput.position)
                    ).scalars()
                ]
                for run_id in (rest_id, mcp_id)
            }
        r, m = runs[rest_id], runs[mcp_id]
        for column in (
            "provider",
            "model",
            "operation",
            "prompt",
            "params",
            "created_by_user_id",
            "asset_group_id",
            "deployment",
        ):
            assert getattr(r, column) == getattr(m, column), column
        assert r.created_by_user_id == w.user_ids["A"]
        assert inputs[rest_id] == inputs[mcp_id]
        assert (r.origin, r.api_token_id) == (None, None)
        assert (m.origin, m.api_token_id) == ("mcp", w.token_ids["A"])


# -- グループの作成と移動 -----------------------------------------------------------


def test_create_group_matches_rest(world: World) -> None:
    """名前の正規化と、断る名前が `POST /api/asset-groups` と `create_group` で同じ。"""
    w = world
    for name in ("  spaced name  ", "a" * 100, "", "   ", "a" * 500, "tab\tname"):
        rest = w.rest("A", "POST", "/api/asset-groups", json={"name": name})
        mcp = w.mcp("A", "create_group", {"name": name})
        assert rest.status_code in (201, 422), rest.text
        assert (rest.status_code == 201) == (not _is_error(mcp)), (name, rest.text, mcp)
        if rest.status_code == 201:
            assert rest.json()["name"] == mcp["structuredContent"]["name"]
            assert rest.json()["member_count"] == mcp["structuredContent"]["member_count"] == 0


def test_move_to_group_matches_rest(world: World) -> None:
    """`POST /api/asset-groups/{id}/assets` と `move_to_group` で、移せる Asset と
    グループが同じ。"""
    w = world
    ids = w.ids
    for who in _VIEWERS:
        for group_key in ("a_group", "b_group", "legacy_group"):
            for asset_key in ("a_upload", "b_upload", "legacy_upload"):
                group_id, asset_id = ids[group_key], ids[asset_key]
                rest = w.rest(
                    who,
                    "POST",
                    f"/api/asset-groups/{group_id}/assets",
                    json={"asset_ids": [asset_id]},
                )
                mcp = w.mcp(who, "move_to_group", {"group_id": group_id, "asset_ids": [asset_id]})
                assert rest.status_code in (200, 404), rest.text
                assert (rest.status_code == 200) == (not _is_error(mcp)), (
                    who,
                    group_key,
                    asset_key,
                    mcp,
                )
                if rest.status_code == 200:
                    assert rest.json()["id"] == mcp["structuredContent"]["id"]
                    assert rest.json()["member_count"] == mcp["structuredContent"]["member_count"]
                else:
                    assert "not found" in _error_text(mcp).lower()


# -- 取り込み ------------------------------------------------------------------


def _rest_upload(w: World, who: str, data: bytes) -> Any:
    return w.rest(
        who,
        "POST",
        "/api/assets",
        files={"file": ("x.png", io.BytesIO(data), "image/png")},
        data={"kind": "upload"},
    )


def test_upload_image_matches_rest(world: World) -> None:
    """`POST /api/assets`(kind=upload)と `upload_image` の取り込みが同じ規則で動く。

    GAKEI からダウンロードした PNG(系列情報入り。ADR-0014)を本人が送り直すと、どちらも既存の
    Asset を返す。他人が送ると、どちらも新しい Asset として取り込む(ADR-0025 4章)。画像で
    ないものは両方とも断る。"""
    w = world
    source = w.ids["a_upload"]
    downloaded = w.rest("A", "GET", f"/api/assets/{source}/content?variant=original&download=1")
    assert downloaded.status_code == 200
    data = downloaded.content
    b64 = base64.b64encode(data).decode("ascii")

    for who in _VIEWERS:
        rest = _rest_upload(w, who, data)
        assert rest.status_code == 201, rest.text
        mcp = w.mcp_ok(who, "upload_image", {"data_base64": b64})
        assert rest.json()["ingest_outcome"] == mcp["ingest_outcome"], who
        if who == "A":
            assert rest.json()["ingest_outcome"] == "matched_existing"
            assert rest.json()["id"] == mcp["asset_id"] == source
        else:
            assert rest.json()["ingest_outcome"] == "created"
            assert source not in (rest.json()["id"], mcp["asset_id"])
        # 取り込んだ Asset の中身は REST の詳細と同じ。
        detail = w.rest(who, "GET", f"/api/assets/{mcp['asset_id']}").json()
        for key in ("kind", "mime", "width", "height", "bytes"):
            assert detail[key] == mcp[key], (who, key)

    # 画像でないものは両方とも断る。
    junk = b"not an image at all"
    assert _rest_upload(w, "A", junk).status_code == 422
    junk_b64 = base64.b64encode(junk).decode("ascii")
    assert _is_error(w.mcp("A", "upload_image", {"data_base64": junk_b64}))


# -- 料金の見積もり -------------------------------------------------------------------


def test_estimate_cost_matches_rest(world: World) -> None:
    """`GET /api/pricing/estimate` と `estimate_cost` の数字が一致する(見えない入力画像を
    数えないことも含めて)。"""
    w = world
    ids = w.ids
    cases: list[dict[str, Any]] = [
        {"quality": "low", "size": "1024x1024", "n": 1},
        {"quality": "high", "size": "1536x1024", "n": 3, "prompt": "a" * 120},
        {"quality": "medium", "size": "1024x1536", "n": 2, "inputs": [ids["a_upload"]]},
        {"quality": "low", "size": "1024x1024", "n": 1, "inputs": [ids["b_upload"]]},
        {
            "quality": "low",
            "size": "1024x1024",
            "n": 1,
            "inputs": [ids["a_upload"], ids["legacy_upload"], str(uuid.uuid4())],
        },
        {"quality": "auto", "size": "1024x1024", "n": 1},
        {"quality": "low", "size": "auto", "n": 1},
        {"quality": "low", "size": "13x13", "n": 1},
        {"quality": "low", "size": "1024x1024", "n": 1, "model": "no-such-model"},
    ]
    keys = (
        "total_usd",
        "unavailable_reason",
        "output_tokens_per_image",
        "input_image_tokens",
        "text_tokens",
        "unit_prices_per_1m",
        "n",
        "pricing_source",
        "pricing_checked_at",
    )
    for who in _VIEWERS:
        for case in cases:
            model = case.get("model", _MODEL)
            prompt = case.get("prompt", "")
            inputs = case.get("inputs", [])
            query = (
                f"model={model}&operation={'edit' if inputs else 'generate'}"
                f"&quality={case['quality']}&size={case['size']}&n={case['n']}"
                f"&prompt_length={len(prompt)}"
                + (f"&input_asset_ids={','.join(inputs)}" if inputs else "")
            )
            rest = w.rest(who, "GET", f"/api/pricing/estimate?{query}")
            assert rest.status_code == 200, rest.text
            args: dict[str, Any] = {
                "model": model,
                "params": {"quality": case["quality"], "size": case["size"]},
                "n": case["n"],
            }
            if prompt:
                args["prompt"] = prompt
            if inputs:
                args["input_asset_ids"] = inputs
            mcp = w.mcp_ok(who, "estimate_cost", args)
            r = rest.json()
            if r["total_usd"] is not None:
                r["total_usd"] = round(r["total_usd"], 6)
            assert {k: r[k] for k in keys} == {k: mcp[k] for k in keys}, (who, case)


# -- 使える機能 -------------------------------------------------------------------


@pytest.mark.parametrize("embeddings", [False, True])
def test_get_capabilities_matches_rest(client: TestClient, embeddings: bool) -> None:
    """`GET /api/capabilities` と `get_capabilities` の、プロバイダー・モデル・パラメーター・
    サイズ・埋め込みの情報が一致する(MCP は使い方の注記を足すだけ)。"""
    _enable(client)
    if embeddings:
        response = client.patch("/api/settings/embeddings", json={"enabled": True})
        assert response.status_code == 200, response.text
    rest = client.get("/api/capabilities")
    assert rest.status_code == 200, rest.text
    mcp = _call(client, "get_capabilities")
    assert not mcp.get("isError"), mcp
    payload = mcp["structuredContent"]
    assert set(payload) - set(rest.json()) == {"usage_notes", "example_generate_image"}
    for key, value in rest.json().items():
        assert payload[key] == value, key
    # 使い方の例は、そのまま REST の検証を通る。
    example = payload["example_generate_image"]
    caps = rest.json()
    default = next(p for p in caps["providers"] if p["provider"] == caps["default_provider"])
    assert example["model"] == default["default_model"]


# -- ツールの対応表 ---------------------------------------------------------------

# MCP のツール → REST と突き合わせているこのファイルのテスト。
TOOL_CHECKS: dict[str, tuple[str, ...]] = {
    "get_capabilities": ("test_get_capabilities_matches_rest",),
    "generate_image": (
        "test_invalid_generate_requests_are_rejected_by_both",
        "test_valid_generate_creates_equivalent_runs",
        "test_deleted_asset_disappears_from_both",
    ),
    "get_run": ("test_get_run_visibility_and_fields_match", "test_deleted_run_detail_matches"),
    "list_runs": ("test_list_runs_matches_rest", "test_deleted_run_disappears_from_lists"),
    "estimate_cost": ("test_estimate_cost_matches_rest",),
    "cancel_run": ("test_cancel_run_refusals_match",),
    "search_assets": (
        "test_stock_list_matches_search_assets",
        "test_keyword_search_matches_rest_search",
        "test_semantic_search_matches_rest",
        "test_deleted_asset_disappears_from_both",
    ),
    "find_similar_assets": ("test_find_similar_assets_matches_rest",),
    "get_asset": ("test_get_asset_visibility_and_fields_match",),
    "get_image": ("test_image_content_visibility_matches",),
    "create_download_url": ("test_image_content_visibility_matches",),
    "upload_image": ("test_upload_image_matches_rest",),
    "list_prompt_sets": ("test_list_prompt_sets_matches_rest",),
    "list_groups": ("test_list_groups_matches_rest",),
    "create_group": ("test_create_group_matches_rest",),
    "move_to_group": ("test_move_to_group_matches_rest",),
}

# REST に対応するものが無いツールと、その理由。
NO_REST_COUNTERPART: dict[str, str] = {
    # アップロード用の1回限りの URL を発行するだけで、REST には発行の API が無い(画面は
    # `POST /api/assets` に直接送る)。URL で送った後の取り込みは `upload_image` と同じ
    # `ingest` で、tests/test_mcp.py と tests/test_visibility.py が確かめる。
    "create_upload_url": "MCP 専用(ADR-0023 7章 2)",
}


def test_every_mcp_tool_is_mapped(client: TestClient) -> None:
    """`tools/list` の全ツールが、突き合わせのテストか「対応なし」のどちらかに載っている。"""
    _enable(client)
    response = _rpc(client, "tools/list")
    assert response.status_code == 200, response.text
    tools = {t["name"] for t in response.json()["result"]["tools"]}
    assert not set(TOOL_CHECKS) & set(NO_REST_COUNTERPART)
    unmapped = tools - set(TOOL_CHECKS) - set(NO_REST_COUNTERPART)
    assert not unmapped, f"REST との突き合わせが決まっていないツール: {sorted(unmapped)}"
    stale = (set(TOOL_CHECKS) | set(NO_REST_COUNTERPART)) - tools
    assert not stale, f"もう無いツールが対応表に残っています: {sorted(stale)}"
    module = globals()
    for tool, tests in TOOL_CHECKS.items():
        assert tests, tool
        for name in tests:
            assert re.fullmatch(r"test_\w+", name) and callable(module.get(name)), (tool, name)
