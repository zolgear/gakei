"""asset_group, asset_group_member (ADR-0022)

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-27

グループ(ストックの手動整理)。証跡ではないので更新・論理削除ができる。新規テーブルの
作成のみで、既存テーブルへの変更は無い(`batch_alter_table` を使わない)。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "asset_group",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), sa.ForeignKey("app_user.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_asset_group_created_by_user_id", "asset_group", ["created_by_user_id"])

    op.create_table(
        "asset_group_member",
        sa.Column(
            "asset_group_id",
            sa.Uuid(),
            sa.ForeignKey("asset_group.id"),
            primary_key=True,
        ),
        sa.Column("asset_id", sa.Uuid(), sa.ForeignKey("asset.id"), primary_key=True),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_asset_group_member_asset_id", "asset_group_member", ["asset_id"])


def downgrade() -> None:
    op.drop_table("asset_group_member")
    op.drop_table("asset_group")
