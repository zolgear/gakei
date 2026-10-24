"""履歴の絞り込みの形・透過・領域(ADR-0055 4章)。境界、組み合わせ、ページング、画像単位の並び。"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.history_filters import SQUARE_MAX_RATIO, classify_shape
from app.domain.models import Asset, AssetEmbedding, Run
from app.embedding.catalog import XENOVA_CLIP_REVISION
from tests.conftest import wait_for_run_terminal

ACTIVE_KEY = f"fake:onnx:clip-vit-b32-u8@{XENOVA_CLIP_REVISION}"

REGIONS = {
    "version": 1,
    "delivery": "image_and_text",
    "regions": [{"number": 1, "box": [0.1, 0.1, 0.5, 0.5], "instruction": "x"}],
}


# -- 形の判定(フロントの historyLabels.test.ts と同じ境界) ---------------------------


@pytest.mark.parametrize(
    ("width", "height", "expected"),
    [
        (1024, 1024, "square"),
        (1100, 1000, "square"),  # 長辺 / 短辺 = 1.1 ちょうどは正方形
        (1000, 1100, "square"),
        (1101, 1000, "wide"),
        (1000, 1101, "portrait"),
        (1536, 1024, "wide"),
        (1024, 1536, "portrait"),
        (0, 1024, None),
        (None, None, None),
    ],
)
def test_classify_shape_boundaries(width: Any, height: Any, expected: str | None) -> None:
    assert classify_shape(width, height) == expected


def test_square_ratio_is_1_1() -> None:
    assert SQUARE_MAX_RATIO == pytest.approx(1.1)


# -- 共通 ---------------------------------------------------------------------------


def _run(client: TestClient, prompt: str, *, n: int = 1, **params: Any) -> dict:
    body = {
        "operation": "generate",
        "model": "gpt-image-2.5-sunburst",
        "prompt": prompt,
        "params": {"n": n, **params},
    }
    response = client.post("/api/runs", json=body)
    assert response.status_code == 202, response.text
    return wait_for_run_terminal(client, response.json()["id"])


def _output_ids(run: dict) -> list[str]:
    return [o["asset_id"] for o in sorted(run["outputs"], key=lambda o: o["output_index"])]


def _set_size(client: TestClient, asset_id: str, width: int, height: int) -> None:
    with client.app.state.session_factory() as db:
        asset = db.get(Asset, uuid.UUID(asset_id))
        assert asset is not None
        asset.width = width
        asset.height = height
        db.commit()


def _update_run(client: TestClient, run_id: str, **values: Any) -> None:
    with client.app.state.session_factory() as db:
        run = db.get(Run, uuid.UUID(run_id))
        assert run is not None
        for key, value in values.items():
            setattr(run, key, value)
        db.commit()


def _runs_ids(client: TestClient, limit: int = 50, **params: Any) -> list[str]:
    ids: list[str] = []
    cursor: str | None = None
    while True:
        query: dict[str, Any] = {"limit": limit, **params}
        if cursor:
            query["cursor"] = cursor
        response = client.get("/api/runs", params=query)
        assert response.status_code == 200, response.text
        body = response.json()
        ids.extend(item["id"] for item in body["items"])
        cursor = body["next_cursor"]
        if not cursor:
            return ids


def _history_ids(client: TestClient, sort: str, limit: int = 50, **params: Any) -> list[str]:
    ids: list[str] = []
    cursor: str | None = None
    while True:
        query: dict[str, Any] = {"sort": sort, "limit": limit, **params}
        if cursor:
            query["cursor"] = cursor
        response = client.get("/api/history/assets", params=query)
        assert response.status_code == 200, response.text
        body = response.json()
        ids.extend(item["asset_id"] for item in body["items"])
        cursor = body["next_cursor"]
        if not cursor:
            return ids


# -- GET /api/runs -------------------------------------------------------------------


def test_runs_shape_filter_uses_actual_size_and_boundary(client: TestClient) -> None:
    sizes = [(1024, 1024), (1100, 1000), (1101, 1000), (1000, 1101), (832, 1216)]
    runs = []
    for i, (w, h) in enumerate(sizes):
        run = _run(client, f"shape {i}")
        _set_size(client, _output_ids(run)[0], w, h)
        runs.append(run)
    failed = _run(client, "[[fail:server]]")  # 出力の無い Run はどの形にも当たらない
    newest_first = [r["id"] for r in reversed(runs)]

    expected = {
        shape: [
            rid
            for rid, (w, h) in zip(newest_first, reversed(sizes), strict=True)
            if classify_shape(w, h) == shape
        ]
        for shape in ("square", "portrait", "wide")
    }
    assert expected["square"] and expected["portrait"] and expected["wide"]
    for shape, ids in expected.items():
        assert _runs_ids(client, shape=shape) == ids
        # 1件ずつのページでも同じ並び(ページングと整合する)。
        assert _runs_ids(client, limit=1, shape=shape) == ids
        assert failed["id"] not in ids
    assert client.get("/api/runs", params={"shape": "tall"}).status_code == 422


def test_runs_shape_matches_any_live_output(client: TestClient) -> None:
    run = _run(client, "two", n=2)
    first, second = _output_ids(run)
    _set_size(client, first, 1024, 1024)
    _set_size(client, second, 1024, 1536)
    assert run["id"] in _runs_ids(client, shape="square")
    assert run["id"] in _runs_ids(client, shape="portrait")
    assert run["id"] not in _runs_ids(client, shape="wide")

    # 削除した出力は数えない。
    with client.app.state.session_factory() as db:
        asset = db.get(Asset, uuid.UUID(second))
        assert asset is not None
        asset.deleted_at = datetime.now(UTC)
        db.commit()
    assert run["id"] not in _runs_ids(client, shape="portrait")
    assert run["id"] in _runs_ids(client, shape="square")


def test_runs_transparent_filter(client: TestClient) -> None:
    opaque = _run(client, "opaque", background="opaque")
    auto = _run(client, "auto")
    clear = _run(client, "clear", background="transparent")
    other = _run(client, "other provider", background="transparent")
    # 背景の指定を持たないプロバイダーは、params に同じ値があっても判定しない。
    _update_run(client, other["id"], provider="comfyui")

    ids = _runs_ids(client, transparent=True)
    assert ids == [clear["id"]]
    assert opaque["id"] not in ids and auto["id"] not in ids
    assert _runs_ids(client, transparent=False) == _runs_ids(client)


def test_runs_regions_filter_and_combination(client: TestClient) -> None:
    plain = _run(client, "plain")
    empty = _run(client, "empty regions")
    boxed = _run(client, "boxed")
    boxed_clear = _run(client, "boxed clear", background="transparent")
    _update_run(client, empty["id"], regions={**REGIONS, "regions": []})
    _update_run(client, boxed["id"], regions=REGIONS)
    _update_run(client, boxed_clear["id"], regions=REGIONS)
    _set_size(client, _output_ids(boxed_clear)[0], 1536, 1024)
    _set_size(client, _output_ids(boxed)[0], 1024, 1024)

    assert _runs_ids(client, regions=True) == [boxed_clear["id"], boxed["id"]]
    assert plain["id"] not in _runs_ids(client, regions=True)
    assert _runs_ids(client, regions=True, transparent=True) == [boxed_clear["id"]]
    assert _runs_ids(client, regions=True, shape="wide") == [boxed_clear["id"]]
    assert _runs_ids(client, regions=True, shape="square") == [boxed["id"]]
    assert _runs_ids(client, regions=True, transparent=True, shape="square") == []
    # お気に入りとも組み合わせられる。
    fav = _output_ids(boxed)[0]
    assert client.put(f"/api/assets/{fav}/favorite").status_code == 200
    assert _runs_ids(client, limit=1, regions=True, favorite=True) == [boxed["id"]]


# -- GET /api/history/assets ---------------------------------------------------------


def test_history_group_filters_by_each_image(client: TestClient) -> None:
    run = _run(client, "two", n=2, background="transparent")
    first, second = _output_ids(run)
    _set_size(client, first, 1024, 1024)
    _set_size(client, second, 1024, 1536)
    other = _run(client, "other")
    _set_size(client, _output_ids(other)[0], 1024, 1536)

    # 画像単位では形はその画像で見る(同じ Run の別の形の画像は出さない)。
    assert _history_ids(client, "group", shape="portrait") == [second, _output_ids(other)[0]]
    assert _history_ids(client, "group", limit=1, shape="square") == [first]
    assert _history_ids(client, "group", transparent=True) == [first, second]
    assert _history_ids(client, "group", transparent=True, shape="portrait") == [second]
    assert _history_ids(client, "group", regions=True) == []
    body = client.get("/api/history/assets", params={"sort": "group", "shape": "portrait"}).json()
    assert sum(section["count"] for section in body["groups"]) == 2


def _wait_embedded(client: TestClient, asset_ids: list[str]) -> None:
    deadline = time.monotonic() + 15
    pending = {uuid.UUID(a) for a in asset_ids}
    while time.monotonic() < deadline:
        with client.app.state.session_factory() as db:
            rows = db.execute(
                select(AssetEmbedding.asset_id).where(
                    AssetEmbedding.model_key == ACTIVE_KEY,
                    AssetEmbedding.status == "succeeded",
                    AssetEmbedding.asset_id.in_(pending),
                )
            ).all()
        pending -= {r[0] for r in rows}
        if not pending:
            return
        time.sleep(0.05)
    raise TimeoutError(f"埋め込みが終わりませんでした: {pending}")


def test_history_embedding_filters(client: TestClient) -> None:
    response = client.patch("/api/settings/embeddings", json={"enabled": True})
    assert response.status_code == 200, response.text
    runs = [_run(client, f"image {i}") for i in range(4)]
    ids = [_output_ids(r)[0] for r in runs]
    _wait_embedded(client, ids)
    for asset_id in ids[:2]:
        _set_size(client, asset_id, 1536, 1024)
    _update_run(client, runs[3]["id"], regions=REGIONS)

    everything = _history_ids(client, "embedding")
    assert sorted(everything) == sorted(ids)
    wide = _history_ids(client, "embedding", shape="wide")
    assert sorted(wide) == sorted(ids[:2])
    assert _history_ids(client, "embedding", limit=1, shape="wide") == wide
    assert _history_ids(client, "embedding", regions=True) == [ids[3]]
    # 絞り込みの後で全体を読んでも、キャッシュが混ざらない。
    assert _history_ids(client, "embedding") == everything
