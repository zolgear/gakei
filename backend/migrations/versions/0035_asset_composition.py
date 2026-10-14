"""asset_composition と asset_composition_source(入力画像の配置。ADR-0050)

Revision ID: 0035
Revises: 0034
Create Date: 2026-10-15

- `asset_composition`: 配置の結果(`asset.kind = 'composite'`)ごとのレシピ(JSON)。
- `asset_composition_source`: 配置の素材。系列をたどるための索引で、`position` は素材を
  足した順(0 が主たる素材)。
- どちらも追記のみ(ADR-0003 1項)。`asset.kind` は文字列の列なので、種類の追加に
  既存テーブルの変更は要らない。

downgrade はテーブルを消す。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "asset_composition",
        sa.Column("asset_id", sa.Uuid(), sa.ForeignKey("asset.id"), primary_key=True),
        sa.Column("recipe", _JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "asset_composition_source",
        sa.Column("asset_id", sa.Uuid(), sa.ForeignKey("asset.id"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("source_asset_id", sa.Uuid(), sa.ForeignKey("asset.id"), nullable=False),
        sa.PrimaryKeyConstraint("asset_id", "position"),
    )
    op.create_index(
        "ix_asset_composition_source_source_asset_id",
        "asset_composition_source",
        ["source_asset_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_asset_composition_source_source_asset_id", table_name="asset_composition_source"
    )
    op.drop_table("asset_composition_source")
    op.drop_table("asset_composition")
