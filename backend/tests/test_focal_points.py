"""サムネイルの焦点(ADR-0043)のテスト。

- 検出: テストで作る単純な画像には顔が無いので、どの形式でも例外にならず None になること。
  検出そのもの(顔の矩形)は `detect_faces` を差し替えて、座標の変換と y の寄せを確かめる。
- 保存と一覧での引き方、取り込みで焦点の worker に渡ること、埋め戻しのツール、API の
  `focal_point`。
"""

from __future__ import annotations

import io
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from app.domain import focal_points
from app.domain.models import AssetFocalPoint
from app.focal import detect
from app.tools import backfill_focal_points
from tests.conftest import make_png_bytes

pytestmark = pytest.mark.windows


# -- 検出 ---------------------------------------------------------------------


def test_cascade_is_bundled_and_loads() -> None:
    import hashlib

    data = detect.CASCADE_PATH.read_bytes()
    assert hashlib.sha256(data).hexdigest() == detect.CASCADE_SHA256
    assert detect._load_cascade() is not None
    assert "MIT" in detect.CASCADE_LICENSE_PATH.read_text(encoding="utf-8")


def _gradient(mode: str, size: tuple[int, int]) -> Image.Image:
    base = Image.linear_gradient("L").resize(size)
    if mode == "L":
        return base
    if mode == "RGBA":
        rgba = Image.merge("RGBA", (base, base.rotate(90), base, base))
        return rgba
    if mode == "P":
        return Image.merge("RGB", (base, base, base)).convert("P", palette=Image.Palette.ADAPTIVE)
    if mode == "I;16":
        return base.convert("I").point(lambda v: v * 256).convert("I;16")
    if mode == "LA":
        return Image.merge("LA", (base, base))
    return Image.merge("RGB", (base, base.rotate(90), base))


@pytest.mark.parametrize("mode", ["RGB", "RGBA", "L", "LA", "P", "I;16"])
@pytest.mark.parametrize("size", [(800, 1200), (1200, 800), (100, 60)])
def test_detect_returns_none_without_faces(mode: str, size: tuple[int, int]) -> None:
    assert detect.detect_focal_point(_gradient(mode, size)) is None


def test_detect_handles_tiny_and_huge_images() -> None:
    assert detect.detect_focal_point(Image.new("RGB", (8, 8), "white")) is None
    assert detect.detect_focal_point(Image.new("RGB", (1, 4000), "white")) is None
    started = time.perf_counter()
    assert detect.detect_focal_point(Image.new("L", (6000, 4000), 128)) is None
    # 縮小してから探すので、大きな画像でも時間がかからない(目安)。
    assert time.perf_counter() - started < 5


def test_detect_reduces_to_detect_long_edge(monkeypatch: pytest.MonkeyPatch) -> None:
    size, _faces = detect.detect_faces(Image.new("RGB", (1600, 800), "white"))
    assert size == (320, 160)
    size, _faces = detect.detect_faces(Image.new("RGB", (200, 100), "white"))
    assert size == (200, 100)


def test_focal_from_face_centers_x_and_shifts_y_up() -> None:
    face = detect.Face(left=100, top=40, width=40, height=40)
    point = detect.focal_from_face((320, 480), face)
    assert point.x == pytest.approx(120 / 320, abs=1e-4)
    # 顔の中心 60 から、顔の高さ 40 の 1/4 = 10 だけ上。
    assert point.y == pytest.approx(50 / 480, abs=1e-4)
    no_shift = detect.focal_from_face((320, 480), face, y_shift=0.0)
    assert no_shift.y == pytest.approx(60 / 480, abs=1e-4)


def test_focal_from_face_clamps_to_unit_range() -> None:
    point = detect.focal_from_face((100, 100), detect.Face(left=0, top=0, width=24, height=4))
    assert 0.0 <= point.x <= 1.0
    assert point.y == pytest.approx(0.01, abs=1e-4)
    point = detect.focal_from_face((100, 100), detect.Face(left=0, top=-50, width=10, height=10))
    assert point.y == 0.0


