"""asset.source_asset_id (上描きスケッチの下地 Asset。ADR-0010、2026-09-23 追記)

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-23

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQLite は既存テーブルへの FK 追加に ALTER TABLE が使えない(テーブル再作成が要る)ため、
    # batch モードでまとめて行う。PostgreSQL でも同じコードのまま通常の ALTER として動く。
    with op.batch_alter_table("asset") as batch_op:
        batch_op.add_column(sa.Column("source_asset_id", sa.Uuid(), nullable=True))
        batch_op.create_index("ix_asset_source_asset_id", ["source_asset_id"])
        batch_op.create_foreign_key(
            "fk_asset_source_asset_id_asset",
            "asset",
            ["source_asset_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("asset") as batch_op:
        batch_op.drop_constraint("fk_asset_source_asset_id_asset", type_="foreignkey")
        batch_op.drop_index("ix_asset_source_asset_id")
        batch_op.drop_column("source_asset_id")
