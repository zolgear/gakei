"""MCP のツール(ADR-0023 3章)と、SDK のサーバー・セッションマネージャーの組み立て。

ツールは REST を HTTP で呼ばず、REST と同じドメイン関数を直接呼ぶ。削除と設定の変更は
提供しない。画像は Asset ID と URL で返し、本文に載せるのはサムネイル(512px WebP)だけ
(ADR-0004)。
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
from datetime import UTC, datetime
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
from app.domain import mcp_settings
from app.domain import run_create as run_create_domain
from app.domain.asset_groups import (
    AssetGroupAssetsMissingError,
    add_members,
    get_active_group_or_none,
    group_for_asset,
)
from app.domain.asset_groups import create_group as create_group_domain
from app.domain.asset_groups import list_groups as list_groups_domain
from app.domain.assets import MAX_UPLOAD_BYTES, IngestError, ingest_upload
from app.domain.models import (
    Asset,
    AssetGroupMember,
    AssetKind,
    Run,
    RunInput,
    RunInputRole,
    RunStatus,
)
from app.domain.schemas import AssetGroupCreate, RunCreateRequest, RunInputCreate
from app.domain.search import MAX_LIMIT as SEARCH_MAX_LIMIT
from app.domain.search import InvalidSearchQueryError, search
from app.domain.storage import AssetStore
from app.mcp.context import McpRequestContext, get_mcp_context
from app.version import get_version

# `generate_image` が完了を待つ上限(秒)。これを過ぎたら run_id と status を返し、
# `get_run` の `wait_seconds` で続きを待ってもらう。
WAIT_MAX_SECONDS = 240
_POLL_INTERVAL_SECONDS = 0.25

# 1回の検索・一覧で返す件数の上限。
SEARCH_LIMIT_MAX = SEARCH_MAX_LIMIT

# `upload_image` の本文の上限。REST のアップロードと同じ `MAX_UPLOAD_BYTES` を base64 に
# した大きさに、JSON-RPC の包みの分の余裕を足す。
MAX_REQUEST_BODY_BYTES = math.ceil(MAX_UPLOAD_BYTES / 3) * 4 + 1024 * 1024

_TERMINAL = {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELED}

_INSTRUCTIONS = (
    "GAKEI is a self-hosted image generation workspace. Use get_capabilities to see models "
    "and parameters, generate_image to create or edit images (this is billed to the GAKEI "
    "operator), search_assets / get_asset to find existing images, and groups to organize "
    "them. Images are referenced by asset ID; results include the original image URL and a "
    "small thumbnail. Deleting and changing settings are only possible in the GAKEI web UI."
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


def _thumbnail(store: AssetStore, asset: Asset) -> ImageContent | None:
    """サムネイル(512px WebP の派生画像)。原本・プレビューは載せない(ADR-0004)。"""
    path = store.content_path(asset.blob_key, asset.sha256, "thumb")
    if not path.is_file():
        return None
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return ImageContent(type="image", data=data, mime_type="image/webp")


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


def _run_payload(
    mc: McpRequestContext, db: Session, run: Run, include_thumbnails: bool
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
    group = None
    if run.asset_group_id is not None:
        g = get_active_group_or_none(db, run.asset_group_id)
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
        "asset_group": group,
        "inputs": [
            {"asset_id": str(i.asset_id), "role": str(i.role), "position": i.position}
            for i in inputs
        ],
        "outputs": [_asset_brief(mc, a) for a in outputs],
    }
    if run.status not in _TERMINAL:
        payload["note"] = (
            "The run has not finished yet. Call get_run with wait_seconds to wait for it."
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
    mc: McpRequestContext, run_id: uuid.UUID, include_thumbnails: bool
) -> tuple[dict[str, Any], list[ImageContent]] | None:
    with _session(mc) as db:
        run = db.get(Run, run_id)
        if run is None or run.deleted_at is not None:
            return None
        return _run_payload(mc, db, run, include_thumbnails)


def _run_status(mc: McpRequestContext, run_id: uuid.UUID) -> RunStatus | None:
    with _session(mc) as db:
        run = db.get(Run, run_id)
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
    mc: McpRequestContext, run_id: uuid.UUID, include_thumbnails: bool
) -> CallToolResult:
    loaded = await _in_thread(lambda: _load_run_result(mc, run_id, include_thumbnails))
    if loaded is None:
        raise ToolError(f"Run {run_id} not found.")
    payload, images = loaded
    return _result(payload, images)


# -- ツール本体 --------------------------------------------------------------


async def get_capabilities(ctx: Context) -> CallToolResult:
    """List the image providers, models, parameters and size constraints GAKEI accepts.

    Use the returned `default_provider`, each provider's `default_model`, and the per-model
    parameter definitions to build a `generate_image` call.
    """
    mc = get_mcp_context(ctx)
    caps = await _in_thread(lambda: get_capabilities_endpoint(mc.state.registry))
    return _result(caps.model_dump(mode="json"))


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
                created_by_user_id=mc.user.id,
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
            description="Model parameters (e.g. size, quality, n) as listed by get_capabilities."
        ),
    ] = None,
    input_asset_ids: Annotated[
        list[uuid.UUID] | None,
        Field(
            description="Input images for 'edit' (up to 16). The first one is the primary parent.",
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
                f"Wait up to {WAIT_MAX_SECONDS} seconds for the run to finish. If it has not "
                "finished by then, the run_id and current status are returned."
            )
        ),
    ] = True,
    include_thumbnails: Annotated[
        bool, Field(description="Attach 512px WebP thumbnails of the outputs.")
    ] = True,
) -> CallToolResult:
    """Generate or edit images with GAKEI. Each call creates a new run that is BILLED to the
    GAKEI operator's image API account, and is recorded with its lineage.

    The number of runs created through MCP per hour is limited by the administrator; when the
    limit is reached, no run is created and an error is returned. Outputs are returned as
    asset IDs with the original image URL (and optional thumbnails).
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
    if wait:
        await _wait_for_terminal(mc, run_id, WAIT_MAX_SECONDS)
    return await _run_result(mc, run_id, include_thumbnails)


