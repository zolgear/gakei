"""ジョブ実行。ADR-0005 の「キューは run テーブルそのもの」をプロセス内 asyncio タスクで実装する
(ローカルMVPでは別プロセス worker の代わりに、api プロセス内で動かす。ADR-0008 参照)。

ADR-0013: プロバイダーごとに実行レーン(asyncio タスク)を1本持つ。GPU で数分かかる
ComfyUI の実行が、OpenAI の実行を待たせないようにするため。同じプロバイダーの中は、
これまでどおり直列に実行する。

状態遷移は queued → running → succeeded | failed | canceled。
更新してよいのは run.status / started_at / finished_at /
error_* / usage / provider_request_id のみ。
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import select, update
from sqlalchemy.orm import sessionmaker

from app.domain import asset_groups as asset_groups_domain
from app.domain import assets as assets_domain
from app.domain.models import Asset, AssetKind, Run, RunInput, RunInputRole, RunStatus
from app.domain.storage import AssetStore
from app.i18n import t
from app.providers.base import (
    ImageProvider,
    InputImage,
    ProgressEvent,
    ProviderError,
    RunRequest,
    StepProgressEvent,
)
from app.providers.registry import ProviderRegistry
from app.worker.progress import ProgressBus

if TYPE_CHECKING:
    from app.worker.annotator import Annotator

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 1.0


def _utcnow() -> datetime:
    return datetime.now(UTC)


def reset_interrupted_runs(session_factory: sessionmaker) -> int:
    """起動時に呼ぶ。`running` のままの行は再開せず `failed` + `interrupted` にする。

    (本線では `queued` に戻して再試行するが、ローカルMVPでは `--reload` での頻繁な再起動中に
    気付かないうちに課金されるのを避けるため、ADR-0008 の方針どおり failed にする。)
    """
    with session_factory() as session:
        rows = session.execute(select(Run).where(Run.status == RunStatus.RUNNING)).scalars().all()
        now = _utcnow()
        for row in rows:
            row.status = RunStatus.FAILED
            row.error_code = "interrupted"
            row.error_message = t("worker.interruptedByRestart")
            row.finished_at = now
        session.commit()
        return len(rows)


def mark_unregistered_provider_runs_failed(
    session_factory: sessionmaker, known_providers: set[str]
) -> int:
    """起動時に呼ぶ。登録されていない provider の `queued` な Run を
    `failed` + `providerUnavailable` にする(ADR-0013)。

    (ComfyUI が無効化された、または FAKE_PROVIDER を変えた後に前回投入した Run が
    残っているケースなど。)
    """
    with session_factory() as session:
        rows = session.execute(select(Run).where(Run.status == RunStatus.QUEUED)).scalars().all()
        now = _utcnow()
        count = 0
        for row in rows:
            if row.provider in known_providers:
                continue
            row.status = RunStatus.FAILED
            row.error_code = "providerUnavailable"
            row.error_message = t("worker.providerNotRegistered", provider=row.provider)
            row.finished_at = now
            count += 1
        session.commit()
        return count


def _pick_and_start_run(session_factory: sessionmaker, provider_name: str) -> Run | None:
    """`queued` かつ `provider == provider_name` を1件、古い順に取って `running` にする。

    競合(cancel との同時実行)に備え、UPDATE の WHERE に status='queued' を含めて
    再確認する。0件しか更新できなければ、他の処理が先に状態を変えたとみなして諦める。
    """
    with session_factory() as session:
        candidate = session.execute(
            select(Run)
            .where(Run.status == RunStatus.QUEUED, Run.provider == provider_name)
            .order_by(Run.queued_at.asc())
            .limit(1)
        ).scalar_one_or_none()
        if candidate is None:
            return None

        result = session.execute(
            update(Run)
            .where(Run.id == candidate.id, Run.status == RunStatus.QUEUED)
            .values(status=RunStatus.RUNNING, started_at=_utcnow())
        )
        session.commit()
        if result.rowcount == 0:
            return None

        session.refresh(candidate)
        return candidate


def _load_inputs(
    session_factory: sessionmaker, store: AssetStore, run_id: uuid.UUID
) -> list[InputImage]:
    with session_factory() as session:
        rows = (
            session.execute(
                select(RunInput).where(RunInput.run_id == run_id).order_by(RunInput.position)
            )
            .scalars()
            .all()
        )
        inputs: list[InputImage] = []
        for run_input in rows:
            # provider に渡すのは image / mask のみ。reference は参照表示専用。
            if run_input.role not in (RunInputRole.IMAGE, RunInputRole.MASK):
                continue
            asset = session.get(Asset, run_input.asset_id)
            assert asset is not None
            data = store.read(asset.blob_key)
            inputs.append(
                InputImage(
                    role=run_input.role, position=run_input.position, data=data, mime=asset.mime
                )
            )
        return inputs


def _save_partial(partial_dir: Path, index: int, data: bytes) -> None:
    partial_dir.mkdir(parents=True, exist_ok=True)
    (partial_dir / f"{index}.png").write_bytes(data)


def _cleanup_partial_dir(partial_dir: Path) -> None:
    if partial_dir.exists():
        shutil.rmtree(partial_dir, ignore_errors=True)


def _finish_run_succeeded(
    session_factory: sessionmaker,
    store: AssetStore,
    run_id: uuid.UUID,
    result,
    annotator: Annotator | None = None,
) -> list[uuid.UUID]:
    """出力を取り込んで Run を成功にする。`annotator` があれば、取り込み時の自動推定
    (ADR-0024 4章。設定がオンのときだけ)を同じトランザクションで待ち行列に入れる。"""
    from app.domain import annotations as annotations_domain

    annotation_queued = False
    with session_factory() as session:
        run = session.get(Run, run_id)
        assert run is not None

        output_ids: list[uuid.UUID] = []
        for index, output in enumerate(result.outputs):
            asset = assets_domain.ingest(
                session,
                store,
                output.data,
                AssetKind.GENERATED,
                produced_by_run_id=run_id,
                output_index=index,
                created_by_user_id=run.created_by_user_id,
            )
            output_ids.append(asset.id)
            if annotator is not None:
                annotation_queued = (
                    annotations_domain.enqueue_on_ingest(session, asset, annotator.settings)
                    or annotation_queued
                )

        # ADR-0022: 生成時にグループが指定されていれば、取り込んだ出力を同じトランザクションで
        # そのグループに入れる(既に入っているものは無視)。実行までにグループが削除されて
        # いたら何もせず、Run は成功のままにする。
        if run.asset_group_id is not None and output_ids:
            group = asset_groups_domain.get_active_group_or_none(session, run.asset_group_id)
            if group is not None:
                asset_groups_domain.add_members(session, group, output_ids)

        run.status = RunStatus.SUCCEEDED
        run.finished_at = _utcnow()
        run.usage = result.usage
        run.provider_request_id = result.provider_request_id
        session.commit()
    if annotation_queued and annotator is not None:
        annotator.notify()
    return output_ids


def _finish_run_failed(
    session_factory: sessionmaker,
    run_id: uuid.UUID,
    error_code: str,
    error_message: str,
    provider_request_id: str | None,
) -> None:
    with session_factory() as session:
        run = session.get(Run, run_id)
        assert run is not None
        run.status = RunStatus.FAILED
        run.finished_at = _utcnow()
        run.error_code = error_code
        run.error_message = error_message[:2000]
        if provider_request_id:
            run.provider_request_id = provider_request_id
        session.commit()


class Runner:
    """lifespan で起動する asyncio タスク群。プロバイダーごとに1レーン持つ(ADR-0013)。"""

    def __init__(
        self,
        session_factory: sessionmaker,
        store: AssetStore,
        registry: ProviderRegistry,
        progress_bus: ProgressBus,
        data_dir: Path,
        *,
        annotator: Annotator | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.annotator = annotator
        self.store = store
        self.registry = registry
        self.progress_bus = progress_bus
        self.data_dir = data_dir
        self._wake_events: dict[str, asyncio.Event] = {
            name: asyncio.Event() for name in registry.providers
        }
        self._stop_event = asyncio.Event()
        self._tasks: list[asyncio.Task] = []

    def notify(self) -> None:
        """新規 Run 投入時に呼ぶ。どのレーン宛かは分からないので、全レーンを起こす。"""
        for event in self._wake_events.values():
            event.set()

    async def start(self) -> int:
        reset_count = await asyncio.to_thread(reset_interrupted_runs, self.session_factory)
        known_providers = set(self.registry.providers)
        await asyncio.to_thread(
            mark_unregistered_provider_runs_failed, self.session_factory, known_providers
        )
        self._tasks = [
            asyncio.create_task(self._loop(name), name=f"gakei-runner-{name}")
            for name in self.registry.providers
        ]
        return reset_count

    def ensure_lane(self, provider_name: str) -> None:
        """`provider_name` 用の実行レーンが無ければ起動する(ADR-0013 7章)。

        起動時にレジストリに無かったプロバイダー(画面から接続した ComfyUI)向け。
        すでにレーンがあれば(切り離し後も、レーン自体は動いたままのため)何もせず、
        念のため起こすだけにする。
        """
        if provider_name not in self._wake_events:
            self._wake_events[provider_name] = asyncio.Event()

        task_name = f"gakei-runner-{provider_name}"
        if not any(t.get_name() == task_name and not t.done() for t in self._tasks):
            self._tasks.append(asyncio.create_task(self._loop(provider_name), name=task_name))

        self._wake_events[provider_name].set()

    async def stop(self) -> None:
        self._stop_event.set()
        for event in self._wake_events.values():
            event.set()
        for task in self._tasks:
            await task

    async def _loop(self, provider_name: str) -> None:
        wake_event = self._wake_events[provider_name]
        while not self._stop_event.is_set():
            try:
                picked = await asyncio.to_thread(
                    _pick_and_start_run, self.session_factory, provider_name
                )
            except Exception:
                logger.exception(
                    "次に実行する run の取得に失敗しました(provider=%s)", provider_name
                )
                await asyncio.sleep(_POLL_INTERVAL_SECONDS)
                continue

            if picked is None:
                wake_event.clear()
                try:
                    await asyncio.wait_for(wake_event.wait(), timeout=_POLL_INTERVAL_SECONDS)
                except TimeoutError:
                    pass
                continue

            # 差し替え(テストでのスタブ差し込み含む)を実行のたびに反映できるよう、
            # レジストリを都度引く(レーン開始時点の参照をキャッシュしない)。
            provider = self.registry.get(provider_name)
            if provider is None:
                await self._mark_failed(
                    picked.id,
                    "providerUnavailable",
                    t("worker.providerNotRegistered", provider=provider_name),
                    None,
                )
                continue

            try:
                await self._execute_run(picked, provider)
            except Exception:
                # _execute_run 内の失敗は原則 failed として記録されるが、
                # それでも漏れた例外でループ自体が死なないようにする最後の砦。
                logger.exception("run %s の処理で想定外の例外が発生しました", picked.id)

    async def _execute_run(self, run: Run, provider: ImageProvider) -> None:
        await self.progress_bus.publish(run.id, {"type": "status", "status": "running"})

        partial_dir = self.data_dir / "tmp" / "partial" / str(run.id)
        partial_counter = itertools.count()

        async def on_progress(event: ProgressEvent) -> None:
            if isinstance(event, StepProgressEvent):
                await self.progress_bus.publish(
                    run.id,
                    {
                        "type": "progress",
                        "value": event.value,
                        "max": event.max,
                        "node": event.node,
                    },
                )
                return

            index = next(partial_counter)
            await asyncio.to_thread(_save_partial, partial_dir, index, event.data)
            await self.progress_bus.publish(
                run.id,
                {
                    "type": "partial",
                    "index": index,
                    "output_index": event.output_index,
                    "partial_index": event.partial_index,
                },
            )

        try:
            # 入力読み込み・実行・出力の ingest のどこで失敗しても failed として記録する。
            inputs = await asyncio.to_thread(_load_inputs, self.session_factory, self.store, run.id)
            request = RunRequest(
                run_id=run.id,
                operation=run.operation,
                model=run.model,
                prompt=run.prompt,
                params=run.params or {},
                inputs=inputs,
            )
            result = await provider.execute(request, on_progress)
            output_ids = await asyncio.to_thread(
                _finish_run_succeeded,
                self.session_factory,
                self.store,
                run.id,
                result,
                self.annotator,
            )
        except ProviderError as e:
            await self._mark_failed(run.id, e.code, e.message, e.request_id)
        except Exception as e:  # noqa: BLE001 - どの段階の失敗もすべて記録する
            logger.exception("run %s の処理中に予期しない例外が発生しました", run.id)
            await self._mark_failed(run.id, "internalError", str(e), None)
        else:
            await self.progress_bus.publish(
                run.id,
                {
                    "type": "status",
                    "status": "succeeded",
                    "output_asset_ids": [str(i) for i in output_ids],
                },
            )
        finally:
            await asyncio.to_thread(_cleanup_partial_dir, partial_dir)

    async def _mark_failed(
        self,
        run_id: uuid.UUID,
        error_code: str,
        error_message: str,
        provider_request_id: str | None,
    ) -> None:
        """Run を failed として記録する。記録自体に失敗してもループは止めない。"""
        try:
            await asyncio.to_thread(
                _finish_run_failed,
                self.session_factory,
                run_id,
                error_code,
                error_message,
                provider_request_id,
            )
        except Exception:
            logger.exception("run %s を failed として記録できませんでした", run_id)
            return

        await self.progress_bus.publish(
            run_id,
            {
                "type": "status",
                "status": "failed",
                "error_code": error_code,
                "error_message": error_message,
            },
        )
