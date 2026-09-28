"""推定エンジン(ADR-0024 3章): LLM(プロンプトからタイトル)、VLM(画像からタグ、プロンプトが
無ければタイトルも)、ONNX タガー(WD Tagger v3)。

LLM と VLM は OpenAI 互換 API を OpenAI Python SDK で呼ぶ。Responses API(既定)と Chat
Completions の両方に対応する(手元のサーバーには Chat Completions しか持たないものがあるため)。
応答の JSON は `response_format` に頼らず、指示で JSON を求めて本文から取り出す(互換
サーバーの対応がまちまちなため)。

タグの言語(ADR-0024 6章。設定 `tag_language`):

- `native`: 各エンジンがそれぞれの言語でタグを付ける(WD Tagger は英語のまま)。VLM には
  タグの言語を指定しない。翻訳はしない。
- `localized`: 設定 `language` に合わせる。VLM のタグは `language` で付け、WD Tagger の英語の
  タグは残したうえで訳をタグとして足す(`language` が en なら訳さない)。訳は、VLM が有効なら
  同じ VLM の呼び出しで返させ(`translations`)、VLM が無効で LLM が有効なら LLM で訳す
  (`translate_tags`)。

VLM には ONNX のタグ(`known_tags`)を渡し、同じ意味のタグを付け直さないよう頼む。

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
        "出力してください。記号、引用符、かぎ括弧、マークダウン(** や # など)、説明、前置きは"
        "付けず、タイトルの文字列だけを返してください。"
    ),
    "en": (
        "You write short titles for generated images based on their prompts. Infer what the "
        "image shows from the given prompt and output exactly one English title of about 30 "
        "characters or fewer. Return only the title text: no symbols, quotes, Markdown "
        "(such as ** or #), explanations or preamble."
    ),
}

_VLM_BASE = {
    "ja": (
        "あなたは画像にタグを付ける係です。画像を見て、内容を表すタグを最大{max_tags}個"
        "付けてください。"
    ),
    "en": "You tag images. Look at the image and give up to {max_tags} tags describing it.",
}
# タグの言語の指定。native では言語を指定しない(モデル任せ)。
_VLM_TAG_FORM = {
    "localized": {
        "ja": "タグは日本語の短い名詞にしてください。",
        "en": " Write each tag as a short English noun.",
    },
    "native": {
        "ja": "タグは短い語句にしてください。",
        "en": " Keep each tag short.",
    },
}
_VLM_KNOWN_TAGS = {
    "ja": (
        "この画像には次のタグが既に付いています: {tags}。これらと同じ意味のタグは、別の言語で"
        "あっても付け直さず、足りない観点のタグだけを付けてください。"
    ),
    "en": (
        " The image already has these tags: {tags}. Do not repeat tags with the same meaning, "
        "even in another language; add only tags for aspects they miss."
    ),
}
_VLM_TRANSLATIONS_PART = {
    "ja": (
        "あわせて、既に付いているタグそれぞれの日本語訳を translations に、元のタグをキー、"
        "訳を値として入れてください。訳と同じ意味のタグは tags に入れないでください。"
    ),
    "en": (
        " Also put an English translation of each existing tag in translations, keyed by the "
        "original tag. Do not put tags with the same meaning as a translation in tags."
    ),
}
_VLM_TITLE_PART = {
    "ja": (
        "あわせて、画像の内容を表す30文字程度までのタイトルを1つ付けてください。タイトルには"
        "記号、引用符、マークダウンを付けず、タイトルの文字列だけを入れてください。"
    ),
    "en": (
        " Also give one title of about 30 characters or fewer describing the image. Put only "
        "the title text in it, without symbols, quotes or Markdown."
    ),
}
_VLM_OUTPUT = {
    "ja": "出力は次の形の JSON だけにしてください: {schema}",
    "en": " Output only JSON in this form: {schema}",
}
_VLM_HINT = {
    "ja": "参考: この画像の生成に使われたプロンプト:\n{prompt}",
    "en": "For reference, the prompt used to generate this image:\n{prompt}",
}
_TRANSLATE_INSTRUCTIONS = {
    "ja": (
        "あなたは画像のタグを翻訳する係です。与えられた英語のタグ(1行に1つ)それぞれを、"
        "日本語の短い名詞に訳してください。出力は次の形の JSON だけにしてください: "
        '{"translations": {"元のタグ": "訳"}}'
    ),
    "en": (
        "You translate image tags. Translate each given tag (one per line) into a short "
        'English noun. Output only JSON in this form: {"translations": {"original": "translation"}}'
    ),
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
    # 既に付いているタグ(ONNX)の訳。元のタグ → 訳(localized で訳すときだけ)。
    translations: dict[str, str] = field(default_factory=dict)


def wants_translation(config: AnnotationConfig) -> bool:
    """ONNX の英語のタグを訳して足すか(localized で、言語が英語以外)。"""
    return config.tag_language == "localized" and config.language != "en"


class AnnotationEngines(Protocol):
    async def title_from_prompt(self, prompt: str, ctx: EngineContext) -> str: ...

    async def describe_image(
        self,
        image_jpeg: bytes,
        prompt: str | None,
        want_title: bool,
        ctx: EngineContext,
        known_tags: list[str] | None = None,
    ) -> VlmResult:
        """`known_tags` は ONNX のタグ(同じ意味のタグを付け直さない。`wants_translation`
        なら、その訳も `translations` で返す)。"""
        ...

    async def translate_tags(self, tags: list[str], ctx: EngineContext) -> dict[str, str]:
        """VLM が無効なときに、ONNX のタグを LLM で `language` に訳す。元のタグ → 訳。"""
        ...

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


_TITLE_PREFIX_RE = re.compile(r"^(?:title|タイトル)\s*[::]\s*", re.IGNORECASE)
_HEADING_RE = re.compile(r"^#+\s*")
_TITLE_EDGE_CHARS = _TITLE_QUOTES + "*_"
_TITLE_TRAILING_PERIODS = "。．"


def _strip_title_once(text: str) -> str:
    text = text.strip()
    text = _HEADING_RE.sub("", text)
    text = _TITLE_PREFIX_RE.sub("", text)
    # 強調(**、__)は位置を問わず除く。1文字の * と _ は前後にあるときだけ除く
    # (語の中の _ は名前の一部のことがあるため)。
    text = text.replace("**", "").replace("__", "")
    text = text.strip().strip(_TITLE_EDGE_CHARS).strip()
    text = text.rstrip(_TITLE_TRAILING_PERIODS)
    if text.endswith(".") and not text.endswith(".."):
        text = text[:-1]
    return text.strip()


def clean_title(raw: str | None) -> str | None:
    """LLM の出力からタイトルを取り出す(ADR-0024 6章)。

    最初の空でない行を使い、見出しの `#`、`Title:` の前置き、マークダウンの強調
    (`**` `__`、前後の `*` `_`)、前後の引用符・かぎ括弧、末尾の句点を取り除き、長さを抑える。
    出力が揺れるので、プロンプトの指示だけに頼らずここで整える。
    """
    if not raw:
        return None
    for line in raw.strip().splitlines():
        text = line
        while True:
            stripped = _strip_title_once(text)
            if stripped == text:
                break
            text = stripped
        if text:
            return text[:TITLE_MAX_CHARS]
    return None


def parse_vlm_json(raw: str | None) -> VlmResult:
    """VLM の応答から JSON を取り出す。読めなければ AnnotationEngineError。"""
    data = _parse_json_object(raw)
    raw_tags = data.get("tags", [])
    if not isinstance(raw_tags, list):
        raise AnnotationEngineError(t("annotations.invalidVlmResponse"))
    tags = [tag.strip() for tag in raw_tags if isinstance(tag, str) and tag.strip()]
    title = data.get("title")
    return VlmResult(
        tags=tags[:VLM_MAX_TAGS],
        title=clean_title(title) if isinstance(title, str) else None,
        translations=parse_translations(data.get("translations")),
    )


def _parse_json_object(raw: str | None) -> dict[str, Any]:
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
    return data


def parse_translations(value: object) -> dict[str, str]:
    """`{"元のタグ": "訳"}` を読む。形が違う項目は捨てる(訳は補いなので失敗にしない)。"""
    if not isinstance(value, dict):
        return {}
    result: dict[str, str] = {}
    for key, translated in value.items():
        if isinstance(key, str) and isinstance(translated, str):
            key, translated = key.strip(), translated.strip()
            if key and translated:
                result[key] = translated
    return result


def build_vlm_instructions(
    config: AnnotationConfig, want_title: bool, known_tags: list[str] | None
) -> str:
    """VLM への指示(タグの言語、既に付いているタグ、訳、タイトル、出力の形)。"""
    language = config.language
    translate = bool(known_tags) and wants_translation(config)
    schema_parts = []
    if want_title:
        schema_parts.append('"title": "..."')
    schema_parts.append('"tags": ["...", "..."]')
    if translate:
        schema_parts.append('"translations": {"<tag>": "..."}')
    text = _VLM_BASE[language].format(max_tags=VLM_MAX_TAGS)
    text += _VLM_TAG_FORM[config.tag_language][language]
    if known_tags:
        text += _VLM_KNOWN_TAGS[language].format(tags=", ".join(known_tags))
    if translate:
        text += _VLM_TRANSLATIONS_PART[language]
    if want_title:
        text += _VLM_TITLE_PART[language]
    text += _VLM_OUTPUT[language].format(schema="{" + ", ".join(schema_parts) + "}")
    return text


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
        self,
        image_jpeg: bytes,
        prompt: str | None,
        want_title: bool,
        ctx: EngineContext,
        known_tags: list[str] | None = None,
    ) -> VlmResult:
        language = ctx.config.language
        instructions = build_vlm_instructions(ctx.config, want_title, known_tags)
        text = _VLM_HINT[language].format(prompt=prompt[:4000]) if prompt else "-"
        raw = await self._complete(ctx, ctx.config.vlm_model, instructions, text, image_jpeg)
        result = parse_vlm_json(raw)
        if not want_title:
            result.title = None
        if not (known_tags and wants_translation(ctx.config)):
            result.translations = {}
        return result

    async def translate_tags(self, tags: list[str], ctx: EngineContext) -> dict[str, str]:
        raw = await self._complete(
            ctx,
            ctx.config.llm_model,
            _TRANSLATE_INSTRUCTIONS[ctx.config.language],
            "\n".join(tags),
        )
        return parse_translations(_parse_json_object(raw).get("translations"))

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
        self,
        image_jpeg: bytes,
        prompt: str | None,
        want_title: bool,
        ctx: EngineContext,
        known_tags: list[str] | None = None,
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
        translations = {}
        if known_tags and wants_translation(ctx.config):
            translations = {tag: _fake_translation(tag, ctx) for tag in known_tags}
        return VlmResult(tags=["fake vlm", orientation], title=title, translations=translations)

    async def translate_tags(self, tags: list[str], ctx: EngineContext) -> dict[str, str]:
        return {tag: _fake_translation(tag, ctx) for tag in tags}

    def onnx_tags(self, image: Image.Image, ctx: EngineContext) -> list[tuple[str, float]]:
        return [("fake onnx", 0.9)]

    def release_idle(self) -> None:
        return None


def _fake_translation(tag: str, ctx: EngineContext) -> str:
    return f"訳 {tag}" if ctx.config.language == "ja" else f"tr {tag}"
