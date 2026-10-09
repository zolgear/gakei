"""ADR-0038: Stable Diffusion WebUI(A1111 互換の API)を呼ぶ `ImageProvider` 実装。

チェックポイントをモデルとして見せ、パラメーターは GAKEI が固定で定義する(ADR-0038 2章)。
今は Generate(`txt2img`)だけ。Edit(`img2img`)は後の段階で足す。

拡張機能(`alwayson_scripts`)は `extensions.py` のアダプターで対応を決める(ADR-0038 7章)。
対応する拡張機能が接続先にあれば、利用者が値を変えなくても毎回引数の全体を送って記録する。

`finalize_params` が WebUI に送る本文を確定して `run.params["sdwebui_request"]` に保存し
(ADR-0003 ルール4、ADR-0038 3章)、`execute` はそれをそのまま送る。`execute` の開始時に
一度だけ、画面で保存したタイムアウト(`app_setting`)を DB から引く。
"""

from __future__ import annotations

import asyncio
import copy
import io
import itertools
import logging
import secrets
import time
import uuid
from collections.abc import Callable
from typing import Any

from PIL import Image
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.domain import general_settings, sizes
from app.domain.run_validation import RunValidationError
from app.i18n import t
from app.providers.base import (
    ModelCapabilities,
    OperationCapabilities,
    ParamDef,
    PartialImageEvent,
    ProgressCallback,
    ProviderCapabilities,
    ProviderError,
    ProviderUnavailableError,
    RunDraft,
    RunOutputImage,
    RunRequest,
    RunResult,
    SizeConstraints,
    StepProgressEvent,
)
from app.providers.sdwebui.client import (
    Catalog,
    Credentials,
    SdWebuiClient,
    SdWebuiError,
    decode_base64_image,
    parse_info,
)
from app.providers.sdwebui.extensions import (
    all_param_names,
    build_alwayson_scripts,
    output_limit,
    resolve_extensions,
)

logger = logging.getLogger(__name__)

PROVIDER_NAME = "sdwebui"

# WebUI の seed の範囲(ADR-0038 3章: 2^32 未満。-1 は送らない)
SEED_MAX = 2**32 - 1
VAE_BUILTIN = "builtin"
DEFAULT_SIZE = "1024x1024"
N_MAX = 8

_AVAILABILITY_CACHE_SECONDS = 5.0
_CATALOG_CACHE_SECONDS = 60.0
# 一覧を取れなかったときは短い間だけ覚えておく(capabilities のたびに待たないため)
_CATALOG_FAILURE_CACHE_SECONDS = 5.0
_PROGRESS_INTERVAL_SECONDS = 1.0
_PREVIEW_MIN_INTERVAL_SECONDS = 0.5
# 進捗の value / max(`/internal/progress` は割合しか返さないので百分率にする)
_PROGRESS_MAX = 100

# サンプラー・スケジューラーの初期値の候補(接続先の一覧にあるときだけ使う)
_PREFERRED_SAMPLERS = ("Euler a", "Euler")
_PREFERRED_SCHEDULERS = ("automatic", "Automatic")

# 出力ごとのプロンプト(展開後)の1件あたりの上限(ADR-0030 2章の最終プロンプトと同じ)
TEXT_OUTPUT_MAX_CHARS = 100_000

_MIME_FROM_PILLOW_FORMAT = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}

SIZE_CONSTRAINTS = SizeConstraints(
    multiple_of=8,
    max_long_edge=2048,
    min_total_pixels=256 * 256,
    max_total_pixels=2048 * 2048,
    min_aspect_ratio=1 / 4,
    max_aspect_ratio=4,
    allow_auto=False,
)

CredentialsLoader = Callable[[], Credentials | None]
Clock = Callable[[], float]


