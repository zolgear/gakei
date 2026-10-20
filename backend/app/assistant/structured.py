"""アシスタントの接続先で、決まった形の JSON を1回だけ受け取る呼び出し(ADR-0052 6章)。

会話(`engine.py`)とは別に、道具もストリーミングも使わず、Structured Outputs(JSON Schema の
strict)で応答を受ける。今は ComfyUI のワークフローの推定だけが使う。

- `OpenAIStructuredEngine`: OpenAI 互換 API を OpenAI Python SDK で呼ぶ。接続先の API 形式
  (ADR-0032)で、Responses API(`text.format`)と Chat Completions(`response_format`)を
  切り替える。reasoning effort を渡し、接続先が知らないと断られたら付けずに送り直す。
- `FakeStructuredEngine`: `FAKE_PROVIDER=1` とテスト用。LLM を呼ばず、要求に添えた決まった
  応答(`StructuredRequest.fake_response`)か、テストが入れた応答を返す。

どちらも、応答の JSON の中身の検証はしない(呼び出し側が読む)。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

import openai

from app.assistant.engine import AssistantEngineError
from app.domain.llm_connections import LlmTarget
from app.i18n import t
from app.llm.clients import LlmClientCache
from app.llm.errors import safe_api_message

logger = logging.getLogger(__name__)

# 1回の呼び出しのタイムアウト(秒)。検証では 1回あたり数秒だった。
_TIMEOUT_SECONDS = 120.0


@dataclass
class StructuredRequest:
    target: LlmTarget | None
    instructions: str
    input_text: str
    schema_name: str
    schema: dict[str, Any]
    reasoning_effort: str | None = "low"
    # Fake が返す応答(FAKE_PROVIDER=1 用。実 API には送らない)。
    fake_response: dict[str, Any] | None = None


@dataclass
class StructuredResult:
    data: dict[str, Any]
    # 応答したモデルの名前(接続先が返した値。無ければ要求したモデル)。
    model: str | None = None
    # トークン数(`input_tokens`、`output_tokens`、`reasoning_tokens`、`total_tokens`)。
    usage: dict[str, int] = field(default_factory=dict)


class StructuredEngine(Protocol):
    # 送り先(接続先とキー)が要るか。Fake は要らない。
    requires_target: bool

    async def complete(self, request: StructuredRequest) -> StructuredResult: ...


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _usage(raw: Any) -> dict[str, int]:
    """Responses / Chat Completions の usage を同じ形にする(無い項目は省く)。"""
    if raw is None:
        return {}
    get = (lambda k: raw.get(k)) if isinstance(raw, dict) else (lambda k: getattr(raw, k, None))
    result: dict[str, int] = {}
    input_tokens = _int(get("input_tokens"))
    if input_tokens is None:
        input_tokens = _int(get("prompt_tokens"))
    output_tokens = _int(get("output_tokens"))
    if output_tokens is None:
        output_tokens = _int(get("completion_tokens"))
    details = get("output_tokens_details") or get("completion_tokens_details")
    reasoning = None
    if details is not None:
        reasoning = _int(
            details.get("reasoning_tokens")
            if isinstance(details, dict)
            else getattr(details, "reasoning_tokens", None)
        )
    total = _int(get("total_tokens"))
    for key, value in (
        ("input_tokens", input_tokens),
        ("output_tokens", output_tokens),
        ("reasoning_tokens", reasoning),
        ("total_tokens", total),
    ):
        if value is not None:
            result[key] = value
    return result


def _parse_json(text: str | None) -> dict[str, Any]:
    if not text or not text.strip():
        raise AssistantEngineError(t("comfyui.suggest.emptyResponse"))
    try:
        value = json.loads(text)
    except ValueError as e:
        raise AssistantEngineError(t("comfyui.suggest.invalidResponse")) from e
    if not isinstance(value, dict):
        raise AssistantEngineError(t("comfyui.suggest.invalidResponse"))
    return value


def _mentions_reasoning(e: openai.BadRequestError) -> bool:
    return "reasoning" in str(e).lower()


class OpenAIStructuredEngine:
    requires_target = True

    def __init__(self) -> None:
        # 会話のエンジンとはキャッシュを分ける(片方が捨てたクライアントを、もう片方が使って
        # いる最中に閉じないため。`app/llm/clients.py`)。
        self._cache = LlmClientCache(timeout=_TIMEOUT_SECONDS)

    async def complete(self, request: StructuredRequest) -> StructuredResult:
        target = request.target
        if target is None:
            raise AssistantEngineError(t("assistant.connectionMissing"))
        if not target.api_key:
            raise AssistantEngineError(t("assistant.apiKeyMissing"))
        client = self._cache.get(target)
        try:
            if target.api_style == "chat":
                return await self._chat(client, target, request)
            return await self._responses(client, target, request)
        except openai.APIError as e:
            raise AssistantEngineError(safe_api_message(e)) from e

    async def _responses(
        self, client: Any, target: LlmTarget, request: StructuredRequest
    ) -> StructuredResult:
        kwargs: dict[str, Any] = {
            "model": target.model,
            "instructions": request.instructions,
            "input": request.input_text,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": request.schema_name,
                    "schema": request.schema,
                    "strict": True,
                }
            },
            "store": False,
        }
        if request.reasoning_effort:
            kwargs["reasoning"] = {"effort": request.reasoning_effort}
        try:
            response = await client.responses.create(**kwargs)
        except openai.BadRequestError as e:
            # 推論の設定を持たないモデル・接続先は、付けずに送り直す。
            if "reasoning" not in kwargs or not _mentions_reasoning(e):
                raise
            kwargs.pop("reasoning")
            response = await client.responses.create(**kwargs)
        error = getattr(response, "error", None)
        if error is not None:
            raise AssistantEngineError(getattr(error, "message", None) or str(error))
        text: str | None = None
        for item in getattr(response, "output", None) or []:
            if getattr(item, "type", None) != "message":
                continue
            for content in getattr(item, "content", None) or []:
                kind = getattr(content, "type", None)
                if kind == "refusal":
                    raise AssistantEngineError(
                        t("comfyui.suggest.refused", reason=getattr(content, "refusal", "") or "-")
                    )
                if kind == "output_text":
                    text = getattr(content, "text", None)
        return StructuredResult(
            data=_parse_json(text),
            model=getattr(response, "model", None) or target.model,
            usage=_usage(getattr(response, "usage", None)),
        )

    async def _chat(
        self, client: Any, target: LlmTarget, request: StructuredRequest
    ) -> StructuredResult:
        kwargs: dict[str, Any] = {
            "model": target.model,
            "messages": [
                {"role": "system", "content": request.instructions},
                {"role": "user", "content": request.input_text},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": request.schema_name,
                    "schema": request.schema,
                    "strict": True,
                },
            },
        }
        if request.reasoning_effort:
            kwargs["reasoning_effort"] = request.reasoning_effort
        try:
            response = await client.chat.completions.create(**kwargs)
        except openai.BadRequestError as e:
            if "reasoning_effort" not in kwargs or not _mentions_reasoning(e):
                raise
            kwargs.pop("reasoning_effort")
            response = await client.chat.completions.create(**kwargs)
        choices = getattr(response, "choices", None) or []
        message = getattr(choices[0], "message", None) if choices else None
        refusal = getattr(message, "refusal", None) if message is not None else None
        if refusal:
            raise AssistantEngineError(t("comfyui.suggest.refused", reason=refusal))
        return StructuredResult(
            data=_parse_json(getattr(message, "content", None) if message is not None else None),
            model=getattr(response, "model", None) or target.model,
            usage=_usage(getattr(response, "usage", None)),
        )


class FakeStructuredEngine:
    """LLM を呼ばない。`responses` に入れた応答(テスト)を先に、無ければ要求に添えた
    `fake_response` を返す。どちらも無ければ空のオブジェクト。"""

    requires_target = False

    def __init__(self) -> None:
        self.requests: list[StructuredRequest] = []
        # テストが入れる応答(dict を返す、または例外を投げる)。先頭から使う。
        self.responses: list[dict[str, Any] | Exception] = []

    async def complete(self, request: StructuredRequest) -> StructuredResult:
        self.requests.append(request)
        if self.responses:
            item = self.responses.pop(0)
            if isinstance(item, Exception):
                raise item
            data = item
        else:
            data = request.fake_response or {}
        model = request.target.model if request.target is not None else "fake-structured"
        return StructuredResult(
            data=json.loads(json.dumps(data)),
            model=model,
            usage={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        )
