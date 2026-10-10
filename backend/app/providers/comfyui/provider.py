"""ADR-0013: ローカル ComfyUI を呼ぶ `ImageProvider` 実装。

登録済みワークフロー(`app/domain/comfy_workflow.py`、`comfy_workflow` テーブル)に
値を差し込んで実行するだけで、グラフの構造そのものは変えない。

`finalize_params` は DB を読む(ワークフローを引いて `run.params` を確定する)。
`execute` は `run.params["comfyui_prompt"]` をそのまま ComfyUI に送り、ワークフローや
Run の記録には触れない。これにより、実行後にワークフローを編集・削除しても過去の Run の
記録は変わらない(ADR-0013 4節)。ただし `execute` の開始時に一度だけ、画面で保存した
タイムアウト(`app_setting`。ADR-0013 7章)の上書きが無いか DB を引く。実行中の
WebSocket イベントのたびには引かない。
"""

from __future__ import annotations

import asyncio
import io
import itertools
import logging
import secrets
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack
from typing import Any

from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.domain import general_settings
from app.domain.comfy_workflow import (
    SEED_MAX,
    Bindings,
    ExposedParam,
    resolve_prompt,
    workflow_model_capabilities,
)
from app.domain.models import ComfyWorkflow
from app.domain.run_validation import RunInputMeta, RunValidationError
from app.domain.sql_compat import binary_order
from app.i18n import t
from app.providers.base import (
    InputImage,
    ModelCapabilities,
    PartialImageEvent,
    ProgressCallback,
    ProviderCapabilities,
    ProviderError,
    RunDraft,
    RunOutputImage,
    RunRequest,
    RunResult,
    StepProgressEvent,
)
from app.providers.comfyui.client import (
    ComfyUIClient,
    ComfyUIError,
    OutputImageRef,
    WsEvent,
    WsPreview,
    check_available,
    collect_output_images,
    collect_output_texts,
    sanitize_text,
)

logger = logging.getLogger(__name__)

_AVAILABILITY_CACHE_SECONDS = 5.0
_PREVIEW_MIN_INTERVAL_SECONDS = 0.5
_HISTORY_POLL_INTERVAL_SECONDS = 1.0
# 最終プロンプトの1件あたりの上限(ADR-0030 2章)。超えた分は切り詰めて `truncated` を付ける。
TEXT_OUTPUT_MAX_CHARS = 100_000

# `InputImage.mime` -> ComfyUI にアップロードするときの拡張子。
_EXT_FROM_MIME = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
_MIME_FROM_PILLOW_FORMAT = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}

ClientFactory = Callable[[], ComfyUIClient]
Clock = Callable[[], float]


