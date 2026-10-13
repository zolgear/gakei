"""アシスタントの道具(ADR-0048 4・5・6・9章)。

道具は REST を HTTP で呼ばず、REST と同じドメイン関数を直接呼ぶ(MCP と同じ方針。ADR-0023)。
扱えるのは会話の利用者に見えるものだけ(ADR-0025)。削除の道具は作らない。

- 読み取り: `list_prompt_sets`、`get_form_context`、`propose_prompt`、`get_capabilities`、
  `get_run`、`view_assets`
- プロンプトセットの書き込み: `create_prompt_set`、`rename_prompt_set`、`add_prompt_set_items`、
  `update_prompt_set_item`。書き込むたびに変更前の値を結果(`before`)に残し、「元に戻す」に
  要る値を `undo` に入れる(4章)。
- 生成: `generate_image`、`cancel_run`。Run は `run.origin = 'assistant'`、実行者は会話の
  利用者。1回の発言で作れる Run の数と、1時間あたりの Run の数を超えたら、Run を作らずに
  道具のエラーを返す(9章)。

引数は Pydantic のモデルで検証し、LLM に渡す JSON Schema も同じモデルから作る。
"""

from __future__ import annotations

import asyncio
import io
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

import anyio.to_thread
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.annotation.engines import image_to_jpeg
from app.api.capabilities import get_capabilities_endpoint
from app.assistant.engine import LlmImage, ToolCall, ToolSpec
from app.auth.identity import CurrentUser
from app.domain import assistant_settings, derivatives
from app.domain import prompt_sets as prompt_sets_domain
from app.domain import run_create as run_create_domain
from app.domain.assistant_settings import AssistantConfig
from app.domain.models import (
    RUN_ORIGIN_ASSISTANT,
    Asset,
    PromptSetItem,
    Run,
    RunInput,
    RunInputRole,
    RunStatus,
)
from app.domain.pricing import estimate_cost as estimate_cost_domain
from app.domain.schemas import REPEAT_MAX, AssistantFormState, RunCreateRequest, RunInputCreate
from app.domain.visibility import get_visible_asset, get_visible_run
from app.i18n import t

# `get_run` の1回の待ちの上限(秒。9章)。
WAIT_MAX_SECONDS = 25.0
_POLL_INTERVAL_SECONDS = 0.25
# `view_assets` で1回に見る枚数の上限(添付と同じ。6章)。
VIEW_ASSETS_MAX = 4
# LLM に送る画像の長辺(派生のプレビューを縮める。6章)。
IMAGE_MAX_SIDE = 1024
# 1回の書き込みで足せる項目の上限。
ITEMS_MAX = 100
# `list_prompt_sets` で1項目の本文を返す上限(長い本文で履歴が埋まらないように)。
LIST_ITEM_TEXT_MAX = 2000

_TERMINAL = {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELED}
_COST_NOTE = "Reference price in USD computed by GAKEI, not an invoice."


class ToolFailure(Exception):
    """道具の失敗。メッセージは LLM にそのまま返す(LLM が利用者に伝える)。"""


@dataclass
class ToolContext:
    """道具を実行する文脈。`state` は FastAPI の `app.state`(session_factory、store、registry、
    runner、settings、progress_bus、embedding_index_backend)。"""

    state: Any
    user: CurrentUser
    config: AssistantConfig
    form: AssistantFormState | None = None
    # この発言で作った Run(上限の数えと、止めたときの取り消しに使う)。
    runs_this_turn: list[uuid.UUID] = field(default_factory=list)

    def session(self) -> Session:
        return self.state.session_factory()


@dataclass
class ToolOutcome:
    ok: bool
    result: dict[str, Any]
    # LLM に見せる画像(保存しない)。
    images: list[LlmImage] = field(default_factory=list)
    # 「元に戻す」に要る値(書き込みの道具だけ)。
    undo: dict[str, Any] | None = None
    run_ids: list[uuid.UUID] = field(default_factory=list)


async def _in_thread[T](fn: Callable[[], T]) -> T:
    return await anyio.to_thread.run_sync(fn)


