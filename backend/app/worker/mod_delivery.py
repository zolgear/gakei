"""非同期のイベント(`asset.ingested`、`run.finished`)の配達の worker(ADR-0051 5章)。

待ち行列は `mod_delivery` の行そのもの(Run の表と同じ考え方。ADR-0005)。api プロセス内の
asyncio タスクが、`next_attempt_at` を過ぎた `queued` の行を古いものから1件ずつ配る。

- 送れたら(応答が契約どおりなら)`done` にし、返った判定を `mod_verdict` に足す。
- 失敗(時間切れ、例外、HTTP の失敗、契約に合わない応答)したら、間隔を延ばしながら
  `MAX_ATTEMPTS` 回まで送り直し、最後は `failed` にする。失敗は GAKEI の動作を止めない。
- 配る時点でその Mod が無効になっていたら、送らずに `done` にする(呼び出しの記録は `skipped`)。
- 起動時に `delivering` のまま残った行を `queued` に戻す(配り直す)。
- 呼び出しは、成功も失敗も `mod_invocation` に残す。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import sessionmaker

from app.domain.models import ModDelivery
from app.domain.text_safety import sanitize_external_text
from app.mods import api as contract
from app.mods.runtime import (
    STATUS_DELIVERING,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_QUEUED,
    CallResult,
    ImageOpener,
    ModRuntime,
    Subscriber,
)

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
# 送り直すまでの間隔(秒)。回数ごとに倍にする(30, 60, 120, 240)。
BACKOFF_BASE_SECONDS = 30.0
BACKOFF_MAX_SECONDS = 3600.0
_POLL_INTERVAL_SECONDS = 1.0


def _utcnow() -> datetime:
    return datetime.now(UTC)


def reset_delivering(session_factory: sessionmaker) -> int:
    """起動時に呼ぶ。`delivering` のままの行を `queued` に戻す。"""
    with session_factory() as session:
        result = session.execute(
            update(ModDelivery)
            .where(ModDelivery.status == STATUS_DELIVERING)
            .values(status=STATUS_QUEUED, updated_at=_utcnow())
        )
        session.commit()
        return result.rowcount or 0


def backoff_seconds(attempts: int, base: float = BACKOFF_BASE_SECONDS) -> float:
    """`attempts` 回失敗した後、次に送るまでの間隔。"""
    return min(base * (2 ** max(attempts - 1, 0)), BACKOFF_MAX_SECONDS)


def _pick(session_factory: sessionmaker) -> uuid.UUID | None:
    """送る時刻を過ぎた `queued` の行を1件 `delivering` にして返す。"""
    with session_factory() as session:
        now = _utcnow()
        candidate = session.execute(
            select(ModDelivery.id)
            .where(ModDelivery.status == STATUS_QUEUED, ModDelivery.next_attempt_at <= now)
            .order_by(ModDelivery.next_attempt_at.asc(), ModDelivery.created_at.asc())
            .limit(1)
        ).scalar_one_or_none()
        if candidate is None:
            return None
        result = session.execute(
            update(ModDelivery)
            .where(ModDelivery.id == candidate, ModDelivery.status == STATUS_QUEUED)
            .values(status=STATUS_DELIVERING, updated_at=now)
        )
        session.commit()
        if (result.rowcount or 0) == 0:
            return None
        return candidate


class ModDeliveryWorker:
    """lifespan で起動する配達の worker。"""

    def __init__(
        self,
        session_factory: sessionmaker,
        runtime: ModRuntime,
        *,
        backoff_base_seconds: float = BACKOFF_BASE_SECONDS,
        max_attempts: int = MAX_ATTEMPTS,
    ) -> None:
        self.session_factory = session_factory
        self.runtime = runtime
        self.backoff_base_seconds = backoff_base_seconds
        self.max_attempts = max_attempts
        self._wake = asyncio.Event()
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._event_loop: asyncio.AbstractEventLoop | None = None

    def notify(self) -> None:
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

    async def start(self) -> int:
        self._event_loop = asyncio.get_running_loop()
        self.runtime.bind_loop(self._event_loop)
        self.runtime.set_wake(self.notify)
        count = await asyncio.to_thread(reset_delivering, self.session_factory)
        self._task = asyncio.create_task(self._loop(), name="gakei-mod-delivery")
        return count

    async def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._task is not None:
            await self._task
        self.runtime.set_wake(None)
        self.runtime.bind_loop(None)

    async def _sleep(self) -> None:
        self._wake.clear()
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=_POLL_INTERVAL_SECONDS)
        except TimeoutError:
            pass

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                picked = await asyncio.to_thread(_pick, self.session_factory)
            except Exception:
                logger.exception("Mod の配達の待ち行列の取得に失敗しました")
                await self._sleep()
                continue
            if picked is None:
                await self._sleep()
                continue
            try:
                await self.deliver(picked)
            except Exception:
                logger.exception("Mod の配達 %s で想定外の例外が発生しました", picked)
                await asyncio.to_thread(self._finish_failure, picked, "internal error", True)

    async def run_once(self) -> bool:
        """待ち行列を1件だけ処理する(テスト用)。処理したら True。"""
        picked = await asyncio.to_thread(_pick, self.session_factory)
        if picked is None:
            return False
        await self.deliver(picked)
        return True

    # -- 1件 ----------------------------------------------------------------------

    def _load(self, delivery_id: uuid.UUID) -> tuple[str, str, dict] | None:
        with self.session_factory() as session:
            row = session.get(ModDelivery, delivery_id)
            if row is None:
                return None
            return row.mod_kind, row.mod_id, dict(row.payload)

    async def deliver(self, delivery_id: uuid.UUID) -> None:
        loaded = await asyncio.to_thread(self._load, delivery_id)
        if loaded is None:
            return
        kind, mod_id, payload = loaded
        try:
            event = contract.event_from_json(payload)
        except (KeyError, TypeError, ValueError) as e:
            await asyncio.to_thread(
                self._finish_failure, delivery_id, f"invalid payload: {e}", True
            )
            return
        sub = self.runtime.find_subscriber(kind, mod_id)
        if sub is None or event.event not in sub.events:
            await asyncio.to_thread(self._finish_skipped, delivery_id, event, kind, mod_id)
            return
        opener = ImageOpener(self.runtime.session_factory, self.runtime.store)
        result = await self.runtime.call(sub, event, opener)
        if result.failed:
            await asyncio.to_thread(self._finish_failed_attempt, delivery_id, event, sub, result)
            return
        await asyncio.to_thread(self._finish_done, delivery_id, event, sub, result)

    def _finish_done(
        self,
        delivery_id: uuid.UUID,
        event: contract.Event,
        sub: Subscriber,
        result: CallResult,
    ) -> None:
        with self.session_factory() as session:
            row = session.get(ModDelivery, delivery_id)
            if row is None:
                return
            invocation_id = self.runtime.record_invocation(
                event=event,
                sub=sub,
                result=result,
                run_id=row.run_id,
                asset_id=row.asset_id,
                session=session,
            )
            self.runtime.save_verdicts(
                session,
                event=event,
                sub=sub,
                invocation_id=invocation_id,
                verdicts=result.verdicts,
            )
            now = _utcnow()
            row.status = STATUS_DONE
            row.attempts = row.attempts + 1
            row.last_error = None
            row.finished_at = now
            row.updated_at = now
            session.commit()

    def _finish_failed_attempt(
        self,
        delivery_id: uuid.UUID,
        event: contract.Event,
        sub: Subscriber,
        result: CallResult,
    ) -> None:
        with self.session_factory() as session:
            row = session.get(ModDelivery, delivery_id)
            if row is None:
                return
            self.runtime.record_invocation(
                event=event,
                sub=sub,
                result=result,
                run_id=row.run_id,
                asset_id=row.asset_id,
                session=session,
            )
            self._apply_failure(row, result.error or result.outcome, give_up=False)
            session.commit()

    def _finish_failure(self, delivery_id: uuid.UUID, message: str, give_up: bool) -> None:
        with self.session_factory() as session:
            row = session.get(ModDelivery, delivery_id)
            if row is None or row.status != STATUS_DELIVERING:
                return
            self._apply_failure(row, message, give_up=give_up)
            session.commit()

    def _apply_failure(self, row: ModDelivery, message: str, *, give_up: bool) -> None:
        now = _utcnow()
        row.attempts = row.attempts + 1
        row.last_error = sanitize_external_text(message)[:1000]
        row.updated_at = now
        if give_up or row.attempts >= self.max_attempts:
            row.status = STATUS_FAILED
            row.finished_at = now
        else:
            row.status = STATUS_QUEUED
            row.next_attempt_at = now + timedelta(
                seconds=backoff_seconds(row.attempts, self.backoff_base_seconds)
            )

    def _finish_skipped(
        self, delivery_id: uuid.UUID, event: contract.Event, kind: str, mod_id: str
    ) -> None:
        """その Mod が無効になっていた。送らずに `done` にし、`skipped` を残す。"""
        from app.mods import config as mods_config

        with self.session_factory() as session:
            row = session.get(ModDelivery, delivery_id)
            if row is None:
                return
            name = mod_id
            if kind == mods_config.KIND_WEBHOOK:
                webhook = mods_config.find_webhook(mods_config.load_webhooks(session), mod_id)
                name = webhook.name if webhook is not None else mod_id
            else:
                folder = self.runtime.folder(mod_id)
                if folder is not None and folder.manifest is not None:
                    name = folder.manifest.name
            placeholder = Subscriber(
                kind=kind,
                id=mod_id,
                name=name,
                events=frozenset(),
                filters=mods_config.Filters(),
                timeout_seconds=0,
                on_failure="allow",
                order=0,
            )
            self.runtime.record_invocation(
                event=event,
                sub=placeholder,
                result=CallResult(outcome="skipped", duration_ms=0),
                run_id=row.run_id,
                asset_id=row.asset_id,
                session=session,
            )
            now = _utcnow()
            row.status = STATUS_DONE
            row.finished_at = now
            row.updated_at = now
            session.commit()
