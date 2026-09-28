"""自動タイトル・タグの推定の worker(ADR-0024 4章)。

待ち行列は `asset_annotation.auto_status = 'queued'` の行そのもの。Run の runner と同じく
api プロセス内の asyncio タスクが1件ずつ(古い依頼から)処理する。起動時に `running` の行を
`queued` に戻す(推定は来歴に加わらず、途中で止まっても害が無いため再開してよい)。

- 1 回の Run の出力には同じタイトルを付ける。LLM は Run につき1回だけ呼び、同じ Run の他の
  出力に auto タイトルが既にあればそれを写す(worker は直列なので、2枚目以降は必ず写せる)。
- LLM と VLM の呼び出しは直近1時間の回数を数え、上限(設定 `hourly_limit`)に達したら失敗に
  せず、行を `queued` に戻して次の枠まで待つ。回数はプロセス内で数える(再起動で 0 に戻る)。
- 推定に失敗したら `failed` と理由を記録し、自動では再試行しない。
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from PIL import Image
from sqlalchemy import select, update
from sqlalchemy.orm import sessionmaker

from app.annotation.engines import (
    AnnotationEngineError,
    AnnotationEngines,
    EngineContext,
    FakeEngines,
    OpenAIEngines,
    image_to_jpeg,
)
from app.annotation.wd_tagger import WdTagger
from app.domain import annotation_settings
from app.domain import annotations as annotations_domain
from app.domain.models import Asset, AssetAnnotation, AssetKind, Run
from app.domain.storage import AssetStore
from app.i18n import t

if TYPE_CHECKING:
    from app.config import Settings

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 1.0
_WINDOW_SECONDS = 3600.0
_ERROR_MAX = 500


def _utcnow() -> datetime:
    return datetime.now(UTC)


class _RateLimitedError(Exception):
    """LLM・VLM の1時間の上限に達した(失敗にせず queued に戻す)。"""


def reset_running_annotations(session_factory: sessionmaker) -> int:
    """起動時に呼ぶ。`running` のままの行を `queued` に戻す。"""
    with session_factory() as session:
        result = session.execute(
            update(AssetAnnotation)
            .where(AssetAnnotation.auto_status == annotations_domain.STATUS_RUNNING)
            .values(auto_status=annotations_domain.STATUS_QUEUED)
        )
        session.commit()
        return result.rowcount or 0


def _pick(session_factory: sessionmaker) -> uuid.UUID | None:
    with session_factory() as session:
        candidate = session.execute(
            select(AssetAnnotation.asset_id)
            .where(AssetAnnotation.auto_status == annotations_domain.STATUS_QUEUED)
            .order_by(AssetAnnotation.auto_requested_at.asc())
            .limit(1)
        ).scalar_one_or_none()
        if candidate is None:
            return None
        result = session.execute(
            update(AssetAnnotation)
            .where(
                AssetAnnotation.asset_id == candidate,
                AssetAnnotation.auto_status == annotations_domain.STATUS_QUEUED,
            )
            .values(auto_status=annotations_domain.STATUS_RUNNING, updated_at=_utcnow())
        )
        session.commit()
        if (result.rowcount or 0) == 0:
            return None
        return candidate


@dataclass
class _Job:
    asset_id: uuid.UUID
    config: annotation_settings.AnnotationConfig
    connection: annotation_settings.Connection
    engines: list[str]
    prompt: str | None
    title_locked: bool
    sibling_title: str | None
    image_bytes: bytes | None


class Annotator:
    """lifespan で起動する推定の worker。"""

    def __init__(
        self,
        session_factory: sessionmaker,
        store: AssetStore,
        settings: Settings,
        engines: AnnotationEngines | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.store = store
        self.settings = settings
        # テストでは差し替えてよい(呼び出し回数を数えるなど)。
        self.engines: AnnotationEngines = engines or (
            FakeEngines() if settings.fake_provider else OpenAIEngines(WdTagger(settings.data_dir))
        )
        self._calls: deque[float] = deque()
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

    def calls_last_hour(self) -> int:
        self._trim_calls(time.monotonic())
        return len(self._calls)

    async def start(self) -> int:
        self._event_loop = asyncio.get_running_loop()
        count = await asyncio.to_thread(reset_running_annotations, self.session_factory)
        self._task = asyncio.create_task(self._loop(), name="gakei-annotator")
        return count

    async def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._task is not None:
            await self._task

    # -- 上限 ------------------------------------------------------------------

    def _trim_calls(self, now: float) -> None:
        while self._calls and now - self._calls[0] >= _WINDOW_SECONDS:
            self._calls.popleft()

    def _take_call(self, limit: int) -> None:
        now = time.monotonic()
        self._trim_calls(now)
        if len(self._calls) >= limit:
            raise _RateLimitedError
        self._calls.append(now)

    def _limit_reached(self, limit: int) -> bool:
        return self.calls_last_hour() >= limit

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
                config = await asyncio.to_thread(self._load_config)
                if config.api_engines_enabled and self._limit_reached(config.hourly_limit):
                    await self._sleep()
                    continue
                picked = await asyncio.to_thread(_pick, self.session_factory)
            except Exception:
                logger.exception("推定の待ち行列の取得に失敗しました")
                await self._sleep()
                continue

            if picked is None:
                await self._sleep()
                continue

            try:
                await self._process(picked)
            except Exception:
                logger.exception("asset %s の推定で想定外の例外が発生しました", picked)

    def _load_config(self) -> annotation_settings.AnnotationConfig:
        with self.session_factory() as session:
            return annotation_settings.load(session)

    # -- 1件の処理 --------------------------------------------------------------

    def _load_job(self, asset_id: uuid.UUID) -> _Job | str:
        """処理に要るものを集める。処理できなければ失敗の理由(文字列)を返す。"""
        with self.session_factory() as session:
            asset = session.get(Asset, asset_id)
            if asset is None or asset.deleted_at is not None:
                return t("annotations.assetDeleted")
            if asset.kind == AssetKind.MASK:
                return t("annotations.maskNotSupported")
            config = annotation_settings.load(session)
            engines = annotations_domain.usable_engines(config, self.settings)
            if not engines:
                return t("annotations.noEngine")

            prompt: str | None = None
            if asset.kind == AssetKind.GENERATED and asset.produced_by_run_id is not None:
                run = session.get(Run, asset.produced_by_run_id)
                prompt = run.prompt if run is not None else None
            elif isinstance(asset.embedded_meta, dict):
                value = asset.embedded_meta.get("prompt")
                prompt = value if isinstance(value, str) else None
            prompt = prompt.strip() if prompt and prompt.strip() else None

            row = session.get(AssetAnnotation, asset_id)
            title_locked = row is not None and row.title_source == annotations_domain.SOURCE_USER
            sibling_title = annotations_domain.copy_auto_title_from_sibling(session, asset)

            image_bytes = None
            if (
                annotations_domain.ENGINE_VLM in engines
                or annotations_domain.ENGINE_ONNX in engines
            ):
                preview = self.store.content_path(asset.blob_key, asset.sha256, "preview")
                image_bytes = (
                    preview.read_bytes() if preview.is_file() else self.store.read(asset.blob_key)
                )

            return _Job(
                asset_id=asset_id,
                config=config,
                connection=annotation_settings.resolve_connection(config, self.settings),
                engines=engines,
                prompt=prompt,
                title_locked=title_locked,
                sibling_title=sibling_title,
                image_bytes=image_bytes,
            )

    async def _process(self, asset_id: uuid.UUID) -> None:
        try:
            job = await asyncio.to_thread(self._load_job, asset_id)
        except Exception as e:  # noqa: BLE001
            logger.exception("asset %s の推定の準備に失敗しました", asset_id)
            await asyncio.to_thread(self._finish_failed, asset_id, f"internalError: {e}")
            return
        if isinstance(job, str):
            await asyncio.to_thread(self._finish_failed, asset_id, job)
            return

        ctx = EngineContext(config=job.config, connection=job.connection)
        title: str | None = None
        tags: list[tuple[str, float | None]] | None = None
        ran: list[str] = []
        models: dict[str, str] = {}
        try:
            image: Image.Image | None = None
            if job.image_bytes is not None:
                image = Image.open(io.BytesIO(job.image_bytes))
                image.load()

            if not job.title_locked and job.prompt and annotations_domain.ENGINE_LLM in job.engines:
                if job.sibling_title:
                    title = job.sibling_title
                else:
                    self._take_call(job.config.hourly_limit)
                    title = await self.engines.title_from_prompt(job.prompt, ctx)
                ran.append(annotations_domain.ENGINE_LLM)
                models[annotations_domain.ENGINE_LLM] = job.config.llm_model

            onnx_tags: list[tuple[str, float | None]] | None = None
            if annotations_domain.ENGINE_ONNX in job.engines and image is not None:
                onnx_tags = list(await asyncio.to_thread(self.engines.onnx_tags, image, ctx))

            vlm_tags: list[tuple[str, float | None]] | None = None
            if annotations_domain.ENGINE_VLM in job.engines and image is not None:
                want_title = not job.title_locked and not job.prompt
                self._take_call(job.config.hourly_limit)
                result = await self.engines.describe_image(
                    image_to_jpeg(image), job.prompt, want_title, ctx
                )
                if want_title and result.title:
                    title = result.title
                vlm_tags = [(name, None) for name in result.tags]

            if onnx_tags is not None:
                ran.append(annotations_domain.ENGINE_ONNX)
                models[annotations_domain.ENGINE_ONNX] = job.config.onnx_model
            if vlm_tags is not None:
                ran.append(annotations_domain.ENGINE_VLM)
                models[annotations_domain.ENGINE_VLM] = job.config.vlm_model
            if onnx_tags is not None or vlm_tags is not None:
                # ONNX を先に置く(同じ名前なら確信度の付いた方を残す)。
                tags = (onnx_tags or []) + (vlm_tags or [])
        except _RateLimitedError:
            await asyncio.to_thread(self._requeue, asset_id)
            return
        except AnnotationEngineError as e:
            await asyncio.to_thread(self._finish_failed, asset_id, str(e))
            return
        except Exception as e:  # noqa: BLE001 - どの段階の失敗も記録する
            logger.exception("asset %s の推定中に予期しない例外が発生しました", asset_id)
            await asyncio.to_thread(self._finish_failed, asset_id, f"internalError: {e}")
            return

        await asyncio.to_thread(self._finish_succeeded, asset_id, title, tags, ran, models)

    def _requeue(self, asset_id: uuid.UUID) -> None:
        with self.session_factory() as session:
            row = session.get(AssetAnnotation, asset_id)
            if row is not None and row.auto_status == annotations_domain.STATUS_RUNNING:
                row.auto_status = annotations_domain.STATUS_QUEUED
                row.updated_at = _utcnow()
                session.commit()

    def _finish_failed(self, asset_id: uuid.UUID, message: str) -> None:
        with self.session_factory() as session:
            row = session.get(AssetAnnotation, asset_id)
            if row is None:
                return
            now = _utcnow()
            row.auto_status = annotations_domain.STATUS_FAILED
            row.auto_error = message[:_ERROR_MAX]
            row.auto_finished_at = now
            row.updated_at = now
            session.commit()

    def _finish_succeeded(
        self,
        asset_id: uuid.UUID,
        title: str | None,
        tags: list[tuple[str, float | None]] | None,
        ran: list[str],
        models: dict[str, str],
    ) -> None:
        with self.session_factory() as session:
            annotations_domain.apply_auto_result(session, asset_id, title=title, tags=tags)
            row = session.get(AssetAnnotation, asset_id)
            assert row is not None
            now = _utcnow()
            row.auto_status = annotations_domain.STATUS_SUCCEEDED
            row.auto_error = None
            order = [
                annotations_domain.ENGINE_LLM,
                annotations_domain.ENGINE_VLM,
                annotations_domain.ENGINE_ONNX,
            ]
            row.auto_engines = "+".join(e for e in order if e in ran) or None
            row.auto_models = models or None
            row.auto_finished_at = now
            row.updated_at = now
            session.commit()