async def get_run(
    ctx: Context,
    run_id: Annotated[uuid.UUID, Field(description="Run ID returned by generate_image.")],
    wait_seconds: Annotated[
        float,
        Field(
            ge=0,
            le=WAIT_MAX_SECONDS,
            description="Wait up to this many seconds for the run to finish (0 = do not wait).",
        ),
    ] = 0,
    include_thumbnails: Annotated[
        bool, Field(description="Attach 512px WebP thumbnails of the outputs.")
    ] = True,
) -> CallToolResult:
    """Get a run's status, parameters and outputs. Optionally wait for it to finish."""
    mc = get_mcp_context(ctx)
    if wait_seconds > 0:
        await _wait_for_terminal(mc, run_id, wait_seconds)
    return await _run_result(mc, run_id, include_thumbnails)


async def cancel_run(
    ctx: Context,
    run_id: Annotated[uuid.UUID, Field(description="Run ID to cancel.")],
) -> CallToolResult:
    """Cancel a run that is still waiting in the queue. A run that has already started
    cannot be canceled."""
    mc = get_mcp_context(ctx)

    def _cancel() -> None:
        with _session(mc) as db:
            run = db.get(Run, run_id)
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
    limit: Annotated[int, Field(ge=1, le=SEARCH_LIMIT_MAX)] = 20,
    include_thumbnails: Annotated[
        bool, Field(description="Attach 512px WebP thumbnails of the results.")
    ] = False,
) -> CallToolResult:
    """Search or list images in the GAKEI stock (newest first). Deleted assets are excluded."""
    mc = get_mcp_context(ctx)

    def _search() -> tuple[dict[str, Any], list[ImageContent]]:
        with _session(mc) as db:
            if group_id is not None and get_active_group_or_none(db, group_id) is None:
                raise ToolError(f"Group {group_id} not found.")
            if query and query.strip():
                try:
                    hits = search(db, query, limit=SEARCH_LIMIT_MAX, types={"asset"}).assets
                except InvalidSearchQueryError as e:
                    raise ToolError(str(e)) from e
                snippets = {h.id: h.prompt_snippet for h in hits}
                ids = [h.id for h in hits]
                assets_by_id = {
                    a.id: a
                    for a in db.execute(select(Asset).where(Asset.id.in_(ids))).scalars().all()
                }
                assets = [assets_by_id[i] for i in ids if i in assets_by_id]
            else:
                snippets = {}
                q = select(Asset).where(Asset.deleted_at.is_(None))
                if kind is not None:
                    q = q.where(Asset.kind == kind)
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
            items = []
            for asset in assets:
                item = _asset_brief(mc, asset)
                group = group_for_asset(db, asset.id)
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
    include_thumbnail: Annotated[bool, Field(description="Attach a 512px WebP thumbnail.")] = True,
) -> CallToolResult:
    """Get an image's metadata: kind, size, group, the run that produced it (prompt, model,
    parameters), and its primary parent image."""
    mc = get_mcp_context(ctx)

    def _get() -> tuple[dict[str, Any], list[ImageContent]]:
        with _session(mc) as db:
            asset = db.get(Asset, asset_id)
            if asset is None:
                raise ToolError(f"Asset {asset_id} not found.")
            payload = _asset_brief(mc, asset)
            group = group_for_asset(db, asset.id)
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
                    }
                    parent = db.execute(
                        select(RunInput.asset_id).where(
                            RunInput.run_id == run.id,
                            RunInput.role == RunInputRole.IMAGE,
                            RunInput.position == 0,
                        )
                    ).scalar_one_or_none()
                    payload["primary_parent_asset_id"] = str(parent) if parent else None
            images: list[ImageContent] = []
            if include_thumbnail:
                thumb = _thumbnail(mc.state.store, asset)
                if thumb is not None:
                    images.append(thumb)
            return payload, images

    payload, images = await _in_thread(_get)
    return _result(payload, images)


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
    """Import an image into the GAKEI stock so it can be used as an input of generate_image
    (operation 'edit'). Returns the new asset ID."""
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
                    created_by_user_id=mc.user.id,
                )
            except IngestError as e:
                db.rollback()
                raise ToolError(str(e)) from e
            db.commit()
            return _asset_brief(mc, result.asset), result.outcome

    payload, outcome = await _in_thread(_ingest)
    payload["ingest_outcome"] = outcome
    return _result(payload)


