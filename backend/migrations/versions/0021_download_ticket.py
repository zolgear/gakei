"""download_ticket(MCP の原本の1回限りのダウンロード URL。ADR-0023 8章 3)

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-29

アップロード URL(0017 の `upload_ticket`)と同じ作り。URL のトークンは保存せず SHA-256 の
ハッシュだけを持つ。有効期限は発行から10分で、取得したら `used_at` を書く。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "download_ticket",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("api_token_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
        sa.ForeignKeyConstraint(
            ["asset_id"], ["asset.id"], name="fk_download_ticket_asset_id_asset"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["app_user.id"], name="fk_download_ticket_user_id_app_user"
        ),
        sa.ForeignKeyConstraint(
            ["api_token_id"],
            ["api_token.id"],
            name="fk_download_ticket_api_token_id_api_token",
        ),
    )
    op.create_index("ix_download_ticket_expires_at", "download_ticket", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_download_ticket_expires_at", table_name="download_ticket")
    op.drop_table("download_ticket")
