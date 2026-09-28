"""prompt_set.created_by_user_id(プロンプトセットの作成者。ADR-0025)

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-28

認証モードでは本人のものだけを見せる(ADR-0025)ため、プロンプトセットにも作成者を記録する。
作成時に一度だけ書き、UPDATE しない。既存行は null のまま(埋め戻さない。認証モードでは
管理者だけに見える)。個人モードでは常に null。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQLite は既存テーブルへの FK 追加に ALTER TABLE が使えないため、batch モードで行う
    # (0010、0013 と同じ書き方)。
    with op.batch_alter_table("prompt_set") as batch_op:
        batch_op.add_column(sa.Column("created_by_user_id", sa.Uuid(), nullable=True))
        batch_op.create_index("ix_prompt_set_created_by_user_id", ["created_by_user_id"])
        batch_op.create_foreign_key(
            "fk_prompt_set_created_by_user_id_app_user",
            "app_user",
            ["created_by_user_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("prompt_set") as batch_op:
        batch_op.drop_constraint("fk_prompt_set_created_by_user_id_app_user", type_="foreignkey")
        batch_op.drop_index("ix_prompt_set_created_by_user_id")
        batch_op.drop_column("created_by_user_id")
