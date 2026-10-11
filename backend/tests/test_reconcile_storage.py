"""画像を手で消した後に DB を合わせるツール(ADR-0046)。

ローカルFS と S3(moto)、SQLite と PostgreSQL(`GAKEI_TEST_DATABASE_URL` があるとき)で同じ
テストを回す。
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import ForeignKey, func, select
from sqlalchemy.orm import Session, sessionmaker

import app.tools.reconcile_storage as tool
from app.config import Settings
from app.db import create_all, make_engine, make_session_factory
from app.domain.models import (
    AppSetting,
    AppUser,
    Asset,
    AssetAnnotation,
    AssetEmbedding,
    AssetFocalPoint,
    AssetGroup,
    AssetGroupMember,
    AssetPerceptualHash,
    AssetTag,
    Base,
    DownloadTicket,
    PromptSet,
    Run,
    RunImport,
    RunInput,
    RunPromptEmbedding,
    Share,
    ShareAsset,
    Tag,
    UploadTicket,
)
from app.domain.storage import LocalFsStore, OriginalKeyInfo
from tests.conftest import make_png_bytes, using_postgresql

pytestmark = pytest.mark.windows


@dataclass
class Env:
    settings: Settings
    store: Any
    factory: sessionmaker[Session]


@pytest.fixture(params=["local", "s3"])
def env(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[Env]:
    data_dir = tmp_path / "data"
    # PostgreSQL のときは、conftest が `DATABASE_URL` にテストごとの DB を入れている。
    settings = Settings(_env_file=None, data_dir=data_dir)
    if request.param == "local":
        store: Any = LocalFsStore(data_dir)
    else:
        data_dir.mkdir(parents=True, exist_ok=True)
        store = request.getfixturevalue("s3_store")
    monkeypatch.setattr(tool, "open_store", lambda _settings: store)
    engine = make_engine(settings.sqlalchemy_url)
    create_all(engine)
    try:
        yield Env(settings, store, make_session_factory(engine))
    finally:
        engine.dispose()


def _put(store: Any, key: str, data: bytes) -> None:
    if isinstance(store, LocalFsStore):
        path = store.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    else:
        store.overwrite(key, data)


_seq = iter(range(1, 10_000))


def _asset(
    env: Env,
    db: Session,
    *,
    color: tuple[int, int, int] | None = None,
    kind: str = "generated",
    run: Run | None = None,
    index: int | None = None,
    blob_key: str | None = None,
    sha256: str | None = None,
    deleted: bool = False,
    **columns: Any,
) -> Asset:
    """Asset の行と、原本・派生(今の版と古い版)のファイルを作る。`blob_key` を渡せば、その
    原本を共有する(ファイルは書かない)。"""
    n = next(_seq)
    asset_id = uuid.uuid4()
    created = datetime(2026, 10, 1, tzinfo=UTC) + timedelta(seconds=n)
    if blob_key is None:
        data = make_png_bytes(8, 8, color or (n % 256, (n * 7) % 256, (n * 13) % 256))
        sha256 = sha256 or hashlib.sha256(data).hexdigest()
        blob_key = env.store.write_original(
            data,
            "png",
            OriginalKeyInfo(
                kind=kind,  # type: ignore[arg-type]
                asset_id=asset_id,
                created_at=created,
                provider="fake",
                model="m",
            ),
        )
        env.store.write_derived(sha256, "thumb", b"thumb")
        env.store.write_derived(sha256, "preview", b"preview")
        _put(env.store, f"derived/{sha256}/thumb.v2.webp", b"thumb2")
    assert sha256 is not None
    asset = Asset(
        id=asset_id,
        kind=kind,
        sha256=sha256,
        blob_key=blob_key,
        mime="image/png",
        width=8,
        height=8,
        bytes=10,
        produced_by_run_id=run.id if run is not None else None,
        output_index=index,
        created_at=created,
        deleted_at=created if deleted else None,
        **columns,
    )
    db.add(asset)
    db.flush()
    return asset


def _run(db: Session, *, operation: str = "generate", status: str = "succeeded") -> Run:
    run = Run(
        provider="fake",
        model="m",
        operation=operation,
        prompt="p",
        params={},
        status=status,
    )
    db.add(run)
    db.flush()
    return run


def _input(db: Session, run: Run, asset: Asset, position: int = 0) -> None:
    db.add(RunInput(run_id=run.id, asset_id=asset.id, role="image", position=position))
    db.flush()


def _attach(db: Session, asset: Asset, group: AssetGroup, tag: Tag) -> None:
    """Asset に付く行をひととおり作る。"""
    db.add_all(
        [
            AssetAnnotation(asset_id=asset.id, title="t"),
            AssetTag(asset_id=asset.id, tag_id=tag.id, source="auto"),
            AssetEmbedding(asset_id=asset.id, model_key="fake:x", status="succeeded"),
            AssetPerceptualHash(asset_id=asset.id, dhash=b"\0" * 8, color=b"\0" * 64, version=1),
            AssetFocalPoint(asset_id=asset.id, method="none", version=1),
            AssetGroupMember(asset_group_id=group.id, asset_id=asset.id),
            DownloadTicket(
                token_hash=uuid.uuid4().hex,
                asset_id=asset.id,
                expires_at=datetime.now(UTC),
            ),
            UploadTicket(
                token_hash=uuid.uuid4().hex,
                asset_id=asset.id,
                expires_at=datetime.now(UTC),
            ),
        ]
    )
    db.flush()


def _share(db: Session, root: Asset, members: list[Asset]) -> Share:
    share = Share(token=uuid.uuid4().hex, root_asset_id=root.id, scope="lineage")
    db.add(share)
    db.flush()
    db.add_all(ShareAsset(share_id=share.id, asset_id=m.id) for m in members)
    db.flush()
    return share


@dataclass
class Scenario:
    ids: dict[str, uuid.UUID]
    keys: dict[str, str]
    shas: dict[str, str]


def _build(env: Env) -> Scenario:
    """よく使う形の DB を作る。

    - R1(生成): 出力 A1、A2。R1 にはプロンプトの埋め込みと取り込みの記録がある。
    - R2(Edit): 入力 A1(主たる親)と U1、出力 B1。
    - R3(失敗): 入力 A2、出力なし。
    - U1(アップロード)と U2(同じ原本のキーを共有)、U3(同じ内容で別のキー)。U2 は論理削除済み。
    - S1(スケッチ): 下地は A1。O1(アップロード): 取り込みの元は A2。
    - 共有: SH1 の起点は A2、範囲は A2・A1。SH2 の起点は U3、範囲は A1 だけ。SH3 の起点は B1。
    - A1 には、タイトル・タグ・埋め込み・知覚ハッシュ・焦点・グループなどの行がある。
    - 設定・利用者・プロンプトセット・グループ・タグ(消さないもの)。
    """
    with env.factory() as db:
        user = AppUser(issuer="i", subject="s", email="u@example.com")
        db.add_all([user, AppSetting(key="general", value={"x": 1}), PromptSet(name="ps")])
        group = AssetGroup(name="g")
        tag = Tag(name="cat")
        db.add_all([group, tag])
        db.flush()

        r1 = _run(db)
        a1 = _asset(env, db, run=r1, index=0)
        a2 = _asset(env, db, run=r1, index=1)
        db.add(RunPromptEmbedding(run_id=r1.id, model_key="fake:x", status="succeeded"))
        db.add(RunImport(run_id=r1.id, source_run_id=uuid.uuid4()))

        u1 = _asset(env, db, kind="upload")
        u2 = _asset(env, db, kind="upload", blob_key=u1.blob_key, sha256=u1.sha256, deleted=True)
        r2 = _run(db, operation="edit")
        _input(db, r2, a1, 0)
        _input(db, r2, u1, 1)
        b1 = _asset(env, db, run=r2, index=0)

        r3 = _run(db, operation="edit", status="failed")
        _input(db, r3, a2, 0)

        s1 = _asset(env, db, kind="sketch", source_asset_id=a1.id)
        o1 = _asset(env, db, kind="upload", origin_asset_id=a2.id)

        sh1 = _share(db, a2, [a2, a1])
        u3 = _asset(env, db, kind="upload", color=(1, 2, 3))
        sh2 = _share(db, u3, [a1])
        sh3 = _share(db, b1, [b1])

        _attach(db, a1, group, tag)
        db.commit()
        rows = {
            "r1": r1, "r2": r2, "r3": r3, "a1": a1, "a2": a2, "u1": u1, "u2": u2, "u3": u3,
            "b1": b1, "s1": s1, "o1": o1, "sh1": sh1, "sh2": sh2, "sh3": sh3,
            "user": user, "group": group, "tag": tag,
        }  # fmt: skip
        ids = {name: row.id for name, row in rows.items()}
        keys = {name: row.blob_key for name, row in rows.items() if isinstance(row, Asset)}
        shas = {name: row.sha256 for name, row in rows.items() if isinstance(row, Asset)}
    return Scenario(ids, keys, shas)


def _exists(env: Env, model: Any, id_: uuid.UUID) -> bool:
    with env.factory() as db:
        return db.get(model, id_) is not None


def _count(env: Env, model: Any, **where: Any) -> int:
    with env.factory() as db:
        query = select(func.count()).select_from(model)
        for column, value in where.items():
            query = query.where(getattr(model, column) == value)
        return db.scalar(query) or 0


def _snapshot(env: Env) -> dict[str, int]:
    with env.factory() as db:
        return {
            name: db.scalar(select(func.count()).select_from(table)) or 0
            for name, table in Base.metadata.tables.items()
        }


def _derived(env: Env, sha256: str) -> set[str]:
    return set(env.store.list_keys(f"derived/{sha256}/"))


# --- 外部キーの洗い出し(ADR-0046 Action Item 2) ---------------------------------------


def _foreign_keys_to(target: str) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for table in Base.metadata.tables.values():
        for fk in table.foreign_keys:
            assert isinstance(fk, ForeignKey)
            if fk.target_fullname == target:
                found.add((table.name, fk.parent.name))
    return found


def test_all_foreign_keys_to_asset_and_run_are_handled() -> None:
    """Asset・Run・共有リンクを指す外部キーが、ツールの一覧とちょうど一致する。

    表を足して落ちたら、`reconcile_storage.py` の `ASSET_REFERENCES` / `RUN_REFERENCES` /
    `SHARE_REFERENCES` に扱いを足す(ADR-0046 Consequences)。
    """
    assert _foreign_keys_to("asset.id") == set(tool.ASSET_REFERENCES)
    assert _foreign_keys_to("run.id") == set(tool.RUN_REFERENCES)
    assert _foreign_keys_to("share.id") == set(tool.SHARE_REFERENCES)


# --- dry-run --------------------------------------------------------------------------


def test_dry_run_changes_nothing(env: Env) -> None:
    sc = _build(env)
    env.store.delete(sc.keys["a1"])
    before = _snapshot(env)
    derived_before = _derived(env, sc.shas["a1"])

    result = tool.reconcile(env.settings)

    plan = result.plan
    assert plan.asset_ids == {sc.ids["a1"]}
    assert plan.missing_keys == [sc.keys["a1"]]
    assert plan.run_ids == set()
    assert plan.share_ids == {sc.ids["sh2"]}
    assert plan.derived_sha256 == {sc.shas["a1"]}
    assert plan.runs_without_outputs_total == 1
    assert plan.rows == {
        "asset_annotation": 1,
        "asset_embedding": 1,
        "asset_focal_point": 1,
        "asset_group_member": 1,
        "asset_perceptual_hash": 1,
        "asset_tag": 1,
        "download_ticket": 1,
        "run_input": 1,
        "share_asset": 2,
    }
    assert plan.nullified == {"asset.source_asset_id": 1, "upload_ticket.asset_id": 1}
    assert result.backup is None
    assert _snapshot(env) == before
    assert _derived(env, sc.shas["a1"]) == derived_before
    # 設定なしで、DB と派生は変わらない。
    assert not list(Path(env.settings.data_dir).glob("gakei.db.bak-*"))


# --- 一部の原本が無い -----------------------------------------------------------------


def test_partial_missing(env: Env) -> None:
    sc = _build(env)
    env.store.delete(sc.keys["a1"])

    result = tool.reconcile(env.settings, apply=True)

    assert not _exists(env, Asset, sc.ids["a1"])
    # R1 は A2 が残るので残す。R2 は出力 B1 が残るので残し、入力 A1 の行だけ消える。
    assert _exists(env, Run, sc.ids["r1"])
    assert _exists(env, Run, sc.ids["r2"])
    assert _count(env, RunInput, run_id=sc.ids["r2"]) == 1
    assert _count(env, RunInput, asset_id=sc.ids["u1"]) == 1
    # スケッチの下地は null。
    with env.factory() as db:
        s1 = db.get(Asset, sc.ids["s1"])
        assert s1 is not None and s1.source_asset_id is None
    # Asset に付いた行は消え、グループとタグの表記は残る。
    for model in (
        AssetAnnotation,
        AssetTag,
        AssetEmbedding,
        AssetPerceptualHash,
        AssetFocalPoint,
        AssetGroupMember,
        DownloadTicket,
    ):
        assert _count(env, model, asset_id=sc.ids["a1"]) == 0, model
    assert _count(env, UploadTicket) == 1
    assert _count(env, UploadTicket, asset_id=None) == 1
    assert _exists(env, AssetGroup, sc.ids["group"])
    assert _exists(env, Tag, sc.ids["tag"])
    # 共有: SH1 は A2 が残るので残り、範囲から A1 が外れる。SH2 は範囲が空になるので消える。
    assert _exists(env, Share, sc.ids["sh1"])
    assert _count(env, ShareAsset, share_id=sc.ids["sh1"]) == 1
    assert not _exists(env, Share, sc.ids["sh2"])
    assert _count(env, ShareAsset, share_id=sc.ids["sh2"]) == 0
    assert _exists(env, Share, sc.ids["sh3"])
    # 派生は A1 の分だけ、すべての版を消す。
    assert _derived(env, sc.shas["a1"]) == set()
    assert _derived(env, sc.shas["a2"])
    assert result.derived_deleted == 3
    assert result.derived_failed == []
    # SQLite なら自動でバックアップを取る。
    if using_postgresql():
        assert result.backup is None
    else:
        assert result.backup is not None and result.backup.is_file()
        assert result.backup.name.startswith("gakei.db.bak-")

    # もう一度実行しても、消すものは無い。
    again = tool.reconcile(env.settings, apply=True)
    assert again.plan.is_empty
    assert again.backup is None


def test_all_outputs_gone_deletes_run(env: Env) -> None:
    sc = _build(env)
    env.store.delete(sc.keys["a1"])
    env.store.delete(sc.keys["a2"])

    result = tool.reconcile(env.settings, apply=True)

    assert result.plan.runs_outputs_gone == 1
    assert not _exists(env, Run, sc.ids["r1"])
    assert _count(env, RunPromptEmbedding, run_id=sc.ids["r1"]) == 0
    assert _count(env, RunImport, run_id=sc.ids["r1"]) == 0
    # 出力が一度も無い R3 は、既定では残す(入力 A2 の行は消える)。
    assert _exists(env, Run, sc.ids["r3"])
    assert _count(env, RunInput, run_id=sc.ids["r3"]) == 0
    # 取り込みの元は null。
    with env.factory() as db:
        o1 = db.get(Asset, sc.ids["o1"])
        assert o1 is not None and o1.origin_asset_id is None
    # SH1 は起点が消えるので消える。
    assert not _exists(env, Share, sc.ids["sh1"])
    assert _exists(env, Share, sc.ids["sh3"])


def test_edit_input_only_missing(env: Env) -> None:
    """Edit の入力だけが無い: Run と出力は残り、`run_input` だけ消える。同じ原本を共有する
    Asset も消え、同じ内容でも別のキーが残る Asset は残る(派生も残す)。"""
    sc = _build(env)
    env.store.delete(sc.keys["u1"])

    result = tool.reconcile(env.settings, apply=True)

    assert result.plan.asset_ids == {sc.ids["u1"], sc.ids["u2"]}
    assert result.plan.logically_deleted == 1
    assert result.plan.missing_keys == [sc.keys["u1"]]
    assert _exists(env, Run, sc.ids["r2"])
    assert _exists(env, Asset, sc.ids["b1"])
    with env.factory() as db:
        inputs = db.scalars(select(RunInput.asset_id).where(RunInput.run_id == sc.ids["r2"])).all()
    assert inputs == [sc.ids["a1"]]
    assert _derived(env, sc.shas["u1"]) == set()


def test_shared_content_keeps_derived(env: Env) -> None:
    """同じ内容(sha256)で別のキーの Asset が残るときは、派生を消さない。"""
    sc = _build(env)
    with env.factory() as db:
        other = _asset(
            env,
            db,
            kind="upload",
            blob_key="assets/aa/legacy.png",
            sha256=sc.shas["a2"],
        )
        db.commit()
        other_id = other.id
    # `assets/aa/legacy.png` は保存先に無い。
    result = tool.reconcile(env.settings, apply=True)
    assert result.plan.asset_ids == {other_id}
    assert result.plan.derived_sha256 == set()
    assert _derived(env, sc.shas["a2"])
    assert _exists(env, Asset, sc.ids["a2"])


# --- 全部無い --------------------------------------------------------------------------


def _delete_all_originals(env: Env, sc: Scenario) -> None:
    for key in set(sc.keys.values()):
        env.store.delete(key)


def test_all_missing_keeps_runs_without_outputs_by_default(env: Env) -> None:
    sc = _build(env)
    _delete_all_originals(env, sc)

    result = tool.reconcile(env.settings, apply=True)

    assert _count(env, Asset) == 0
    assert result.plan.runs_without_outputs == 0
    assert result.plan.runs_without_outputs_total == 1
    with env.factory() as db:
        assert db.scalars(select(Run.id)).all() == [sc.ids["r3"]]
    assert _count(env, RunInput) == 0


def test_all_missing_full_reset(env: Env) -> None:
    sc = _build(env)
    _delete_all_originals(env, sc)
    # DB に無い原本のファイル(数えるだけで消さない)。
    _put(env.store, "assets/uploads/stray.png", b"x")

    result = tool.reconcile(env.settings, apply=True, include_runs_without_outputs=True)

    assert result.plan.untracked_files == 1
    assert result.plan.runs_without_outputs == 1
    for model in (
        Asset,
        Run,
        RunInput,
        RunImport,
        RunPromptEmbedding,
        Share,
        ShareAsset,
        AssetAnnotation,
        AssetTag,
        AssetEmbedding,
        AssetPerceptualHash,
        AssetFocalPoint,
        AssetGroupMember,
        DownloadTicket,
    ):
        assert _count(env, model) == 0, model
    # 設定などは残る。
    assert _count(env, AppSetting) == 1
    assert _count(env, AppUser) == 1
    assert _count(env, PromptSet) == 1
    assert _count(env, AssetGroup) == 1
    assert _count(env, Tag) == 1
    assert _count(env, UploadTicket) == 1
    assert env.store.exists("assets/uploads/stray.png")
    assert set(env.store.list_keys("derived/")) == set()


# --- コマンドライン --------------------------------------------------------------------


def test_main_dry_run_and_confirm(
    env: Env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sc = _build(env)
    env.store.delete(sc.keys["a1"])
    monkeypatch.setattr(tool, "get_settings", lambda: env.settings)
    before = _snapshot(env)

    tool.main([])
    out = capsys.readouterr().out
    assert "原本が無い画像: 1 件" in out
    assert "dry-run" in out
    assert _snapshot(env) == before

    # 確認で断れば何も変えない。
    tool.main(["--apply"], read=lambda _prompt: "n")
    out = capsys.readouterr().out
    assert "中止しました" in out
    assert _snapshot(env) == before
    assert not list(Path(env.settings.data_dir).glob("gakei.db.bak-*"))

    # 入力が無い(EOF)ときも断ったとみなす。
    def _eof(_prompt: str) -> str:
        raise EOFError

    tool.main(["--apply"], read=_eof)
    assert _snapshot(env) == before

    tool.main(["--apply"], read=lambda _prompt: "y")
    out = capsys.readouterr().out
    assert not _exists(env, Asset, sc.ids["a1"])
    if using_postgresql():
        assert "pg_dump" in out
    else:
        assert "DB のバックアップ" in out
        assert len(list(Path(env.settings.data_dir).glob("gakei.db.bak-*"))) == 1


def test_main_yes_skips_confirm(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    sc = _build(env)
    env.store.delete(sc.keys["b1"])
    monkeypatch.setattr(tool, "get_settings", lambda: env.settings)

    def _no_prompt(_prompt: str) -> str:
        raise AssertionError("--yes なら確認しない")

    tool.main(["--apply", "--yes"], read=_no_prompt)
    assert not _exists(env, Asset, sc.ids["b1"])
    assert not _exists(env, Run, sc.ids["r2"])
    assert not _exists(env, Share, sc.ids["sh3"])
    # R2 の入力だった A1 と U1 は残る。
    assert _exists(env, Asset, sc.ids["a1"])
    assert _exists(env, Asset, sc.ids["u1"])


def test_main_nothing_to_do(
    env: Env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _build(env)
    monkeypatch.setattr(tool, "get_settings", lambda: env.settings)
    tool.main(["--apply"], read=lambda _prompt: "y")
    assert "消すものはありません" in capsys.readouterr().out
    assert not list(Path(env.settings.data_dir).glob("gakei.db.bak-*"))


def test_missing_database_aborts(tmp_path: Path) -> None:
    if using_postgresql():
        pytest.skip("SQLite のファイルに固有")
    settings = Settings(_env_file=None, data_dir=tmp_path / "nothing")
    with pytest.raises(tool.ReconcileAbortedError):
        tool.reconcile(settings)
