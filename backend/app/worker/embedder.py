"""画像の埋め込みの worker(ADR-0033 5章)。自動タイトル・タグの worker とは別。

待ち行列は `asset_embedding.status = 'queued'` の行そのもの。api プロセス内の asyncio タスクが、
使うモデルの行を古い依頼から取り、thumb(512px)を読んで計算する。

- 1回に取る件数は `CLAIM_LIMIT`(8)と、エンジンが1回にまとめる枚数(`image_batch_size`。
  リモートは 32)の大きいほう。ローカルの ONNX(8 か 1)は 8 件のまま。
- thumb の読み込み(オブジェクトストレージならネットワーク)とデコードは、複数のスレッドで
  並列に行う。
- 推論している間に、次の分を取って thumb を読み、デコードしておく(先読みは1つだけ)。
  止めるときに、先に取ってまだ計算していない行は `queued` に戻す。先読みは使うモデルが
  変わっていないときだけ行う(計算中のエンジンを閉じないため)。

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
- 知覚ハッシュ(ADR-0033 12章): 埋め込みを計算するときに、同じ thumb からハッシュも作る
  (無いか、版が古いときだけ)。ハッシュの失敗は記録して飛ばし、埋め込みは失敗にしない。
  待ち行列が空のときは、埋め込みはあるのにハッシュが無い画像(この機能より前に計算したもの
  など)を少しずつ埋める。読めなかった画像は、プロセスが動いている間は再び試さない。
"""

from __future__ import annotations

import asyncio
import io
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import numpy as np
from PIL import Image
from sqlalchemy import or_, select, update
from sqlalchemy.orm import sessionmaker

from app.domain import embedding_index, embedding_settings
from app.domain import embeddings as embeddings_domain
from app.domain import perceptual_hash as perceptual_hash_domain
from app.domain.models import Asset, AssetEmbedding, AssetKind, AssetPerceptualHash
from app.domain.storage import AssetStore
from app.domain.text_safety import sanitize_external_text
from app.embedding.base import EmbeddingEngine, EmbeddingError, vector_to_blob
from app.embedding.engines import EmbeddingEngines
from app.i18n import t

if TYPE_CHECKING:
    from app.config import Settings

logger = logging.getLogger(__name__)

