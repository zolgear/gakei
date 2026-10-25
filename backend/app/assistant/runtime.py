"""アシスタントのプロセス内の状態(ADR-0048 7章)。`app.state.assistant` に1つ置く。

- エンジン(`FAKE_PROVIDER=1` では Fake)。
- LLM のリクエストの1時間の記録(利用者ごと)。プロセス内で数え、再起動で 0 に戻る
  (ADR-0024 8章と同じ)。上限に達したら待たせずに断る。
- 進行中の会話(同じ会話で同時に2つの発言を処理しない)。
- 決まった形の JSON を受ける呼び出しのエンジン(ComfyUI のワークフローの推定。ADR-0052 6章)。
  1時間の記録は会話と同じものに数える。
- 領域の四角の検出(ADR-0053 7章)も同じエンジンを使う。1時間の記録は検出の設定の上限で、
  会話とは別に数える(`detection_requests`)。
- 画像を LLM に送る前の許可の待ち(ADR-0048 14.1。`permissions`)。プロセス内に持ち、再起動で
  消える。答えられるのは求めた利用者だけ。
"""

from __future__ import annotations

import secrets
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field

import anyio

from app.assistant.engine import AssistantEngine, FakeAssistantEngine, OpenAIAssistantEngine
from app.assistant.structured import (
    FakeStructuredEngine,
    OpenAIStructuredEngine,
    StructuredEngine,
)

_WINDOW_SECONDS = 3600.0
# 個人モード(利用者の id が無い)の記録のキー。
_LOCAL_KEY = "local"


def user_key(user_id: uuid.UUID | None) -> str:
    return _LOCAL_KEY if user_id is None else str(user_id)


class HourlyRequestCounter:
    """利用者ごとに、直近1時間の LLM のリクエストの時刻を持つ。"""

    def __init__(self, clock=time.monotonic) -> None:  # noqa: ANN001
        self._clock = clock
        self._calls: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _trim(self, key: str, now: float) -> deque[float]:
        calls = self._calls.setdefault(key, deque())
        while calls and now - calls[0] >= _WINDOW_SECONDS:
            calls.popleft()
        return calls

    def count(self, key: str) -> int:
        with self._lock:
            calls = self._trim(key, self._clock())
            if not calls:
                self._calls.pop(key, None)
            return len(calls)

    def try_acquire(self, key: str, limit: int) -> bool:
        """上限の手前なら1件記録して True。上限に達していれば記録せずに False。"""
        with self._lock:
            now = self._clock()
            calls = self._trim(key, now)
            if len(calls) >= limit:
                return False
            calls.append(now)
            return True


@dataclass
class PermissionRequest:
    """画像を LLM に送る前の許可の求め(ADR-0048 14.1)。`event` は答えが届いたら立つ。"""

    id: str
    owner: str
    conversation_id: uuid.UUID
    event: anyio.Event = field(default_factory=anyio.Event)
    answer: str | None = None


class PermissionRegistry:
    """待っている許可の求め。イベントループの中(async の関数)からだけ触る。"""

    def __init__(self) -> None:
        self._pending: dict[str, PermissionRequest] = {}

    def create(self, owner: str, conversation_id: uuid.UUID) -> PermissionRequest:
        request = PermissionRequest(
            id=secrets.token_urlsafe(16), owner=owner, conversation_id=conversation_id
        )
        self._pending[request.id] = request
        return request

    def answer(self, request_id: str, owner: str, answer: str) -> bool:
        """答える。無い・他人の・答え済みの求めなら False(何もしない)。"""
        request = self._pending.get(request_id)
        if request is None or request.owner != owner or request.answer is not None:
            return False
        request.answer = answer
        request.event.set()
        return True

    def owns(self, request_id: str, owner: str) -> bool:
        request = self._pending.get(request_id)
        return request is not None and request.owner == owner and request.answer is None

    def discard(self, request_id: str) -> None:
        self._pending.pop(request_id, None)

    def pending_ids(self) -> list[str]:
        return list(self._pending)


class AssistantRuntime:
    def __init__(self, engine: AssistantEngine, structured: StructuredEngine | None = None) -> None:
        self.engine = engine
        self.structured: StructuredEngine = structured or FakeStructuredEngine()
        self.requests = HourlyRequestCounter()
        # 領域の四角の検出の1時間の記録(ADR-0053 7章)。
        self.detection_requests = HourlyRequestCounter()
        self.permissions = PermissionRegistry()
        self._active: set[uuid.UUID] = set()
        self._active_lock = threading.Lock()

    def begin(self, conversation_id: uuid.UUID) -> bool:
        """会話の発言の処理を始める。既に処理中なら False。"""
        with self._active_lock:
            if conversation_id in self._active:
                return False
            self._active.add(conversation_id)
            return True

    def end(self, conversation_id: uuid.UUID) -> None:
        with self._active_lock:
            self._active.discard(conversation_id)


def build_runtime(fake: bool) -> AssistantRuntime:
    engine: AssistantEngine = FakeAssistantEngine() if fake else OpenAIAssistantEngine()
    structured: StructuredEngine = FakeStructuredEngine() if fake else OpenAIStructuredEngine()
    return AssistantRuntime(engine, structured)
