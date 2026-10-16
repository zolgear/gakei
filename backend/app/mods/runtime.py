"""Mod の実行時の状態と呼び出し(ADR-0051)。

`ModRuntime` はプロセスに1つ(`app.state.mods`)。有効な購読(Webhook と Python の Mod)を
メモリに持ち、設定の保存のたびに `reload` で作り直す。有効な購読が無ければ、イベントを組み立てず、
DB にも触れない(`wants` はメモリだけを見る)。

- `ai.before_send`(同期): `before_send` が有効な購読を順番に呼び、最初の拒否で止める。時間切れ・
  例外・HTTP の失敗・契約に合わない応答は、その Mod の失敗時の扱いに従う(`block` は拒否と同じで、
  理由は「チェックに失敗した」)。呼び出しはすべて `mod_invocation` に残す。
- `asset.ingested` / `run.finished`(非同期): `enqueue_*` が呼び出し側のトランザクションで
  `mod_delivery` に積み、commit の後に `notify` で配達の worker(`app/worker/mod_delivery.py`)を
  起こす。

Python の Mod は、マニフェストを起動のときに読み(`scan`)、有効なものだけ `reload` で import する。
読み込みに失敗した Mod は、エラーを覚えて購読から外すだけで、起動は止めない。
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx
from sqlalchemy.orm import Session, sessionmaker

from app.domain.models import Asset, ModDelivery, ModInvocation, ModVerdict, Run
from app.domain.text_safety import sanitize_external_text_or_none
from app.i18n import t
from app.mods import api as contract
from app.mods import config as mods_config
from app.mods import events as mods_events
from app.mods import loader
from app.mods import webhook as webhook_send
from app.version import get_version

if TYPE_CHECKING:
    from app.config import Settings
    from app.domain.storage import AssetStore

logger = logging.getLogger(__name__)

STATUS_QUEUED = "queued"
STATUS_DELIVERING = "delivering"
STATUS_DONE = "done"
STATUS_FAILED = "failed"

_ERROR_MAX = 1000
# 並びの値が無い Mod は最後(id の順)。
_ORDER_LAST = 1 << 30


def _utcnow() -> datetime:
    return datetime.now(UTC)


# -- 画像を開く ------------------------------------------------------------------------


class ImageOpener:
    """イベントの画像の原本を読む。Asset でない画像(画像で探すときの画像)は `extras`
    (sha256 → バイト列)から返す。"""

    def __init__(
        self,
        session_factory: sessionmaker,
        store: AssetStore,
        extras: Mapping[str, bytes] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._store = store
        self._extras = dict(extras or {})

    def read(self, ref: contract.ImageRef | str) -> bytes:
        asset_id = ref if isinstance(ref, str) else ref.asset_id
        if asset_id is None:
            assert not isinstance(ref, str)
            data = self._extras.get(ref.sha256)
            if data is None:
                raise LookupError("image not available")
            return data
        with self._session_factory() as session:
            asset = session.get(Asset, uuid.UUID(asset_id))
            if asset is None or asset.content_withheld_at is not None:
                raise LookupError(f"asset {asset_id} not available")
            blob_key = asset.blob_key
        return self._store.read(blob_key)

    async def open(self, ref: contract.ImageRef | str) -> bytes:
        return await asyncio.to_thread(self.read, ref)


# -- 購読 ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Subscriber:
    """有効な Mod 1つ(Webhook か Python の Mod)。"""

    kind: str
    id: str
    name: str
    events: frozenset[str]
    filters: mods_config.Filters
    timeout_seconds: float
    on_failure: str
    order: int
    send_images: bool = False
    url: str | None = None
    loaded: loader.LoadedMod | None = None
    values: Mapping[str, Any] = field(default_factory=dict)

    def matches(
        self,
        event: str,
        *,
        purpose: str | None = None,
        source: str | None = None,
        provider: str | None = None,
    ) -> bool:
        if event not in self.events:
            return False
        filters = self.filters
        if filters.purposes and purpose is not None and purpose not in filters.purposes:
            return False
        if filters.sources and source is not None and source not in filters.sources:
            return False
        if filters.providers and provider not in filters.providers:
            return False
        return True


@dataclass(frozen=True)
class Denial:
    """`ai.before_send` で止めた Mod と理由。`message` は利用者に見せる文。"""

    mod_kind: str
    mod_id: str
    mod_name: str
    reason: str
    reference: str | None = None

    @property
    def message(self) -> str:
        return t("mods.denied", mod=self.mod_name, reason=self.reason)


@dataclass
class CallResult:
    """1回の呼び出しの結果。`outcome` は allow / deny / ok / error / timeout。"""

    outcome: str
    duration_ms: int
    decision: contract.Decision | None = None
    verdicts: list[contract.Verdict] = field(default_factory=list)
    error: str | None = None
    status_code: int | None = None
    response: Any = None

    @property
    def failed(self) -> bool:
        return self.outcome in ("error", "timeout")


@dataclass(frozen=True)
class PythonModStatus:
    """管理者設定の一覧に出す Python の Mod(読み込みエラーを含む)。"""

    folder: loader.ModFolder
    config: mods_config.PythonModConfig
    loaded: bool
    error: str | None


def _short_error(e: BaseException) -> str:
    text = str(e).strip()
    message = f"{e.__class__.__name__}: {text}" if text else e.__class__.__name__
    return message[:_ERROR_MAX]


class ModRuntime:
    def __init__(
        self,
        session_factory: sessionmaker,
        store: AssetStore,
        settings: Settings,
    ) -> None:
        self.session_factory = session_factory
        self.store = store
        self.settings = settings
        # テストでは `httpx.MockTransport` を入れる(Webhook を実際には送らない)。
        self.http_transport: httpx.AsyncBaseTransport | None = None
        self._folders: dict[str, loader.ModFolder] = {}
        self._loaded: dict[str, loader.LoadedMod] = {}
        self._load_errors: dict[str, str] = {}
        self._subscribers: tuple[Subscriber, ...] = ()
        self._instance_name: str | None = None
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        # 配達の worker を起こす関数(`app/worker/mod_delivery.py` が登録する)。
        self._wake: Callable[[], None] | None = None

    # -- 読み込み ------------------------------------------------------------------

    def scan(self) -> None:
        """`MODS_DIR` のマニフェストを読む(起動のとき)。"""
        folders = loader.scan(self.settings.mods_path)
        with self._lock:
            self._folders = {f.id: f for f in folders}

    def reload(self, db: Session) -> None:
        """設定から有効な購読を作り直す。有効な Python の Mod で、まだ import していないものは
        ここで import する(失敗したらエラーを覚えて外す)。"""
        webhooks = mods_config.load_webhooks(db)
        python_configs = mods_config.load_python_configs(db)
        instance_name = mods_config.load_instance_name(db)
        subscribers: list[Subscriber] = []
        for webhook in webhooks:
            common = webhook.common
            if not common.enabled or not common.events:
                continue
            subscribers.append(
                Subscriber(
                    kind=mods_config.KIND_WEBHOOK,
                    id=webhook.id,
                    name=webhook.name,
                    events=frozenset(common.events),
                    filters=common.filters,
                    timeout_seconds=common.timeout_seconds,
                    on_failure=common.on_failure,
                    order=common.order if common.order is not None else _ORDER_LAST,
                    send_images=webhook.send_images,
                    url=webhook.url,
                )
            )
        with self._lock:
            folders = dict(self._folders)
        for mod_id, folder in folders.items():
            cfg = mods_config.python_config_or_default(python_configs, mod_id)
            if not cfg.common.enabled or folder.manifest is None:
                continue
            loaded = self._ensure_loaded(folder)
            if loaded is None:
                continue
            manifest = folder.manifest
            events = (
                manifest.events
                if cfg.common.events is None
                else tuple(e for e in cfg.common.events if e in manifest.events)
            )
            if not events:
                continue
            subscribers.append(
                Subscriber(
                    kind=mods_config.KIND_PYTHON,
                    id=mod_id,
                    name=manifest.name,
                    events=frozenset(events),
                    filters=cfg.common.filters,
                    timeout_seconds=cfg.common.timeout_seconds,
                    on_failure=cfg.common.on_failure,
                    order=cfg.common.order if cfg.common.order is not None else _ORDER_LAST,
                    loaded=loaded,
                    values=self._python_values(folder, cfg),
                )
            )
        subscribers.sort(key=lambda s: (s.order, s.kind, s.id))
        with self._lock:
            self._subscribers = tuple(subscribers)
            self._instance_name = instance_name

    def _ensure_loaded(self, folder: loader.ModFolder) -> loader.LoadedMod | None:
        with self._lock:
            loaded = self._loaded.get(folder.id)
        if loaded is not None:
            return loaded
        try:
            loaded = loader.import_mod(folder)
        except loader.ModLoadError as e:
            with self._lock:
                self._load_errors[folder.id] = str(e)
            return None
        with self._lock:
            self._loaded[folder.id] = loaded
            self._load_errors.pop(folder.id, None)
        return loaded

    def _python_values(
        self, folder: loader.ModFolder, cfg: mods_config.PythonModConfig
    ) -> dict[str, Any]:
        """設定の項目の値(既定 → 保存した値 → 秘密の値)。"""
        assert folder.manifest is not None
        values: dict[str, Any] = {}
        for spec in folder.manifest.settings:
            if spec.secret:
                values[spec.key] = mods_config.read_python_secret(
                    self.settings.data_dir, folder.id, spec.key
                )
            else:
                values[spec.key] = cfg.values.get(spec.key, spec.default)
        return values

    def python_mods(self, db: Session) -> list[PythonModStatus]:
        configs = mods_config.load_python_configs(db)
        with self._lock:
            folders = list(self._folders.values())
            loaded = set(self._loaded)
            errors = dict(self._load_errors)
        return [
            PythonModStatus(
                folder=folder,
                config=mods_config.python_config_or_default(configs, folder.id),
                loaded=folder.id in loaded,
                error=folder.error or errors.get(folder.id),
            )
            for folder in sorted(folders, key=lambda f: f.id)
        ]

    def folder(self, mod_id: str) -> loader.ModFolder | None:
        with self._lock:
            return self._folders.get(mod_id)

    # -- 購読を引く(メモリだけ) ------------------------------------------------------

    def subscribers(
        self,
        event: str,
        *,
        purpose: str | None = None,
        source: str | None = None,
        provider: str | None = None,
    ) -> list[Subscriber]:
        current = self._subscribers
        if not current:
            return []
        return [
            s
            for s in current
            if s.matches(event, purpose=purpose, source=source, provider=provider)
        ]

    def wants(
        self,
        event: str,
        *,
        purpose: str | None = None,
        source: str | None = None,
        provider: str | None = None,
    ) -> bool:
        """このイベントを受ける購読があるか。絞り込みは渡したものだけ当てる(プロバイダーを
        渡さなければ、プロバイダーで絞る購読も受けるものとみなす)。DB には触れない。"""
        current = self._subscribers
        if not current:
            return False
        for s in current:
            if event not in s.events:
                continue
            filters = s.filters
            if filters.purposes and purpose is not None and purpose not in filters.purposes:
                continue
            if filters.sources and source is not None and source not in filters.sources:
                continue
            if filters.providers and provider is not None and provider not in filters.providers:
                continue
            return True
        return False

    def find_subscriber(self, kind: str, mod_id: str) -> Subscriber | None:
        return next((s for s in self._subscribers if s.kind == kind and s.id == mod_id), None)

    # -- イベント ----------------------------------------------------------------------

    def make_event(
        self, name: str, subject: contract.Subject, actor: contract.Actor | None
    ) -> contract.Event:
        return contract.Event(
            event=name,
            event_id=str(uuid.uuid4()),
            occurred_at=_utcnow().isoformat(),
            instance=contract.Instance(version=get_version(), name=self._instance_name),
            actor=actor,
            subject=subject,
        )

    # -- 呼び出し ----------------------------------------------------------------------

    async def call(self, sub: Subscriber, event: contract.Event, opener: ImageOpener) -> CallResult:
        """1つの Mod を呼んで、応答を契約どおりに読む。失敗は `outcome` に入れて返す(例外に
        しない)。"""
        started = time.monotonic()

        def elapsed() -> int:
            return int((time.monotonic() - started) * 1000)

        status_code: int | None = None
        raw: Any = None
        try:
            if sub.kind == mods_config.KIND_WEBHOOK:
                response = await asyncio.wait_for(
                    self._call_webhook(sub, event, opener), timeout=sub.timeout_seconds
                )
                status_code = response.status_code
                raw = response.body
            else:
                assert sub.loaded is not None
                handler = sub.loaded.handlers.get(event.event)
                if handler is None:
                    raise contract.ContractError(f"no handler for {event.event}")
                ctx = contract.ModContext(
                    mod_id=sub.id,
                    settings=dict(sub.values),
                    open_image=opener.open,
                    logger=logging.getLogger(f"gakei.mods.{sub.id}"),
                )
                raw = await asyncio.wait_for(handler(event, ctx), timeout=sub.timeout_seconds)
            if event.event == contract.AI_BEFORE_SEND:
                decision = contract.decision_from_json(raw)
                return CallResult(
                    outcome=decision.decision,
                    duration_ms=elapsed(),
                    decision=decision,
                    status_code=status_code,
                    response=raw,
                )
            verdicts = contract.verdicts_from_json(raw)
            return CallResult(
                outcome="ok",
                duration_ms=elapsed(),
                verdicts=verdicts,
                status_code=status_code,
                response=raw,
            )
        except (TimeoutError, httpx.TimeoutException):
            return CallResult(outcome="timeout", duration_ms=elapsed(), error="timeout")
        except contract.ContractError as e:
            return CallResult(
                outcome="error",
                duration_ms=elapsed(),
                error=f"invalid response: {e}"[:_ERROR_MAX],
                status_code=status_code,
                response=raw,
            )
        except webhook_send.WebhookError as e:
            return CallResult(
                outcome="error",
                duration_ms=elapsed(),
                error=str(e)[:_ERROR_MAX],
                status_code=e.status_code,
            )
        except Exception as e:  # noqa: BLE001 - Mod の失敗は記録して、扱いは呼び出し側が決める
            if sub.kind == mods_config.KIND_PYTHON:
                logger.warning("Mod %s が例外を出しました", sub.id, exc_info=True)
            return CallResult(outcome="error", duration_ms=elapsed(), error=_short_error(e))

    async def _call_webhook(
        self, sub: Subscriber, event: contract.Event, opener: ImageOpener
    ) -> webhook_send.WebhookResponse:
        assert sub.url is not None
        secret = await asyncio.to_thread(
            mods_config.read_webhook_secret, self.settings.data_dir, sub.id
        )
        if not secret:
            raise webhook_send.WebhookError("signing secret is not set")
        images: list[webhook_send.WebhookImage] = []
        if sub.send_images:
            for ref in _event_images(event):
                data = await opener.open(ref)
                images.append(
                    webhook_send.WebhookImage(
                        asset_id=ref.asset_id, sha256=ref.sha256, mime=ref.mime, data=data
                    )
                )
        request = await asyncio.to_thread(
            webhook_send.build_request, sub.url, secret, contract.event_to_json(event), images
        )
        return await webhook_send.send(
            request, timeout_seconds=sub.timeout_seconds, transport=self.http_transport
        )

    # -- ai.before_send ----------------------------------------------------------------

    async def before_send(
        self,
        subject: contract.BeforeSendSubject,
        *,
        actor: contract.Actor | None,
        asset_id: uuid.UUID | None = None,
        extra_images: Mapping[str, bytes] | None = None,
    ) -> Denial | None:
        """有効な購読を順番に呼び、最初の拒否で止める。止めたら Denial、通したら None。"""
        subs = self.subscribers(
            contract.AI_BEFORE_SEND, purpose=subject.purpose, provider=subject.target.provider
        )
        if not subs:
            return None
        event = self.make_event(contract.AI_BEFORE_SEND, subject, actor)
        opener = ImageOpener(self.session_factory, self.store, extra_images)
        run_id = uuid.UUID(subject.run_id) if subject.run_id else None
        if asset_id is None and subject.asset_id:
            asset_id = uuid.UUID(subject.asset_id)
        for sub in subs:
            result = await self.call(sub, event, opener)
            if result.decision is not None:
                effective = result.decision.decision
                reason = result.decision.reason
                reference = result.decision.reference
            else:
                effective = "deny" if sub.on_failure == "block" else "allow"
                reason = t("mods.checkFailed") if effective == "deny" else None
                reference = None
            if effective == "deny" and not reason:
                reason = t("mods.deniedNoReason")
            try:
                await asyncio.to_thread(
                    self.record_invocation,
                    event=event,
                    sub=sub,
                    result=result,
                    effective=effective,
                    reason=reason,
                    reference=reference,
                    purpose=subject.purpose,
                    run_id=run_id,
                    asset_id=asset_id,
                )
            except Exception:  # noqa: BLE001 - 記録の失敗で送る・止めるの判断は変えない
                logger.exception("Mod の呼び出しを記録できませんでした")
            if effective == "deny":
                assert reason is not None
                return Denial(
                    mod_kind=sub.kind,
                    mod_id=sub.id,
                    mod_name=sub.name,
                    reason=reason,
                    reference=reference,
                )
        return None

    def before_send_blocking(
        self,
        subject: contract.BeforeSendSubject,
        *,
        actor: contract.Actor | None,
        asset_id: uuid.UUID | None = None,
        extra_images: Mapping[str, bytes] | None = None,
    ) -> Denial | None:
        """スレッド(同期の処理)から `before_send` を呼ぶ。イベントループのスレッドから呼ばない。"""
        coro = self.before_send(subject, actor=actor, asset_id=asset_id, extra_images=extra_images)
        loop = self._loop
        if loop is None or loop.is_closed():
            return asyncio.run(coro)
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            coro.close()
            raise RuntimeError("before_send_blocking must not be called from the event loop")
        return asyncio.run_coroutine_threadsafe(coro, loop).result()

    # -- 記録 -------------------------------------------------------------------------

    def record_invocation(
        self,
        *,
        event: contract.Event,
        sub: Subscriber,
        result: CallResult,
        effective: str | None = None,
        reason: str | None = None,
        reference: str | None = None,
        purpose: str | None = None,
        run_id: uuid.UUID | None = None,
        asset_id: uuid.UUID | None = None,
        session: Session | None = None,
    ) -> uuid.UUID:
        """`mod_invocation` に1行足す。`session` を渡したら commit しない。"""
        row = ModInvocation(
            id=uuid.uuid4(),
            event=event.event,
            event_id=uuid.UUID(event.event_id),
            mod_kind=sub.kind,
            mod_id=sub.id,
            mod_name=sub.name,
            purpose=purpose,
            run_id=run_id,
            asset_id=asset_id,
            actor_user_id=_actor_uuid(event.actor),
            outcome=result.outcome,
            effective=effective,
            # Mod の応答は外部由来。NUL などを除いてから保存する(ADR-0027)。
            reason=sanitize_external_text_or_none(reason),
            reference=sanitize_external_text_or_none(reference),
            error=sanitize_external_text_or_none(result.error),
            duration_ms=result.duration_ms,
            created_at=_utcnow(),
        )
        if session is not None:
            session.add(row)
            return row.id
        with self.session_factory() as own:
            own.add(row)
            own.commit()
        return row.id

    # -- 非同期のイベントを積む ----------------------------------------------------------

    def enqueue(
        self,
        db: Session,
        name: str,
        subject: contract.Subject,
        *,
        actor_user_id: uuid.UUID | None,
        asset_id: uuid.UUID | None = None,
        run_id: uuid.UUID | None = None,
        source: str | None = None,
        provider: str | None = None,
    ) -> bool:
        """呼び出し側のトランザクションで `mod_delivery` に積む(commit はしない)。積んだら
        True。commit の後に `notify` を呼ぶ。"""
        subs = self.subscribers(name, source=source, provider=provider)
        if not subs:
            return False
        event = self.make_event(name, subject, mods_events.actor_for_user_id(db, actor_user_id))
        payload = contract.event_to_json(event)
        now = _utcnow()
        for sub in subs:
            db.add(
                ModDelivery(
                    id=uuid.uuid4(),
                    event=name,
                    event_id=uuid.UUID(event.event_id),
                    mod_kind=sub.kind,
                    mod_id=sub.id,
                    payload=payload,
                    asset_id=asset_id,
                    run_id=run_id,
                    status=STATUS_QUEUED,
                    attempts=0,
                    next_attempt_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
        return True

    def enqueue_asset_ingested(self, db: Session, asset: Asset, source: str) -> bool:
        """取り込んだ Asset の `asset.ingested` を積む。"""
        if not self.wants(contract.ASSET_INGESTED, source=source):
            return False
        run = db.get(Run, asset.produced_by_run_id) if asset.produced_by_run_id else None
        provider = run.provider if run is not None else None
        db.flush()
        subject = mods_events.asset_ingested_subject(db, asset, source)
        actor_user_id = asset.created_by_user_id or (
            run.created_by_user_id if run is not None else None
        )
        return self.enqueue(
            db,
            contract.ASSET_INGESTED,
            subject,
            actor_user_id=actor_user_id,
            asset_id=asset.id,
            run_id=asset.produced_by_run_id,
            source=source,
            provider=provider,
        )

    def enqueue_run_finished(self, db: Session, run: Run) -> bool:
        """終わった Run の `run.finished` を積む(Run の状態を書いたのと同じトランザクションで)。"""
        if not self.wants(contract.RUN_FINISHED, provider=run.provider):
            return False
        db.flush()
        subject = mods_events.run_finished_subject(db, run)
        return self.enqueue(
            db,
            contract.RUN_FINISHED,
            subject,
            actor_user_id=run.created_by_user_id,
            run_id=run.id,
            provider=run.provider,
        )

    def enqueue_runs_finished(self, run_ids: list[uuid.UUID]) -> bool:
        """commit 済みの Run(取り消しなど)の `run.finished` を、別のトランザクションで積み、
        worker を起こす。"""
        if not run_ids or not self.wants(contract.RUN_FINISHED):
            return False
        queued = False
        with self.session_factory() as session:
            for run_id in run_ids:
                run = session.get(Run, run_id)
                if run is not None:
                    queued = self.enqueue_run_finished(session, run) or queued
            session.commit()
        if queued:
            self.notify()
        return queued

    # -- 配達の worker --------------------------------------------------------------

    def set_wake(self, wake: Callable[[], None] | None) -> None:
        self._wake = wake

    def bind_loop(self, loop: asyncio.AbstractEventLoop | None) -> None:
        self._loop = loop

    def notify(self) -> None:
        """積んだ後(commit の後)に呼ぶ。スレッドからも呼べる。"""
        wake = self._wake
        if wake is not None:
            wake()

    def save_verdicts(
        self,
        session: Session,
        *,
        event: contract.Event,
        sub: Subscriber,
        invocation_id: uuid.UUID,
        verdicts: list[contract.Verdict],
    ) -> int:
        """判定を `mod_verdict` に足す(commit しない)。そのイベントで届いた Asset と Run に
        対するものだけ受け、ほかは捨てる。足した数を返す。"""
        allowed_assets, allowed_runs = _verdict_targets(event)
        now = _utcnow()
        count = 0
        for verdict in verdicts:
            target_id = verdict.target.id
            if verdict.target.type == "asset" and target_id in allowed_assets:
                asset_id, run_id = uuid.UUID(target_id), None
            elif verdict.target.type == "run" and target_id in allowed_runs:
                asset_id, run_id = None, uuid.UUID(target_id)
            else:
                continue
            session.add(
                ModVerdict(
                    id=uuid.uuid4(),
                    mod_kind=sub.kind,
                    mod_id=sub.id,
                    mod_name=sub.name,
                    invocation_id=invocation_id,
                    event_id=uuid.UUID(event.event_id),
                    asset_id=asset_id,
                    run_id=run_id,
                    verdict=verdict.verdict,
                    label=sanitize_external_text_or_none(verdict.label),
                    score=verdict.score,
                    message=sanitize_external_text_or_none(verdict.message),
                    reference=sanitize_external_text_or_none(verdict.reference),
                    url=sanitize_external_text_or_none(verdict.url),
                    created_at=now,
                )
            )
            count += 1
        return count

    # -- テスト送信 ------------------------------------------------------------------

    def subscriber_for_test(self, db: Session, kind: str, mod_id: str) -> Subscriber:
        """テスト送信に使う購読を作る。Webhook は有効かどうかに関わらず送れる。Python の Mod は、
        置いただけでは import しない(ADR-0051 7章)ので、有効にして読み込めたものだけ呼ぶ。
        無ければ ModNotFoundError、有効でなければ ModNotEnabledError、読み込めなければ
        loader.ModLoadError。"""
        if kind == mods_config.KIND_WEBHOOK:
            webhook = mods_config.find_webhook(mods_config.load_webhooks(db), mod_id)
            if webhook is None:
                raise mods_config.ModNotFoundError
            return Subscriber(
                kind=kind,
                id=webhook.id,
                name=webhook.name,
                events=frozenset(contract.EVENTS),
                filters=mods_config.Filters(),
                timeout_seconds=webhook.common.timeout_seconds,
                on_failure=webhook.common.on_failure,
                order=0,
                send_images=webhook.send_images,
                url=webhook.url,
            )
        folder = self.folder(mod_id)
        if folder is None:
            raise mods_config.ModNotFoundError
        if folder.manifest is None:
            raise loader.ModLoadError(folder.error or "invalid manifest")
        cfg = mods_config.python_config_or_default(mods_config.load_python_configs(db), mod_id)
        with self._lock:
            loaded = self._loaded.get(mod_id)
            error = self._load_errors.get(mod_id)
        if not cfg.common.enabled:
            raise ModNotEnabledError
        if loaded is None:
            raise loader.ModLoadError(error or "import failed")
        return Subscriber(
            kind=kind,
            id=mod_id,
            name=folder.manifest.name,
            events=frozenset(folder.manifest.events),
            filters=mods_config.Filters(),
            timeout_seconds=cfg.common.timeout_seconds,
            on_failure=cfg.common.on_failure,
            order=0,
            loaded=loaded,
            values=self._python_values(folder, cfg),
        )

    async def test_send(self, sub: Subscriber, event_name: str) -> CallResult:
        """ダミーのイベントを送る(記録は残さない)。"""
        if event_name not in sub.events:
            return CallResult(outcome="error", duration_ms=0, error="event not subscribed")
        image = _dummy_png()
        event = self.make_event(event_name, dummy_subject(event_name, image), None)
        opener = ImageOpener(
            self.session_factory, self.store, {hashlib.sha256(image).hexdigest(): image}
        )
        return await self.call(sub, event, opener)


class ModNotEnabledError(Exception):
    """有効にしていない Python の Mod は呼ばない(テスト送信も)。"""


def _actor_uuid(actor: contract.Actor | None) -> uuid.UUID | None:
    if actor is None:
        return None
    try:
        return uuid.UUID(actor.id)
    except ValueError:
        return None


def _event_images(event: contract.Event) -> list[contract.ImageRef]:
    """Webhook で送る画像(送る画像・取り込んだ画像・Run の出力)。"""
    subject = event.subject
    if isinstance(subject, contract.BeforeSendSubject):
        return list(subject.images)
    if isinstance(subject, contract.AssetIngestedSubject):
        return [subject.asset]
    if isinstance(subject, contract.RunFinishedSubject):
        return list(subject.outputs)
    return []


def _verdict_targets(event: contract.Event) -> tuple[set[str], set[str]]:
    """判定を受けてよい Asset と Run の id。"""
    subject = event.subject
    if isinstance(subject, contract.AssetIngestedSubject):
        return {subject.asset.asset_id} if subject.asset.asset_id else set(), set()
    if isinstance(subject, contract.RunFinishedSubject):
        return {o.asset_id for o in subject.outputs if o.asset_id}, {subject.run_id}
    return set(), set()


def _dummy_png() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (128, 128, 128)).save(buffer, format="PNG")
    return buffer.getvalue()


def dummy_subject(event_name: str, image_data: bytes) -> contract.Subject:
    """テスト送信のダミーの中身。id は実在しない。画像は Asset でない小さな PNG(`asset_id` が
    null)で、画像を送る設定なら multipart で送られる。"""
    dummy_id = str(uuid.UUID(int=0))
    image = contract.ImageRef(
        asset_id=None,
        sha256=hashlib.sha256(image_data).hexdigest(),
        width=8,
        height=8,
        bytes=len(image_data),
        mime="image/png",
    )
    if event_name == contract.AI_BEFORE_SEND:
        return contract.BeforeSendSubject(
            purpose="image_run",
            target=contract.Target(
                provider="openai",
                model="gpt-image-1",
                host="api.openai.com",
                locality="cloud",
                locality_source="default",
            ),
            text=[contract.TextPart(role="prompt", text="GAKEI test event")],
            params={"size": "1024x1024"},
            images=[replace(image, role="primary")],
            run_id=dummy_id,
        )
    if event_name == contract.ASSET_INGESTED:
        return contract.AssetIngestedSubject(asset=image, kind="upload", source="upload")
    return contract.RunFinishedSubject(
        run_id=dummy_id,
        status="succeeded",
        provider="openai",
        model="gpt-image-1",
        operation="generate",
        prompt="GAKEI test event",
        params={"size": "1024x1024"},
        outputs=[image],
    )
