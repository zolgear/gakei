"""日時列がタイムゾーン付きで往復すること(レビュー指摘3)。

SQLite は tz を保持しないため、`UtcDateTime` TypeDecorator で読み出し時に UTC を
付け直している。ここではその往復と、API レスポンスのオフセット表記、カーソル
ページングが引き続き動くことを確認する。
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.domain.models import Run, RunStatus
from tests.conftest import wait_for_run_terminal


def test_run_queued_at_roundtrips_as_utc_aware(db_session_factory: sessionmaker) -> None:
    with db_session_factory() as session:
        run = Run(
            provider="fake",
            model="gpt-image-2.5-sunburst",
            operation="generate",
            prompt="tz test",
            params={},
            status=RunStatus.QUEUED,
        )
        session.add(run)
        session.commit()
        run_id = run.id

    with db_session_factory() as session:
        reloaded = session.get(Run, run_id)
        assert reloaded is not None
        assert reloaded.queued_at.tzinfo is not None
        assert reloaded.queued_at.utcoffset() is not None
        assert reloaded.queued_at.utcoffset().total_seconds() == 0


def test_run_detail_json_has_offset_in_queued_at(client: TestClient) -> None:
    response = client.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": "gpt-image-2.5-sunburst",
            "prompt": "tz http test",
            "params": {"n": 1},
        },
    )
    run_id = response.json()["id"]
    detail = wait_for_run_terminal(client, run_id)

    queued_at = detail["queued_at"]
    has_offset = queued_at.endswith("Z") or "+00:00" in queued_at or queued_at[-6] in "+-"
    assert has_offset, f"queued_at にオフセットが付いていない: {queued_at}"


def test_run_list_pagination_still_works_after_utc_fix(client: TestClient) -> None:
    run_ids = []
    for i in range(3):
        response = client.post(
            "/api/runs",
            json={
                "operation": "generate",
                "model": "gpt-image-2.5-sunburst",
                "prompt": f"pagination test {i}",
                "params": {"n": 1},
            },
        )
        run_id = response.json()["id"]
        wait_for_run_terminal(client, run_id)
        run_ids.append(run_id)

    first_page = client.get("/api/runs", params={"limit": 2}).json()
    assert len(first_page["items"]) == 2
    assert first_page["next_cursor"] is not None

    second_page = client.get(
        "/api/runs", params={"limit": 2, "cursor": first_page["next_cursor"]}
    ).json()
    assert len(second_page["items"]) >= 1

    seen_ids = {item["id"] for item in first_page["items"]} | {
        item["id"] for item in second_page["items"]
    }
    assert set(run_ids).issubset(seen_ids)
