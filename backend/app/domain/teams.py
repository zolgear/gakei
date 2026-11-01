"""チーム(ADR-0058)。利用者の集まりで、IP 素材の許可(ADR-0059)の単位。

- 認証モード(oidc)だけで使う。モードの確かめは API の側で行う。
- 管理者はチームを作る・名前を変える・消す・代表者を決める。代表者は自分のチームのメンバー
  (代表者でない人)を出し入れする。メンバーはチームとメンバーを見られる。
- 利用者が入っているチームは `user_team_ids` の1か所で引く(許可の判定もここを通す)。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Select, delete, func, select
from sqlalchemy.orm import Session

from app.auth.identity import CurrentUser
from app.domain.models import TEAM_ROLE_LEAD, TEAM_ROLE_MEMBER, AppUser, Team, TeamMember

NAME_MAX_LENGTH = 100
DESCRIPTION_MAX_LENGTH = 1000
TEAM_ROLES = (TEAM_ROLE_MEMBER, TEAM_ROLE_LEAD)


class TeamNameError(ValueError):
    """名前が空か長すぎる。"""


class TeamNameTakenError(Exception):
    """同じ名前の(削除していない)チームがある。"""


class TeamUserNotFoundError(Exception):
    """メールアドレスに当たる利用者がいない(一度もログインしていない)。"""


class TeamPermissionError(Exception):
    """その操作をする権限が無い。"""


class TeamMemberNotFoundError(Exception):
    """その人はチームのメンバーではない。"""


@dataclass(frozen=True)
class TeamMemberRow:
    user: AppUser
    role: str
    added_at: datetime


def _utcnow() -> datetime:
    return datetime.now(UTC)


def normalize_name(name: str) -> str:
    name = name.strip()
    if not name or len(name) > NAME_MAX_LENGTH:
        raise TeamNameError(name)
    return name


def normalize_description(description: str | None) -> str | None:
    if description is None:
        return None
    description = description.strip()
    return description[:DESCRIPTION_MAX_LENGTH] or None


def user_team_ids(user_id: uuid.UUID) -> Select[tuple]:
    """利用者が入っている(削除していない)チームの id の副問い合わせ。"""
    return (
        select(TeamMember.team_id)
        .join(Team, Team.id == TeamMember.team_id)
        .where(TeamMember.user_id == user_id, Team.deleted_at.is_(None))
    )


def get_team(db: Session, team_id: uuid.UUID) -> Team | None:
    team = db.get(Team, team_id)
    if team is None or team.deleted_at is not None:
        return None
    return team


def member_role(db: Session, team_id: uuid.UUID, user_id: uuid.UUID | None) -> str | None:
    if user_id is None:
        return None
    return db.execute(
        select(TeamMember.role).where(TeamMember.team_id == team_id, TeamMember.user_id == user_id)
    ).scalar_one_or_none()


def can_see(db: Session, viewer: CurrentUser, team: Team) -> bool:
    """チームとメンバーを見られるか(管理者とメンバー)。"""
    return viewer.is_admin or member_role(db, team.id, viewer.id) is not None


def can_manage_members(db: Session, viewer: CurrentUser, team: Team) -> bool:
    """メンバーを出し入れできるか(管理者と代表者)。"""
    return viewer.is_admin or member_role(db, team.id, viewer.id) == TEAM_ROLE_LEAD


def list_teams(
    db: Session, viewer: CurrentUser, *, all_teams: bool
) -> list[tuple[Team, int, str | None]]:
    """(チーム、メンバーの数、見る人の役割)。`all_teams` は管理者が全部を見るとき。"""
    counts = (
        select(TeamMember.team_id, func.count().label("n")).group_by(TeamMember.team_id).subquery()
    )
    query = (
        select(Team, func.coalesce(counts.c.n, 0))
        .outerjoin(counts, counts.c.team_id == Team.id)
        .where(Team.deleted_at.is_(None))
        .order_by(Team.name, Team.id)
    )
    if not all_teams:
        if viewer.id is None:
            return []
        query = query.where(Team.id.in_(user_team_ids(viewer.id)))
    rows = db.execute(query).all()
    roles: dict[uuid.UUID, str] = {}
    if viewer.id is not None and rows:
        roles = dict(
            db.execute(
                select(TeamMember.team_id, TeamMember.role).where(
                    TeamMember.user_id == viewer.id,
                    TeamMember.team_id.in_([team.id for team, _ in rows]),
                )
            ).all()
        )
    return [(team, count, roles.get(team.id)) for team, count in rows]


def members(db: Session, team_id: uuid.UUID) -> list[TeamMemberRow]:
    rows = db.execute(
        select(AppUser, TeamMember.role, TeamMember.added_at)
        .join(TeamMember, TeamMember.user_id == AppUser.id)
        .where(TeamMember.team_id == team_id)
        .order_by(TeamMember.role.desc(), AppUser.name, AppUser.email, AppUser.id)
    ).all()
    return [TeamMemberRow(user=user, role=role, added_at=added_at) for user, role, added_at in rows]


def _name_taken(db: Session, name: str, exclude: uuid.UUID | None = None) -> bool:
    query = select(Team.id).where(Team.name == name, Team.deleted_at.is_(None))
    if exclude is not None:
        query = query.where(Team.id != exclude)
    return db.execute(query).first() is not None


def create_team(db: Session, viewer: CurrentUser, name: str, description: str | None) -> Team:
    name = normalize_name(name)
    if _name_taken(db, name):
        raise TeamNameTakenError(name)
    now = _utcnow()
    team = Team(
        name=name,
        description=normalize_description(description),
        created_by_user_id=viewer.id,
        created_at=now,
        updated_at=now,
    )
    db.add(team)
    db.commit()
    return team


def update_team(
    db: Session, team: Team, *, name: str | None = None, description: str | None = None
) -> Team:
    if name is not None:
        name = normalize_name(name)
        if _name_taken(db, name, exclude=team.id):
            raise TeamNameTakenError(name)
        team.name = name
    if description is not None:
        team.description = normalize_description(description)
    team.updated_at = _utcnow()
    db.commit()
    return team


def delete_team(db: Session, team: Team) -> None:
    team.deleted_at = _utcnow()
    db.commit()


def find_user_by_email(db: Session, email: str) -> AppUser | None:
    email = email.strip().lower()
    if not email:
        return None
    return (
        db.execute(
            select(AppUser).where(func.lower(AppUser.email) == email).order_by(AppUser.created_at)
        )
        .scalars()
        .first()
    )


def add_member(
    db: Session, viewer: CurrentUser, team: Team, email: str, role: str = TEAM_ROLE_MEMBER
) -> AppUser:
    """メンバーを足す。既にいれば役割だけを変える(代表者にするのは管理者だけ)。"""
    if role not in TEAM_ROLES:
        raise ValueError(role)
    if not can_manage_members(db, viewer, team):
        raise TeamPermissionError
    if role == TEAM_ROLE_LEAD and not viewer.is_admin:
        raise TeamPermissionError
    user = find_user_by_email(db, email)
    if user is None:
        raise TeamUserNotFoundError(email)
    existing = db.get(TeamMember, (team.id, user.id))
    if existing is not None:
        if existing.role != role:
            if existing.role == TEAM_ROLE_LEAD and not viewer.is_admin:
                raise TeamPermissionError
            existing.role = role
            db.commit()
        return user
    db.add(
        TeamMember(
            team_id=team.id,
            user_id=user.id,
            role=role,
            added_by_user_id=viewer.id,
            added_at=_utcnow(),
        )
    )
    db.commit()
    return user


def set_role(
    db: Session, viewer: CurrentUser, team: Team, user_id: uuid.UUID, role: str
) -> TeamMember:
    """役割を変える(管理者だけ)。"""
    if role not in TEAM_ROLES:
        raise ValueError(role)
    if not viewer.is_admin:
        raise TeamPermissionError
    member = db.get(TeamMember, (team.id, user_id))
    if member is None:
        raise TeamMemberNotFoundError
    member.role = role
    db.commit()
    return member


def remove_member(db: Session, viewer: CurrentUser, team: Team, user_id: uuid.UUID) -> str:
    """メンバーを外す。代表者が外せるのは代表者でない人だけ。外した人の役割を返す。"""
    member = db.get(TeamMember, (team.id, user_id))
    if member is None:
        raise TeamMemberNotFoundError
    if not viewer.is_admin:
        if member_role(db, team.id, viewer.id) != TEAM_ROLE_LEAD or member.role == TEAM_ROLE_LEAD:
            raise TeamPermissionError
    role = member.role
    db.execute(
        delete(TeamMember).where(TeamMember.team_id == team.id, TeamMember.user_id == user_id)
    )
    db.commit()
    return role
