"""asset_perceptual_hash(画像の知覚ハッシュ。ADR-0033 12章)

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-03

- `asset_perceptual_hash`: Asset ごとの知覚ハッシュ(重複の候補の判定に使う)。
  `dhash` は 64 ビットの差分ハッシュ(ビッグエンディアンの 8 バイト。PostgreSQL の BIGINT は
  符号付きなので、SQLite と同じ扱いにできるバイト列にする)、`color` は色の値(64 バイト)、
  `version` はアルゴリズムの版(古ければ作り直す)。
- 新規テーブルの作成のみで、既存テーブルへの変更は無い。証跡ではない。

downgrade はテーブルを消す。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "asset_perceptual_hash",
        sa.Column("asset_id", sa.Uuid(), sa.ForeignKey("asset.id"), primary_key=True),
        sa.Column("dhash", sa.LargeBinary(), nullable=False),
        sa.Column("color", sa.LargeBinary(), nullable=False),
        sa.Column("version", sa.SmallInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("asset_perceptual_hash")