class SdWebuiProvider:
    """`ImageProvider` プロトコルの SD WebUI 実装。"""

    name = PROVIDER_NAME
    label = "SD WebUI"
    requires_api_key = False
    supports_pricing = False

    def __init__(
        self,
        base_url: str,
        session_factory: sessionmaker,
        timeout_seconds: float,
        *,
        credentials_loader: CredentialsLoader | None = None,
        transport: Any = None,
        clock: Clock | None = None,
        progress_interval: float = _PROGRESS_INTERVAL_SECONDS,
    ) -> None:
        """`credentials_loader` は Basic 認証の資格情報を毎回読む関数(画面で変えたら次の
        呼び出しから使う)。`transport` はテストで偽の WebUI を注入するため。

        `timeout_seconds` は画面で一度も保存していないときの既定値(`SDWEBUI_TIMEOUT_SECONDS`、
        無ければ 600 秒)。画面で保存した値があれば `execute` の開始時にそれを優先する。
        """
        self._base_url = base_url.rstrip("/")
        self._session_factory = session_factory
        self._timeout_seconds = timeout_seconds
        self._credentials_loader = credentials_loader or (lambda: None)
        self._transport = transport
        self._clock = clock or time.monotonic
        self._progress_interval = progress_interval
        self._availability_cache: tuple[float, tuple[bool, str | None]] | None = None
        self._catalog_cache: tuple[float, Catalog | None] | None = None

    @classmethod
    def from_settings(
        cls, base_url: str, settings: Settings, session_factory: sessionmaker
    ) -> SdWebuiProvider:
        from app.domain.sdwebui_connection import read_credentials

        data_dir = settings.data_dir
        return cls(
            base_url,
            session_factory,
            settings.sdwebui_timeout_seconds,
            credentials_loader=lambda: read_credentials(data_dir),
        )

    @property
    def base_url(self) -> str:
        return self._base_url

    def client(self) -> SdWebuiClient:
        return SdWebuiClient(
            self._base_url, credentials=self._credentials_loader(), transport=self._transport
        )

    def invalidate_cache(self) -> None:
        """一覧と到達性のキャッシュを捨てる(`POST /api/sdwebui/refresh`、資格情報の変更)。"""
        self._availability_cache = None
        self._catalog_cache = None

    # -- availability / 一覧 ---------------------------------------------------------

    def availability(self) -> tuple[bool, str | None]:
        """`GET /sdapi/v1/options` の結果を5秒キャッシュする。"""
        now = time.monotonic()
        if self._availability_cache is not None:
            cached_at, cached = self._availability_cache
            if now - cached_at < _AVAILABILITY_CACHE_SECONDS:
                return cached
        result = self.client().check_available(timeout=1.0)
        value = (result.available, None if result.available else result.message)
        self._availability_cache = (now, value)
        return value

    def catalog(self) -> Catalog | None:
        """チェックポイントなどの一覧(60秒キャッシュ)。接続できなければ None。"""
        now = time.monotonic()
        if self._catalog_cache is not None:
            cached_at, cached = self._catalog_cache
            ttl = _CATALOG_CACHE_SECONDS if cached is not None else _CATALOG_FAILURE_CACHE_SECONDS
            if now - cached_at < ttl:
                return cached
        try:
            catalog: Catalog | None = self.client().fetch_catalog()
        except SdWebuiError as exc:
            logger.info("SD WebUI の一覧を取得できませんでした(%s)", exc.code)
            catalog = None
        self._catalog_cache = (now, catalog)
        return catalog

    # -- capabilities -------------------------------------------------------------------

    def capabilities(self) -> ProviderCapabilities:
        catalog = self.catalog()
        models: list[ModelCapabilities] = []
        if catalog is not None:
            params = _generate_params(catalog)
            for extension in resolve_extensions(catalog.scripts):
                params.extend(extension.adapter.param_defs())
            for checkpoint in catalog.checkpoints:
                models.append(
                    ModelCapabilities(
                        model=checkpoint.model_name,
                        label=checkpoint.model_name,
                        quality_choices=[],
                        operations=[
                            OperationCapabilities(
                                operation="generate",
                                params=params,
                                max_input_images=0,
                                min_input_images=0,
                                supports_mask=False,
                            )
                        ],
                    )
                )
        return ProviderCapabilities(
            provider=self.name,
            label=self.label,
            models=models,
            default_model=models[0].model if models else "",
            default_size=DEFAULT_SIZE,
            size=SIZE_CONSTRAINTS,
            prompt_max_length=sizes.PROMPT_MAX_LENGTH,
            n_min=1,
            n_max=N_MAX,
            partial_images_min=0,
            partial_images_max=0,
            max_input_images=0,
        )

    # -- finalize_params ---------------------------------------------------------------

    def finalize_params(self, db: Session, draft: RunDraft) -> dict[str, Any]:
        catalog = self.catalog()
        if catalog is None:
            raise ProviderUnavailableError(t("sdwebui.provider.unavailable"))
        if draft.model not in {c.model_name for c in catalog.checkpoints}:
            raise RunValidationError(t("sdwebui.provider.unknownCheckpoint", model=draft.model))
        if draft.operation != "generate":
            raise RunValidationError(
                t(
                    "runValidation.operationNotSupported",
                    model=draft.model,
                    operation=draft.operation,
                )
            )

        params = draft.params
        for key in params:
            # capabilities に無いので検証で落ちるはずだが、念のためここでも断る(ADR-0038 3章)。
            if key.startswith("sdwebui_"):
                raise RunValidationError(t("runValidation.unknownParam", key=key))

        extensions = resolve_extensions(catalog.scripts)
        # 接続先に無い(または対応外の版の)拡張機能の項目は断る(capabilities に出していない)。
        available_ext_params = {name for ext in extensions for name in ext.adapter.param_names()}
        for key in params:
            if key in all_param_names() and key not in available_ext_params:
                raise RunValidationError(t("runValidation.unknownParam", key=key))

        seed = params.get("seed")
        if seed is None:
            seed = secrets.randbelow(SEED_MAX + 1)

        size_text = str(params.get("size") or DEFAULT_SIZE)
        parsed = sizes.parse_size(size_text, SIZE_CONSTRAINTS)
        if parsed is None:
            raise RunValidationError(t("sizes.autoNotAllowed"))
        width, height = parsed

        vae = params.get("vae") or VAE_BUILTIN
        if vae != VAE_BUILTIN and vae not in catalog.vaes:
            raise RunValidationError(
                t("runValidation.enumInvalid", name="vae", choices=catalog.vaes)
            )

        task_id = f"gakei-{uuid.uuid4().hex}"
        override_settings: dict[str, Any] = {"sd_model_checkpoint": draft.model}
        if catalog.flavor == "forge":
            # Forge: ファイル名だけで通る。[] はチェックポイント内蔵の VAE。
            override_settings["forge_additional_modules"] = [] if vae == VAE_BUILTIN else [vae]
        else:
            override_settings["sd_vae"] = "None" if vae == VAE_BUILTIN else vae

        request: dict[str, Any] = {
            "prompt": draft.prompt,
            "negative_prompt": params.get("negative_prompt", ""),
            "seed": seed,
            "steps": params.get("steps", 20),
            "cfg_scale": params.get("cfg_scale", 7.0),
            "width": width,
            "height": height,
            "batch_size": params.get("n", 1),
            "n_iter": 1,
        }
        if "sampler_name" in params:
            request["sampler_name"] = params["sampler_name"]
        if "scheduler" in params:
            request["scheduler"] = params["scheduler"]
        request.update(
            {
                "save_images": False,
                "send_images": True,
                "force_task_id": task_id,
                "override_settings": override_settings,
                "override_settings_restore_afterwards": True,
            }
        )
        if extensions:
            # WebUI の画面の既定値が後で変わっても記録と実際が食い違わないよう、毎回すべての
            # 引数を明示して送る(ADR-0038 7章)。
            request["alwayson_scripts"] = build_alwayson_scripts(extensions, params)

        result: dict[str, Any] = dict(params)
        result["sdwebui_seed"] = seed
        result["sdwebui_task_id"] = task_id
        result["sdwebui_request"] = request
        return result

    # -- execute ---------------------------------------------------------------------

    def _effective_timeout_seconds(self) -> float:
        with self._session_factory() as session:
            saved = general_settings.get_saved_sdwebui_timeout_seconds(session)
        return float(saved) if saved is not None else self._timeout_seconds

    async def execute(self, run: RunRequest, on_progress: ProgressCallback) -> RunResult:
        params = run.params
        request = params.get("sdwebui_request")
        if not isinstance(request, dict):
            raise ProviderError("internalError", t("sdwebui.provider.missingRequestInternal"))
        if run.operation != "generate":
            raise ProviderError(
                "internalError",
                t("runValidation.operationNotSupported", model=run.model, operation=run.operation),
            )
        body = copy.deepcopy(request)
        task_id = str(params.get("sdwebui_task_id") or body.get("force_task_id") or "")
        batch_size = body.get("batch_size") if isinstance(body.get("batch_size"), int) else 1

        timeout = await asyncio.to_thread(self._effective_timeout_seconds)
        client = self.client()
        started_at = time.monotonic()

        async with client.async_client(timeout=30.0) as http:
            watcher = asyncio.create_task(self._watch_progress(client, http, task_id, on_progress))
            try:
                # 全体の締め切りは画面設定のタイムアウト。httpx の読み取りの待ちはそれより
                # 少し長くし、締め切りは wait_for で決める。
                response = await asyncio.wait_for(
                    client.txt2img(http, body, timeout=timeout + 5.0), timeout=timeout
                )
            except TimeoutError as exc:
                raise ProviderError(
                    "timeout", t("sdwebui.provider.timeoutExecuting"), request_id=task_id
                ) from exc
            except SdWebuiError as exc:
                raise ProviderError(exc.code, exc.message, request_id=task_id) from exc
            finally:
                watcher.cancel()
                await asyncio.gather(watcher, return_exceptions=True)

        info = parse_info(response.get("info"))
        actual_model = info.get("sd_model_name")
        if isinstance(actual_model, str) and actual_model != run.model:
            # WebUI は知らない名前を黙って無視し、今のチェックポイントで描く(ADR-0038 2章)。
            raise ProviderError(
                "sdwebuiModelMismatch",
                t("sdwebui.provider.modelMismatch", expected=run.model, actual=actual_model),
                request_id=task_id,
            )

        images = response.get("images")
        images = images if isinstance(images, list) else []
        # グリッドを先頭に足したときは `index_of_first_image` が 1 になる(A1111 / Forge)。
        first = info.get("index_of_first_image")
        first = first if isinstance(first, int) and 0 <= first < len(images) else 0
        all_prompts = _str_list(info.get("all_prompts"))
        limit = output_limit(body.get("alwayson_scripts"), params)
        if limit is None:
            # 補助の画像(グリッドなど)は batch_size を超える分を捨てる(ADR-0038 4章)。
            expected = max(batch_size, 1)
        else:
            # 組み合わせ生成では、枚数は batch_size ではなく組み合わせの数で決まる(7章)。
            # 何枚が本物かは all_prompts の数で分かる。分からなければ返った分すべて。
            expected = len(all_prompts) if all_prompts else len(images) - first
        candidates = images[first : first + expected]
        discarded = 0
        if limit is not None and len(candidates) > limit:
            discarded = len(candidates) - limit
            candidates = candidates[:limit]
        decoded = [
            data for data in (decode_base64_image(item) for item in candidates) if data is not None
        ]
        if not decoded:
            raise ProviderError(
                "sdwebuiNoOutput", t("sdwebui.provider.noOutputImages"), request_id=task_id
            )
        try:
            outputs = [
                RunOutputImage(data=data, mime=await asyncio.to_thread(_detect_mime, data))
                for data in decoded
            ]
        except Exception as exc:  # noqa: BLE001 - 画像として読めないものは出力なしと同じに扱う
            raise ProviderError(
                "sdwebuiNoOutput", t("sdwebui.provider.unreadableOutput"), request_id=task_id
            ) from exc

        usage: dict[str, Any] = {
            "task_id": task_id,
            "duration_ms": int((time.monotonic() - started_at) * 1000),
        }
        all_seeds = info.get("all_seeds")
        if isinstance(all_seeds, list):
            usage["all_seeds"] = [s for s in all_seeds if isinstance(s, int)][: len(outputs)]
        infotexts = info.get("infotexts")
        infotext = (
            infotexts[first]
            if isinstance(infotexts, list) and len(infotexts) > first
            else info.get("infotext")
        )
        if isinstance(infotext, str):
            usage["infotext"] = infotext.replace("\x00", "")
        if limit is not None:
            usage["output_limit"] = limit
        if discarded:
            usage["discarded_outputs"] = discarded
        # 1枚ずつの展開後のプロンプト(Dynamic Prompts など。ADR-0038 7章)。出力の順。
        all_negative_prompts = _str_list(info.get("all_negative_prompts"))
        if all_prompts:
            usage["all_prompts"] = all_prompts[: len(outputs)]
        if all_negative_prompts:
            usage["all_negative_prompts"] = all_negative_prompts[: len(outputs)]
        text_outputs = _per_output_prompts(
            usage.get("all_prompts"), usage.get("all_negative_prompts")
        )
        return RunResult(
            outputs=outputs,
            usage=usage,
            provider_request_id=task_id or None,
            text_outputs=text_outputs,
        )

    async def _watch_progress(
        self,
        client: SdWebuiClient,
        http: Any,
        task_id: str,
        on_progress: ProgressCallback,
    ) -> None:
        """1秒ごとに進み具合を問い合わせて流す。進捗は表示のためだけで、失敗しても実行は
        止めない(ADR-0038 4章)。`/internal/progress` が無ければ `/sdapi/v1/progress`。"""
        use_internal = True
        id_live_preview = -1
        last_preview_at: float | None = None
        partial_counter = itertools.count()
        last_value: int | None = None

        while True:
            await asyncio.sleep(self._progress_interval)
            try:
                snapshot = None
                if use_internal:
                    snapshot = await client.internal_progress(http, task_id, id_live_preview)
                    if snapshot is None:
                        use_internal = False
                if snapshot is None:
                    snapshot = await client.public_progress(http)
            except SdWebuiError:
                continue
            except Exception:  # noqa: BLE001 - 進捗の失敗で実行を止めない
                logger.debug("SD WebUI の進捗の取得に失敗しました", exc_info=True)
                continue

            if snapshot.queued and not snapshot.active:
                # WebUI の中で順番を待っている。まだ何も流さない。
                continue

            if snapshot.step is not None and snapshot.steps:
                value, max_value = snapshot.step, snapshot.steps
            elif snapshot.progress is not None:
                value = max(0, min(_PROGRESS_MAX, round(snapshot.progress * _PROGRESS_MAX)))
                max_value = _PROGRESS_MAX
            else:
                value = None
                max_value = _PROGRESS_MAX
            if value is not None and value != last_value:
                last_value = value
                await on_progress(StepProgressEvent(value=value, max=max_value))

            if snapshot.id_live_preview is not None:
                id_live_preview = snapshot.id_live_preview
            if snapshot.live_preview is not None:
                now = self._clock()
                if last_preview_at is not None and now - last_preview_at < (
                    _PREVIEW_MIN_INTERVAL_SECONDS
                ):
                    continue
                try:
                    png = await asyncio.to_thread(_to_png_bytes, snapshot.live_preview)
                except Exception:  # noqa: BLE001 - 読めない途中経過は捨てる
                    continue
                last_preview_at = now
                await on_progress(
                    PartialImageEvent(
                        output_index=0,
                        partial_index=next(partial_counter),
                        data=png,
                        mime="image/png",
                    )
                )


