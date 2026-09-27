"""app_user・auth_session の新設と run/asset.created_by_user_id(ADR-0019)

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-27

OIDC ログイン(AUTH_MODE=oidc)のユーザーとサーバー側セッション、実行者/アップロード者の
記録を追加する。既存行の created_by_user_id は常に null のまま(none モードで動いていた
既存データに実行者を遡って付けることはできないため)。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_user",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("issuer", sa.String(255), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("issuer", "subject", name="uq_app_user_issuer_subject"),
    )

    op.create_table(
        "auth_session",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["app_user.id"], name="fk_auth_session_user_id_app_user"
        ),
    )
    op.create_index("ix_auth_session_user_id", "auth_session", ["user_id"])
    op.create_index("ix_auth_session_expires_at", "auth_session", ["expires_at"])

    # SQLite は既存テーブルへの FK 追加に ALTER TABLE が使えない(テーブル再作成が要る)ため、
    # batch モードでまとめて行う(0004 と同じ書き方)。PostgreSQL でも同じコードのまま
    # 通常の ALTER として動く。
    with op.batch_alter_table("run") as batch_op:
        batch_op.add_column(sa.Column("created_by_user_id", sa.Uuid(), nullable=True))
        batch_op.create_index("ix_run_created_by_user_id", ["created_by_user_id"])
        batch_op.create_foreign_key(
            "fk_run_created_by_user_id_app_user",
            "app_user",
            ["created_by_user_id"],
            ["id"],
        )

    with op.batch_alter_table("asset") as batch_op:
        batch_op.add_column(sa.Column("created_by_user_id", sa.Uuid(), nullable=True))
        batch_op.create_index("ix_asset_created_by_user_id", ["created_by_user_id"])
        batch_op.create_foreign_key(
            "fk_asset_created_by_user_id_app_user",
            "app_user",
            ["created_by_user_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("asset") as batch_op:
        batch_op.drop_constraint("fk_asset_created_by_user_id_app_user", type_="foreignkey")
        batch_op.drop_index("ix_asset_created_by_user_id")
        batch_op.drop_column("created_by_user_id")

    with op.batch_alter_table("run") as batch_op:
        batch_op.drop_constraint("fk_run_created_by_user_id_app_user", type_="foreignkey")
        batch_op.drop_index("ix_run_created_by_user_id")
        batch_op.drop_column("created_by_user_id")

    op.drop_index("ix_auth_session_expires_at", table_name="auth_session")
    op.drop_index("ix_auth_session_user_id", table_name="auth_session")
    op.drop_table("auth_session")
    op.drop_table("app_user")
