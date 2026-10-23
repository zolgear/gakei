"""asset_mark(画像ごと・利用者ごとの印。今はお気に入りだけ。ADR-0055)

Revision ID: 0041
Revises: 0040
Create Date: 2026-10-23

- `asset_mark`: ADR-0003 の設計の最初の実装。`flag` は今は 'pick'(お気に入り)だけ。証跡では
  ないので、外したら行を消す。`user_id` は個人モードで NULL。
- 1つの Asset に1人の行は1つだけ。NULL どうしは一意制約で重複とみなされないので、個人モードの
  行は部分一意索引(`WHERE user_id IS NULL`)で1つに限る(SQLite も PostgreSQL も部分索引を持つ)。
- 新規テーブルの作成のみで、既存テーブルへの変更は無い。

downgrade はテーブルを消す。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0041"
down_revision: str | None = "0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "asset_mark",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("asset_id", sa.Uuid(), sa.ForeignKey("asset.id"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("app_user.id"), nullable=True),
        sa.Column("flag", sa.String(16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ux_asset_mark_asset_id_user_id", "asset_mark", ["asset_id", "user_id"], unique=True
    )
    op.create_index(
        "ux_asset_mark_asset_id_local",
        "asset_mark",
        ["asset_id"],
        unique=True,
        sqlite_where=sa.text("user_id IS NULL"),
        postgresql_where=sa.text("user_id IS NULL"),
    )
    op.create_index("ix_asset_mark_user_id_flag", "asset_mark", ["user_id", "flag"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_asset_mark_user_id_flag", table_name="asset_mark")
    op.drop_index("ux_asset_mark_asset_id_local", table_name="asset_mark")
    op.drop_index("ux_asset_mark_asset_id_user_id", table_name="asset_mark")
    op.drop_table("asset_mark")
