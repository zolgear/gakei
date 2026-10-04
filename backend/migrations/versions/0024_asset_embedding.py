"""asset_embedding(画像の埋め込みベクトル。ADR-0033 4章)

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-03

- `asset_embedding`: (Asset、`model_key`)ごとのベクトルと計算の状態(待ち行列を兼ねる)。
  `vector` は float32 リトルエンディアンの BLOB(PostgreSQL では bytea)で、これが正本。
- PostgreSQL では、拡張 `vector`(pgvector)を SAVEPOINT の中で作ってみる。作れたら、次元を
  決めない `embedding vector` 列を足す(検索の索引用。値は `vector` と同じ)。権限が無い、
  拡張が入っていないなどで失敗したら、列は足さずに続ける(SAVEPOINT まで戻すので、
  マイグレーション全体は失敗しない)。後から拡張を入れた場合は、起動時に列を足して BLOB から
  埋める(`app/domain/embedding_index.py`)。
- 新規テーブルの作成のみで、既存テーブルへの変更は無い。証跡ではない(ADR-0033 1章)。

downgrade はテーブルを消す(`embedding` 列もいっしょに消える)。拡張 `vector` は、ほかで
使っているかもしれないので消さない。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.exc import DBAPIError

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "asset_embedding",
        sa.Column("asset_id", sa.Uuid(), sa.ForeignKey("asset.id"), primary_key=True),
        sa.Column("model_key", sa.String(200), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("dim", sa.Integer(), nullable=True),
        sa.Column("vector", sa.LargeBinary(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_asset_embedding_model_key_status", "asset_embedding", ["model_key", "status"]
    )

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    try:
        with bind.begin_nested():
            bind.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
    except DBAPIError:
        return
    bind.exec_driver_sql("ALTER TABLE asset_embedding ADD COLUMN embedding vector")


def downgrade() -> None:
    op.drop_index("ix_asset_embedding_model_key_status", table_name="asset_embedding")
    op.drop_table("asset_embedding")
