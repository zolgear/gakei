"""asset.origin_asset_id / asset.origin_meta (再アップロード時の由来。ADR-0014、2026-09-24 追記)

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-24

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQLite は既存テーブルへの FK 追加に ALTER TABLE が使えない(テーブル再作成が要る)ため、
    # batch モードでまとめて行う。PostgreSQL でも同じコードのまま通常の ALTER として動く。
    with op.batch_alter_table("asset") as batch_op:
        batch_op.add_column(sa.Column("origin_asset_id", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("origin_meta", sa.JSON(), nullable=True))
        batch_op.create_index("ix_asset_origin_asset_id", ["origin_asset_id"])
        batch_op.create_foreign_key(
            "fk_asset_origin_asset_id_asset",
            "asset",
            ["origin_asset_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("asset") as batch_op:
        batch_op.drop_constraint("fk_asset_origin_asset_id_asset", type_="foreignkey")
        batch_op.drop_index("ix_asset_origin_asset_id")
        batch_op.drop_column("origin_meta")
        batch_op.drop_column("origin_asset_id")