class ComfyUIProvider:
    """`ImageProvider` プロトコルの ComfyUI 実装。"""

    name = "comfyui"
    label = "ComfyUI"
    requires_api_key = False
    supports_pricing = False

    def __init__(
        self,
        base_url: str,
        session_factory: sessionmaker,
        timeout_seconds: float,
        *,
        client_factory: ClientFactory | None = None,
        clock: Clock | None = None,
    ) -> None:
        """`client_factory` はテストで偽の ComfyUI(`tests/comfyui_fake.py`)を注入するため。

        `clock` はプレビューの間引き(0.5秒に1回まで)を決定的にテストするための注入点。
        省略時は `time.monotonic`。

        `timeout_seconds` は画面で一度も保存していないときに使う既定値(環境変数
        `COMFYUI_TIMEOUT_SECONDS`、無ければ組み込みの1800秒。呼び出し元が解決して渡す)。
        画面で保存した値があれば `execute` の開始時にそれを優先する(ADR-0013 7章)。
        """
        self._base_url = base_url
        self._session_factory = session_factory
        self._timeout_seconds = timeout_seconds
        self._client_factory = client_factory or (lambda: ComfyUIClient(base_url))
        self._clock = clock or time.monotonic
        self._availability_cache: tuple[float, tuple[bool, str | None]] | None = None

    # -- capabilities / availability -----------------------------------------

    def capabilities(self) -> ProviderCapabilities:
        with self._session_factory() as session:
            workflows = (
                session.execute(
                    select(ComfyWorkflow)
                    .where(ComfyWorkflow.deleted_at.is_(None))
                    # ADR-0027 2章: 名前の並びは両方の DB でバイト順にそろえる。
                    .order_by(binary_order(ComfyWorkflow.name), ComfyWorkflow.id)
                )
                .scalars()
                .all()
            )
            models: list[ModelCapabilities] = [workflow_model_capabilities(wf) for wf in workflows]

        return ProviderCapabilities(
            provider=self.name,
            label=self.label,
            models=models,
            default_model=models[0].model if models else "",
            default_size=None,
            size=None,
            max_input_images=1,
            n_min=1,
            n_max=1,
            partial_images_min=0,
            partial_images_max=0,
        )

    def availability(self) -> tuple[bool, str | None]:
        """`check_available` の結果を5秒キャッシュする。"""
        now = time.monotonic()
        if self._availability_cache is not None:
            cached_at, cached_result = self._availability_cache
            if now - cached_at < _AVAILABILITY_CACHE_SECONDS:
                return cached_result

        available, reason, _body = check_available(self._base_url, timeout=1.0)
        result = (available, reason)
        self._availability_cache = (now, result)
        return result

    # -- finalize_params -------------------------------------------------------

    def finalize_params(self, db: Session, draft: RunDraft) -> dict[str, Any]:
        unknown_workflow_message = t("comfyui.provider.unknownWorkflow", model=draft.model)
        try:
            workflow_id = uuid.UUID(draft.model)
        except ValueError as e:
            raise RunValidationError(unknown_workflow_message) from e

        wf = db.get(ComfyWorkflow, workflow_id)
        if wf is None or wf.deleted_at is not None:
            raise RunValidationError(unknown_workflow_message)
        if wf.operation != draft.operation:
            raise RunValidationError(
                t(
                    "comfyui.provider.workflowOperationMismatch",
                    name=wf.name,
                    operation=draft.operation,
                )
            )

        bindings = Bindings.model_validate(wf.bindings)
        exposed = [ExposedParam.model_validate(p) for p in wf.exposed_params]

        seed = draft.params.get("seed")
        if seed is None:
            seed = secrets.randbelow(SEED_MAX + 1)

        image_names, mask_upload_name, mask_resolve_name = self._resolve_upload_names(
            bindings, draft.inputs
        )

        graph = resolve_prompt(
            wf.template,
            bindings,
            exposed,
            prompt=draft.prompt,
            params=draft.params,
            seed=seed,
            image_names=image_names,
            mask_name=mask_resolve_name,
        )

        uploads: dict[str, Any] = {}
        if image_names:
            uploads["images"] = image_names
        if mask_upload_name is not None:
            uploads["mask"] = mask_upload_name

        result: dict[str, Any] = dict(draft.params)
        result["comfyui_workflow"] = {
            "id": str(wf.id),
            "name": wf.name,
            "template_sha256": wf.template_sha256,
        }
        result["comfyui_seed"] = seed
        result["comfyui_uploads"] = uploads
        result["comfyui_prompt"] = graph
        result["comfyui_outputs"] = list(bindings.outputs)
        if bindings.final_prompt is not None:
            # 実行時にワークフローのテーブルを読まないため、ノード id を params に残す
            # (ADR-0013 4章、ADR-0030 2章)。
            result["comfyui_final_prompt"] = bindings.final_prompt
        if bindings.mask is not None:
            result["comfyui_mask_mode"] = bindings.mask.mode
        return result

    def repeat_seed(self, first_params: dict[str, Any], index: int) -> int | None:
        # ComfyUI は1つの seed でバッチ全体を作るので、Run ごとに 1 ずつ進める(ADR-0042 2章)。
        return (int(first_params["comfyui_seed"]) + index) % (SEED_MAX + 1)

    @staticmethod
    def _resolve_upload_names(
        bindings: Bindings, inputs: list[RunInputMeta]
    ) -> tuple[list[str], str | None, str | None]:
        """(image_names, mask_upload_name, mask_resolve_name) を決める。

        `image_names` は枠(`bindings.images`)の順に、ComfyUI の各 LoadImage.image へ
        差し込む名前。`mask_upload_name` は別途アップロードするマスクの名前
        (load_image_mask のときだけ)。`mask_resolve_name` は `resolve_prompt` に渡す名前
        (同じく load_image_mask のときだけ。image_alpha は枠1の名前自体を合成画像の名前に
        することで表現するので None)。マスクは枠1(position=0)の画像に対するものだけ
        (ADR-0013 3節)。
        """
        if not bindings.images:
            return [], None, None

        image_metas = _find_input_metas(inputs, "image")
        image_names = [
            f"gakei_{meta.sha256}.{_EXT_FROM_MIME.get(meta.mime, 'png')}" for meta in image_metas
        ]

        if bindings.mask is None:
            return image_names, None, None

        mask_meta = _find_input_meta(inputs, "mask")
        if bindings.mask.mode == "image_alpha":
            image_names[0] = f"gakei_{image_metas[0].sha256}_{mask_meta.sha256}.png"
            return image_names, None, None

        mask_ext = _EXT_FROM_MIME.get(mask_meta.mime, "png")
        mask_name = f"gakei_{mask_meta.sha256}.{mask_ext}"
        return image_names, mask_name, mask_name

    # -- execute -----------------------------------------------------------

    async def execute(self, run: RunRequest, on_progress: ProgressCallback) -> RunResult:
        client = self._client_factory()
        try:
            return await self._execute(client, run, on_progress)
        finally:
            await client.aclose()

    def _effective_timeout_seconds(self) -> float:
        """画面で保存したタイムアウト(`app_setting`)があればそれを優先し、無ければ
        コンストラクタで渡された既定値(env/組み込み)を使う(ADR-0013 7章)。

        実行開始のたびに1回だけ DB を引く(WebSocket イベントのたびには引かない)。
        """
        with self._session_factory() as session:
            saved = general_settings.get_saved_timeout_seconds(session)
        return float(saved) if saved is not None else self._timeout_seconds

    async def _execute(
        self, client: ComfyUIClient, run: RunRequest, on_progress: ProgressCallback
    ) -> RunResult:
        params = run.params
        prompt_graph = params.get("comfyui_prompt")
        if not isinstance(prompt_graph, dict):
            raise ProviderError(
                "internalError",
                t("comfyui.provider.missingPromptInternal"),
            )
        output_node_ids = [str(n) for n in (params.get("comfyui_outputs") or [])]
        uploads: dict[str, Any] = dict(params.get("comfyui_uploads") or {})
        mask_mode = params.get("comfyui_mask_mode")

        client_id = str(run.run_id)
        started_at = time.monotonic()
        deadline = started_at + self._effective_timeout_seconds()
        prompt_id: str | None = None

        try:
            async with AsyncExitStack() as stack:
                events: AsyncIterator[WsEvent] | None
                try:
                    events = await stack.enter_async_context(client.ws_events(client_id))
                except ComfyUIError:
                    # 接続できなくても続行し、あとで /history のポーリングに切り替える。
                    events = None

                await self._upload_inputs(client, run.inputs, uploads, mask_mode)
                prompt_id = await client.queue_prompt(prompt_graph, client_id)

                completed_via_ws = False
                if events is not None:
                    try:
                        await self._consume_ws(events, prompt_id, on_progress, deadline)
                        completed_via_ws = True
                    except ComfyUIError:
                        completed_via_ws = False

            if completed_via_ws:
                entry = await client.history(prompt_id)
                if entry is None:
                    entry = await self._poll_until_done(client, prompt_id, deadline)
            else:
                entry = await self._poll_until_done(client, prompt_id, deadline)
        except ComfyUIError as e:
            raise ProviderError(e.code, e.message, request_id=prompt_id) from e

        status = entry.get("status") if isinstance(entry, dict) else None
        if isinstance(status, dict) and status.get("status_str") == "error":
            raise ProviderError(
                "executionError", _summarize_history_error(status), request_id=prompt_id
            )

        refs = collect_output_images(entry, output_node_ids)
        if not refs:
            raise ProviderError(
                "comfyuiNoOutput",
                t("comfyui.provider.noOutputImages"),
                request_id=prompt_id,
            )

        outputs = [await self._fetch_output(client, ref) for ref in refs]
        text_outputs = _collect_final_prompt(entry, params, prompt_graph, prompt_id)

        duration_ms = int((time.monotonic() - started_at) * 1000)
        usage = {"prompt_id": prompt_id, "duration_ms": duration_ms}
        return RunResult(
            outputs=outputs,
            usage=usage,
            provider_request_id=prompt_id,
            text_outputs=text_outputs,
        )

    @staticmethod
    async def _fetch_output(client: ComfyUIClient, ref: OutputImageRef) -> RunOutputImage:
        data = await client.view(ref)
        mime = await asyncio.to_thread(_detect_mime, data)
        return RunOutputImage(data=data, mime=mime)

    async def _upload_inputs(
        self,
        client: ComfyUIClient,
        inputs: list[InputImage],
        uploads: dict[str, Any],
        mask_mode: Any,
    ) -> None:
        """枠(position)の順にアップロードする。マスクは枠1(position=0)の画像に対する
        ものだけ(ADR-0013 3節)。`comfyui_uploads` に `images` が無い旧形式({"image": ...}
        のみ。移行前にキューされた Run)は `images=[uploads["image"]]` として扱う。
        """
        image_names = uploads.get("images")
        if image_names is None and "image" in uploads:
            image_names = [uploads["image"]]
        image_names = list(image_names or [])
        mask_name = uploads.get("mask")

        # 枠 n には position の小さい順で n 番目の画像を入れる(position が連番とは限らない)。
        image_data = [
            item.data
            for item in sorted(
                (item for item in inputs if item.role == "image"), key=lambda i: i.position
            )
        ]
        uploaded: set[str] = set()

        for index, name in enumerate(image_names):
            if name in uploaded:
                # 同じ画像が複数の枠に使われる場合、アップロードは一度でよい
                # (内容から決めた名前なので、先にアップロードした内容がそのまま使われる)。
                continue
            uploaded.add(name)
            if mask_mode == "image_alpha" and index == 0:
                mask_bytes = _find_input_data(inputs, "mask")
                data = await asyncio.to_thread(_composite_alpha_png, image_data[0], mask_bytes)
            else:
                data = image_data[index]
            await client.upload_image(data, name, overwrite=True)

        if mask_name is not None:
            await client.upload_image(_find_input_data(inputs, "mask"), mask_name, overwrite=True)

    async def _consume_ws(
        self,
        events: AsyncIterator[WsEvent],
        prompt_id: str,
        on_progress: ProgressCallback,
        deadline: float,
    ) -> None:
        """自分の `prompt_id` のイベントだけ扱う。ストリームが終わった(切断)ときは
        `ComfyUIError` を送出し、呼び出し側が /history のポーリングに切り替える。
        """
        own_execution_active = False
        last_preview_at: float | None = None
        partial_counter = itertools.count()

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProviderError(
                    "timeout",
                    t("comfyui.provider.timeoutExecuting"),
                    request_id=prompt_id,
                )
            try:
                event = await asyncio.wait_for(events.__anext__(), timeout=remaining)
            except TimeoutError as exc:
                raise ProviderError(
                    "timeout",
                    t("comfyui.provider.timeoutExecuting"),
                    request_id=prompt_id,
                ) from exc
            except StopAsyncIteration:
                break

            if isinstance(event, WsPreview):
                if not own_execution_active:
                    continue
                now = self._clock()
                if (
                    last_preview_at is not None
                    and now - last_preview_at < _PREVIEW_MIN_INTERVAL_SECONDS
                ):
                    continue
                last_preview_at = now
                png_bytes = await asyncio.to_thread(_to_png_bytes, event.data)
                await on_progress(
                    PartialImageEvent(
                        output_index=0,
                        partial_index=next(partial_counter),
                        data=png_bytes,
                        mime="image/png",
                    )
                )
                continue

            data = event.data or {}

            if event.type == "progress":
                if data.get("prompt_id") != prompt_id:
                    continue
                value, max_value, node = data.get("value"), data.get("max"), data.get("node")
                if isinstance(value, int) and isinstance(max_value, int):
                    await on_progress(StepProgressEvent(value=value, max=max_value, node=node))
                continue

            if event.type == "executing":
                if data.get("prompt_id") != prompt_id:
                    continue
                if data.get("node") is None:
                    return  # 完了
                own_execution_active = True
                continue

            if event.type == "execution_success":
                if data.get("prompt_id") == prompt_id:
                    return
                continue

            if event.type == "execution_error":
                if data.get("prompt_id") != prompt_id:
                    continue
                raise ProviderError(
                    "executionError", _summarize_execution_error(data), request_id=prompt_id
                )

            if event.type == "execution_interrupted":
                if data.get("prompt_id") != prompt_id:
                    continue
                raise ProviderError(
                    "executionError",
                    t("comfyui.provider.executionInterrupted"),
                    request_id=prompt_id,
                )
            # 他のイベント種別(status 等)は無視する。

        # ループを抜けた(ストリームが終わった) = 切断。呼び出し側でポーリングに切り替える。
        raise ComfyUIError(
            "comfyuiUnavailable",
            t("comfyui.provider.wsEnded"),
        )

    async def _poll_until_done(
        self, client: ComfyUIClient, prompt_id: str, deadline: float
    ) -> dict[str, Any]:
        while True:
            entry = await client.history(prompt_id)
            if entry is not None:
                return entry
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProviderError(
                    "timeout",
                    t("comfyui.provider.timeoutExecuting"),
                    request_id=prompt_id,
                )
            await asyncio.sleep(min(_HISTORY_POLL_INTERVAL_SECONDS, remaining))


