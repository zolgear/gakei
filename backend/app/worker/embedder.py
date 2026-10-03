"""画像の埋め込みの worker(ADR-0033 5章)。自動タイトル・タグの worker とは別。

待ち行列は `asset_embedding.status = 'queued'` の行そのもの。api プロセス内の asyncio タスクが、
使うモデルの行を古い依頼から最大 `CLAIM_LIMIT` 件ずつ取り、thumb(512px)を読んで計算する。

- 使うモデル以外の `queued` の行は、そのモデルが選ばれるまで取り出さない。
- 埋め込みが無効、またはモデルが使えない(ダウンロードしていないなど)間は、行を `queued` の
  まま残す。
- まとめて計算する枚数はエンジンが決める(`image_batch_size`)。量子化したモデルと LY の
  画像側は1枚ずつ(ADR-0033 2章)。推論は `asyncio.to_thread` でイベントループの外で行う。
- 失敗したら `failed` と理由を記録し、自動では再試行しない。
- 起動時に `running` の行を `queued` に戻す(埋め込みは来歴に加わらず、やり直して害が無い)。
- ベクトルを書いたら、そのモデルの版(`version`)を上げる。numpy で検索するときに、メモリに
  持った行列を読み直す目安にする(ADR-0033 6章)。
- PostgreSQL で pgvector を使えるときは、BLOB と同じトランザクションで `embedding` 列も書き、
  そのモデルの最初のベクトルを書いたときに部分 HNSW 索引を作る。
"""

from __future__ import annotations

import asyncio
import io
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import numpy as np
from PIL import Image
from sqlalchemy import select, update
from sqlalchemy.orm import sessionmaker

from app.domain import embedding_index, embedding_settings
from app.domain import embeddings as embeddings_domain
from app.domain.models import Asset, AssetEmbedding, AssetKind
from app.domain.storage import AssetStore
from app.domain.text_safety import sanitize_external_text
from app.embedding.base import EmbeddingEngine, EmbeddingError, vector_to_blob
from app.embedding.engines import EmbeddingEngines
from app.i18n import t

if TYPE_CHECKING:
    from app.config import Settings

logger = logging.getLogger(__name__)

CLAIM_LIMIT = 8
_POLL_INTERVAL_SECONDS = 1.0
_ERROR_MAX = 500


def _utcnow() -> datetime:
    return datetime.now(UTC)


def reset_running_embeddings(session_factory: sessionmaker) -> int:
    """起動時に呼ぶ。`running` のままの行を `queued` に戻す。"""
    with session_factory() as session:
        result = session.execute(
            update(AssetEmbedding)
            .where(AssetEmbedding.status == embeddings_domain.STATUS_RUNNING)
            .values(status=embeddings_domain.STATUS_QUEUED)
        )
        session.commit()
        return result.rowcount or 0


def claim(
    session_factory: sessionmaker, model_key: str, limit: int = CLAIM_LIMIT
) -> list[uuid.UUID]:
    """そのモデルの待ち行列の先頭から `limit` 件を `running` にして返す。"""
    with session_factory() as session:
        candidates = (
            session.execute(
                select(AssetEmbedding.asset_id)
                .where(
                    AssetEmbedding.model_key == model_key,
                    AssetEmbedding.status == embeddings_domain.STATUS_QUEUED,
                )
                # ADR-0027 2章: NULL の位置と同順位の並びを SQLite の挙動にそろえる。
                .order_by(
                    AssetEmbedding.requested_at.asc().nulls_first(),
                    AssetEmbedding.asset_id.asc(),
                )
                .limit(limit)
            )
            .scalars()
            .all()
        )
        claimed: list[uuid.UUID] = []
        now = _utcnow()
        for asset_id in candidates:
            result = session.execute(
                update(AssetEmbedding)
                .where(
                    AssetEmbedding.asset_id == asset_id,
                    AssetEmbedding.model_key == model_key,
                    AssetEmbedding.status == embeddings_domain.STATUS_QUEUED,
                )
                .values(status=embeddings_domain.STATUS_RUNNING, updated_at=now)
            )
            if (result.rowcount or 0) > 0:
                claimed.append(asset_id)
        session.commit()
        return claimed


@dataclass
class _Job:
    model_key: str
    engine: EmbeddingEngine | None
    # 計算する行(asset_id と画像のバイト列)
    items: list[tuple[uuid.UUID, bytes]] = field(default_factory=list)
    # 計算する前に失敗が決まった行(asset_id と理由)
    failures: list[tuple[uuid.UUID, str]] = field(default_factory=list)


