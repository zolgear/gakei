"""MCP のツール(ADR-0023 3章)と、SDK のサーバー・セッションマネージャーの組み立て。

ツールは REST を HTTP で呼ばず、REST と同じドメイン関数を直接呼ぶ。削除と設定の変更は
提供しない。画像は Asset ID と URL で返し、本文に載せるのはサムネイル(512px)と、`get_image`
で求められた長辺 1568px までの画像だけ(ADR-0004、ADR-0023 8章)。どちらも JPEG / PNG で、
4K の原本は載せない。原本は `create_download_url` の1回限りの URL で取り出す。

ADR-0025: どのツールも、トークンの持ち主(`mc.user`)に見えるものだけを扱う
(`app/domain/visibility.py`)。他人のものは「見つからない」と同じエラーにする。
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import math
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

import anyio.to_thread
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, ImageContent, TextContent, ToolAnnotations
from pydantic import Field, ValidationError
from sqlalchemy import and_, select, update
from sqlalchemy.orm import Session

from app.api.capabilities import get_capabilities_endpoint
from app.api.prompt_sets import list_prompt_sets as list_prompt_sets_rest
from app.domain import agent_images, mcp_settings
from app.domain import annotations as annotations_domain
from app.domain import download_tickets as download_tickets_domain
from app.domain import run_create as run_create_domain
from app.domain import upload_tickets as upload_tickets_domain
from app.domain.alpha_stats import alpha_stats
from app.domain.asset_groups import (
    AssetGroupAssetsMissingError,
    add_members,
    group_for_asset,
)
from app.domain.asset_groups import create_group as create_group_domain
from app.domain.asset_groups import list_groups as list_groups_domain
from app.domain.assets import MAX_UPLOAD_BYTES, IngestError, ingest_upload
from app.domain.lineage import (
    LineageNotFoundError,
    RunLineageNotFoundError,
    asset_lineage_graph,
    run_lineage_graph,
)
from app.domain.lineage_mermaid import LineageGraph, MermaidOptions, render_lineage_mermaid
from app.domain.models import (
    Asset,
    AssetGroupMember,
    AssetKind,
    Run,
    RunInput,
    RunInputRole,
    RunStatus,
)
from app.domain.pricing import EstimateResult, cost_from_usage
from app.domain.pricing import estimate_cost as estimate_cost_domain
from app.domain.run_views import run_text_outputs
from app.domain.schemas import AssetGroupCreate, RunCreateRequest, RunInputCreate
from app.domain.search import MAX_LIMIT as SEARCH_MAX_LIMIT
from app.domain.search import InvalidSearchQueryError, search
from app.domain.storage import AssetStore
from app.domain.visibility import (
    asset_visible,
    get_visible_asset,
    get_visible_group,
    get_visible_run,
    run_visible,
    visible_asset_ids,
)
from app.mcp.context import McpRequestContext, get_mcp_context
from app.providers.openai_pricing import PRICING_CHECKED_AT, PRICING_SOURCE_URL
from app.version import get_version

# 1回のツール呼び出しで Run の完了を待つ上限(秒。ADR-0023 7章 1)。一般的な MCP クライアントの
# タイムアウトより短くし、応答が返らずに run_id が分からなくなる事態を避ける。これを過ぎたら
# run_id と現在の状態を返し、`get_run` の `wait_seconds` で続きを待ってもらう。
WAIT_MAX_SECONDS = 25
_POLL_INTERVAL_SECONDS = 0.25

# 1回の検索・一覧で返す件数の上限。
SEARCH_LIMIT_MAX = SEARCH_MAX_LIMIT
LIST_RUNS_LIMIT_MAX = 50

# `upload_image` の本文の上限。REST のアップロードと同じ `MAX_UPLOAD_BYTES` を base64 に
# した大きさに、JSON-RPC の包みの分の余裕を足す。
MAX_REQUEST_BODY_BYTES = math.ceil(MAX_UPLOAD_BYTES / 3) * 4 + 1024 * 1024

_TERMINAL = {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELED}

# `lineage_mermaid`(ADR-0023 9章)の範囲。1世代 = Asset→Run→Asset の2ホップ。
# `get_asset` は起点の祖先 3 世代・子孫 2 世代。`get_run` は出力から数えて祖先 3 世代
# (入力 Asset が1世代目なので、入力からはさらに 2 世代 = 4 ホップ)。
LINEAGE_UP_GENERATIONS = 3
LINEAGE_DOWN_GENERATIONS = 2
LINEAGE_MAX_NODES = 40
_LINEAGE_OPTIONS = MermaidOptions(direction="LR", prompt_chars=40)

_INSTRUCTIONS = (
    "GAKEI is a self-hosted image generation workspace. Use get_capabilities to see models "
    'and parameters (put the image size in params.size, e.g. {"size": "1024x1024"}), '
    "estimate_cost to see a reference price, and generate_image to create or edit images "
    "(this is billed to the GAKEI operator). generate_image returns a run_id right away; call "
    "get_run with wait_seconds (up to 25 s per call, repeat as needed) until the status is "
    "succeeded, failed or canceled. If you lose a run_id, list_runs shows your recent runs. "
    "To use a local image as an edit input, call create_upload_url and send the file with "
    "curl (upload_image with base64 is only for small images). search_assets / get_asset find "
    "existing images, and groups organize them. Images are referenced by asset ID. "
    "To keep editing a result (chained edits), pass the output asset_id from get_run (or any "
    "asset_id from search_assets / get_asset) directly in generate_image's input_asset_ids. "
    "Prefer this over downloading and re-uploading the image: it needs no transfer, keeps the "
    "original quality and records the lineage (parent image) in GAKEI. Upload only images "
    "that are not in GAKEI yet (e.g. local files or images you created outside GAKEI). "
    "To LOOK at an image, call get_image (it returns the image in the tool result, up to "
    "1568 px on the long edge; results also carry small 512 px thumbnails). To get the "
    "ORIGINAL file for processing, call create_download_url and fetch the one-time URL with a "
    "tool that runs on the user's machine (e.g. curl). The url and viewer_url fields are for "
    "the user to open in a browser: GAKEI usually runs inside the user's LAN, so tools that "
    "run in the cloud (web fetch, cloud code execution) cannot reach any GAKEI URL, and there "
    "is no way to hand the original to cloud code execution. Deleting and changing settings "
    "are only possible in the GAKEI web UI. get_run / generate_image / get_asset results "
    "include lineage_mermaid, a Mermaid flowchart of how images were made: rounded boxes "
    "are assets (images), hexagons are runs (one API call), arrows go input asset -> run -> "
    "output asset with the input role (primary parent, reference, mask), and the %% comment "
    "lines map the short node IDs (a1, r1) to full asset / run IDs you can pass to other tools."
)

_SIZE_HINT = (
    'Put the image size in params.size as "WIDTHxHEIGHT" (e.g. {"size": "1024x1024"}) or '
    '"auto"; the allowed range is in providers[].size of get_capabilities.'
)


async def _in_thread[T](fn: Callable[[], T]) -> T:
    return await anyio.to_thread.run_sync(fn)


def _session(mc: McpRequestContext) -> Session:
    return mc.state.session_factory()


def _asset_urls(mc: McpRequestContext, asset_id: uuid.UUID) -> dict[str, str]:
    return {
        "url": f"{mc.base_url}/api/assets/{asset_id}/content?variant=original",
        "viewer_url": f"{mc.base_url}/assets/{asset_id}",
    }


def _image_content(image: agent_images.AgentImage) -> ImageContent:
    return ImageContent(type="image", data=image.base64, mime_type=image.mime_type)


def _thumbnail(store: AssetStore, asset: Asset) -> ImageContent | None:
    """長辺 512px のサムネイル(ADR-0023 8章 2)。WebP を扱えないクライアントがあるので、
    `get_image` と同じ規則で JPEG / PNG にする(透過の判定はサムネイルの画素で行い、4K の
    原本はデコードしない)。原本・プレビューは載せない(ADR-0004)。"""
    try:
        image = agent_images.render_asset(store, asset, "small")
    except (OSError, ValueError):
        return None
    return None if image is None else _image_content(image)


def _result(payload: dict[str, Any], images: list[ImageContent] | None = None) -> CallToolResult:
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    content: list[Any] = [TextContent(type="text", text=text)]
    content.extend(images or [])
    return CallToolResult(content=content, structured_content=payload)


def _asset_brief(mc: McpRequestContext, asset: Asset) -> dict[str, Any]:
    return {
        "asset_id": str(asset.id),
        "kind": str(asset.kind),
        "mime": asset.mime,
        "width": asset.width,
        "height": asset.height,
        "bytes": asset.bytes,
        "created_at": asset.created_at.isoformat(),
        "deleted": asset.deleted_at is not None,
        **_asset_urls(mc, asset.id),
    }


def _mermaid(build: Callable[[], LineageGraph]) -> str | None:
    """系列グラフを Mermaid にする。起点が見えない・無いときは None。"""
    try:
        return render_lineage_mermaid(build(), _LINEAGE_OPTIONS)
    except (LineageNotFoundError, RunLineageNotFoundError):
        return None


def _run_lineage_mermaid(mc: McpRequestContext, db: Session, run_id: uuid.UUID) -> str | None:
    return _mermaid(
        lambda: run_lineage_graph(
            db,
            run_id,
            viewer=mc.user,
            up=LINEAGE_UP_GENERATIONS * 2 - 2,
            max_nodes=LINEAGE_MAX_NODES,
        )
    )


def _asset_lineage_mermaid(mc: McpRequestContext, db: Session, asset_id: uuid.UUID) -> str | None:
    return _mermaid(
        lambda: asset_lineage_graph(
            db,
            asset_id,
            viewer=mc.user,
            up=LINEAGE_UP_GENERATIONS * 2,
            down=LINEAGE_DOWN_GENERATIONS * 2,
            max_nodes=LINEAGE_MAX_NODES,
        )
    )


def _text_outputs_payload(run: Run) -> list[dict[str, Any]] | None:
    """最終プロンプト(PE の出力)など、実行時に作られたテキスト(ADR-0030 4章)。無ければ None。"""
    items = run_text_outputs(run)
    if items is None:
        return None
    return [item.model_dump(mode="json") for item in items]


def _run_payload(
    mc: McpRequestContext,
    db: Session,
    run: Run,
    include_thumbnails: bool,
    include_lineage: bool = False,
) -> tuple[dict[str, Any], list[ImageContent]]:
    outputs = (
        db.execute(
            select(Asset).where(Asset.produced_by_run_id == run.id).order_by(Asset.output_index)
        )
        .scalars()
        .all()
    )
    inputs = (
        db.execute(select(RunInput).where(RunInput.run_id == run.id).order_by(RunInput.position))
        .scalars()
        .all()
    )
    # 見えない入力(以前のデータで他人の Asset を入力にしていた場合)は id も返さない(ADR-0025)。
    visible_inputs = visible_asset_ids(db, mc.user, [i.asset_id for i in inputs])
    group = None
    if run.asset_group_id is not None:
        g = get_visible_group(db, mc.user, run.asset_group_id)
        if g is not None:
            group = {"id": str(g.id), "name": g.name}
    payload: dict[str, Any] = {
        "run_id": str(run.id),
        "status": str(run.status),
        "provider": run.provider,
        "operation": str(run.operation),
        "model": run.model,
        "prompt": run.prompt,
        "params": run.params or {},
        "error_code": run.error_code,
        "error_message": run.error_message,
        "queued_at": run.queued_at.isoformat(),
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "origin": run.origin,
        "text_outputs": _text_outputs_payload(run),
        "asset_group": group,
        "inputs": [
            {"asset_id": str(i.asset_id), "role": str(i.role), "position": i.position}
            for i in inputs
            if i.asset_id in visible_inputs
        ],
        "outputs": [_asset_brief(mc, a) for a in outputs],
    }
    payload["cost"] = _run_cost(mc, db, run, inputs)
    if include_lineage:
        payload["lineage_mermaid"] = _run_lineage_mermaid(mc, db, run.id)
    if run.status not in _TERMINAL:
        payload["note"] = (
            "The run has not finished yet. Call get_run with this run_id and wait_seconds "
            f"(up to {WAIT_MAX_SECONDS}) to wait for it; repeat until the status is succeeded, "
            "failed or canceled."
        )
    images: list[ImageContent] = []
    if include_thumbnails:
        store: AssetStore = mc.state.store
        for asset in outputs:
            thumb = _thumbnail(store, asset)
            if thumb is not None:
                images.append(thumb)
    return payload, images


def _load_run_result(
    mc: McpRequestContext, run_id: uuid.UUID, include_thumbnails: bool, include_lineage: bool
) -> tuple[dict[str, Any], list[ImageContent]] | None:
    with _session(mc) as db:
        run = get_visible_run(db, mc.user, run_id)
        if run is None or run.deleted_at is not None:
            return None
        return _run_payload(mc, db, run, include_thumbnails, include_lineage)


def _run_status(mc: McpRequestContext, run_id: uuid.UUID) -> RunStatus | None:
    with _session(mc) as db:
        run = get_visible_run(db, mc.user, run_id)
        return None if run is None else RunStatus(run.status)


async def _wait_for_terminal(mc: McpRequestContext, run_id: uuid.UUID, seconds: float) -> None:
    deadline = time.monotonic() + max(0.0, min(seconds, WAIT_MAX_SECONDS))
    while True:
        status = await _in_thread(lambda: _run_status(mc, run_id))
        if status is None or status in _TERMINAL:
            return
        if time.monotonic() >= deadline:
            return
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)


async def _run_result(
    mc: McpRequestContext,
    run_id: uuid.UUID,
    include_thumbnails: bool,
    *,
    with_quota: bool,
    include_lineage: bool = False,
) -> CallToolResult:
    loaded = await _in_thread(
        lambda: _load_run_result(mc, run_id, include_thumbnails, include_lineage)
    )
    if loaded is None:
        raise ToolError(f"Run {run_id} not found.")
    payload, images = loaded
    if with_quota:
        payload["quota"] = await _in_thread(lambda: _load_quota(mc))
    return _result(payload, images)


def _quota(db: Session) -> dict[str, int]:
    """MCP 経由の生成の、1時間の上限と残り(ADR-0023 7章 6)。"""
    limit = mcp_settings.hourly_run_limit(db)
    used = mcp_settings.count_recent_mcp_runs(db)
    return {
        "hourly_run_limit": limit,
        "runs_last_hour": used,
        "remaining": max(0, limit - used),
    }


def _load_quota(mc: McpRequestContext) -> dict[str, int]:
    with _session(mc) as db:
        return _quota(db)


def _supports_pricing(mc: McpRequestContext, provider_name: str) -> bool:
    provider = mc.state.registry.get(provider_name)
    return bool(provider is not None and getattr(provider, "supports_pricing", False))


def _input_sizes(
    mc: McpRequestContext, db: Session, asset_ids: list[uuid.UUID]
) -> list[tuple[uuid.UUID, int, int]]:
    """見積もりに使う入力画像の大きさ(削除済み・大きさ不明・見えない(ADR-0025)ものは
    数えない。REST と同じ)。"""
    if not asset_ids:
        return []
    rows = db.execute(
        select(Asset).where(
            Asset.id.in_(asset_ids), Asset.deleted_at.is_(None), asset_visible(mc.user)
        )
    ).scalars()
    by_id = {a.id: a for a in rows}
    sizes: list[tuple[uuid.UUID, int, int]] = []
    for asset_id in asset_ids:
        asset = by_id.get(asset_id)
        if asset is not None and asset.width and asset.height:
            sizes.append((asset.id, asset.width, asset.height))
    return sizes


def _estimate(
    mc: McpRequestContext,
    db: Session,
    model: str,
    params: dict[str, Any],
    n: int,
    prompt_length: int,
    input_asset_ids: list[uuid.UUID],
) -> EstimateResult:
    """画面の見積もり(`GET /api/pricing/estimate`)と同じ計算。quality・size を省いた場合は
    API の既定(auto)として扱うので、見積もれない。"""
    return estimate_cost_domain(
        model=model,
        quality=str(params.get("quality") or "auto"),
        size=str(params.get("size") or "auto"),
        n=n,
        prompt_length=prompt_length,
        input_images=_input_sizes(mc, db, input_asset_ids),
    )


def _params_n(params: dict[str, Any]) -> int:
    value = params.get("n", 1)
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 1


_COST_NOTE = "Reference price in USD computed by GAKEI, not an invoice."


def _run_cost(
    mc: McpRequestContext, db: Session, run: Run, inputs: list[RunInput]
) -> dict[str, Any] | None:
    """Run の料金の目安(分かる場合だけ)。成功した Run は実際の usage × 単価、それ以外の
    実行前・実行中の Run はパラメーターからの見積もり。失敗・取り消しは null。"""
    if not _supports_pricing(mc, run.provider):
        return None
    if run.usage:
        usd = cost_from_usage(run.model, run.usage)
        if usd is not None:
            return {"usd": usd, "basis": "usage", "note": _COST_NOTE}
        return None
    if run.status in (RunStatus.FAILED, RunStatus.CANCELED):
        return None
    params = run.params or {}
    image_ids = [i.asset_id for i in inputs if i.role == RunInputRole.IMAGE]
    result = _estimate(mc, db, run.model, params, _params_n(params), len(run.prompt), image_ids)
    if result.total_usd is None:
        return None
    return {"usd": round(result.total_usd, 6), "basis": "estimate", "note": _COST_NOTE}


# -- ツール本体 --------------------------------------------------------------


async def get_capabilities(ctx: Context) -> CallToolResult:
    """List the image providers, models, parameters and size constraints GAKEI accepts.

    Use the returned `default_provider`, each provider's `default_model`, and the per-model
    parameter definitions to build a `generate_image` call. The size is not in the per-model
    parameter list: put it in params.size as "WIDTHxHEIGHT" (e.g. {"size": "1024x1024"}) within
    the limits in providers[].size. `usage_notes` and `example_generate_image` show how.
    """
    mc = get_mcp_context(ctx)
    caps = await _in_thread(lambda: get_capabilities_endpoint(mc.state.registry))
    payload = caps.model_dump(mode="json")
    default = next((p for p in caps.providers if p.provider == caps.default_provider), None)
    example_params: dict[str, Any] = {}
    if default is not None and default.size is not None:
        example_params["size"] = default.default_size or "1024x1024"
    if default is not None:
        model_caps = next((m for m in default.models if m.model == default.default_model), None)
        if model_caps is not None and "low" in model_caps.quality_choices:
            example_params["quality"] = "low"
    payload["usage_notes"] = [
        _SIZE_HINT,
        "Other parameters listed under models[].operations[].params also go in params "
        "(e.g. quality, background, output_format, n).",
        "generate_image returns right away with a run_id; wait with get_run(wait_seconds).",
        "estimate_cost gives a reference price before running (set quality and size to get "
        "a number; 'auto' cannot be estimated).",
    ]
    payload["example_generate_image"] = {
        "prompt": "a lighthouse at dusk",
        "operation": "generate",
        **({"model": default.default_model} if default is not None else {}),
        "params": example_params,
    }
    return _result(payload)


def _create_mcp_run(mc: McpRequestContext, body: RunCreateRequest) -> uuid.UUID:
    with _session(mc) as db:
        limit = mcp_settings.hourly_run_limit(db)
        if limit == 0:
            raise ToolError(
                "Image generation through MCP is turned off by the GAKEI administrator "
                "(hourly limit is 0). Read-only tools are still available."
            )
        recent = mcp_settings.count_recent_mcp_runs(db)
        if recent >= limit:
            raise ToolError(
                f"The MCP generation limit has been reached ({recent}/{limit} runs in the "
                "last hour). No run was created. Try again later or ask the GAKEI "
                "administrator to raise the limit."
            )
        try:
            run = run_create_domain.create_run(
                db,
                mc.state.registry,
                mc.state.runner,
                mc.state.settings,
                body,
                viewer=mc.user,
                origin=run_create_domain.RunOrigin(
                    origin=mcp_settings.ORIGIN_MCP, api_token_id=mc.api_token_id
                ),
            )
        except run_create_domain.RunCreateError as e:
            raise ToolError(str(e)) from e
        return run.id


async def generate_image(
    ctx: Context,
    prompt: Annotated[str, Field(description="Text prompt describing the image or the edit.")],
    operation: Annotated[
        Literal["generate", "edit"],
        Field(description="'generate' creates from text; 'edit' uses input_asset_ids."),
    ] = "generate",
    model: Annotated[
        str | None,
        Field(description="Model ID from get_capabilities. Defaults to the provider default."),
    ] = None,
    provider: Annotated[
        str | None,
        Field(description="Provider name from get_capabilities. Defaults to the primary one."),
    ] = None,
    params: Annotated[
        dict[str, Any] | None,
        Field(
            description=(
                "Model parameters as listed by get_capabilities, e.g. "
                '{"size": "1024x1024", "quality": "low", "n": 1}. The size goes here as '
                'params.size ("WIDTHxHEIGHT" or "auto"), not as a separate argument.'
            )
        ),
    ] = None,
    input_asset_ids: Annotated[
        list[uuid.UUID] | None,
        Field(
            description=(
                "Input images for 'edit' (up to 16). The first one is the primary parent. "
                "To edit a previous result, pass its output asset_id here directly; do not "
                "download and re-upload it (that loses the lineage and wastes a transfer)."
            ),
            max_length=16,
        ),
    ] = None,
    mask_asset_id: Annotated[
        uuid.UUID | None,
        Field(description="Optional mask asset for 'edit' (same size as the first input)."),
    ] = None,
    group_id: Annotated[
        uuid.UUID | None, Field(description="Put the outputs into this group (see list_groups).")
    ] = None,
    wait: Annotated[
        bool,
        Field(
            description=(
                f"If true, wait up to {WAIT_MAX_SECONDS} seconds for the run to finish before "
                "returning. Either way the run_id is always returned; by default the call "
                "returns right away and you wait with get_run(wait_seconds)."
            )
        ),
    ] = False,
    include_thumbnails: Annotated[
        bool, Field(description="Attach 512px JPEG/PNG thumbnails of the outputs.")
    ] = True,
    include_lineage: Annotated[
        bool,
        Field(
            description=(
                "Include lineage_mermaid: a Mermaid flowchart of the input images' ancestry "
                "-> this run -> its outputs."
            )
        ),
    ] = True,
) -> CallToolResult:
    """Generate or edit images with GAKEI. Each call creates a new run that is BILLED to the
    GAKEI operator's image API account, and is recorded with its lineage.

    Returns right away with the run_id and status (queued/running); then call get_run with
    wait_seconds (up to 25 s per call, repeat as needed) until it has finished. Put the size in
    params.size, e.g. {"size": "1024x1024", "quality": "low"}. Use estimate_cost first to see
    a reference price. The number of runs created through MCP per hour is limited by the
    administrator; the result includes the remaining quota, and when the limit is reached no
    run is created and an error is returned. Outputs are returned as asset IDs (and optional
    512px thumbnails). To look at an output in detail use get_image; to get the original file
    use create_download_url. The url / viewer_url fields are for the user's browser.
    """
    mc = get_mcp_context(ctx)

    def _resolve_model() -> str:
        registry = mc.state.registry
        name = provider or registry.primary
        p = registry.get(name)
        if p is None:
            raise ToolError(f"Unknown provider: {name}")
        return p.capabilities().default_model

    resolved_model = model or await _in_thread(_resolve_model)
    inputs = [
        RunInputCreate(asset_id=asset_id, role="image", position=i)
        for i, asset_id in enumerate(input_asset_ids or [])
    ]
    if mask_asset_id is not None:
        inputs.append(RunInputCreate(asset_id=mask_asset_id, role="mask", position=0))
    body = RunCreateRequest(
        operation=operation,
        model=resolved_model,
        prompt=prompt,
        provider=provider,
        params=params or {},
        inputs=inputs,
        asset_group_id=group_id,
    )
    run_id = await _in_thread(lambda: _create_mcp_run(mc, body))
    # ここから先は Run ができている。何が起きても run_id を失わないよう、エラーにせず返す。
    try:
        if wait:
            await _wait_for_terminal(mc, run_id, WAIT_MAX_SECONDS)
        return await _run_result(
            mc, run_id, include_thumbnails, with_quota=True, include_lineage=include_lineage
        )
    except Exception:  # noqa: BLE001
        return _result(
            {
                "run_id": str(run_id),
                "status": "unknown",
                "note": (
                    "The run was created, but its details could not be loaded. Call get_run "
                    "with this run_id to check it."
                ),
            }
        )


async def get_run(
    ctx: Context,
    run_id: Annotated[uuid.UUID, Field(description="Run ID returned by generate_image.")],
    wait_seconds: Annotated[
        float,
        Field(
            ge=0,
            description=(
                "Wait up to this many seconds for the run to finish (0 = do not wait). Values "
                f"above {WAIT_MAX_SECONDS} are treated as {WAIT_MAX_SECONDS}; call again if it "
                "is still queued or running."
            ),
        ),
    ] = 0,
    include_thumbnails: Annotated[
        bool, Field(description="Attach 512px JPEG/PNG thumbnails of the outputs.")
    ] = True,
    include_lineage: Annotated[
        bool,
        Field(
            description=(
                "Include lineage_mermaid: a Mermaid flowchart of the input images' ancestry "
                "-> this run -> its outputs."
            )
        ),
    ] = True,
) -> CallToolResult:
    """Get a run's status, parameters, outputs, reference cost and the remaining hourly quota.
    Optionally wait (up to 25 s per call) for it to finish. lineage_mermaid is a Mermaid
    flowchart of the inputs' ancestry -> this run -> its outputs. text_outputs holds text the
    workflow produced at run time (role "final_prompt": the prompt rewritten by a ComfyUI
    prompt enhancer), or null."""
    mc = get_mcp_context(ctx)
    if wait_seconds > 0:
        await _wait_for_terminal(mc, run_id, wait_seconds)
    return await _run_result(
        mc, run_id, include_thumbnails, with_quota=True, include_lineage=include_lineage
    )


async def cancel_run(
    ctx: Context,
    run_id: Annotated[uuid.UUID, Field(description="Run ID to cancel.")],
) -> CallToolResult:
    """Cancel a run that is still waiting in the queue. A run that has already started
    cannot be canceled."""
    mc = get_mcp_context(ctx)

    def _cancel() -> None:
        with _session(mc) as db:
            run = get_visible_run(db, mc.user, run_id)
            if run is None or run.deleted_at is not None:
                raise ToolError(f"Run {run_id} not found.")
            if run.status != RunStatus.QUEUED:
                raise ToolError(f"Run {run_id} cannot be canceled (status: {run.status}).")
            result = db.execute(
                update(Run)
                .where(Run.id == run_id, Run.status == RunStatus.QUEUED)
                .values(status=RunStatus.CANCELED, finished_at=datetime.now(UTC))
            )
            db.commit()
            if result.rowcount == 0:
                raise ToolError(f"Run {run_id} has already started and cannot be canceled.")

    await _in_thread(_cancel)
    await mc.state.progress_bus.publish(run_id, {"type": "status", "status": "canceled"})
    return _result({"run_id": str(run_id), "status": "canceled"})


async def search_assets(
    ctx: Context,
    query: Annotated[
        str | None,
        Field(description="Words to match in the generating prompt (space-separated, AND)."),
    ] = None,
    kind: Annotated[
        Literal["upload", "generated", "mask", "sketch"] | None,
        Field(description="Only return assets of this kind."),
    ] = None,
    group_id: Annotated[uuid.UUID | None, Field(description="Only assets in this group.")] = None,
    tag: Annotated[
        str | None,
        Field(description="Only assets with this tag (case-insensitive, exact tag name)."),
    ] = None,
    limit: Annotated[int, Field(ge=1, le=SEARCH_LIMIT_MAX)] = 20,
    include_thumbnails: Annotated[
        bool, Field(description="Attach 512px JPEG/PNG thumbnails of the results.")
    ] = False,
) -> CallToolResult:
    """Search or list images in the GAKEI stock (newest first). Deleted assets are excluded.

    The query matches the generating prompt, prompts embedded in uploaded images, titles and
    tag names. Results include each image's title and tags (`source` is "user" for tags a
    person added, "auto" for automatically estimated ones, which may be wrong).
    """
    mc = get_mcp_context(ctx)
    tag_value = tag.strip() if tag and tag.strip() else None

    def _search() -> tuple[dict[str, Any], list[ImageContent]]:
        with _session(mc) as db:
            if group_id is not None and get_visible_group(db, mc.user, group_id) is None:
                raise ToolError(f"Group {group_id} not found.")
            tag_clause = None
            if tag_value is not None:
                try:
                    tag_clause = annotations_domain.tag_filter(tag_value)
                except annotations_domain.TagNameError as e:
                    raise ToolError(str(e)) from e
            if query and query.strip():
                try:
                    hits = search(
                        db,
                        query,
                        viewer=mc.user,
                        limit=SEARCH_LIMIT_MAX,
                        types={"asset"},
                        tag=tag_value,
                    ).assets
                except InvalidSearchQueryError as e:
                    raise ToolError(str(e)) from e
                snippets = {h.id: h.prompt_snippet for h in hits}
                ids = [h.id for h in hits]
                assets_by_id = {
                    a.id: a
                    for a in db.execute(
                        select(Asset).where(Asset.id.in_(ids), asset_visible(mc.user))
                    )
                    .scalars()
                    .all()
                }
                assets = [assets_by_id[i] for i in ids if i in assets_by_id]
            else:
                snippets = {}
                q = select(Asset).where(Asset.deleted_at.is_(None), asset_visible(mc.user))
                if kind is not None:
                    q = q.where(Asset.kind == kind)
                if tag_clause is not None:
                    q = q.where(tag_clause)
                if group_id is not None:
                    q = q.join(
                        AssetGroupMember,
                        and_(
                            AssetGroupMember.asset_id == Asset.id,
                            AssetGroupMember.asset_group_id == group_id,
                        ),
                    )
                q = q.order_by(Asset.created_at.desc(), Asset.id.desc()).limit(limit + 1)
                assets = list(db.execute(q).scalars().all())

            if query and query.strip():
                if kind is not None:
                    assets = [a for a in assets if a.kind == kind]
                if group_id is not None:
                    member_ids = set(
                        db.execute(
                            select(AssetGroupMember.asset_id).where(
                                AssetGroupMember.asset_group_id == group_id
                            )
                        )
                        .scalars()
                        .all()
                    )
                    assets = [a for a in assets if a.id in member_ids]

            truncated = len(assets) > limit
            assets = assets[:limit]
            asset_ids = [a.id for a in assets]
            titles = annotations_domain.bulk_titles(db, asset_ids)
            tags_map = annotations_domain.bulk_tags(db, asset_ids)
            items = []
            for asset in assets:
                item = _asset_brief(mc, asset)
                item["title"] = titles.get(asset.id)
                item["tags"] = [ref.model_dump(mode="json") for ref in tags_map.get(asset.id, [])]
                group = group_for_asset(db, asset.id, mc.user)
                item["group"] = group.model_dump(mode="json") if group else None
                if asset.id in snippets:
                    item["prompt_snippet"] = snippets[asset.id]
                items.append(item)
            images: list[ImageContent] = []
            if include_thumbnails:
                for asset in assets:
                    thumb = _thumbnail(mc.state.store, asset)
                    if thumb is not None:
                        images.append(thumb)
            return {"items": items, "truncated": truncated}, images

    payload, images = await _in_thread(_search)
    return _result(payload, images)


async def get_asset(
    ctx: Context,
    asset_id: Annotated[uuid.UUID, Field(description="Asset ID.")],
    include_thumbnail: Annotated[
        bool, Field(description="Attach a 512px JPEG/PNG thumbnail.")
    ] = True,
    include_lineage: Annotated[
        bool,
        Field(
            description=(
                "Include lineage_mermaid: a Mermaid flowchart of this image's ancestors and "
                "descendants (a few generations each way)."
            )
        ),
    ] = True,
) -> CallToolResult:
    """Get an image's metadata: kind, size, title, tags, transparency (has_alpha and
    transparent_ratio, counted on the original image), group, the run that produced it
    (prompt, model, parameters, and text_outputs such as the final prompt rewritten by a
    ComfyUI prompt enhancer), its primary parent image, and lineage_mermaid (a Mermaid
    flowchart of its ancestors and descendants). The attached thumbnail is
    512px; call get_image to look at the image in more detail, or create_download_url to get
    the original file.

    `title_source` and each tag's `source` are "user" when a person set them and "auto" when
    they were estimated automatically (which may be wrong)."""
    mc = get_mcp_context(ctx)

    def _get() -> tuple[dict[str, Any], list[ImageContent]]:
        with _session(mc) as db:
            asset = get_visible_asset(db, mc.user, asset_id)
            if asset is None:
                raise ToolError(f"Asset {asset_id} not found.")
            payload = _asset_brief(mc, asset)
            payload.update(_transparency(mc.state.store, asset))
            fields = annotations_domain.annotation_fields(db, asset.id)
            payload["title"] = fields["title"]
            payload["title_source"] = fields["title_source"]
            payload["tags"] = [ref.model_dump(mode="json") for ref in fields["tags"]]
            group = group_for_asset(db, asset.id, mc.user)
            payload["group"] = group.model_dump(mode="json") if group else None
            payload["produced_by_run"] = None
            payload["primary_parent_asset_id"] = None
            if asset.produced_by_run_id is not None:
                run = db.get(Run, asset.produced_by_run_id)
                if run is not None:
                    payload["produced_by_run"] = {
                        "run_id": str(run.id),
                        "status": str(run.status),
                        "operation": str(run.operation),
                        "provider": run.provider,
                        "model": run.model,
                        "prompt": run.prompt,
                        "params": run.params or {},
                        "origin": run.origin,
                        "text_outputs": _text_outputs_payload(run),
                    }
                    parent = db.execute(
                        select(RunInput.asset_id).where(
                            RunInput.run_id == run.id,
                            RunInput.role == RunInputRole.IMAGE,
                            RunInput.position == 0,
                        )
                    ).scalar_one_or_none()
                    if parent is not None and not visible_asset_ids(db, mc.user, [parent]):
                        parent = None
                    payload["primary_parent_asset_id"] = str(parent) if parent else None
            if include_lineage:
                payload["lineage_mermaid"] = _asset_lineage_mermaid(mc, db, asset.id)
            images: list[ImageContent] = []
            if include_thumbnail:
                thumb = _thumbnail(mc.state.store, asset)
                if thumb is not None:
                    images.append(thumb)
            return payload, images

    payload, images = await _in_thread(_get)
    return _result(payload, images)


async def get_image(
    ctx: Context,
    asset_id: Annotated[uuid.UUID, Field(description="Asset ID.")],
    size: Annotated[
        agent_images.ImageSize,
        Field(
            description=(
                f"'large' = up to {agent_images.LARGE_LONG_EDGE}px on the long edge (default; "
                f"enough to see details), 'small' = up to {agent_images.SMALL_LONG_EDGE}px. "
                "Images smaller than that are returned at their own size (never enlarged)."
            )
        ),
    ] = "large",
) -> CallToolResult:
    """Look at an image: returns it in the tool result so you can see it, wherever you run.

    Images with transparency (any pixel with alpha < 255) come back as PNG, others as JPEG.
    To keep the result under about 1 MB, JPEG quality is lowered and, if still too large,
    the image is scaled down (PNG is only scaled down); the actual width and height are in
    the result. This is for viewing, not for processing: to get the original file (e.g. to
    edit it with a local tool), use create_download_url. Do not try to fetch the url /
    viewer_url fields to see an image; they are for the user's browser."""
    mc = get_mcp_context(ctx)

    def _load() -> tuple[dict[str, Any], ImageContent]:
        with _session(mc) as db:
            asset = get_visible_asset(db, mc.user, asset_id)
            if asset is None:
                raise ToolError(f"Asset {asset_id} not found.")
            db.expunge(asset)
        store: AssetStore = mc.state.store
        transparency = _transparency(store, asset)
        ratio = transparency["transparent_ratio"]
        # 原本から数えた透過(`get_asset` と同じ)で決める。原本が読めなければ画素から判定する。
        transparent = None if ratio is None else ratio > 0
        try:
            image = agent_images.render_asset(store, asset, size, transparent=transparent)
        except (OSError, ValueError) as e:
            raise ToolError(f"The image of asset {asset_id} could not be read.") from e
        if image is None:
            raise ToolError(f"The image file of asset {asset_id} is missing.")
        payload = {
            "asset_id": str(asset.id),
            "size": size,
            "mime_type": image.mime_type,
            "width": image.width,
            "height": image.height,
            "original_width": asset.width,
            "original_height": asset.height,
            "original_mime": asset.mime,
            **transparency,
            **_asset_urls(mc, asset.id),
        }
        return payload, _image_content(image)

    payload, image = await _in_thread(_load)
    return _result(payload, [image])