def test_detect_picks_largest_face(monkeypatch: pytest.MonkeyPatch) -> None:
    faces = [detect.Face(200, 200, 60, 60), detect.Face(10, 10, 30, 30)]
    monkeypatch.setattr(detect, "detect_faces", lambda image: ((320, 320), faces))
    point = detect.detect_focal_point(Image.new("RGB", (320, 320)))
    assert point == detect.focal_from_face((320, 320), faces[0])


def test_detect_swallows_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(image: Image.Image):  # noqa: ANN202
        raise RuntimeError("cv2 error")

    monkeypatch.setattr(detect, "detect_faces", boom)
    assert detect.detect_focal_point(Image.new("RGB", (64, 64))) is None


def test_detect_from_bytes_ignores_broken_data() -> None:
    assert focal_points.detect_from_bytes(b"not an image") is None
    assert focal_points.detect_from_bytes(make_png_bytes(64, 64)) is None


# -- API の補助 -----------------------------------------------------------------


def _upload(client: TestClient, data: bytes | None = None, kind: str = "upload") -> str:
    response = client.post(
        "/api/assets",
        files={"file": ("f.png", data or make_png_bytes(96, 64), "image/png")},
        data={"kind": kind},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _focal_row(client: TestClient, asset_id: str) -> AssetFocalPoint | None:
    with client.app.state.session_factory() as session:
        row = session.get(AssetFocalPoint, uuid.UUID(asset_id))
        if row is not None:
            session.expunge(row)
        return row


def _wait_focal_row(client: TestClient, asset_id: str, timeout: float = 10.0) -> AssetFocalPoint:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = _focal_row(client, asset_id)
        if row is not None:
            return row
        time.sleep(0.02)
    raise TimeoutError(f"asset {asset_id} の焦点が時間内に記録されませんでした")


def _set_focal(client: TestClient, asset_id: str, x: float, y: float) -> None:
    """worker が「見つからない」を書き終えてから、顔が見つかった値で置き換える。"""
    _wait_focal_row(client, asset_id)
    with client.app.state.session_factory() as session:
        focal_points.save(session, uuid.UUID(asset_id), detect.FocalPoint(x=x, y=y))
        session.commit()


# -- 取り込みと worker ----------------------------------------------------------


def test_upload_is_processed_by_focal_worker(client: TestClient) -> None:
    asset_id = _upload(client)
    row = _wait_focal_row(client, asset_id)
    assert row.method == detect.METHOD_NONE
    assert row.x is None and row.y is None
    assert row.version == detect.ALGORITHM_VERSION


def test_upload_records_detected_point(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        detect, "detect_faces", lambda image: ((320, 320), [detect.Face(32, 64, 64, 64)])
    )
    asset_id = _upload(client)
    row = _wait_focal_row(client, asset_id)
    assert row.method == detect.METHOD_ANIMEFACE_LBP
    assert (row.x, row.y) == (0.2, 0.25)


def test_mask_is_not_processed(client: TestClient) -> None:
    mask_id = _upload(client, kind="mask")
    # 後に積んだアップロードが処理されたら、先のマスクは飛ばされている(worker は順に処理する)。
    later = _upload(client)
    _wait_focal_row(client, later)
    assert _focal_row(client, mask_id) is None


def test_generated_outputs_are_processed(client: TestClient) -> None:
    from tests.conftest import wait_for_run_terminal

    response = client.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": "gpt-image-2.5-sunburst",
            "prompt": "a red apple",
            "params": {"n": 2},
        },
    )
    assert response.status_code == 202, response.text
    detail = wait_for_run_terminal(client, response.json()["id"])
    for output in detail["outputs"]:
        _wait_focal_row(client, output["asset_id"])


def test_worker_process_is_idempotent(client: TestClient) -> None:
    asset_id = _upload(client)
    _wait_focal_row(client, asset_id)
    worker = client.app.state.focal_worker
    assert worker.process(uuid.UUID(asset_id)) is False
    assert worker.process(uuid.uuid4()) is False


