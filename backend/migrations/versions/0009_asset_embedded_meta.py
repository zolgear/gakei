"""asset.embedded_meta (他ツール/C2PA の生成メタ情報。ADR-0018、2026-09-26 追記)

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-26

既存行の埋め戻しは migration では行わず `app.tools.backfill_embedded_meta` で行う
(0008 と同じ理由: 原本の読み直しは `AssetStore` を介す必要があり、マイグレーションの
手軽さに見合わない)。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("asset") as batch_op:
        batch_op.add_column(sa.Column("embedded_meta", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("asset") as batch_op:
        batch_op.drop_column("embedded_meta")