async def create_download_url(
    ctx: Context,
    asset_id: Annotated[uuid.UUID, Field(description="Asset ID of the image to download.")],
) -> CallToolResult:
    """Get a one-time URL for downloading the ORIGINAL image file (valid for 10 minutes, one
    download), e.g. to process it with a local tool.

    Fetch it with a tool that runs on the user's machine, e.g. `curl --fail -o image.png
    <download_url>`. No Authorization header is needed: the URL itself grants the download.
    PNG files include GAKEI's lineage metadata. GAKEI usually runs inside the user's LAN, so
    tools that run in the cloud (web fetch, cloud code execution) cannot reach this URL, and
    there is no way to hand the original to them. To just look at an image, use get_image.
    To edit an image again in GAKEI, do not download it: pass its asset_id to generate_image's
    input_asset_ids."""
    mc = get_mcp_context(ctx)

    def _issue() -> tuple[str, datetime, dict[str, Any]]:
        with _session(mc) as db:
            asset = get_visible_asset(db, mc.user, asset_id)
            if asset is None:
                raise ToolError(f"Asset {asset_id} not found.")
            info = {
                "asset_id": str(asset.id),
                "mime": asset.mime,
                "width": asset.width,
                "height": asset.height,
                "bytes": asset.bytes,
            }
            row, raw = download_tickets_domain.issue_ticket(
                db, asset_id=asset.id, user_id=mc.user.id, api_token_id=mc.api_token_id
            )
            db.commit()
            return raw, row.expires_at, info

    raw, expires_at, info = await _in_thread(_issue)
    url = f"{mc.base_url}/api/downloads/{raw}"
    ttl = int(download_tickets_domain.TICKET_TTL / timedelta(seconds=1))
    ext = str(info["mime"]).split("/")[-1]
    return _result(
        {
            **info,
            "download_url": url,
            "method": "GET",
            "expires_at": expires_at.isoformat(),
            "expires_in_seconds": ttl,
            "curl_example": f"curl --fail -o '{info['asset_id']}.{ext}' '{url}'",
            "note": (
                "The URL works once and expires in 10 minutes. No Authorization header is "
                "needed. Fetch it from the user's machine; cloud tools cannot reach GAKEI "
                "inside a LAN. Expired or used URLs return 404; call create_download_url "
                "again for a new one."
            ),
        }
    )