def _str_list(value: Any) -> list[str]:
    """文字列のリスト。形が崩れていれば(位置がずれるので)空にする。"""
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value)
    return []


def _per_output_prompts(
    prompts: list[str] | None, negative_prompts: list[str] | None
) -> list[dict[str, Any]] | None:
    """出力ごとの展開後のプロンプトを `run.text_outputs` の形にする(ADR-0030 と同じ列)。

    `role` は `final_prompt`(プロンプト)と `final_negative_prompt`(ネガティブプロンプト)。
    `output_index` で出力の Asset に対応させる。元のプロンプトと同じかどうかは記録の側では
    判断しない(表示する側が比べる)。空のものは記録しない(ADR-0030 2026-10-01 改訂)。
    """
    items: list[dict[str, Any]] = []
    for role, texts in (("final_prompt", prompts), ("final_negative_prompt", negative_prompts)):
        for index, text in enumerate(texts or []):
            if not text.strip():
                continue
            item: dict[str, Any] = {"role": role, "output_index": index, "text": text}
            if len(text) > TEXT_OUTPUT_MAX_CHARS:
                item["text"] = text[:TEXT_OUTPUT_MAX_CHARS]
                item["truncated"] = True
            items.append(item)
    return items or None


def _labels() -> dict[str, str]:
    return {
        "negative_prompt": t("sdwebui.params.negativePrompt"),
        "sampler_name": t("sdwebui.params.sampler"),
        "scheduler": t("sdwebui.params.scheduler"),
        "steps": t("sdwebui.params.steps"),
        "cfg_scale": t("sdwebui.params.cfgScale"),
        "seed": t("sdwebui.params.seed"),
        "vae": t("sdwebui.params.vae"),
        "n": t("sdwebui.params.count"),
    }


