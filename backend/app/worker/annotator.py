"""自動タイトル・タグの推定の worker(ADR-0024 4章)。

待ち行列は `asset_annotation.auto_status = 'queued'` の行そのもの。Run の runner と同じく
api プロセス内の asyncio タスクが1件ずつ(古い依頼から)処理する。起動時に `running` の行を
`queued` に戻す(推定は来歴に加わらず、途中で止まっても害が無いため再開してよい)。

- 1 回の Run の出力には同じタイトルを付ける。LLM は Run につき1回だけ呼び、同じ Run の他の
  出力に auto タイトルが既にあればそれを写す(worker は直列なので、2枚目以降は必ず写せる)。
- LLM と VLM の呼び出しは直近1時間の回数を接続先ごとに数え(ADR-0024 8章)、上限(共通の設定
  `hourly_limit`)に達したら失敗にせず、行を `queued` に戻して次の枠まで待つ。回数はプロセス内で
  数える(再起動で 0 に戻る)。上限に達した接続先を使う組(既定 / ComfyUI の画像)の行は、
  枠が空くまで取り出さない(ほかの組の行は進める)。上限にはまだ達していなくても、先頭の行に
  要る回数が枠に収まらなかったときは、その組を「その回数が収まるまで」止める(先頭の行を
  取っては戻すのを繰り返して、ほかの組の行まで塞がないように。依頼順は変えない)。
- 回数の記録は API のスレッド(設定画面の `calls_last_hour`)からも読むので、鍵で守る。
- 送り先(接続先とモデル名)は、Asset を作った Run のプロバイダーが `comfyui` なら「ComfyUI の
  画像」の組、それ以外(アップロード、スケッチ、ほかのプロバイダー)は「既定」の組から決める
  (ADR-0024 8章)。別の組の接続先で失敗しても既定の組には切り替えず `failed` にする。
- 推定に失敗したら `failed` と理由を記録し、自動では再試行しない。

実行順(ADR-0024 6章): LLM(タイトル)は API の応答待ちなので ONNX と並行して動かす。ONNX
(`asyncio.to_thread`)を先に終え、そのタグを VLM に渡す(同じ意味のタグを付け直さない。
localized ならその訳も同じ呼び出しで返させる)。VLM が無効で LLM が有効なら、ONNX の後に
LLM で訳す。どれか1つが失敗したら、動いている他の処理を取り消して全体を `failed` にする。
LLM・VLM の呼び出しは、始める前に要る回数だけ1時間の枠が空いているかを確かめる(画像を
読む前に一度確かめ、呼ぶ直前にもう一度確かめる)。
"""

from __future__ import annotations

import asyncio
import io
import logging
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from PIL import Image
from sqlalchemy import and_, select, update
from sqlalchemy.orm import sessionmaker

from app.annotation.engines import (
    AnnotationEngineError,
    AnnotationEngines,
    EngineContext,
    FakeEngines,
    OpenAIEngines,
    image_to_jpeg,
    wants_translation,
)
from app.annotation.wd_tagger import WdTagger
from app.domain import annotation_settings, llm_connections
from app.domain import annotations as annotations_domain
from app.domain.models import Asset, AssetAnnotation, AssetKind, Run
from app.domain.storage import AssetStore
from app.domain.text_safety import (
    sanitize_external,
    sanitize_external_text,
    sanitize_external_text_or_none,
)
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
    """LLM・VLM の1時間の上限に達した(失敗にせず queued に戻す)。

    `profile` はその行の組、`needs` は接続先ごとに要る呼び出し回数(その組を止めておき、
    この回数が枠に収まったら再開するのに使う)。"""

    def __init__(self, profile: str, needs: dict[str, int]) -> None:
        super().__init__(profile)
        self.profile = profile
        self.needs = dict(needs)


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


