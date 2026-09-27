"""asset.origin_ref_asset_id (祖先埋め込みノードへの対応付け。ADR-0014 6章、2026-09-24 追記)

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-24

外部キーを張らない: 埋め込まれていたグラフの root が自称する asset id は、他のインスタンス
の id であることがあり、このデータベースの `asset.id` に存在するとは限らないため。

バックフィルはしない: 既存の `origin_meta` を持つ行についてチャンクを除いた原本の sha256 を
出し直すには、`DATA_DIR` の Blob を読み直す必要があり、マイグレーションの手軽さに見合わない
(ADR-0004 のとおり、原本の読み書きは `AssetStore` を介するべきで、ここでは行わない)。
この migration より前に取り込んだ画像は、`origin_ref_asset_id` が null のままになる
(埋め込みノードのサムネイル対応付けが効かない)。対応付けが必要なら、該当ファイルを
再度アップロードし直す(再取り込み)ことで解決する。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("asset") as batch_op:
        batch_op.add_column(sa.Column("origin_ref_asset_id", sa.Uuid(), nullable=True))
        batch_op.create_index("ix_asset_origin_ref_asset_id", ["origin_ref_asset_id"])


def downgrade() -> None:
    with op.batch_alter_table("asset") as batch_op:
        batch_op.drop_index("ix_asset_origin_ref_asset_id")
        batch_op.drop_column("origin_ref_asset_id")
