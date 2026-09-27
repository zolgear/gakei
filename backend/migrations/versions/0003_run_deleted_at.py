"""run.deleted_at (論理削除。ADR-0008「削除」追加分)

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-22

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("run", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_run_deleted_at", "run", ["deleted_at"])


def downgrade() -> None:
    op.drop_index("ix_run_deleted_at", table_name="run")
    op.drop_column("run", "deleted_at")
