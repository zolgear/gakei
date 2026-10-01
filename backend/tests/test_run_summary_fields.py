"""RunSummary の拡張(履歴カード用)。

params/usage/error_message/primary_parent_asset_id/input_count。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import make_png_bytes, wait_for_run_terminal


def _upload(client: TestClient, width: int = 128, height: int = 128, kind: str = "upload") -> str:
    data = make_png_bytes(width=width, height=height)
    response = client.post(
        "/api/assets",
        files={"file": ("f.png", data, "image/png")},
        data={"kind": kind},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_generate_run_summary_has_no_primary_parent(client: TestClient) -> None:
    response = client.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": "gpt-image-2.5-sunburst",
            "prompt": "a plain generate run",
            "params": {"n": 1, "size": "1024x1024"},
        },
    )
    run_id = response.json()["id"]
    wait_for_run_terminal(client, run_id)

    listed = client.get("/api/runs").json()
    matched = next(item for item in listed["items"] if item["id"] == run_id)

    # moderation はフォームに出さず、Run 作成時にサーバーが params へ足す
    # (設定 MODERATION、既定 low)。
    assert matched["params"] == {"n": 1, "size": "1024x1024", "moderation": "low"}
    assert matched["usage"] is not None
    assert matched["error_message"] is None
    assert matched["primary_parent_asset_id"] is None
    assert matched["input_count"] == 0


def test_edit_run_summary_has_primary_parent_and_input_count(client: TestClient) -> None:
    base_asset_id = _upload(client)
    mask_asset_id = _upload(client, kind="mask")

    response = client.post(
        "/api/runs",
        json={
            "operation": "edit",
            "model": "gpt-image-2.5-sunburst",
            "prompt": "edit with mask for summary check",
            "params": {"n": 1},
            "inputs": [
                {"asset_id": base_asset_id, "role": "image", "position": 0},
                {"asset_id": mask_asset_id, "role": "mask", "position": 0},
            ],
        },
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]
    detail = wait_for_run_terminal(client, run_id)

    # RunDetail にも primary_parent_asset_id が乗ること。
    assert detail["primary_parent_asset_id"] == base_asset_id
    assert detail["input_count"] == 1  # role=image のみ数える(maskは含めない)

    listed = client.get("/api/runs").json()
    matched = next(item for item in listed["items"] if item["id"] == run_id)
    assert matched["primary_parent_asset_id"] == base_asset_id
    assert matched["input_count"] == 1
    assert matched["error_message"] is None


def test_failed_run_summary_includes_error_message(client: TestClient) -> None:
    response = client.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": "gpt-image-2.5-sunburst",
            "prompt": "[[fail:contentFilter]] summary failure check",
            "params": {"n": 1},
        },
    )
    run_id = response.json()["id"]
    wait_for_run_terminal(client, run_id)

    listed = client.get("/api/runs").json()
    matched = next(item for item in listed["items"] if item["id"] == run_id)
    assert matched["status"] == "failed"
    assert matched["error_code"] == "contentFilter"
    assert matched["error_message"]


def test_list_runs_query_count_does_not_grow_with_page_size(client: TestClient) -> None:
    """N+1 にならないことの確認: SELECT 回数が Run 件数に比例して増えないこと。"""
    run_ids = []
    for i in range(5):
        response = client.post(
            "/api/runs",
            json={
                "operation": "generate",
                "model": "gpt-image-2.5-sunburst",
                "prompt": f"batch check {i}",
                "params": {"n": 1},
            },
        )
        run_id = response.json()["id"]
        wait_for_run_terminal(client, run_id)
        run_ids.append(run_id)

    from sqlalchemy import event
    from sqlalchemy.orm import ORMExecuteState

    from app.deps import get_session

    # このリクエストに渡すセッションで実行した SELECT だけを数える。エンジン全体やスレッドごとに
    # 数えると、runner・annotator のポーリング(asyncio の executor スレッドで SELECT する)が
    # 計測中に走った回数まで拾い、境界を越えて不安定になる(2026-09-27・2026-10-01 に CI で再現)。
    # `do_orm_execute` は関連の読み込み(selectin・lazy)も通るので、N+1 になれば回数に表れる。
    select_count = 0

    def _count_selects(state: ORMExecuteState) -> None:
        nonlocal select_count
        if state.is_select:
            select_count += 1

    app = client.app

    def _counting_session():  # noqa: ANN202
        session = app.state.session_factory()
        event.listen(session, "do_orm_execute", _count_selects)
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = _counting_session
    try:
        response = client.get("/api/runs", params={"limit": 10})
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    listed_ids = [item["id"] for item in response.json()["items"]]
    assert set(run_ids).issubset(set(listed_ids))

    # 一覧本体 + outputs一括 + inputs一括 でおおむね定数回のはず。
    # Run 件数(5件)より十分少ない回数に収まっていることだけ確認する(N+1になっていない)。
    assert 0 < select_count < len(run_ids)
