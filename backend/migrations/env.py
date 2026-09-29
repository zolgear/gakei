"""Alembic 実行環境。`app.domain.models` の metadata を使い、SQLite 向けに batch モードで動かす
(PostgreSQL でも同じマイグレーションが通る。ADR-0027)。"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# backend/ を sys.path に追加し、CLI から直接 alembic を叩いても app を import できるようにする。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.models import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # 呼び出し側が接続を渡した場合(`app.tools.migrate_to_postgres`)は、その接続の
    # トランザクションの中で流す(スキーマの作成とデータのコピーを1つのトランザクションに
    # まとめ、途中で失敗したら全体を戻すため。ADR-0027 4章)。
    shared_connection = config.attributes.get("connection")
    if shared_connection is not None:
        context.configure(
            connection=shared_connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()
        return

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # SQLite は ALTER が弱いため、将来のマイグレーションに備える
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
