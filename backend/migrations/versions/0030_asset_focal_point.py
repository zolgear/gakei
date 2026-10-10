"""asset_focal_point(サムネイルの焦点。ADR-0043)

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-10

- `asset_focal_point`: Asset ごとのサムネイルの焦点(顔の位置)。`x`・`y` は画像の幅・高さに
  対する 0〜1 の位置で、顔が見つからなかったときは null(`method` は `none`)。`version` は
  検出の手順の版(古ければ作り直す)。
- 新規テーブルの作成のみで、既存テーブルへの変更は無い。証跡ではない(ADR-0003 の来歴の列には
  書かない)。

downgrade はテーブルを消す。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "asset_focal_point",
        sa.Column("asset_id", sa.Uuid(), sa.ForeignKey("asset.id"), primary_key=True),
        sa.Column("x", sa.Float(), nullable=True),
        sa.Column("y", sa.Float(), nullable=True),
        sa.Column("method", sa.String(32), nullable=False),
        sa.Column("version", sa.SmallInteger(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("asset_focal_point")
