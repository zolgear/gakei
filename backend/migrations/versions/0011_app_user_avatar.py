"""app_user.avatar_sha256 の追加(ADR-0020)

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-27

ユーザーのアバター(アップロードと生成画像からの選択)の実体は Asset にせず
`DATA_DIR/avatars/{user_id}.webp` に1枚だけ持つ(証跡ではないので追記のみの規則の対象外)。
ここでは URL のキャッシュ破りと ETag に使うハッシュの列だけを足す。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("app_user") as batch_op:
        batch_op.add_column(sa.Column("avatar_sha256", sa.String(64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("app_user") as batch_op:
        batch_op.drop_column("avatar_sha256")