# -- API の focal_point ---------------------------------------------------------


def test_asset_list_and_detail_carry_focal_point(client: TestClient) -> None:
    with_face = _upload(client, make_png_bytes(64, 96, (10, 20, 30)))
    without_face = _upload(client, make_png_bytes(96, 64, (30, 20, 10)))
    _set_focal(client, with_face, 0.25, 0.125)
    _wait_focal_row(client, without_face)

    items = {item["id"]: item for item in client.get("/api/assets").json()["items"]}
    assert items[with_face]["focal_point"] == {"x": 0.25, "y": 0.125}
    assert items[without_face]["focal_point"] is None

    detail = client.get(f"/api/assets/{with_face}").json()
    assert detail["focal_point"] == {"x": 0.25, "y": 0.125}


def test_runs_and_lineage_carry_focal_point(client: TestClient) -> None:
    from tests.conftest import wait_for_run_terminal

    base = _upload(client)
    _set_focal(client, base, 0.4, 0.2)
    response = client.post(
        "/api/runs",
        json={
            "operation": "edit",
            "model": "gpt-image-2.5-sunburst",
            "prompt": "make it sunset",
            "params": {"n": 1},
            "inputs": [{"asset_id": base, "role": "image", "position": 0}],
        },
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]
    detail = wait_for_run_terminal(client, run_id)
    output_id = detail["outputs"][0]["asset_id"]
    _set_focal(client, output_id, 0.6, 0.3)

    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["inputs"][0]["focal_point"] == {"x": 0.4, "y": 0.2}
    assert detail["outputs"][0]["focal_point"] == {"x": 0.6, "y": 0.3}
    assert detail["primary_parent_focal_point"] == {"x": 0.4, "y": 0.2}

    listed = {r["id"]: r for r in client.get("/api/runs").json()["items"]}
    assert listed[run_id]["outputs"][0]["focal_point"] == {"x": 0.6, "y": 0.3}
    assert listed[run_id]["primary_parent_focal_point"] == {"x": 0.4, "y": 0.2}

    lineage = client.get(f"/api/assets/{output_id}/lineage").json()
    nodes = {n["id"]: n for n in lineage["nodes"] if n["type"] == "asset"}
    assert nodes[base]["asset"]["focal_point"] == {"x": 0.4, "y": 0.2}
    assert nodes[output_id]["asset"]["focal_point"] == {"x": 0.6, "y": 0.3}


def test_bulk_get_skips_rows_without_point(db_session_factory) -> None:  # noqa: ANN001
    from app.domain.models import Asset, AssetKind

    with db_session_factory() as session:
        ids = []
        for i in range(3):
            asset = Asset(
                id=uuid.uuid4(),
                kind=AssetKind.UPLOAD,
                sha256=f"{i:064x}",
                blob_key=f"assets/uploads/{i}.png",
                mime="image/png",
                width=10,
                height=10,
                bytes=1,
            )
            session.add(asset)
            ids.append(asset.id)
        session.flush()
        focal_points.save(session, ids[0], detect.FocalPoint(0.1, 0.2))
        focal_points.save(session, ids[1], None)
        session.commit()

        found = focal_points.bulk_get(session, ids)
        assert set(found) == {ids[0]}
        assert (found[ids[0]].x, found[ids[0]].y) == (0.1, 0.2)
        assert focal_points.needs_compute(session, ids[2])
        assert not focal_points.needs_compute(session, ids[1])
        pending = set(session.execute(focal_points.pending_query()).scalars())
        assert pending == {ids[2]}
        everything = set(session.execute(focal_points.pending_query(recompute=True)).scalars())
        assert everything == set(ids)

        # 版が古い行は対象に戻る。
        row = session.get(AssetFocalPoint, ids[1])
        row.version = detect.ALGORITHM_VERSION - 1
        session.commit()
        pending = set(session.execute(focal_points.pending_query()).scalars())
        assert pending == {ids[1], ids[2]}


# -- 埋め戻しのツール ------------------------------------------------------------


