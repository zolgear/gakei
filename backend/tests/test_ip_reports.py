"""IP の状況のレポートと、素材の原本の破棄(ADR-0059 8章・9章)。"""

from __future__ import annotations

import csv
import io
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import Asset, AuditEvent
from tests.conftest import make_png_bytes, wait_for_run_terminal
from tests.test_ip_usage import _as, _edit, _setup


def test_report_json_csv_and_html(client_oidc: TestClient) -> None:
    c = client_oidc
    ids = _setup(c)
    _as(c, "member@example.com")
    for note in ("PO-1", "PO-1", None):
        ip_use = {"purpose": "モック", **({"work_note": note} if note else {})}
        wait_for_run_terminal(c, _edit(c, ids["material"], ip_use).json()["id"])
    assert _edit(c, ids["material"], {"purpose": "コンテ"}).status_code == 409
    c.get(f"/api/assets/{ids['material']}/content", params={"variant": "original"})

    _as(c, "member@example.com")
    assert c.get(f"/api/ip/{ids['ip']}/report").status_code == 403

    _as(c, "mgr@example.com")
    report = c.get(f"/api/ip/{ids['ip']}/report").json()
    assert report["uses"] == 3 and report["uses_succeeded"] == 3 and report["outputs"] == 3
    assert report["derived"] == 3 and report["original_downloads"] == 1 and report["denied"] == 1
    assert {r["key"]: r["count"] for r in report["by_work_note"]} == {"PO-1": 2, "": 1}
    assert [r["key"] for r in report["by_grantee"]] == ["チーム「班」"]
    assert report["materials"][0]["use_count"] == 3
    assert (
        c.get(
            f"/api/ip/{ids['ip']}/report", params={"start": "2030-01-02", "end": "2030-01-01"}
        ).status_code
        == 422
    )

    response = c.get(f"/api/ip/{ids['ip']}/report/download", params={"format": "csv"})
    assert response.status_code == 200
    text = response.content.decode("utf-8")
    assert text.startswith("﻿")
    rows = list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))
    assert len(rows) == 3 and rows[0]["purpose"] == "モック"
    # 名前を入れないときは仮の名前。
    assert {row["user"] for row in rows} == {"利用者 1"}

    html = c.get(f"/api/ip/{ids['ip']}/report/download").content.decode("utf-8")
    assert "IP 利用報告書: 作品A" in html and "member" not in html
    named = c.get(
        f"/api/ip/{ids['ip']}/report/download", params={"include_names": True}
    ).content.decode("utf-8")
    assert "member" in named
    with c.app.state.session_factory() as db:
        exports = (
            db.execute(select(AuditEvent).where(AuditEvent.action == "export.ip_report"))
            .scalars()
            .all()
        )
    assert len(exports) == 3


def test_destroy_materials_after_termination(client_oidc: TestClient) -> None:
    c = client_oidc
    ids = _setup(c)
    # 同じ内容の画像を、IP と関係なく取り込んだ人がいる(ファイルは共有される)。
    _as(c, "other@example.com")
    shared = c.post(
        "/api/assets",
        files={"file": ("s.png", make_png_bytes(64, 64, (200, 30, 30)), "image/png")},
        data={"kind": "upload"},
    ).json()["id"]

    _as(c, "mgr@example.com")
    second = c.post(
        f"/api/ip/{ids['ip']}/materials",
        files={"file": ("m2.png", make_png_bytes(64, 64, (1, 2, 250)), "image/png")},
    ).json()["asset"]["id"]
    assert c.post(f"/api/ip/{ids['ip']}/materials/destroy").status_code == 409
    _as(c, "admin@example.com")
    assert c.put(f"/api/ip/{ids['ip']}/state", json={"state": "terminated"}).status_code == 200
    _as(c, "mgr@example.com")
    result = c.post(f"/api/ip/{ids['ip']}/materials/destroy").json()
    assert result == {"destroyed": 2, "kept_shared": 1}

    with c.app.state.session_factory() as db:
        material = db.get(Asset, uuid.UUID(ids["material"]))
        other = db.get(Asset, uuid.UUID(shared))
        assert material is not None and material.content_withheld_at is not None
        assert other is not None and other.content_withheld_at is None
    # 共有していない素材の画像は消え、共有している画像はほかの人がまだ見られる。
    assert c.get(f"/api/assets/{second}/content", params={"variant": "thumb"}).status_code == 410
    _as(c, "other@example.com")
    assert c.get(f"/api/assets/{shared}/content", params={"variant": "thumb"}).status_code == 200
    # 破棄した素材と同じ内容を後から取り込んでも、画像は残る。
    again = c.post(
        "/api/assets",
        files={"file": ("b.png", make_png_bytes(64, 64, (1, 2, 250)), "image/png")},
        data={"kind": "upload"},
    ).json()
    assert again["content_withheld"] is False
    _as(c, "mgr@example.com")
    report = c.get(f"/api/ip/{ids['ip']}/report").json()
    assert all(m["destroyed"] for m in report["materials"])