def _transparency(store: AssetStore, asset: Asset) -> dict[str, Any]:
    """原本から数えた透過の情報(ADR-0023 7章 4)。原本が読めなければ null。"""
    try:
        stats = alpha_stats(asset.sha256, lambda: store.read(asset.blob_key))
    except (OSError, ValueError):
        return {"has_alpha": None, "transparent_ratio": None}
    return {"has_alpha": stats.has_alpha, "transparent_ratio": stats.transparent_ratio}


def _decode_image(data_base64: str) -> bytes:
    raw = data_base64.strip()
    if raw.startswith("data:"):
        # data URL(`data:image/png;base64,...`)も受ける。
        _, _, raw = raw.partition(",")
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as e:
        raise ToolError("data_base64 is not valid base64.") from e
    if not data:
        raise ToolError("data_base64 is empty.")
    return data


async def upload_image(
    ctx: Context,
    data_base64: Annotated[
        str,
        Field(
            description=(
                "The image file (PNG, JPEG or WebP) encoded as base64 (a data: URL is also "
                f"accepted). Maximum {MAX_UPLOAD_BYTES // (1024 * 1024)} MB before encoding."
            )
        ),
    ],
) -> CallToolResult:
    """Import a small image (sent inline as base64) into the GAKEI stock so it can be used as
    an input of generate_image (operation 'edit'). Returns the new asset ID. For anything
    larger than a few hundred KB, use create_upload_url and send the file with curl instead."""
    mc = get_mcp_context(ctx)
    data = _decode_image(data_base64)
    if len(data) >= MAX_UPLOAD_BYTES:
        raise ToolError(f"The image is too large (limit {MAX_UPLOAD_BYTES // (1024 * 1024)} MB).")

    def _ingest() -> tuple[dict[str, Any], str]:
        with _session(mc) as db:
            try:
                result = ingest_upload(
                    db,
                    mc.state.store,
                    data,
                    AssetKind.UPLOAD,
                    viewer=mc.user,
                )
            except IngestError as e:
                db.rollback()
                raise ToolError(str(e)) from e
            # ADR-0024 4章: 取り込み時の自動推定(設定がオンで、新しく作ったときだけ)。
            queued = result.outcome == "created" and annotations_domain.enqueue_on_ingest(
                db, result.asset, mc.state.settings
            )
            db.commit()
            if queued:
                mc.state.annotator.notify()
            return _asset_brief(mc, result.asset), result.outcome

    payload, outcome = await _in_thread(_ingest)
    payload["ingest_outcome"] = outcome
    return _result(payload)


