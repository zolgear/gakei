"""アシスタントのプロセス内の状態(ADR-0048 7章)。`app.state.assistant` に1つ置く。

- エンジン(`FAKE_PROVIDER=1` では Fake)。
- LLM のリクエストの1時間の記録(利用者ごと)。プロセス内で数え、再起動で 0 に戻る
  (ADR-0024 8章と同じ)。上限に達したら待たせずに断る。
- 進行中の会話(同じ会話で同時に2つの発言を処理しない)。
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque

from app.assistant.engine import AssistantEngine, FakeAssistantEngine, OpenAIAssistantEngine

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


class AssistantRuntime:
    def __init__(self, engine: AssistantEngine) -> None:
        self.engine = engine
        self.requests = HourlyRequestCounter()
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
    return AssistantRuntime(engine)
