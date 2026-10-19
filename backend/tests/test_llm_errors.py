"""LLM・VLM・埋め込みの接続先の失敗の文言に、API キーの断片を出さないこと。

方針: API キーは末尾の数文字も含め、一部も画面・API の応答に出さない。実 API は呼ばない。
"""

from __future__ import annotations

import httpx
import openai
import pytest

from app.domain.secret_values import REDACTED, redact_secret_text
from app.embedding.base import EmbeddingError
from app.embedding.remote import InfinityEngine
from app.i18n import t
from app.llm.errors import safe_api_message

# OpenAI の 401 の本文の形(途中を伏せ、先頭と末尾が残る)。
_MASKED_KEY = "sk-proj-abc1********************************wxyz"
_FULL_KEY = "sk-proj-AbCdEf0123456789GhIjKlMnOpQrStUv"
_FRAGMENTS = ("abc1", "wxyz", "sk-proj", "AbCdEf0123", "QrStUv")


def _status_error(cls: type[openai.APIStatusError], status: int, message: str) -> openai.APIError:
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    body = {
        "error": {"message": message, "type": "invalid_request_error", "code": "invalid_api_key"}
    }
    return cls(message, response=httpx.Response(status, request=request), body=body)


def _assert_no_fragment(text: str) -> None:
    for fragment in _FRAGMENTS:
        assert fragment not in text, (fragment, text)


@pytest.mark.parametrize(
    ("cls", "status", "key"),
    [
        (openai.AuthenticationError, 401, "llm.authFailed"),
        (openai.PermissionDeniedError, 403, "llm.permissionDenied"),
    ],
)
def test_auth_errors_use_fixed_message(
    cls: type[openai.APIStatusError], status: int, key: str
) -> None:
    message = (
        f"Incorrect API key provided: {_MASKED_KEY}. You can find your API key at "
        "https://platform.openai.com/account/api-keys."
    )
    result = safe_api_message(_status_error(cls, status, message))
    _assert_no_fragment(result)
    assert t(key) in result


def test_other_api_errors_redact_key_like_strings() -> None:
    message = f"Bad request for key {_FULL_KEY} and {_MASKED_KEY}; model not found"
    result = safe_api_message(_status_error(openai.BadRequestError, 400, message))
    _assert_no_fragment(result)
    assert "model not found" in result
    assert REDACTED in result


def test_redact_secret_text_keeps_ordinary_words() -> None:
    text = "task-runner failed: Bearer abcdefghijklmnopqrstuvwxyz0123 hf_" + "a1" * 20
    redacted = redact_secret_text(text)
    assert "task-runner failed" in redacted
    assert "abcdefghijklmnopqrstuvwxyz0123" not in redacted
    assert "a1a1a1" not in redacted


def test_remote_embedding_error_body_is_redacted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text=f'{{"detail": "invalid key {_MASKED_KEY}"}}')

    engine = InfinityEngine(
        connection_id="abc",
        base_url="http://infer.local:7997",
        model="m",
        api_key=_FULL_KEY,
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(EmbeddingError) as excinfo:
        engine.embed_texts(["hello"])
    _assert_no_fragment(str(excinfo.value))
    assert "401" in str(excinfo.value)