def _collect_final_prompt(
    entry: dict[str, Any],
    params: dict[str, Any],
    prompt_graph: dict[str, Any],
    prompt_id: str,
) -> list[dict[str, Any]] | None:
    """最終プロンプト(`params["comfyui_final_prompt"]` のノード)の `text` を記録の形にする
    (ADR-0030 2章)。ノード id・class_type・title は送ったグラフから取る。ノードを登録して
    いない、または `text` が無いときは None(後者はログに警告を出す。Run は成功のまま)。
    """
    node_id = params.get("comfyui_final_prompt")
    if node_id is None:
        return None
    node_id = str(node_id)
    text = collect_output_texts(entry, node_id)
    if text is None:
        logger.warning(
            "ComfyUI の最終プロンプトのノード %s に text がありません(prompt_id=%s)",
            node_id,
            prompt_id,
        )
        return None
    node = prompt_graph.get(node_id)
    node = node if isinstance(node, dict) else {}
    class_type = node.get("class_type")
    meta = node.get("_meta")
    title = meta.get("title") if isinstance(meta, dict) else None
    item: dict[str, Any] = {
        "role": "final_prompt",
        "node_id": node_id,
        "class_type": sanitize_text(class_type) if isinstance(class_type, str) else None,
        "title": sanitize_text(title) if isinstance(title, str) else None,
        "text": text,
    }
    if len(text) > TEXT_OUTPUT_MAX_CHARS:
        item["text"] = text[:TEXT_OUTPUT_MAX_CHARS]
        item["truncated"] = True
    return [item]


