"""initial schema: asset, run, run_input

Revision ID: 0001
Revises:
Create Date: 2026-09-21

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("deployment", sa.String(length=64), nullable=True),
        sa.Column("operation", sa.String(length=16), nullable=False),
        sa.Column("prompt", sa.String(length=32000), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error_code", sa.String(length=32), nullable=True),
        sa.Column("error_message", sa.String(length=2000), nullable=True),
        sa.Column("usage", sa.JSON(), nullable=True),
        sa.Column("provider_request_id", sa.String(length=128), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_run_status", "run", ["status"])
    op.create_index("ix_run_queued_at", "run", ["queued_at"])

    op.create_table(
        "asset",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("blob_key", sa.String(length=255), nullable=False),
        sa.Column("mime", sa.String(length=64), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("bytes", sa.Integer(), nullable=False),
        sa.Column("produced_by_run_id", sa.Uuid(), sa.ForeignKey("run.id"), nullable=True),
        sa.Column("output_index", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_asset_sha256", "asset", ["sha256"])
    op.create_index("ix_asset_produced_by_run_id", "asset", ["produced_by_run_id"])

    op.create_table(
        "run_input",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("run.id"), nullable=False),
        sa.Column("asset_id", sa.Uuid(), sa.ForeignKey("asset.id"), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_run_input_run_id", "run_input", ["run_id"])
    op.create_index("ix_run_input_asset_id", "run_input", ["asset_id"])
    op.create_index("ix_run_input_run_role_position", "run_input", ["run_id", "role", "position"])


def downgrade() -> None:
    op.drop_table("run_input")
    op.drop_table("asset")
    op.drop_table("run")
