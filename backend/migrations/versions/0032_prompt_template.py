"""prompt_template(プロンプトテンプレート(実験))と run.prompt_build

Revision ID: 0032
Revises: 0031
Create Date: 2026-10-11

- `prompt_template` / `prompt_slot` / `prompt_variant` / `prompt_combo` / `prompt_combo_item`:
  プロンプトを「スロット(枠)」に分けた組み立ての雛形。証跡ではないので更新でき、削除は論理削除
  (`prompt_combo_item` は組み合わせの置換時に入れ替えるので、論理削除の列を持たない)。
  `run` とは外部キーを張らない。
- `run.prompt_build`: 組み立ての内訳(選ばれた候補のテキストそのもの)。nullable で、Run の
  作成時に一度だけ書く。既存の行は NULL のまま(ADR-0003 の追記のみの規則に反しない)。
  他の JSON 列と同じく PostgreSQL では JSONB(ADR-0027 2章)。

downgrade は列とテーブルを消す。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "prompt_template",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("separator", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["app_user.id"],
            name="fk_prompt_template_created_by_user_id_app_user",
        ),
    )
    op.create_index(
        "ix_prompt_template_created_by_user_id", "prompt_template", ["created_by_user_id"]
    )
    op.create_index("ix_prompt_template_updated_at", "prompt_template", ["updated_at"])

    op.create_table(
        "prompt_slot",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("template_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("default_mode", sa.String(8), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["prompt_template.id"],
            name="fk_prompt_slot_template_id_prompt_template",
        ),
    )
    op.create_index("ix_prompt_slot_template_id", "prompt_slot", ["template_id"])

    op.create_table(
        "prompt_variant",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("slot_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["slot_id"], ["prompt_slot.id"], name="fk_prompt_variant_slot_id_prompt_slot"
        ),
    )
    op.create_index("ix_prompt_variant_slot_id", "prompt_variant", ["slot_id"])

    op.create_table(
        "prompt_combo",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("template_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["prompt_template.id"],
            name="fk_prompt_combo_template_id_prompt_template",
        ),
    )
    op.create_index("ix_prompt_combo_template_id", "prompt_combo", ["template_id"])

    op.create_table(
        "prompt_combo_item",
        sa.Column("combo_id", sa.Uuid(), nullable=False),
        sa.Column("variant_id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("combo_id", "variant_id"),
        sa.ForeignKeyConstraint(
            ["combo_id"], ["prompt_combo.id"], name="fk_prompt_combo_item_combo_id_prompt_combo"
        ),
        sa.ForeignKeyConstraint(
            ["variant_id"],
            ["prompt_variant.id"],
            name="fk_prompt_combo_item_variant_id_prompt_variant",
        ),
    )
    op.create_index("ix_prompt_combo_item_variant_id", "prompt_combo_item", ["variant_id"])

    op.add_column("run", sa.Column("prompt_build", _JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("run", "prompt_build")
    op.drop_index("ix_prompt_combo_item_variant_id", table_name="prompt_combo_item")
    op.drop_table("prompt_combo_item")
    op.drop_index("ix_prompt_combo_template_id", table_name="prompt_combo")
    op.drop_table("prompt_combo")
    op.drop_index("ix_prompt_variant_slot_id", table_name="prompt_variant")
    op.drop_table("prompt_variant")
    op.drop_index("ix_prompt_slot_template_id", table_name="prompt_slot")
    op.drop_table("prompt_slot")
    op.drop_index("ix_prompt_template_updated_at", table_name="prompt_template")
    op.drop_index("ix_prompt_template_created_by_user_id", table_name="prompt_template")
    op.drop_table("prompt_template")
