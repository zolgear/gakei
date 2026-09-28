"""upload_ticket(MCP の1回限りのアップロード URL。ADR-0023 7章 2)

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-28

URL のトークンは保存せず SHA-256 のハッシュだけを持つ。有効期限は発行から10分で、使ったら
`used_at` と取り込んだ `asset_id` を書く。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "upload_ticket",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("api_token_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("asset_id", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["app_user.id"], name="fk_upload_ticket_user_id_app_user"
        ),
        sa.ForeignKeyConstraint(
            ["api_token_id"], ["api_token.id"], name="fk_upload_ticket_api_token_id_api_token"
        ),
        sa.ForeignKeyConstraint(["asset_id"], ["asset.id"], name="fk_upload_ticket_asset_id_asset"),
    )
    op.create_index("ix_upload_ticket_expires_at", "upload_ticket", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_upload_ticket_expires_at", table_name="upload_ticket")
    op.drop_table("upload_ticket")
