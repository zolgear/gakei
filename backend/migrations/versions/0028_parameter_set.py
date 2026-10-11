"""parameter_set(生成の設定一式を名前を付けて保存する。ADR-0040)

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-10

証跡ではないので、名前と中身を更新でき、削除は論理削除(`deleted_at`)。Run とは外部キーを
張らない。作成者(ADR-0025)は作成時に一度だけ書く。`params` は PostgreSQL では他の JSON 列と
同じく JSONB にする(ADR-0027 2章)。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "parameter_set",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=True),
        sa.Column(
            "params",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["app_user.id"],
            name="fk_parameter_set_created_by_user_id_app_user",
        ),
    )
    op.create_index("ix_parameter_set_created_by_user_id", "parameter_set", ["created_by_user_id"])
    op.create_index("ix_parameter_set_updated_at", "parameter_set", ["updated_at"])


def downgrade() -> None:
    op.drop_index("ix_parameter_set_updated_at", table_name="parameter_set")
    op.drop_index("ix_parameter_set_created_by_user_id", table_name="parameter_set")
    op.drop_table("parameter_set")
