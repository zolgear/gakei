"""api_token.expires_at / api_token.scope(アクセストークンの有効期限と権限。ADR-0023 11章)

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-06

- `expires_at`: 有効期限。null は無期限。
- `scope`: `full`(すべて)か `read`(読み取りのみ)。
- この改訂より前に発行したトークンは、無期限(null)・`full` のまま扱う(11章 1・2)。
  `scope` はサーバー側の既定値 `'full'` で埋める。

どちらも発行のときに1回だけ書く。downgrade は列を消す。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("api_token") as batch_op:
        batch_op.add_column(sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(
            sa.Column("scope", sa.String(16), nullable=False, server_default="full")
        )


def downgrade() -> None:
    with op.batch_alter_table("api_token") as batch_op:
        batch_op.drop_column("scope")
        batch_op.drop_column("expires_at")