def _iso(value: datetime) -> str:
    return value.isoformat()


# -- 引数のモデル ------------------------------------------------------------------


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoArgs(_Args):
    pass


class ProposePromptArgs(_Args):
    prompt: str = Field(min_length=1, max_length=32_000, description="The prompt to show.")
    label: str | None = Field(
        default=None, max_length=100, description="Short title for the card (optional)."
    )
    note: str | None = Field(
        default=None, max_length=1000, description="One-line explanation (optional)."
    )


class GetCapabilitiesArgs(_Args):
    provider: str | None = Field(
        default=None,
        description="Only this provider (from get_form_context). Omit to list all providers.",
    )


class GetRunArgs(_Args):
    run_id: uuid.UUID = Field(description="Run id returned by generate_image.")
    wait_seconds: float = Field(
        default=0,
        ge=0,
        description=(
            f"Wait up to this many seconds for the run to finish (max {int(WAIT_MAX_SECONDS)}; "
            "call again if it is still queued or running)."
        ),
    )


class ViewAssetsArgs(_Args):
    asset_ids: list[uuid.UUID] = Field(
        min_length=1,
        max_length=VIEW_ASSETS_MAX,
        description=f"Asset ids to look at (up to {VIEW_ASSETS_MAX}).",
    )


class PromptSetItemArg(_Args):
    label: str | None = Field(default=None, max_length=100, description="Optional label.")
    text: str = Field(min_length=1, max_length=32_000, description="The prompt text.")


class CreatePromptSetArgs(_Args):
    name: str = Field(min_length=1, max_length=100)
    items: list[PromptSetItemArg] = Field(default_factory=list, max_length=ITEMS_MAX)


class RenamePromptSetArgs(_Args):
    prompt_set_id: uuid.UUID
    name: str = Field(min_length=1, max_length=100)


class AddPromptSetItemsArgs(_Args):
    prompt_set_id: uuid.UUID
    items: list[PromptSetItemArg] = Field(min_length=1, max_length=ITEMS_MAX)


class UpdatePromptSetItemArgs(_Args):
    prompt_set_id: uuid.UUID
    item_id: uuid.UUID
    label: str | None = Field(
        default=None,
        max_length=100,
        description="New label. Pass an empty string to remove the label. Omit to keep it.",
    )
    text: str | None = Field(default=None, min_length=1, max_length=32_000)
    position: int | None = Field(default=None, ge=0, description="New 0-based position.")


class GenerateImageArgs(_Args):
    prompt: str | None = Field(
        default=None,
        max_length=32_000,
        description="Prompt. Omit to use the prompt currently in the form.",
    )
    operation: Literal["generate", "edit"] | None = Field(
        default=None, description="Omit to use the form's operation."
    )
    provider: str | None = Field(default=None, description="Omit to use the form's provider.")
    model: str | None = Field(default=None, description="Omit to use the form's model.")
    params: dict[str, Any] | None = Field(
        default=None,
        description=(
            'Parameters (see get_capabilities), e.g. {"size": "1024x1024", "quality": '
            '"low"}. Merged over the form\'s parameters when the provider and model are the '
            "form's."
        ),
    )
    input_asset_ids: list[uuid.UUID] | None = Field(
        default=None,
        max_length=16,
        description=(
            "Input images for 'edit'. The first one is the primary parent. Omit to use the "
            "form's input images."
        ),
    )
    reference_asset_ids: list[uuid.UUID] | None = Field(
        default=None, max_length=16, description="Reference images (providers that use them)."
    )
    mask_asset_id: uuid.UUID | None = Field(default=None, description="Mask for 'edit'.")
    count: int = Field(
        default=1,
        ge=1,
        le=REPEAT_MAX,
        description="Number of runs to create with these settings (each run is billed).",
    )
    group_id: uuid.UUID | None = Field(
        default=None, description="Put the outputs into this group. Omit to use the form's."
    )


class CancelRunArgs(_Args):
    run_id: uuid.UUID


# -- JSON Schema ------------------------------------------------------------------


