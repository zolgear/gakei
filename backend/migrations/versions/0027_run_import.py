"""run_import(系列の ZIP から取り込んだ Run の、書き出し元での記録。ADR-0037)

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-07

取り込んだ Run は `run.origin = 'import'` の新しい行で、元の Run の ID・実行者の表示名・
作成と終了の日時・書き出した GAKEI の版をこの表に置く。追記のみ(ADR-0003)。
`run.origin` は String(16) のままで、値 `import` が加わるだけなので列の変更は無い。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_import",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("source_run_id", sa.Uuid(), nullable=False),
        sa.Column("source_creator_name", sa.Text(), nullable=True),
        sa.Column("source_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_gakei_version", sa.Text(), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("run_id"),
        sa.ForeignKeyConstraint(["run_id"], ["run.id"], name="fk_run_import_run_id_run"),
    )
    op.create_index("ix_run_import_source_run_id", "run_import", ["source_run_id"])


def downgrade() -> None:
    op.drop_index("ix_run_import_source_run_id", table_name="run_import")
    op.drop_table("run_import")
