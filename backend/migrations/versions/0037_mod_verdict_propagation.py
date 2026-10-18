"""Mod の判定の波及(ADR-0051 4章)

Revision ID: 0037
Revises: 0036
Create Date: 2026-10-16

- `mod_verdict.propagated_from_id`: `deny` の判定を、同じ内容(sha256)のほかの Asset に及ぼした
  ときに足す判定の行が、元のどの判定から来たかを指す。元の判定の行は null。外部キーは張らない
  (0036 と同じ理由)。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("mod_verdict") as batch_op:
        batch_op.add_column(sa.Column("propagated_from_id", sa.Uuid(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("mod_verdict") as batch_op:
        batch_op.drop_column("propagated_from_id")
