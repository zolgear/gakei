"""asset_annotation, tag, asset_tag(タイトルとタグ。ADR-0024)

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-28

- `asset_annotation`: Asset のタイトルと自動推定の状態(待ち行列を兼ねる)。
- `tag`: 正規化したタグの表記。
- `asset_tag`: Asset とタグの対応。人が消したものは `removed = true` で残す。

いずれも証跡ではないので更新してよい(ADR-0003、ADR-0024 1章)。新規テーブルの作成のみで、
既存テーブルへの変更は無い(`batch_alter_table` を使わない)。既存の Asset はタイトルも
タグも無い状態から始まる。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "asset_annotation",
        sa.Column("asset_id", sa.Uuid(), sa.ForeignKey("asset.id"), primary_key=True),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("title_source", sa.String(8), nullable=True),
        sa.Column("auto_status", sa.String(16), nullable=True),
        sa.Column("auto_engines", sa.String(32), nullable=True),
        sa.Column("auto_models", sa.JSON(), nullable=True),
        sa.Column("auto_error", sa.String(500), nullable=True),
        sa.Column("auto_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("auto_finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_asset_annotation_auto_status", "asset_annotation", ["auto_status"])

    op.create_table(
        "tag",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name", name="uq_tag_name"),
    )

    op.create_table(
        "asset_tag",
        sa.Column("asset_id", sa.Uuid(), sa.ForeignKey("asset.id"), primary_key=True),
        sa.Column("tag_id", sa.Uuid(), sa.ForeignKey("tag.id"), primary_key=True),
        sa.Column("source", sa.String(8), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("removed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_asset_tag_tag_id", "asset_tag", ["tag_id"])


def downgrade() -> None:
    op.drop_index("ix_asset_tag_tag_id", table_name="asset_tag")
    op.drop_table("asset_tag")
    op.drop_table("tag")
    op.drop_index("ix_asset_annotation_auto_status", table_name="asset_annotation")
    op.drop_table("asset_annotation")
