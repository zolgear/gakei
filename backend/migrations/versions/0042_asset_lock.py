"""asset_lock、asset_lock_scope(ロック。ADR-0060)

Revision ID: 0042
Revises: 0041
Create Date: 2026-11-01

- `asset_lock`: 納品・共有・提出した画像に付ける印。外すときは行を消さず、外した人と時刻を書く。
  1つの Asset に、外していないロックは1つまで(部分一意索引 `WHERE unlocked_at IS NULL`)。
- `asset_lock_scope`: ロックが守る Asset(ロックした画像と祖先)。付けたときに書く。
- 新規テーブルの作成のみで、既存テーブルへの変更は無い。

downgrade はテーブルを消す。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0042"
down_revision: str | None = "0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "asset_lock",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("asset_id", sa.Uuid(), sa.ForeignKey("asset.id"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("locked_by_user_id", sa.Uuid(), sa.ForeignKey("app_user.id"), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("unlocked_by_user_id", sa.Uuid(), sa.ForeignKey("app_user.id"), nullable=True),
        sa.Column("unlocked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ux_asset_lock_asset_id_active",
        "asset_lock",
        ["asset_id"],
        unique=True,
        sqlite_where=sa.text("unlocked_at IS NULL"),
        postgresql_where=sa.text("unlocked_at IS NULL"),
    )
    op.create_table(
        "asset_lock_scope",
        sa.Column("lock_id", sa.Uuid(), sa.ForeignKey("asset_lock.id"), primary_key=True),
        sa.Column("asset_id", sa.Uuid(), sa.ForeignKey("asset.id"), primary_key=True),
    )
    op.create_index("ix_asset_lock_scope_asset_id", "asset_lock_scope", ["asset_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_asset_lock_scope_asset_id", table_name="asset_lock_scope")
    op.drop_table("asset_lock_scope")
    op.drop_index("ux_asset_lock_asset_id_active", table_name="asset_lock")
    op.drop_table("asset_lock")
