"""LLM・VLM の呼び出しの失敗を、画面と API の応答に出してよい文言にする。

自動タイトル・タグのエンジン(`app/annotation/engines.py`)と生成のアシスタント
(`app/assistant/engine.py`、`structured.py`)で共有する。

API キーは一部(末尾の数文字)でも画面・API の応答に出さない。OpenAI 互換の API の 401 の本文は
キーの先頭と末尾を含むことがあるので、認証・権限の失敗は本文を使わず固定の文言にし、それ以外の
失敗も本文からキーの形の文字列を伏せる。
"""

from __future__ import annotations

import openai

from app.domain.secret_values import redact_secret_text
from app.i18n import t


def safe_api_message(e: openai.APIError) -> str:
    """openai の例外を `{code か type か例外名}: {文言}` にする。キーの断片は含めない。"""
    if isinstance(e, openai.AuthenticationError):
        return f"{e.__class__.__name__}: {t('llm.authFailed')}"
    if isinstance(e, openai.PermissionDeniedError):
        return f"{e.__class__.__name__}: {t('llm.permissionDenied')}"
    label = getattr(e, "code", None) or getattr(e, "type", None) or e.__class__.__name__
    detail = getattr(e, "message", None) or str(e)
    return redact_secret_text(f"{label}: {detail}")
