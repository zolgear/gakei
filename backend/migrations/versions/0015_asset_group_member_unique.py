"""asset_group_member.asset_id を一意にする(1 つの Asset は 1 つのグループにだけ。ADR-0022)

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-28

グループの所属を多対多から「1 つの Asset が属するグループは 1 つだけ」に変える
(2026-09-28)。通常の索引 `ix_asset_group_member_asset_id` を一意索引
`ux_asset_group_member_asset_id` に置き換える。

一意索引を作る前に、既に複数のグループに入っている Asset の行を 1 行に減らす。残す行は
次の優先順で決める(Python 側で比較するので SQLite / PostgreSQL のどちらでも同じ結果になる)。

1. 削除済みでないグループの行(削除済みグループの行より優先する。画面に出ている所属を残すため)
2. `added_at` が最新の行(利用者が最後に入れたグループ)
3. 同時刻なら `asset_group_id` の文字列表現が大きい方(決定的にするためだけの規則)

downgrade は通常の索引に戻すだけで、減らした行は戻らない。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_asset_group = sa.table(
    "asset_group",
    sa.column("id", sa.Uuid()),
    sa.column("deleted_at", sa.DateTime(timezone=True)),
)
_member = sa.table(
    "asset_group_member",
    sa.column("asset_group_id", sa.Uuid()),
    sa.column("asset_id", sa.Uuid()),
    sa.column("added_at", sa.DateTime(timezone=True)),
)


def _dedupe_members(bind: sa.Connection) -> None:
    rows = bind.execute(
        sa.select(
            _member.c.asset_group_id,
            _member.c.asset_id,
            _member.c.added_at,
            _asset_group.c.deleted_at,
        ).join(_asset_group, _asset_group.c.id == _member.c.asset_group_id)
    ).all()

    by_asset: dict[object, list[sa.Row]] = {}
    for row in rows:
        by_asset.setdefault(row.asset_id, []).append(row)

    for asset_id, asset_rows in by_asset.items():
        if len(asset_rows) < 2:
            continue
        keep = max(
            asset_rows,
            key=lambda r: (r.deleted_at is None, r.added_at, str(r.asset_group_id)),
        )
        bind.execute(
            sa.delete(_member).where(
                _member.c.asset_id == asset_id,
                _member.c.asset_group_id != keep.asset_group_id,
            )
        )


def upgrade() -> None:
    _dedupe_members(op.get_bind())
    # SQLite は batch モード(0013、0014 と同じ書き方)。PostgreSQL でも同じコードで動く。
    with op.batch_alter_table("asset_group_member") as batch_op:
        batch_op.drop_index("ix_asset_group_member_asset_id")
        batch_op.create_index("ux_asset_group_member_asset_id", ["asset_id"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("asset_group_member") as batch_op:
        batch_op.drop_index("ux_asset_group_member_asset_id")
        batch_op.create_index("ix_asset_group_member_asset_id", ["asset_id"])
