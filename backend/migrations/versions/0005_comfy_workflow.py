"""comfy_workflow (ADR-0013、ローカル ComfyUI プロバイダーの実験)

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-23

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "comfy_workflow",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("operation", sa.String(length=16), nullable=False),
        sa.Column("template", sa.JSON(), nullable=False),
        sa.Column("bindings", sa.JSON(), nullable=False),
        sa.Column("exposed_params", sa.JSON(), nullable=False),
        sa.Column("template_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_comfy_workflow_updated_at", "comfy_workflow", ["updated_at"])


def downgrade() -> None:
    op.drop_table("comfy_workflow")