async def list_prompt_sets(ctx: Context) -> CallToolResult:
    """List the saved prompt sets and their prompts."""
    mc = get_mcp_context(ctx)

    def _list() -> dict[str, Any]:
        with _session(mc) as db:
            return list_prompt_sets_rest(db, mc.user).model_dump(mode="json")

    return _result(await _in_thread(_list))


async def list_groups(ctx: Context) -> CallToolResult:
    """List the groups used to organize the stock (with member counts)."""
    mc = get_mcp_context(ctx)

    def _list() -> dict[str, Any]:
        with _session(mc) as db:
            return {"items": [g.model_dump(mode="json") for g in list_groups_domain(db, mc.user)]}

    return _result(await _in_thread(_list))


async def create_group(
    ctx: Context,
    name: Annotated[str, Field(description="Group name.")],
) -> CallToolResult:
    """Create a new group to organize images."""
    mc = get_mcp_context(ctx)
    try:
        normalized = AssetGroupCreate(name=name).name
    except ValidationError as e:
        raise ToolError(e.errors()[0].get("msg", "Invalid group name.")) from e

    def _create() -> dict[str, Any]:
        with _session(mc) as db:
            row = create_group_domain(db, normalized, mc.user)
            db.commit()
            return row.model_dump(mode="json")

    return _result(await _in_thread(_create))


