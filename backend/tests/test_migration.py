"""起動時の Alembic マイグレーションが期待どおりのテーブルを作ること。"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import inspect


def test_migration_creates_expected_tables(client: TestClient) -> None:
    engine = client.app.state.engine
    tables = set(inspect(engine).get_table_names())
    assert {
        "asset",
        "run",
        "run_input",
        "prompt_set",
        "prompt_set_item",
        "comfy_workflow",
        "app_user",
        "auth_session",
        "alembic_version",
    } <= tables


def test_migration_upgrades_from_0001_to_0002(tmp_path) -> None:  # noqa: ANN001
    """既存の0001適用済みDBに対しても、起動時のupgrade headで0002が当たること。"""
    from alembic import command
    from alembic.config import Config

    from app.main import _MIGRATIONS_DIR

    db_path = tmp_path / "existing.db"
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

    # まず 0001 だけ当てて、既存DBを模する。
    command.upgrade(cfg, "0001")

    from sqlalchemy import create_engine
    from sqlalchemy import inspect as sa_inspect

    engine = create_engine(f"sqlite:///{db_path}")
    tables_after_0001 = set(sa_inspect(engine).get_table_names())
    assert "prompt_set" not in tables_after_0001
    engine.dispose()

    # アプリ起動と同じ経路(upgrade head)で 0002 まで進むこと。
    command.upgrade(cfg, "head")

    engine = create_engine(f"sqlite:///{db_path}")
    tables_after_head = set(sa_inspect(engine).get_table_names())
    assert {"prompt_set", "prompt_set_item"} <= tables_after_head
    engine.dispose()


def test_migration_upgrades_from_0002_to_0003_adds_run_deleted_at(tmp_path) -> None:  # noqa: ANN001
    """既存の0002適用済みDBに対しても、起動時のupgrade headで0003(run.deleted_at)が当たること。"""
    from alembic import command
    from alembic.config import Config

    from app.main import _MIGRATIONS_DIR

    db_path = tmp_path / "existing.db"
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

    # まず 0002 だけ当てて、既存DBを模する。
    command.upgrade(cfg, "0002")

    from sqlalchemy import create_engine
    from sqlalchemy import inspect as sa_inspect

    engine = create_engine(f"sqlite:///{db_path}")
    columns_after_0002 = {c["name"] for c in sa_inspect(engine).get_columns("run")}
    assert "deleted_at" not in columns_after_0002
    engine.dispose()

    # アプリ起動と同じ経路(upgrade head)で 0003 まで進むこと。
    command.upgrade(cfg, "head")

    engine = create_engine(f"sqlite:///{db_path}")
    columns_after_head = {c["name"] for c in sa_inspect(engine).get_columns("run")}
    assert "deleted_at" in columns_after_head
    engine.dispose()


def test_migration_upgrades_from_0004_to_0005_adds_comfy_workflow(tmp_path) -> None:  # noqa: ANN001
    """既存の0004適用済みDBに対しても、起動時のupgrade headで0005(comfy_workflow)が当たること。"""
    from alembic import command
    from alembic.config import Config

    from app.main import _MIGRATIONS_DIR

    db_path = tmp_path / "existing.db"
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

    # まず 0004 だけ当てて、既存DBを模する。
    command.upgrade(cfg, "0004")

    from sqlalchemy import create_engine
    from sqlalchemy import inspect as sa_inspect

    engine = create_engine(f"sqlite:///{db_path}")
    tables_after_0004 = set(sa_inspect(engine).get_table_names())
    assert "comfy_workflow" not in tables_after_0004
    engine.dispose()

    # アプリ起動と同じ経路(upgrade head)で 0005 まで進むこと。
    command.upgrade(cfg, "head")

    engine = create_engine(f"sqlite:///{db_path}")
    tables_after_head = set(sa_inspect(engine).get_table_names())
    assert "comfy_workflow" in tables_after_head
    engine.dispose()

    # downgrade もできること。
    command.downgrade(cfg, "0004")
    engine = create_engine(f"sqlite:///{db_path}")
    tables_after_downgrade = set(sa_inspect(engine).get_table_names())
    assert "comfy_workflow" not in tables_after_downgrade
    engine.dispose()


def test_migration_upgrades_from_0008_to_0009_adds_asset_embedded_meta(tmp_path) -> None:  # noqa: ANN001
    """既存の0008適用済みDBでも、起動時のupgrade headで0009(asset.embedded_meta)が当たること。"""
    from alembic import command
    from alembic.config import Config

    from app.main import _MIGRATIONS_DIR

    db_path = tmp_path / "existing.db"
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

    # まず 0008 だけ当てて、既存DBを模する。
    command.upgrade(cfg, "0008")

    from sqlalchemy import create_engine
    from sqlalchemy import inspect as sa_inspect

    engine = create_engine(f"sqlite:///{db_path}")
    columns_after_0008 = {c["name"] for c in sa_inspect(engine).get_columns("asset")}
    assert "embedded_meta" not in columns_after_0008
    engine.dispose()

    # アプリ起動と同じ経路(upgrade head)で 0009 まで進むこと。
    command.upgrade(cfg, "head")

    engine = create_engine(f"sqlite:///{db_path}")
    columns_after_head = {c["name"] for c in sa_inspect(engine).get_columns("asset")}
    assert "embedded_meta" in columns_after_head
    engine.dispose()

    # downgrade もできること。
    command.downgrade(cfg, "0008")
    engine = create_engine(f"sqlite:///{db_path}")
    columns_after_downgrade = {c["name"] for c in sa_inspect(engine).get_columns("asset")}
    assert "embedded_meta" not in columns_after_downgrade
    engine.dispose()


def test_migration_upgrades_from_0009_to_0010_adds_auth_tables(tmp_path) -> None:  # noqa: ANN001
    """既存の0009適用済みDBでも、起動時のupgrade headで0010(app_user・auth_session・
    run/asset.created_by_user_id)が当たること(ADR-0019)。"""
    from alembic import command
    from alembic.config import Config

    from app.main import _MIGRATIONS_DIR

    db_path = tmp_path / "existing.db"
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

    # まず 0009 だけ当てて、既存DBを模する。
    command.upgrade(cfg, "0009")

    from sqlalchemy import create_engine
    from sqlalchemy import inspect as sa_inspect

    engine = create_engine(f"sqlite:///{db_path}")
    tables_after_0009 = set(sa_inspect(engine).get_table_names())
    assert "app_user" not in tables_after_0009
    assert "auth_session" not in tables_after_0009
    columns_after_0009 = {c["name"] for c in sa_inspect(engine).get_columns("run")}
    assert "created_by_user_id" not in columns_after_0009
    engine.dispose()

    # アプリ起動と同じ経路(upgrade head)で 0010 まで進むこと。
    command.upgrade(cfg, "head")

    engine = create_engine(f"sqlite:///{db_path}")
    tables_after_head = set(sa_inspect(engine).get_table_names())
    assert {"app_user", "auth_session"} <= tables_after_head
    run_columns_after_head = {c["name"] for c in sa_inspect(engine).get_columns("run")}
    asset_columns_after_head = {c["name"] for c in sa_inspect(engine).get_columns("asset")}
    assert "created_by_user_id" in run_columns_after_head
    assert "created_by_user_id" in asset_columns_after_head
    engine.dispose()

    # downgrade もできること。
    command.downgrade(cfg, "0009")
    engine = create_engine(f"sqlite:///{db_path}")
    tables_after_downgrade = set(sa_inspect(engine).get_table_names())
    assert "app_user" not in tables_after_downgrade
    assert "auth_session" not in tables_after_downgrade
    run_columns_after_downgrade = {c["name"] for c in sa_inspect(engine).get_columns("run")}
    assert "created_by_user_id" not in run_columns_after_downgrade
    engine.dispose()


def test_migration_upgrades_from_0010_to_0011_adds_avatar_column(tmp_path) -> None:  # noqa: ANN001
    """既存の0010適用済みDBでも、起動時のupgrade headで0011(app_user.avatar_sha256)
    が当たること(ADR-0020)。"""
    from alembic import command
    from alembic.config import Config

    from app.main import _MIGRATIONS_DIR

    db_path = tmp_path / "existing.db"
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

    command.upgrade(cfg, "0010")

    from sqlalchemy import create_engine
    from sqlalchemy import inspect as sa_inspect

    engine = create_engine(f"sqlite:///{db_path}")
    columns_after_0010 = {c["name"] for c in sa_inspect(engine).get_columns("app_user")}
    assert "avatar_sha256" not in columns_after_0010
    engine.dispose()

    command.upgrade(cfg, "head")
    engine = create_engine(f"sqlite:///{db_path}")
    columns_after_head = {c["name"] for c in sa_inspect(engine).get_columns("app_user")}
    assert "avatar_sha256" in columns_after_head
    engine.dispose()

    command.downgrade(cfg, "0010")
    engine = create_engine(f"sqlite:///{db_path}")
    columns_after_downgrade = {c["name"] for c in sa_inspect(engine).get_columns("app_user")}
    assert "avatar_sha256" not in columns_after_downgrade
    engine.dispose()
