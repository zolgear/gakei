"""tag_dictionary とその中身(タグの一覧・別名・訳。ADR-0041)

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-10

辞書は証跡ではないので、有効/無効を変えられ、削除は物理削除。中身の行は辞書の外部キーに
ON DELETE CASCADE を付ける(アプリは明示的にも消す)。検索用の列(`*_key`)は前方一致を主キーの
索引の範囲検索で引くので、PostgreSQL では照合順序を "C" にする(ADR-0027)。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _key_text() -> sa.types.TypeEngine:
    return sa.Text().with_variant(sa.Text(collation="C"), "postgresql")


def upgrade() -> None:
    op.create_table(
        "tag_dictionary",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("category_scheme", sa.String(32), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["app_user.id"],
            name="fk_tag_dictionary_created_by_user_id_app_user",
        ),
    )
    op.create_table(
        "tag_dictionary_entry",
        sa.Column("dictionary_id", sa.Uuid(), nullable=False),
        sa.Column("name_key", _key_text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("category", sa.Integer(), nullable=True),
        sa.Column("post_count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("dictionary_id", "name_key"),
        sa.ForeignKeyConstraint(
            ["dictionary_id"],
            ["tag_dictionary.id"],
            name="fk_tag_dictionary_entry_dictionary_id_tag_dictionary",
            ondelete="CASCADE",
        ),
    )
    op.create_table(
        "tag_dictionary_alias",
        sa.Column("dictionary_id", sa.Uuid(), nullable=False),
        sa.Column("alias_key", _key_text(), nullable=False),
        sa.Column("name_key", _key_text(), nullable=False),
        sa.Column("alias", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("dictionary_id", "alias_key", "name_key"),
        sa.ForeignKeyConstraint(
            ["dictionary_id"],
            ["tag_dictionary.id"],
            name="fk_tag_dictionary_alias_dictionary_id_tag_dictionary",
            ondelete="CASCADE",
        ),
    )
    op.create_table(
        "tag_dictionary_translation",
        sa.Column("dictionary_id", sa.Uuid(), nullable=False),
        sa.Column("name_key", _key_text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("translation", sa.Text(), nullable=False),
        sa.Column("translations", sa.Text(), nullable=False),
        sa.Column("search_key", _key_text(), nullable=False),
        sa.PrimaryKeyConstraint("dictionary_id", "name_key"),
        sa.ForeignKeyConstraint(
            ["dictionary_id"],
            ["tag_dictionary.id"],
            name="fk_tag_dictionary_translation_dictionary_id_tag_dictionary",
            ondelete="CASCADE",
        ),
    )


def downgrade() -> None:
    op.drop_table("tag_dictionary_translation")
    op.drop_table("tag_dictionary_alias")
    op.drop_table("tag_dictionary_entry")
    op.drop_table("tag_dictionary")