async def list_prompt_sets(ctx: Context) -> CallToolResult:
    """List the saved prompt sets and their prompts."""
    mc = get_mcp_context(ctx)

    def _list() -> dict[str, Any]:
        with _session(mc) as db:
            return list_prompt_sets_rest(db).model_dump(mode="json")

    return _result(await _in_thread(_list))


async def list_groups(ctx: Context) -> CallToolResult:
    """List the groups used to organize the stock (with member counts)."""
    mc = get_mcp_context(ctx)

    def _list() -> dict[str, Any]:
        with _session(mc) as db:
            return {"items": [g.model_dump(mode="json") for g in list_groups_domain(db)]}

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
            row = create_group_domain(db, normalized, created_by_user_id=mc.user.id)
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
            group = get_active_group_or_none(db, group_id)
            if group is None:
                raise ToolError(f"Group {group_id} not found.")
            try:
                row = add_members(db, group, asset_ids)
            except AssetGroupAssetsMissingError as e:
                db.rollback()
                ids = ", ".join(str(i) for i in e.missing_ids)
                raise ToolError(f"Assets not found: {ids}") from e
            db.commit()
            return row.model_dump(mode="json")

    return _result(await _in_thread(_move))


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
    server.add_tool(
        cancel_run,
        title="Cancel run",
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=False, idempotent_hint=True
        ),
    )
    server.add_tool(search_assets, title="Search assets", annotations=_READ_ONLY)
    server.add_tool(get_asset, title="Get asset", annotations=_READ_ONLY)
    server.add_tool(
        upload_image,
        title="Upload image",
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
