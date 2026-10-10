"""起動時の Alembic マイグレーションが期待どおりのテーブルを作ること。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect

from tests.conftest import requires_postgresql

pytestmark = pytest.mark.windows


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
        "api_token",
        "asset_annotation",
        "tag",
        "asset_tag",
        "upload_ticket",
        "download_ticket",
        "share",
        "share_asset",
        "run_import",
        "parameter_set",
        "tag_dictionary",
        "tag_dictionary_entry",
        "tag_dictionary_alias",
        "tag_dictionary_translation",
        "alembic_version",
    } <= tables


def test_migration_upgrades_from_0001_to_0002(empty_database_url: str) -> None:
    """既存の0001適用済みDBに対しても、起動時のupgrade headで0002が当たること。"""
    from alembic import command
    from alembic.config import Config

    from app.main import _MIGRATIONS_DIR

    db_url = empty_database_url
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", db_url)

    # まず 0001 だけ当てて、既存DBを模する。
    command.upgrade(cfg, "0001")

    from sqlalchemy import create_engine
    from sqlalchemy import inspect as sa_inspect

    engine = create_engine(db_url)
    tables_after_0001 = set(sa_inspect(engine).get_table_names())
    assert "prompt_set" not in tables_after_0001
    engine.dispose()

    # アプリ起動と同じ経路(upgrade head)で 0002 まで進むこと。
    command.upgrade(cfg, "head")

    engine = create_engine(db_url)
    tables_after_head = set(sa_inspect(engine).get_table_names())
    assert {"prompt_set", "prompt_set_item"} <= tables_after_head
    engine.dispose()


def test_migration_upgrades_from_0002_to_0003_adds_run_deleted_at(empty_database_url: str) -> None:
    """既存の0002適用済みDBに対しても、起動時のupgrade headで0003(run.deleted_at)が当たること。"""
    from alembic import command
    from alembic.config import Config

    from app.main import _MIGRATIONS_DIR

    db_url = empty_database_url
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", db_url)

    # まず 0002 だけ当てて、既存DBを模する。
    command.upgrade(cfg, "0002")

    from sqlalchemy import create_engine
    from sqlalchemy import inspect as sa_inspect

    engine = create_engine(db_url)
    columns_after_0002 = {c["name"] for c in sa_inspect(engine).get_columns("run")}
    assert "deleted_at" not in columns_after_0002
    engine.dispose()

    # アプリ起動と同じ経路(upgrade head)で 0003 まで進むこと。
    command.upgrade(cfg, "head")

    engine = create_engine(db_url)
    columns_after_head = {c["name"] for c in sa_inspect(engine).get_columns("run")}
    assert "deleted_at" in columns_after_head
    engine.dispose()


def test_migration_upgrades_from_0004_to_0005_adds_comfy_workflow(empty_database_url: str) -> None:
    """既存の0004適用済みDBに対しても、起動時のupgrade headで0005(comfy_workflow)が当たること。"""
    from alembic import command
    from alembic.config import Config

    from app.main import _MIGRATIONS_DIR

    db_url = empty_database_url
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", db_url)

    # まず 0004 だけ当てて、既存DBを模する。
    command.upgrade(cfg, "0004")

    from sqlalchemy import create_engine
    from sqlalchemy import inspect as sa_inspect

    engine = create_engine(db_url)
    tables_after_0004 = set(sa_inspect(engine).get_table_names())
    assert "comfy_workflow" not in tables_after_0004
    engine.dispose()

    # アプリ起動と同じ経路(upgrade head)で 0005 まで進むこと。
    command.upgrade(cfg, "head")

    engine = create_engine(db_url)
    tables_after_head = set(sa_inspect(engine).get_table_names())
    assert "comfy_workflow" in tables_after_head
    engine.dispose()

    # downgrade もできること。
    command.downgrade(cfg, "0004")
    engine = create_engine(db_url)
    tables_after_downgrade = set(sa_inspect(engine).get_table_names())
    assert "comfy_workflow" not in tables_after_downgrade
    engine.dispose()


def test_migration_upgrades_from_0008_to_0009_adds_asset_embedded_meta(
    empty_database_url: str,
) -> None:
    """既存の0008適用済みDBでも、起動時のupgrade headで0009(asset.embedded_meta)が当たること。"""
    from alembic import command
    from alembic.config import Config

    from app.main import _MIGRATIONS_DIR

    db_url = empty_database_url
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", db_url)

    # まず 0008 だけ当てて、既存DBを模する。
    command.upgrade(cfg, "0008")

    from sqlalchemy import create_engine
    from sqlalchemy import inspect as sa_inspect

    engine = create_engine(db_url)
    columns_after_0008 = {c["name"] for c in sa_inspect(engine).get_columns("asset")}
    assert "embedded_meta" not in columns_after_0008
    engine.dispose()

    # アプリ起動と同じ経路(upgrade head)で 0009 まで進むこと。
    command.upgrade(cfg, "head")

    engine = create_engine(db_url)
    columns_after_head = {c["name"] for c in sa_inspect(engine).get_columns("asset")}
    assert "embedded_meta" in columns_after_head
    engine.dispose()

    # downgrade もできること。
    command.downgrade(cfg, "0008")
    engine = create_engine(db_url)
    columns_after_downgrade = {c["name"] for c in sa_inspect(engine).get_columns("asset")}
    assert "embedded_meta" not in columns_after_downgrade
    engine.dispose()


def test_migration_upgrades_from_0009_to_0010_adds_auth_tables(empty_database_url: str) -> None:
    """既存の0009適用済みDBでも、起動時のupgrade headで0010(app_user・auth_session・
    run/asset.created_by_user_id)が当たること(ADR-0019)。"""
    from alembic import command
    from alembic.config import Config

    from app.main import _MIGRATIONS_DIR

    db_url = empty_database_url
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", db_url)

    # まず 0009 だけ当てて、既存DBを模する。
    command.upgrade(cfg, "0009")

    from sqlalchemy import create_engine
    from sqlalchemy import inspect as sa_inspect

    engine = create_engine(db_url)
    tables_after_0009 = set(sa_inspect(engine).get_table_names())
    assert "app_user" not in tables_after_0009
    assert "auth_session" not in tables_after_0009
    columns_after_0009 = {c["name"] for c in sa_inspect(engine).get_columns("run")}
    assert "created_by_user_id" not in columns_after_0009
    engine.dispose()

    # アプリ起動と同じ経路(upgrade head)で 0010 まで進むこと。
    command.upgrade(cfg, "head")

    engine = create_engine(db_url)
    tables_after_head = set(sa_inspect(engine).get_table_names())
    assert {"app_user", "auth_session"} <= tables_after_head
    run_columns_after_head = {c["name"] for c in sa_inspect(engine).get_columns("run")}
    asset_columns_after_head = {c["name"] for c in sa_inspect(engine).get_columns("asset")}
    assert "created_by_user_id" in run_columns_after_head
    assert "created_by_user_id" in asset_columns_after_head
    engine.dispose()

    # downgrade もできること。
    command.downgrade(cfg, "0009")
    engine = create_engine(db_url)
    tables_after_downgrade = set(sa_inspect(engine).get_table_names())
    assert "app_user" not in tables_after_downgrade
    assert "auth_session" not in tables_after_downgrade
    run_columns_after_downgrade = {c["name"] for c in sa_inspect(engine).get_columns("run")}
    assert "created_by_user_id" not in run_columns_after_downgrade
    engine.dispose()


def test_migration_upgrades_from_0010_to_0011_adds_avatar_column(empty_database_url: str) -> None:
    """既存の0010適用済みDBでも、起動時のupgrade headで0011(app_user.avatar_sha256)
    が当たること(ADR-0020)。"""
    from alembic import command
    from alembic.config import Config

    from app.main import _MIGRATIONS_DIR

    db_url = empty_database_url
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", db_url)

    command.upgrade(cfg, "0010")

    from sqlalchemy import create_engine
    from sqlalchemy import inspect as sa_inspect

    engine = create_engine(db_url)
    columns_after_0010 = {c["name"] for c in sa_inspect(engine).get_columns("app_user")}
    assert "avatar_sha256" not in columns_after_0010
    engine.dispose()

    command.upgrade(cfg, "head")
    engine = create_engine(db_url)
    columns_after_head = {c["name"] for c in sa_inspect(engine).get_columns("app_user")}
    assert "avatar_sha256" in columns_after_head
    engine.dispose()

    command.downgrade(cfg, "0010")
    engine = create_engine(db_url)
    columns_after_downgrade = {c["name"] for c in sa_inspect(engine).get_columns("app_user")}
    assert "avatar_sha256" not in columns_after_downgrade
    engine.dispose()


def test_migration_upgrades_from_0011_to_0012_adds_asset_group_tables(
    empty_database_url: str,
) -> None:
    """既存の0011適用済みDBでも、起動時のupgrade headで0012(asset_group、
    asset_group_member)が当たること(ADR-0022)。"""
    from alembic import command
    from alembic.config import Config

    from app.main import _MIGRATIONS_DIR

    db_url = empty_database_url
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", db_url)

    command.upgrade(cfg, "0011")

    from sqlalchemy import create_engine
    from sqlalchemy import inspect as sa_inspect

    engine = create_engine(db_url)
    tables_after_0011 = set(sa_inspect(engine).get_table_names())
    assert "asset_group" not in tables_after_0011
    assert "asset_group_member" not in tables_after_0011
    engine.dispose()

    command.upgrade(cfg, "head")
    engine = create_engine(db_url)
    tables_after_head = set(sa_inspect(engine).get_table_names())
    assert {"asset_group", "asset_group_member"} <= tables_after_head
    engine.dispose()

    command.downgrade(cfg, "0011")
    engine = create_engine(db_url)
    tables_after_downgrade = set(sa_inspect(engine).get_table_names())
    assert "asset_group" not in tables_after_downgrade
    assert "asset_group_member" not in tables_after_downgrade
    engine.dispose()


def test_migration_upgrades_from_0012_to_0013_adds_run_asset_group_id(
    empty_database_url: str,
) -> None:
    """既存の0012適用済みDBでも、起動時のupgrade headで0013(run.asset_group_id)
    が当たること(ADR-0022)。"""
    from alembic import command
    from alembic.config import Config

    from app.main import _MIGRATIONS_DIR

    db_url = empty_database_url
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", db_url)

    command.upgrade(cfg, "0012")

    from sqlalchemy import create_engine
    from sqlalchemy import inspect as sa_inspect

    engine = create_engine(db_url)
    columns_after_0012 = {c["name"] for c in sa_inspect(engine).get_columns("run")}
    assert "asset_group_id" not in columns_after_0012
    engine.dispose()

    command.upgrade(cfg, "head")
    engine = create_engine(db_url)
    inspector = sa_inspect(engine)
    columns_after_head = {c["name"] for c in inspector.get_columns("run")}
    assert "asset_group_id" in columns_after_head
    assert "ix_run_asset_group_id" in {i["name"] for i in inspector.get_indexes("run")}
    fks = {fk["name"]: fk for fk in inspector.get_foreign_keys("run")}
    assert fks["fk_run_asset_group_id_asset_group"]["referred_table"] == "asset_group"
    engine.dispose()

    command.downgrade(cfg, "0012")
    engine = create_engine(db_url)
    inspector = sa_inspect(engine)
    columns_after_downgrade = {c["name"] for c in inspector.get_columns("run")}
    assert "asset_group_id" not in columns_after_downgrade
    assert "ix_run_asset_group_id" not in {i["name"] for i in inspector.get_indexes("run")}
    engine.dispose()


def test_migration_upgrades_from_0013_to_0014_adds_asset_group_position(
    empty_database_url: str,
) -> None:
    """既存の0013適用済みDBでも、起動時のupgrade headで0014(asset_group.position)が当たり、
    既存行はそれまでの表示順(updated_at DESC)が 0..n-1 になるよう埋まること(ADR-0022)。"""
    import uuid

    from alembic import command
    from alembic.config import Config

    from app.main import _MIGRATIONS_DIR

    db_url = empty_database_url
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", db_url)

    command.upgrade(cfg, "0013")

    from sqlalchemy import Uuid, bindparam, create_engine, text
    from sqlalchemy import inspect as sa_inspect

    engine = create_engine(db_url)
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
    engine = create_engine(db_url)
    columns = {c["name"]: c for c in sa_inspect(engine).get_columns("asset_group")}
    assert "position" in columns
    assert columns["position"]["nullable"] is False
    assert columns["position"]["default"] is None  # 埋めた後は DB 側の既定値を外す
    with engine.connect() as conn:
        positions = dict(conn.execute(text("SELECT name, position FROM asset_group")).all())
    assert positions == {"new": 0, "middle": 1, "old": 2}
    engine.dispose()

    command.downgrade(cfg, "0013")
    engine = create_engine(db_url)
    columns_after_downgrade = {c["name"] for c in sa_inspect(engine).get_columns("asset_group")}
    assert "position" not in columns_after_downgrade
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM asset_group")).scalar_one() == 3
    engine.dispose()


def test_migration_upgrades_from_0014_to_0015_makes_member_asset_id_unique(
    empty_database_url: str,
) -> None:
    """既存の0014適用済みDBでも、起動時のupgrade headで0015が当たり、複数のグループに
    入っていた Asset の行が 1 行に減り(削除済みでないグループを優先し、その中で added_at が
    最新の行を残す)、asset_id の一意索引ができること(ADR-0022、2026-09-28)。"""
    import uuid

    from alembic import command
    from alembic.config import Config

    from app.main import _MIGRATIONS_DIR

    db_url = empty_database_url
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", db_url)

    command.upgrade(cfg, "0014")

    from sqlalchemy import Uuid, bindparam, create_engine, text
    from sqlalchemy import inspect as sa_inspect

    engine = create_engine(db_url)
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
    engine = create_engine(db_url)
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
    # SQLite は文字列、PostgreSQL は datetime で返るので、日付の部分だけで比べる(ADR-0027)。
    remaining = {
        asset_names[uuid.UUID(str(a)).hex]: (group_names[uuid.UUID(str(g)).hex], str(added)[:10])
        for a, g, added in rows
    }
    assert len(rows) == 3
    assert remaining == {
        "two_live": ("g2", "2026-09-03"),
        "live_and_deleted": ("g1", "2026-09-01"),
        "only_deleted": ("deleted", "2026-09-02"),
    }
    engine.dispose()

    command.downgrade(cfg, "0014")
    engine = create_engine(db_url)
    indexes = {i["name"]: i for i in sa_inspect(engine).get_indexes("asset_group_member")}
    assert "ux_asset_group_member_asset_id" not in indexes
    assert not indexes["ix_asset_group_member_asset_id"]["unique"]
    engine.dispose()


def test_migration_upgrades_from_0015_to_0016_adds_api_token_and_run_origin(
    empty_database_url: str,
) -> None:
    """既存の0015適用済みDBでも、起動時のupgrade headで0016(api_token、run.origin、
    run.api_token_id)が当たり、既存の Run は null(= 画面)のままであること。downgrade も
    できること(ADR-0023)。"""
    import uuid

    from alembic import command
    from alembic.config import Config

    from app.main import _MIGRATIONS_DIR

    db_url = empty_database_url
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", db_url)

    command.upgrade(cfg, "0015")

    from sqlalchemy import Uuid, bindparam, create_engine, text
    from sqlalchemy import inspect as sa_inspect

    engine = create_engine(db_url)
    run_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO run (id, provider, model, operation, prompt, params, status, "
                "queued_at) VALUES (:id, 'fake', 'm', 'generate', 'p', '{}', 'succeeded', "
                "'2026-09-01 00:00:00.000000')"
            ).bindparams(bindparam("id", type_=Uuid())),
            {"id": run_id},
        )
    engine.dispose()

    # 0026 で api_token に列が増えるので、ここでは 0016 までの形を確かめる。
    command.upgrade(cfg, "0016")
    engine = create_engine(db_url)
    inspector = sa_inspect(engine)
    assert "api_token" in set(inspector.get_table_names())
    token_columns = {c["name"] for c in inspector.get_columns("api_token")}
    assert token_columns == {
        "id",
        "user_id",
        "name",
        "token_hash",
        "created_at",
        "last_used_at",
        "revoked_at",
    }
    run_columns = {c["name"] for c in inspector.get_columns("run")}
    assert {"origin", "api_token_id"} <= run_columns
    fks = {fk["name"]: fk for fk in inspector.get_foreign_keys("run")}
    assert fks["fk_run_api_token_id_api_token"]["referred_table"] == "api_token"
    with engine.connect() as conn:
        row = conn.execute(text("SELECT origin, api_token_id FROM run")).one()
    assert tuple(row) == (None, None)
    engine.dispose()

    command.downgrade(cfg, "0015")
    engine = create_engine(db_url)
    inspector = sa_inspect(engine)
    assert "api_token" not in set(inspector.get_table_names())
    run_columns = {c["name"] for c in inspector.get_columns("run")}
    assert "origin" not in run_columns
    assert "api_token_id" not in run_columns
    engine.dispose()


def test_migration_upgrades_from_0016_to_0017_adds_upload_ticket(empty_database_url: str) -> None:
    """既存の0016適用済みDBでも、起動時のupgrade headで0017(upload_ticket)が当たること。
    downgrade も通ること(ADR-0023 7章 2)。
    """
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine
    from sqlalchemy import inspect as sa_inspect

    from app.main import _MIGRATIONS_DIR

    db_url = empty_database_url
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", db_url)

    command.upgrade(cfg, "0016")
    engine = create_engine(db_url)
    assert "upload_ticket" not in set(sa_inspect(engine).get_table_names())
    engine.dispose()

    command.upgrade(cfg, "head")
    engine = create_engine(db_url)
    inspector = sa_inspect(engine)
    assert "upload_ticket" in set(inspector.get_table_names())
    columns = {c["name"] for c in inspector.get_columns("upload_ticket")}
    assert columns == {
        "id",
        "token_hash",
        "user_id",
        "api_token_id",
        "created_at",
        "expires_at",
        "used_at",
        "asset_id",
    }
    fks = {fk["name"]: fk["referred_table"] for fk in inspector.get_foreign_keys("upload_ticket")}
    assert fks == {
        "fk_upload_ticket_user_id_app_user": "app_user",
        "fk_upload_ticket_api_token_id_api_token": "api_token",
        "fk_upload_ticket_asset_id_asset": "asset",
    }
    uniques = inspector.get_unique_constraints("upload_ticket")
    assert any(u["column_names"] == ["token_hash"] for u in uniques)
    engine.dispose()

    command.downgrade(cfg, "0016")
    engine = create_engine(db_url)
    assert "upload_ticket" not in set(sa_inspect(engine).get_table_names())
    engine.dispose()


def test_migration_upgrades_from_0017_to_0018_adds_prompt_set_created_by(
    empty_database_url: str,
) -> None:
    """既存の0017適用済みDBでも、起動時のupgrade headで0018(prompt_set.created_by_user_id)
    が当たり、既存行は null のまま残ること。downgrade も通ること(ADR-0025)。
    """
    import uuid

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import Uuid, bindparam, create_engine, text
    from sqlalchemy import inspect as sa_inspect

    from app.main import _MIGRATIONS_DIR

    db_url = empty_database_url
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", db_url)

    command.upgrade(cfg, "0017")
    engine = create_engine(db_url)
    columns_after_0017 = {c["name"] for c in sa_inspect(engine).get_columns("prompt_set")}
    assert "created_by_user_id" not in columns_after_0017
    insert = text(
        "INSERT INTO prompt_set (id, name, created_at, updated_at) "
        "VALUES (:id, 'old', '2026-09-01 00:00:00.000000', '2026-09-01 00:00:00.000000')"
    ).bindparams(bindparam("id", type_=Uuid()))
    with engine.begin() as conn:
        conn.execute(insert, {"id": uuid.uuid4()})
    engine.dispose()

    command.upgrade(cfg, "head")
    engine = create_engine(db_url)
    inspector = sa_inspect(engine)
    columns = {c["name"]: c for c in inspector.get_columns("prompt_set")}
    assert columns["created_by_user_id"]["nullable"] is True
    assert "ix_prompt_set_created_by_user_id" in {
        i["name"] for i in inspector.get_indexes("prompt_set")
    }
    fks = {fk["name"]: fk["referred_table"] for fk in inspector.get_foreign_keys("prompt_set")}
    assert fks["fk_prompt_set_created_by_user_id_app_user"] == "app_user"
    with engine.connect() as conn:
        assert conn.execute(text("SELECT created_by_user_id FROM prompt_set")).all() == [(None,)]
    engine.dispose()

    command.downgrade(cfg, "0017")
    engine = create_engine(db_url)
    columns_after_downgrade = {c["name"] for c in sa_inspect(engine).get_columns("prompt_set")}
    assert "created_by_user_id" not in columns_after_downgrade
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM prompt_set")).scalar_one() == 1
    engine.dispose()


def test_migration_upgrades_from_0018_to_0019_adds_annotation_tables(
    empty_database_url: str,
) -> None:
    """既存の0018適用済みDBでも、起動時のupgrade headで0019(asset_annotation、tag、asset_tag)
    が当たること。downgrade でテーブルが消えること(ADR-0024)。
    """
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine
    from sqlalchemy import inspect as sa_inspect

    from app.main import _MIGRATIONS_DIR

    db_url = empty_database_url
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", db_url)
    new_tables = {"asset_annotation", "tag", "asset_tag"}

    command.upgrade(cfg, "0018")
    engine = create_engine(db_url)
    assert not new_tables & set(sa_inspect(engine).get_table_names())
    engine.dispose()

    command.upgrade(cfg, "head")
    engine = create_engine(db_url)
    assert new_tables <= set(sa_inspect(engine).get_table_names())
    engine.dispose()

    command.downgrade(cfg, "0018")
    engine = create_engine(db_url)
    tables = set(sa_inspect(engine).get_table_names())
    assert not new_tables & tables
    assert "upload_ticket" in tables
    engine.dispose()


def test_migration_downgrade_to_base_and_upgrade_again(empty_database_url: str) -> None:
    """ADR-0027 6章: 両方の DB で、head → base → head と通ること。"""
    from alembic import command
    from sqlalchemy import create_engine
    from sqlalchemy import inspect as sa_inspect

    from app.main import alembic_config

    cfg = alembic_config(empty_database_url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    engine = create_engine(empty_database_url)
    assert set(sa_inspect(engine).get_table_names()) <= {"alembic_version"}
    engine.dispose()

    command.upgrade(cfg, "head")
    engine = create_engine(empty_database_url)
    assert {"asset", "run", "tag"} <= set(sa_inspect(engine).get_table_names())
    engine.dispose()


@requires_postgresql
def test_migration_0020_makes_json_jsonb_and_user_strings_text_on_postgresql(
    pg_empty_database_url: str,
) -> None:
    """ADR-0027 2章: PostgreSQL では 0020 で JSON → JSONB、利用者や外部から来る文字列 →
    TEXT に変わり、既存の値はそのまま残ること。コードが決める値は VARCHAR(n) のまま。
    downgrade で元の型に戻ること。"""
    import uuid

    from alembic import command
    from sqlalchemy import Uuid, bindparam, create_engine, text
    from sqlalchemy import inspect as sa_inspect

    from app.main import alembic_config

    url = pg_empty_database_url
    cfg = alembic_config(url)
    command.upgrade(cfg, "0019")

    engine = create_engine(url)
    run_id = uuid.uuid4()
    long_model = "m" * 100  # 0019 までの VARCHAR(64) には入らない長さ(0020 の後で使う)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO run (id, provider, model, operation, prompt, params, status, "
                "queued_at) VALUES (:id, 'fake', 'm', 'generate', 'p', "
                """'{"n": 1, "nested": {"a": [1, 2]}}', 'succeeded', now())"""
            ).bindparams(bindparam("id", type_=Uuid())),
            {"id": run_id},
        )
    engine.dispose()

    def column_types(table: str) -> dict[str, str]:
        engine = create_engine(url)
        try:
            return {
                c["name"]: str(c["type"]).upper() for c in sa_inspect(engine).get_columns(table)
            }
        finally:
            engine.dispose()

    assert column_types("run")["params"] == "JSON"
    assert column_types("run")["prompt"] == "VARCHAR(32000)"

    command.upgrade(cfg, "head")
    run_types = column_types("run")
    assert run_types["params"] == "JSONB"
    assert run_types["usage"] == "JSONB"
    assert run_types["prompt"] == "TEXT"
    assert run_types["model"] == "TEXT"
    assert run_types["error_message"] == "TEXT"
    # コードが決める値は VARCHAR(n) のまま。
    assert run_types["status"] == "VARCHAR(16)"
    assert run_types["error_code"] == "VARCHAR(32)"
    assert column_types("asset")["embedded_meta"] == "JSONB"
    assert column_types("asset")["blob_key"] == "TEXT"
    assert column_types("asset")["sha256"] == "VARCHAR(64)"
    assert column_types("tag")["name"] == "TEXT"
    assert column_types("app_user")["email"] == "TEXT"
    assert column_types("app_setting")["key"] == "VARCHAR(100)"
    assert column_types("comfy_workflow")["template"] == "JSONB"

    engine = create_engine(url)
    with engine.begin() as conn:
        params = conn.execute(
            text("SELECT params FROM run WHERE id = :id").bindparams(bindparam("id", type_=Uuid())),
            {"id": run_id},
        ).scalar_one()
        assert params == {"n": 1, "nested": {"a": [1, 2]}}
        # JSONB は等値比較と DISTINCT ができる(json 型ではできない)。
        assert conn.execute(text("SELECT count(DISTINCT params) FROM run")).scalar_one() == 1
        conn.execute(text("UPDATE run SET model = :m"), {"m": long_model})
        conn.execute(text("UPDATE run SET model = 'm'"))
    engine.dispose()

    command.downgrade(cfg, "0019")
    run_types = column_types("run")
    assert run_types["params"] == "JSON"
    assert run_types["prompt"] == "VARCHAR(32000)"
    assert run_types["model"] == "VARCHAR(64)"
    assert column_types("tag")["name"] == "VARCHAR(100)"


def test_migration_0020_is_noop_on_sqlite(tmp_path) -> None:  # noqa: ANN001
    """ADR-0027 2章: SQLite では 0020 は何もしない(型の長さも JSONB も無い)。"""
    from alembic import command
    from sqlalchemy import create_engine
    from sqlalchemy import inspect as sa_inspect

    from app.main import alembic_config

    url = f"sqlite:///{tmp_path / 'noop.db'}"
    cfg = alembic_config(url)
    command.upgrade(cfg, "0019")
    engine = create_engine(url)
    before = {c["name"]: str(c["type"]) for c in sa_inspect(engine).get_columns("run")}
    engine.dispose()
    # 後のマイグレーション(0023 の run.text_outputs など)の列を混ぜないよう 0020 で止める。
    command.upgrade(cfg, "0020")
    engine = create_engine(url)
    after = {c["name"]: str(c["type"]) for c in sa_inspect(engine).get_columns("run")}
    engine.dispose()
    assert before == after
    command.downgrade(cfg, "0019")


def test_migration_upgrades_from_0020_to_0021_adds_download_ticket(
    empty_database_url: str,
) -> None:
    """既存の0020適用済みDBでも、起動時のupgrade headで0021(download_ticket)が当たること。
    downgrade も通ること(ADR-0023 8章 3)。
    """
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine
    from sqlalchemy import inspect as sa_inspect

    from app.main import _MIGRATIONS_DIR

    db_url = empty_database_url
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", db_url)

    command.upgrade(cfg, "0020")
    engine = create_engine(db_url)
    assert "download_ticket" not in set(sa_inspect(engine).get_table_names())
    engine.dispose()

    command.upgrade(cfg, "head")
    engine = create_engine(db_url)
    inspector = sa_inspect(engine)
    assert "download_ticket" in set(inspector.get_table_names())
    columns = {c["name"] for c in inspector.get_columns("download_ticket")}
    assert columns == {
        "id",
        "token_hash",
        "asset_id",
        "user_id",
        "api_token_id",
        "created_at",
        "expires_at",
        "used_at",
    }
    fks = {fk["name"]: fk["referred_table"] for fk in inspector.get_foreign_keys("download_ticket")}
    assert fks == {
        "fk_download_ticket_asset_id_asset": "asset",
        "fk_download_ticket_user_id_app_user": "app_user",
        "fk_download_ticket_api_token_id_api_token": "api_token",
    }
    uniques = inspector.get_unique_constraints("download_ticket")
    assert any(u["column_names"] == ["token_hash"] for u in uniques)
    engine.dispose()

    command.downgrade(cfg, "0020")
    engine = create_engine(db_url)
    assert "download_ticket" not in set(sa_inspect(engine).get_table_names())
    engine.dispose()


def test_migration_upgrades_from_0021_to_0022_adds_share_tables(empty_database_url: str) -> None:
    """0021 適用済みの DB に 0022(share / share_asset。ADR-0029)が当たり、downgrade も通ること。"""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine
    from sqlalchemy import inspect as sa_inspect

    from app.main import _MIGRATIONS_DIR

    db_url = empty_database_url
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", db_url)

    command.upgrade(cfg, "0021")
    engine = create_engine(db_url)
    assert "share" not in set(sa_inspect(engine).get_table_names())
    engine.dispose()

    command.upgrade(cfg, "head")
    engine = create_engine(db_url)
    inspector = sa_inspect(engine)
    assert {"share", "share_asset"} <= set(inspector.get_table_names())
    assert {c["name"] for c in inspector.get_columns("share")} == {
        "id",
        "token",
        "root_asset_id",
        "scope",
        "allow_original",
        "created_by_user_id",
        "created_at",
        "revoked_at",
        "last_accessed_at",
        "access_count",
    }
    assert {c["name"] for c in inspector.get_columns("share_asset")} == {
        "share_id",
        "asset_id",
        "depth",
    }
    uniques = inspector.get_unique_constraints("share")
    assert any(u["column_names"] == ["token"] for u in uniques)
    engine.dispose()

    command.downgrade(cfg, "0021")
    engine = create_engine(db_url)
    tables = set(sa_inspect(engine).get_table_names())
    assert "share" not in tables and "share_asset" not in tables
    engine.dispose()


def test_migration_0023_adds_run_text_outputs(tmp_path) -> None:  # noqa: ANN001
    """ADR-0030: 0023 で run.text_outputs(JSON、null 可)を足す。既存の Run は null のまま。"""
    from alembic import command
    from sqlalchemy import create_engine, text
    from sqlalchemy import inspect as sa_inspect

    from app.main import alembic_config

    url = f"sqlite:///{tmp_path / 'text_outputs.db'}"
    cfg = alembic_config(url)
    command.upgrade(cfg, "0022")
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO run (id, provider, model, operation, prompt, params, status, "
                "queued_at) VALUES ('00000000000000000000000000000001', 'openai', 'm', "
                "'generate', 'p', '{}', 'succeeded', '2026-09-30 00:00:00')"
            )
        )
    engine.dispose()

    command.upgrade(cfg, "head")
    engine = create_engine(url)
    columns = {c["name"]: c for c in sa_inspect(engine).get_columns("run")}
    assert "text_outputs" in columns
    assert columns["text_outputs"]["nullable"] is True
    with engine.connect() as conn:
        assert conn.execute(text("SELECT text_outputs FROM run")).scalar_one() is None
    engine.dispose()

    command.downgrade(cfg, "0022")
    engine = create_engine(url)
    assert "text_outputs" not in {c["name"] for c in sa_inspect(engine).get_columns("run")}
    engine.dispose()


def test_migration_0026_adds_api_token_expiry_and_scope(empty_database_url: str) -> None:
    """ADR-0023 11章: 0026 で api_token.expires_at(null 可)と scope(既定 'full')を足す。
    既存のトークンは無期限・'full' のまま。downgrade もできること。"""
    import uuid

    from alembic import command
    from sqlalchemy import Uuid, bindparam, create_engine, text
    from sqlalchemy import inspect as sa_inspect

    from app.main import alembic_config

    cfg = alembic_config(empty_database_url)
    command.upgrade(cfg, "0025")
    engine = create_engine(empty_database_url)
    user_id = uuid.uuid4()
    token_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO app_user (id, issuer, subject, email, name, role, created_at, "
                "last_login_at) VALUES (:id, 'https://idp.example', 'sub-1', "
                "'old@example.com', 'Old', 'user', '2026-09-01 00:00:00', '2026-09-01 00:00:00')"
            ).bindparams(bindparam("id", type_=Uuid())),
            {"id": user_id},
        )
        conn.execute(
            text(
                "INSERT INTO api_token (id, user_id, name, token_hash, created_at) "
                "VALUES (:id, :user_id, 'old', :hash, '2026-09-01 00:00:00')"
            ).bindparams(bindparam("id", type_=Uuid()), bindparam("user_id", type_=Uuid())),
            {"id": token_id, "user_id": user_id, "hash": "0" * 64},
        )
    engine.dispose()

    command.upgrade(cfg, "head")
    engine = create_engine(empty_database_url)
    columns = {c["name"]: c for c in sa_inspect(engine).get_columns("api_token")}
    assert columns["expires_at"]["nullable"] is True
    assert columns["scope"]["nullable"] is False
    with engine.connect() as conn:
        row = conn.execute(text("SELECT expires_at, scope FROM api_token")).one()
    assert tuple(row) == (None, "full")
    engine.dispose()

    command.downgrade(cfg, "0025")
    engine = create_engine(empty_database_url)
    columns = {c["name"] for c in sa_inspect(engine).get_columns("api_token")}
    assert "expires_at" not in columns
    assert "scope" not in columns
    engine.dispose()


def test_migration_0027_adds_run_import(empty_database_url: str) -> None:
    """ADR-0037: 0026 の DB に upgrade head で run_import が加わり、downgrade で消えること。"""
    from alembic import command
    from sqlalchemy import create_engine
    from sqlalchemy import inspect as sa_inspect

    from app.main import alembic_config

    cfg = alembic_config(empty_database_url)
    command.upgrade(cfg, "0026")
    engine = create_engine(empty_database_url)
    assert "run_import" not in set(sa_inspect(engine).get_table_names())
    engine.dispose()

    command.upgrade(cfg, "head")
    engine = create_engine(empty_database_url)
    inspector = sa_inspect(engine)
    assert "run_import" in set(inspector.get_table_names())
    columns = {c["name"]: c for c in inspector.get_columns("run_import")}
    assert columns["source_run_id"]["nullable"] is False
    assert columns["imported_at"]["nullable"] is False
    assert columns["source_creator_name"]["nullable"] is True
    indexes = {i["name"] for i in inspector.get_indexes("run_import")}
    assert "ix_run_import_source_run_id" in indexes
    engine.dispose()

    command.downgrade(cfg, "0026")
    engine = create_engine(empty_database_url)
    assert "run_import" not in set(sa_inspect(engine).get_table_names())
    engine.dispose()


def test_migration_0028_adds_parameter_set(empty_database_url: str) -> None:
    """ADR-0040: 0027 の DB に upgrade head で parameter_set が加わり、downgrade で消えること。"""
    from alembic import command
    from sqlalchemy import create_engine
    from sqlalchemy import inspect as sa_inspect

    from app.main import alembic_config

    cfg = alembic_config(empty_database_url)
    command.upgrade(cfg, "0027")
    engine = create_engine(empty_database_url)
    assert "parameter_set" not in set(sa_inspect(engine).get_table_names())
    engine.dispose()

    command.upgrade(cfg, "head")
    engine = create_engine(empty_database_url)
    inspector = sa_inspect(engine)
    assert "parameter_set" in set(inspector.get_table_names())
    columns = {c["name"]: c for c in inspector.get_columns("parameter_set")}
    assert columns["name"]["nullable"] is False
    assert columns["provider"]["nullable"] is False
    assert columns["params"]["nullable"] is False
    assert columns["model"]["nullable"] is True
    assert columns["prompt"]["nullable"] is True
    assert columns["created_by_user_id"]["nullable"] is True
    assert columns["deleted_at"]["nullable"] is True
    indexes = {i["name"] for i in inspector.get_indexes("parameter_set")}
    assert {"ix_parameter_set_created_by_user_id", "ix_parameter_set_updated_at"} <= indexes
    foreign_keys = inspector.get_foreign_keys("parameter_set")
    assert [fk["referred_table"] for fk in foreign_keys] == ["app_user"]
    engine.dispose()

    command.downgrade(cfg, "0027")
    engine = create_engine(empty_database_url)
    assert "parameter_set" not in set(sa_inspect(engine).get_table_names())
    engine.dispose()


def test_migration_0029_adds_tag_dictionary_tables(empty_database_url: str) -> None:
    """ADR-0041: 0028 の DB に upgrade head でタグ辞書のテーブルが加わり、downgrade で消えること。
    前方一致の検索に使う主キー(辞書 + 検索用の列)を確かめる。"""
    from alembic import command
    from sqlalchemy import create_engine
    from sqlalchemy import inspect as sa_inspect

    from app.main import alembic_config

    names = {
        "tag_dictionary",
        "tag_dictionary_entry",
        "tag_dictionary_alias",
        "tag_dictionary_translation",
    }
    cfg = alembic_config(empty_database_url)
    command.upgrade(cfg, "0028")
    engine = create_engine(empty_database_url)
    assert not names & set(sa_inspect(engine).get_table_names())
    engine.dispose()

    command.upgrade(cfg, "head")
    engine = create_engine(empty_database_url)
    inspector = sa_inspect(engine)
    assert names <= set(inspector.get_table_names())
    assert inspector.get_pk_constraint("tag_dictionary_entry")["constrained_columns"] == [
        "dictionary_id",
        "name_key",
    ]
    assert inspector.get_pk_constraint("tag_dictionary_alias")["constrained_columns"] == [
        "dictionary_id",
        "alias_key",
        "name_key",
    ]
    assert inspector.get_pk_constraint("tag_dictionary_translation")["constrained_columns"] == [
        "dictionary_id",
        "name_key",
    ]
    columns = {c["name"]: c for c in inspector.get_columns("tag_dictionary")}
    assert columns["category_scheme"]["nullable"] is True
    assert columns["status"]["nullable"] is False
    for table in ("tag_dictionary_entry", "tag_dictionary_alias", "tag_dictionary_translation"):
        foreign_keys = inspector.get_foreign_keys(table)
        assert [fk["referred_table"] for fk in foreign_keys] == ["tag_dictionary"]
    engine.dispose()

    command.downgrade(cfg, "0028")
    engine = create_engine(empty_database_url)
    assert not names & set(sa_inspect(engine).get_table_names())
    engine.dispose()
