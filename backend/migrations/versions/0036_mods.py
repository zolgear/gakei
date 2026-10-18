"""Mod とフック(ADR-0051)

Revision ID: 0036
Revises: 0035
Create Date: 2026-10-16

- `mod_invocation`: Mod の呼び出しの記録。追記のみ(ADR-0003 1項)。
- `mod_verdict`: Mod が返した判定。追記のみ。
- `mod_delivery`: 非同期のイベントの配達の待ち行列。状態の行(証跡ではない)。
- `asset.content_withheld_at` / `asset.content_withheld_verdict_id`: `deny` の判定で画像を
  残さなかったことを表す状態の列(ADR-0051 4章)。
- `comfy_workflow.locality`: ワークフローの送り先の区分(ADR-0051 3章。既定 `unset`)。

Run・Asset・判定への外部キーは張らない(メンテナンスのツールが Run や Asset を消しても記録を
残すため)。downgrade は列とテーブルを消す。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "mod_invocation",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event", sa.String(32), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("mod_kind", sa.String(16), nullable=False),
        sa.Column("mod_id", sa.String(100), nullable=False),
        sa.Column("mod_name", sa.Text(), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=True),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("asset_id", sa.Uuid(), nullable=True),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("effective", sa.String(8), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("reference", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_mod_invocation_event_id", "mod_invocation", ["event_id"])
    op.create_index("ix_mod_invocation_run_id", "mod_invocation", ["run_id"])
    op.create_index("ix_mod_invocation_asset_id", "mod_invocation", ["asset_id"])
    op.create_index("ix_mod_invocation_created_at", "mod_invocation", ["created_at"])
    op.create_index("ix_mod_invocation_mod", "mod_invocation", ["mod_kind", "mod_id"])

    op.create_table(
        "mod_verdict",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("mod_kind", sa.String(16), nullable=False),
        sa.Column("mod_id", sa.String(100), nullable=False),
        sa.Column("mod_name", sa.Text(), nullable=False),
        sa.Column("invocation_id", sa.Uuid(), nullable=True),
        sa.Column("event_id", sa.Uuid(), nullable=True),
        sa.Column("asset_id", sa.Uuid(), nullable=True),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("verdict", sa.String(8), nullable=False),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("reference", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_mod_verdict_asset_id", "mod_verdict", ["asset_id"])
    op.create_index("ix_mod_verdict_run_id", "mod_verdict", ["run_id"])

    op.create_table(
        "mod_delivery",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event", sa.String(32), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("mod_kind", sa.String(16), nullable=False),
        sa.Column("mod_id", sa.String(100), nullable=False),
        sa.Column("payload", _JSON, nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=True),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_mod_delivery_status_next", "mod_delivery", ["status", "next_attempt_at"])

    with op.batch_alter_table("asset") as batch_op:
        batch_op.add_column(
            sa.Column("content_withheld_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(sa.Column("content_withheld_verdict_id", sa.Uuid(), nullable=True))

    with op.batch_alter_table("comfy_workflow") as batch_op:
        batch_op.add_column(
            sa.Column("locality", sa.String(8), nullable=False, server_default="unset")
        )


def downgrade() -> None:
    with op.batch_alter_table("comfy_workflow") as batch_op:
        batch_op.drop_column("locality")
    with op.batch_alter_table("asset") as batch_op:
        batch_op.drop_column("content_withheld_verdict_id")
        batch_op.drop_column("content_withheld_at")
    op.drop_index("ix_mod_delivery_status_next", table_name="mod_delivery")
    op.drop_table("mod_delivery")
    op.drop_index("ix_mod_verdict_run_id", table_name="mod_verdict")
    op.drop_index("ix_mod_verdict_asset_id", table_name="mod_verdict")
    op.drop_table("mod_verdict")
    op.drop_index("ix_mod_invocation_mod", table_name="mod_invocation")
    op.drop_index("ix_mod_invocation_created_at", table_name="mod_invocation")
    op.drop_index("ix_mod_invocation_asset_id", table_name="mod_invocation")
    op.drop_index("ix_mod_invocation_run_id", table_name="mod_invocation")
    op.drop_index("ix_mod_invocation_event_id", table_name="mod_invocation")
    op.drop_table("mod_invocation")