def _inline_refs(schema: Any, defs: dict[str, Any]) -> Any:
    """`$ref` を展開し、`title` を落とす(互換サーバーが `$defs` を読めないことがあるため)。"""
    if isinstance(schema, dict):
        if "$ref" in schema:
            name = str(schema["$ref"]).rsplit("/", 1)[-1]
            return _inline_refs(defs.get(name, {}), defs)
        return {
            key: _inline_refs(value, defs)
            for key, value in schema.items()
            if key not in ("title", "$defs")
        }
    if isinstance(schema, list):
        return [_inline_refs(value, defs) for value in schema]
    return schema


def parameters_schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    defs = schema.get("$defs", {})
    result = _inline_refs(schema, defs)
    result.setdefault("properties", {})
    return result


def _validation_message(e: ValidationError) -> str:
    parts = []
    for error in e.errors()[:5]:
        location = ".".join(str(p) for p in error.get("loc", ())) or "(arguments)"
        parts.append(f"{location}: {error.get('msg')}")
    return "Invalid arguments: " + "; ".join(parts)


# -- 読み取りの道具 ----------------------------------------------------------------


def _set_view(prompt_set: Any, *, truncate: bool = False) -> dict[str, Any]:
    items = []
    for item in prompt_set.items:
        text = item.text
        entry: dict[str, Any] = {
            "id": str(item.id),
            "label": item.label,
            "position": item.position,
        }
        if truncate and len(text) > LIST_ITEM_TEXT_MAX:
            entry["text"] = text[:LIST_ITEM_TEXT_MAX]
            entry["text_truncated"] = True
        else:
            entry["text"] = text
        items.append(entry)
    return {
        "id": str(prompt_set.id),
        "name": prompt_set.name,
        "updated_at": _iso(prompt_set.updated_at),
        "items": items,
    }


async def list_prompt_sets(ctx: ToolContext, _args: NoArgs) -> ToolOutcome:
    def _list() -> list[dict[str, Any]]:
        with ctx.session() as db:
            return [_set_view(s, truncate=True) for s in prompt_sets_domain.list_sets(db, ctx.user)]

    return ToolOutcome(ok=True, result={"prompt_sets": await _in_thread(_list)})


async def get_form_context(ctx: ToolContext, _args: NoArgs) -> ToolOutcome:
    if ctx.form is None:
        return ToolOutcome(
            ok=True,
            result={"available": False, "note": "The browser did not send the form state."},
        )
    return ToolOutcome(ok=True, result={"available": True, **ctx.form.model_dump(mode="json")})


async def propose_prompt(ctx: ToolContext, args: ProposePromptArgs) -> ToolOutcome:
    result: dict[str, Any] = {"prompt": args.prompt, "label": args.label, "shown": True}
    if args.note:
        result["note"] = args.note
    return ToolOutcome(ok=True, result=result)


async def get_capabilities(ctx: ToolContext, args: GetCapabilitiesArgs) -> ToolOutcome:
    def _caps() -> dict[str, Any]:
        with ctx.session() as db:
            caps = get_capabilities_endpoint(
                ctx.state.registry,
                db,
                ctx.state.settings,
                getattr(ctx.state, "embedding_index_backend", "numpy"),
            )
        payload = caps.model_dump(mode="json")
        payload.pop("embeddings", None)
        if args.provider is not None:
            payload["providers"] = [
                p for p in payload["providers"] if p.get("provider") == args.provider
            ]
            if not payload["providers"]:
                raise ToolFailure(f"Unknown provider: {args.provider}")
        payload["usage_notes"] = [
            'Put the size in params.size as "WIDTHxHEIGHT" within providers[].size limits.',
            "Other parameters under models[].operations[].params also go in params.",
        ]
        return payload

    return ToolOutcome(ok=True, result=await _in_thread(_caps))


