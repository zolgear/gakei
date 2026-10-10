"""取り込みのたびに走らせる後処理の待ち行列への投入(ADR-0024 4章、ADR-0033 5章、ADR-0043)。

自動タイトル・タグと埋め込みを、同じ場所(生成の出力、アップロード、スケッチ、MCP の
アップロード、URL でのアップロード)から1つの関数で待ち行列に入れる。どちらも設定が
オフなら何もしない。呼び出し側は commit の後に `notify_workers` で worker を起こす。

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
from app.domain import embeddings as embeddings_domain
from app.domain.models import Asset, AssetKind

if TYPE_CHECKING:
    from app.config import Settings


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

    def __or__(self, other: IngestQueued) -> IngestQueued:
        return IngestQueued(
            annotation=self.annotation or other.annotation,
            embedding=self.embedding or other.embedding,
            focal_asset_ids=self.focal_asset_ids + other.focal_asset_ids,
        )

    @property
    def any(self) -> bool:
        return self.annotation or self.embedding or bool(self.focal_asset_ids)


def focal_targets(asset: Asset) -> IngestQueued:
    """焦点を求める対象なら、その Asset を入れた `IngestQueued`(マスクは入れない)。"""
    if asset.kind == AssetKind.MASK:
        return IngestQueued()
    return IngestQueued(focal_asset_ids=(asset.id,))


def enqueue_after_ingest(db: Session, asset: Asset, settings: Settings) -> IngestQueued:
    """新しく取り込んだ Asset を、自動タイトル・タグと埋め込みの待ち行列に入れる(設定が
    オンのときだけ。マスクは入れない)。焦点(ADR-0043)の対象も覚えておく。commit はしない。"""
    return IngestQueued(
        annotation=annotations_domain.enqueue_on_ingest(db, asset, settings),
        embedding=embeddings_domain.enqueue_on_ingest(db, asset, settings),
    ) | focal_targets(asset)


def notify_workers(
    queued: IngestQueued,
    *,
    annotator: _Notifiable | None,
    embedder: _Notifiable | None,
    focal: _FocalSubmitter | None = None,
) -> None:
    """commit の後に呼ぶ。入れた方の worker を起こす。"""
    if queued.annotation and annotator is not None:
        annotator.notify()
    if queued.embedding and embedder is not None:
        embedder.notify()
    if queued.focal_asset_ids and focal is not None:
        focal.submit(queued.focal_asset_ids)
