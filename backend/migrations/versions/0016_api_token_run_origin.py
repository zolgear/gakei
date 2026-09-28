"""api_token と run.origin / run.api_token_id(MCP サーバー。ADR-0023)

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-28

- `api_token`: MCP 用のアクセストークン(認証モードのみ)。値は保存せず SHA-256 だけを持つ。
- `run.origin`: 実行元。null は画面、`mcp` は MCP。
- `run.api_token_id`: MCP をアクセストークンで呼んだときのトークン。

run の2列は作成時に一度だけ書く来歴の列(ADR-0003 の追記のみの規則に反しない)。既存行は
null(= 画面)のまま。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "api_token",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"], name="fk_api_token_user_id_app_user"),
    )
    op.create_index("ix_api_token_user_id", "api_token", ["user_id"])

    # SQLite は既存テーブルへの FK 追加に ALTER TABLE が使えないため、batch モードで行う
    # (0010、0013 と同じ書き方)。PostgreSQL でも同じコードのまま通常の ALTER として動く。
    with op.batch_alter_table("run") as batch_op:
        batch_op.add_column(sa.Column("origin", sa.String(16), nullable=True))
        batch_op.add_column(sa.Column("api_token_id", sa.Uuid(), nullable=True))
        batch_op.create_index("ix_run_api_token_id", ["api_token_id"])
        batch_op.create_foreign_key(
            "fk_run_api_token_id_api_token",
            "api_token",
            ["api_token_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("run") as batch_op:
        batch_op.drop_constraint("fk_run_api_token_id_api_token", type_="foreignkey")
        batch_op.drop_index("ix_run_api_token_id")
        batch_op.drop_column("api_token_id")
        batch_op.drop_column("origin")

    op.drop_index("ix_api_token_user_id", table_name="api_token")
    op.drop_table("api_token")
