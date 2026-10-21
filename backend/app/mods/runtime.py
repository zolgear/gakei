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
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.domain import content_withholding
from app.domain.models import Asset, ModDelivery, ModInvocation, ModVerdict, Run
from app.domain.text_safety import sanitize_external_text_or_none
from app.i18n import t
from app.mods import api as contract
from app.mods import config as mods_config
from app.mods import events as mods_events
from app.mods import loader
from app.mods import webhook as webhook_send
from app.mods.replay import VerdictReplayGuard
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
        # 「判定を待つ」購読では、原本をまだ保存していない(メモリの中のバイト列を渡す)。
        if not isinstance(ref, str) and ref.sha256 in self._extras:
            return self._extras[ref.sha256]
        if asset_id is None:
            assert not isinstance(ref, str)
            data = self._extras.get(ref.sha256)
            if data is None:
                raise LookupError("image not available")
            return data
        with self._session_factory() as session:
            asset = self._asset(session, asset_id)
            blob_key = asset.blob_key
        return self._store.read(blob_key)

    @staticmethod
    def _asset(session: Session, asset_id: str) -> Asset:
        try:
            parsed = uuid.UUID(asset_id)
        except ValueError as e:
            raise LookupError(f"asset {asset_id} not available") from e
        asset = session.get(Asset, parsed)
        if asset is None:
            raise LookupError(f"asset {asset_id} not available")
        if asset.content_withheld_at is not None:
            raise contract.ImageWithheld(f"asset {asset_id} has no image (withheld)")
        return asset

    def read_with_lineage(self, ref: contract.ImageRef | str) -> bytes:
        """ダウンロードと同じ、系列の情報を埋め込んだ PNG(ADR-0014)。系列は、その Asset を
        作った人に見える範囲(作った人の記録が無ければ、個人モードと同じくすべて)。PNG でない
        原本と、まだ保存していない画像(`extras`)は原本のまま返す。"""
        from app.auth.identity import LOCAL_ADMIN, CurrentUser
        from app.domain.embedded_meta import build_lineage_meta, embed_gakei_chunk
        from app.domain.models import AppUser

        if not isinstance(ref, str) and (ref.sha256 in self._extras or ref.asset_id is None):
            return self.read(ref)
        asset_id = ref if isinstance(ref, str) else ref.asset_id
        assert asset_id is not None
        with self._session_factory() as session:
            asset = self._asset(session, asset_id)
            data = self._store.read(asset.blob_key)
            if asset.mime != "image/png":
                return data
            owner_id = asset.created_by_user_id
            if owner_id is None and asset.produced_by_run_id is not None:
                run = session.get(Run, asset.produced_by_run_id)
                owner_id = run.created_by_user_id if run is not None else None
            viewer = LOCAL_ADMIN
            if owner_id is not None:
                user = session.get(AppUser, owner_id)
                viewer = CurrentUser(
                    id=owner_id,
                    name=user.name if user is not None else None,
                    email=user.email if user is not None else None,
                    role="user",
                )
            meta = build_lineage_meta(session, asset, viewer=viewer)
        return embed_gakei_chunk(data, meta)

    def run_info(self, run_id: str) -> contract.RunFinishedSubject | None:
        """`run.finished` の `subject` と同じ形の Run の情報(無ければ None)。"""
        try:
            parsed = uuid.UUID(run_id)
        except ValueError:
            return None
        with self._session_factory() as session:
            run = session.get(Run, parsed)
            if run is None:
                return None
            return mods_events.run_finished_subject(session, run)

    async def open(self, ref: contract.ImageRef | str) -> bytes:
        return await asyncio.to_thread(self.read, ref)

    async def open_with_lineage(self, ref: contract.ImageRef | str) -> bytes:
        return await asyncio.to_thread(self.read_with_lineage, ref)

    async def get_run(self, run_id: str) -> contract.RunFinishedSubject | None:
        return await asyncio.to_thread(self.run_info, run_id)


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
    # 「判定を待つ」(ADR-0051 2章)。`asset.ingested` と `run.finished` を、原本をストレージに
    # 書く前に同期で呼ぶ。
    wait_for_verdict: bool = False
    values: Mapping[str, Any] = field(default_factory=dict)
    # 判定を返す Mod か(Python の Mod はマニフェストの `returns_verdicts`。Webhook は常に True)。
    # False なら「判定を待つ」にせず、返してきた判定は捨てる。
    returns_verdicts: bool = True

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
        # 判定の受付で、同じ要求の再送を受けないための記録(ADR-0051 8章)。
        self.verdict_replay = VerdictReplayGuard()

    # -- 読み込み ------------------------------------------------------------------

    def scan(self) -> None:
        """同梱の Mod と `MODS_DIR` のマニフェストを読む(起動のとき)。"""
        folders = loader.scan_all(self.settings.mods_path)
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
                    wait_for_verdict=common.wait_for_verdict,
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
                    # 判定を返さない Mod は、保存した設定が「待つ」でも待たない(マニフェストを
                    # 後から変えたときのため)。
                    wait_for_verdict=cfg.common.wait_for_verdict and manifest.returns_verdicts,
                    returns_verdicts=manifest.returns_verdicts,
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

    def waiting_subscribers(
        self,
        event: str,
        *,
        source: str | None = None,
        provider: str | None = None,
    ) -> list[Subscriber]:
        """「判定を待つ」購読(`asset.ingested` と `run.finished` だけ)。DB には触れない。"""
        if event not in (contract.ASSET_INGESTED, contract.RUN_FINISHED):
            return []
        return [
            s
            for s in self.subscribers(event, source=source, provider=provider)
            if s.wait_for_verdict
        ]

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
                    submit_verdicts=self._verdict_submitter(sub),
                    open_image_with_lineage=opener.open_with_lineage,
                    get_run=opener.get_run,
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
            if verdicts and not sub.returns_verdicts:
                logger.warning(
                    "Mod %s は判定を返さない Mod なので、返した判定 %d 件を捨てました",
                    sub.id,
                    len(verdicts),
                )
                verdicts = []
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
                try:
                    data = await opener.open(ref)
                except (LookupError, FileNotFoundError):
                    # 画像を残さなかった Asset(ADR-0051 4章)など。メタ情報だけを送る。
                    continue
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
        return self._blocking(coro)

    def _blocking(self, coro):  # noqa: ANN001, ANN202
        """スレッドからコルーチンを動かして結果を待つ(イベントループのスレッドからは呼ばない)。"""
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
        exclude_waiting: bool = False,
    ) -> bool:
        """呼び出し側のトランザクションで `mod_delivery` に積む(commit はしない)。積んだら
        True。commit の後に `notify` を呼ぶ。`exclude_waiting` なら、同期で呼んだ(「判定を
        待つ」)購読には積まない(同じイベントを重ねて送らない)。"""
        subs = self.subscribers(name, source=source, provider=provider)
        if exclude_waiting:
            subs = [s for s in subs if not s.wait_for_verdict]
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
        """取り込んだ Asset の `asset.ingested` を積む。「判定を待つ」購読は取り込みのときに同期で
        呼んでいるので積まない。画像を残さなかった Asset(取り込みのときに拒否された、同じ内容が
        拒否されていた)は積まない。"""
        if asset.content_withheld_at is not None:
            return False
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
            exclude_waiting=True,
        )

    def enqueue_run_finished(self, db: Session, run: Run) -> bool:
        """終わった Run の `run.finished` を積む(Run の状態を書いたのと同じトランザクションで)。
        成功した Run は、「判定を待つ」購読を出力の取り込みの前に同期で呼んでいるので、その購読
        には積まない(失敗・取り消しの Run は画像が無いので、待つ購読にも非同期で送る)。"""
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
            exclude_waiting=str(run.status) == "succeeded",
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
    ) -> list[ModVerdict]:
        """判定を `mod_verdict` に足す(commit しない)。そのイベントで届いた Asset と Run に
        対するものだけ受け、ほかは捨てる。足した行を返す(`deny` の効力は呼び出し側が
        `apply_verdicts` で当てる)。"""
        allowed_assets, allowed_runs = _verdict_targets(event)
        return self._add_verdicts(
            session,
            sub_kind=sub.kind,
            sub_id=sub.id,
            sub_name=sub.name,
            invocation_id=invocation_id,
            event_id=uuid.UUID(event.event_id),
            verdicts=verdicts,
            allowed_assets=allowed_assets,
            allowed_runs=allowed_runs,
        )

    def _add_verdicts(
        self,
        session: Session,
        *,
        sub_kind: str,
        sub_id: str,
        sub_name: str,
        invocation_id: uuid.UUID | None,
        event_id: uuid.UUID | None,
        verdicts: list[contract.Verdict],
        allowed_assets: set[str],
        allowed_runs: set[str],
    ) -> list[ModVerdict]:
        now = _utcnow()
        rows: list[ModVerdict] = []
        for verdict in verdicts:
            target_id = verdict.target.id
            if verdict.target.type == "asset" and target_id in allowed_assets:
                asset_id, run_id = uuid.UUID(target_id), None
            elif verdict.target.type == "run" and target_id in allowed_runs:
                asset_id, run_id = None, uuid.UUID(target_id)
            else:
                continue
            row = ModVerdict(
                id=uuid.uuid4(),
                mod_kind=sub_kind,
                mod_id=sub_id,
                mod_name=sub_name,
                invocation_id=invocation_id,
                event_id=event_id,
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
            session.add(row)
            rows.append(row)
        return rows

    # -- 判定の効力(ADR-0051 4章) ---------------------------------------------------

    def apply_verdicts(
        self, session: Session, rows: list[ModVerdict]
    ) -> list[content_withholding.PurgeTarget]:
        """足した判定のうち `deny` の効力を当てる(画像を残さない。commit はしない)。commit の
        後に `purge` に渡す。"""
        session.flush()
        return content_withholding.apply_denials(session, rows)

    def purge(self, targets: list[content_withholding.PurgeTarget]) -> None:
        """保存先から原本と派生を消す(commit の後)。"""
        if targets:
            content_withholding.purge(self.store, targets)

    # -- 判定を待つ(ADR-0051 2章) ---------------------------------------------------

    async def judge(
        self,
        subs: list[Subscriber],
        event: contract.Event,
        extras: Mapping[str, bytes],
    ) -> list[tuple[Subscriber, CallResult]]:
        """「判定を待つ」購読を順に呼ぶ(記録は呼び出し側が `record_judgement` で残す。原本を
        書く前の取り込みのトランザクションの中で記録するため)。画像は `extras`(sha256 →
        バイト列)から渡す。"""
        opener = ImageOpener(self.session_factory, self.store, extras)
        results: list[tuple[Subscriber, CallResult]] = []
        for sub in subs:
            results.append((sub, await self.call(sub, event, opener)))
        return results

    def judge_blocking(
        self,
        subs: list[Subscriber],
        event: contract.Event,
        extras: Mapping[str, bytes],
    ) -> list[tuple[Subscriber, CallResult]]:
        """スレッド(取り込みの処理)から `judge` を呼ぶ。"""
        return self._blocking(self.judge(subs, event, extras))

    def record_judgement(
        self,
        session: Session,
        event: contract.Event,
        results: list[tuple[Subscriber, CallResult]],
        *,
        asset_id: uuid.UUID | None = None,
        run_id: uuid.UUID | None = None,
    ) -> dict[uuid.UUID, ModVerdict]:
        """同期で呼んだ結果を `mod_invocation` と `mod_verdict` に記録し(commit しない)、
        `deny` を受けた Asset ごとに最初の `deny` の判定を返す。

        失敗・時間切れは、その Mod の失敗時の扱いに従う。`block` なら、そのイベントで届いた
        Asset すべてに `deny` と同じ判定(理由は「チェックに失敗した」)を GAKEI が記録する。
        """
        allowed_assets, _allowed_runs = _verdict_targets(event)
        denials: dict[uuid.UUID, ModVerdict] = {}
        for sub, result in results:
            effective: str | None = None
            if result.failed:
                effective = "deny" if sub.on_failure == "block" else "allow"
            invocation_id = self.record_invocation(
                event=event,
                sub=sub,
                result=result,
                effective=effective,
                reason=t("mods.checkFailed") if effective == "deny" else None,
                run_id=run_id,
                asset_id=asset_id,
                session=session,
            )
            if result.failed:
                if effective != "deny":
                    continue
                verdicts = [
                    contract.Verdict(
                        target=contract.VerdictTarget(type="asset", id=target),
                        verdict="deny",
                        label=t("mods.checkFailedLabel"),
                        message=t("mods.checkFailed"),
                    )
                    for target in sorted(allowed_assets)
                ]
                rows = self._add_verdicts(
                    session,
                    sub_kind=sub.kind,
                    sub_id=sub.id,
                    sub_name=sub.name,
                    invocation_id=invocation_id,
                    event_id=uuid.UUID(event.event_id),
                    verdicts=verdicts,
                    allowed_assets=allowed_assets,
                    allowed_runs=set(),
                )
            else:
                rows = self.save_verdicts(
                    session,
                    event=event,
                    sub=sub,
                    invocation_id=invocation_id,
                    verdicts=result.verdicts,
                )
            for row in rows:
                if row.verdict == "deny" and row.asset_id is not None:
                    denials.setdefault(row.asset_id, row)
        return denials

    # -- 後から届く判定(ADR-0051 8章) -----------------------------------------------

    def receive_verdicts(
        self, kind: str, mod_id: str, mod_name: str, verdicts: list[contract.Verdict]
    ) -> int:
        """受付の API と、Python の Mod が後で送る判定。送れる対象は、その Mod の呼び出し
        (`asset.ingested` / `run.finished`)で届いた Asset と Run だけ(対象外が1つでもあれば
        何も記録せず VerdictTargetError)。記録して `deny` の効力を当て、足した数を返す。"""
        with self.session_factory() as session:
            allowed_assets, allowed_runs = _delivered_targets(session, kind, mod_id, verdicts)
            for verdict in verdicts:
                allowed = allowed_assets if verdict.target.type == "asset" else allowed_runs
                if verdict.target.id not in allowed:
                    raise VerdictTargetError(verdict.target.type, verdict.target.id)
            rows = self._add_verdicts(
                session,
                sub_kind=kind,
                sub_id=mod_id,
                sub_name=mod_name,
                invocation_id=None,
                event_id=None,
                verdicts=verdicts,
                allowed_assets=allowed_assets,
                allowed_runs=allowed_runs,
            )
            targets = self.apply_verdicts(session, rows)
            session.commit()
        self.purge(targets)
        return len(rows)

    def _verdict_submitter(self, sub: Subscriber):  # noqa: ANN202
        """Python の Mod に渡す、判定を後で送る関数(`ModContext.submit_verdicts`)。"""

        async def submit(verdicts: list[contract.Verdict]) -> int:
            parsed = contract.verdicts_from_json(list(verdicts))
            if not sub.returns_verdicts:
                if parsed:
                    logger.warning(
                        "Mod %s は判定を返さない Mod なので、送った判定 %d 件を捨てました",
                        sub.id,
                        len(parsed),
                    )
                return 0
            return await asyncio.to_thread(
                self.receive_verdicts, sub.kind, sub.id, sub.name, parsed
            )

        return submit

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
            returns_verdicts=folder.manifest.returns_verdicts,
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


class VerdictTargetError(Exception):
    """後から送られた判定の対象が、その Mod に届けた Asset・Run ではない。"""

    def __init__(self, target_type: str, target_id: str) -> None:
        super().__init__(f"{target_type} {target_id}")
        self.target_type = target_type
        self.target_id = target_id


def _parse_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def _delivered_targets(
    session: Session, kind: str, mod_id: str, verdicts: list[contract.Verdict]
) -> tuple[set[str], set[str]]:
    """判定の対象のうち、その Mod の呼び出しで届いたもの(Asset と Run の id の文字列)。

    `asset.ingested` で届いた Asset、`run.finished` で届いた Run と、その Run の出力の Asset。
    送らなかった呼び出し(`skipped`)は数えない。"""
    asset_ids = {
        parsed
        for v in verdicts
        if v.target.type == "asset" and (parsed := _parse_uuid(v.target.id)) is not None
    }
    run_ids = {
        parsed
        for v in verdicts
        if v.target.type == "run" and (parsed := _parse_uuid(v.target.id)) is not None
    }
    base = select(ModInvocation).where(
        ModInvocation.mod_kind == kind,
        ModInvocation.mod_id == mod_id,
        ModInvocation.event.in_((contract.ASSET_INGESTED, contract.RUN_FINISHED)),
        ModInvocation.outcome != "skipped",
    )
    allowed_assets: set[str] = set()
    allowed_runs: set[str] = set()
    if asset_ids:
        direct = session.execute(
            base.with_only_columns(ModInvocation.asset_id).where(
                ModInvocation.event == contract.ASSET_INGESTED,
                ModInvocation.asset_id.in_(asset_ids),
            )
        ).scalars()
        allowed_assets |= {str(a) for a in direct if a is not None}
        produced = session.execute(
            select(Asset.id, Asset.produced_by_run_id).where(
                Asset.id.in_(asset_ids), Asset.produced_by_run_id.is_not(None)
            )
        ).all()
        parent_runs = {run_id for _asset_id, run_id in produced}
        if parent_runs:
            finished = set(
                session.execute(
                    base.with_only_columns(ModInvocation.run_id).where(
                        ModInvocation.event == contract.RUN_FINISHED,
                        ModInvocation.run_id.in_(parent_runs),
                    )
                ).scalars()
            )
            allowed_assets |= {str(a) for a, run_id in produced if run_id in finished}
    if run_ids:
        delivered = session.execute(
            base.with_only_columns(ModInvocation.run_id).where(
                ModInvocation.event == contract.RUN_FINISHED,
                ModInvocation.run_id.in_(run_ids),
            )
        ).scalars()
        allowed_runs |= {str(r) for r in delivered if r is not None}
    return allowed_assets, allowed_runs


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
                locality="external",
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
