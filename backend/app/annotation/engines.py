"""推定エンジン(ADR-0024 3章): LLM(プロンプトからタイトル)、VLM(画像からタグ、プロンプトが
無ければタイトルも)、ONNX タガー(WD Tagger v3)。

LLM と VLM は OpenAI 互換 API を OpenAI Python SDK で呼ぶ。Responses API(既定)と Chat
Completions の両方に対応する(手元のサーバーには Chat Completions しか持たないものがあるため)。
応答の JSON は `response_format` に頼らず、指示で JSON を求めて本文から取り出す(互換
サーバーの対応がまちまちなため)。

`FAKE_PROVIDER=1` のときは `FakeEngines`(決定的なタイトルとタグ。課金もダウンロードもしない)
に差し替える。
"""

from __future__ import annotations

import base64
import io
import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

import openai
from openai import AsyncOpenAI
from PIL import Image

from app.annotation.wd_tagger import WdTagger
from app.domain.annotation_settings import AnnotationConfig, Connection
from app.i18n import t

VLM_MAX_SIDE = 1024
VLM_MAX_TAGS = 10
TITLE_MAX_CHARS = 100
_TIMEOUT_SECONDS = 120.0

_TITLE_INSTRUCTIONS = {
    "ja": (
        "あなたは画像生成プロンプトから、画像の短いタイトルを付ける係です。"
        "与えられたプロンプトから画像の内容を推測し、日本語で30文字程度までのタイトルを1つだけ"
        "出力してください。引用符、説明、前置きは付けないでください。"
    ),
    "en": (
        "You write short titles for generated images based on their prompts. Infer what the "
        "image shows from the given prompt and output exactly one English title of about 30 "
        "characters or fewer. Do not add quotes, explanations or any preamble."
    ),
}

_VLM_INSTRUCTIONS = {
    "ja": (
        "あなたは画像にタグを付ける係です。画像を見て、内容を表すタグを最大{max_tags}個、"
        "日本語の短い名詞で付けてください。{title_part}"
        "出力は次の形の JSON だけにしてください: {schema}"
    ),
    "en": (
        "You tag images. Look at the image and give up to {max_tags} tags describing it, as "
        "short English nouns. {title_part}Output only JSON in this form: {schema}"
    ),
}
_VLM_TITLE_PART = {
    "ja": "あわせて、画像の内容を表す30文字程度までのタイトルを1つ付けてください。",
    "en": "Also give one title of about 30 characters or fewer describing the image. ",
}
_VLM_HINT = {
    "ja": "参考: この画像の生成に使われたプロンプト:\n{prompt}",
    "en": "For reference, the prompt used to generate this image:\n{prompt}",
}
_TITLE_QUOTES = "\"'「」『』“”‘’`"


class AnnotationEngineError(Exception):
    """推定に失敗した(`failed` として理由を記録する)。"""


@dataclass
class EngineContext:
    config: AnnotationConfig
    connection: Connection


@dataclass
class VlmResult:
    tags: list[str] = field(default_factory=list)
    title: str | None = None


class AnnotationEngines(Protocol):
    async def title_from_prompt(self, prompt: str, ctx: EngineContext) -> str: ...

    async def describe_image(
        self, image_jpeg: bytes, prompt: str | None, want_title: bool, ctx: EngineContext
    ) -> VlmResult: ...

    def onnx_tags(self, image: Image.Image, ctx: EngineContext) -> list[tuple[str, float]]:
        """同期関数(呼び出し側が `asyncio.to_thread` で呼ぶ)。"""
        ...

    def release_idle(self) -> None: ...


# -- 共通の小道具 ----------------------------------------------------------------


def image_to_jpeg(image: Image.Image, max_side: int = VLM_MAX_SIDE) -> bytes:
    """VLM に送る画像。長辺 `max_side` 以内に縮め、透過は白で埋めて JPEG にする。"""
    copy = image.copy()
    copy.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    if copy.mode in ("RGBA", "LA", "P"):
        rgba = copy.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        copy = Image.alpha_composite(background, rgba)
    buffer = io.BytesIO()
    copy.convert("RGB").save(buffer, format="JPEG", quality=85)
    return buffer.getvalue()


def clean_title(raw: str | None) -> str | None:
    """LLM の出力からタイトルを取り出す(最初の行、前後の引用符を除く、長さを抑える)。"""
    if not raw:
        return None
    for line in raw.strip().splitlines():
        text = line.strip().strip(_TITLE_QUOTES).strip()
        if text.lower().startswith("title:"):
            text = text[len("title:") :].strip().strip(_TITLE_QUOTES).strip()
        if text:
            return text[:TITLE_MAX_CHARS]
    return None


def parse_vlm_json(raw: str | None) -> VlmResult:
    """VLM の応答から JSON を取り出す。読めなければ AnnotationEngineError。"""
    if not raw:
        raise AnnotationEngineError(t("annotations.invalidVlmResponse"))
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise AnnotationEngineError(t("annotations.invalidVlmResponse"))
    try:
        data = json.loads(text[start : end + 1])
    except ValueError as e:
        raise AnnotationEngineError(t("annotations.invalidVlmResponse")) from e
    if not isinstance(data, dict):
        raise AnnotationEngineError(t("annotations.invalidVlmResponse"))
    raw_tags = data.get("tags", [])
    if not isinstance(raw_tags, list):
        raise AnnotationEngineError(t("annotations.invalidVlmResponse"))
    tags = [tag.strip() for tag in raw_tags if isinstance(tag, str) and tag.strip()]
    title = data.get("title")
    return VlmResult(
        tags=tags[:VLM_MAX_TAGS],
        title=clean_title(title) if isinstance(title, str) else None,
    )


