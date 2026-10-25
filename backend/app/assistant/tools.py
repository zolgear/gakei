"""アシスタントの道具(ADR-0048 4・5・6・9・10・11・12・13章)。

道具は REST を HTTP で呼ばず、REST と同じドメイン関数を直接呼ぶ(MCP と同じ方針。ADR-0023)。
扱えるのは会話の利用者に見えるものだけ(ADR-0025)。削除の道具は作らない。

- 読み取り: `list_prompt_sets`、`get_form_context`、`propose_prompt`、`get_capabilities`、
  `get_run`、`view_assets`
- 生成結果の読み取り: `view_result`(12章)。スタジオの結果エリアの画像(ブラウザが送る
  `form.result_asset_id`)か指定の画像(`asset_ids` で最大4枚)の、`get_asset` と同じ情報と
  縮めたプレビューを返す。`list_recent_results` は本人に見える Run を新しい順に返す(履歴の
  参照。中身は REST・MCP の Run の一覧と同じ `domain/run_views.py`)。画像の中身は入れない。
- プロンプトセットの書き込み: `create_prompt_set`、`rename_prompt_set`、`add_prompt_set_items`、
  `update_prompt_set_item`。書き込むたびに変更前の値を結果(`before`)に残し、「元に戻す」に
  要る値を `undo` に入れる(4章)。
- ストックの捜索: `search_assets`、`find_similar_assets`、`get_asset`、`list_groups`、
  `list_tags`、`show_assets`(10章)。読み取りだけ。中身は MCP の同名のツールと同じ
  `domain/asset_search.py` を呼ぶ。結果に画像の中身は入れない。
- 生成: `generate_image`、`cancel_run`。Run は `run.origin = 'assistant'`、実行者は会話の
  利用者。1回の発言で作れる Run の数と、1時間あたりの Run の数を超えたら、Run を作らずに
  道具のエラーを返す(9章)。
- ストックの整理: `add_tags`、`remove_tags`、`set_title`、`create_group`、`rename_group`、
  `move_to_group`、`remove_from_group`(13章)。中身と変更前の記録は `app/assistant/stock.py`。
  読み取りの `suggest_groups` はグループの提案(ADR-0056)と同じドメインの関数を呼ぶ。
- LLM に送る情報(14章): 一覧の道具(`search_assets`、`find_similar_assets`、
  `list_recent_results`、`list_tags`、`list_group_assets`)は1件を短い形にし、予算に収まる件数で
  切って `returned`・`total`・`next_offset` を返す(`offset` で続きを読む)。外部の接続先には、
  X 以上の画像の情報を ID・種類・段階・グループだけにする(`app/assistant/llm_view.py`)。
  `view_assets` と `view_result` の画像は、送る前に利用者の許可を求める(`service.py`)。
- 生成フォームの書き込み: `update_form`(11章)。検証して結果に入れるだけで、サーバーには
  保存しない。ブラウザが応答を受けている間に届いた結果をフォームに反映する。同じ発言の
  `get_form_context` と `generate_image` の補完には、変更を重ねた `ToolContext.form` が効く。

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
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.annotation.engines import image_to_jpeg
from app.api.capabilities import get_capabilities_endpoint
from app.assistant import llm_view
from app.assistant import stock as stock_domain
from app.assistant.engine import LlmImage, ToolCall, ToolSpec
from app.assistant.llm_view import Disclosure
from app.auth.identity import CurrentUser
from app.domain import annotations as annotations_domain
from app.domain import asset_search as asset_search_domain
from app.domain import assistant_settings, content_withholding, derivatives
from app.domain import content_rating as content_rating_domain
from app.domain import focal_points as focal_points_domain
from app.domain import group_suggestions as group_suggestions_domain
from app.domain import prompt_sets as prompt_sets_domain
from app.domain import run_create as run_create_domain
from app.domain import run_views as run_views_domain
from app.domain.asset_groups import group_for_asset
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
from app.domain.schemas import (
    ASSISTANT_ATTACHMENTS_MAX,
    REPEAT_MAX,
    AssistantFormInput,
    AssistantFormState,
    RunCreateRequest,
    RunInputCreate,
)
from app.domain.semantic_search import EmbeddingsConflictError
from app.domain.visibility import get_visible_asset, get_visible_group, get_visible_run
from app.i18n import t
from app.providers.base import ModelCapabilities, ProviderCapabilities, provider_status_snapshot

# `get_run` の1回の待ちの上限(秒。9章)。
WAIT_MAX_SECONDS = 25.0
_POLL_INTERVAL_SECONDS = 0.25
# `view_assets` と `view_result` で1回に見る枚数の上限(添付と同じ。6・12章)。
VIEW_ASSETS_MAX = ASSISTANT_ATTACHMENTS_MAX
# LLM に送る画像の長辺(派生のプレビューを縮める。6章)。
IMAGE_MAX_SIDE = 1024
# `search_assets` / `find_similar_assets` の件数の上限(MCP と同じ。10章)。
SEARCH_LIMIT_MAX = asset_search_domain.SEARCH_LIMIT_MAX
# `show_assets` で1枚のカードに並べる枚数の上限。
SHOW_ASSETS_MAX = 24
# `list_tags` の件数の上限。
LIST_TAGS_LIMIT_MAX = 200
# `list_group_assets` の件数の上限と既定(14.4。整理の道具の1回の上限と同じ)。
GROUP_ASSETS_LIMIT_MAX = 200
# `offset` の上限(検索の続きを読める範囲。14.4)。
OFFSET_MAX = asset_search_domain.SEARCH_WINDOW_MAX
# 画像を LLM に送る前に許可を求める道具(14.1)。利用者の添付は聞かない。
IMAGE_PERMISSION_TOOLS = frozenset({"view_assets", "view_result"})
# `update_form` で入れられる入力画像の上限(フォームの状態の上限と同じ)。
FORM_INPUTS_MAX = 32
# 1回の書き込みで足せる項目の上限。
ITEMS_MAX = 100
# `list_prompt_sets` で1項目の本文を返す上限(長い本文で履歴が埋まらないように)。
LIST_ITEM_TEXT_MAX = 2000
# `list_recent_results` の件数の上限と既定(12章)。
RECENT_RESULTS_LIMIT_MAX = 50
RECENT_RESULTS_LIMIT_DEFAULT = 10
# `list_recent_results` で1件のプロンプトを返す上限(文字数)。
RECENT_RESULTS_PROMPT_MAX = 300

# ストックの整理で1回に扱う画像の上限(13章)。
STOCK_ASSETS_MAX = stock_domain.STOCK_ASSETS_MAX
# `suggest_groups` で返す案の数の上限と既定、1つの案で返す画像の ID の数。
SUGGEST_GROUPS_LIMIT_MAX = 20
SUGGEST_GROUPS_LIMIT_DEFAULT = 8
SUGGEST_GROUPS_IDS_PREVIEW = 12
SUGGEST_GROUPS_IDS_MAX = 400

_TERMINAL = {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELED}
_COST_NOTE = "Reference price in USD computed by GAKEI, not an invoice."


class ToolFailure(Exception):
    """道具の失敗。メッセージは LLM にそのまま返す(LLM が利用者に伝える)。"""


class ImageWithheldFailure(ToolFailure):
    """`deny` の判定を受けた・画像を残さなかった Asset の画像は LLM に送らない(ADR-0051 4章)。"""


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
    # 接続先の `locality` と名前(14.3)。最初に使うときに管理者設定から引く。
    disclosure: Disclosure | None = None

    def session(self) -> Session:
        return self.state.session_factory()

    def get_disclosure(self) -> Disclosure:
        if self.disclosure is None:
            with self.session() as db:
                self.disclosure = llm_view.resolve_disclosure(db, self.state.settings, self.config)
        return self.disclosure


@dataclass
class ToolOutcome:
    ok: bool
    result: dict[str, Any]
    # LLM に見せる画像(保存しない)。
    images: list[LlmImage] = field(default_factory=list)
    # LLM に送る結果が画面用の `result` と違うとき(X 以上の画像の情報を落とす。14.3)。
    # 保存はするが画面には返さない。
    llm_result: dict[str, Any] | None = None
    # 画像を送る前の許可の答え(14.1)。許可を求めたときだけ。
    permission: dict[str, Any] | None = None
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


class SearchAssetsArgs(_Args):
    query: str | None = Field(
        default=None,
        max_length=1000,
        description=(
            "What to find. keyword: words to match in the generating prompt, titles and tags "
            "(space-separated, AND). semantic: a natural-language description of the image "
            "(required). hybrid: either. Omit to list the newest images."
        ),
    )
    mode: Literal["hybrid", "keyword", "semantic"] | None = Field(
        default=None,
        description=(
            "hybrid (default): combines keyword and semantic results into one ranking; each "
            "result says whether it matched by keyword, semantic or both. When embeddings are "
            "off, the default falls back to keyword and the result says so. keyword: exact "
            "words, newest first; use it when you know the exact words (a tag name, a word in "
            "the prompt). semantic: image embeddings, most similar first; use it for visual "
            "similarity to a description. semantic fails when embeddings are turned off."
        ),
    )
    kind: Literal["upload", "generated", "mask", "sketch", "composite", "regions"] | None = Field(
        default=None, description="Only images of this kind."
    )
    group_id: uuid.UUID | None = Field(
        default=None, description="Only images in this group (see list_groups)."
    )
    tag: str | None = Field(
        default=None,
        max_length=200,
        description="Only images with this tag (exact tag name, see list_tags).",
    )
    limit: int = Field(default=20, ge=1, le=SEARCH_LIMIT_MAX)
    offset: int = Field(
        default=0,
        ge=0,
        le=OFFSET_MAX,
        description="Skip this many results (use next_offset from the previous call).",
    )


class FindSimilarAssetsArgs(_Args):
    asset_id: uuid.UUID = Field(description="The image to compare with.")
    limit: int = Field(default=12, ge=1, le=SEARCH_LIMIT_MAX)
    offset: int = Field(
        default=0,
        ge=0,
        le=OFFSET_MAX,
        description="Skip this many results (use next_offset from the previous call).",
    )


class ListGroupAssetsArgs(_Args):
    group_id: uuid.UUID = Field(description="The group (see list_groups).")
    limit: int = Field(default=GROUP_ASSETS_LIMIT_MAX, ge=1, le=GROUP_ASSETS_LIMIT_MAX)
    offset: int = Field(
        default=0,
        ge=0,
        description="Skip this many images (use next_offset from the previous call).",
    )


class GetAssetArgs(_Args):
    asset_id: uuid.UUID


class ViewResultArgs(_Args):
    asset_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "One image to look at. Omit (and omit asset_ids) to look at the image shown in "
            "the studio's result area (the generation result the user is looking at)."
        ),
    )
    asset_ids: list[uuid.UUID] | None = Field(
        default=None,
        min_length=1,
        max_length=VIEW_ASSETS_MAX,
        description=(
            f"Several images to look at together, for example to compare results from the "
            f"history (up to {VIEW_ASSETS_MAX}). Do not combine with asset_id."
        ),
    )
    include_image: bool = Field(
        default=True,
        description=(
            "Send the image itself. Pass false to get only the text (tags, prompt, model, "
            "parameters), for example when the model cannot read images."
        ),
    )


class ListRecentResultsArgs(_Args):
    limit: int = Field(
        default=RECENT_RESULTS_LIMIT_DEFAULT,
        ge=1,
        le=RECENT_RESULTS_LIMIT_MAX,
        description="How many runs to return, newest first.",
    )
    group_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Only runs in this group (runs whose outputs were sent to it or are in it now). "
            "Get ids with list_groups."
        ),
    )
    offset: int = Field(
        default=0,
        ge=0,
        description="Skip this many runs (use next_offset from the previous call).",
    )


class ListTagsArgs(_Args):
    prefix: str | None = Field(
        default=None, max_length=200, description="Only tags starting with this text."
    )
    limit: int = Field(default=50, ge=1, le=LIST_TAGS_LIMIT_MAX)
    offset: int = Field(
        default=0,
        ge=0,
        description="Skip this many tags (use next_offset from the previous call).",
    )


class ShowAssetsArgs(_Args):
    asset_ids: list[uuid.UUID] = Field(
        min_length=1,
        max_length=SHOW_ASSETS_MAX,
        description=f"Images to show, in this order (up to {SHOW_ASSETS_MAX}).",
    )
    title: str | None = Field(
        default=None, max_length=100, description="Short title for the card (optional)."
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


class UpdateFormInputArg(_Args):
    # 参照画像(reference)は、生成フォームに欄が無いので受けない(入れても見えずに送られる)。
    asset_id: uuid.UUID
    role: Literal["image", "mask"] = Field(
        default="image",
        description="image (the first one is the primary parent) or mask (at most one).",
    )


class UpdateFormArgs(_Args):
    """指定した項目だけを変える(11章)。`asset_group_id` 以外は null を「指定なし」と同じに
    扱う。`asset_group_id` は null を渡すと「グループなし」にする(省略とは区別する)。"""

    provider: str | None = Field(
        default=None,
        max_length=64,
        description="Switch to this provider. The model becomes its default unless you pass model.",
    )
    model: str | None = Field(default=None, max_length=200, description="Switch to this model.")
    operation: Literal["generate", "edit"] | None = Field(
        default=None,
        description=(
            "generate clears the input images (unless you pass inputs). edit needs at least "
            "one input image (in the form or in inputs)."
        ),
    )
    prompt: str | None = Field(
        default=None, max_length=32_000, description="Replace the prompt (empty string clears it)."
    )
    params: dict[str, Any] | None = Field(
        default=None,
        description=(
            'Change only these parameters (see get_capabilities), e.g. {"quality": "low", '
            '"size": "1024x1536"}. A null value resets that parameter to the form\'s default.'
        ),
    )
    inputs: list[UpdateFormInputArg] | None = Field(
        default=None,
        max_length=FORM_INPUTS_MAX,
        description="Replace the input images. Pass [] to remove all input images.",
    )
    asset_group_id: uuid.UUID | None = Field(
        default=None,
        description="Put outputs into this group (see list_groups). Pass null for no group.",
    )


class _StockAssetsArg(_Args):
    asset_ids: list[uuid.UUID] = Field(
        min_length=1,
        max_length=STOCK_ASSETS_MAX,
        description=(
            f"Images to change (up to {STOCK_ASSETS_MAX} per call; split larger sets into "
            "several calls). The images the user selected in the stock are in "
            "get_form_context (selected_asset_ids)."
        ),
    )


class TagsArgs(_StockAssetsArg):
    tags: list[str] = Field(
        min_length=1,
        max_length=stock_domain.STOCK_TAGS_MAX,
        description="Tag names. Prefer tags that already exist (see list_tags).",
    )


class SetTitleArgs(_Args):
    asset_id: uuid.UUID
    title: str | None = Field(
        default=None,
        max_length=annotations_domain.TITLE_MAX,
        description="New title. Pass null or an empty string to clear it.",
    )


class CreateGroupArgs(_Args):
    name: str = Field(min_length=1, max_length=100)
    asset_ids: list[uuid.UUID] = Field(
        default_factory=list,
        max_length=STOCK_ASSETS_MAX,
        description=(
            "Images to put into the new group (optional, up to "
            f"{STOCK_ASSETS_MAX}). An image belongs to one group only, so they move out of "
            "their current group."
        ),
    )


class RenameGroupArgs(_Args):
    group_id: uuid.UUID
    name: str = Field(min_length=1, max_length=100)


class GroupAssetsArgs(_StockAssetsArg):
    group_id: uuid.UUID = Field(description="The group (see list_groups).")


class SuggestGroupsArgs(_Args):
    scope: Literal["ungrouped", "all"] = Field(
        default="ungrouped",
        description="ungrouped: only images that are in no group. all: include grouped images.",
    )
    granularity: Literal["normal", "fine"] = Field(
        default="normal", description="fine: split large clusters into smaller ones."
    )
    limit: int = Field(
        default=SUGGEST_GROUPS_LIMIT_DEFAULT,
        ge=1,
        le=SUGGEST_GROUPS_LIMIT_MAX,
        description="How many suggestions to return, largest first.",
    )
    suggestion_id: str | None = Field(
        default=None,
        max_length=200,
        description=(
            "Return only this suggestion (an id from an earlier call) with all its image ids "
            f"(up to {SUGGEST_GROUPS_IDS_MAX}). Other calls return only the first "
            f"{SUGGEST_GROUPS_IDS_PREVIEW} ids of each suggestion."
        ),
    )


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
    result: dict[str, Any] = {"available": True, **ctx.form.model_dump(mode="json")}
    if ctx.form.selected_asset_ids:
        result["selected_note"] = (
            f"The user has selected {len(ctx.form.selected_asset_ids)} images in the stock "
            "(selected_asset_ids). 'The selected images' means these."
        )
    return ToolOutcome(ok=True, result=result)


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
    input_ids = list(
        db.execute(select(RunInput.asset_id).where(RunInput.run_id == run.id)).scalars().all()
    )
    if ctx.get_disclosure().withheld_ids(db, [a.id for a in outputs] + input_ids):
        # X 以上の画像を作った・使った Run のプロンプトとパラメーターは送らない(14.3)。
        payload["prompt"] = None
        payload["params"] = None
        payload["withheld"] = True
        payload["withheld_note"] = llm_view.WITHHELD_NOTE
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
    """見える Asset の画像を読む。見えない・無いものは ToolFailure。

    外部の接続先に送らない X 以上の画像(14.3)は読まず、`briefs` にその1件
    (`withheld: true`)だけを入れる。`images` は送る画像だけ(`briefs` の並びから送らない
    ものを除いた順)。"""
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
        # ADR-0051 4章: `deny` の判定を受けた・画像を残さなかった画像は送らない。
        blocked = content_withholding.blocked_asset_ids(db, [a.id for a in assets])
        for asset in assets:
            if asset.id in blocked:
                raise ImageWithheldFailure(
                    f"The image of asset {asset.id} cannot be shown: a GAKEI Mod denied it "
                    "(content withheld or denied). Its text information is still available."
                )
        # レーティング(ADR-0049)。AI に渡す画像はマスクしない代わりに、段階を情報として渡す。
        ratings = content_rating_domain.bulk_levels(db, [a.id for a in assets])
        # 外部の接続先には X 以上の画像を送らない(14.3)。
        external = ctx.get_disclosure().external
        withheld = {
            a.id: llm_view.withheld_entry(
                a.id, a.kind, ratings.get(a.id), group_for_asset(db, a.id, ctx.user)
            )
            for a in assets
            if llm_view.withholds(ratings.get(a.id), external=external)
        }
    for asset in assets:
        if asset.id in withheld:
            briefs.append(withheld[asset.id])
            continue
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
                "content_rating": ratings.get(asset.id),
            }
        )
    return briefs, images


async def view_assets(ctx: ToolContext, args: ViewAssetsArgs) -> ToolOutcome:
    briefs, images = await _in_thread(lambda: load_visible_images(ctx, args.asset_ids))
    return ToolOutcome(
        ok=True, result=_view_note({"assets": briefs}, briefs, images), images=images
    )


def _view_note(
    result: dict[str, Any], briefs: list[dict[str, Any]], images: list[LlmImage]
) -> dict[str, Any]:
    """`view_assets` / `view_result` の注記。送らなかった画像(14.3)があればその旨も。"""
    withheld = [b for b in briefs if b.get("withheld")]
    if images:
        order = "in the same order as the assets"
        if withheld:
            order += " that are not withheld"
        result["note"] = f"The images follow in the next message, {order}. " + _SHOW_HINT
    if withheld:
        result["withheld_note"] = llm_view.WITHHELD_IMAGE_NOTE
    return result


# -- ストックの捜索(10章) ----------------------------------------------------------

# 見つけた画像を文章で説明するだけで終わり、`show_assets` を呼ばないことがある(実機の確認で
# 起きた)。システムプロンプトに加えて、見つけた画像を返す道具の結果でも促す。
_SHOW_HINT = (
    "The user cannot see these images. To present any of them, call show_assets with their ids."
)


def _asset_brief(asset: Asset) -> dict[str, Any]:
    """結果の1件の土台。URL は入れない(画面はサムネイルを Asset ID から引く)。"""
    return {
        "asset_id": str(asset.id),
        "kind": str(asset.kind),
        "width": asset.width,
        "height": asset.height,
        "created_at": _iso(asset.created_at),
    }


def _search_call[T](fn: Callable[[], T]) -> T:
    try:
        return fn()
    except asset_search_domain.AssetSearchError as e:
        raise ToolFailure(str(e)) from e


def _paged_assets(
    ctx: ToolContext, payload: dict[str, Any], offset: int, extra: dict[str, Any]
) -> dict[str, Any]:
    """検索の結果を、1件の短い形(14.4)と X 以上の絞り込み(14.3)にして、予算に収まる件数で
    切る。"""
    external = ctx.get_disclosure().external
    items = [llm_view.compact_asset(item, external=external) for item in payload["items"]]
    result = llm_view.paged(
        items,
        offset=offset,
        has_more=bool(payload.get("truncated")),
        total=payload.get("total"),
        extra=extra,
    )
    if result["items"]:
        result["note"] = _SHOW_HINT
    if any(item.get("withheld") for item in result["items"]):
        result["withheld_note"] = llm_view.WITHHELD_NOTE
    return result


async def search_assets(ctx: ToolContext, args: SearchAssetsArgs) -> ToolOutcome:
    def _search() -> dict[str, Any]:
        with ctx.session() as db:
            payload, _assets = _search_call(
                lambda: asset_search_domain.search_assets(
                    db,
                    ctx.state,
                    ctx.user,
                    _asset_brief,
                    query=args.query,
                    mode=args.mode or "hybrid",
                    kind=args.kind,
                    group_id=args.group_id,
                    tag=args.tag,
                    limit=args.limit,
                    offset=args.offset,
                )
            )
        extra: dict[str, Any] = {"mode": payload["mode"]}
        if payload["mode"] in ("semantic", "hybrid"):
            extra["multilingual"] = payload.get("multilingual")
        if payload.get("fallback_reason"):
            extra["fallback_reason"] = payload["fallback_reason"]
        return _paged_assets(ctx, payload, args.offset, extra)

    return ToolOutcome(ok=True, result=await _in_thread(_search))


async def find_similar_assets(ctx: ToolContext, args: FindSimilarAssetsArgs) -> ToolOutcome:
    def _find() -> dict[str, Any]:
        with ctx.session() as db:
            payload, _assets = _search_call(
                lambda: asset_search_domain.find_similar_assets(
                    db,
                    ctx.state,
                    ctx.user,
                    _asset_brief,
                    asset_id=args.asset_id,
                    limit=args.limit,
                    offset=args.offset,
                )
            )
        return _paged_assets(ctx, payload, args.offset, {"asset_id": str(args.asset_id)})

    return ToolOutcome(ok=True, result=await _in_thread(_find))


async def list_group_assets(ctx: ToolContext, args: ListGroupAssetsArgs) -> ToolOutcome:
    """グループの画像を短い形で全部返す(14.4。新しい順、`offset` で続きを読む)。"""

    def _list() -> dict[str, Any]:
        with ctx.session() as db:
            group = get_visible_group(db, ctx.user, args.group_id)
            if group is None or group.deleted_at is not None:
                raise ToolFailure(f"Group {args.group_id} not found.")
            payload, _assets = _search_call(
                lambda: asset_search_domain.search_assets(
                    db,
                    ctx.state,
                    ctx.user,
                    _asset_brief,
                    query=None,
                    group_id=args.group_id,
                    limit=args.limit,
                    offset=args.offset,
                )
            )
            extra = {"group": {"id": str(group.id), "name": group.name}}
        return _paged_assets(ctx, payload, args.offset, extra)

    return ToolOutcome(ok=True, result=await _in_thread(_list))


async def get_asset(ctx: ToolContext, args: GetAssetArgs) -> ToolOutcome:
    payload = await _in_thread(lambda: _asset_payload(ctx, args.asset_id))
    return ToolOutcome(ok=True, result=payload)


def _asset_payload(ctx: ToolContext, asset_id: uuid.UUID) -> dict[str, Any]:
    """`get_asset` と `view_result` の文字の情報。見えない・削除済みは ToolFailure。外部の接続先に
    送らない X 以上の画像は、ID・種類・段階・グループだけ(`withheld: true`。14.3)。"""
    with ctx.session() as db:
        asset = get_visible_asset(db, ctx.user, asset_id)
        if asset is None or asset.deleted_at is not None:
            raise ToolFailure(f"Asset {asset_id} not found.")
        level = content_rating_domain.level_for(db, asset.id)
        if llm_view.withholds(level, external=ctx.get_disclosure().external):
            # X 以上の画像は、外部の接続先には ID・種類・段階・グループだけ(14.3)。
            payload = llm_view.withheld_entry(
                asset.id, asset.kind, level, group_for_asset(db, asset.id, ctx.user)
            )
            payload["withheld_note"] = llm_view.WITHHELD_NOTE
            return payload
        payload = _asset_brief(asset)
        payload.update(asset_search_domain.asset_details(db, ctx.user, asset))
    return payload


async def view_result(ctx: ToolContext, args: ViewResultArgs) -> ToolOutcome:
    """生成結果を読む(12章)。`asset_id` と `asset_ids` を省くと、スタジオの結果エリアの画像を
    見る。`asset_ids` は複数の画像を渡した順に返す(結果は画像ごとの配列)。"""
    if args.asset_id is not None and args.asset_ids is not None:
        raise ToolFailure("Pass either asset_id or asset_ids, not both.")
    if args.asset_ids is not None:
        return await _in_thread(lambda: _view_many(ctx, args.asset_ids or [], args.include_image))

    asset_id = args.asset_id
    from_result_area = asset_id is None
    if asset_id is None:
        asset_id = ctx.form.result_asset_id if ctx.form is not None else None
    if asset_id is None:
        raise ToolFailure(
            "No image is shown in the studio's result area (or the studio is not open). "
            "Ask the user to open the result in the studio, or pass asset_id."
        )

    def _view() -> ToolOutcome:
        payload = _asset_payload(ctx, asset_id)
        payload["from_result_area"] = from_result_area
        images: list[LlmImage] = []
        if args.include_image:
            briefs, images = load_visible_images(ctx, [asset_id])
            if images:
                payload["note"] = "The image follows in the next message."
            if any(b.get("withheld") for b in briefs):
                payload["withheld_note"] = llm_view.WITHHELD_IMAGE_NOTE
        return ToolOutcome(ok=True, result=payload, images=images)

    return await _in_thread(_view)


def _view_many(ctx: ToolContext, asset_ids: list[uuid.UUID], include_image: bool) -> ToolOutcome:
    """`view_result` の `asset_ids`。1枚でも見えなければ全体を ToolFailure にする。"""
    shown = ctx.form.result_asset_id if ctx.form is not None else None
    items: list[dict[str, Any]] = []
    for asset_id in asset_ids:
        payload = _asset_payload(ctx, asset_id)
        # 結果エリアの画像かどうか(履歴の位置を数える起点を示す)。
        payload["in_result_area"] = asset_id == shown
        items.append(payload)
    result: dict[str, Any] = {"assets": items}
    images: list[LlmImage] = []
    if include_image:
        briefs, images = load_visible_images(ctx, asset_ids)
        _view_note(result, briefs, images)
    return ToolOutcome(ok=True, result=result, images=images)


def _clip(text: str | None, limit: int) -> str | None:
    if text is None or len(text) <= limit:
        return text
    return text[:limit] + "…"


async def list_recent_results(ctx: ToolContext, args: ListRecentResultsArgs) -> ToolOutcome:
    """履歴の参照(12章)。本人に見える Run を新しい順に(実行元は問わない)。画像の中身は
    入れない。一覧と見える範囲は REST・MCP の Run の一覧と同じ `visible_runs_query`。"""

    def _list() -> dict[str, Any]:
        with ctx.session() as db:
            query = run_views_domain.visible_runs_query(ctx.user)
            if args.group_id is not None:
                if get_visible_group(db, ctx.user, args.group_id) is None:
                    raise ToolFailure(f"Group {args.group_id} not found.")
                query = query.where(run_views_domain.run_in_group(args.group_id))
            total = db.execute(select(func.count()).select_from(query.subquery())).scalar_one()
            runs = list(db.execute(query.offset(args.offset).limit(args.limit + 1)).scalars().all())
            more = len(runs) > args.limit
            runs = runs[: args.limit]
            run_ids = [r.id for r in runs]
            outputs = run_views_domain.bulk_output_refs(db, run_ids, exclude_deleted=True)
            inputs = run_views_domain.bulk_input_refs(db, run_ids, ctx.user)
            # X 以上の画像を作った・使った Run のプロンプトは送らない(14.3)。
            withheld = ctx.get_disclosure().withheld_ids(
                db,
                [o.asset_id for r in runs for o in outputs[r.id]]
                + [asset_id for r in runs for asset_id, _role, _pos in inputs[r.id]],
            )
            items = []
            for run in runs:
                output_ids = [o.asset_id for o in outputs[run.id]]
                input_refs = inputs[run.id]
                item: dict[str, Any] = {
                    "run_id": str(run.id),
                    "created_at": _iso(run.queued_at),
                    "status": str(run.status),
                    "operation": str(run.operation),
                    "provider": run.provider,
                    "model": run.model,
                    "origin": run.origin or "web",
                    "prompt": _clip(run.prompt, RECENT_RESULTS_PROMPT_MAX),
                    "output_asset_ids": [str(a) for a in output_ids],
                    "inputs": [
                        {"asset_id": str(asset_id), "role": role}
                        for asset_id, role, _position in input_refs
                    ],
                }
                if withheld & ({*output_ids} | {a for a, _r, _p in input_refs}):
                    item["prompt"] = None
                    item["withheld"] = True
                items.append(item)
        shown = ctx.form.result_asset_id if ctx.form is not None else None
        payload = llm_view.paged(items, offset=args.offset, has_more=more, total=total, extra={})
        if shown is not None:
            payload["result_area_asset_id"] = str(shown)
        if any(item.get("withheld") for item in payload["items"]):
            payload["withheld_note"] = (
                "Runs marked withheld made or used images rated X or higher; their prompts are "
                "not sent to the external LLM connection."
            )
        payload["note"] = (
            "Newest first. Count positions by run (a run with several outputs counts once); "
            "runs you created are included. Text only: to look at outputs, call view_result "
            f"with asset_ids (up to {VIEW_ASSETS_MAX})."
        )
        return payload

    return ToolOutcome(ok=True, result=await _in_thread(_list))


async def list_groups(ctx: ToolContext, _args: NoArgs) -> ToolOutcome:
    def _list() -> dict[str, Any]:
        with ctx.session() as db:
            return asset_search_domain.groups_payload(db, ctx.user)

    return ToolOutcome(ok=True, result=await _in_thread(_list))


async def list_tags(ctx: ToolContext, args: ListTagsArgs) -> ToolOutcome:
    def _list() -> dict[str, Any]:
        # 外部の接続先なら、X 以上の画像は数えない(14.3)。
        excluded = ctx.get_disclosure().excluded_levels()
        with ctx.session() as db:
            rows = annotations_domain.list_tags(
                db,
                ctx.user,
                args.prefix,
                args.limit + 1,
                prefix=True,
                offset=args.offset,
                exclude_levels=excluded,
            )
            total = annotations_domain.count_tags(
                db, ctx.user, args.prefix, prefix=True, exclude_levels=excluded
            )
        more = len(rows) > args.limit
        items = [{"name": name, "count": count} for name, count in rows[: args.limit]]
        return llm_view.paged(items, offset=args.offset, has_more=more, total=total)

    return ToolOutcome(ok=True, result=await _in_thread(_list))


async def show_assets(ctx: ToolContext, args: ShowAssetsArgs) -> ToolOutcome:
    """見つけた画像をカードで示す(書き込みはしない)。結果の `assets` を画面がサムネイルの
    格子にする(サムネイルは Asset ID から `GET /api/assets/{id}/content?variant=thumb` で
    引く)。並びは指定どおりで、重複は1つにまとめる。見えない・削除済みの画像は道具のエラー。"""

    def _show() -> ToolOutcome:
        ids = list(dict.fromkeys(args.asset_ids))
        with ctx.session() as db:
            found: dict[uuid.UUID, Asset] = {}
            for asset_id in ids:
                asset = get_visible_asset(db, ctx.user, asset_id)
                if asset is not None and asset.deleted_at is None:
                    found[asset_id] = asset
            missing = [str(i) for i in ids if i not in found]
            if missing:
                raise ToolFailure("Assets not found: " + ", ".join(missing))
            titles = annotations_domain.bulk_titles(db, ids)
            focal = focal_points_domain.bulk_get(db, ids)
            ratings = content_rating_domain.bulk_levels(db, ids)
            assets = []
            for asset_id in ids:
                entry = _asset_brief(found[asset_id])
                entry["title"] = titles.get(asset_id)
                # サムネイルの焦点(ADR-0043)。画面が `object-position` に使う。
                point = focal.get(asset_id)
                entry["focal_point"] = point.model_dump(mode="json") if point else None
                # レーティング(ADR-0049)。画面のカードがマスクに使う。
                entry["content_rating"] = ratings.get(asset_id)
                assets.append(entry)
            external = ctx.get_disclosure().external
            llm_assets = [
                llm_view.withheld_entry(
                    a["asset_id"],
                    a["kind"],
                    a["content_rating"],
                    group_for_asset(db, uuid.UUID(a["asset_id"]), ctx.user),
                )
                if llm_view.withholds(a["content_rating"], external=external)
                else {
                    "asset_id": a["asset_id"],
                    "kind": a["kind"],
                    "title": a["title"],
                    "content_rating": a["content_rating"],
                }
                for a in assets
            ]
        result = {"shown": True, "title": args.title, "assets": assets}
        # 画面のカードは焦点・幅・高さ・タイトルを使う。LLM には短い形(14.4)だけ。
        llm_result = {"shown": True, "title": args.title, "assets": llm_assets}
        return ToolOutcome(ok=True, result=result, llm_result=llm_result)

    return await _in_thread(_show)


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


# -- ストックの整理(13章) ----------------------------------------------------------


def _stock_call(ctx: ToolContext, fn: Callable[[Session], stock_domain.StockWrite]) -> ToolOutcome:
    with ctx.session() as db:
        try:
            write = fn(db)
        except stock_domain.StockError as e:
            db.rollback()
            raise ToolFailure(str(e)) from e
    if write.requeued_embeddings:
        embedder = getattr(ctx.state, "embedder", None)
        if embedder is not None:
            embedder.notify()
    return ToolOutcome(ok=True, result=write.result, undo=write.undo)


async def add_tags(ctx: ToolContext, args: TagsArgs) -> ToolOutcome:
    return await _in_thread(
        lambda: _stock_call(
            ctx, lambda db: stock_domain.add_tags(db, ctx.user, args.asset_ids, args.tags)
        )
    )


async def remove_tags(ctx: ToolContext, args: TagsArgs) -> ToolOutcome:
    return await _in_thread(
        lambda: _stock_call(
            ctx, lambda db: stock_domain.remove_tags(db, ctx.user, args.asset_ids, args.tags)
        )
    )


async def set_title(ctx: ToolContext, args: SetTitleArgs) -> ToolOutcome:
    def _set() -> ToolOutcome:
        outcome = _stock_call(
            ctx,
            lambda db: stock_domain.set_title(
                db, ctx.user, args.asset_id, args.title, ctx.state.settings
            ),
        )
        with ctx.session() as db:
            withheld = ctx.get_disclosure().withheld_ids(db, [args.asset_id])
        if withheld:
            # 書き込みは ID で働く。X 以上の画像の前後のタイトルは外部の接続先に送らない(14.3)。
            llm_result = dict(outcome.result)
            llm_result["before"] = {"title": None}
            llm_result["after"] = {"title": None}
            llm_result["withheld"] = True
            llm_result["withheld_note"] = llm_view.WITHHELD_NOTE
            outcome.llm_result = llm_result
        return outcome

    return await _in_thread(_set)


async def create_group(ctx: ToolContext, args: CreateGroupArgs) -> ToolOutcome:
    return await _in_thread(
        lambda: _stock_call(
            ctx, lambda db: stock_domain.create_group(db, ctx.user, args.name, args.asset_ids)
        )
    )


async def rename_group(ctx: ToolContext, args: RenameGroupArgs) -> ToolOutcome:
    return await _in_thread(
        lambda: _stock_call(
            ctx, lambda db: stock_domain.rename_group(db, ctx.user, args.group_id, args.name)
        )
    )


async def move_to_group(ctx: ToolContext, args: GroupAssetsArgs) -> ToolOutcome:
    return await _in_thread(
        lambda: _stock_call(
            ctx,
            lambda db: stock_domain.move_to_group(db, ctx.user, args.group_id, args.asset_ids),
        )
    )


async def remove_from_group(ctx: ToolContext, args: GroupAssetsArgs) -> ToolOutcome:
    return await _in_thread(
        lambda: _stock_call(
            ctx,
            lambda db: stock_domain.remove_from_group(db, ctx.user, args.group_id, args.asset_ids),
        )
    )


def _suggestion_view(suggestion: Any, *, ids_max: int) -> dict[str, Any]:
    member_ids = [str(i) for i in suggestion.member_ids]
    view: dict[str, Any] = {
        "suggestion_id": suggestion.id,
        "size": suggestion.size,
        "asset_ids": member_ids[:ids_max],
        "asset_ids_truncated": len(member_ids) > ids_max,
        "top_tags": [
            {"tag": tag.tag, "label": tag.label, "rate": round(tag.rate, 3)}
            for tag in suggestion.top_tags
        ],
        "franchise": suggestion.franchise.name if suggestion.franchise else None,
        "existing_group": (
            {
                "id": str(suggestion.target_group.id),
                "name": suggestion.target_group.name,
                "overlap": round(suggestion.target_group.overlap, 3),
            }
            if suggestion.target_group
            else None
        ),
        "moving_from_other_groups": suggestion.moving_count,
    }
    return view


async def suggest_groups(ctx: ToolContext, args: SuggestGroupsArgs) -> ToolOutcome:
    """グループの提案(13章、ADR-0056)。保存はしない。埋め込みが無効なら道具のエラー。"""

    def _suggest() -> dict[str, Any]:
        state = ctx.state
        with ctx.session() as db:
            try:
                response = group_suggestions_domain.suggest_groups(
                    db,
                    settings=state.settings,
                    index=state.vector_index,
                    version_of=state.embedder.version,
                    cache=state.group_suggestion_cache,
                    viewer=ctx.user,
                    scope=args.scope,
                    granularity=args.granularity,
                    # 外部の接続先なら、特徴的なタグに X 以上の画像を数えない(14.3)。
                    exclude_tag_levels=ctx.get_disclosure().excluded_levels(),
                )
            except EmbeddingsConflictError as e:
                raise ToolFailure(
                    f"Group suggestions are not available: {e.message} (embeddings are off or "
                    "not ready). Use search_assets instead."
                ) from e
        suggestions = response.suggestions
        if args.suggestion_id is not None:
            found = next((x for x in suggestions if x.id == args.suggestion_id), None)
            if found is None:
                raise ToolFailure(
                    f"Suggestion {args.suggestion_id} not found (the stock may have changed). "
                    "Call suggest_groups again without suggestion_id."
                )
            return {
                "suggestion": _suggestion_view(found, ids_max=SUGGEST_GROUPS_IDS_MAX),
                "note": _SUGGEST_NOTE,
            }
        return {
            "scope": response.scope,
            "scanned": response.scanned,
            "truncated": response.truncated,
            "total_suggestions": len(suggestions),
            "suggestions": [
                _suggestion_view(x, ids_max=SUGGEST_GROUPS_IDS_PREVIEW)
                for x in suggestions[: args.limit]
            ],
            "ungrouped_outliers": len(response.outlier_ids),
            "note": _SUGGEST_NOTE,
        }

    return ToolOutcome(ok=True, result=await _in_thread(_suggest))


_SUGGEST_NOTE = (
    "Nothing was saved. Suggestions with existing_group are images that mostly belong to that "
    "group already. To organize, use create_group or move_to_group with the image ids (get "
    "all ids of a suggestion with suggestion_id). To show a suggestion to the user, call "
    "show_assets."
)


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
    # ADR-0051: 取り消しも Run の終わり(`run.finished`)として Mod に知らせる。
    mods = getattr(state, "mods", None)
    if mods is not None and canceled:
        mods.enqueue_runs_finished(canceled)
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


# -- 生成フォームの書き込み(11章) --------------------------------------------------

_FORM_FIELDS = ("provider", "model", "operation", "prompt", "params", "inputs")
_FORM_NOTE = (
    "The generation form in the user's browser now has these values (nothing is saved on the "
    "server). Tell the user briefly what you changed. Parameters you do not pass to "
    "generate_image now come from this form."
)


def _given(args: UpdateFormArgs, name: str) -> bool:
    """指定された項目か。`asset_group_id` は null も指定(「グループなし」)。"""
    if name not in args.model_fields_set:
        return False
    return name == "asset_group_id" or getattr(args, name) is not None


def _resolve_provider(ctx: ToolContext, name: str) -> ProviderCapabilities:
    """今使えるプロバイダーの capabilities。使えなければ ToolFailure。"""
    registry = ctx.state.registry
    provider = registry.get(name)
    if provider is None:
        available = ", ".join(sorted(registry.providers))
        raise ToolFailure(f"Unknown provider: {name}. Available providers: {available}.")
    snapshot = provider_status_snapshot(provider)
    if not snapshot.available:
        reason = snapshot.unavailable_reason or t("runs.providerUnavailable", label=provider.label)
        raise ToolFailure(f"The provider {name} cannot be used now: {reason}")
    return snapshot.capabilities


def _find_model(caps: ProviderCapabilities, model: str) -> ModelCapabilities:
    found = next((m for m in caps.models if m.model == model), None)
    if found is None:
        models = ", ".join(m.model for m in caps.models) or "(none)"
        raise ToolFailure(f"Unknown model for {caps.provider}: {model}. Models: {models}.")
    return found


def _param_default(caps: ProviderCapabilities, defs: dict[str, Any], key: str) -> Any:
    """null で戻す先の値(フォームの既定。無ければ None = 未指定)。"""
    if key == "size":
        return caps.default_size
    pdef = defs.get(key)
    return None if pdef is None else pdef.form_default


def _param_kept(caps: ProviderCapabilities, defs: dict[str, Any], key: str, value: Any) -> bool:
    """モデル・操作を変えたとき、今の値を引き継げるか(キーと選択肢だけを見る。フォームの
    `sanitizeRawValues` と同じ考え方)。"""
    if key == "size":
        return caps.size is not None
    pdef = defs.get(key)
    if pdef is None:
        return False
    return not (pdef.choices and str(value) not in pdef.choices)


@dataclass
class _FormUpdate:
    patch: dict[str, Any]
    form: AssistantFormState
    dropped_params: list[str]


def _resolve_form_update(ctx: ToolContext, args: UpdateFormArgs) -> _FormUpdate:
    """フォームへの変更を検証し、変えた項目(`patch`)と重ねた後の値を作る。何かが通らなければ
    ToolFailure(何も変えない)。"""
    if not any(_given(args, name) for name in (*_FORM_FIELDS, "asset_group_id")):
        raise ToolFailure(
            "Nothing to change: pass at least one of provider, model, operation, prompt, "
            "params, inputs or asset_group_id."
        )
    base = ctx.form or AssistantFormState()
    registry = ctx.state.registry
    patch: dict[str, Any] = {}

    # 入力画像。操作(Generate / Edit)は、フォームと同じく入力画像から導く(ADR-0009)。
    inputs = list(base.inputs)
    if _given(args, "inputs"):
        inputs = [AssistantFormInput(asset_id=i.asset_id, role=i.role) for i in args.inputs or []]
        masks = sum(1 for i in inputs if i.role == "mask")
        if masks > 1:
            raise ToolFailure("Only one mask can be in the form.")
        if masks and not any(i.role == "image" for i in inputs):
            raise ToolFailure("A mask needs at least one input image (role image).")
        with ctx.session() as db:
            for item in inputs:
                asset = get_visible_asset(db, ctx.user, item.asset_id)
                if asset is None or asset.deleted_at is not None:
                    raise ToolFailure(f"Asset {item.asset_id} not found.")
        patch["inputs"] = [i.model_dump(mode="json") for i in inputs]
    elif args.operation == "generate":
        # Generate は入力画像の無いフォーム。入力画像を外す。
        inputs = []
        patch["inputs"] = []
    has_image = any(i.role == "image" for i in inputs)
    operation: Literal["generate", "edit"] = "edit" if has_image else "generate"
    if args.operation is not None and args.operation != operation:
        if args.operation == "edit":
            raise ToolFailure(
                "edit needs at least one input image: pass inputs with an image (role image)."
            )
        raise ToolFailure("generate takes no input images: pass inputs [] or omit inputs.")
    base_operation = base.operation or ("edit" if base.inputs else "generate")
    if _given(args, "operation") or _given(args, "inputs"):
        patch["operation"] = operation

    # プロバイダーとモデル。変えるとき・パラメーターを変えるとき・操作が変わるときだけ引く。
    current_provider = base.provider or registry.primary
    provider_name = args.provider if _given(args, "provider") else current_provider
    if _given(args, "model"):
        model = args.model
    elif provider_name == current_provider and base.model:
        model = base.model
    else:
        model = None
    model_changed = provider_name != current_provider or (model is not None and model != base.model)
    needs_caps = (
        _given(args, "provider")
        or _given(args, "model")
        or _given(args, "params")
        or operation != base_operation
    )
    params = dict(base.params)
    dropped: list[str] = []
    if needs_caps:
        caps = _resolve_provider(ctx, provider_name or "")
        model = model or caps.default_model
        model_caps = _find_model(caps, model)
        op_caps = next((o for o in model_caps.operations if o.operation == operation), None)
        if op_caps is None:
            raise ToolFailure(f"The model {model} does not support {operation}.")
        defs = {p.name: p for p in op_caps.params}
        if _given(args, "provider"):
            patch["provider"] = provider_name
        if _given(args, "provider") or _given(args, "model"):
            patch["model"] = model
        if model_changed or operation != base_operation:
            # 新しいモデル・操作に無いキーと選択肢は落とす(フォームと同じ)。
            for key in list(params):
                if not _param_kept(caps, defs, key, params[key]):
                    dropped.append(key)
                    params.pop(key)
        if _given(args, "params"):
            requested = args.params or {}
            for key, value in requested.items():
                if key == "size":
                    if caps.size is None:
                        raise ToolFailure(f"{caps.provider} does not take a size parameter.")
                elif key not in defs:
                    names = ", ".join(["size", *defs] if caps.size else defs) or "(none)"
                    raise ToolFailure(
                        f"Unknown parameter for {model} ({operation}): {key}. Parameters: {names}."
                    )
                if value is not None and not isinstance(value, str | int | float | bool):
                    raise ToolFailure(f"The value of {key} must be a string, number or boolean.")
            for key, value in requested.items():
                if value is None:
                    default = _param_default(caps, defs, key)
                    if default is None:
                        params.pop(key, None)
                    else:
                        params[key] = default
                else:
                    params[key] = value
            patch["params"] = dict(requested)
        provider_out: str | None = provider_name
    else:
        provider_out = base.provider

    if _given(args, "prompt"):
        patch["prompt"] = args.prompt
    group_id = base.asset_group_id
    if _given(args, "asset_group_id"):
        group_id = args.asset_group_id
        if group_id is not None:
            with ctx.session() as db:
                if get_visible_group(db, ctx.user, group_id) is None:
                    raise ToolFailure(f"Group {group_id} not found.")
        patch["asset_group_id"] = str(group_id) if group_id is not None else None

    form = AssistantFormState(
        provider=provider_out,
        model=model,
        operation=operation,
        prompt=patch["prompt"] if "prompt" in patch else base.prompt,
        params=params,
        inputs=inputs,
        asset_group_id=group_id,
        # 結果エリアの画像はフォームの値ではない。書き込んでも引き継ぐ(12章)。
        result_asset_id=base.result_asset_id,
        # ストックの選択もフォームの値ではない(13章)。
        selected_asset_ids=base.selected_asset_ids,
    )
    return _FormUpdate(patch=patch, form=form, dropped_params=dropped)


async def update_form(ctx: ToolContext, args: UpdateFormArgs) -> ToolOutcome:
    update = await _in_thread(lambda: _resolve_form_update(ctx, args))
    # 同じ発言の `get_form_context` と `generate_image` の補完に効くよう、変更を重ねる。
    ctx.form = update.form
    result: dict[str, Any] = {
        "patch": update.patch,
        # ブラウザがフォームに反映する値。結果エリアの画像はフォームの値ではないので入れない。
        "form": update.form.model_dump(
            mode="json", exclude={"result_asset_id", "selected_asset_ids"}
        ),
        "note": _FORM_NOTE,
    }
    if update.dropped_params:
        result["dropped_params"] = update.dropped_params
        result["dropped_note"] = (
            "These parameters do not exist (or have a value that is not allowed) for the new "
            "model or operation and were reset."
        )
    return ToolOutcome(ok=True, result=result)


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
        "prompt, params, input images and group. Includes changes made with update_form. "
        "Also returns the image in the studio's result area (result_asset_id) and the images "
        "the user selected in the stock (selected_asset_ids).",
        NoArgs,
        get_form_context,
    ),
    ToolDefinition(
        "update_form",
        "Change the generation form in the user's browser: provider, model, operation, prompt "
        "(replaced), params (only the given keys; null resets a key), input images (replaced) "
        "and output group. Only the fields you pass change. Applied immediately without "
        "asking; the user sees a card with an Undo button. Nothing is saved on the server.",
        UpdateFormArgs,
        update_form,
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
        "to you after the tool result. Do not use it to find images: narrow candidates with "
        "the text results of search_assets, find_similar_assets or list_group_assets first, "
        "and look only at the few you cannot decide on from text. The user may be asked to "
        "allow sending the images; if they decline, continue with the text information.",
        ViewAssetsArgs,
        view_assets,
    ),
    ToolDefinition(
        "view_result",
        "Look at the generation result shown in the studio's result area (omit asset_id and "
        "asset_ids), at another image (asset_id), or at up to 4 images together (asset_ids, "
        "for example earlier results found with list_recent_results). Use this whenever the "
        "user talks about the result, the current image or the image they are looking at, for "
        "example when they want it fixed or ask for an edit prompt. Returns the same details "
        "as get_asset (title, tags including automatic tags, the prompt, model and parameters "
        "that made it with its run id, its primary parent) and the images after the tool "
        "result. With asset_ids, the details are a list in the given order. Pass "
        "include_image=false for the text only. The user may be asked to allow sending the "
        "images.",
        ViewResultArgs,
        view_result,
    ),
    ToolDefinition(
        "list_recent_results",
        "List the user's recent runs, newest first, from any origin (the form, you, or MCP): "
        "run id, time, status, operation, model, prompt (shortened), output asset ids in order "
        "and input asset ids with roles. Includes unfinished and failed runs. Use it for "
        "requests such as 'the result before last' or 'compare the last two'. Text only; look "
        "at outputs with view_result (asset_ids). Read older runs with offset when "
        "next_offset is set.",
        ListRecentResultsArgs,
        list_recent_results,
    ),
    ToolDefinition(
        "search_assets",
        "Search the user's stock of images. hybrid mode (the default) combines keyword and "
        "semantic matching into one ranking; start with it. keyword mode matches exact words "
        "in prompts, titles and tags (newest first; omit query to list the newest). semantic "
        "mode finds images by visual similarity to a natural-language description using image "
        "embeddings. Filter by kind, group and tag. "
        "Results are text only (ids, kinds, titles, tags, ratings, groups, scores). This is the "
        "first tool for finding images. Results stop at a size limit: when next_offset is set, "
        "call again with offset to read the rest. Use view_assets only for a few candidates "
        "you cannot decide on from text, and show_assets to present the ones you picked.",
        SearchAssetsArgs,
        search_assets,
    ),
    ToolDefinition(
        "find_similar_assets",
        "List images that look similar to the given image, most similar first (image "
        "embeddings), as text. Read more with offset when next_offset is set. Fails when "
        "embeddings are turned off.",
        FindSimilarAssetsArgs,
        find_similar_assets,
    ),
    ToolDefinition(
        "get_asset",
        "Get one image's details: kind, size, title, tags, group, the run that made it "
        "(prompt, model, parameters) and its primary parent image.",
        GetAssetArgs,
        get_asset,
    ),
    ToolDefinition(
        "list_group_assets",
        "List all images in a group as text (id, kind, title, tags, rating), newest first. "
        "Results stop at a size limit: when next_offset is set, call again with offset until "
        "it is null. Use it to organize a whole group.",
        ListGroupAssetsArgs,
        list_group_assets,
    ),
    ToolDefinition(
        "list_groups",
        "List the groups that organize the stock (with member counts).",
        NoArgs,
        list_groups,
    ),
    ToolDefinition(
        "list_tags",
        "List tags on the user's images with counts, most used first. Filter by prefix. Read "
        "more with offset when next_offset is set.",
        ListTagsArgs,
        list_tags,
    ),
    ToolDefinition(
        "show_assets",
        "Show images to the user as a card with a thumbnail grid. The user can open them, "
        "drag them into the form or attach them to the next message. Changes nothing.",
        ShowAssetsArgs,
        show_assets,
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
        "add_tags",
        "Add tags to images (up to 200 per call). Tags are saved as if the user added them; "
        "automatic re-tagging does not remove them. Applied immediately; the user can undo it.",
        TagsArgs,
        add_tags,
    ),
    ToolDefinition(
        "remove_tags",
        "Remove tags from images (up to 200 per call). Automatic re-tagging does not add them "
        "back. Applied immediately; the user can undo it.",
        TagsArgs,
        remove_tags,
    ),
    ToolDefinition(
        "set_title",
        "Change one image's title (null or empty clears it). Saved as if the user edited it. "
        "Applied immediately; the user can undo it.",
        SetTitleArgs,
        set_title,
    ),
    ToolDefinition(
        "create_group",
        "Create a group, optionally putting images into it (up to 200). An image belongs to "
        "one group only, so the images move out of their current group. Applied immediately; "
        "the user can undo it.",
        CreateGroupArgs,
        create_group,
    ),
    ToolDefinition(
        "rename_group",
        "Rename a group. Applied immediately; the user can undo it.",
        RenameGroupArgs,
        rename_group,
    ),
    ToolDefinition(
        "move_to_group",
        "Put images into an existing group (up to 200 per call). They move out of their "
        "current group. Applied immediately; the user can undo it.",
        GroupAssetsArgs,
        move_to_group,
    ),
    ToolDefinition(
        "remove_from_group",
        "Take images out of a group (they stay in the stock without a group). Applied "
        "immediately; the user can undo it.",
        GroupAssetsArgs,
        remove_from_group,
    ),
    ToolDefinition(
        "suggest_groups",
        "Suggest groups by clustering the images with image embeddings (largest first): each "
        "suggestion has image ids, size, distinctive tags and the existing group it overlaps "
        "with. Saves nothing. Fails when embeddings are turned off.",
        SuggestGroupsArgs,
        suggest_groups,
    ),
    ToolDefinition(
        "generate_image",
        "Create generation / edit runs (billed). Parameters you omit come from the current "
        "form (including changes made with update_form). Returns run ids right away with an "
        "estimated price; then wait with get_run.",
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