async def move_to_group(
    ctx: Context,
    group_id: Annotated[uuid.UUID, Field(description="Destination group ID.")],
    asset_ids: Annotated[
        list[uuid.UUID], Field(min_length=1, max_length=200, description="Assets to move.")
    ],
) -> CallToolResult:
    """Move images into a group. An image belongs to at most one group, so it is removed from
    its previous group."""
    mc = get_mcp_context(ctx)

    def _move() -> dict[str, Any]:
        with _session(mc) as db:
            group = get_visible_group(db, mc.user, group_id)
            if group is None:
                raise ToolError(f"Group {group_id} not found.")
            try:
                row = add_members(db, group, asset_ids, viewer=mc.user)
            except AssetGroupAssetsMissingError as e:
                db.rollback()
                ids = ", ".join(str(i) for i in e.missing_ids)
                raise ToolError(f"Assets not found: {ids}") from e
            db.commit()
            return row.model_dump(mode="json")

    return _result(await _in_thread(_move))


async def list_runs(
    ctx: Context,
    origin: Annotated[
        Literal["mcp", "web", "any"],
        Field(description="'mcp' = created through MCP, 'web' = created in the web UI."),
    ] = "any",
    since: Annotated[
        datetime | None,
        Field(
            description=(
                "Only runs queued at or after this time (ISO 8601, e.g. 2026-09-28T10:00:00Z; "
                "a time without an offset is UTC)."
            )
        ),
    ] = None,
    status: Annotated[
        list[Literal["queued", "running", "succeeded", "failed", "canceled"]] | None,
        Field(description="Only runs in one of these states."),
    ] = None,
    limit: Annotated[int, Field(ge=1, le=LIST_RUNS_LIMIT_MAX)] = 10,
    include_thumbnails: Annotated[
        bool, Field(description="Attach 512px JPEG/PNG thumbnails of the outputs.")
    ] = False,
) -> CallToolResult:
    """List your recent runs (newest first) with their status, outputs and reference cost, plus
    the remaining hourly quota. Only runs created by you (the owner of the access token) are
    listed; in personal mode (no login) every run is yours. Use it to find a run whose run_id
    you lost, e.g. list_runs(origin="mcp", since="2026-09-28T10:00:00Z"). Deleted runs are
    excluded."""
    mc = get_mcp_context(ctx)

    def _list() -> tuple[dict[str, Any], list[ImageContent]]:
        with _session(mc) as db:
            # ADR-0025: 常に本人の Run だけ(個人モードは全件が本人のもの)。
            q = select(Run).where(Run.deleted_at.is_(None), run_visible(mc.user))
            if origin == "mcp":
                q = q.where(Run.origin == mcp_settings.ORIGIN_MCP)
            elif origin == "web":
                q = q.where(Run.origin.is_(None))
            if since is not None:
                moment = since if since.tzinfo is not None else since.replace(tzinfo=UTC)
                q = q.where(Run.queued_at >= moment.astimezone(UTC))
            if status:
                q = q.where(Run.status.in_(status))
            q = q.order_by(Run.queued_at.desc(), Run.id.desc()).limit(limit + 1)
            runs = list(db.execute(q).scalars().all())
            truncated = len(runs) > limit
            items: list[dict[str, Any]] = []
            images: list[ImageContent] = []
            for run in runs[:limit]:
                payload, thumbs = _run_payload(mc, db, run, include_thumbnails)
                payload.pop("note", None)
                items.append(payload)
                images.extend(thumbs)
            return {"items": items, "truncated": truncated, "quota": _quota(db)}, images

    payload, images = await _in_thread(_list)
    return _result(payload, images)


