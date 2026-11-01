"""チーム(ADR-0058)。管理者が作り、代表者がメンバーを出し入れする。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import login_as


def _as(client: TestClient, email: str) -> None:
    client.cookies.clear()
    login_as(client, email, name=email.split("@")[0])


def _create(client: TestClient, name: str = "Poses") -> dict:
    response = client.post("/api/teams", json={"name": name, "description": "班"})
    assert response.status_code == 201, response.text
    return response.json()


def _add(client: TestClient, team_id: str, email: str, role: str = "member") -> int:
    return client.post(
        f"/api/teams/{team_id}/members", json={"email": email, "role": role}
    ).status_code


def test_personal_mode_has_no_teams(client: TestClient) -> None:
    assert client.get("/api/teams").status_code == 404
    assert client.post("/api/teams", json={"name": "x"}).status_code == 404


def test_admin_creates_team_and_lead_manages_members(client_oidc: TestClient) -> None:
    for email in ("lead@example.com", "member@example.com", "other@example.com"):
        _as(client_oidc, email)
    _as(client_oidc, "admin@example.com")
    team = _create(client_oidc)
    assert team["member_count"] == 0 and team["can_manage_leads"] is True
    assert client_oidc.post("/api/teams", json={"name": " Poses "}).status_code == 409
    assert _add(client_oidc, team["id"], "LEAD@example.com", "lead") == 200
    assert _add(client_oidc, team["id"], "nobody@example.com") == 422
    assert [
        t["name"] for t in client_oidc.get("/api/teams", params={"scope": "all"}).json()["items"]
    ] == ["Poses"]

    # 代表者: メンバーを足す・外す。代表者にはできない。
    _as(client_oidc, "lead@example.com")
    mine = client_oidc.get("/api/teams").json()["items"]
    assert [(t["name"], t["my_role"]) for t in mine] == [("Poses", "lead")]
    assert _add(client_oidc, team["id"], "member@example.com") == 200
    assert _add(client_oidc, team["id"], "other@example.com", "lead") == 403
    detail = client_oidc.get(f"/api/teams/{team['id']}").json()
    assert detail["can_manage_members"] is True and detail["can_manage_leads"] is False
    roles = {m["email"]: m["role"] for m in detail["members"]}
    assert roles == {"lead@example.com": "lead", "member@example.com": "member"}
    lead_id = next(m["user_id"] for m in detail["members"] if m["role"] == "lead")
    member_id = next(m["user_id"] for m in detail["members"] if m["role"] == "member")
    assert client_oidc.get("/api/teams", params={"scope": "all"}).status_code == 403
    assert client_oidc.patch(f"/api/teams/{team['id']}", json={"name": "x"}).status_code == 403

    # メンバー: 見られるが、出し入れはできない。
    _as(client_oidc, "member@example.com")
    detail = client_oidc.get(f"/api/teams/{team['id']}").json()
    assert detail["my_role"] == "member" and detail["can_manage_members"] is False
    assert _add(client_oidc, team["id"], "other@example.com") == 403

    # 入っていない人には見えない(404)。
    _as(client_oidc, "other@example.com")
    assert client_oidc.get(f"/api/teams/{team['id']}").status_code == 404
    assert client_oidc.get("/api/teams").json()["items"] == []

    # 代表者は代表者を外せない。メンバーは外せる。
    _as(client_oidc, "lead@example.com")
    assert client_oidc.delete(f"/api/teams/{team['id']}/members/{lead_id}").status_code == 403
    assert client_oidc.delete(f"/api/teams/{team['id']}/members/{member_id}").status_code == 204
    assert client_oidc.delete(f"/api/teams/{team['id']}/members/{member_id}").status_code == 404

    # 管理者: 役割を変え、名前を変え、消す。
    _as(client_oidc, "admin@example.com")
    response = client_oidc.patch(
        f"/api/teams/{team['id']}/members/{lead_id}", json={"role": "member"}
    )
    assert response.status_code == 200
    assert [m["role"] for m in response.json()["members"]] == ["member"]
    renamed = client_oidc.patch(f"/api/teams/{team['id']}", json={"name": "Poses B"})
    assert renamed.json()["name"] == "Poses B"
    assert client_oidc.delete(f"/api/teams/{team['id']}").status_code == 204
    assert client_oidc.get(f"/api/teams/{team['id']}").status_code == 404
    # 消したチームの名前は、また使える。
    _create(client_oidc, "Poses B")