def _run_payload(db: Session, ctx: ToolContext, run: Run) -> dict[str, Any]:
    outputs = (
        db.execute(
            select(Asset).where(Asset.produced_by_run_id == run.id).order_by(Asset.output_index)
        )
        .scalars()
        .all()
    )
    payload: dict[str, Any] = {
        "run_id": str(run.id),
        "status": str(run.status),
        "provider": run.provider,
        "model": run.model,
        "operation": str(run.operation),
        "prompt": run.prompt,
        "params": run.params or {},
        "origin": run.origin,
        "error_message": run.error_message,
        "queued_at": _iso(run.queued_at),
        "finished_at": _iso(run.finished_at) if run.finished_at else None,
        "deleted": run.deleted_at is not None,
        "outputs": [{"asset_id": str(a.id), "width": a.width, "height": a.height} for a in outputs],
    }
    if run.status not in _TERMINAL:
        payload["note"] = (
            "The run has not finished yet. Call get_run again with wait_seconds "
            f"(up to {int(WAIT_MAX_SECONDS)})."
        )
    return payload


def _run_status(ctx: ToolContext, run_id: uuid.UUID) -> RunStatus | None:
    with ctx.session() as db:
        run = get_visible_run(db, ctx.user, run_id)
        return None if run is None else RunStatus(run.status)


async def get_run(ctx: ToolContext, args: GetRunArgs) -> ToolOutcome:
    wait = min(args.wait_seconds, WAIT_MAX_SECONDS)
    deadline = time.monotonic() + wait
    while True:
        status = await _in_thread(lambda: _run_status(ctx, args.run_id))
        if status is None:
            raise ToolFailure(f"Run {args.run_id} not found.")
        if status in _TERMINAL or time.monotonic() >= deadline:
            break
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)

    def _load() -> dict[str, Any]:
        with ctx.session() as db:
            run = get_visible_run(db, ctx.user, args.run_id)
            if run is None:
                raise ToolFailure(f"Run {args.run_id} not found.")
            return _run_payload(db, ctx, run)

    return ToolOutcome(ok=True, result=await _in_thread(_load))


def render_asset_for_llm(store: Any, asset: Asset) -> LlmImage | None:
    """派生のプレビュー(2048px)を長辺 `IMAGE_MAX_SIDE` に縮めた JPEG(6章)。原本は送らない。
    派生は `ensure_derived` を通す(ADR-0036)。原本も無ければ None。"""
    content = derivatives.ensure_derived(store, asset.blob_key, asset.sha256, "preview")
    if content is None:
        return None
    with Image.open(io.BytesIO(content.read_all())) as image:
        data = image_to_jpeg(image, IMAGE_MAX_SIDE)
    return LlmImage(data=data, label=str(asset.id))


def load_visible_images(
    ctx: ToolContext, asset_ids: list[uuid.UUID]
) -> tuple[list[dict[str, Any]], list[LlmImage]]:
    """見える Asset の画像を読む。見えない・無いものは ToolFailure。"""
    briefs: list[dict[str, Any]] = []
    images: list[LlmImage] = []
    with ctx.session() as db:
        assets = []
        for asset_id in asset_ids:
            asset = get_visible_asset(db, ctx.user, asset_id)
            if asset is None or asset.deleted_at is not None:
                raise ToolFailure(f"Asset {asset_id} not found.")
            db.expunge(asset)
            assets.append(asset)
    for asset in assets:
        try:
            image = render_asset_for_llm(ctx.state.store, asset)
        except (OSError, ValueError) as e:
            raise ToolFailure(f"The image of asset {asset.id} could not be read.") from e
        if image is None:
            raise ToolFailure(f"The image file of asset {asset.id} is missing.")
        images.append(image)
        briefs.append(
            {
                "asset_id": str(asset.id),
                "kind": str(asset.kind),
                "width": asset.width,
                "height": asset.height,
            }
        )
    return briefs, images


async def view_assets(ctx: ToolContext, args: ViewAssetsArgs) -> ToolOutcome:
    briefs, images = await _in_thread(lambda: load_visible_images(ctx, args.asset_ids))
    return ToolOutcome(
        ok=True,
        result={
            "assets": briefs,
            "note": "The images follow in the next message, in this order.",
        },
        images=images,
    )


