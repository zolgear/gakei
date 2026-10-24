"""アシスタントの接続先で、決まった形の JSON を1回だけ受け取る呼び出し(ADR-0052 6章)。

会話(`engine.py`)とは別に、道具もストリーミングも使わず、Structured Outputs(JSON Schema の
strict)で応答を受ける。ComfyUI のワークフローの推定と、領域の四角の検出(ADR-0053 7章。画像を
添える)が使う。

- `OpenAIStructuredEngine`: OpenAI 互換 API を OpenAI Python SDK で呼ぶ。接続先の API 形式
  (ADR-0032)で、Responses API(`text.format`)と Chat Completions(`response_format`)を
  切り替える。reasoning effort を渡し、接続先が知らないと断られたら付けずに送り直す。
  スキーマの無い要求(`schema=None`。構造化出力に対応しない接続先)では、形の指定を付けずに
  送る。出力のトークン数の上限(`max_output_tokens`)を渡せる。
- `raw_text` の要求では、応答の JSON を読まずに文のまま返す(呼び出し側が緩く読む。領域の
  検出の手元の VLM。ADR-0053 7章)。
- `FakeStructuredEngine`: `FAKE_PROVIDER=1` とテスト用。LLM を呼ばず、要求に添えた決まった
  応答(`StructuredRequest.fake_response`)か、テストが入れた応答を返す。

どちらも、応答の JSON の中身の検証はしない(呼び出し側が読む)。
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import openai

from app.assistant.engine import AssistantEngineError
from app.domain.llm_connections import LlmTarget
from app.i18n import t
from app.llm.clients import LlmClientCache
from app.llm.errors import safe_api_message

logger = logging.getLogger(__name__)

# 1回の呼び出しのタイムアウト(秒)。検証では 1回あたり数秒だった。
_TIMEOUT_SECONDS = 120.0


@dataclass(frozen=True)
class StructuredImage:
    """要求に添える画像(VLM 向け)。呼び出し側が縮めて JPEG などにしたもの。"""

    data: bytes
    mime: str = "image/jpeg"
    # 画像の細かさの指定(OpenAI の `detail`)。None なら付けない。
    detail: Literal["low", "high", "auto"] | None = "high"

    def data_url(self) -> str:
        return f"data:{self.mime};base64," + base64.b64encode(self.data).decode("ascii")


@dataclass
class StructuredRequest:
    target: LlmTarget | None
    instructions: str
    input_text: str
    schema_name: str
    # 応答の JSON のスキーマ。None なら形の指定を付けずに送る(指示文で形を示す前提)。
    schema: dict[str, Any] | None
    reasoning_effort: str | None = "low"
    # 出力のトークン数の上限(None なら付けない)。Responses は `max_output_tokens`、
    # Chat Completions は `max_completion_tokens`(断られたら `max_tokens`)で渡す。
    max_output_tokens: int | None = None
    # 真なら応答を JSON として読まず、`StructuredResult.text` に文のまま返す。
    raw_text: bool = False
    # Fake が返す応答(FAKE_PROVIDER=1 用。実 API には送らない)。
    fake_response: dict[str, Any] | None = None
    # 添える画像(文の後ろに、この順で送る)。空なら文だけを送る(今までと同じ形)。
    images: tuple[StructuredImage, ...] = ()


def _responses_input(request: StructuredRequest) -> Any:
    """Responses API の `input`。画像が無ければ文そのもの。"""
    if not request.images:
        return request.input_text
    parts: list[dict[str, Any]] = [{"type": "input_text", "text": request.input_text}]
    for image in request.images:
        part: dict[str, Any] = {"type": "input_image", "image_url": image.data_url()}
        if image.detail is not None:
            part["detail"] = image.detail
        parts.append(part)
    return [{"role": "user", "content": parts}]


def _chat_user_content(request: StructuredRequest) -> Any:
    """Chat Completions の利用者の発言の `content`。画像が無ければ文そのもの。"""
    if not request.images:
        return request.input_text
    parts: list[dict[str, Any]] = [{"type": "text", "text": request.input_text}]
    for image in request.images:
        image_url: dict[str, Any] = {"url": image.data_url()}
        if image.detail is not None:
            image_url["detail"] = image.detail
        parts.append({"type": "image_url", "image_url": image_url})
    return parts


@dataclass
class StructuredResult:
    data: dict[str, Any]
    # 応答したモデルの名前(接続先が返した値。無ければ要求したモデル)。
    model: str | None = None
    # トークン数(`input_tokens`、`output_tokens`、`reasoning_tokens`、`total_tokens`)。
    usage: dict[str, int] = field(default_factory=dict)
    # 応答の文(`raw_text` の要求のときだけ。`data` は空)。
    text: str | None = None


def _result(
    request: StructuredRequest, text: str | None, model: str | None, usage: dict[str, int]
) -> StructuredResult:
    if request.raw_text:
        return StructuredResult(data={}, model=model, usage=usage, text=text or "")
    return StructuredResult(data=_parse_json(text), model=model, usage=usage)


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


def _mentions_max_completion_tokens(e: openai.BadRequestError) -> bool:
    return "max_completion_tokens" in str(e).lower()


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
            "input": _responses_input(request),
            "store": False,
        }
        if request.schema is not None:
            kwargs["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": request.schema_name,
                    "schema": request.schema,
                    "strict": True,
                }
            }
        if request.max_output_tokens is not None:
            # 上限で切れた応答(`status = incomplete`)も、途中までの文を返す(呼び出し側が読む)。
            kwargs["max_output_tokens"] = request.max_output_tokens
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
        return _result(
            request,
            text,
            getattr(response, "model", None) or target.model,
            _usage(getattr(response, "usage", None)),
        )

    async def _chat(
        self, client: Any, target: LlmTarget, request: StructuredRequest
    ) -> StructuredResult:
        kwargs: dict[str, Any] = {
            "model": target.model,
            "messages": [
                {"role": "system", "content": request.instructions},
                {"role": "user", "content": _chat_user_content(request)},
            ],
        }
        if request.schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": request.schema_name,
                    "schema": request.schema,
                    "strict": True,
                },
            }
        if request.max_output_tokens is not None:
            kwargs["max_completion_tokens"] = request.max_output_tokens
        if request.reasoning_effort:
            kwargs["reasoning_effort"] = request.reasoning_effort
        response = None
        # 断られた項目を外して(上限は古い名前の `max_tokens` に替えて)送り直す。1回の断りで
        # 1つずつ直す。
        for _ in range(3):
            try:
                response = await client.chat.completions.create(**kwargs)
                break
            except openai.BadRequestError as e:
                if "reasoning_effort" in kwargs and _mentions_reasoning(e):
                    kwargs.pop("reasoning_effort")
                elif "max_completion_tokens" in kwargs and _mentions_max_completion_tokens(e):
                    kwargs["max_tokens"] = kwargs.pop("max_completion_tokens")
                else:
                    raise
        if response is None:
            response = await client.chat.completions.create(**kwargs)
        choices = getattr(response, "choices", None) or []
        message = getattr(choices[0], "message", None) if choices else None
        refusal = getattr(message, "refusal", None) if message is not None else None
        if refusal:
            raise AssistantEngineError(t("comfyui.suggest.refused", reason=refusal))
        return _result(
            request,
            getattr(message, "content", None) if message is not None else None,
            getattr(response, "model", None) or target.model,
            _usage(getattr(response, "usage", None)),
        )


class FakeStructuredEngine:
    """LLM を呼ばない。`responses` に入れた応答(テスト)を先に、無ければ要求に添えた
    `fake_response` を返す。どちらも無ければ空のオブジェクト。`raw_text` の要求では、文の応答
    (テストが入れた str)はそのまま、ほかは JSON の文にして返す。"""

    requires_target = False

    def __init__(self) -> None:
        self.requests: list[StructuredRequest] = []
        # テストが入れる応答(dict を返す、または例外を投げる)。先頭から使う。
        self.responses: list[Any] = []

    async def complete(self, request: StructuredRequest) -> StructuredResult:
        self.requests.append(request)
        data: Any
        if self.responses:
            item = self.responses.pop(0)
            if isinstance(item, Exception):
                raise item
            data = item
        else:
            data = request.fake_response or {}
        model = request.target.model if request.target is not None else "fake-structured"
        usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        if request.raw_text:
            text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
            return StructuredResult(data={}, model=model, usage=usage, text=text)
        return StructuredResult(data=json.loads(json.dumps(data)), model=model, usage=usage)
