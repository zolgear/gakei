"""run.text_outputs(ComfyUI の最終プロンプトなど、実行時に作られたテキスト。ADR-0030)

Revision ID: 0023
Revises: 0022
Create Date: 2026-09-30

null 可の JSON 列を足すだけ。既存の Run は null のまま。PostgreSQL では他の JSON 列と
同じく JSONB にする(ADR-0027 2章)。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("run") as batch_op:
        batch_op.add_column(
            sa.Column(
                "text_outputs",
                sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
                nullable=True,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("run") as batch_op:
        batch_op.drop_column("text_outputs")
