"""asset_content_rating と user_preference(画像のレーティングとユーザーごとの設定。ADR-0049)

Revision ID: 0034
Revises: 0033
Create Date: 2026-10-14

- `asset_content_rating`: Asset ごとのレーティング(性表現の強さの段階)。`status` が判定の
  待ち行列を兼ねる。証跡ではないので更新してよい(ADR-0049 2章)。
- `user_preference`: ユーザーごとの設定。主キーは (`owner`, `key`)。個人モードには `app_user`
  の行が無いので、`owner` は外部キーにせず文字列で持つ(ADR-0049 6章)。
- 新規テーブルの作成のみで、既存テーブルへの変更は無い。

downgrade はテーブルを消す。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "asset_content_rating",
        sa.Column("asset_id", sa.Uuid(), sa.ForeignKey("asset.id"), primary_key=True),
        sa.Column("level", sa.String(8), nullable=True),
        sa.Column("source", sa.String(8), nullable=True),
        sa.Column("auto_level", sa.String(8), nullable=True),
        sa.Column("status", sa.String(16), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("rules_version", sa.SmallInteger(), nullable=True),
        sa.Column("scores", _JSON, nullable=True),
        sa.Column("signals", _JSON, nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("judged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_asset_content_rating_status", "asset_content_rating", ["status"], unique=False
    )

    op.create_table(
        "user_preference",
        sa.Column("owner", sa.String(64), nullable=False),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("value", _JSON, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("owner", "key"),
    )


def downgrade() -> None:
    op.drop_table("user_preference")
    op.drop_index("ix_asset_content_rating_status", table_name="asset_content_rating")
    op.drop_table("asset_content_rating")
