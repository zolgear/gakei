"""prompt_set, prompt_set_item (ADR-0009)

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-21

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "prompt_set",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_prompt_set_updated_at", "prompt_set", ["updated_at"])

    op.create_table(
        "prompt_set_item",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("prompt_set_id", sa.Uuid(), sa.ForeignKey("prompt_set.id"), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=True),
        sa.Column("text", sa.String(length=32000), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_prompt_set_item_prompt_set_id", "prompt_set_item", ["prompt_set_id"])


def downgrade() -> None:
    op.drop_table("prompt_set_item")
    op.drop_table("prompt_set")
