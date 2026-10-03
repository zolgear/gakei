"""取り込みのたびに走らせる後処理の待ち行列への投入(ADR-0024 4章、ADR-0033 5章)。

自動タイトル・タグと埋め込みを、同じ場所(生成の出力、アップロード、スケッチ、MCP の
アップロード、URL でのアップロード)から1つの関数で待ち行列に入れる。どちらも設定が
オフなら何もしない。呼び出し側は commit の後に `notify_workers` で worker を起こす。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from sqlalchemy.orm import Session

from app.domain import annotations as annotations_domain
from app.domain import embeddings as embeddings_domain
from app.domain.models import Asset

if TYPE_CHECKING:
    from app.config import Settings


class _Notifiable(Protocol):
    def notify(self) -> None: ...


@dataclass
class IngestQueued:
    annotation: bool = False
    embedding: bool = False

    def __or__(self, other: IngestQueued) -> IngestQueued:
        return IngestQueued(
            annotation=self.annotation or other.annotation,
            embedding=self.embedding or other.embedding,
        )

    @property
    def any(self) -> bool:
        return self.annotation or self.embedding


def enqueue_after_ingest(db: Session, asset: Asset, settings: Settings) -> IngestQueued:
    """新しく取り込んだ Asset を、自動タイトル・タグと埋め込みの待ち行列に入れる(設定が
    オンのときだけ。マスクは入れない)。commit はしない。"""
    return IngestQueued(
        annotation=annotations_domain.enqueue_on_ingest(db, asset, settings),
        embedding=embeddings_domain.enqueue_on_ingest(db, asset, settings),
    )


def notify_workers(
    queued: IngestQueued,
    *,
    annotator: _Notifiable | None,
    embedder: _Notifiable | None,
) -> None:
    """commit の後に呼ぶ。入れた方の worker を起こす。"""
    if queued.annotation and annotator is not None:
        annotator.notify()
    if queued.embedding and embedder is not None:
        embedder.notify()