async def create_upload_url(ctx: Context) -> CallToolResult:
    """Get a one-time URL for uploading a local image file (valid for 10 minutes, one upload).

    Send the file bytes with an HTTP PUT, e.g. `curl --fail-with-body -X PUT --data-binary
    @image.png <upload_url>` (a multipart POST with `curl -F file=@image.png <upload_url>` also
    works). No Authorization header is needed: the URL itself grants the upload. The response
    is JSON with the new asset_id, which you can pass to generate_image as an edit input.
    Prefer this over upload_image for anything but tiny images. Only upload images that are
    not in GAKEI yet: to edit a GAKEI image (e.g. a previous result), pass its asset_id to
    generate_image's input_asset_ids instead."""
    mc = get_mcp_context(ctx)

    def _issue() -> tuple[str, datetime]:
        with _session(mc) as db:
            row, raw = upload_tickets_domain.issue_ticket(
                db, user_id=mc.user.id, api_token_id=mc.api_token_id
            )
            db.commit()
            return raw, row.expires_at

    raw, expires_at = await _in_thread(_issue)
    url = f"{mc.base_url}/api/uploads/{raw}"
    ttl = int(upload_tickets_domain.TICKET_TTL / timedelta(seconds=1))
    return _result(
        {
            "upload_url": url,
            "method": "PUT",
            "expires_at": expires_at.isoformat(),
            "expires_in_seconds": ttl,
            "max_bytes": MAX_UPLOAD_BYTES - 1,
            "accepted_formats": ["image/png", "image/jpeg", "image/webp"],
            "curl_example": f"curl --fail-with-body -X PUT --data-binary @image.png '{url}'",
            "curl_multipart_example": f"curl --fail-with-body -F file=@image.png '{url}'",
            "note": (
                "The URL works once and expires in 10 minutes. No Authorization header is "
                "needed. The JSON response contains asset_id. If the upload is rejected "
                "(not an image, too large), the URL can be used again until it expires."
            ),
        }
    )