def _delete_focal_rows(client: TestClient) -> None:
    with client.app.state.session_factory() as session:
        for row in session.execute(select(AssetFocalPoint)).scalars():
            session.delete(row)
        session.commit()


def test_backfill_tool(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    ids = [_upload(client, make_png_bytes(64 + i, 64)) for i in range(3)]
    mask_id = _upload(client, kind="mask")
    for asset_id in ids:
        _wait_focal_row(client, asset_id)
    _delete_focal_rows(client)
    session_factory = client.app.state.session_factory
    store = client.app.state.store

    report = backfill_focal_points.run_backfill(session_factory, store, dry_run=True)
    assert report.targets == 3
    assert all(_focal_row(client, a) is None for a in ids)

    report = backfill_focal_points.run_backfill(session_factory, store, limit=2)
    assert (report.targets, report.not_found) == (2, 2)

    monkeypatch.setattr(
        detect, "detect_faces", lambda image: ((100, 100), [detect.Face(40, 40, 20, 20)])
    )
    report = backfill_focal_points.run_backfill(session_factory, store)
    assert (report.targets, report.found) == (1, 1)
    assert _focal_row(client, mask_id) is None

    # 対象が無ければ何もしない。--recompute なら全部を作り直す。
    assert backfill_focal_points.run_backfill(session_factory, store).targets == 0
    report = backfill_focal_points.run_backfill(session_factory, store, recompute=True)
    assert (report.targets, report.found) == (3, 3)
    assert all(_focal_row(client, a).method == detect.METHOD_ANIMEFACE_LBP for a in ids)


def test_backfill_tool_records_missing_original(client: TestClient) -> None:
    asset_id = _upload(client)
    _wait_focal_row(client, asset_id)
    _delete_focal_rows(client)
    store = client.app.state.store
    detail = client.get(f"/api/assets/{asset_id}").json()
    with client.app.state.session_factory() as session:
        from app.domain.models import Asset

        blob_key = session.get(Asset, uuid.UUID(asset_id)).blob_key
    (store.root / blob_key).unlink()
    assert detail["focal_point"] is None

    report = backfill_focal_points.run_backfill(client.app.state.session_factory, store)
    assert (report.targets, report.unreadable) == (1, 1)
    assert _focal_row(client, asset_id).method == detect.METHOD_NONE


def test_backfill_main_dry_run(
    monkeypatch: pytest.MonkeyPatch,
    data_dir,
    capsys: pytest.CaptureFixture[str],  # noqa: ANN001
) -> None:
    from app.main import create_app
    from tests.conftest import _fake_settings

    settings = _fake_settings(data_dir)
    with TestClient(create_app(settings)) as client:
        _wait_focal_row(client, _upload(client))
        _delete_focal_rows(client)
    monkeypatch.setattr(backfill_focal_points, "get_settings", lambda: settings)
    backfill_focal_points.main(["--dry-run"])
    out = capsys.readouterr().out
    assert "対象の画像: 1 件" in out
    assert "--dry-run" in out


def test_backfill_main_rejects_bad_limit(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        backfill_focal_points.main(["--limit", "0"])
    assert excinfo.value.code == 2


def test_detect_from_bytes_handles_png_modes() -> None:
    # 透過やパレットの PNG でも worker の読み込みが通ること(バイト列からの経路)。
    for mode in ("RGBA", "P", "LA"):
        buffer = io.BytesIO()
        _gradient(mode, (300, 200)).save(buffer, format="PNG")
        assert focal_points.detect_from_bytes(buffer.getvalue()) is None


def test_keyword_search_carries_focal_point(client: TestClient) -> None:
    asset_id = _upload(client)
    _set_focal(client, asset_id, 0.3, 0.1)
    response = client.patch(f"/api/assets/{asset_id}/title", json={"title": "focal kitten"})
    assert response.status_code == 200, response.text
    hits = client.get("/api/search", params={"q": "kitten"}).json()["assets"]
    assert [h["focal_point"] for h in hits if h["id"] == asset_id] == [{"x": 0.3, "y": 0.1}]
