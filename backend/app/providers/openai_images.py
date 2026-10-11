"""OpenAI 実プロバイダー。`AsyncOpenAI` で `images.generate` / `images.edit` を呼ぶ。

設計のポイント(計画・CLAUDE.md 参照):
- `run.params` の値をそのまま SDK に渡す。既定値で埋めたり、勝手にキーを足したりしない。
  例外は `model` / `prompt`(Run の列から渡す)と、
  edit の `image` / `mask`(入力 Asset から組み立てる)、
  および `partial_images >= 1` のときだけ付ける `stream=True`
  (`stream` 自体は params に保存しない)。
- base64 はデコードした bytes として扱い、
  `RunResult` / 例外メッセージ / ログに base64 文字列を残さない。
- SDK 3.16.2 の `images.generate` / `images.edit` は `quality` の Literal に `xhigh` / `max` を
  含んでいるため、追加のパラメータをそのまま渡せば型を素通りする(`extra_body` は不要だった)。
"""

from __future__ import annotations

import base64
from typing import Any

import openai
from openai import AsyncOpenAI
from openai.types.images_response import ImagesResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.domain import general_settings
from app.domain.api_key import resolve_base_url, resolve_key
from app.i18n import t
from app.providers import openai_spec
from app.providers.base import (
    InputImage,
    PartialImageEvent,
    ProgressCallback,
    ProviderCapabilities,
    ProviderError,
    RunDraft,
    RunOutputImage,
    RunRequest,
    RunResult,
)

# InputImage.mime -> (ファイル名の拡張子)。ingest 側が許可する形式(png/jpeg/webp)と対応させる。
_MIME_TO_EXT = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
}

_OUTPUT_FORMAT_TO_MIME = {
    "png": "image/png",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
}

# コンテンツ拒否と判定する error.code。実機で他の値が判明した場合はここに追加する。
_CONTENT_FILTER_CODES = {"moderation_blocked", "content_policy_violation"}
# code だけで判定できない場合に error.type / message から拾うヒント。
_CONTENT_FILTER_HINTS = ("moderation", "content_policy", "safety")