_UNAVAILABLE_MESSAGES = {
    "quality_auto": "quality is 'auto' (or not set); set params.quality (e.g. 'low').",
    "size_auto": "size is 'auto' (or not set); set params.size (e.g. '1024x1024').",
    "size_invalid": "params.size is not a valid size for this model.",
    "unknown_model": "No price table for this model.",
    "provider_not_supported": "This provider has no reference prices (e.g. a local ComfyUI).",
}


async def estimate_cost(
    ctx: Context,
    provider: Annotated[
        str | None,
        Field(description="Provider name from get_capabilities. Defaults to the primary one."),
    ] = None,
    model: Annotated[
        str | None,
        Field(description="Model ID from get_capabilities. Defaults to the provider default."),
    ] = None,
    params: Annotated[
        dict[str, Any] | None,
        Field(
            description=(
                "The same params you would pass to generate_image, e.g. "
                '{"size": "1024x1024", "quality": "low"}. quality and size are required for a '
                "number ('auto' cannot be estimated)."
            )
        ),
    ] = None,
    n: Annotated[
        int | None,
        Field(ge=1, le=10, description="Number of images. Defaults to params.n or 1."),
    ] = None,
    prompt: Annotated[
        str | None, Field(description="Optional prompt, to count its text tokens.")
    ] = None,
    input_asset_ids: Annotated[
        list[uuid.UUID] | None,
        Field(description="Optional edit input images, to count their tokens.", max_length=16),
    ] = None,
) -> CallToolResult:
    """Estimate the reference price (USD) of a generate_image call without running it. This is
    the same estimate the GAKEI web UI shows; it is not an invoice. When it cannot be
    estimated, total_usd is null and unavailable_reason explains why."""
    mc = get_mcp_context(ctx)
    values = dict(params or {})

    def _run() -> dict[str, Any]:
        registry = mc.state.registry
        name = provider or registry.primary
        p = registry.get(name)
        if p is None:
            raise ToolError(f"Unknown provider: {name}")
        resolved_model = model or p.capabilities().default_model
        count = n if n is not None else _params_n(values)
        base: dict[str, Any] = {
            "provider": name,
            "model": resolved_model,
            "n": count,
            "currency": "USD",
            "note": _COST_NOTE,
        }
        if not getattr(p, "supports_pricing", False):
            return {
                **base,
                "total_usd": None,
                "unavailable_reason": "provider_not_supported",
                "unavailable_message": _UNAVAILABLE_MESSAGES["provider_not_supported"],
            }
        with _session(mc) as db:
            result = _estimate(
                mc,
                db,
                resolved_model,
                values,
                count,
                len(prompt or ""),
                list(input_asset_ids or []),
            )
        reason = result.unavailable_reason
        prices = result.unit_prices
        return {
            **base,
            "total_usd": round(result.total_usd, 6) if result.total_usd is not None else None,
            "unavailable_reason": reason,
            "unavailable_message": _UNAVAILABLE_MESSAGES.get(reason) if reason else None,
            "output_tokens_per_image": result.output_tokens_per_image,
            "input_image_tokens": result.input_image_tokens,
            "text_tokens": result.text_tokens,
            "unit_prices_per_1m": (
                {
                    "text_input": prices.text_input,
                    "image_input": prices.image_input,
                    "image_output": prices.image_output,
                }
                if prices is not None
                else None
            ),
            "pricing_source": PRICING_SOURCE_URL,
            "pricing_checked_at": PRICING_CHECKED_AT,
        }

    return _result(await _in_thread(_run))


