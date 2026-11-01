"""IP 素材(ADR-0059)と証跡(ADR-0006)

Revision ID: 0044
Revises: 0043
Create Date: 2026-11-01

- `ip_property`: IP(作品・版権)。状態は active / suspended / terminated。
- `ip_manager`: IP の担当者。
- `ip_terms`: 契約の条件の版(追記のみ)。用途の一覧と外部の AI への送信。
- `ip_material`: IP 素材の Asset。取り下げは `withdrawn_at`。
- `ip_grant`: 利用の許可(チームか個人)。取り消しは `revoked_at`。
- `audit_event`: 証跡(追記のみ)。`ip_id` には外部キーを張らない。
- 新規テーブルの作成のみで、既存テーブルへの変更は無い。

downgrade はテーブルを消す。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0044"
down_revision: str | None = "0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "ip_property",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("owner_kind", sa.String(16), nullable=False),
        sa.Column("rights_holder", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), sa.ForeignKey("app_user.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "ip_manager",
        sa.Column("ip_id", sa.Uuid(), sa.ForeignKey("ip_property.id"), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("app_user.id"), primary_key=True),
        sa.Column("added_by_user_id", sa.Uuid(), sa.ForeignKey("app_user.id"), nullable=True),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ip_manager_user_id", "ip_manager", ["user_id"], unique=False)
    op.create_table(
        "ip_terms",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("ip_id", sa.Uuid(), sa.ForeignKey("ip_property.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("contract_number", sa.Text(), nullable=True),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column("purposes", _JSON, nullable=False),
        sa.Column("external_ai", sa.String(16), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), sa.ForeignKey("app_user.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("ip_id", "version", name="uq_ip_terms_ip_id_version"),
    )
    op.create_table(
        "ip_material",
        sa.Column("asset_id", sa.Uuid(), sa.ForeignKey("asset.id"), primary_key=True),
        sa.Column("ip_id", sa.Uuid(), sa.ForeignKey("ip_property.id"), nullable=False),
        sa.Column("added_by_user_id", sa.Uuid(), sa.ForeignKey("app_user.id"), nullable=True),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("withdrawn_by_user_id", sa.Uuid(), sa.ForeignKey("app_user.id"), nullable=True),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_ip_material_ip_id", "ip_material", ["ip_id"], unique=False)
    op.create_table(
        "ip_grant",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("ip_id", sa.Uuid(), sa.ForeignKey("ip_property.id"), nullable=False),
        sa.Column("team_id", sa.Uuid(), sa.ForeignKey("team.id"), nullable=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("app_user.id"), nullable=True),
        sa.Column("purposes", _JSON, nullable=False),
        sa.Column("can_use", sa.Boolean(), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column("granted_by_user_id", sa.Uuid(), sa.ForeignKey("app_user.id"), nullable=True),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_by_user_id", sa.Uuid(), sa.ForeignKey("app_user.id"), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_ip_grant_ip_id", "ip_grant", ["ip_id"], unique=False)
    op.create_index("ix_ip_grant_team_id", "ip_grant", ["team_id"], unique=False)
    op.create_index("ix_ip_grant_user_id", "ip_grant", ["user_id"], unique=False)
    op.create_table(
        "audit_event",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("app_user.id"), nullable=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("ip_id", sa.Uuid(), nullable=True),
        sa.Column("target_type", sa.String(32), nullable=True),
        sa.Column("target_id", sa.Text(), nullable=True),
        sa.Column("detail", _JSON, nullable=True),
        sa.Column("client_ip", sa.Text(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_audit_event_ip_id_occurred_at", "audit_event", ["ip_id", "occurred_at"], unique=False
    )
    op.create_index("ix_audit_event_occurred_at", "audit_event", ["occurred_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_audit_event_occurred_at", table_name="audit_event")
    op.drop_index("ix_audit_event_ip_id_occurred_at", table_name="audit_event")
    op.drop_table("audit_event")
    op.drop_index("ix_ip_grant_user_id", table_name="ip_grant")
    op.drop_index("ix_ip_grant_team_id", table_name="ip_grant")
    op.drop_index("ix_ip_grant_ip_id", table_name="ip_grant")
    op.drop_table("ip_grant")
    op.drop_index("ix_ip_material_ip_id", table_name="ip_material")
    op.drop_table("ip_material")
    op.drop_table("ip_terms")
    op.drop_index("ix_ip_manager_user_id", table_name="ip_manager")
    op.drop_table("ip_manager")
    op.drop_table("ip_property")
