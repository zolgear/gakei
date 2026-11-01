"""team、team_member(チーム。ADR-0058)

Revision ID: 0043
Revises: 0042
Create Date: 2026-11-01

- `team`: 利用者の集まり(階層なし)。削除は論理削除。
- `team_member`: メンバーと役割(`member` / `lead`)。主キーは (team_id, user_id)。外したら行を消す。
- 新規テーブルの作成のみで、既存テーブルへの変更は無い。

downgrade はテーブルを消す。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0043"
down_revision: str | None = "0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "team",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), sa.ForeignKey("app_user.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "team_member",
        sa.Column("team_id", sa.Uuid(), sa.ForeignKey("team.id"), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("app_user.id"), primary_key=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("added_by_user_id", sa.Uuid(), sa.ForeignKey("app_user.id"), nullable=True),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_team_member_user_id", "team_member", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_team_member_user_id", table_name="team_member")
    op.drop_table("team_member")
    op.drop_table("team")
