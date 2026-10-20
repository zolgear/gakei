"""asset_region_detection(VLM での四角の検出の履歴。ADR-0053 7章)

Revision ID: 0040
Revises: 0039
Create Date: 2026-10-20

- `asset_region_detection`: 1回の検出を1行にする(探した言葉、接続先とモデル、上限の数、候補の
  四角、日時、検出した利用者)。証跡ではないので消してよい。1つの Asset に新しい方から 20 回まで
  残す(古いものはアプリが消す)。
- 新規テーブルの作成のみで、既存テーブルへの変更は無い。

downgrade はテーブルを消す。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0040"
down_revision: str | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "asset_region_detection",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("asset_id", sa.Uuid(), sa.ForeignKey("asset.id"), nullable=False),
        sa.Column("query", sa.Text(), nullable=True),
        sa.Column("connection_id", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("max_items", sa.Integer(), nullable=False),
        sa.Column("regions", _JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), sa.ForeignKey("app_user.id"), nullable=True),
    )
    op.create_index(
        "ix_asset_region_detection_asset_id_created_at",
        "asset_region_detection",
        ["asset_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_asset_region_detection_asset_id_created_at", table_name="asset_region_detection"
    )
    op.drop_table("asset_region_detection")
