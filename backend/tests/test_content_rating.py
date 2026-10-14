"""画像のレーティング(ADR-0049): 判定の規則、保存(人の選択と自動の判定)、待ち行列と worker、
管理者設定の一括判定、一覧・MCP・アシスタントの `content_rating`。

FAKE プロバイダーのアプリ(`client`)では推定エンジンもダミー(`FakeEngines`)になる。ダミーの
段階は `engines.rating_level` で固定する。実 API もモデルのダウンロードも使わない。
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.annotation.engines import AnnotationEngineError, FakeEngines
from app.domain import content_rating as cr
from app.domain.models import AssetContentRating
from tests.conftest import make_png_bytes, wait_for_run_terminal

_MODEL = "gpt-image-2.5-sunburst"


# -- 判定の規則(1章) ---------------------------------------------------------------


def _scores(g: float, s: float, q: float, e: float) -> dict[str, float]:
    return {"general": g, "sensitive": s, "questionable": q, "explicit": e}


def test_base_level_uses_cumulative_median() -> None:
    # ADR の例: sensitive 以上の累積は 0.55 → r(最大の確率なら general で g になる)。
    level, signals = cr.judge(_scores(0.45, 0.40, 0.10, 0.05), {})
    assert level == "r"
    assert signals == []
    assert cr.base_level(_scores(0.9, 0.05, 0.03, 0.02)) == "g"
    assert cr.base_level(_scores(0.1, 0.3, 0.5, 0.1)) == "x"
    assert cr.base_level(_scores(0.05, 0.1, 0.25, 0.6)) == "xx"
    # 累積がちょうど 0.5 なら強い側。
    assert cr.base_level(_scores(0.5, 0.0, 0.0, 0.5)) == "xx"
    # どこでも 0.5 に届かなければ g。
    assert cr.base_level(_scores(0.1, 0.1, 0.1, 0.1)) == "g"
    assert cr.base_level({}) == "g"


@pytest.mark.parametrize(
    ("rule_level", "tag"),
    [(rule.level, tag) for rule in cr.BOOST_RULES if rule.requires is None for tag in rule.tags],
)
def test_each_boost_tag_raises_from_general(rule_level: str, tag: str) -> None:
    rule = next(r for r in cr.BOOST_RULES if tag in r.tags)
    base = _scores(0.95, 0.03, 0.01, 0.01)
    level, signals = cr.judge(base, {tag: rule.threshold})
    assert level == rule_level
    assert signals == [{"tag": tag, "score": rule.threshold, "level": rule_level, "raised": True}]
    # しきい値未満なら上げない(根拠には残る)。
    below = round(rule.threshold - 0.01, 4)
    level, signals = cr.judge(base, {tag: below})
    assert level == "g"
    assert signals == [{"tag": tag, "score": below, "level": rule_level, "raised": False}]


def test_boost_thresholds_match_adr() -> None:
    thresholds = {rule.level: rule.threshold for rule in cr.BOOST_RULES}
    assert thresholds == {"r": 0.6, "x": 0.5, "xx": 0.5, "xxx": 0.5}
    assert "completely_nude" in cr.BOOST_TAGS and "cum_in_pussy" in cr.BOOST_TAGS
    assert cr.RULES_VERSION == 1


@pytest.mark.parametrize("tag", next(r for r in cr.BOOST_RULES if r.level == "xxx").tags)
def test_xxx_only_when_already_xx(tag: str) -> None:
    general = _scores(0.95, 0.03, 0.01, 0.01)
    # 性行為のタグだけでは xxx にしない。
    level, signals = cr.judge(general, {tag: 0.9})
    assert level == "g"
    assert signals == [{"tag": tag, "score": 0.9, "level": "xxx", "raised": False}]
    # 基準が x でも上げない。
    assert cr.judge(_scores(0.1, 0.3, 0.5, 0.1), {tag: 0.9})[0] == "x"
    # 基準が xx(explicit)なら xxx。
    assert cr.judge(_scores(0.05, 0.1, 0.25, 0.6), {tag: 0.5})[0] == "xxx"
    # 一般タグで xx に上がったときも xxx。
    level, signals = cr.judge(general, {tag: 0.9, "pussy": 0.7})
    assert level == "xxx"
    raised = {s["tag"]: s["raised"] for s in signals}
    assert raised == {tag: True, "pussy": True}


def test_boost_never_lowers_and_signals_filter() -> None:
    level, signals = cr.judge(
        _scores(0.05, 0.1, 0.25, 0.6),
        {"swimsuit": 0.9, "bikini": 0.19, "nude": 0.3, "unrelated": 0.99},
    )
    assert level == "xx"
    # 0.2 未満と、表に無いタグは根拠に入れない。確率の高い順。基準より上げていないので印は無し。
    assert signals == [
        {"tag": "swimsuit", "score": 0.9, "level": "r", "raised": False},
        {"tag": "nude", "score": 0.3, "level": "x", "raised": False},
    ]


# -- 設定と API の補助 ----------------------------------------------------------------


def _patch_settings(client: TestClient, **values: Any) -> dict:
    response = client.patch("/api/settings/annotation", json=values)
    assert response.status_code == 200, response.text
    return response.json()


def _engines(client: TestClient) -> FakeEngines:
    engines = client.app.state.annotator.engines
    assert isinstance(engines, FakeEngines)
    return engines


def _upload(client: TestClient, color=(10, 20, 30), kind: str = "upload") -> str:
    response = client.post(
        "/api/assets",
        files={"file": ("a.png", make_png_bytes(48, 32, color), "image/png")},
        data={"kind": kind},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _generate(client: TestClient, prompt: str, inputs: list | None = None) -> dict:
    body: dict[str, Any] = {
        "operation": "edit" if inputs else "generate",
        "model": _MODEL,
        "prompt": prompt,
        "params": {"n": 1} if inputs else {"n": 1, "size": "1024x1024"},
    }
    if inputs:
        body["inputs"] = inputs
    response = client.post("/api/runs", json=body)
    assert response.status_code == 202, response.text
    return wait_for_run_terminal(client, response.json()["id"])


def _detail(client: TestClient, asset_id: str) -> dict:
    response = client.get(f"/api/assets/{asset_id}")
    assert response.status_code == 200, response.text
    return response.json()


def _wait_rating(client: TestClient, asset_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = _detail(client, asset_id)
        detail = body.get("content_rating_detail")
        if detail and detail["status"] in ("succeeded", "failed"):
            return body
        time.sleep(0.05)
    raise TimeoutError(f"asset {asset_id} のレーティングの判定が終わりませんでした")


def _wait_annotation(client: TestClient, asset_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = _detail(client, asset_id)
        annotation = body.get("annotation")
        if annotation and annotation["status"] in ("succeeded", "failed"):
            return body
        time.sleep(0.05)
    raise TimeoutError(f"asset {asset_id} の推定が終わりませんでした")


def _row(client: TestClient, asset_id: str) -> AssetContentRating | None:
    with client.app.state.session_factory() as db:
        row = db.get(AssetContentRating, uuid.UUID(asset_id))
        if row is not None:
            db.expunge(row)
        return row


class CountingEngines(FakeEngines):
    def __init__(self, level: str) -> None:
        self.rating_level = level
        self.analyze_calls = 0

    def onnx_analyze(self, image, ctx):  # noqa: ANN001, ANN201
        self.analyze_calls += 1
        return super().onnx_analyze(image, ctx)


# -- 管理者設定 -----------------------------------------------------------------------


def test_settings_expose_content_rating(client: TestClient) -> None:
    body = client.get("/api/settings/annotation").json()
    assert body["content_rating_enabled"] is False
    # FAKE ではモデルのダウンロードは要らない。
    assert body["content_rating_model_ready"] is True
    assert body["content_rating_usable"] is False
    assert body["content_rating_pending_count"] == 0
    assert body["content_rating_queued_count"] == 0

    body = _patch_settings(client, content_rating_enabled=True)
    assert body["content_rating_enabled"] is True
    assert body["content_rating_usable"] is True
    # ONNX のタグ付けとは独立。
    assert body["onnx_enabled"] is False
    assert (
        client.patch("/api/settings/annotation", json={"content_rating_enabled": None}).status_code
        == 422
    )


def test_model_ready_requires_download(tmp_path) -> None:  # noqa: ANN001
    from app.config import Settings
    from app.domain.annotation_settings import AnnotationConfig

    settings = Settings(_env_file=None, data_dir=tmp_path, fake_provider=False)
    config = AnnotationConfig(content_rating_enabled=True)
    assert cr.model_ready(config, settings) is False
    assert cr.usable(config, settings) is False


# -- 取り込み時の判定と worker(3章) -------------------------------------------------------


def test_ingest_queues_rating_and_worker_judges(client: TestClient) -> None:
    _engines(client).rating_level = "x"
    _patch_settings(client, content_rating_enabled=True)
    asset_id = _upload(client)
    body = _wait_rating(client, asset_id)
    assert body["content_rating"] == "x"
    detail = body["content_rating_detail"]
    assert detail["status"] == "succeeded"
    assert detail["level"] == "x" and detail["auto_level"] == "x"
    assert detail["source"] == "auto"
    assert detail["rules_version"] == cr.RULES_VERSION
    assert detail["model"] == "fake:wd-vit-tagger-v3"
    assert detail["scores"] == {
        "general": 0.1,
        "sensitive": 0.3,
        "questionable": 0.5,
        "explicit": 0.1,
    }
    assert detail["signals"] == [{"tag": "nipples", "score": 0.3, "level": "x", "raised": False}]
    assert detail["judged_at"] is not None
    # タイトル・タグの推定(auto_on_ingest はオフ)は動かない。
    assert body["annotation"] is None


def test_ingest_skips_when_disabled_and_masks(client: TestClient) -> None:
    asset_id = _upload(client)
    assert _detail(client, asset_id)["content_rating_detail"] is None
    assert _detail(client, asset_id)["content_rating"] is None

    _patch_settings(client, content_rating_enabled=True)
    mask_id = _upload(client, kind="mask")
    time.sleep(0.3)
    assert _row(client, mask_id) is None


def test_generated_outputs_are_judged(client: TestClient) -> None:
    _engines(client).rating_level = "xxx"
    _patch_settings(client, content_rating_enabled=True)
    run = _generate(client, "a beach")
    asset_id = run["outputs"][0]["asset_id"]
    body = _wait_rating(client, asset_id)
    assert body["content_rating"] == "xxx"
    signals = {s["tag"]: s["raised"] for s in body["content_rating_detail"]["signals"]}
    assert signals == {"sex": True, "pussy": False}


def test_annotation_with_onnx_writes_rating_from_same_inference(client: TestClient) -> None:
    """推定で ONNX が走るときは、同じ推論からレーティングも書く(判定だけの待ち行列が無くても)。"""
    engines = CountingEngines("r")
    client.app.state.annotator.engines = engines
    _patch_settings(client, onnx_enabled=True)
    asset_id = _upload(client)
    # レーティングがオフの間は書かない。
    response = client.post(f"/api/assets/{asset_id}/annotate")
    assert response.status_code == 200, response.text
    _wait_annotation(client, asset_id)
    assert engines.analyze_calls == 1
    assert _row(client, asset_id) is None

    _patch_settings(client, content_rating_enabled=True)
    response = client.post(f"/api/assets/{asset_id}/annotate")
    assert response.status_code == 200, response.text
    deadline = time.monotonic() + 10
    while engines.analyze_calls < 2 and time.monotonic() < deadline:
        time.sleep(0.05)
    body = _wait_rating(client, asset_id)
    assert body["content_rating"] == "r"
    assert body["content_rating_detail"]["signals"] == [
        {"tag": "swimsuit", "score": 0.45, "level": "r", "raised": False}
    ]
    body = _wait_annotation(client, asset_id)
    assert body["annotation"]["status"] == "succeeded"
    # 推論は1回だけ(レーティングのために2回目を走らせない)。
    time.sleep(0.3)
    assert engines.analyze_calls == 2


def test_worker_failure_is_recorded_and_not_retried(client: TestClient) -> None:
    class Broken(FakeEngines):
        calls = 0

        def onnx_analyze(self, image, ctx):  # noqa: ANN001, ANN201
            Broken.calls += 1
            raise AnnotationEngineError("WD が壊れた")

    client.app.state.annotator.engines = Broken()
    _patch_settings(client, content_rating_enabled=True)
    asset_id = _upload(client)
    body = _wait_rating(client, asset_id)
    detail = body["content_rating_detail"]
    assert detail["status"] == "failed"
    assert "WD が壊れた" in detail["error"]
    assert body["content_rating"] is None
    time.sleep(0.3)
    assert Broken.calls == 1


def test_reset_running_on_startup(db_session_factory) -> None:  # noqa: ANN001
    from app.domain.models import Asset, AssetKind

    with db_session_factory() as db:
        asset = Asset(
            kind=AssetKind.UPLOAD,
            blob_key="assets/x",
            sha256="0" * 64,
            mime="image/png",
            width=1,
            height=1,
            bytes=1,
        )
        db.add(asset)
        db.flush()
        db.add(AssetContentRating(asset_id=asset.id, status=cr.STATUS_RUNNING))
        db.commit()
        asset_id = asset.id
    assert cr.reset_running(db_session_factory) == 1
    with db_session_factory() as db:
        assert db.get(AssetContentRating, asset_id).status == cr.STATUS_QUEUED
    assert cr.pick_queued(db_session_factory) == asset_id
    assert cr.pick_queued(db_session_factory) is None


# -- 人の選択(2章) ---------------------------------------------------------------------


def test_user_level_survives_rejudge_and_revert(client: TestClient) -> None:
    _engines(client).rating_level = "x"
    _patch_settings(client, content_rating_enabled=True)
    asset_id = _upload(client)
    _wait_rating(client, asset_id)

    response = client.put(f"/api/assets/{asset_id}/content-rating", json={"level": "xxx"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["asset_id"] == asset_id
    assert body["content_rating"] == "xxx"
    assert body["content_rating_detail"]["source"] == "user"
    assert body["content_rating_detail"]["auto_level"] == "x"

    # 再判定は auto_level だけを変え、人の段階は保つ。
    _engines(client).rating_level = "g"
    response = client.post(f"/api/assets/{asset_id}/content-rating/judge")
    assert response.status_code == 200, response.text
    assert response.json()["content_rating_detail"]["status"] in ("queued", "running", "succeeded")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        detail = _detail(client, asset_id)["content_rating_detail"]
        if detail["status"] == "succeeded" and detail["auto_level"] == "g":
            break
        time.sleep(0.05)
    body = _detail(client, asset_id)
    assert body["content_rating"] == "xxx"
    assert body["content_rating_detail"]["auto_level"] == "g"
    assert body["content_rating_detail"]["source"] == "user"

    # 自動に戻す。
    response = client.put(f"/api/assets/{asset_id}/content-rating", json={"level": None})
    assert response.status_code == 200, response.text
    assert response.json()["content_rating"] == "g"
    assert response.json()["content_rating_detail"]["source"] == "auto"


def test_user_level_without_judgement(client: TestClient) -> None:
    asset_id = _upload(client)
    response = client.put(f"/api/assets/{asset_id}/content-rating", json={"level": "r"})
    assert response.status_code == 200, response.text
    assert response.json()["content_rating"] == "r"
    assert _detail(client, asset_id)["content_rating"] == "r"
    # 判定がまだなので、自動に戻すと未判定。
    response = client.put(f"/api/assets/{asset_id}/content-rating", json={"level": None})
    assert response.json()["content_rating"] is None
    assert response.json()["content_rating_detail"]["source"] is None


def test_content_rating_endpoints_validate(client: TestClient) -> None:
    asset_id = _upload(client)
    missing = str(uuid.uuid4())
    assert (
        client.put(f"/api/assets/{missing}/content-rating", json={"level": "r"}).status_code == 404
    )
    assert (
        client.put(f"/api/assets/{asset_id}/content-rating", json={"level": "nc17"}).status_code
        == 422
    )
    assert client.put(f"/api/assets/{asset_id}/content-rating", json={}).status_code == 422
    mask_id = _upload(client, kind="mask")
    assert (
        client.put(f"/api/assets/{mask_id}/content-rating", json={"level": "r"}).status_code == 409
    )
    # 判定が無効なら 409。
    assert client.post(f"/api/assets/{asset_id}/content-rating/judge").status_code == 409
    _patch_settings(client, content_rating_enabled=True)
    assert client.post(f"/api/assets/{mask_id}/content-rating/judge").status_code == 409
    assert client.post(f"/api/assets/{missing}/content-rating/judge").status_code == 404


def test_write_auto_result_respects_user_source(client: TestClient) -> None:
    asset_id = _upload(client)
    uid = uuid.UUID(asset_id)
    with client.app.state.session_factory() as db:
        from app.domain.models import Asset

        asset = db.get(Asset, uid)
        cr.set_user_level(db, asset, "g")
        cr.write_auto_result(db, uid, model="m", scores=_scores(0.05, 0.1, 0.25, 0.6), tag_probs={})
        db.commit()
    row = _row(client, asset_id)
    assert row is not None
    assert (row.level, row.source, row.auto_level) == ("g", "user", "xx")


# -- 一括判定(3章) ---------------------------------------------------------------------


def test_backfill_selects_unrated_and_outdated(client: TestClient) -> None:
    _engines(client).rating_level = "r"
    first = _upload(client, (1, 2, 3))
    second = _upload(client, (4, 5, 6))
    _upload(client, (7, 8, 9), kind="mask")
    deleted = _upload(client, (9, 9, 9))
    assert client.delete(f"/api/assets/{deleted}").status_code in (200, 204)

    # 無効なら 409。
    assert client.post("/api/settings/annotation/content-rating/backfill").status_code == 409

    body = _patch_settings(client, content_rating_enabled=True)
    assert body["content_rating_pending_count"] == 2
    response = client.post("/api/settings/annotation/content-rating/backfill")
    assert response.status_code == 200, response.text
    assert response.json() == {"queued": 2}
    for asset_id in (first, second):
        assert _wait_rating(client, asset_id)["content_rating"] == "r"
    assert client.get("/api/settings/annotation").json()["content_rating_pending_count"] == 0

    # 古い版の規則で自動判定したものは対象に戻る。人が選んだ段階は保つ。
    with client.app.state.session_factory() as db:
        row = db.get(AssetContentRating, uuid.UUID(first))
        row.rules_version = cr.RULES_VERSION - 1
        row.source = "user"
        row.level = "g"
        db.commit()
    assert client.get("/api/settings/annotation").json()["content_rating_pending_count"] == 1
    _engines(client).rating_level = "xx"
    assert client.post("/api/settings/annotation/content-rating/backfill").json() == {"queued": 1}
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        row = _row(client, first)
        if row is not None and row.rules_version == cr.RULES_VERSION:
            break
        time.sleep(0.05)
    row = _row(client, first)
    assert row is not None
    assert (row.rules_version, row.auto_level, row.level) == (cr.RULES_VERSION, "xx", "g")
    # 判定済みの second は対象外のまま。
    assert _row(client, second).auto_level == "r"


def test_backfill_requires_admin(client_oidc: TestClient) -> None:
    from tests.conftest import login_as

    login_as(client_oidc, "user@example.com")
    response = client_oidc.post("/api/settings/annotation/content-rating/backfill")
    assert response.status_code == 403


# -- 一覧・検索・系列の content_rating(7章) -------------------------------------------------


def _set_level(client: TestClient, asset_id: str, level: str) -> None:
    response = client.put(f"/api/assets/{asset_id}/content-rating", json={"level": level})
    assert response.status_code == 200, response.text


def test_list_endpoints_include_content_rating(client: TestClient) -> None:
    parent = _upload(client, (200, 10, 10))
    run = _generate(
        client, "zebra lighthouse", [{"asset_id": parent, "role": "image", "position": 0}]
    )
    child = run["outputs"][0]["asset_id"]
    _set_level(client, parent, "x")
    _set_level(client, child, "xx")

    items = {i["id"]: i for i in client.get("/api/assets").json()["items"]}
    assert items[parent]["content_rating"] == "x"
    assert items[child]["content_rating"] == "xx"

    runs = client.get("/api/runs").json()["items"]
    target = next(r for r in runs if r["id"] == run["id"])
    assert target["outputs"][0]["content_rating"] == "xx"
    assert target["primary_parent_content_rating"] == "x"

    detail = client.get(f"/api/runs/{run['id']}").json()
    assert detail["outputs"][0]["content_rating"] == "xx"
    assert detail["inputs"][0]["content_rating"] == "x"
    assert detail["primary_parent_content_rating"] == "x"

    search = client.get("/api/search", params={"q": "zebra"}).json()
    asset_hits = {h["id"]: h for h in search["assets"]}
    assert asset_hits[child]["content_rating"] == "xx"
    run_hit = next(r for r in search["runs"] if r["id"] == run["id"])
    assert run_hit["outputs"][0]["content_rating"] == "xx"

    lineage = client.get(f"/api/assets/{child}/lineage").json()
    nodes = {n["id"]: n for n in lineage["nodes"] if n["type"] == "asset"}
    assert nodes[child]["asset"]["content_rating"] == "xx"
    assert nodes[parent]["asset"]["content_rating"] == "x"


# -- MCP とアシスタント(7章) -------------------------------------------------------------


def test_mcp_get_and_search_include_content_rating(client: TestClient) -> None:
    from tests.test_mcp import _call, _ok

    assert client.patch("/api/settings/mcp", json={"enabled": True}).status_code == 200
    asset_id = _upload(client)
    other = _upload(client, (90, 90, 90))
    _set_level(client, asset_id, "r")

    payload = _ok(_call(client, "get_asset", {"asset_id": asset_id, "include_thumbnail": False}))
    assert payload["content_rating"] == "r"
    payload = _ok(_call(client, "search_assets", {"kind": "upload"}))
    ratings = {item["asset_id"]: item["content_rating"] for item in payload["items"]}
    assert ratings == {asset_id: "r", other: None}


def _parse_sse(text: str) -> list[dict]:
    events: list[dict] = []
    for block in text.strip().split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line.removeprefix("data: ")))
    return events


def _assistant_tool(client: TestClient, name: str, args: dict) -> dict:
    conversation = client.post("/api/assistant/conversations", json={"title": ""})
    assert conversation.status_code == 201, conversation.text
    response = client.post(
        f"/api/assistant/conversations/{conversation.json()['id']}/messages",
        json={"text": f"/tool {name} {json.dumps(args)}"},
    )
    assert response.status_code == 200, response.text
    results = [e["message"] for e in _parse_sse(response.text) if e["type"] == "tool_result"]
    assert len(results) == 1 and results[0]["ok"] is True, results
    return results[0]["result"]


def test_assistant_tools_include_content_rating(client: TestClient) -> None:
    assert client.patch("/api/settings/assistant", json={"enabled": True}).status_code == 200
    asset_id = _upload(client)
    _set_level(client, asset_id, "xxx")

    shown = _assistant_tool(client, "show_assets", {"asset_ids": [asset_id]})
    assert shown["assets"][0]["content_rating"] == "xxx"
    info = _assistant_tool(client, "get_asset", {"asset_id": asset_id})
    assert info["content_rating"] == "xxx"
    found = _assistant_tool(client, "search_assets", {"kind": "upload"})
    assert found["items"][0]["content_rating"] == "xxx"
    viewed = _assistant_tool(client, "view_assets", {"asset_ids": [asset_id]})
    assert viewed["assets"][0]["content_rating"] == "xxx"


def test_bulk_levels_skips_unrated(client: TestClient) -> None:
    rated = _upload(client)
    unrated = _upload(client, (50, 60, 70))
    _set_level(client, rated, "g")
    with client.app.state.session_factory() as db:
        levels = cr.bulk_levels(db, [uuid.UUID(rated), uuid.UUID(unrated), uuid.UUID(rated)])
        assert levels == {uuid.UUID(rated): "g"}
        assert db.execute(select(AssetContentRating)).scalars().all()


def test_fake_rating_level_is_deterministic() -> None:
    from PIL import Image

    from app.annotation.engines import fake_rating_level

    image = Image.new("RGB", (8, 8), (100, 150, 200))
    assert fake_rating_level(image) == fake_rating_level(image.copy())
    assert fake_rating_level(image) in cr.LEVELS