class OpenAIImagesProvider:
    """`ImageProvider` プロトコルの OpenAI 実装。

    キーは実行時(`execute`)まで解決しない(ADR-0012: キーが無くても起動できる)。
    `AsyncOpenAI` クライアントはキーと接続先(Base URL)の組でキャッシュし、画面でどちらかを
    変更したら(`/api/settings/openai-key` / `/api/settings/openai-base-url`。ADR-0017)
    次回実行時に作り直す。
    """

    name = "openai"
    label = "OpenAI"
    requires_api_key = True
    supports_pricing = True

    def __init__(self, client: AsyncOpenAI | None = None, *, moderation: str = "low") -> None:
        """`client` はテスト用、および将来 `AsyncAzureOpenAI` に差し替えるためのフック。

        省略した場合は実行のたびに設定(環境変数 / `.env` / 画面で保存したキー)から
        `AsyncOpenAI` を組み立てる。`moderation` は generate のときだけ `finalize_params` が
        params へ足す(ADR-0013)。画面で保存した値(`app_setting`)が無いときの既定値としてだけ
        使う(ADR-0009 2章)。
        """
        self._injected_client = client
        self._cached_client: AsyncOpenAI | None = None
        # (api_key, base_url) の組でキャッシュする(ADR-0017)。
        self._cached_key: tuple[str, str | None] | None = None
        self._moderation = moderation

    def capabilities(self) -> ProviderCapabilities:
        return openai_spec.build_capabilities(self.name, self.label)

    def availability(self) -> tuple[bool, str | None]:
        # API キーの有無は POST /api/runs 側で別途チェックする(ADR-0012)。
        # ここでは接続先そのものが使えるかどうかだけを見るため、常に利用可能とする。
        return True, None

    def finalize_params(self, db: Session, draft: RunDraft) -> dict[str, Any]:
        params = dict(draft.params)
        if draft.operation == "generate":
            # 画面で保存した値を優先し、無ければコンストラクタで渡された既定値(env/組み込み)
            # を使う(再起動なしで次の Run から反映する。ADR-0009 2章)。
            saved = general_settings.get_saved_moderation(db)
            params["moderation"] = saved if saved is not None else self._moderation
        return params

    def repeat_seed(self, first_params: dict[str, Any], index: int) -> int | None:
        # OpenAI の画像 API に seed は無い(ADR-0042 2章)。
        return None

    def _resolve_client(self) -> AsyncOpenAI:
        """キーと接続先(Base URL)を解決してクライアントを返す。

        キーが無ければ `missingApiKey` で失敗させる。Base URL が未設定なら OpenAI 本体を
        使う(`AsyncOpenAI` の既定。ADR-0017)。
        """
        if self._injected_client is not None:
            return self._injected_client

        settings = get_settings()
        api_key, _source = resolve_key(settings)
        if not api_key:
            raise ProviderError(
                code="missingApiKey",
                message=t("openai.missingApiKey"),
            )

        base_url, _url_source = resolve_base_url(settings)

        cache_key = (api_key, base_url)
        if self._cached_client is None or self._cached_key != cache_key:
            self._cached_client = AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                max_retries=settings.openai_max_retries,
                timeout=settings.openai_timeout_seconds,
            )
            self._cached_key = cache_key
        return self._cached_client

    async def execute(self, run: RunRequest, on_progress: ProgressCallback) -> RunResult:
        client = self._resolve_client()

        params = dict(run.params)
        # partial_images が params に無い/0 のときは非ストリーミング。
        # 1以上のときだけ stream=True を付ける(stream 自体は params に保存されない運用)。
        partial_images = int(params.get("partial_images") or 0)
        stream = partial_images >= 1

        try:
            if run.operation == "generate":
                return await self._execute_generate(client, run, params, stream, on_progress)
            return await self._execute_edit(client, run, params, stream, on_progress)
        except ProviderError:
            raise
        except openai.APIError as e:
            raise _convert_error(e) from e

    # -- generate ---------------------------------------------------------

    async def _execute_generate(
        self,
        client: AsyncOpenAI,
        run: RunRequest,
        params: dict[str, Any],
        stream: bool,
        on_progress: ProgressCallback,
    ) -> RunResult:
        kwargs: dict[str, Any] = dict(params)
        kwargs["model"] = run.model
        kwargs["prompt"] = run.prompt

        if not stream:
            response = await client.images.generate(**kwargs)
            return _result_from_response(response)

        kwargs["stream"] = True
        response_stream = await client.images.generate(**kwargs)
        return await self._consume_stream(response_stream, "image_generation", on_progress)

    # -- edit ---------------------------------------------------------------

    async def _execute_edit(
        self,
        client: AsyncOpenAI,
        run: RunRequest,
        params: dict[str, Any],
        stream: bool,
        on_progress: ProgressCallback,
    ) -> RunResult:
        kwargs: dict[str, Any] = dict(params)
        kwargs["model"] = run.model
        kwargs["prompt"] = run.prompt

        images = sorted(
            (item for item in run.inputs if item.role == "image"), key=lambda item: item.position
        )
        if not images:
            raise ProviderError(
                code="providerError",
                message=t("openai.editRequiresImageInput"),
            )
        kwargs["image"] = [
            _to_file_tuple(item, index, "image") for index, item in enumerate(images)
        ]

        masks = sorted(
            (item for item in run.inputs if item.role == "mask"), key=lambda item: item.position
        )
        if masks:
            # API はマスクを1枚だけ受け取る(複数あれば最初の image に適用される仕様)。
            kwargs["mask"] = _to_file_tuple(masks[0], 0, "mask")

        if not stream:
            response = await client.images.edit(**kwargs)
            return _result_from_response(response)

        kwargs["stream"] = True
        response_stream = await client.images.edit(**kwargs)
        return await self._consume_stream(response_stream, "image_edit", on_progress)

    # -- ストリーミング共通 ---------------------------------------------------

    async def _consume_stream(
        self,
        stream: Any,
        prefix: str,
        on_progress: ProgressCallback,
    ) -> RunResult:
        """`image_generation.*` / `image_edit.*` のイベントを消費する。

        n > 1 のときにイベントがどう来るか(completed が複数回来るか、output_index に相当する
        情報が別に来るか等)は実機未確認(ADR-0008 Action Item 1)。ここでは completed が複数回
        来ても取りこぼさないよう、来た順に outputs へ積む実装にしている。partial イベントの
        output_index も同様に「これまでに完了した枚数」を暫定値として使っている。
        """
        partial_type = f"{prefix}.partial_image"
        completed_type = f"{prefix}.completed"

        outputs: list[RunOutputImage] = []
        usage: dict[str, Any] | None = None
        request_id: str | None = None

        async for event in stream:
            if event.type == partial_type:
                await on_progress(
                    PartialImageEvent(
                        output_index=len(outputs),
                        partial_index=event.partial_image_index,
                        data=base64.b64decode(event.b64_json),
                        mime=_mime_from_output_format(event.output_format),
                    )
                )
            elif event.type == completed_type:
                outputs.append(
                    RunOutputImage(
                        data=base64.b64decode(event.b64_json),
                        mime=_mime_from_output_format(event.output_format),
                    )
                )
                usage = _usage_to_dict(event.usage)
                # ストリーミングイベントには `_request_id` が付かない(実測済み)ため None のまま。
                request_id = getattr(event, "_request_id", None) or request_id

        _ensure_outputs(outputs, request_id)
        return RunResult(outputs=outputs, usage=usage, provider_request_id=request_id)


