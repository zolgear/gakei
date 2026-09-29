"""PostgreSQL の列の型を揃える: JSON → JSONB、利用者や外部から来る文字列 → TEXT(ADR-0027 2章)

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-29

- JSON の列は、PostgreSQL では JSONB にする(`json` 型は等値比較ができず、DISTINCT や
  重複の確認で失敗するため)。
- 利用者や外部(IdP、プロバイダー、ComfyUI など)から来る文字列の列は、PostgreSQL では
  TEXT にする(SQLite は VARCHAR(n) の長さを無視して通すので、これまで実質「長さ制限なし」で
  動いてきた。PostgreSQL だけが長さ超過で失敗することを避ける)。コードが決める値
  (status、kind、role、sha256 など)は VARCHAR(n) のまま。

SQLite は型の長さも JSON / JSONB の区別も持たないので、何もしない(モデル側の型の変更だけで
よい)。downgrade は PostgreSQL のときだけ元の型に戻す。TEXT から VARCHAR(n) へは明示の
キャストを使わない(長さを超える値があれば黙って切り詰めずに失敗させる)。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (テーブル, 列, nullable)
_JSON_COLUMNS: tuple[tuple[str, str, bool], ...] = (
    ("run", "params", False),
    ("run", "usage", True),
    ("asset", "origin_meta", True),
    ("asset", "embedded_meta", True),
    ("app_setting", "value", False),
    ("comfy_workflow", "template", False),
    ("comfy_workflow", "bindings", False),
    ("comfy_workflow", "exposed_params", False),
    ("asset_annotation", "auto_models", True),
)

# (テーブル, 列, 元の長さ, nullable)
_TEXT_COLUMNS: tuple[tuple[str, str, int, bool], ...] = (
    ("app_user", "issuer", 255, False),
    ("app_user", "subject", 255, False),
    ("app_user", "email", 320, True),
    ("app_user", "name", 255, True),
    ("api_token", "name", 100, False),
    ("asset", "blob_key", 255, False),
    ("run", "model", 64, False),
    ("run", "deployment", 64, True),
    ("run", "prompt", 32000, False),
    ("run", "error_message", 2000, True),
    ("run", "provider_request_id", 128, True),
    ("prompt_set", "name", 100, False),
    ("prompt_set_item", "label", 100, True),
    ("prompt_set_item", "text", 32000, False),
    ("asset_group", "name", 100, False),
    ("comfy_workflow", "name", 100, False),
    ("asset_annotation", "title", 200, True),
    ("asset_annotation", "auto_error", 500, True),
    ("tag", "name", 100, False),
)


def _is_postgresql() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgresql():
        return
    for table, column, nullable in _JSON_COLUMNS:
        op.alter_column(
            table,
            column,
            type_=postgresql.JSONB(),
            existing_type=sa.JSON(),
            existing_nullable=nullable,
            postgresql_using=f'"{column}"::jsonb',
        )
    for table, column, length, nullable in _TEXT_COLUMNS:
        op.alter_column(
            table,
            column,
            type_=sa.Text(),
            existing_type=sa.String(length),
            existing_nullable=nullable,
        )


def downgrade() -> None:
    if not _is_postgresql():
        return
    for table, column, length, nullable in _TEXT_COLUMNS:
        op.alter_column(
            table,
            column,
            type_=sa.String(length),
            existing_type=sa.Text(),
            existing_nullable=nullable,
        )
    for table, column, nullable in _JSON_COLUMNS:
        op.alter_column(
            table,
            column,
            type_=sa.JSON(),
            existing_type=postgresql.JSONB(),
            existing_nullable=nullable,
            postgresql_using=f'"{column}"::json',
        )