def _find_input_meta(inputs: list[RunInputMeta], role: str) -> RunInputMeta:
    for item in inputs:
        if item.role == role:
            return item
    raise RunValidationError(t("comfyui.provider.roleInputNotFound", role=role))


def _find_input_metas(inputs: list[RunInputMeta], role: str) -> list[RunInputMeta]:
    """`role` に一致する入力を position 順(=枠の順)に並べて返す。"""
    return sorted((item for item in inputs if item.role == role), key=lambda item: item.position)


def _find_input_data(inputs: list[InputImage], role: str) -> bytes:
    for item in inputs:
        if item.role == role:
            return item.data
    raise ProviderError(
        "internalError",
        t("comfyui.provider.roleImageInputNotFound", role=role),
    )


def _composite_alpha_png(image_bytes: bytes, mask_bytes: bytes) -> bytes:
    """入力画像の RGB とマスクの alpha を合成した RGBA PNG を作る(Asset の原本は変えない)。"""
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    mask = Image.open(io.BytesIO(mask_bytes)).convert("RGBA")
    alpha = mask.split()[-1]
    if alpha.size != image.size:
        alpha = alpha.resize(image.size)
    composite = image.convert("RGBA")
    composite.putalpha(alpha)
    buffer = io.BytesIO()
    composite.save(buffer, format="PNG")
    return buffer.getvalue()


