"""画像の埋め込みの worker(ADR-0033 5章)。自動タイトル・タグの worker とは別。

待ち行列は `asset_embedding.status = 'queued'` の行そのもの。api プロセス内の asyncio タスクが、
使うモデルの行を古い依頼から取り、thumb(512px)を読んで計算する。EmbeddingGemma 2 のように
入力に大きな画像を使うモデルは、preview(2048px)を読む(カタログの `input_variant`。ADR-0044)。

- 1回に取る件数は `CLAIM_LIMIT`(8)と、エンジンが1回にまとめる枚数(`image_batch_size`。
  リモートは 32)の大きいほう。ローカルの ONNX(8 か 1)は 8 件のまま。preview を読むモデルは
  `PREVIEW_CLAIM_LIMIT`(2)件にする(デコードした preview は1枚 最大 12MB で、先読みの分も
  メモリに持つため。1枚に数秒かかるモデルなので、少なく取っても待ちは生じない)。
- 知覚ハッシュは、どのモデルでも thumb から作る(preview を読むモデルでは thumb も読む)。
- thumb の読み込み(オブジェクトストレージならネットワーク)とデコードは、複数のスレッドで
  並列に行う。知覚ハッシュも取ったときに作る(先読みなら推論と重なる)。
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
- Run のプロンプトの埋め込み(ADR-0045 4章): `run_prompt_embedding.status = 'queued'` の行も
  処理する。画像の待ち行列が空のときにだけ、1件ずつ取る(画像の結果が先にそろうように)。
  使うモデルがプロンプトの埋め込みに対応しない間は取り出さない。文章側のセッションだけを
  使い、計算中にタイトルが変わって待ち行列に戻された行(`running` でなくなった行)には結果を
  書かない。
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

from app.domain import content_withholding, derivatives, embedding_index, embedding_settings
from app.domain import embeddings as embeddings_domain
from app.domain import perceptual_hash as perceptual_hash_domain
from app.domain import prompt_embeddings as prompt_domain
from app.domain.models import (
    Asset,
    AssetEmbedding,
    AssetKind,
    AssetPerceptualHash,
    Run,
    RunPromptEmbedding,
)
from app.domain.storage import AssetStore
from app.domain.text_safety import sanitize_external_text
from app.embedding.base import (
    DocumentEmbeddingEngine,
    EmbeddingEngine,
    EmbeddingError,
    vector_to_blob,
)
from app.embedding.catalog import InputVariant
from app.embedding.engines import EmbeddingEngines
from app.i18n import t
from app.mods import events as mods_events
from app.mods.api import ImageRef

if TYPE_CHECKING:
    from app.config import Settings

logger = logging.getLogger(__name__)

CLAIM_LIMIT = 8
PREVIEW_CLAIM_LIMIT = 2
# プロンプトは1件ずつ取る(取るたびに画像の待ち行列を先に見る)。
PROMPT_CLAIM_LIMIT = 1
# thumb の読み込みとデコードを並列に行うスレッドの数(控えめにする)。
_LOAD_WORKERS = 8
# 待ち行列が空のときに、1回で知覚ハッシュを埋める枚数。
HASH_BACKFILL_LIMIT = 16
_POLL_INTERVAL_SECONDS = 1.0
_ERROR_MAX = 500


def _utcnow() -> datetime:
    return datetime.now(UTC)


def reset_running_embeddings(session_factory: sessionmaker) -> int:
    """起動時に呼ぶ。`running` のままの行(画像とプロンプト)を `queued` に戻す。"""
    with session_factory() as session:
        result = session.execute(
            update(AssetEmbedding)
            .where(AssetEmbedding.status == embeddings_domain.STATUS_RUNNING)
            .values(status=embeddings_domain.STATUS_QUEUED)
        )
        prompts = session.execute(
            update(RunPromptEmbedding)
            .where(RunPromptEmbedding.status == prompt_domain.STATUS_RUNNING)
            .values(status=prompt_domain.STATUS_QUEUED)
        )
        session.commit()
        return (result.rowcount or 0) + (prompts.rowcount or 0)


def claim_prompts(
    session_factory: sessionmaker, model_key: str, limit: int = PROMPT_CLAIM_LIMIT
) -> list[uuid.UUID]:
    """そのモデルのプロンプトの待ち行列の先頭から `limit` 件を `running` にして返す。"""
    with session_factory() as session:
        candidates = (
            session.execute(
                select(RunPromptEmbedding.run_id)
                .where(
                    RunPromptEmbedding.model_key == model_key,
                    RunPromptEmbedding.status == prompt_domain.STATUS_QUEUED,
                )
                .order_by(
                    RunPromptEmbedding.requested_at.asc().nulls_first(),
                    RunPromptEmbedding.run_id.asc(),
                )
                .limit(limit)
            )
            .scalars()
            .all()
        )
        claimed: list[uuid.UUID] = []
        now = _utcnow()
        for run_id in candidates:
            result = session.execute(
                update(RunPromptEmbedding)
                .where(
                    RunPromptEmbedding.run_id == run_id,
                    RunPromptEmbedding.model_key == model_key,
                    RunPromptEmbedding.status == prompt_domain.STATUS_QUEUED,
                )
                .values(status=prompt_domain.STATUS_RUNNING, updated_at=now)
            )
            if (result.rowcount or 0) > 0:
                claimed.append(run_id)
        session.commit()
        return claimed


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
    # 入力にする派生画像。thumb でなければ、知覚ハッシュ用に thumb も読む。
    variant: InputVariant = "thumb"
    # 計算する前に失敗が決まった行(asset_id と理由)
    failures: list[tuple[uuid.UUID, str]] = field(default_factory=list)
    # Mod の `ai.before_send` に載せる画像と、取り込んだ人(ADR-0051)。
    refs: dict[uuid.UUID, ImageRef] = field(default_factory=dict)
    creators: dict[uuid.UUID, uuid.UUID | None] = field(default_factory=dict)


@dataclass
class _PromptJob:
    model_key: str
    engine: DocumentEmbeddingEngine | None
    claimed: list[uuid.UUID] = field(default_factory=list)
    # 計算する行(run_id と埋め込む文章)
    items: list[tuple[uuid.UUID, prompt_domain.DocumentText]] = field(default_factory=list)
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
            # `FAKE_PROVIDER=1` のダミーのエンジンは、カタログと違う次元(512)を返すので、
            # カタログの次元で索引を作らない(計算したベクトルの次元で作る)。
            catalog_dim = (
                None if self.settings.fake_provider else embedding_settings.active_dim(config)
            )
            dim = catalog_dim or embeddings_domain.known_dim(session, model_key)
        if dim is not None:
            embedding_index.ensure_hnsw_index(self.db_engine, model_key, dim)
            self._indexed.add(model_key)
        # プロンプトの索引(ADR-0045 3章。対応するモデルのときだけ)。
        if not embedding_settings.prompt_supported(config):
            return
        with self.session_factory() as session:
            prompt_dim = catalog_dim or prompt_domain.known_dim(session, model_key)
        if prompt_dim is None:
            return
        embedding_index.ensure_hnsw_index(
            self.db_engine, model_key, prompt_dim, embedding_index.PROMPT_TABLE
        )
        self._indexed.add(prompt_domain.version_key(model_key))

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
                    # 画像の待ち行列が空なら、プロンプトを1件(ADR-0045 4章)。
                    try:
                        prompt_job = await asyncio.to_thread(self._prepare_prompt)
                    except Exception:
                        logger.exception("プロンプトの埋め込みの待ち行列の取得に失敗しました")
                        prompt_job = None
                    if prompt_job is not None:
                        try:
                            await self._process_prompt(prompt_job)
                        except Exception:
                            logger.exception("プロンプトの埋め込みで想定外の例外が発生しました")
                        continue
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
            variant = embedding_settings.input_variant(config)

        limit = CLAIM_LIMIT if engine is None else max(CLAIM_LIMIT, engine.image_batch_size)
        if variant != "thumb":
            limit = PREVIEW_CLAIM_LIMIT
        claimed = claim(self.session_factory, model_key, limit)
        if not claimed:
            return None
        job = _Job(model_key=model_key, engine=engine, claimed=list(claimed), variant=variant)
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
                # ADR-0051 4章: `deny` の判定を受けた・画像を残さなかった画像は埋め込まない
                # (リモートの埋め込みに送らない)。
                if content_withholding.blocked_asset_ids(session, [asset.id]):
                    job.failures.append((asset_id, t("mods.contentWithheld")))
                    continue
                targets.append(asset)
                job.refs[asset.id] = mods_events.image_ref(asset, "primary")
                job.creators[asset.id] = asset.created_by_user_id
            loaded = self._load_images(targets, job.variant)
        hash_items: list[tuple[uuid.UUID, Image.Image]] = []
        for asset, pair in zip(targets, loaded, strict=True):
            if pair is None:
                job.failures.append((asset.id, t("embeddings.imageUnreadable")))
            else:
                image, hash_image = pair
                job.items.append((asset.id, image))
                if hash_image is not None:
                    hash_items.append((asset.id, hash_image))
        if hash_items:
            # 先読みのときは、前の分の推論を待つ間にハッシュも作っておける。
            try:
                self._store_hashes(hash_items)
            except Exception:
                # ハッシュが無くても重複の候補は CLIP だけで判定できる。埋め込みは続ける。
                logger.exception("知覚ハッシュの保存に失敗しました")
        return job

    def _load_images(
        self, assets: list[Asset], variant: InputVariant = "thumb"
    ) -> list[tuple[Image.Image, Image.Image | None] | None]:
        """入力の派生画像を読んでデコードする(複数のスレッドで並列に)。返すのは
        (入力の画像、知覚ハッシュ用の thumb)。読めなければ None。順序は保つ。

        `variant` が thumb でなければ、ハッシュ用に thumb も読む。thumb だけが読めないときは
        ハッシュ用を None にする(埋め込みは続ける)。

        Asset の属性は読み込み済みのものだけを使う(別スレッドから遅延読み込みをしない)。
        """
        refs = [(asset.id, asset.blob_key, asset.sha256) for asset in assets]

        def load(
            ref: tuple[uuid.UUID, str, str],
        ) -> tuple[Image.Image, Image.Image | None] | None:
            asset_id, blob_key, sha256 = ref
            try:
                image = _decode_image(self._read_derived_by_key(blob_key, sha256, variant))
            except Exception as e:  # noqa: BLE001 - 読めなければその行だけ失敗にする
                logger.warning("asset %s の画像を読めませんでした: %s", asset_id, e)
                return None
            if variant == "thumb":
                return image, image
            try:
                return image, _decode_image(self._read_thumb_by_key(blob_key, sha256))
            except Exception as e:  # noqa: BLE001 - ハッシュは後で埋め戻す
                logger.warning("asset %s の thumb を読めませんでした: %s", asset_id, e)
                return image, None

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
        batch = max(1, engine.image_batch_size)
        for start in range(0, len(decoded), batch):
            chunk = decoded[start : start + batch]
            ids = [asset_id for asset_id, _ in chunk]
            try:
                # ADR-0051: リモートに送る前のチェック(ローカルの ONNX とダミーは何もしない)。
                denial = await asyncio.to_thread(self._before_send, engine, job, ids)
                if denial is not None:
                    raise EmbeddingError(denial.message)
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

    def _before_send(self, engine: EmbeddingEngine, job: _Job, ids: list[uuid.UUID]) -> Any:
        before_send = getattr(self.engines, "before_send", None)
        if before_send is None:
            return None
        creators = {job.creators.get(i) for i in ids}
        return before_send(
            engine,
            images=[job.refs[i] for i in ids if i in job.refs],
            # 取り込んだ人が1人に決まるときだけ actor にする。
            actor_user_id=next(iter(creators)) if len(creators) == 1 else None,
        )

    # -- Run のプロンプト(ADR-0045) ------------------------------------------------

    def _prepare_prompt(self) -> _PromptJob | None:
        """使うモデルのプロンプトの行を取り、埋め込む文章を組み立てる。取るものが無い、
        またはモデルが対応しなければ None。"""
        with self.session_factory() as session:
            config = embedding_settings.load(session)
            model_key = prompt_domain.active_model_key(config, self.settings)
            if model_key is None:
                return None
            engine: Any = None
            engine_error: str | None = None
            try:
                engine = self.engines.engine_for(session, config)
            except EmbeddingError as e:
                engine_error = str(e)
        if engine is not None and not hasattr(engine, "embed_documents"):
            engine, engine_error = None, t("embeddings.promptNotSupported")
        claimed = claim_prompts(self.session_factory, model_key, PROMPT_CLAIM_LIMIT)
        if not claimed:
            return None
        job = _PromptJob(model_key=model_key, engine=engine, claimed=list(claimed))
        if engine is None:
            job.failures = [(run_id, engine_error or "") for run_id in claimed]
            return job
        with self.session_factory() as session:
            for run_id in claimed:
                run = session.get(Run, run_id)
                if run is None or run.deleted_at is not None:
                    job.failures.append((run_id, t("embeddings.runDeleted")))
                    continue
                if not prompt_domain.is_target(session, run):
                    job.failures.append((run_id, t("embeddings.runNotTarget")))
                    continue
                prompt = prompt_domain.select_prompt(run)
                title = prompt_domain.first_output_title(session, run_id)
                try:
                    document = prompt_domain.build_document(prompt, title, engine.document_fits)
                except EmbeddingError as e:
                    job.failures.append((run_id, str(e)))
                    continue
                job.items.append((run_id, document))
        return job

    async def _process_prompt(self, job: _PromptJob) -> None:
        if job.failures:
            await asyncio.to_thread(self._finish_prompt_failed, job.model_key, job.failures)
        if job.engine is None:
            return
        engine = job.engine
        for run_id, document in job.items:
            try:
                vectors = await asyncio.to_thread(engine.embed_documents, [document.text])
                if vectors.shape[0] != 1:
                    raise EmbeddingError(t("embeddings.remoteInvalidResponse"))
            except EmbeddingError as e:
                await asyncio.to_thread(
                    self._finish_prompt_failed, job.model_key, [(run_id, str(e))]
                )
                continue
            except Exception as e:  # noqa: BLE001 - どの失敗も記録する
                logger.exception("プロンプトの埋め込みの計算中に予期しない例外が発生しました")
                await asyncio.to_thread(
                    self._finish_prompt_failed,
                    job.model_key,
                    [(run_id, f"internalError: {e}")],
                )
                continue
            await asyncio.to_thread(
                self._finish_prompt_succeeded, job.model_key, run_id, vectors[0], document
            )

    def _finish_prompt_failed(self, model_key: str, failures: list[tuple[uuid.UUID, str]]) -> None:
        with self.session_factory() as session:
            now = _utcnow()
            for run_id, message in failures:
                row = session.get(RunPromptEmbedding, (run_id, model_key))
                # 計算の間に消された・待ち行列に戻された行には書かない。
                if row is None or row.status != prompt_domain.STATUS_RUNNING:
                    continue
                row.status = prompt_domain.STATUS_FAILED
                row.error = sanitize_external_text(message)[:_ERROR_MAX]
                row.finished_at = now
                row.updated_at = now
            session.commit()

    def _finish_prompt_succeeded(
        self,
        model_key: str,
        run_id: uuid.UUID,
        vector: np.ndarray,
        document: prompt_domain.DocumentText,
    ) -> None:
        values = np.asarray(vector, dtype=np.float32).reshape(-1)
        with self.session_factory() as session:
            row = session.get(RunPromptEmbedding, (run_id, model_key))
            # 計算の間に消された(管理者の操作)、またはタイトルが変わって待ち行列に戻された
            # 行には書かない(戻された行は計算し直す)。
            if row is None or row.status != prompt_domain.STATUS_RUNNING:
                return
            now = _utcnow()
            row.status = prompt_domain.STATUS_SUCCEEDED
            row.vector = vector_to_blob(values)
            row.dim = int(values.shape[0])
            row.text_sha256 = document.sha256
            row.truncated = document.truncated
            row.error = None
            row.finished_at = now
            row.updated_at = now
            session.flush()
            if self.pgvector:
                embedding_index.write_embedding_column(
                    session.connection(), run_id, model_key, values, embedding_index.PROMPT_TABLE
                )
            session.commit()
        key = prompt_domain.version_key(model_key)
        self.bump(key)
        if self.pgvector and key not in self._indexed and self.db_engine is not None:
            embedding_index.ensure_hnsw_index(
                self.db_engine, model_key, int(values.shape[0]), embedding_index.PROMPT_TABLE
            )
            self._indexed.add(key)

    # -- 知覚ハッシュ(ADR-0033 12章) ----------------------------------------------

    def _read_thumb(self, asset: Asset) -> bytes:
        return self._read_thumb_by_key(asset.blob_key, asset.sha256)

    def _read_thumb_by_key(self, blob_key: str, sha256: str) -> bytes:
        return self._read_derived_by_key(blob_key, sha256, "thumb")

    def _read_derived_by_key(self, blob_key: str, sha256: str, variant: InputVariant) -> bytes:
        # 今の版の派生が無ければ原本から作る(ADR-0036 2章)。作れなければ原本を読む。
        derived = derivatives.ensure_derived(self.store, blob_key, sha256, variant)
        return derived.read_all() if derived is not None else self.store.read(blob_key)

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
                    Asset.content_withheld_at.is_(None),
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
