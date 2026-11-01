"""IP 素材を使うときの申告と検査、派生物への引き継ぎ(ADR-0059 5章・6章)。"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import AssetIpLineage, AuditEvent, RunIpUse
from app.worker.runner import _ip_denied_message
from tests.conftest import login_as, make_png_bytes, wait_for_run_terminal

EMAILS = (
    "admin@example.com",
    "mgr@example.com",
    "member@example.com",
    "mate@example.com",
    "viewer@example.com",
    "other@example.com",
)


def _as(client: TestClient, email: str) -> None:
    client.cookies.clear()
    login_as(client, email, name=email.split("@")[0])


def _setup(client: TestClient, external_ai: str = "allow") -> dict[str, str]:
    for email in EMAILS:
        _as(client, email)
    _as(client, "admin@example.com")
    assert client.put("/api/ip/settings", json={"enabled": True}).status_code == 200
    ip = client.post(
        "/api/ip",
        json={
            "name": "作品A",
            "owner_kind": "third_party",
            "terms": {"purposes": ["モック", "コンテ"], "external_ai": external_ai},
        },
    ).json()
    assert (
        client.post(f"/api/ip/{ip['id']}/managers", json={"email": "mgr@example.com"}).status_code
        == 200
    )
    team = client.post("/api/teams", json={"name": "班"}).json()
    for email in ("member@example.com", "mate@example.com"):
        assert (
            client.post(f"/api/teams/{team['id']}/members", json={"email": email}).status_code
            == 200
        )
    _as(client, "mgr@example.com")
    material = client.post(
        f"/api/ip/{ip['id']}/materials",
        files={"file": ("m.png", make_png_bytes(64, 64, (200, 30, 30)), "image/png")},
    ).json()["asset"]["id"]
    grants = client.post(
        f"/api/ip/{ip['id']}/grants", json={"team_id": team["id"], "purposes": ["モック"]}
    )
    assert grants.status_code == 201, grants.text
    viewer_grant = client.post(
        f"/api/ip/{ip['id']}/grants",
        json={"email": "viewer@example.com", "purposes": ["モック"], "can_use": False},
    )
    assert viewer_grant.status_code == 201
    team_grant = next(g for g in viewer_grant.json()["items"] if g["team_id"] == team["id"])
    return {"ip": ip["id"], "material": material, "team": team["id"], "grant": team_grant["id"]}


def _edit(client: TestClient, source: str, ip_use: dict[str, Any] | None) -> Any:
    body: dict[str, Any] = {
        "operation": "edit",
        "model": "gpt-image-2.5-sunburst",
        "prompt": "pose",
        "params": {"n": 1},
        "inputs": [{"asset_id": source, "role": "image", "position": 0}],
    }
    if ip_use is not None:
        body["ip_use"] = ip_use
    return client.post("/api/runs", json=body)


def _ips_of(client: TestClient, asset_id: str) -> set[uuid.UUID]:
    with client.app.state.session_factory() as db:
        return set(
            db.execute(
                select(AssetIpLineage.ip_id).where(AssetIpLineage.asset_id == uuid.UUID(asset_id))
            )
            .scalars()
            .all()
        )


def test_declaration_grant_and_derived_sharing(client_oidc: TestClient) -> None:
    c = client_oidc
    ids = _setup(c)
    ip_uuid = uuid.UUID(ids["ip"])
    assert _ips_of(c, ids["material"]) == {ip_uuid}

    _as(c, "member@example.com")
    assert _edit(c, ids["material"], None).status_code == 422
    # 契約に無い用途と、契約にはあるが許可に無い用途。
    assert _edit(c, ids["material"], {"purpose": "社内資料"}).status_code == 409
    assert _edit(c, ids["material"], {"purpose": "コンテ"}).status_code == 409
    response = _edit(c, ids["material"], {"purpose": "モック", "work_note": "発注 PO-12"})
    assert response.status_code == 202, response.text
    run = wait_for_run_terminal(c, response.json()["id"])
    assert run["status"] == "succeeded", run
    derived = run["outputs"][0]["asset_id"]
    assert _ips_of(c, derived) == {ip_uuid}
    with c.app.state.session_factory() as db:
        uses = db.execute(select(RunIpUse)).scalars().all()
        assert [(u.ip_id, u.purpose, u.work_note) for u in uses] == [
            (ip_uuid, "モック", "発注 PO-12")
        ]

    # 孫の世代にも引き継ぐ。
    second = wait_for_run_terminal(c, _edit(c, derived, {"purpose": "モック"}).json()["id"])
    assert _ips_of(c, second["outputs"][0]["asset_id"]) == {ip_uuid}

    # 同じ許可を持つチームのメンバーは読める(変えられない)。担当者も読める。
    _as(c, "mate@example.com")
    assert c.get(f"/api/assets/{derived}").status_code == 200
    assert c.get(f"/api/assets/{derived}/content", params={"variant": "thumb"}).status_code == 200
    assert c.delete(f"/api/assets/{derived}").status_code == 404
    # 読めるだけでなく、許可の用途で入力にも使える。
    assert _edit(c, derived, {"purpose": "モック"}).status_code == 202
    _as(c, "mgr@example.com")
    assert c.get(f"/api/assets/{derived}").status_code == 200

    # 許可の無い人と、見るだけの許可の人。
    _as(c, "other@example.com")
    assert c.get(f"/api/assets/{derived}").status_code == 404
    assert _edit(c, ids["material"], {"purpose": "モック"}).status_code == 422
    _as(c, "viewer@example.com")
    assert c.get(f"/api/assets/{derived}").status_code == 200
    assert _edit(c, derived, {"purpose": "モック"}).status_code == 409

    # 断ったことは証跡に残る。
    with c.app.state.session_factory() as db:
        reasons = [
            e.detail["reason"]
            for e in db.execute(select(AuditEvent).where(AuditEvent.action == "ip.denied"))
            .scalars()
            .all()
        ]
    assert "ip.declarationRequired" in reasons and "ip.purposeNotAllowed" in reasons
    assert "ip.noGrant" in reasons


def test_execution_rechecks_grant(client_oidc: TestClient) -> None:
    c = client_oidc
    ids = _setup(c)
    _as(c, "member@example.com")
    run = wait_for_run_terminal(c, _edit(c, ids["material"], {"purpose": "モック"}).json()["id"])
    session_factory = c.app.state.session_factory
    assert _ip_denied_message(session_factory, uuid.UUID(run["id"])) is None
    _as(c, "mgr@example.com")
    revoked = c.post(f"/api/ip/{ids['ip']}/grants/{ids['grant']}/revoke")
    assert revoked.status_code == 200
    assert _ip_denied_message(session_factory, uuid.UUID(run["id"])) is not None


def test_local_only_rejects_external_provider_and_withdrawn(client_oidc: TestClient) -> None:
    c = client_oidc
    ids = _setup(c, external_ai="local_only")
    _as(c, "member@example.com")
    response = _edit(c, ids["material"], {"purpose": "モック"})
    assert response.status_code == 409
    _as(c, "mgr@example.com")
    assert (
        c.post(
            f"/api/ip/{ids['ip']}/terms", json={"purposes": ["モック"], "external_ai": "allow"}
        ).status_code
        == 200
    )
    _as(c, "member@example.com")
    assert _edit(c, ids["material"], {"purpose": "モック"}).status_code == 202
    _as(c, "mgr@example.com")
    assert c.post(f"/api/ip/{ids['ip']}/materials/{ids['material']}/withdraw").status_code == 200
    _as(c, "member@example.com")
    assert _edit(c, ids["material"], {"purpose": "モック"}).status_code == 422


def test_usage_check_share_and_mixed_derivatives(client_oidc: TestClient) -> None:
    c = client_oidc
    ids = _setup(c)
    # 2つ目の IP。同じチームに同じ用途で許可を出す(担当者は管理者だけ)。
    _as(c, "admin@example.com")
    ip_b = c.post(
        "/api/ip",
        json={
            "name": "作品B",
            "owner_kind": "own",
            "terms": {"purposes": ["モック"], "external_ai": "allow"},
        },
    ).json()
    material_b = c.post(
        f"/api/ip/{ip_b['id']}/materials",
        files={"file": ("b.png", make_png_bytes(64, 64, (10, 200, 10)), "image/png")},
    ).json()["asset"]["id"]
    assert (
        c.post(
            f"/api/ip/{ip_b['id']}/grants", json={"team_id": ids["team"], "purposes": ["モック"]}
        ).status_code
        == 201
    )

    _as(c, "member@example.com")
    check = c.post("/api/ip/usage-check", json={"asset_ids": [ids["material"], material_b]}).json()
    assert check["required"] is True and check["purposes"] == ["モック"]
    assert sorted(ip["name"] for ip in check["ips"]) == ["作品A", "作品B"]
    body = {
        "operation": "edit",
        "model": "gpt-image-2.5-sunburst",
        "prompt": "mix",
        "params": {"n": 1},
        "inputs": [
            {"asset_id": ids["material"], "role": "image", "position": 0},
            {"asset_id": material_b, "role": "image", "position": 1},
        ],
        "ip_use": {"purpose": "モック"},
    }
    response = c.post("/api/runs", json=body)
    assert response.status_code == 202, response.text
    mixed = wait_for_run_terminal(c, response.json()["id"])["outputs"][0]["asset_id"]
    assert len(_ips_of(c, mixed)) == 2
    detail = c.get(f"/api/assets/{mixed}").json()
    assert detail["ip_marked"] is True and len(detail["ips"]) == 2

    # 共有リンクにはできない。
    _as(c, "admin@example.com")
    assert c.patch("/api/settings/share", json={"enabled": True}).status_code == 200
    _as(c, "member@example.com")
    share = c.post("/api/shares", json={"asset_id": mixed, "scope": "single"})
    assert share.status_code == 409

    # 作品A の担当者は、作品B の許可が無くても混ざった派生物を見られ、印が付く。
    _as(c, "mgr@example.com")
    assert c.get(f"/api/assets/{mixed}").status_code == 200
    items = c.get(f"/api/ip/{ids['ip']}/derivatives").json()["items"]
    assert [(i["asset"]["id"], i["other_ip_count"]) for i in items] == [(mixed, 1)]

    # 読めない人の確かめには IP を出さない。
    _as(c, "other@example.com")
    assert c.post("/api/ip/usage-check", json={"asset_ids": [mixed]}).json() == {
        "required": False,
        "ips": [],
        "purposes": [],
    }


def test_ai_blocked_by_contract(client_oidc: TestClient) -> None:
    from app.domain import ip_usage

    c = client_oidc
    ids = _setup(c, external_ai="local_only")
    material = uuid.UUID(ids["material"])
    with c.app.state.session_factory() as db:
        assert ip_usage.ai_blocked_asset_ids(db, [material], "external") == {material}
        assert ip_usage.ai_blocked_asset_ids(db, [material], "local") == set()
    _as(c, "mgr@example.com")
    assert (
        c.post(
            f"/api/ip/{ids['ip']}/terms", json={"purposes": ["モック"], "external_ai": "deny"}
        ).status_code
        == 200
    )
    with c.app.state.session_factory() as db:
        assert ip_usage.ai_blocked_asset_ids(db, [material], "local") == {material}