def _preferred(choices: list[str], preferred: tuple[str, ...]) -> str | None:
    for name in preferred:
        if name in choices:
            return name
    return None


def _generate_params(catalog: Catalog) -> list[ParamDef]:
    """Generate のパラメーター(ADR-0038 2章)。サンプラーとスケジューラーは接続先の一覧から
    補い、取れなければ項目を出さない。"""
    labels = _labels()
    params: list[ParamDef] = [
        ParamDef(
            name="negative_prompt",
            type="text",
            label=labels["negative_prompt"],
            max_length=sizes.PROMPT_MAX_LENGTH,
            default="",
            description=t("sdwebui.params.negativePromptDescription"),
        ),
    ]
    if catalog.samplers:
        params.append(
            ParamDef(
                name="sampler_name",
                type="enum",
                label=labels["sampler_name"],
                choices=list(catalog.samplers),
                form_default=_preferred(catalog.samplers, _PREFERRED_SAMPLERS),
                description=t("sdwebui.params.samplerDescription"),
            )
        )
    if catalog.schedulers:
        params.append(
            ParamDef(
                name="scheduler",
                type="enum",
                label=labels["scheduler"],
                choices=list(catalog.schedulers),
                form_default=_preferred(catalog.schedulers, _PREFERRED_SCHEDULERS),
                description=t("sdwebui.params.schedulerDescription"),
            )
        )
    params.extend(
        [
            ParamDef(
                name="steps",
                type="int",
                label=labels["steps"],
                minimum=1,
                maximum=150,
                default=20,
                form_default=20,
                description=t("sdwebui.params.stepsDescription"),
            ),
            ParamDef(
                name="cfg_scale",
                type="float",
                label=labels["cfg_scale"],
                minimum=1,
                maximum=30,
                step=0.5,
                default=7.0,
                form_default=7,
                description=t("sdwebui.params.cfgScaleDescription"),
            ),
            ParamDef(
                name="seed",
                type="int",
                label=labels["seed"],
                minimum=0,
                maximum=SEED_MAX,
                required=False,
                default=None,
                widget="seed",
            ),
            ParamDef(
                name="vae",
                type="enum",
                label=labels["vae"],
                choices=[VAE_BUILTIN, *catalog.vaes],
                choice_labels={VAE_BUILTIN: t("sdwebui.params.vaeBuiltin")},
                default=VAE_BUILTIN,
                form_default=VAE_BUILTIN,
                description=t("sdwebui.params.vaeDescription"),
            ),
            ParamDef(
                name="n",
                type="int",
                label=labels["n"],
                minimum=1,
                maximum=N_MAX,
                default=1,
                form_default=1,
                description=t("sdwebui.params.countDescription"),
            ),
        ]
    )
    return params


def _to_png_bytes(data: bytes) -> bytes:
    image = Image.open(io.BytesIO(data))
    image.load()
    buffer = io.BytesIO()
    image.convert("RGBA" if image.mode in ("RGBA", "LA", "P") else "RGB").save(buffer, format="PNG")
    return buffer.getvalue()


def _detect_mime(data: bytes) -> str:
    image = Image.open(io.BytesIO(data))
    image.verify()
    return _MIME_FROM_PILLOW_FORMAT.get(image.format or "", "image/png")
