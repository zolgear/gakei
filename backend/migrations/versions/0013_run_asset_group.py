"""run.asset_group_id(生成時のグループ指定。ADR-0022)

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-27

Run の作成時に一度だけ書く列(ADR-0003 の追記のみの規則に反しない)。Run が成功して
出力の Asset を取り込んだ直後に、worker がその Asset をこのグループに入れる。
既存行は常に null のまま。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQLite は既存テーブルへの FK 追加に ALTER TABLE が使えないため、batch モードで行う
    # (0010 と同じ書き方)。PostgreSQL でも同じコードのまま通常の ALTER として動く。
    with op.batch_alter_table("run") as batch_op:
        batch_op.add_column(sa.Column("asset_group_id", sa.Uuid(), nullable=True))
        batch_op.create_index("ix_run_asset_group_id", ["asset_group_id"])
        batch_op.create_foreign_key(
            "fk_run_asset_group_id_asset_group",
            "asset_group",
            ["asset_group_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("run") as batch_op:
        batch_op.drop_constraint("fk_run_asset_group_id_asset_group", type_="foreignkey")
        batch_op.drop_index("ix_run_asset_group_id")
        batch_op.drop_column("asset_group_id")
