"""run.regions(領域の指示の四角。ADR-0053 4章)

Revision ID: 0039
Revises: 0038
Create Date: 2026-10-20

- `run.regions`: 利用者が付けた四角と、どの渡し方で送ったか(`delivery`)と、変換の版
  (`version`)。nullable で、Run の作成時に一度だけ書く。既存の行は NULL のまま(ADR-0003 の
  追記のみの規則に反しない)。他の JSON 列と同じく PostgreSQL では JSONB(ADR-0027 2章)。
- Asset の種類 `regions` は文字列の列の値が増えるだけなので、列は変えない。

downgrade は列を消す。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0039"
down_revision: str | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column("run", sa.Column("regions", _JSON, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("run") as batch_op:
        batch_op.drop_column("regions")
