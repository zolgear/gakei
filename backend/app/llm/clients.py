"""接続先ごとの OpenAI 互換クライアントのキャッシュ(ADR-0024 8章、ADR-0048 7章)。

自動タイトル・タグのエンジン(`app/annotation/engines.py`)と生成のアシスタントのエンジン
(`app/assistant/engine.py`)が、同じ作りのキャッシュをそれぞれ1つずつ持つ。インスタンスを
分けるのは、片方が上限を超えて捨てたクライアントを、もう片方が使っている最中に閉じないため。
"""

from __future__ import annotations

import asyncio
import logging

from openai import AsyncOpenAI

from app.domain.llm_connections import LlmTarget

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 120.0


class LlmClientCache:
    """(キー, Base URL) ごとのクライアント。使った順に並べ(最後が最新)、上限を超えたら
    最も古いものから捨てて閉じる。キーの有無は呼び出し側で確かめる。"""

    # 接続先ごとのクライアントを持つ上限(設定を何度も変えたときに増え続けないように)。
    CLIENTS_MAX = 8

    def __init__(self, *, timeout: float = DEFAULT_TIMEOUT_SECONDS, max_retries: int = 2) -> None:
        self.timeout = timeout
        self.max_retries = max_retries
        self.clients: dict[tuple[str, str | None], AsyncOpenAI] = {}
        # 捨てたクライアントを閉じるタスク(終わるまで参照を持っておく)。
        self.closing: set[asyncio.Task[None]] = set()

    def get(self, target: LlmTarget) -> AsyncOpenAI:
        assert target.api_key, "キーの有無は呼び出し側で確かめる"
        key = (target.api_key, target.base_url)
        client = self.clients.pop(key, None)
        if client is None:
            while len(self.clients) >= self.CLIENTS_MAX:
                oldest = next(iter(self.clients))
                self._discard(self.clients.pop(oldest))
            client = AsyncOpenAI(
                api_key=target.api_key,
                base_url=target.base_url,
                max_retries=self.max_retries,
                timeout=self.timeout,
            )
        # 最新として末尾に入れ直す。
        self.clients[key] = client
        return client

    def _discard(self, client: AsyncOpenAI) -> None:
        """捨てたクライアントを閉じる(接続を残さないように)。`AsyncOpenAI.close()` は
        コルーチンなので、イベントループの上ではタスクにして閉じる。ループの外なら閉じずに
        ガベージコレクションに任せる。"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._close_quietly(client))
        self.closing.add(task)
        task.add_done_callback(self.closing.discard)

    @staticmethod
    async def _close_quietly(client: AsyncOpenAI) -> None:
        try:
            await client.close()
        except Exception:  # noqa: BLE001 - 捨てるクライアントの後始末の失敗は無視する
            logger.debug("LLM のクライアントを閉じられませんでした", exc_info=True)