def _to_file_tuple(item: InputImage, index: int, prefix: str) -> tuple[str, bytes, str]:
    ext = _MIME_TO_EXT.get(item.mime, "png")
    return (f"{prefix}{index}.{ext}", item.data, item.mime)


def _mime_from_output_format(output_format: str | None) -> str:
    return _OUTPUT_FORMAT_TO_MIME.get(output_format or "png", "image/png")


def _usage_to_dict(usage: Any) -> dict[str, Any] | None:
    if usage is None:
        return None
    return usage.model_dump()


def _result_from_response(response: ImagesResponse) -> RunResult:
    outputs: list[RunOutputImage] = []
    mime = _mime_from_output_format(response.output_format)
    for image in response.data or []:
        if image.b64_json is None:
            raise ProviderError(
                code="providerError",
                message=t("openai.missingImageData"),
            )
        outputs.append(RunOutputImage(data=base64.b64decode(image.b64_json), mime=mime))

    usage = _usage_to_dict(response.usage)
    request_id = getattr(response, "_request_id", None)
    _ensure_outputs(outputs, request_id)
    return RunResult(outputs=outputs, usage=usage, provider_request_id=request_id)


def _ensure_outputs(outputs: list[RunOutputImage], request_id: str | None) -> None:
    """出力0枚を成功として記録しない(空の data や、completed なしで終わったストリーム)。"""
    if not outputs:
        raise ProviderError(
            code="providerError",
            message=t("openai.noImagesReturned"),
            request_id=request_id,
        )


def _convert_error(e: openai.APIError) -> ProviderError:
    """openai の例外を `ProviderError` に変換する。メッセージに API キーを含めない。"""
    request_id = getattr(e, "request_id", None)

    if isinstance(e, openai.RateLimitError):
        return ProviderError(
            code="rateLimited",
            message=t("openai.rateLimited"),
            request_id=request_id,
        )
    if isinstance(e, openai.AuthenticationError):
        return ProviderError(
            code="authError",
            message=t("openai.authFailed"),
            request_id=request_id,
        )
    if isinstance(e, (openai.APITimeoutError, openai.APIConnectionError)):
        return ProviderError(
            code="connectionError",
            message=t("openai.connectionFailed"),
            request_id=request_id,
        )
    if _is_content_filter(e):
        return ProviderError(
            code="contentFilter",
            message=t("openai.contentFilterRejected"),
            request_id=request_id,
        )
    return ProviderError(code="providerError", message=_safe_message(e), request_id=request_id)


def _is_content_filter(e: openai.APIError) -> bool:
    # 400 (BadRequestError) と、ストリーミング中に SSE の `error` イベントとして届く素の
    # APIError(APIStatusError を継承しない)だけを対象にする。403/404/409/422/5xx のような
    # 他の HTTP ステータスエラーはコンテンツ拒否として扱わない。
    if not isinstance(e, openai.BadRequestError) and isinstance(e, openai.APIStatusError):
        return False

    code = (e.code or "").lower()
    if code in _CONTENT_FILTER_CODES:
        return True

    haystack = " ".join(filter(None, [e.type, getattr(e, "message", None)])).lower()
    return any(hint in haystack for hint in _CONTENT_FILTER_HINTS)


def _safe_message(e: openai.APIError) -> str:
    label = e.code or e.type or e.__class__.__name__
    detail = getattr(e, "message", None) or str(e)
    return t("openai.apiError", label=label, detail=detail)