# -- 組み立て ------------------------------------------------------------------

_READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)


def build_mcp_server() -> MCPServer:
    server = MCPServer(name="gakei", version=get_version(), instructions=_INSTRUCTIONS)
    server.add_tool(get_capabilities, title="Get capabilities", annotations=_READ_ONLY)
    server.add_tool(
        generate_image,
        title="Generate image (billed)",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            # 外部の画像生成 API を呼ぶ(課金を伴う)。
            open_world_hint=True,
        ),
    )
    server.add_tool(get_run, title="Get run", annotations=_READ_ONLY)
    server.add_tool(list_runs, title="List runs", annotations=_READ_ONLY)
    server.add_tool(estimate_cost, title="Estimate cost", annotations=_READ_ONLY)
    server.add_tool(
        cancel_run,
        title="Cancel run",
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=False, idempotent_hint=True
        ),
    )
    server.add_tool(search_assets, title="Search assets", annotations=_READ_ONLY)
    server.add_tool(get_asset, title="Get asset", annotations=_READ_ONLY)
    server.add_tool(get_image, title="Get image", annotations=_READ_ONLY)
    server.add_tool(
        create_download_url,
        title="Create download URL",
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=False, idempotent_hint=False
        ),
    )
    server.add_tool(
        upload_image,
        title="Upload image",
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=False, idempotent_hint=False
        ),
    )
    server.add_tool(
        create_upload_url,
        title="Create upload URL",
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=False, idempotent_hint=False
        ),
    )
    server.add_tool(list_prompt_sets, title="List prompt sets", annotations=_READ_ONLY)
    server.add_tool(list_groups, title="List groups", annotations=_READ_ONLY)
    server.add_tool(
        create_group,
        title="Create group",
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=False, idempotent_hint=False
        ),
    )
    server.add_tool(
        move_to_group,
        title="Move to group",
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=False, idempotent_hint=True
        ),
    )
    return server


def build_session_manager(server: MCPServer) -> StreamableHTTPSessionManager:
    """stateless + JSON 応答のセッションマネージャー(ADR-0023 1章)。

    `streamable_http_app()` が返す Starlette アプリは使わず、そこで作られるセッション
    マネージャーだけを `/mcp`(`app.mcp.endpoint.McpEndpoint`)から呼ぶ。Host/Origin の判定は
    `McpEndpoint` で行うので、SDK の DNS リバインディング対策は無効にする。
    """
    server.streamable_http_app(
        stateless_http=True,
        json_response=True,
        max_request_body_size=MAX_REQUEST_BODY_BYTES,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    return server.session_manager
