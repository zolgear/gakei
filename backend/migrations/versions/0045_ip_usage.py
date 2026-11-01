"""asset_ip_lineage、run_ip_use(IP の引き継ぎと利用の申告。ADR-0059 5章・6章)

Revision ID: 0045
Revises: 0044
Create Date: 2026-11-01

- `asset_ip_lineage`: Asset が引き継ぐ IP(素材そのものと、素材から作った画像)。作るときに書き、
  後から変えない。
- `run_ip_use`: Run の IP の利用の申告(用途、作業のメモ、条件の版、許可)。追記のみ。
- 既存の IP 素材(`ip_material`)の行から、素材そのものの `asset_ip_lineage` を書く。
- 新規テーブルの作成と、その初期の行だけで、既存テーブルへの変更は無い。

downgrade はテーブルを消す。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0045"
down_revision: str | None = "0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "asset_ip_lineage",
        sa.Column("asset_id", sa.Uuid(), sa.ForeignKey("asset.id"), primary_key=True),
        sa.Column("ip_id", sa.Uuid(), sa.ForeignKey("ip_property.id"), primary_key=True),
    )
    op.create_index("ix_asset_ip_lineage_ip_id", "asset_ip_lineage", ["ip_id"], unique=False)
    op.create_table(
        "run_ip_use",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("run.id"), nullable=False),
        sa.Column("ip_id", sa.Uuid(), sa.ForeignKey("ip_property.id"), nullable=False),
        sa.Column("terms_id", sa.Uuid(), sa.ForeignKey("ip_terms.id"), nullable=False),
        sa.Column("grant_id", sa.Uuid(), sa.ForeignKey("ip_grant.id"), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("work_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_run_ip_use_run_id", "run_ip_use", ["run_id"], unique=False)
    op.create_index(
        "ix_run_ip_use_ip_id_created_at", "run_ip_use", ["ip_id", "created_at"], unique=False
    )
    op.execute(
        "INSERT INTO asset_ip_lineage (asset_id, ip_id) SELECT asset_id, ip_id FROM ip_material"
    )


def downgrade() -> None:
    op.drop_index("ix_run_ip_use_ip_id_created_at", table_name="run_ip_use")
    op.drop_index("ix_run_ip_use_run_id", table_name="run_ip_use")
    op.drop_table("run_ip_use")
    op.drop_index("ix_asset_ip_lineage_ip_id", table_name="asset_ip_lineage")
    op.drop_table("asset_ip_lineage")
