"""run_prompt_embedding(Run のプロンプトの埋め込みベクトル。ADR-0045 3章)

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-11

- `run_prompt_embedding`: (Run、`model_key`)ごとのベクトルと計算の状態(待ち行列を兼ねる)。
  作りは `asset_embedding`(0024)と同じで、`vector` は float32 リトルエンディアンの BLOB が正本。
  ほかに、埋め込んだ文章の sha256(`text_sha256`)と、上限で切ったか(`truncated`)を持つ。
- PostgreSQL では、0024 と同じく拡張 `vector`(pgvector)を SAVEPOINT の中で作ってみて、作れたら
  `embedding vector` 列を足す。作れなければ列は足さずに続ける(起動時に、拡張を後から入れた
  場合も含めて列を足して BLOB から埋める。`app/domain/embedding_index.py`)。
- 新規テーブルの作成のみで、既存テーブルへの変更は無い。証跡ではない(ADR-0045 1章)。

downgrade はテーブルを消す(`embedding` 列もいっしょに消える)。拡張 `vector` は消さない。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.exc import DBAPIError

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_prompt_embedding",
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("run.id"), primary_key=True),
        sa.Column("model_key", sa.String(200), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("dim", sa.Integer(), nullable=True),
        sa.Column("vector", sa.LargeBinary(), nullable=True),
        sa.Column("text_sha256", sa.String(64), nullable=True),
        sa.Column("truncated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_run_prompt_embedding_model_key_status",
        "run_prompt_embedding",
        ["model_key", "status"],
    )

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    try:
        with bind.begin_nested():
            bind.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
    except DBAPIError:
        return
    bind.exec_driver_sql("ALTER TABLE run_prompt_embedding ADD COLUMN embedding vector")


def downgrade() -> None:
    op.drop_index("ix_run_prompt_embedding_model_key_status", table_name="run_prompt_embedding")
    op.drop_table("run_prompt_embedding")
