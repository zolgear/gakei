"""サムネイルの焦点を求める worker(ADR-0043 3章)。

取り込み(生成の出力、アップロード、スケッチ、系列の取り込み、MCP・URL でのアップロード)の
commit の後に、`ingest_hooks.notify_workers` が新しい Asset の id を `submit` で渡す。api
プロセス内の asyncio タスクが1件ずつ、原本を `open_content` で読み、顔を探して
`asset_focal_point` に記録する。取り込みの応答は待たせない。

待ち行列はプロセス内だけに持つ(DB には積まない)。再起動などで渡しそこねた Asset は焦点が
無いまま(画面は中央)になり、埋め戻しのツール(`python -m app.tools.backfill_focal_points`)が
拾う。計測(Raspberry Pi 5、実際の生成画像 48 枚)では、原本のデコードが中央値 55〜59ms、
検出(縮小を含む)が中央値 15〜20ms だった。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from collections import deque
from collections.abc import Iterable

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.domain import focal_points
from app.domain.models import Asset, AssetKind
from app.domain.storage import AssetStore

logger = logging.getLogger(__name__)

# 渡された id をためておく上限(大量の取り込みでメモリを使いすぎないため)。あふれた分は
# 埋め戻しのツールに任せる。
_MAX_PENDING = 10000


class FocalPointWorker:
    """lifespan で起動する焦点の worker。"""

    def __init__(self, session_factory: sessionmaker, store: AssetStore) -> None:
        self.session_factory = session_factory
        self.store = store
        self._pending: deque[uuid.UUID] = deque()
        self._pending_lock = threading.Lock()
        # 取り出して処理している件数(`is_idle` 用)。`_pending_lock` の下で増減する。
        self._inflight = 0
        self._wake = asyncio.Event()
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._event_loop: asyncio.AbstractEventLoop | None = None

    # -- 公開 API --------------------------------------------------------------

    def submit(self, asset_ids: Iterable[uuid.UUID]) -> None:
        """焦点を求める Asset を渡す(commit の後に呼ぶ)。同期エンドポイント(スレッド)からも
        呼べる。"""
        with self._pending_lock:
            for asset_id in asset_ids:
                if len(self._pending) >= _MAX_PENDING:
                    logger.warning("焦点の待ち行列があふれました。埋め戻しのツールで補ってください")
                    break
                self._pending.append(asset_id)
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

    def pending_count(self) -> int:
        with self._pending_lock:
            return len(self._pending)

    def is_idle(self) -> bool:
        """待ち行列が空で、処理中のものも無いか。テストが原本を動かす・消す前に、worker が
        原本を開いていないことを確かめるのに使う(Windows では開いているファイルを動かせない)。"""
        with self._pending_lock:
            return not self._pending and self._inflight == 0

    async def start(self) -> None:
        self._event_loop = asyncio.get_running_loop()
        self._task = asyncio.create_task(self._loop(), name="gakei-focal")
        if self.pending_count():
            self._wake.set()

    async def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._task is not None:
            await self._task

    async def drain(self) -> None:
        """待ち行列が空になるまで処理する(テスト用)。"""
        while True:
            asset_id = self._pop()
            if asset_id is None:
                return
            try:
                await asyncio.to_thread(self.process, asset_id)
            finally:
                self._done()

    # -- ループ ----------------------------------------------------------------

    def _pop(self) -> uuid.UUID | None:
        """1件取り出す。取り出したら、処理の後に必ず `_done` を呼ぶ。"""
        with self._pending_lock:
            if not self._pending:
                return None
            self._inflight += 1
            return self._pending.popleft()

    def _done(self) -> None:
        with self._pending_lock:
            self._inflight -= 1

    async def _loop(self) -> None:
        while not self._stop.is_set():
            asset_id = self._pop()
            if asset_id is None:
                self._wake.clear()
                # clear と pop の間に積まれたものを取りこぼさないよう、もう一度見る。
                if self.pending_count():
                    continue
                await self._wake.wait()
                continue
            try:
                await asyncio.to_thread(self.process, asset_id)
            except Exception:
                logger.exception("asset %s の焦点を求められませんでした", asset_id)
            finally:
                self._done()

    def process(self, asset_id: uuid.UUID) -> bool:
        """1件の焦点を求めて記録する。記録したら True(対象外・記録済みなら False)。"""
        with self.session_factory() as session:
            asset = session.get(Asset, asset_id)
            if asset is None or asset.kind == AssetKind.MASK:
                return False
            if not focal_points.needs_compute(session, asset_id):
                return False
            point = focal_points.detect_for_asset(self.store, asset)
            focal_points.save(session, asset_id, point)
            try:
                session.commit()
            except IntegrityError:
                # 埋め戻しのツールが同時に同じ Asset を書いた。どちらの値も同じ手順なので捨てる。
                session.rollback()
                return False
            return True
