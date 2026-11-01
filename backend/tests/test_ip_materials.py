"""IP 素材(ADR-0059 1〜4・7章)。有効化、IP と条件、担当者、素材、許可、証跡。"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import AuditEvent
from tests.conftest import login_as, make_png_bytes

EMAILS = ("admin@example.com", "mgr@example.com", "member@example.com", "other@example.com")


def _as(client: TestClient, email: str) -> None:
    client.cookies.clear()
    login_as(client, email, name=email.split("@")[0])


def _setup(client: TestClient) -> None:
    for email in EMAILS:
        _as(client, email)
    _as(client, "admin@example.com")
    response = client.put("/api/ip/settings", json={"enabled": True})
    assert response.status_code == 200, response.text
    assert response.json() == {"enabled": True, "available": True}


def _terms(**overrides: Any) -> dict[str, Any]:
    return {
        "contract_number": "C-1",
        "purposes": ["デザインモック", "発注用コンテ", "最終成果物"],
        "external_ai": "local_only",
        "notes": "クレジット表記: (c) 権利者",
        **overrides,
    }


def _create_ip(client: TestClient, name: str = "作品A") -> dict:
    response = client.post(
        "/api/ip",
        json={
            "name": name,
            "owner_kind": "third_party",
            "rights_holder": "権利者株式会社",
            "terms": _terms(),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _upload_material(client: TestClient, ip_id: str, color: tuple[int, int, int]) -> str:
    response = client.post(
        f"/api/ip/{ip_id}/materials",
        files={"file": ("m.png", make_png_bytes(64, 64, color), "image/png")},
    )
    assert response.status_code == 201, response.text
    return response.json()["asset"]["id"]


def _team_with(client: TestClient, email: str) -> str:
    team = client.post("/api/teams", json={"name": "班"}).json()
    assert client.post(f"/api/teams/{team['id']}/members", json={"email": email}).status_code == 200
    return team["id"]


def _actions(client: TestClient, ip_id: str | None = None) -> list[str]:
    with client.app.state.session_factory() as db:
        query = select(AuditEvent.action).order_by(AuditEvent.occurred_at, AuditEvent.id)
        if ip_id is not None:
            query = query.where(AuditEvent.ip_id == uuid.UUID(ip_id))
        return list(db.execute(query).scalars().all())


def test_unavailable_without_auth_or_setting(client: TestClient, client_oidc: TestClient) -> None:
    assert client.get("/api/ip").status_code == 404
    assert client.get("/api/ip/settings").json() == {"enabled": False, "available": False}
    login_as(client_oidc, "admin@example.com")
    assert client_oidc.get("/api/ip").status_code == 404
    assert client_oidc.get("/api/ip/settings").json()["available"] is False


def test_ip_lifecycle_materials_grants_and_audit(client_oidc: TestClient) -> None:
    c = client_oidc
    _setup(c)
    ip = _create_ip(c)
    assert ip["terms"]["version"] == 1 and ip["can_administer"] is True
    assert (
        c.post(f"/api/ip/{ip['id']}/managers", json={"email": "mgr@example.com"}).status_code == 200
    )
    team_id = _team_with(c, "member@example.com")

    # 管理者でない人は IP を作れない。
    _as(c, "mgr@example.com")
    assert (
        c.post("/api/ip", json={"name": "x", "owner_kind": "own", "terms": _terms()}).status_code
        == 403
    )

    # 担当者: 素材を取り込む。普段のストックには出ない。
    material = _upload_material(c, ip["id"], (200, 10, 10))
    assert material not in [a["id"] for a in c.get("/api/assets").json()["items"]]
    assert c.get(f"/api/assets/{material}").status_code == 200
    # 素材は普段の削除では消せず、ロックも付けない。
    assert c.delete(f"/api/assets/{material}").status_code == 409
    assert c.put(f"/api/assets/{material}/lock", json={"kind": "other"}).status_code == 409

    # 条件の版を重ねる。条件に無い用途は許可できない。
    response = c.post(
        f"/api/ip/{ip['id']}/terms", json=_terms(purposes=["デザインモック", "社内資料"])
    )
    assert response.status_code == 200 and response.json()["terms"]["version"] == 2
    assert [t["version"] for t in c.get(f"/api/ip/{ip['id']}/terms").json()] == [2, 1]
    bad = c.post(
        f"/api/ip/{ip['id']}/grants", json={"team_id": team_id, "purposes": ["最終成果物"]}
    )
    assert bad.status_code == 422
    grants = c.post(
        f"/api/ip/{ip['id']}/grants", json={"team_id": team_id, "purposes": ["デザインモック"]}
    )
    assert grants.status_code == 201, grants.text
    grant = grants.json()["items"][0]
    assert grant["team_name"] == "班" and grant["active"] is True

    # 許可を受けたメンバー: IP と素材が見え、詳細と画像を読める。変更はできない。
    _as(c, "member@example.com")
    listed = c.get("/api/ip").json()["items"]
    assert [(i["name"], i["access"]) for i in listed] == [
        ("作品A", {"purposes": ["デザインモック"], "can_use": True})
    ]
    assert [m["asset"]["id"] for m in c.get(f"/api/ip/{ip['id']}/materials").json()["items"]] == [
        material
    ]
    assert c.get(f"/api/assets/{material}").status_code == 200
    assert c.get(f"/api/assets/{material}/content", params={"variant": "thumb"}).status_code == 200
    assert (
        c.get(f"/api/assets/{material}/content", params={"variant": "original"}).status_code == 200
    )
    assert c.delete(f"/api/assets/{material}").status_code == 404
    assert c.patch(f"/api/assets/{material}/title", json={"title": "x"}).status_code == 404
    assert c.get(f"/api/ip/{ip['id']}/grants").status_code == 403
    assert c.get(f"/api/ip/{ip['id']}/audit").status_code == 403

    # 許可の無い人には見えない。
    _as(c, "other@example.com")
    assert c.get("/api/ip").json()["items"] == []
    assert c.get(f"/api/ip/{ip['id']}").status_code == 404
    assert c.get(f"/api/assets/{material}").status_code == 404

    # 停止すると許可は効かない。終了は戻せない。
    _as(c, "admin@example.com")
    assert c.put(f"/api/ip/{ip['id']}/state", json={"state": "suspended"}).status_code == 200
    _as(c, "member@example.com")
    assert c.get(f"/api/assets/{material}").status_code == 404
    _as(c, "admin@example.com")
    assert c.put(f"/api/ip/{ip['id']}/state", json={"state": "active"}).status_code == 200

    # 取り下げると、許可を受けた人には出ない。担当者には印つきで出る。
    _as(c, "mgr@example.com")
    withdrawn = c.post(f"/api/ip/{ip['id']}/materials/{material}/withdraw")
    assert withdrawn.status_code == 200 and withdrawn.json()["withdrawn_at"] is not None
    _as(c, "member@example.com")
    assert c.get(f"/api/ip/{ip['id']}/materials").json()["items"] == []
    _as(c, "mgr@example.com")
    assert len(c.get(f"/api/ip/{ip['id']}/materials").json()["items"]) == 1

    # 取り消すと見えなくなる。
    revoked = c.post(f"/api/ip/{ip['id']}/grants/{grant['id']}/revoke").json()["items"][0]
    assert revoked["active"] is False and revoked["revoked_at"] is not None
    _as(c, "member@example.com")
    assert c.get(f"/api/ip/{ip['id']}").status_code == 404

    _as(c, "admin@example.com")
    assert c.put(f"/api/ip/{ip['id']}/state", json={"state": "terminated"}).status_code == 200
    assert c.put(f"/api/ip/{ip['id']}/state", json={"state": "active"}).status_code == 409

    # 証跡: 変更と、素材の詳細・原本の取得が残る。
    actions = _actions(c, ip["id"])
    for expected in (
        "ip.create",
        "ip.terms_add",
        "ip.manager_change",
        "ip.material_add",
        "asset.view_detail",
        "asset.download_original",
        "ip.grant_add",
        "ip.state_change",
        "ip.material_withdraw",
        "ip.grant_revoke",
    ):
        assert expected in actions, expected
    page = c.get(f"/api/ip/{ip['id']}/audit", params={"limit": 3}).json()
    assert len(page["items"]) == 3 and page["next_cursor"]
    assert page["items"][0]["action"] == "ip.state_change"


def test_team_and_lock_changes_are_audited(client_oidc: TestClient) -> None:
    c = client_oidc
    _setup(c)
    team_id = _team_with(c, "member@example.com")
    member = next(
        m["user_id"]
        for m in c.get(f"/api/teams/{team_id}").json()["members"]
        if m["role"] == "member"
    )
    assert c.delete(f"/api/teams/{team_id}/members/{member}").status_code == 204
    upload = c.post(
        "/api/assets",
        files={"file": ("u.png", make_png_bytes(32, 32, (1, 2, 3)), "image/png")},
        data={"kind": "upload"},
    ).json()["id"]
    assert c.put(f"/api/assets/{upload}/lock", json={"kind": "delivered"}).status_code == 200
    assert c.delete(f"/api/assets/{upload}/lock").status_code == 204
    actions = _actions(c)
    for expected in (
        "ip.settings",
        "team.create",
        "team.member_add",
        "team.member_remove",
        "asset.lock",
        "asset.unlock",
    ):
        assert expected in actions, expected
