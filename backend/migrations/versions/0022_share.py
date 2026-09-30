"""share / share_asset(ログイン不要の共有リンク。ADR-0029)

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-30

`share` は共有リンク1件(トークンは平文。取り消しは `revoked_at` の論理削除)。
`share_asset` は共有に含まれる Asset の一覧で、作成時に固定する。Run は持たず、含まれる
Asset の `produced_by_run_id` から引く。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "share",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("token", sa.String(64), nullable=False),
        sa.Column("root_asset_id", sa.Uuid(), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("allow_original", sa.Boolean(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("access_count", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token"),
        sa.ForeignKeyConstraint(
            ["root_asset_id"], ["asset.id"], name="fk_share_root_asset_id_asset"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["app_user.id"], name="fk_share_created_by_user_id_app_user"
        ),
    )
    op.create_index("ix_share_root_asset_id", "share", ["root_asset_id"])
    op.create_index("ix_share_created_by_user_id", "share", ["created_by_user_id"])

    op.create_table(
        "share_asset",
        sa.Column("share_id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("share_id", "asset_id"),
        sa.ForeignKeyConstraint(["share_id"], ["share.id"], name="fk_share_asset_share_id_share"),
        sa.ForeignKeyConstraint(["asset_id"], ["asset.id"], name="fk_share_asset_asset_id_asset"),
    )
    op.create_index("ix_share_asset_asset_id", "share_asset", ["asset_id"])


def downgrade() -> None:
    op.drop_index("ix_share_asset_asset_id", table_name="share_asset")
    op.drop_table("share_asset")
    op.drop_index("ix_share_created_by_user_id", table_name="share")
    op.drop_index("ix_share_root_asset_id", table_name="share")
    op.drop_table("share")
