"""取り込みのたびに走らせる後処理の待ち行列への投入(ADR-0024 4章、ADR-0033 5章、ADR-0043、
ADR-0049 3章)。

自動タイトル・タグと埋め込みを、同じ場所(生成の出力、アップロード、スケッチ、MCP の
アップロード、URL でのアップロード)から1つの関数で待ち行列に入れる。どちらも設定が
オフなら何もしない。呼び出し側は commit の後に `notify_workers` で worker を起こす。

Mod の `asset.ingested`(ADR-0051 2章)も、ここから配達の待ち行列(`mod_delivery`)に積む。
取り込みの経路(`source`)は呼び出し側が渡す。有効な購読が無ければ何もしない。

サムネイルの焦点(ADR-0043)は設定に依らず、マスク以外の新しい Asset をすべて焦点の worker に
渡す。待ち行列はプロセス内(DB には積まない)で、渡しそこねた Asset は埋め戻しのツールが拾う。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from sqlalchemy.orm import Session

from app.domain import annotations as annotations_domain
from app.domain import content_rating as content_rating_domain
from app.domain import embeddings as embeddings_domain
from app.domain.models import Asset, AssetKind

if TYPE_CHECKING:
    from app.config import Settings
    from app.mods.runtime import ModRuntime


class _Notifiable(Protocol):
    def notify(self) -> None: ...


class _FocalSubmitter(Protocol):
    def submit(self, asset_ids: Iterable[uuid.UUID]) -> None: ...


@dataclass
class IngestQueued:
    annotation: bool = False
    embedding: bool = False
    # 焦点を求める Asset(ADR-0043)。commit の後に焦点の worker へ渡す。
    focal_asset_ids: tuple[uuid.UUID, ...] = ()
    # Mod の配達の待ち行列に積んだか(ADR-0051)。
    mods: bool = False

    def __or__(self, other: IngestQueued) -> IngestQueued:
        return IngestQueued(
            annotation=self.annotation or other.annotation,
            embedding=self.embedding or other.embedding,
            focal_asset_ids=self.focal_asset_ids + other.focal_asset_ids,
            mods=self.mods or other.mods,
        )

    @property
    def any(self) -> bool:
        return self.annotation or self.embedding or bool(self.focal_asset_ids) or self.mods


def focal_targets(asset: Asset) -> IngestQueued:
    """焦点を求める対象なら、その Asset を入れた `IngestQueued`(マスクは入れない)。"""
    if asset.kind == AssetKind.MASK or asset.content_withheld_at is not None:
        return IngestQueued()
    return IngestQueued(focal_asset_ids=(asset.id,))


def mod_targets(db: Session, asset: Asset, *, source: str, mods: ModRuntime | None) -> IngestQueued:
    """Mod の `asset.ingested` を積む(有効な購読があるときだけ。commit はしない)。"""
    if mods is None:
        return IngestQueued()
    return IngestQueued(mods=mods.enqueue_asset_ingested(db, asset, source))


def enqueue_after_ingest(
    db: Session,
    asset: Asset,
    settings: Settings,
    *,
    source: str,
    mods: ModRuntime | None = None,
) -> IngestQueued:
    """新しく取り込んだ Asset を、自動タイトル・タグと埋め込みの待ち行列に入れる(設定が
    オンのときだけ。マスクは入れない)。焦点(ADR-0043)の対象も覚えておく。commit はしない。

    レーティング(ADR-0049 3章)は、判定がオンなら `auto_on_ingest` とは別に常に入れる。判定も
    推定と同じ worker が処理するので、どちらかを入れたら推定の worker を起こす。

    `source` は取り込みの経路(ADR-0051 2章。upload / url / sketch / mask / composite /
    generated / import / mcp)で、Mod の `asset.ingested` に載せる。"""
    annotation = annotations_domain.enqueue_on_ingest(db, asset, settings)
    rating = content_rating_domain.enqueue_on_ingest(db, asset, settings)
    return (
        IngestQueued(
            annotation=annotation or rating,
            embedding=embeddings_domain.enqueue_on_ingest(db, asset, settings),
        )
        | focal_targets(asset)
        | mod_targets(db, asset, source=source, mods=mods)
    )


def source_for_kind(kind: str) -> str:
    """画面からの取り込み(`POST /api/assets`)の経路。種類がそのまま経路になる。"""
    return str(kind)


def notify_workers(
    queued: IngestQueued,
    *,
    annotator: _Notifiable | None,
    embedder: _Notifiable | None,
    focal: _FocalSubmitter | None = None,
    mods: _Notifiable | None = None,
) -> None:
    """commit の後に呼ぶ。入れた方の worker を起こす。"""
    if queued.annotation and annotator is not None:
        annotator.notify()
    if queued.embedding and embedder is not None:
        embedder.notify()
    if queued.focal_asset_ids and focal is not None:
        focal.submit(queued.focal_asset_ids)
    if queued.mods and mods is not None:
        mods.notify()
