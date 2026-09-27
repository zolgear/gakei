"""asset_group.position(利用者が決めるグループの並び順。ADR-0022)

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-28

一覧の並びを `updated_at DESC` から `position ASC, created_at DESC` に変える。
既存行は、これまでの表示順(`updated_at DESC`)がそのまま 0..n-1 になるように埋める
(削除済みの行も同じ規則で振る。一覧には出ないので値に意味はない)。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_asset_group = sa.table(
    "asset_group",
    sa.column("id", sa.Uuid()),
    sa.column("updated_at", sa.DateTime()),
    sa.column("position", sa.Integer()),
)


def upgrade() -> None:
    # NOT NULL の列を既存行のあるテーブルに足すため、いったん server_default="0" で作る。
    # SQLite は batch モード(0010、0013 と同じ書き方)。PostgreSQL でも同じコードで動く。
    with op.batch_alter_table("asset_group") as batch_op:
        batch_op.add_column(sa.Column("position", sa.Integer(), nullable=False, server_default="0"))

    bind = op.get_bind()
    ids = (
        bind.execute(
            sa.select(_asset_group.c.id).order_by(
                _asset_group.c.updated_at.desc(), _asset_group.c.id.desc()
            )
        )
        .scalars()
        .all()
    )
    for position, group_id in enumerate(ids):
        bind.execute(
            sa.update(_asset_group).where(_asset_group.c.id == group_id).values(position=position)
        )

    # 他の列と同じく DB 側の既定値は持たない(値は常にアプリが決める。models.py と揃える)。
    with op.batch_alter_table("asset_group") as batch_op:
        batch_op.alter_column("position", existing_type=sa.Integer(), server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("asset_group") as batch_op:
        batch_op.drop_column("position")