# -- プロンプトセットの書き込み ----------------------------------------------------------


def _items_in(items: list[PromptSetItemArg]) -> list[prompt_sets_domain.ItemInput]:
    return [prompt_sets_domain.ItemInput(label=i.label or None, text=i.text) for i in items]


def _domain_call[T](fn: Callable[[], T]) -> T:
    try:
        return fn()
    except (
        prompt_sets_domain.PromptSetNotFoundError,
        prompt_sets_domain.PromptSetItemNotFoundError,
    ) as e:
        raise ToolFailure(str(e)) from e


async def create_prompt_set(ctx: ToolContext, args: CreatePromptSetArgs) -> ToolOutcome:
    def _create() -> ToolOutcome:
        with ctx.session() as db:
            created = prompt_sets_domain.create_set(db, ctx.user, args.name, _items_in(args.items))
        after = _set_view(created)
        return ToolOutcome(
            ok=True,
            result={"prompt_set_id": str(created.id), "before": None, "after": after},
            undo={
                "kind": "create_prompt_set",
                "prompt_set_id": str(created.id),
                "updated_at": _iso(created.updated_at),
            },
        )

    return await _in_thread(_create)


async def rename_prompt_set(ctx: ToolContext, args: RenamePromptSetArgs) -> ToolOutcome:
    def _rename() -> ToolOutcome:
        with ctx.session() as db:
            before = _domain_call(
                lambda: prompt_sets_domain.get_set(db, ctx.user, args.prompt_set_id)
            )
            after = prompt_sets_domain.rename_set(db, ctx.user, args.prompt_set_id, args.name)
        return ToolOutcome(
            ok=True,
            result={
                "prompt_set_id": str(after.id),
                "before": {"name": before.name},
                "after": {"name": after.name},
            },
            undo={
                "kind": "rename_prompt_set",
                "prompt_set_id": str(after.id),
                "name": before.name,
                "updated_at": _iso(after.updated_at),
            },
        )

    return await _in_thread(_rename)


async def add_prompt_set_items(ctx: ToolContext, args: AddPromptSetItemsArgs) -> ToolOutcome:
    def _add() -> ToolOutcome:
        with ctx.session() as db:
            added = _domain_call(
                lambda: prompt_sets_domain.add_items(
                    db, ctx.user, args.prompt_set_id, _items_in(args.items)
                )
            )
            name = prompt_sets_domain.get_set(db, ctx.user, args.prompt_set_id).name
        views = [
            {"id": str(i.id), "label": i.label, "text": i.text, "position": i.position}
            for i in added
        ]
        return ToolOutcome(
            ok=True,
            result={
                "prompt_set_id": str(args.prompt_set_id),
                "prompt_set_name": name,
                "before": None,
                "after": {"items": views},
            },
            undo={
                "kind": "add_prompt_set_items",
                "prompt_set_id": str(args.prompt_set_id),
                "items": [{"id": str(i.id), "updated_at": _iso(i.updated_at)} for i in added],
            },
        )

    return await _in_thread(_add)


async def update_prompt_set_item(ctx: ToolContext, args: UpdatePromptSetItemArgs) -> ToolOutcome:
    fields_set = args.model_fields_set
    if not ({"label", "text", "position"} & fields_set):
        raise ToolFailure("Nothing to change: pass label, text or position.")

    def _update() -> ToolOutcome:
        with ctx.session() as db:
            prompt_set = _domain_call(
                lambda: prompt_sets_domain.get_set(db, ctx.user, args.prompt_set_id)
            )
            item: PromptSetItem = _domain_call(
                lambda: prompt_sets_domain.get_active_item(db, args.prompt_set_id, args.item_id)
            )
            before = {"label": item.label, "text": item.text, "position": item.position}
            kwargs: dict[str, Any] = {"text": args.text, "position": args.position}
            if "label" in fields_set:
                kwargs["label"] = args.label or None
            after = prompt_sets_domain.update_item(
                db, ctx.user, args.prompt_set_id, args.item_id, **kwargs
            )
        return ToolOutcome(
            ok=True,
            result={
                "prompt_set_id": str(args.prompt_set_id),
                "prompt_set_name": prompt_set.name,
                "item_id": str(args.item_id),
                "before": before,
                "after": {"label": after.label, "text": after.text, "position": after.position},
            },
            undo={
                "kind": "update_prompt_set_item",
                "prompt_set_id": str(args.prompt_set_id),
                "item_id": str(args.item_id),
                "before": before,
                "updated_at": _iso(after.updated_at),
            },
        )

    return await _in_thread(_update)


