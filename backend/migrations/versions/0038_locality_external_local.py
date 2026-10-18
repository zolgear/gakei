"""接続先(`locality`)の値を `external` / `local` に改める(ADR-0051 3章、2026-10-18 の改訂)

Revision ID: 0038
Revises: 0037
Create Date: 2026-10-18

値を `local` / `cloud` / `unset` の3つから、`external` / `local` の2つに改めた。接続の既定は
`external`。ComfyUI のワークフローだけは「接続の設定に従う」(`unset`)を残す。

- `app_setting` の `providers.locality`(画像のプロバイダーの接続): `cloud` → `external`。
  `unset` の項目は消す(既定の `external` に戻る)。
- `app_setting` の `annotation.connections`(LLM・VLM の接続先の一覧): 各項目の `locality` の
  `cloud` → `external`。`unset` は項目から `locality` を消す(既定の `external` に戻る)。
- `comfy_workflow.locality`: `cloud` → `external`。`unset` はそのまま(接続の設定に従う)。

downgrade は `external` → `cloud` に戻す。消した `unset` は戻せないが、旧い版では値が無い・
`unset` のどちらも既定として読むので困らない。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PROVIDERS_KEY = "providers.locality"
CONNECTIONS_KEY = "annotation.connections"

_app_setting = sa.table(
    "app_setting",
    sa.column("key", sa.String(100)),
    sa.column("value", sa.JSON().with_variant(JSONB(), "postgresql")),
)


def _rewrite_setting(key: str, rewrite: Callable[[object], object]) -> None:
    """`app_setting` の1行の `{"value": ...}` の中身を書き換える(変わったときだけ)。"""
    bind = op.get_bind()
    row = bind.execute(sa.select(_app_setting.c.value).where(_app_setting.c.key == key)).first()
    if row is None or not isinstance(row.value, dict):
        return
    inner = row.value.get("value")
    new_inner = rewrite(inner)
    if new_inner == inner:
        return
    bind.execute(
        _app_setting.update()
        .where(_app_setting.c.key == key)
        .values(value={**row.value, "value": new_inner})
    )


def _providers_up(inner: object) -> object:
    if not isinstance(inner, dict):
        return inner
    result: dict[str, object] = {}
    for provider, value in inner.items():
        if value == "unset":
            continue
        result[provider] = "external" if value == "cloud" else value
    return result


def _providers_down(inner: object) -> object:
    if not isinstance(inner, dict):
        return inner
    return {k: ("cloud" if v == "external" else v) for k, v in inner.items()}


def _connections_up(inner: object) -> object:
    if not isinstance(inner, list):
        return inner
    result: list[object] = []
    for item in inner:
        if isinstance(item, dict) and "locality" in item:
            item = dict(item)
            if item["locality"] == "cloud":
                item["locality"] = "external"
            elif item["locality"] == "unset":
                del item["locality"]
        result.append(item)
    return result


def _connections_down(inner: object) -> object:
    if not isinstance(inner, list):
        return inner
    result: list[object] = []
    for item in inner:
        if isinstance(item, dict) and item.get("locality") == "external":
            item = {**item, "locality": "cloud"}
        result.append(item)
    return result


def upgrade() -> None:
    _rewrite_setting(PROVIDERS_KEY, _providers_up)
    _rewrite_setting(CONNECTIONS_KEY, _connections_up)
    op.execute("UPDATE comfy_workflow SET locality = 'external' WHERE locality = 'cloud'")


def downgrade() -> None:
    op.execute("UPDATE comfy_workflow SET locality = 'cloud' WHERE locality = 'external'")
    _rewrite_setting(CONNECTIONS_KEY, _connections_down)
    _rewrite_setting(PROVIDERS_KEY, _providers_down)
