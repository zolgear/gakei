"""判定の受付(ADR-0051 8章)の、同じ要求の再送を受けないための記録。

署名は時刻の窓(前後 `MAX_SKEW_SECONDS`)の中なら何度でも通るので、同じ要求(Mod の id、
`X-Gakei-Timestamp`、本文の sha256 が同じもの)を窓の中で2回目に受けないよう、受けた要求を
プロセス内に覚えておく。GAKEI は1プロセスで動く(配達の worker もプロセス内)ので DB には置かない。
窓を過ぎた要求は署名の確認で断るので、窓の2倍を過ぎた記録は捨ててよい。再起動で記録は消えるが、
再起動の前後の窓の中の要求だけが対象になる。

- 1回目の要求は、処理の前に「処理中」として予約する。同じ要求が処理中に届けば `IN_PROGRESS`。
- 処理が終われば応答(記録した数)を覚え、2回目からは記録せずに同じ応答を返す(`DONE`)。Mod が
  応答を受け取れずに同じ要求を送り直しても、成功として扱える。
- 処理が例外で終われば予約を外す(同じ要求を送り直せば、同じ確認をもう一度する)。
- 記録は `max_entries` まで。窓の中の記録で埋まっていれば `FULL`(古い記録を捨てると、その要求の
  再送が通ってしまうため、捨てずに断る)。
"""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass
from typing import Literal

# 時刻のずれの許容(秒)。受付の API はこれを超えてずれた要求を断る。
MAX_SKEW_SECONDS = 300
# 記録を覚えておく長さ。窓は前後 `MAX_SKEW_SECONDS` なので、その2倍。
REPLAY_TTL_SECONDS = 2 * MAX_SKEW_SECONDS
REPLAY_MAX_ENTRIES = 10_000

ReserveOutcome = Literal["reserved", "in_progress", "done", "full"]


@dataclass
class _Entry:
    expires_at: float
    accepted: int | None = None  # None は処理中


@dataclass(frozen=True)
class Reservation:
    outcome: ReserveOutcome
    key: str
    # `done` のとき、1回目に記録した数。
    accepted: int | None = None


def request_key(mod_id: str, timestamp: str, body: bytes) -> str:
    digest = hashlib.sha256(body).hexdigest()
    return f"{mod_id}\n{timestamp}\n{digest}"


class VerdictReplayGuard:
    def __init__(
        self, *, ttl_seconds: float = REPLAY_TTL_SECONDS, max_entries: int = REPLAY_MAX_ENTRIES
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        expired = [k for k, e in self._entries.items() if e.expires_at <= now]
        for k in expired:
            del self._entries[k]

    def reserve(self, key: str, *, now: float | None = None) -> Reservation:
        now = time.monotonic() if now is None else now
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and entry.expires_at > now:
                if entry.accepted is None:
                    return Reservation("in_progress", key)
                return Reservation("done", key, entry.accepted)
            if len(self._entries) >= self.max_entries:
                self._prune(now)
                if len(self._entries) >= self.max_entries:
                    return Reservation("full", key)
            self._entries[key] = _Entry(expires_at=now + self.ttl_seconds)
            return Reservation("reserved", key)

    def complete(self, key: str, accepted: int) -> None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                entry.accepted = accepted

    def release(self, key: str) -> None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and entry.accepted is None:
                del self._entries[key]

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)
