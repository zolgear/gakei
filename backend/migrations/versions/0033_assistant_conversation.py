"""assistant_conversation と assistant_message(生成のアシスタントの会話。ADR-0048 3章)

Revision ID: 0033
Revises: 0032
Create Date: 2026-10-13

- `assistant_conversation`: 会話。証跡ではないので、名前の変更と論理削除(`deleted_at`)ができる。
  作成者(ADR-0025)は作成時に一度だけ書く。
- `assistant_message`: 会話のメッセージ。`role` は `user` / `assistant` / `tool`、`content` は
  JSON(PostgreSQL では他の JSON 列と同じく JSONB。ADR-0027 2章)。会話の中の順番 `position` は
  一意。画像の中身(base64)は入れない(ADR-0004)。

downgrade はテーブルを消す。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "assistant_conversation",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["app_user.id"],
            name="fk_assistant_conversation_created_by_user_id_app_user",
        ),
    )
    op.create_index(
        "ix_assistant_conversation_created_by_user_id",
        "assistant_conversation",
        ["created_by_user_id"],
    )
    op.create_index(
        "ix_assistant_conversation_updated_at", "assistant_conversation", ["updated_at"]
    )

    op.create_table(
        "assistant_message",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", _JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["assistant_conversation.id"],
            name="fk_assistant_message_conversation_id_assistant_conversation",
        ),
        sa.UniqueConstraint(
            "conversation_id", "position", name="uq_assistant_message_conversation_position"
        ),
    )
    op.create_index(
        "ix_assistant_message_conversation_id", "assistant_message", ["conversation_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_assistant_message_conversation_id", table_name="assistant_message")
    op.drop_table("assistant_message")
    op.drop_index("ix_assistant_conversation_updated_at", table_name="assistant_conversation")
    op.drop_index(
        "ix_assistant_conversation_created_by_user_id", table_name="assistant_conversation"
    )
    op.drop_table("assistant_conversation")