CLAIM_LIMIT = 8
# thumb の読み込みとデコードを並列に行うスレッドの数(控えめにする)。
_LOAD_WORKERS = 8
# 待ち行列が空のときに、1回で知覚ハッシュを埋める枚数。
HASH_BACKFILL_LIMIT = 16
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
    # `running` にした行のすべて(止めるときに `queued` に戻すため)
    claimed: list[uuid.UUID] = field(default_factory=list)
    # 計算する行(asset_id とデコード済みの画像)
    items: list[tuple[uuid.UUID, Image.Image]] = field(default_factory=list)
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
        # 知覚ハッシュを作れなかった Asset(プロセス内。埋め戻しで何度も試さないため)。
        self._hash_failed: set[uuid.UUID] = set()

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
        # 先読みしたジョブ(`_prepare` を走らせているタスク)。1つだけ持つ。
        pending: asyncio.Task[_Job | None] | None = None
        try:
            while not self._stop.is_set():
                try:
                    if pending is not None:
                        task, pending = pending, None
                        job = await task
                    else:
                        await asyncio.to_thread(self.engines.release_idle)
                        job = await asyncio.to_thread(self._prepare)
                except Exception:
                    logger.exception("埋め込みの待ち行列の取得に失敗しました")
                    await self._sleep()
                    continue
                if job is None:
                    try:
                        filled = await self._backfill_hashes()
                    except Exception:
                        logger.exception("知覚ハッシュの埋め戻しに失敗しました")
                        filled = 0
                    if not filled:
                        await self._sleep()
                    continue
                # 計算している間に、次の分を取って thumb を読んでおく。
                if not self._stop.is_set() and job.engine is not None:
                    pending = asyncio.create_task(asyncio.to_thread(self._prepare, job.model_key))
                try:
                    await self._process(job)
                except Exception:
                    logger.exception("埋め込みの計算で想定外の例外が発生しました")
        finally:
            if pending is not None:
                await self._requeue_pending(pending)

    async def _requeue_pending(self, pending: asyncio.Task[_Job | None]) -> None:
        """止めるときに、先読みして取ったがまだ計算していない行を `queued` に戻す。"""
        try:
            job = await pending
        except Exception:
            logger.exception("埋め込みの待ち行列の取得に失敗しました")
            return
        if job is None or not job.claimed:
            return
        try:
            await asyncio.to_thread(self._requeue, job.model_key, job.claimed)
        except Exception:
            # 戻せなくても、次の起動時に `running` の行は `queued` に戻る。
            logger.exception("先読みした埋め込みの行を待ち行列に戻せませんでした")

    def _requeue(self, model_key: str, asset_ids: list[uuid.UUID]) -> int:
        with self.session_factory() as session:
            result = session.execute(
                update(AssetEmbedding)
                .where(
                    AssetEmbedding.model_key == model_key,
                    AssetEmbedding.asset_id.in_(asset_ids),
                    AssetEmbedding.status == embeddings_domain.STATUS_RUNNING,
                )
                .values(status=embeddings_domain.STATUS_QUEUED, updated_at=_utcnow())
            )
            session.commit()
            return result.rowcount or 0

    def _prepare(self, expected_model_key: str | None = None) -> _Job | None:
        """使うモデルの行を取り、画像を読んでデコードする。取るものが無ければ None。

        `expected_model_key` は先読みのときに渡す。使うモデルがそれと違えば何もしない
        (計算中のエンジンを閉じないよう、モデルの切り替えは計算が終わってから行う)。
        """
        with self.session_factory() as session:
            config = embedding_settings.load(session)
            if not embedding_settings.usable(config, self.settings):
                return None
            model_key = embedding_settings.active_model_key(config, self.settings)
            assert model_key is not None
            if expected_model_key is not None and model_key != expected_model_key:
                return None
            engine: EmbeddingEngine | None = None
            engine_error: str | None = None
            try:
                engine = self.engines.engine_for(session, config)
            except EmbeddingError as e:
                engine_error = str(e)

        limit = CLAIM_LIMIT if engine is None else max(CLAIM_LIMIT, engine.image_batch_size)
        claimed = claim(self.session_factory, model_key, limit)
        if not claimed:
            return None
        job = _Job(model_key=model_key, engine=engine, claimed=list(claimed))
        if engine_error is not None:
            # 接続先が消えたなど、設定の問題。取った行は失敗にする(自動では再試行しない)。
            job.failures = [(asset_id, engine_error) for asset_id in claimed]
            return job

        targets: list[Asset] = []
        with self.session_factory() as session:
            for asset_id in claimed:
                asset = session.get(Asset, asset_id)
                if asset is None or asset.deleted_at is not None:
                    job.failures.append((asset_id, t("embeddings.assetDeleted")))
                    continue
                if asset.kind == AssetKind.MASK:
                    job.failures.append((asset_id, t("embeddings.maskNotSupported")))
                    continue
                targets.append(asset)
            loaded = self._load_images(targets)
        for asset, image in zip(targets, loaded, strict=True):
            if image is None:
                job.failures.append((asset.id, t("embeddings.imageUnreadable")))
            else:
                job.items.append((asset.id, image))
        return job

    def _load_images(self, assets: list[Asset]) -> list[Image.Image | None]:
        """thumb を読んでデコードする(複数のスレッドで並列に)。読めなければ None。順序は保つ。

        Asset の属性は読み込み済みのものだけを使う(別スレッドから遅延読み込みをしない)。
        """
        refs = [(asset.id, asset.blob_key, asset.sha256) for asset in assets]

        def load(ref: tuple[uuid.UUID, str, str]) -> Image.Image | None:
            asset_id, blob_key, sha256 = ref
            try:
                return _decode_image(self._read_thumb_by_key(blob_key, sha256))
            except Exception as e:  # noqa: BLE001 - 読めなければその行だけ失敗にする
                logger.warning("asset %s の画像を読めませんでした: %s", asset_id, e)
                return None

        if len(refs) <= 1:
            return [load(ref) for ref in refs]
        with ThreadPoolExecutor(
            max_workers=min(_LOAD_WORKERS, len(refs)), thread_name_prefix="gakei-embed-load"
        ) as pool:
            return list(pool.map(load, refs))

    async def _process(self, job: _Job) -> None:
        if job.failures:
            await asyncio.to_thread(self._finish_failed, job.model_key, job.failures)
        if job.engine is None or not job.items:
            return
        engine = job.engine

        decoded = job.items
        try:
            await asyncio.to_thread(self._store_hashes, decoded)
        except Exception:
            # ハッシュが無くても重複の候補は CLIP だけで判定できる。埋め込みは続ける。
            logger.exception("知覚ハッシュの保存に失敗しました")

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

    # -- 知覚ハッシュ(ADR-0033 12章) ----------------------------------------------

    def _read_thumb(self, asset: Asset) -> bytes:
        return self._read_thumb_by_key(asset.blob_key, asset.sha256)

    def _read_thumb_by_key(self, blob_key: str, sha256: str) -> bytes:
        thumb = self.store.open_content(blob_key, sha256, "thumb")
        return thumb.read_all() if thumb is not None else self.store.read(blob_key)

    def _store_hashes(self, images: list[tuple[uuid.UUID, Image.Image]]) -> int:
        """無いか版の古い知覚ハッシュを作って保存し、件数を返す。1枚の失敗は飛ばす。"""
        ids = [asset_id for asset_id, _ in images]
        with self.session_factory() as session:
            rows = {
                row.asset_id: row
                for row in session.execute(
                    select(AssetPerceptualHash).where(AssetPerceptualHash.asset_id.in_(ids))
                ).scalars()
            }
            written = 0
            for asset_id, image in images:
                row = rows.get(asset_id)
                if row is not None and perceptual_hash_domain.from_stored(
                    row.dhash, row.color, row.version
                ):
                    continue
                try:
                    value = perceptual_hash_domain.compute(image)
                except Exception as e:  # noqa: BLE001 - その画像だけ飛ばす
                    logger.warning("asset %s の知覚ハッシュを作れませんでした: %s", asset_id, e)
                    self._hash_failed.add(asset_id)
                    continue
                if row is None:
                    row = AssetPerceptualHash(asset_id=asset_id)
                    session.add(row)
                row.dhash = value.dhash
                row.color = value.color
                row.version = value.version
                row.created_at = _utcnow()
                written += 1
            session.commit()
            return written

    def _hash_backfill_targets(self) -> list[tuple[uuid.UUID, bytes]]:
        """埋め込みはあるのに、知覚ハッシュが無いか版の古い画像(thumb のバイト列つき)。"""
        with self.session_factory() as session:
            config = embedding_settings.load(session)
            if not embedding_settings.usable(config, self.settings):
                return []
            embedded = (
                select(AssetEmbedding.asset_id)
                .where(
                    AssetEmbedding.asset_id == Asset.id,
                    AssetEmbedding.status == embeddings_domain.STATUS_SUCCEEDED,
                )
                .exists()
            )
            query = (
                select(Asset)
                .outerjoin(AssetPerceptualHash, AssetPerceptualHash.asset_id == Asset.id)
                .where(
                    Asset.deleted_at.is_(None),
                    Asset.kind != AssetKind.MASK,
                    embedded,
                    or_(
                        AssetPerceptualHash.asset_id.is_(None),
                        AssetPerceptualHash.version != perceptual_hash_domain.ALGORITHM_VERSION,
                    ),
                )
                .order_by(Asset.created_at.desc(), Asset.id.desc())
                .limit(HASH_BACKFILL_LIMIT + len(self._hash_failed))
            )
            targets: list[tuple[uuid.UUID, bytes]] = []
            for asset in session.execute(query).scalars():
                if asset.id in self._hash_failed:
                    continue
                try:
                    targets.append((asset.id, self._read_thumb(asset)))
                except Exception as e:  # noqa: BLE001
                    logger.warning("asset %s の画像を読めませんでした: %s", asset.id, e)
                    self._hash_failed.add(asset.id)
                if len(targets) >= HASH_BACKFILL_LIMIT:
                    break
            return targets

    async def _backfill_hashes(self) -> int:
        """待ち行列が空のときに呼ぶ。埋めた(または試した)件数を返す(0 なら休む)。"""
        targets = await asyncio.to_thread(self._hash_backfill_targets)
        if not targets:
            return 0
        decoded: list[tuple[uuid.UUID, Image.Image]] = []
        for asset_id, data in targets:
            try:
                decoded.append((asset_id, await asyncio.to_thread(_decode_image, data)))
            except Exception:  # noqa: BLE001
                self._hash_failed.add(asset_id)
        if decoded:
            await asyncio.to_thread(self._store_hashes, decoded)
        return len(targets)

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
