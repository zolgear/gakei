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
        "asset_group",
        "asset_group_member",
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


def test_migration_upgrades_from_0011_to_0012_adds_asset_group_tables(tmp_path) -> None:  # noqa: ANN001
    """既存の0011適用済みDBでも、起動時のupgrade headで0012(asset_group、
    asset_group_member)が当たること(ADR-0022)。"""
    from alembic import command
    from alembic.config import Config

    from app.main import _MIGRATIONS_DIR

    db_path = tmp_path / "existing.db"
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

    command.upgrade(cfg, "0011")

    from sqlalchemy import create_engine
    from sqlalchemy import inspect as sa_inspect

    engine = create_engine(f"sqlite:///{db_path}")
    tables_after_0011 = set(sa_inspect(engine).get_table_names())
    assert "asset_group" not in tables_after_0011
    assert "asset_group_member" not in tables_after_0011
    engine.dispose()

    command.upgrade(cfg, "head")
    engine = create_engine(f"sqlite:///{db_path}")
    tables_after_head = set(sa_inspect(engine).get_table_names())
    assert {"asset_group", "asset_group_member"} <= tables_after_head
    engine.dispose()

    command.downgrade(cfg, "0011")
    engine = create_engine(f"sqlite:///{db_path}")
    tables_after_downgrade = set(sa_inspect(engine).get_table_names())
    assert "asset_group" not in tables_after_downgrade
    assert "asset_group_member" not in tables_after_downgrade
    engine.dispose()


def test_migration_upgrades_from_0012_to_0013_adds_run_asset_group_id(tmp_path) -> None:  # noqa: ANN001
    """既存の0012適用済みDBでも、起動時のupgrade headで0013(run.asset_group_id)
    が当たること(ADR-0022)。"""
    from alembic import command
    from alembic.config import Config

    from app.main import _MIGRATIONS_DIR

    db_path = tmp_path / "existing.db"
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

    command.upgrade(cfg, "0012")

    from sqlalchemy import create_engine
    from sqlalchemy import inspect as sa_inspect

    engine = create_engine(f"sqlite:///{db_path}")
    columns_after_0012 = {c["name"] for c in sa_inspect(engine).get_columns("run")}
    assert "asset_group_id" not in columns_after_0012
    engine.dispose()

    command.upgrade(cfg, "head")
    engine = create_engine(f"sqlite:///{db_path}")
    inspector = sa_inspect(engine)
    columns_after_head = {c["name"] for c in inspector.get_columns("run")}
    assert "asset_group_id" in columns_after_head
    assert "ix_run_asset_group_id" in {i["name"] for i in inspector.get_indexes("run")}
    fks = {fk["name"]: fk for fk in inspector.get_foreign_keys("run")}
    assert fks["fk_run_asset_group_id_asset_group"]["referred_table"] == "asset_group"
    engine.dispose()

    command.downgrade(cfg, "0012")
    engine = create_engine(f"sqlite:///{db_path}")
    inspector = sa_inspect(engine)
    columns_after_downgrade = {c["name"] for c in inspector.get_columns("run")}
    assert "asset_group_id" not in columns_after_downgrade
    assert "ix_run_asset_group_id" not in {i["name"] for i in inspector.get_indexes("run")}
    engine.dispose()


def test_migration_upgrades_from_0013_to_0014_adds_asset_group_position(tmp_path) -> None:  # noqa: ANN001
    """既存の0013適用済みDBでも、起動時のupgrade headで0014(asset_group.position)が当たり、
    既存行はそれまでの表示順(updated_at DESC)が 0..n-1 になるよう埋まること(ADR-0022)。"""
    import uuid

    from alembic import command
    from alembic.config import Config

    from app.main import _MIGRATIONS_DIR

    db_path = tmp_path / "existing.db"
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

    command.upgrade(cfg, "0013")

    from sqlalchemy import Uuid, bindparam, create_engine, text
    from sqlalchemy import inspect as sa_inspect

    engine = create_engine(f"sqlite:///{db_path}")
    columns_after_0013 = {c["name"] for c in sa_inspect(engine).get_columns("asset_group")}
    assert "position" not in columns_after_0013

    # updated_at が古い順に old / middle / new を入れる(挿入順とは変えておく)。
    ids = {name: uuid.uuid4() for name in ("old", "middle", "new")}
    seeds = [
        ("middle", "2026-09-02 00:00:00.000000"),
        ("new", "2026-09-03 00:00:00.000000"),
        ("old", "2026-09-01 00:00:00.000000"),
    ]
    insert = text(
        "INSERT INTO asset_group (id, name, created_at, updated_at) VALUES (:id, :name, :ts, :ts)"
    ).bindparams(bindparam("id", type_=Uuid()))
    with engine.begin() as conn:
        for name, ts in seeds:
            conn.execute(insert, {"id": ids[name], "name": name, "ts": ts})
    engine.dispose()

    command.upgrade(cfg, "head")
    engine = create_engine(f"sqlite:///{db_path}")
    columns = {c["name"]: c for c in sa_inspect(engine).get_columns("asset_group")}
    assert "position" in columns
    assert columns["position"]["nullable"] is False
    assert columns["position"]["default"] is None  # 埋めた後は DB 側の既定値を外す
    with engine.connect() as conn:
        positions = dict(conn.execute(text("SELECT name, position FROM asset_group")).all())
    assert positions == {"new": 0, "middle": 1, "old": 2}
    engine.dispose()

    command.downgrade(cfg, "0013")
    engine = create_engine(f"sqlite:///{db_path}")
    columns_after_downgrade = {c["name"] for c in sa_inspect(engine).get_columns("asset_group")}
    assert "position" not in columns_after_downgrade
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM asset_group")).scalar_one() == 3
    engine.dispose()