# -- 生成 ---------------------------------------------------------------------------


@dataclass
class _ResolvedRun:
    body: RunCreateRequest
    repeat: int


def _resolve_generate(ctx: ToolContext, args: GenerateImageArgs) -> _ResolvedRun:
    """指定の無い項目を、今のフォームの値で埋める(9章)。"""
    form = ctx.form
    registry = ctx.state.registry
    provider_name = args.provider or (form.provider if form else None) or registry.primary
    provider = registry.get(provider_name)
    if provider is None:
        raise ToolFailure(t("runs.unknownProvider", provider=provider_name))
    same_provider = form is not None and (form.provider or registry.primary) == provider_name

    model = args.model
    if model is None and same_provider and form is not None and form.model:
        model = form.model
    if model is None:
        model = provider.capabilities().default_model
    same_form = same_provider and form is not None and form.model == model

    operation = args.operation or (form.operation if form and same_provider else None)
    operation = operation or "generate"

    params: dict[str, Any] = {}
    if same_form and form is not None:
        params.update(form.params)
    params.update(args.params or {})

    prompt = args.prompt if args.prompt is not None else (form.prompt if form else None)
    if not prompt or not prompt.strip():
        raise ToolFailure("No prompt: pass prompt (the form's prompt is empty).")

    inputs: list[RunInputCreate] = []
    explicit_inputs = (
        args.input_asset_ids is not None
        or args.reference_asset_ids is not None
        or args.mask_asset_id is not None
    )
    if explicit_inputs:
        for i, asset_id in enumerate(args.input_asset_ids or []):
            inputs.append(RunInputCreate(asset_id=asset_id, role="image", position=i))
        for i, asset_id in enumerate(args.reference_asset_ids or []):
            inputs.append(RunInputCreate(asset_id=asset_id, role="reference", position=i))
        if args.mask_asset_id is not None:
            inputs.append(RunInputCreate(asset_id=args.mask_asset_id, role="mask", position=0))
    elif form is not None and same_provider and operation == "edit":
        positions: dict[str, int] = {}
        for item in form.inputs:
            position = positions.get(item.role, 0)
            positions[item.role] = position + 1
            inputs.append(RunInputCreate(asset_id=item.asset_id, role=item.role, position=position))

    group_id = args.group_id or (form.asset_group_id if form else None)
    body = RunCreateRequest(
        operation=operation,
        model=model,
        prompt=prompt,
        provider=provider_name,
        params=params,
        inputs=inputs,
        asset_group_id=group_id,
    )
    return _ResolvedRun(body=body, repeat=args.count)


def _params_n(params: dict[str, Any]) -> int:
    value = params.get("n", 1)
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 1


def _estimate_runs(db: Session, ctx: ToolContext, runs: list[Run]) -> float | None:
    """作った Run の料金の目安の合計(画面の見積もりと同じ計算)。見積もれなければ None。"""
    total = 0.0
    for run in runs:
        provider = ctx.state.registry.get(run.provider)
        if provider is None or not getattr(provider, "supports_pricing", False):
            return None
        params = run.params or {}
        image_ids = [
            i.asset_id
            for i in db.execute(select(RunInput).where(RunInput.run_id == run.id)).scalars()
            if i.role == RunInputRole.IMAGE
        ]
        sizes = []
        for asset_id in image_ids:
            asset = db.get(Asset, asset_id)
            if asset is not None and asset.width and asset.height:
                sizes.append((asset.id, asset.width, asset.height))
        result = estimate_cost_domain(
            model=run.model,
            quality=str(params.get("quality") or "auto"),
            size=str(params.get("size") or "auto"),
            n=_params_n(params),
            prompt_length=len(run.prompt),
            input_images=sizes,
        )
        if result.total_usd is None:
            return None
        total += result.total_usd
    return round(total, 6)


