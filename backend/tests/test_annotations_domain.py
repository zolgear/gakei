"""タイトルとタグのドメイン規則(ADR-0024 2章)。HTTP を介さずに確かめる。"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.domain import annotations as ann
from app.domain.assets import ingest
from app.domain.models import AssetAnnotation, AssetKind, AssetTag, Tag
from app.domain.storage import LocalFsStore
from tests.conftest import make_png_bytes


def _asset(db: Session, store: LocalFsStore, kind: AssetKind = AssetKind.UPLOAD, color=(1, 2, 3)):
    asset = ingest(db, store, make_png_bytes(32, 32, color), kind)
    db.commit()
    return asset


def _tags(db: Session, asset_id) -> list[tuple[str, str]]:
    return [(ref.name, ref.source) for ref in ann.tags_for_asset(db, asset_id)]


# -- 正規化 --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Cat  ", "cat"),
        ("Blue   Sky", "blue sky"),
        ("ＡＢＣ", "abc"),  # NFKC で全角英字を半角に
        ("ｶﾀｶﾅ", "カタカナ"),  # 半角カナを全角に
        ("tab\tand\nnewline", "tab and newline"),
        ("long_hair", "long_hair"),  # `_` は人の入力では置き換えない
    ],
)
def test_normalize_tag_name(raw: str, expected: str) -> None:
    assert ann.normalize_tag_name(raw) == expected


def test_normalize_tag_name_rejects_empty_and_too_long() -> None:
    with pytest.raises(ann.TagNameError):
        ann.normalize_tag_name("   ")
    with pytest.raises(ann.TagNameError):
        ann.normalize_tag_name("a" * 101)
    assert ann.normalize_tag_name("a" * 100) == "a" * 100


# -- 人の編集 ------------------------------------------------------------------


def test_set_title_marks_user_and_empty_title_is_kept_as_user(
    db_session_factory: sessionmaker, local_store: LocalFsStore
) -> None:
    with db_session_factory() as db:
        asset = _asset(db, local_store)
        ann.set_title(db, asset, "  夕焼けの海  ")
        db.commit()
        row = db.get(AssetAnnotation, asset.id)
        assert (row.title, row.title_source) == ("夕焼けの海", "user")

        ann.set_title(db, asset, "")
        db.commit()
        row = db.get(AssetAnnotation, asset.id)
        assert (row.title, row.title_source) == (None, "user")

        # 空にしたタイトルは再推定で上書きしない。
        ann.apply_auto_result(db, asset.id, title="自動のタイトル", tags=None)
        db.commit()
        assert db.get(AssetAnnotation, asset.id).title is None


def test_title_too_long_raises(db_session_factory: sessionmaker, local_store: LocalFsStore) -> None:
    with db_session_factory() as db:
        asset = _asset(db, local_store)
        with pytest.raises(ValueError):
            ann.set_title(db, asset, "あ" * 201)


def test_user_title_survives_reestimation(
    db_session_factory: sessionmaker, local_store: LocalFsStore
) -> None:
    with db_session_factory() as db:
        asset = _asset(db, local_store)
        ann.apply_auto_result(db, asset.id, title="自動1", tags=None)
        db.commit()
        assert db.get(AssetAnnotation, asset.id).title_source == "auto"

        ann.apply_auto_result(db, asset.id, title="自動2", tags=None)
        db.commit()
        assert db.get(AssetAnnotation, asset.id).title == "自動2"

        ann.set_title(db, asset, "人のタイトル")
        ann.apply_auto_result(db, asset.id, title="自動3", tags=None)
        db.commit()
        row = db.get(AssetAnnotation, asset.id)
        assert (row.title, row.title_source) == ("人のタイトル", "user")


def test_reestimation_replaces_only_auto_tags_and_keeps_user_tags(
    db_session_factory: sessionmaker, local_store: LocalFsStore
) -> None:
    with db_session_factory() as db:
        asset = _asset(db, local_store)
        ann.apply_auto_result(db, asset.id, title=None, tags=[("cat", 0.9), ("Sky", None)])
        ann.add_tag(db, asset, "お気に入り")
        db.commit()
        assert set(_tags(db, asset.id)) == {
            ("cat", "auto"),
            ("sky", "auto"),
            ("お気に入り", "user"),
        }

        # 再推定: sky が消え dog が増える。人のタグはそのまま。
        ann.apply_auto_result(db, asset.id, title=None, tags=[("dog", 0.8), ("cat", 0.7)])
        db.commit()
        assert set(_tags(db, asset.id)) == {
            ("cat", "auto"),
            ("dog", "auto"),
            ("お気に入り", "user"),
        }
        cat_score = db.execute(
            select(AssetTag.score)
            .join(Tag, Tag.id == AssetTag.tag_id)
            .where(AssetTag.asset_id == asset.id, Tag.name == "cat")
        ).scalar_one()
        assert cat_score == pytest.approx(0.7)

        # tags=None(タグのエンジンが動かなかった)は既存の auto タグを保つ。
        ann.apply_auto_result(db, asset.id, title=None, tags=None)
        db.commit()
        assert ("dog", "auto") in _tags(db, asset.id)


def test_removed_tag_is_not_reattached_by_reestimation(
    db_session_factory: sessionmaker, local_store: LocalFsStore
) -> None:
    with db_session_factory() as db:
        asset = _asset(db, local_store)
        ann.apply_auto_result(db, asset.id, title=None, tags=[("cat", 0.9), ("dog", 0.8)])
        db.commit()
        assert ann.remove_tag(db, asset, "CAT") is True
        db.commit()
        assert _tags(db, asset.id) == [("dog", "auto")]

        row = db.execute(
            select(AssetTag).join(Tag, Tag.id == AssetTag.tag_id).where(Tag.name == "cat")
        ).scalar_one()
        assert (row.removed, row.source) == (True, "user")

        ann.apply_auto_result(db, asset.id, title=None, tags=[("cat", 0.99), ("dog", 0.8)])
        db.commit()
        assert _tags(db, asset.id) == [("dog", "auto")]

        # 消したタグを人が付け直すと、user のタグとして戻る。
        ann.add_tag(db, asset, "cat")
        db.commit()
        assert set(_tags(db, asset.id)) == {("cat", "user"), ("dog", "auto")}

        # 付いていないタグを消そうとすると False。
        assert ann.remove_tag(db, asset, "bird") is False


def test_auto_tag_same_as_user_tag_is_not_duplicated(
    db_session_factory: sessionmaker, local_store: LocalFsStore
) -> None:
    with db_session_factory() as db:
        asset = _asset(db, local_store)
        ann.add_tag(db, asset, "cat")
        ann.apply_auto_result(db, asset.id, title=None, tags=[("Cat", 0.9), ("cat", 0.5)])
        db.commit()
        assert _tags(db, asset.id) == [("cat", "user")]


def test_get_target_asset_rejects_mask_and_deleted(
    db_session_factory: sessionmaker, local_store: LocalFsStore
) -> None:
    import uuid
    from datetime import UTC, datetime

    with db_session_factory() as db:
        mask = _asset(db, local_store, AssetKind.MASK)
        with pytest.raises(ann.AnnotationTargetError) as exc:
            ann.get_target_asset(db, mask.id)
        assert exc.value.kind == "mask"

        deleted = _asset(db, local_store, color=(9, 9, 9))
        deleted.deleted_at = datetime.now(UTC)
        db.commit()
        with pytest.raises(ann.AnnotationTargetError) as exc:
            ann.get_target_asset(db, deleted.id)
        assert exc.value.kind == "deleted"

        with pytest.raises(ann.AnnotationTargetError) as exc:
            ann.get_target_asset(db, uuid.uuid4())
        assert exc.value.kind == "not_found"


def test_backfill_and_pending_count(
    db_session_factory: sessionmaker, local_store: LocalFsStore
) -> None:
    from datetime import UTC, datetime

    with db_session_factory() as db:
        a = _asset(db, local_store, color=(1, 1, 1))
        b = _asset(db, local_store, color=(2, 2, 2))
        _asset(db, local_store, AssetKind.MASK, color=(3, 3, 3))
        deleted = _asset(db, local_store, color=(4, 4, 4))
        deleted.deleted_at = datetime.now(UTC)
        # b はタイトルだけ人が付けた(auto_status は NULL = 未実行)。
        ann.set_title(db, b, "人")
        db.commit()

        assert ann.pending_count(db) == 2
        assert ann.backfill(db) == 2
        db.commit()
        assert ann.pending_count(db) == 0
        assert ann.queued_count(db) == 2
        assert db.get(AssetAnnotation, a.id).auto_status == "queued"
        row_b = db.get(AssetAnnotation, b.id)
        assert (row_b.auto_status, row_b.title, row_b.title_source) == ("queued", "人", "user")
        # 2回目は対象が無い。
        assert ann.backfill(db) == 0


def test_list_tags_counts_exclude_removed_and_deleted(
    db_session_factory: sessionmaker, local_store: LocalFsStore
) -> None:
    from datetime import UTC, datetime

    with db_session_factory() as db:
        a = _asset(db, local_store, color=(1, 1, 1))
        b = _asset(db, local_store, color=(2, 2, 2))
        c = _asset(db, local_store, color=(3, 3, 3))
        for asset in (a, b, c):
            ann.add_tag(db, asset, "cat")
        ann.add_tag(db, a, "catalog")
        ann.add_tag(db, a, "dog")
        ann.remove_tag(db, b, "cat")
        c.deleted_at = datetime.now(UTC)
        db.commit()

        assert ann.list_tags(db, None, 50) == [("cat", 1), ("catalog", 1), ("dog", 1)]
        assert ann.list_tags(db, "CAT", 50) == [("cat", 1), ("catalog", 1)]
        assert ann.list_tags(db, "%", 50) == []