def _to_png_bytes(data: bytes) -> bytes:
    image = Image.open(io.BytesIO(data))
    image.load()
    buffer = io.BytesIO()
    image.convert("RGBA" if image.mode in ("RGBA", "LA", "P") else "RGB").save(buffer, format="PNG")
    return buffer.getvalue()


def _detect_mime(data: bytes) -> str:
    image = Image.open(io.BytesIO(data))
    return _MIME_FROM_PILLOW_FORMAT.get(image.format or "", "image/png")


def _summarize_execution_error(data: dict[str, Any]) -> str:
    node_id = data.get("node_id", "?")
    exception_type = data.get("exception_type") or ""
    exception_message = data.get("exception_message") or ""
    parts = [p for p in (exception_type, exception_message) if p]
    detail = ": ".join(parts) if parts else t("comfyui.provider.noDetails")
    return t("comfyui.provider.nodeExecutionFailed", node=node_id, detail=detail)


def _summarize_history_error(status: dict[str, Any]) -> str:
    messages = status.get("messages")
    if isinstance(messages, list):
        for item in messages:
            if isinstance(item, list) and len(item) == 2 and item[0] == "execution_error":
                detail = item[1]
                if isinstance(detail, dict):
                    return _summarize_execution_error(detail)
    return t("comfyui.provider.historyErrorNoDetails")