async def generate_image(ctx: ToolContext, args: GenerateImageArgs) -> ToolOutcome:
    def _create() -> ToolOutcome:
        resolved = _resolve_generate(ctx, args)
        config = ctx.config
        per_message = config.runs_per_message_limit
        if len(ctx.runs_this_turn) + resolved.repeat > per_message:
            raise ToolFailure(
                t(
                    "assistant.runsPerMessageLimit",
                    limit=per_message,
                    used=len(ctx.runs_this_turn),
                )
            )
        with ctx.session() as db:
            recent = assistant_settings.count_recent_runs(db, ctx.user.id)
            if recent + resolved.repeat > config.hourly_run_limit:
                raise ToolFailure(
                    t("assistant.hourlyRunLimit", limit=config.hourly_run_limit, used=recent)
                )
            try:
                runs = run_create_domain.create_runs(
                    db,
                    ctx.state.registry,
                    ctx.state.runner,
                    ctx.state.settings,
                    resolved.body,
                    viewer=ctx.user,
                    origin=run_create_domain.RunOrigin(origin=RUN_ORIGIN_ASSISTANT),
                    repeat=resolved.repeat,
                )
            except run_create_domain.RunCreateError as e:
                raise ToolFailure(str(e)) from e
            run_ids = [run.id for run in runs]
            ctx.runs_this_turn.extend(run_ids)
            estimate = _estimate_runs(db, ctx, runs)
            body = resolved.body
            result = {
                "run_ids": [str(run_id) for run_id in run_ids],
                "status": "queued",
                "provider": body.provider,
                "model": body.model,
                "operation": body.operation,
                "prompt": body.prompt,
                "params": runs[0].params or {},
                "inputs": [i.model_dump(mode="json") for i in body.inputs],
                "estimated_cost_usd": estimate,
                "cost_note": _COST_NOTE,
                "runs_this_message": len(ctx.runs_this_turn),
                "runs_per_message_limit": per_message,
                "runs_last_hour": recent + len(run_ids),
                "hourly_run_limit": config.hourly_run_limit,
                "note": (
                    "The runs are queued. Call get_run with wait_seconds until each one has "
                    "finished."
                ),
            }
        return ToolOutcome(ok=True, result=result, run_ids=run_ids)

    return await _in_thread(_create)


def cancel_queued_runs(state: Any, run_ids: list[uuid.UUID]) -> list[uuid.UUID]:
    """まだ `queued` の Run を取り消す(止めたとき。`running` はそのまま。9章)。取り消した
    Run を返す。"""
    if not run_ids:
        return []
    canceled: list[uuid.UUID] = []
    with state.session_factory() as db:
        for run_id in run_ids:
            result = db.execute(
                update(Run)
                .where(Run.id == run_id, Run.status == RunStatus.QUEUED)
                .values(status=RunStatus.CANCELED, finished_at=datetime.now(UTC))
            )
            if result.rowcount:
                canceled.append(run_id)
        db.commit()
    return canceled


async def publish_canceled(state: Any, run_ids: list[uuid.UUID]) -> None:
    for run_id in run_ids:
        await state.progress_bus.publish(run_id, {"type": "status", "status": "canceled"})


async def cancel_run(ctx: ToolContext, args: CancelRunArgs) -> ToolOutcome:
    def _cancel() -> None:
        with ctx.session() as db:
            run = get_visible_run(db, ctx.user, args.run_id)
            if run is None or run.deleted_at is not None or run.origin != RUN_ORIGIN_ASSISTANT:
                raise ToolFailure(f"Run {args.run_id} not found (only runs you created).")
            if run.status != RunStatus.QUEUED:
                raise ToolFailure(
                    f"Run {args.run_id} cannot be canceled (status: {run.status}). Only queued "
                    "runs can be canceled."
                )
        if not cancel_queued_runs(ctx.state, [args.run_id]):
            raise ToolFailure(f"Run {args.run_id} has already started and cannot be canceled.")

    await _in_thread(_cancel)
    await publish_canceled(ctx.state, [args.run_id])
    return ToolOutcome(ok=True, result={"run_id": str(args.run_id), "status": "canceled"})