def _safe_api_message(e: openai.APIError) -> str:
    label = getattr(e, "code", None) or getattr(e, "type", None) or e.__class__.__name__
    detail = getattr(e, "message", None) or str(e)
    return f"{label}: {detail}"


# -- OpenAI 互換 API と ONNX を使う本物のエンジン --------------------------------------


class OpenAIEngines:
    def __init__(self, tagger: WdTagger) -> None:
        self.tagger = tagger
        self._client: AsyncOpenAI | None = None
        self._client_key: tuple[str, str | None] | None = None

    def _client_for(self, connection: Connection) -> AsyncOpenAI:
        if not connection.api_key:
            raise AnnotationEngineError(t("annotations.apiKeyMissing"))
        key = (connection.api_key, connection.base_url)
        if self._client is None or self._client_key != key:
            self._client = AsyncOpenAI(
                api_key=connection.api_key,
                base_url=connection.base_url,
                max_retries=2,
                timeout=_TIMEOUT_SECONDS,
            )
            self._client_key = key
        return self._client

    async def _complete(
        self,
        ctx: EngineContext,
        model: str,
        instructions: str,
        text: str,
        image_jpeg: bytes | None = None,
    ) -> str:
        client = self._client_for(ctx.connection)
        data_url = None
        if image_jpeg is not None:
            data_url = "data:image/jpeg;base64," + base64.b64encode(image_jpeg).decode("ascii")
        try:
            if ctx.config.api_style == "chat":
                content: Any = text
                if data_url is not None:
                    content = [
                        {"type": "text", "text": text},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ]
                response = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": instructions},
                        {"role": "user", "content": content},
                    ],
                )
                return (response.choices[0].message.content or "") if response.choices else ""
            parts: list[dict[str, Any]] = [{"type": "input_text", "text": text}]
            if data_url is not None:
                parts.append({"type": "input_image", "image_url": data_url})
            response = await client.responses.create(
                model=model,
                instructions=instructions,
                input=[{"role": "user", "content": parts}],
            )
            return response.output_text or ""
        except openai.APIError as e:
            raise AnnotationEngineError(_safe_api_message(e)) from e

    async def title_from_prompt(self, prompt: str, ctx: EngineContext) -> str:
        language = ctx.config.language
        raw = await self._complete(
            ctx, ctx.config.llm_model, _TITLE_INSTRUCTIONS[language], prompt[:4000]
        )
        title = clean_title(raw)
        if title is None:
            raise AnnotationEngineError(t("annotations.emptyTitle"))
        return title

    async def describe_image(
        self, image_jpeg: bytes, prompt: str | None, want_title: bool, ctx: EngineContext
    ) -> VlmResult:
        language = ctx.config.language
        schema = '{"title": "...", "tags": ["...", "..."]}' if want_title else '{"tags": ["..."]}'
        instructions = _VLM_INSTRUCTIONS[language].format(
            max_tags=VLM_MAX_TAGS,
            title_part=_VLM_TITLE_PART[language] if want_title else "",
            schema=schema,
        )
        text = _VLM_HINT[language].format(prompt=prompt[:4000]) if prompt else "-"
        raw = await self._complete(ctx, ctx.config.vlm_model, instructions, text, image_jpeg)
        result = parse_vlm_json(raw)
        if not want_title:
            result.title = None
        return result

    def onnx_tags(self, image: Image.Image, ctx: EngineContext) -> list[tuple[str, float]]:
        try:
            return self.tagger.tag(image, ctx.config.onnx_model, ctx.config.onnx_threshold)
        except FileNotFoundError as e:
            raise AnnotationEngineError(t("annotations.onnxModelMissing")) from e

    def release_idle(self) -> None:
        self.tagger.release_if_idle()


# -- FAKE_PROVIDER=1 用のダミー ----------------------------------------------------


class FakeEngines:
    """決定的なタイトルとタグを返す(課金なし・ダウンロードなし。開発・E2E 確認・テスト用)。"""

    async def title_from_prompt(self, prompt: str, ctx: EngineContext) -> str:
        head = " ".join(prompt.split())[:20]
        return f"ダミー: {head}" if ctx.config.language == "ja" else f"Fake: {head}"

    async def describe_image(
        self, image_jpeg: bytes, prompt: str | None, want_title: bool, ctx: EngineContext
    ) -> VlmResult:
        with Image.open(io.BytesIO(image_jpeg)) as image:
            orientation = (
                "landscape"
                if image.width > image.height
                else "portrait"
                if image.height > image.width
                else "square"
            )
        title = None
        if want_title:
            title = "ダミー画像" if ctx.config.language == "ja" else "Fake image"
        return VlmResult(tags=["fake vlm", orientation], title=title)

    def onnx_tags(self, image: Image.Image, ctx: EngineContext) -> list[tuple[str, float]]:
        return [("fake onnx", 0.9)]

    def release_idle(self) -> None:
        return None