def test_migration_upgrades_from_0014_to_0015_makes_member_asset_id_unique(tmp_path) -> None:  # noqa: ANN001
    """既存の0014適用済みDBでも、起動時のupgrade headで0015が当たり、複数のグループに
    入っていた Asset の行が 1 行に減り(削除済みでないグループを優先し、その中で added_at が
    最新の行を残す)、asset_id の一意索引ができること(ADR-0022、2026-09-28)。"""
    import uuid

    from alembic import command
    from alembic.config import Config

    from app.main import _MIGRATIONS_DIR

    db_path = tmp_path / "existing.db"
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

    command.upgrade(cfg, "0014")

    from sqlalchemy import Uuid, bindparam, create_engine, text
    from sqlalchemy import inspect as sa_inspect

    engine = create_engine(f"sqlite:///{db_path}")
    groups = {name: uuid.uuid4() for name in ("g1", "g2", "deleted")}
    assets = {name: uuid.uuid4() for name in ("two_live", "live_and_deleted", "only_deleted")}
    insert_group = text(
        "INSERT INTO asset_group (id, name, position, created_at, updated_at, deleted_at) "
        "VALUES (:id, :name, 0, :ts, :ts, :deleted_at)"
    ).bindparams(bindparam("id", type_=Uuid()))
    insert_asset = text(
        "INSERT INTO asset (id, kind, sha256, blob_key, mime, width, height, bytes, created_at) "
        "VALUES (:id, 'upload', :sha, :key, 'image/png', 1, 1, 1, '2026-09-01 00:00:00.000000')"
    ).bindparams(bindparam("id", type_=Uuid()))
    insert_member = text(
        "INSERT INTO asset_group_member (asset_group_id, asset_id, added_at) "
        "VALUES (:group_id, :asset_id, :added_at)"
    ).bindparams(bindparam("group_id", type_=Uuid()), bindparam("asset_id", type_=Uuid()))
    ts = "2026-09-01 00:00:00.000000"
    with engine.begin() as conn:
        for name, group_id in groups.items():
            deleted_at = ts if name == "deleted" else None
            conn.execute(
                insert_group, {"id": group_id, "name": name, "ts": ts, "deleted_at": deleted_at}
            )
        for name, asset_id in assets.items():
            conn.execute(insert_asset, {"id": asset_id, "sha": name, "key": name})
        seeds = [
            # 削除済みでないグループ 2 つ → added_at が新しい g2 を残す。
            ("g1", "two_live", "2026-09-01 00:00:00.000000"),
            ("g2", "two_live", "2026-09-03 00:00:00.000000"),
            # 削除済みグループの方が新しくても、削除済みでない g1 を残す。
            ("g1", "live_and_deleted", "2026-09-01 00:00:00.000000"),
            ("deleted", "live_and_deleted", "2026-09-05 00:00:00.000000"),
            # 削除済みグループにだけ入っている行はそのまま残す。
            ("deleted", "only_deleted", "2026-09-02 00:00:00.000000"),
        ]
        for group_name, asset_name, added_at in seeds:
            conn.execute(
                insert_member,
                {
                    "group_id": groups[group_name],
                    "asset_id": assets[asset_name],
                    "added_at": added_at,
                },
            )
    engine.dispose()

    command.upgrade(cfg, "head")
    engine = create_engine(f"sqlite:///{db_path}")
    indexes = {i["name"]: i for i in sa_inspect(engine).get_indexes("asset_group_member")}
    assert "ix_asset_group_member_asset_id" not in indexes
    assert indexes["ux_asset_group_member_asset_id"]["unique"]
    assert indexes["ux_asset_group_member_asset_id"]["column_names"] == ["asset_id"]
    group_names = {group_id.hex: name for name, group_id in groups.items()}
    asset_names = {asset_id.hex: name for name, asset_id in assets.items()}
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT asset_id, asset_group_id, added_at FROM asset_group_member")
        ).all()
    remaining = {
        asset_names[uuid.UUID(str(a)).hex]: (group_names[uuid.UUID(str(g)).hex], added)
        for a, g, added in rows
    }
    assert len(rows) == 3
    assert remaining == {
        "two_live": ("g2", "2026-09-03 00:00:00.000000"),
        "live_and_deleted": ("g1", "2026-09-01 00:00:00.000000"),
        "only_deleted": ("deleted", "2026-09-02 00:00:00.000000"),
    }
    engine.dispose()

    command.downgrade(cfg, "0014")
    engine = create_engine(f"sqlite:///{db_path}")
    indexes = {i["name"]: i for i in sa_inspect(engine).get_indexes("asset_group_member")}
    assert "ux_asset_group_member_asset_id" not in indexes
    assert not indexes["ix_asset_group_member_asset_id"]["unique"]
    engine.dispose()