# -- 登録簿 ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    args: type[BaseModel]
    handler: Callable[[ToolContext, Any], Awaitable[ToolOutcome]]

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name, description=self.description, parameters=parameters_schema(self.args)
        )


TOOLS: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        "list_prompt_sets",
        "List the user's prompt sets with their items (id, label, text, position).",
        NoArgs,
        list_prompt_sets,
    ),
    ToolDefinition(
        "get_form_context",
        "Get the current generation form: provider, model, operation (generate / edit), "
        "prompt, params, input images and group.",
        NoArgs,
        get_form_context,
    ),
    ToolDefinition(
        "propose_prompt",
        "Show a prompt to the user as a card with buttons to insert it into the form, replace "
        "the form's prompt, or copy it. Does not change the form.",
        ProposePromptArgs,
        propose_prompt,
    ),
    ToolDefinition(
        "get_capabilities",
        "List providers, models and their parameter definitions (same as the form uses).",
        GetCapabilitiesArgs,
        get_capabilities,
    ),
    ToolDefinition(
        "get_run",
        "Get a run's status and outputs. Optionally wait (up to 25 s per call) for it to "
        "finish; call again while it is queued or running.",
        GetRunArgs,
        get_run,
    ),
    ToolDefinition(
        "view_assets",
        "Look at images in the stock (for example the outputs of a run). The images are sent "
        "to you after the tool result.",
        ViewAssetsArgs,
        view_assets,
    ),
    ToolDefinition(
        "create_prompt_set",
        "Create a prompt set with a name and items. Applied immediately; the user can undo it.",
        CreatePromptSetArgs,
        create_prompt_set,
    ),
    ToolDefinition(
        "rename_prompt_set",
        "Rename a prompt set. Applied immediately; the user can undo it.",
        RenamePromptSetArgs,
        rename_prompt_set,
    ),
    ToolDefinition(
        "add_prompt_set_items",
        "Append items to a prompt set. Applied immediately; the user can undo it.",
        AddPromptSetItemsArgs,
        add_prompt_set_items,
    ),
    ToolDefinition(
        "update_prompt_set_item",
        "Change an item's label, text or position. Applied immediately; the user can undo it.",
        UpdatePromptSetItemArgs,
        update_prompt_set_item,
    ),
    ToolDefinition(
        "generate_image",
        "Create generation / edit runs (billed). Parameters you omit come from the current "
        "form. Returns run ids right away with an estimated price; then wait with get_run.",
        GenerateImageArgs,
        generate_image,
    ),
    ToolDefinition(
        "cancel_run",
        "Cancel a queued run that you created. A run that has started cannot be canceled.",
        CancelRunArgs,
        cancel_run,
    ),
)

TOOLS_BY_NAME: dict[str, ToolDefinition] = {tool.name: tool for tool in TOOLS}


def tool_specs() -> list[ToolSpec]:
    return [tool.spec() for tool in TOOLS]


async def execute_tool(ctx: ToolContext, call: ToolCall) -> ToolOutcome:
    """道具を1つ実行する。失敗は `ok=False` の結果にする(例外にしない)。"""
    definition = TOOLS_BY_NAME.get(call.name)
    if definition is None:
        return ToolOutcome(ok=False, result={"error": f"Unknown tool: {call.name}"})
    if call.arguments_error is not None:
        return ToolOutcome(ok=False, result={"error": call.arguments_error})
    try:
        args = definition.args.model_validate(call.arguments)
    except ValidationError as e:
        return ToolOutcome(ok=False, result={"error": _validation_message(e)})
    try:
        return await definition.handler(ctx, args)
    except ToolFailure as e:
        return ToolOutcome(ok=False, result={"error": str(e)})
