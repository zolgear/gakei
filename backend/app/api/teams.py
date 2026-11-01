"""チーム(ADR-0058)。認証モード(oidc)だけで使う(個人モードでは 404)。

- 一覧は自分が入っているチーム。管理者は `scope=all` で全部を見る。
- 作る・名前を変える・消す・代表者を決めるのは管理者だけ。
- メンバーを出し入れするのは管理者と、そのチームの代表者(代表者は代表者でない人だけ)。
- 見えないチーム(入っていない、管理者でない)は、存在しない場合と同じ 404(ADR-0025 と同じ)。
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.auth.deps import require_user
from app.auth.identity import CurrentUser
from app.auth.runtime import AuthRuntime, get_auth_runtime
from app.deps import get_session
from app.domain import audit
from app.domain import teams as domain
from app.domain.avatars import avatar_url
from app.domain.models import Team
from app.domain.schemas import (
    TeamCreateRequest,
    TeamDetail,
    TeamListResponse,
    TeamMemberAddRequest,
    TeamMemberRoleRequest,
    TeamMemberView,
    TeamSummary,
    TeamUpdateRequest,
)
from app.i18n import t

router = APIRouter(prefix="/api/teams", tags=["teams"])


def _require_oidc(runtime: AuthRuntime = Depends(get_auth_runtime)) -> None:
    if not runtime.is_oidc:
        raise HTTPException(status_code=404, detail=t("auth.disabled"))


def _require_admin(user: CurrentUser) -> None:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail=t("teams.adminOnly"))


def _visible_team(db: Session, user: CurrentUser, team_id: uuid.UUID) -> Team:
    team = domain.get_team(db, team_id)
    if team is None or not domain.can_see(db, user, team):
        raise HTTPException(status_code=404, detail=t("teams.notFound"))
    return team


def _summary(team: Team, count: int, role: str | None) -> TeamSummary:
    return TeamSummary(
        id=team.id,
        name=team.name,
        description=team.description,
        member_count=count,
        my_role=role,  # type: ignore[arg-type]
        created_at=team.created_at,
    )


def _detail(db: Session, user: CurrentUser, team: Team) -> TeamDetail:
    rows = domain.members(db, team.id)
    my_role = next((row.role for row in rows if row.user.id == user.id), None)
    return TeamDetail(
        **_summary(team, len(rows), my_role).model_dump(),
        members=[
            TeamMemberView(
                user_id=row.user.id,
                name=row.user.name,
                email=row.user.email,
                avatar_url=avatar_url(row.user.id, row.user.avatar_sha256),
                role=row.role,  # type: ignore[arg-type]
                added_at=row.added_at,
            )
            for row in rows
        ],
        can_manage_members=domain.can_manage_members(db, user, team),
        can_manage_leads=user.is_admin,
    )


def _name_error(exc: Exception) -> HTTPException:
    if isinstance(exc, domain.TeamNameTakenError):
        return HTTPException(status_code=409, detail=t("teams.nameTaken"))
    return HTTPException(status_code=422, detail=t("teams.invalidName"))


@router.get(
    "",
    response_model=TeamListResponse,
    operation_id="list_teams",
    dependencies=[Depends(_require_oidc)],
)
def list_teams(
    scope: Literal["mine", "all"] = Query(default="mine", description="all は管理者だけ"),
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> TeamListResponse:
    if scope == "all":
        _require_admin(user)
    rows = domain.list_teams(db, user, all_teams=scope == "all")
    return TeamListResponse(items=[_summary(team, count, role) for team, count, role in rows])


@router.post(
    "",
    response_model=TeamDetail,
    status_code=201,
    operation_id="create_team",
    dependencies=[Depends(_require_oidc)],
)
def create_team(
    body: TeamCreateRequest,
    request: Request,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> TeamDetail:
    _require_admin(user)
    try:
        team = domain.create_team(db, user, body.name, body.description)
    except (domain.TeamNameError, domain.TeamNameTakenError) as exc:
        raise _name_error(exc) from exc
    audit.record_now(
        db,
        user,
        "team.create",
        target_type="team",
        target_id=team.id,
        detail={"name": team.name},
        request=request,
    )
    return _detail(db, user, team)


@router.get(
    "/{team_id}",
    response_model=TeamDetail,
    operation_id="get_team",
    dependencies=[Depends(_require_oidc)],
)
def get_team(
    team_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> TeamDetail:
    return _detail(db, user, _visible_team(db, user, team_id))


@router.patch(
    "/{team_id}",
    response_model=TeamDetail,
    operation_id="update_team",
    dependencies=[Depends(_require_oidc)],
)
def update_team(
    team_id: uuid.UUID,
    body: TeamUpdateRequest,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> TeamDetail:
    team = _visible_team(db, user, team_id)
    _require_admin(user)
    try:
        domain.update_team(db, team, name=body.name, description=body.description)
    except (domain.TeamNameError, domain.TeamNameTakenError) as exc:
        raise _name_error(exc) from exc
    return _detail(db, user, team)


@router.delete(
    "/{team_id}", status_code=204, operation_id="delete_team", dependencies=[Depends(_require_oidc)]
)
def delete_team(
    team_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> None:
    team = _visible_team(db, user, team_id)
    _require_admin(user)
    domain.delete_team(db, team)
    audit.record_now(
        db,
        user,
        "team.delete",
        target_type="team",
        target_id=team.id,
        detail={"name": team.name},
        request=request,
    )


@router.post(
    "/{team_id}/members",
    response_model=TeamDetail,
    operation_id="add_team_member",
    dependencies=[Depends(_require_oidc)],
)
def add_team_member(
    team_id: uuid.UUID,
    body: TeamMemberAddRequest,
    request: Request,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> TeamDetail:
    """メールアドレスでメンバーを足す(一度ログインした人だけ)。代表者にするのは管理者だけ。"""
    team = _visible_team(db, user, team_id)
    try:
        added = domain.add_member(db, user, team, body.email, body.role)
    except domain.TeamPermissionError as exc:
        raise HTTPException(status_code=403, detail=t("teams.forbidden")) from exc
    except domain.TeamUserNotFoundError as exc:
        raise HTTPException(status_code=422, detail=t("teams.userNotFound")) from exc
    audit.record_now(
        db,
        user,
        "team.member_add",
        target_type="team",
        target_id=team.id,
        detail={"user_id": str(added.id), "email": added.email, "role": body.role},
        request=request,
    )
    return _detail(db, user, team)


@router.patch(
    "/{team_id}/members/{user_id}",
    response_model=TeamDetail,
    operation_id="set_team_member_role",
    dependencies=[Depends(_require_oidc)],
)
def set_team_member_role(
    team_id: uuid.UUID,
    user_id: uuid.UUID,
    body: TeamMemberRoleRequest,
    request: Request,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> TeamDetail:
    """代表者にする・代表者から外す(管理者だけ)。"""
    team = _visible_team(db, user, team_id)
    try:
        domain.set_role(db, user, team, user_id, body.role)
    except domain.TeamPermissionError as exc:
        raise HTTPException(status_code=403, detail=t("teams.forbidden")) from exc
    except domain.TeamMemberNotFoundError as exc:
        raise HTTPException(status_code=404, detail=t("teams.memberNotFound")) from exc
    audit.record_now(
        db,
        user,
        "team.member_role",
        target_type="team",
        target_id=team.id,
        detail={"user_id": str(user_id), "role": body.role},
        request=request,
    )
    return _detail(db, user, team)


@router.delete(
    "/{team_id}/members/{user_id}",
    status_code=204,
    operation_id="remove_team_member",
    dependencies=[Depends(_require_oidc)],
)
def remove_team_member(
    team_id: uuid.UUID,
    user_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> None:
    team = _visible_team(db, user, team_id)
    try:
        role = domain.remove_member(db, user, team, user_id)
    except domain.TeamPermissionError as exc:
        raise HTTPException(status_code=403, detail=t("teams.forbidden")) from exc
    except domain.TeamMemberNotFoundError as exc:
        raise HTTPException(status_code=404, detail=t("teams.memberNotFound")) from exc
    audit.record_now(
        db,
        user,
        "team.member_remove",
        target_type="team",
        target_id=team.id,
        detail={"user_id": str(user_id), "role": role},
        request=request,
    )