def _pick(
    session_factory: sessionmaker, blocked_profiles: frozenset[str] = frozenset()
) -> uuid.UUID | None:
    """待ち行列の先頭を `running` にして返す。`blocked_profiles` の組(1時間の上限に達した
    接続先を使う組)の行は飛ばす。"""
    with session_factory() as session:
        query = select(AssetAnnotation.asset_id).where(
            AssetAnnotation.auto_status == annotations_domain.STATUS_QUEUED
        )
        if blocked_profiles:
            query = query.join(Asset, Asset.id == AssetAnnotation.asset_id).outerjoin(
                Run, Run.id == Asset.produced_by_run_id
            )
            is_comfyui = and_(
                Run.id.is_not(None),
                Run.provider == annotation_settings.COMFYUI_PROVIDER,
            )
            if "comfyui" in blocked_profiles:
                query = query.where(~is_comfyui)
            if "default" in blocked_profiles:
                query = query.where(is_comfyui)
        candidate = session.execute(
            query
            # ADR-0027 2章: NULL の位置と同順位の並びを SQLite の挙動(NULL が先頭)にそろえる。
            .order_by(
                AssetAnnotation.auto_requested_at.asc().nulls_first(),
                AssetAnnotation.asset_id.asc(),
            ).limit(1)
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


@dataclass(frozen=True)
class _CallPlan:
    """1件で動かす処理と、接続先ごとに要る LLM・VLM の呼び出し回数。"""

    use_llm_title: bool
    call_llm_title: bool
    use_onnx: bool
    use_vlm: bool
    translate_by_llm: bool
    needs: dict[str, int]


def _plan_calls(
    config: annotation_settings.AnnotationConfig,
    engines: list[str],
    llm: annotation_settings.Target | None,
    vlm: annotation_settings.Target | None,
    *,
    prompt: str | None,
    title_locked: bool,
    sibling_title: str | None,
    has_image: bool,
) -> _CallPlan:
    """動かす処理と要る回数を決める(画像を読む前の判定と、実行時とで同じ規則を使う)。"""
    use_llm_title = bool(not title_locked and prompt and annotations_domain.ENGINE_LLM in engines)
    call_llm_title = use_llm_title and not sibling_title
    use_onnx = annotations_domain.ENGINE_ONNX in engines and has_image
    use_vlm = annotations_domain.ENGINE_VLM in engines and has_image
    # VLM が無効で LLM が有効なら、ONNX のタグを LLM で訳す(localized で英語以外のとき)。
    translate_by_llm = (
        use_onnx
        and not use_vlm
        and annotations_domain.ENGINE_LLM in engines
        and wants_translation(config)
    )
    # 接続先ごとに要る呼び出し回数(LLM と VLM が同じ接続先なら合わせて数える)。
    needs: dict[str, int] = {}
    if llm is not None:
        needs[llm.connection_id] = (
            needs.get(llm.connection_id, 0) + int(call_llm_title) + int(translate_by_llm)
        )
    if vlm is not None:
        needs[vlm.connection_id] = needs.get(vlm.connection_id, 0) + int(use_vlm)
    return _CallPlan(
        use_llm_title=use_llm_title,
        call_llm_title=call_llm_title,
        use_onnx=use_onnx,
        use_vlm=use_vlm,
        translate_by_llm=translate_by_llm,
        needs={k: v for k, v in needs.items() if v},
    )


@dataclass
class _Job:
    asset_id: uuid.UUID
    config: annotation_settings.AnnotationConfig
    profile: str
    # 使う用途の送り先(無効なエンジンの用途は None)。
    llm: annotation_settings.Target | None
    vlm: annotation_settings.Target | None
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
        # 接続先ごとの直近1時間の呼び出し時刻(ADR-0024 8章)。worker と API のスレッド
        # (`calls_last_hour`)の両方が触るので、読み書きはすべて `_calls_lock` の中で行う。
        self._calls: dict[str, deque[float]] = {}
        self._calls_lock = threading.Lock()
        # 先頭の行に要る回数が枠に収まらず止めている組 → その行に要る回数(接続先ごと)。
        # worker のループだけが触る。その回数が枠に収まったら外す。
        self._waiting: dict[str, dict[str, int]] = {}
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

    def calls_last_hour(self, connection_id: str | None = None) -> int:
        """直近1時間の LLM・VLM の呼び出し回数。`connection_id` を省くと全接続先の合計。
        API のスレッドからも呼ばれる。"""
        with self._calls_lock:
            self._trim_calls(time.monotonic())
            if connection_id is None:
                return sum(len(calls) for calls in self._calls.values())
            calls = self._calls.get(connection_id)
            return len(calls) if calls is not None else 0

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
        """窓の外に出た記録を捨てる。`_calls_lock` を持って呼ぶ。"""
        for connection_id in list(self._calls):
            calls = self._calls.get(connection_id)
            if calls is None:
                continue
            while calls and now - calls[0] >= _WINDOW_SECONDS:
                calls.popleft()
            if not calls:
                self._calls.pop(connection_id, None)

    def _fits(self, needs: dict[str, int], limit: int) -> bool:
        """要る呼び出し回数(接続先ごと)が枠に収まるか。枠が空なら、上限より多く要っても
        収まるとみなす(上限が小さすぎて永久に待つのを避ける)。"""
        with self._calls_lock:
            self._trim_calls(time.monotonic())
            for connection_id, needed in needs.items():
                calls = self._calls.get(connection_id)
                used = len(calls) if calls is not None else 0
                if needed and used and used + needed > limit:
                    return False
            return True

    def _ensure_capacity(self, profile: str, needs: dict[str, int], limit: int) -> None:
        """この1件で要る呼び出し回数が枠に収まるかを先に確かめる(途中で上限に当たって、
        一部の呼び出しだけが無駄になるのを避ける)。収まらなければ _RateLimitedError。"""
        if not self._fits(needs, limit):
            raise _RateLimitedError(profile, needs)

    def _record_call(self, connection_id: str) -> None:
        with self._calls_lock:
            self._calls.setdefault(connection_id, deque()).append(time.monotonic())

    def _limit_reached(self, connection_id: str, limit: int) -> bool:
        return self.calls_last_hour(connection_id) >= limit

    def _blocked_profiles(self, config: annotation_settings.AnnotationConfig) -> frozenset[str]:
        """取り出さない組。1時間の上限に達した接続先を使う組と、先頭の行に要る回数がまだ枠に
        収まらない組(`_waiting`)。収まるようになった組は `_waiting` から外す。"""
        if not config.api_engines_enabled:
            self._waiting.clear()
            return frozenset()
        for profile, needs in list(self._waiting.items()):
            if self._fits(needs, config.hourly_limit):
                del self._waiting[profile]
        return frozenset(
            profile
            for profile in annotation_settings.PROFILES
            if profile in self._waiting
            or any(
                self._limit_reached(connection_id, config.hourly_limit)
                for connection_id in config.connection_ids_for(profile)
            )
        )

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
                blocked = self._blocked_profiles(config)
                if len(blocked) == len(annotation_settings.PROFILES):
                    await self._sleep()
                    continue
                picked = await asyncio.to_thread(_pick, self.session_factory, blocked)
            except Exception:
                logger.exception("推定の待ち行列の取得に失敗しました")
                await self._sleep()
                continue

            if picked is None:
                await self._sleep()
                continue

            try:
                requeued = await self._process(picked)
            except Exception:
                logger.exception("asset %s の推定で想定外の例外が発生しました", picked)
                continue
            if requeued:
                # 上限で戻したときは、同じ行をすぐ取り直さないよう少し待つ。
                await self._sleep()

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
            provider: str | None = None
            if asset.kind == AssetKind.GENERATED and asset.produced_by_run_id is not None:
                run = session.get(Run, asset.produced_by_run_id)
                prompt = run.prompt if run is not None else None
                provider = run.provider if run is not None else None
            elif isinstance(asset.embedded_meta, dict):
                value = asset.embedded_meta.get("prompt")
                prompt = value if isinstance(value, str) else None
            prompt = prompt.strip() if prompt and prompt.strip() else None

            # 送り先(ADR-0024 8章)。使う用途だけ解決する。接続先が無ければ失敗にする(既定の
            # 組には切り替えない)。
            profile = annotation_settings.profile_for_provider(provider)
            targets: dict[str, annotation_settings.Target | None] = {}
            for purpose, engine in (
                ("llm", annotations_domain.ENGINE_LLM),
                ("vlm", annotations_domain.ENGINE_VLM),
            ):
                if engine not in engines:
                    targets[purpose] = None
                    continue
                try:
                    targets[purpose] = annotation_settings.resolve_target(
                        config, self.settings, profile, purpose
                    )
                except llm_connections.ConnectionNotFoundError:
                    return t("annotations.connectionMissing")

            row = session.get(AssetAnnotation, asset_id)
            title_locked = row is not None and row.title_source == annotations_domain.SOURCE_USER
            sibling_title = annotations_domain.copy_auto_title_from_sibling(session, asset)

            # 画像(preview)を読む前に、要る回数が枠に収まるかを確かめる(収まらない行を取っては
            # 戻すたびに、オブジェクトストレージから画像を取り寄せないように)。
            will_read_image = (
                annotations_domain.ENGINE_VLM in engines
                or annotations_domain.ENGINE_ONNX in engines
            )
            plan = _plan_calls(
                config,
                engines,
                targets["llm"],
                targets["vlm"],
                prompt=prompt,
                title_locked=title_locked,
                sibling_title=sibling_title,
                has_image=will_read_image,
            )
            if plan.needs:
                self._ensure_capacity(profile, plan.needs, config.hourly_limit)

            image_bytes = None
            if will_read_image:
                preview = self.store.open_content(asset.blob_key, asset.sha256, "preview")
                image_bytes = (
                    preview.read_all() if preview is not None else self.store.read(asset.blob_key)
                )

            return _Job(
                asset_id=asset_id,
                config=config,
                profile=profile,
                llm=targets["llm"],
                vlm=targets["vlm"],
                engines=engines,
                prompt=prompt,
                title_locked=title_locked,
                sibling_title=sibling_title,
                image_bytes=image_bytes,
            )

    async def _process(self, asset_id: uuid.UUID) -> bool:
        """1件を処理する。1時間の上限で `queued` に戻したら True。"""
        try:
            job = await asyncio.to_thread(self._load_job, asset_id)
        except _RateLimitedError as e:
            await self._wait_for_capacity(asset_id, e)
            return True
        except Exception as e:  # noqa: BLE001
            logger.exception("asset %s の推定の準備に失敗しました", asset_id)
            await asyncio.to_thread(self._finish_failed, asset_id, f"internalError: {e}")
            return False
        if isinstance(job, str):
            await asyncio.to_thread(self._finish_failed, asset_id, job)
            return False

        ctx = EngineContext(config=job.config, llm=job.llm, vlm=job.vlm)
        try:
            outcome = await self._run_engines(job, ctx)
        except _RateLimitedError as e:
            await self._wait_for_capacity(asset_id, e)
            return True
        except AnnotationEngineError as e:
            await asyncio.to_thread(self._finish_failed, asset_id, str(e))
            return False
        except Exception as e:  # noqa: BLE001 - どの段階の失敗も記録する
            logger.exception("asset %s の推定中に予期しない例外が発生しました", asset_id)
            await asyncio.to_thread(self._finish_failed, asset_id, f"internalError: {e}")
            return False

        title, tags, ran, models = outcome
        await asyncio.to_thread(self._finish_succeeded, asset_id, title, tags, ran, models)
        return False

    async def _wait_for_capacity(self, asset_id: uuid.UUID, error: _RateLimitedError) -> None:
        """行を `queued` に戻し、その組を要る回数が枠に収まるまで止める(依頼順は変えない)。"""
        self._waiting[error.profile] = error.needs
        await asyncio.to_thread(self._requeue, asset_id)

    async def _run_engines(
        self, job: _Job, ctx: EngineContext
    ) -> tuple[str | None, list[tuple[str, float | None]] | None, list[str], dict[str, str]]:
        """エンジンを動かして (タイトル, タグ, 動かしたエンジン, モデル名) を返す。順序は
        モジュールの docstring のとおり。"""
        config = job.config
        engines = job.engines
        # 画像のデコードと縮小も CPU を使うので、イベントループの外で行う。
        image: Image.Image | None = None
        if job.image_bytes is not None:
            image = await asyncio.to_thread(_decode_image, job.image_bytes)

        plan = _plan_calls(
            config,
            engines,
            job.llm,
            job.vlm,
            prompt=job.prompt,
            title_locked=job.title_locked,
            sibling_title=job.sibling_title,
            has_image=image is not None,
        )
        use_llm_title = plan.use_llm_title
        call_llm_title = plan.call_llm_title
        use_onnx = plan.use_onnx
        use_vlm = plan.use_vlm
        translate_by_llm = plan.translate_by_llm
        llm_id = job.llm.connection_id if job.llm is not None else None
        vlm_id = job.vlm.connection_id if job.vlm is not None else None
        if plan.needs:
            self._ensure_capacity(job.profile, plan.needs, config.hourly_limit)

        title: str | None = None
        ran: list[str] = []
        models: dict[str, str] = {}

        title_task: asyncio.Task[str] | None = None
        if call_llm_title:
            assert job.prompt is not None and llm_id is not None
            self._record_call(llm_id)
            title_task = asyncio.create_task(self.engines.title_from_prompt(job.prompt, ctx))
        elif use_llm_title:
            title = job.sibling_title

        try:
            onnx_tags: list[tuple[str, float | None]] | None = None
            if use_onnx:
                onnx_tags = list(await asyncio.to_thread(self.engines.onnx_tags, image, ctx))
            onnx_names = [name for name, _ in onnx_tags or []]

            # タイトルの LLM が既に失敗していれば、VLM を呼ぶ前にここで止める(無駄な課金を
            # 避ける)。
            if title_task is not None and title_task.done():
                title_task.result()

            vlm_tags: list[tuple[str, float | None]] | None = None
            translations: dict[str, str] = {}
            if use_vlm:
                assert image is not None
                want_title = not job.title_locked and not job.prompt
                assert vlm_id is not None
                self._record_call(vlm_id)
                image_jpeg = await asyncio.to_thread(image_to_jpeg, image)
                result = await self.engines.describe_image(
                    image_jpeg, job.prompt, want_title, ctx, onnx_names or None
                )
                if want_title and result.title:
                    title = result.title
                vlm_tags = [(name, None) for name in result.tags]
                translations = result.translations
            elif translate_by_llm and onnx_names:
                assert llm_id is not None
                self._record_call(llm_id)
                translations = await self.engines.translate_tags(onnx_names, ctx)

            if title_task is not None:
                title = await title_task
        except BaseException:
            if title_task is not None and not title_task.done():
                title_task.cancel()
                try:
                    await title_task
                except BaseException:  # noqa: BLE001 - 取り消しの後始末
                    pass
            raise

        # 記録するモデル名は、この画像に実際に使った組のもの(ADR-0024 8章)。
        if (use_llm_title or (translate_by_llm and onnx_names)) and job.llm is not None:
            ran.append(annotations_domain.ENGINE_LLM)
            models[annotations_domain.ENGINE_LLM] = job.llm.model

        tags: list[tuple[str, float | None]] | None = None
        if onnx_tags is not None:
            ran.append(annotations_domain.ENGINE_ONNX)
            models[annotations_domain.ENGINE_ONNX] = config.onnx_model
        if vlm_tags is not None and job.vlm is not None:
            ran.append(annotations_domain.ENGINE_VLM)
            models[annotations_domain.ENGINE_VLM] = job.vlm.model
        if onnx_tags is not None or vlm_tags is not None:
            # ONNX(英語)→ その訳 → VLM の順に並べる(同じ名前なら先のもの、つまり確信度の
            # 付いた方を残す)。訳は元のタグの確信度を引き継ぐ。
            translated = translated_tags(onnx_tags or [], translations)
            tags = (onnx_tags or []) + translated + (vlm_tags or [])
        return title, tags, ran, models

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
            # 推定のエンジン(外部の LLM・VLM など)のエラー文言は外部由来。NUL などを除く(ADR-0027)。
            row.auto_error = sanitize_external_text(message)[:_ERROR_MAX]
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
        # LLM・VLM の出力は外部由来。NUL などを除いてから保存する(ADR-0027)。
        title = sanitize_external_text_or_none(title)
        if tags is not None:
            tags = [(sanitize_external_text(name), score) for name, score in tags]
        models = sanitize_external(models)
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


def _decode_image(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


def translated_tags(
    onnx_tags: list[tuple[str, float | None]], translations: dict[str, str]
) -> list[tuple[str, float | None]]:
    """ONNX のタグの訳をタグにする(元のタグの確信度を引き継ぐ)。訳の無いもの・元と同じ
    表記のものは足さない。キーの照合は大文字・小文字と `_` / 空白の違いを無視する。"""
    if not translations:
        return []

    def key(name: str) -> str:
        return " ".join(name.replace("_", " ").lower().split())

    by_key = {key(original): translated for original, translated in translations.items()}
    result: list[tuple[str, float | None]] = []
    for name, score in onnx_tags:
        translated = by_key.get(key(name))
        if translated and key(translated) != key(name):
            result.append((translated, score))
    return result