class Embedder:
    """lifespan で起動する埋め込みの worker。"""

    def __init__(
        self,
        session_factory: sessionmaker,
        store: AssetStore,
        settings: Settings,
        engines: EmbeddingEngines | None = None,
        *,
        pgvector: bool = False,
        db_engine: Any = None,
    ) -> None:
        self.session_factory = session_factory
        self.store = store
        self.settings = settings
        # テストでは差し替えてよい。
        self.engines: Any = engines or EmbeddingEngines(settings)
        self.pgvector = pgvector
        self.db_engine = db_engine
        self._versions: dict[str, int] = {}
        self._versions_lock = threading.Lock()
        self._indexed: set[str] = set()
        self._wake = asyncio.Event()
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._event_loop: asyncio.AbstractEventLoop | None = None

    # -- 公開 API --------------------------------------------------------------

    def notify(self) -> None:
        """待ち行列に入れたあとに呼ぶ。同期エンドポイント(スレッド)からも呼べる。"""
        loop = self._event_loop
        if loop is None or loop.is_closed():
            return
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        if current is loop:
            self._wake.set()
        else:
            loop.call_soon_threadsafe(self._wake.set)

    def version(self, model_key: str) -> int:
        """そのモデルのベクトルを書き換えた回数(プロセス内。再起動で 0 に戻る)。"""
        with self._versions_lock:
            return self._versions.get(model_key, 0)

    def bump(self, model_key: str) -> None:
        """そのモデルの版を上げる(ベクトルを書いた・消したとき)。"""
        with self._versions_lock:
            self._versions[model_key] = self._versions.get(model_key, 0) + 1

    def ensure_active_index(self) -> None:
        """pgvector のとき、使うモデルの部分 HNSW 索引を作る(起動時と設定の保存時。ADR-0033
        4章)。次元が分からない(リモートでまだ1件も計算していない)なら、最初のベクトルを
        書いたときに作る。"""
        if not self.pgvector or self.db_engine is None:
            return
        with self.session_factory() as session:
            config = embedding_settings.load(session)
            model_key = embedding_settings.active_model_key(config, self.settings)
            if model_key is None:
                return
            dim = embedding_settings.active_dim(config) or embeddings_domain.known_dim(
                session, model_key
            )
        if dim is None:
            return
        embedding_index.ensure_hnsw_index(self.db_engine, model_key, dim)
        self._indexed.add(model_key)

    async def start(self) -> int:
        self._event_loop = asyncio.get_running_loop()
        count = await asyncio.to_thread(reset_running_embeddings, self.session_factory)
        self._task = asyncio.create_task(self._loop(), name="gakei-embedder")
        return count

    async def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._task is not None:
            await self._task

    # -- ループ ----------------------------------------------------------------

    async def _sleep(self) -> None:
        self._wake.clear()
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=_POLL_INTERVAL_SECONDS)
        except TimeoutError:
            pass

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.to_thread(self.engines.release_idle)
                job = await asyncio.to_thread(self._prepare)
            except Exception:
                logger.exception("埋め込みの待ち行列の取得に失敗しました")
                await self._sleep()
                continue
            if job is None:
                await self._sleep()
                continue
            try:
                await self._process(job)
            except Exception:
                logger.exception("埋め込みの計算で想定外の例外が発生しました")

    def _prepare(self) -> _Job | None:
        """使うモデルの行を取り、画像を読む。取るものが無ければ None。"""
        with self.session_factory() as session:
            config = embedding_settings.load(session)
            if not embedding_settings.usable(config, self.settings):
                return None
            model_key = embedding_settings.active_model_key(config, self.settings)
            assert model_key is not None

        claimed = claim(self.session_factory, model_key)
        if not claimed:
            return None
        job = _Job(model_key=model_key, engine=None)
        with self.session_factory() as session:
            try:
                job.engine = self.engines.engine_for(session, config)
            except EmbeddingError as e:
                # 接続先が消えたなど、設定の問題。取った行は失敗にする(自動では再試行しない)。
                job.failures = [(asset_id, str(e)) for asset_id in claimed]
                return job

        with self.session_factory() as session:
            for asset_id in claimed:
                asset = session.get(Asset, asset_id)
                if asset is None or asset.deleted_at is not None:
                    job.failures.append((asset_id, t("embeddings.assetDeleted")))
                    continue
                if asset.kind == AssetKind.MASK:
                    job.failures.append((asset_id, t("embeddings.maskNotSupported")))
                    continue
                try:
                    thumb = self.store.open_content(asset.blob_key, asset.sha256, "thumb")
                    data = (
                        thumb.read_all() if thumb is not None else self.store.read(asset.blob_key)
                    )
                except Exception as e:  # noqa: BLE001 - 読めなければその行だけ失敗にする
                    logger.warning("asset %s の画像を読めませんでした: %s", asset_id, e)
                    job.failures.append((asset_id, t("embeddings.imageUnreadable")))
                    continue
                job.items.append((asset_id, data))
        return job

    async def _process(self, job: _Job) -> None:
        if job.failures:
            await asyncio.to_thread(self._finish_failed, job.model_key, job.failures)
        if job.engine is None or not job.items:
            return
        engine = job.engine

        decoded: list[tuple[uuid.UUID, Image.Image]] = []
        unreadable: list[tuple[uuid.UUID, str]] = []
        for asset_id, data in job.items:
            try:
                decoded.append((asset_id, await asyncio.to_thread(_decode_image, data)))
            except Exception:  # noqa: BLE001
                unreadable.append((asset_id, t("embeddings.imageUnreadable")))
        if unreadable:
            await asyncio.to_thread(self._finish_failed, job.model_key, unreadable)

        batch = max(1, engine.image_batch_size)
        for start in range(0, len(decoded), batch):
            chunk = decoded[start : start + batch]
            ids = [asset_id for asset_id, _ in chunk]
            try:
                vectors = await asyncio.to_thread(
                    engine.embed_images, [image for _, image in chunk]
                )
                if vectors.shape[0] != len(chunk):
                    raise EmbeddingError(t("embeddings.remoteInvalidResponse"))
            except EmbeddingError as e:
                await asyncio.to_thread(
                    self._finish_failed, job.model_key, [(i, str(e)) for i in ids]
                )
                continue
            except Exception as e:  # noqa: BLE001 - どの失敗も記録する
                logger.exception("埋め込みの計算中に予期しない例外が発生しました")
                await asyncio.to_thread(
                    self._finish_failed,
                    job.model_key,
                    [(i, f"internalError: {e}") for i in ids],
                )
                continue
            await asyncio.to_thread(
                self._finish_succeeded, job.model_key, list(zip(ids, vectors, strict=True))
            )

    # -- 書き込み ----------------------------------------------------------------

    def _finish_failed(self, model_key: str, failures: list[tuple[uuid.UUID, str]]) -> None:
        with self.session_factory() as session:
            now = _utcnow()
            for asset_id, message in failures:
                row = session.get(AssetEmbedding, (asset_id, model_key))
                if row is None:
                    continue
                row.status = embeddings_domain.STATUS_FAILED
                # エンジン(リモートの推論サーバーなど)のエラー文言は外部由来。NUL などを除く。
                row.error = sanitize_external_text(message)[:_ERROR_MAX]
                row.finished_at = now
                row.updated_at = now
            session.commit()

    def _finish_succeeded(
        self, model_key: str, results: list[tuple[uuid.UUID, np.ndarray]]
    ) -> None:
        written = 0
        dim = 0
        with self.session_factory() as session:
            now = _utcnow()
            for asset_id, vector in results:
                row = session.get(AssetEmbedding, (asset_id, model_key))
                # 計算の間にベクトルを消された(管理者の操作)なら、作り直さない。
                if row is None:
                    continue
                values = np.asarray(vector, dtype=np.float32).reshape(-1)
                row.status = embeddings_domain.STATUS_SUCCEEDED
                row.vector = vector_to_blob(values)
                row.dim = int(values.shape[0])
                row.error = None
                row.finished_at = now
                row.updated_at = now
                session.flush()
                if self.pgvector:
                    embedding_index.write_embedding_column(
                        session.connection(), asset_id, model_key, values
                    )
                written += 1
                dim = int(values.shape[0])
            session.commit()
        if written:
            self.bump(model_key)
            if self.pgvector and model_key not in self._indexed and self.db_engine is not None:
                embedding_index.ensure_hnsw_index(self.db_engine, model_key, dim)
                self._indexed.add(model_key)


def _decode_image(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    return image
