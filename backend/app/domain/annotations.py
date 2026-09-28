"""Asset のタイトルとタグ(ADR-0024)。人の編集規則、自動推定の結果の反映、待ち行列への投入、
一覧・検索用の問い合わせをまとめる。

タイトルとタグは証跡ではないので更新・削除してよい(ADR-0003、ADR-0024 1章)。来歴の列
(`asset`、`run`、`run_input`)には書き込まない。

人の判断を優先する(ADR-0024 2章):

- タイトルを人が編集すると `title_source = user` になり、以後の再推定で上書きしない。
  空にした場合も `title = NULL`・`title_source = user` として残す。
- 人が足したタグは `source = user`。
- 人が消したタグは行を残して `removed = True`・`source = user` にする。同じタグを再推定で
  付け直さない。
- 再推定では `auto` のタイトルと、`removed` でない `auto` のタグだけを入れ替える。`user` の
  行(`removed` を含む)と同じ名前のタグは付けない。

呼び出し側がトランザクション(commit)を制御できるよう、ここでは `flush` までに留める。
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session

from app.domain import annotation_settings
from app.domain.models import Asset, AssetAnnotation, AssetKind, AssetTag, Tag
from app.domain.schemas import AnnotationStatusView, AssetAnnotationResponse, AssetTagRef
from app.i18n import t

if TYPE_CHECKING:
    from app.config import Settings

TAG_NAME_MAX = 100
TITLE_MAX = 200

SOURCE_AUTO = "auto"
SOURCE_USER = "user"

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"

ENGINE_LLM = "llm"
ENGINE_VLM = "vlm"
ENGINE_ONNX = "onnx"

_WHITESPACE_RE = re.compile(r"\s+")
_LIKE_ESCAPE_CHAR = "\\"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class TagNameError(ValueError):
    """タグ名が空、または長すぎる(API 層で 422 にする)。"""


class AnnotationTargetError(Exception):
    """編集・推定の対象にできない Asset。`kind` で API 層がステータスを決める。"""

    def __init__(self, kind: Literal["not_found", "mask", "deleted"], message: str) -> None:
        super().__init__(message)
        self.kind = kind


# -- 正規化 --------------------------------------------------------------------


def normalize_tag_name(raw: str) -> str:
    """NFKC、前後の空白除去、小文字化、空白の連続を1つに。空・100文字超は TagNameError。"""
    name = unicodedata.normalize("NFKC", raw)
    name = _WHITESPACE_RE.sub(" ", name).strip().lower()
    if not name:
        raise TagNameError(t("annotations.tagEmpty"))
    if len(name) > TAG_NAME_MAX:
        raise TagNameError(t("annotations.tagTooLong", max=TAG_NAME_MAX))
    return name


def normalize_tag_name_or_none(raw: str) -> str | None:
    """自動推定の結果向け。使えない名前は例外にせず None(捨てる)。"""
    try:
        return normalize_tag_name(raw)
    except TagNameError:
        return None


def normalize_title(raw: str | None) -> str | None:
    """前後の空白を除き、改行などの空白の連続を1つにする。空なら None。長すぎれば TagNameError
    ではなく ValueError(API 層で 422)。"""
    if raw is None:
        return None
    title = _WHITESPACE_RE.sub(" ", unicodedata.normalize("NFKC", raw)).strip()
    if not title:
        return None
    if len(title) > TITLE_MAX:
        raise ValueError(t("annotations.titleTooLong", max=TITLE_MAX))
    return title


# -- 対象の検査と行の取得 --------------------------------------------------------


def get_target_asset(db: Session, asset_id: uuid.UUID) -> Asset:
    """編集・推定の対象にできる Asset を返す(マスク・削除済みは不可)。"""
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise AnnotationTargetError("not_found", t("assets.notFound"))
    if asset.kind == AssetKind.MASK:
        raise AnnotationTargetError("mask", t("annotations.maskNotSupported"))
    if asset.deleted_at is not None:
        raise AnnotationTargetError("deleted", t("annotations.assetDeleted"))
    return asset


def _get_or_create_annotation(db: Session, asset_id: uuid.UUID) -> AssetAnnotation:
    row = db.get(AssetAnnotation, asset_id)
    if row is None:
        row = AssetAnnotation(asset_id=asset_id, updated_at=_utcnow())
        db.add(row)
        db.flush()
    return row


def _get_or_create_tag(db: Session, name: str) -> Tag:
    tag = db.execute(select(Tag).where(Tag.name == name)).scalar_one_or_none()
    if tag is None:
        tag = Tag(name=name)
        db.add(tag)
        db.flush()
    return tag


def _asset_tag_rows(db: Session, asset_id: uuid.UUID) -> list[tuple[AssetTag, str]]:
    return list(
        db.execute(
            select(AssetTag, Tag.name)
            .join(Tag, Tag.id == AssetTag.tag_id)
            .where(AssetTag.asset_id == asset_id)
        )
        .tuples()
        .all()
    )


# -- 人の編集 ------------------------------------------------------------------


def set_title(db: Session, asset: Asset, title: str | None) -> None:
    """人がタイトルを編集する。空(None)でも `title_source = user` として残す。"""
    normalized = normalize_title(title)
    row = _get_or_create_annotation(db, asset.id)
    row.title = normalized
    row.title_source = SOURCE_USER
    row.updated_at = _utcnow()
    db.flush()


def add_tag(db: Session, asset: Asset, raw_name: str) -> None:
    """人がタグを足す。既存の行(自動、人が消したもの)も `user`・`removed = False` にする。"""
    name = normalize_tag_name(raw_name)
    tag = _get_or_create_tag(db, name)
    row = db.get(AssetTag, (asset.id, tag.id))
    if row is None:
        db.add(
            AssetTag(
                asset_id=asset.id, tag_id=tag.id, source=SOURCE_USER, score=None, removed=False
            )
        )
    else:
        row.source = SOURCE_USER
        row.score = None
        row.removed = False
    _touch(db, asset.id)
    db.flush()


def remove_tag(db: Session, asset: Asset, raw_name: str) -> bool:
    """人がタグを消す。行は残して `removed = True`・`source = user` にする(再推定で付け直さない)。

    付いていない(または既に消した)タグなら False。
    """
    name = normalize_tag_name(raw_name)
    row = db.execute(
        select(AssetTag)
        .join(Tag, Tag.id == AssetTag.tag_id)
        .where(AssetTag.asset_id == asset.id, Tag.name == name)
    ).scalar_one_or_none()
    if row is None or row.removed:
        return False
    row.removed = True
    row.source = SOURCE_USER
    _touch(db, asset.id)
    db.flush()
    return True


def _touch(db: Session, asset_id: uuid.UUID) -> None:
    row = _get_or_create_annotation(db, asset_id)
    row.updated_at = _utcnow()


# -- 自動推定の結果の反映 ----------------------------------------------------------


def apply_auto_result(
    db: Session,
    asset_id: uuid.UUID,
    *,
    title: str | None,
    tags: list[tuple[str, float | None]] | None,
) -> None:
    """推定の結果を反映する(再推定の規則はモジュールの docstring)。

    `title=None` はタイトルを推定しなかった(既存の auto タイトルは保つ)。`tags=None` は
    タグのエンジンが動かなかった(既存の auto タグは保つ)。`tags=[]` は「タグ無し」の結果で、
    既存の auto タグを外す。
    """
    row = _get_or_create_annotation(db, asset_id)
    if title is not None and row.title_source != SOURCE_USER:
        normalized_title = normalize_title(title[:TITLE_MAX])
        if normalized_title is not None:
            row.title = normalized_title
            row.title_source = SOURCE_AUTO

    if tags is not None:
        # 名前を正規化して重複を除く(先に出たもの=確信度の高いものを残す)。
        wanted: dict[str, float | None] = {}
        for raw_name, score in tags:
            name = normalize_tag_name_or_none(raw_name)
            if name is not None and name not in wanted:
                wanted[name] = score

        existing = _asset_tag_rows(db, asset_id)
        user_names = {name for asset_tag, name in existing if asset_tag.source == SOURCE_USER}
        auto_rows = {
            name: asset_tag
            for asset_tag, name in existing
            if asset_tag.source == SOURCE_AUTO and not asset_tag.removed
        }
        for name, asset_tag in auto_rows.items():
            if name in wanted:
                asset_tag.score = wanted[name]
            else:
                db.delete(asset_tag)
        for name, score in wanted.items():
            if name in user_names or name in auto_rows:
                continue
            tag = _get_or_create_tag(db, name)
            db.add(
                AssetTag(
                    asset_id=asset_id, tag_id=tag.id, source=SOURCE_AUTO, score=score, removed=False
                )
            )

    row.updated_at = _utcnow()
    db.flush()


def copy_auto_title_from_sibling(db: Session, asset: Asset) -> str | None:
    """同じ Run の他の出力に auto のタイトルが付いていれば、それを返す(1 Run 1回の LLM 呼び出し。
    ADR-0024 3章)。"""
    if asset.produced_by_run_id is None:
        return None
    return db.execute(
        select(AssetAnnotation.title)
        .join(Asset, Asset.id == AssetAnnotation.asset_id)
        .where(
            Asset.produced_by_run_id == asset.produced_by_run_id,
            Asset.id != asset.id,
            AssetAnnotation.title_source == SOURCE_AUTO,
            AssetAnnotation.title.is_not(None),
        )
        .limit(1)
    ).scalar_one_or_none()


# -- エンジンの有無と待ち行列 --------------------------------------------------------


def usable_engines(config: annotation_settings.AnnotationConfig, settings: Settings) -> list[str]:
    """設定で有効にしていて、いま使えるエンジン。ONNX はモデルをダウンロード済みのときだけ
    (`FAKE_PROVIDER=1` ではダミーを使うのでダウンロード不要)。"""
    from app.annotation.wd_models import is_downloaded

    engines: list[str] = []
    if config.llm_enabled:
        engines.append(ENGINE_LLM)
    if config.vlm_enabled:
        engines.append(ENGINE_VLM)
    if config.onnx_enabled and (
        settings.fake_provider or is_downloaded(settings.data_dir, config.onnx_model)
    ):
        engines.append(ENGINE_ONNX)
    return engines


def _queue(row: AssetAnnotation, now: datetime) -> None:
    row.auto_status = STATUS_QUEUED
    row.auto_error = None
    row.auto_requested_at = now
    row.auto_finished_at = None
    row.updated_at = now


def request_annotation(db: Session, asset: Asset) -> None:
    """1枚の(再)推定を待ち行列に入れる。既に queued / running なら何もしない。"""
    row = _get_or_create_annotation(db, asset.id)
    if row.auto_status in (STATUS_QUEUED, STATUS_RUNNING):
        return
    _queue(row, _utcnow())
    db.flush()


def enqueue_on_ingest(db: Session, asset: Asset, settings: Settings) -> bool:
    """取り込み時の自動実行(ADR-0024 4章)。設定がオフ、使えるエンジンが無い、マスクなら何も
    しない。待ち行列に入れたら True(呼び出し側は commit 後に worker を起こす)。"""
    if asset.kind == AssetKind.MASK or asset.deleted_at is not None:
        return False
    config = annotation_settings.load(db)
    if not config.auto_on_ingest or not usable_engines(config, settings):
        return False
    request_annotation(db, asset)
    return True


def _pending_condition() -> Any:
    """未実行(注釈の行が無い、または auto_status が NULL)・削除済みでない・マスク以外。"""
    return and_(
        Asset.deleted_at.is_(None),
        Asset.kind != AssetKind.MASK,
        or_(AssetAnnotation.asset_id.is_(None), AssetAnnotation.auto_status.is_(None)),
    )


def pending_count(db: Session) -> int:
    return db.execute(
        select(func.count())
        .select_from(Asset)
        .outerjoin(AssetAnnotation, AssetAnnotation.asset_id == Asset.id)
        .where(_pending_condition())
    ).scalar_one()


def queued_count(db: Session) -> int:
    return db.execute(
        select(func.count())
        .select_from(AssetAnnotation)
        .where(AssetAnnotation.auto_status.in_((STATUS_QUEUED, STATUS_RUNNING)))
    ).scalar_one()


def backfill(db: Session) -> int:
    """未実行の Asset をまとめて待ち行列に入れ、件数を返す(ADR-0024 4章「一括実行」)。"""
    rows = db.execute(
        select(Asset.id, AssetAnnotation)
        .outerjoin(AssetAnnotation, AssetAnnotation.asset_id == Asset.id)
        .where(_pending_condition())
        .order_by(Asset.created_at.asc())
    ).all()
    now = _utcnow()
    for asset_id, annotation in rows:
        if annotation is None:
            annotation = AssetAnnotation(asset_id=asset_id, updated_at=now)
            db.add(annotation)
        _queue(annotation, now)
    db.flush()
    return len(rows)


# -- 画面・API 向けの読み出し ------------------------------------------------------


def tags_for_asset(db: Session, asset_id: uuid.UUID) -> list[AssetTagRef]:
    """付いているタグ(removed を除く)。人のタグを先に、その中は名前順、自動は確信度順。"""
    rows = _asset_tag_rows(db, asset_id)
    visible = [(asset_tag, name) for asset_tag, name in rows if not asset_tag.removed]
    visible.sort(
        key=lambda pair: (
            0 if pair[0].source == SOURCE_USER else 1,
            -(pair[0].score or 0.0),
            pair[1],
        )
    )
    return [AssetTagRef(name=name, source=asset_tag.source) for asset_tag, name in visible]


def annotation_status(row: AssetAnnotation | None) -> AnnotationStatusView | None:
    if row is None or row.auto_status is None:
        return None
    return AnnotationStatusView(
        status=row.auto_status,
        error=row.auto_error,
        requested_at=row.auto_requested_at,
        finished_at=row.auto_finished_at,
    )


def annotation_fields(db: Session, asset_id: uuid.UUID) -> dict[str, Any]:
    """`AssetDetail` / `AssetAnnotationResponse` に渡す title・title_source・tags・annotation。"""
    row = db.get(AssetAnnotation, asset_id)
    return {
        "title": row.title if row else None,
        "title_source": row.title_source if row else None,
        "tags": tags_for_asset(db, asset_id),
        "annotation": annotation_status(row),
    }


def annotation_response(db: Session, asset_id: uuid.UUID) -> AssetAnnotationResponse:
    return AssetAnnotationResponse(asset_id=asset_id, **annotation_fields(db, asset_id))


def bulk_titles(db: Session, asset_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, str]:
    """asset_id ごとのタイトル(一覧・検索・履歴のカード用。N+1 を避ける)。"""
    ids = list(set(asset_ids))
    if not ids:
        return {}
    rows = db.execute(
        select(AssetAnnotation.asset_id, AssetAnnotation.title).where(
            AssetAnnotation.asset_id.in_(ids), AssetAnnotation.title.is_not(None)
        )
    ).all()
    return {asset_id: title for asset_id, title in rows}


def bulk_tags(db: Session, asset_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, list[AssetTagRef]]:
    """asset_id ごとのタグ(removed を除く)。MCP の検索結果用。"""
    ids = list(set(asset_ids))
    result: dict[uuid.UUID, list[AssetTagRef]] = {i: [] for i in ids}
    if not ids:
        return result
    rows = db.execute(
        select(AssetTag.asset_id, AssetTag.source, AssetTag.score, Tag.name)
        .join(Tag, Tag.id == AssetTag.tag_id)
        .where(AssetTag.asset_id.in_(ids), AssetTag.removed.is_(False))
    ).all()
    grouped: dict[uuid.UUID, list[tuple[str, float | None, str]]] = {i: [] for i in ids}
    for asset_id, source, score, name in rows:
        grouped[asset_id].append((source, score, name))
    for asset_id, items in grouped.items():
        items.sort(key=lambda x: (0 if x[0] == SOURCE_USER else 1, -(x[1] or 0.0), x[2]))
        result[asset_id] = [AssetTagRef(name=name, source=source) for source, _, name in items]
    return result


def tag_filter(raw_name: str) -> Any:
    """`Asset` にそのタグ(removed でない)が付いている、という WHERE 句。"""
    name = normalize_tag_name(raw_name)
    return exists().where(
        AssetTag.asset_id == Asset.id,
        AssetTag.removed.is_(False),
        AssetTag.tag_id == Tag.id,
        Tag.name == name,
    )


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def tag_name_matches(pattern: str) -> Any:
    """`Asset` に、名前が LIKE `pattern` に当たるタグ(removed でない)が付いている WHERE 句。
    `pattern` はエスケープ済みで、小文字化してあること(タグ名は小文字で保存している)。"""
    return exists().where(
        AssetTag.asset_id == Asset.id,
        AssetTag.removed.is_(False),
        AssetTag.tag_id == Tag.id,
        Tag.name.like(pattern, escape=_LIKE_ESCAPE_CHAR),
    )


def list_tags(db: Session, q: str | None, limit: int) -> list[tuple[str, int]]:
    """タグ名と件数(removed と削除済み Asset を除く)。件数の多い順、同数は名前順。

    `q` は正規化してから部分一致で絞る(オートコンプリート用)。
    """
    count = func.count(AssetTag.asset_id)
    query = (
        select(Tag.name, count)
        .join(AssetTag, AssetTag.tag_id == Tag.id)
        .join(Asset, Asset.id == AssetTag.asset_id)
        .where(AssetTag.removed.is_(False), Asset.deleted_at.is_(None))
        .group_by(Tag.id, Tag.name)
        .order_by(count.desc(), Tag.name.asc())
        .limit(limit)
    )
    if q is not None and q.strip():
        needle = normalize_tag_name_or_none(q)
        if needle is None:
            return []
        query = query.where(Tag.name.like(f"%{_escape_like(needle)}%", escape=_LIKE_ESCAPE_CHAR))
    return [(name, n) for name, n in db.execute(query).all()]
