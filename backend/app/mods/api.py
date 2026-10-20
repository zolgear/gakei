"""Mod の契約(ADR-0051 3章・4章)。Python の Mod には `gakei_mod` の名前で見せる公開モジュール。

Mod が使ってよいのはこのモジュールの型と関数だけ(GAKEI の内部のモジュールを import した Mod は、
動作を保証しない)。Webhook と Python の Mod は同じ中身を受け取る。JSON への直列化
(`event_to_json`)と、応答の読み取り(`decision_from_json` / `verdicts_from_json`)はここ1か所に
置く。

- 項目を足すのは `SCHEMA_VERSION` を上げずに行う。消す・意味を変えるときは上げる。
- `API_VERSION` は Python の Mod の関数の呼ばれ方の版(マニフェストの `api_version`)。

Python の Mod の関数(どれも `async def`。購読するイベントの分だけ置く):

    async def on_ai_before_send(event: Event, ctx: ModContext) -> Decision: ...
    async def on_asset_ingested(event: Event, ctx: ModContext) -> list[Verdict] | None: ...
    async def on_run_finished(event: Event, ctx: ModContext) -> list[Verdict] | None: ...
"""

from __future__ import annotations

import logging
import math
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

SCHEMA_VERSION = 1
API_VERSION = 1

ASSET_INGESTED = "asset.ingested"
AI_BEFORE_SEND = "ai.before_send"
RUN_FINISHED = "run.finished"
EVENTS: tuple[str, ...] = (ASSET_INGESTED, AI_BEFORE_SEND, RUN_FINISHED)

# イベントごとの Python の Mod の関数の名前。
HANDLER_NAMES: dict[str, str] = {
    AI_BEFORE_SEND: "on_ai_before_send",
    ASSET_INGESTED: "on_asset_ingested",
    RUN_FINISHED: "on_run_finished",
}

# `ai.before_send` の目的(ADR-0051 2章)。`workflow_analysis` は ComfyUI のワークフローの推定
# (ADR-0052 6章)。値を足すのは項目の追加と同じ扱いで、`SCHEMA_VERSION` は上げない。
PURPOSES: tuple[str, ...] = (
    "image_run",
    "annotation",
    "assistant",
    "embedding",
    "workflow_analysis",
)
# `asset.ingested` の取り込みの経路。
SOURCES: tuple[str, ...] = (
    "upload",
    "url",
    "sketch",
    "mask",
    "composite",
    "generated",
    "import",
    "mcp",
)
# 接続先(外部かローカルか)と、それがどこで決まったか(ADR-0051 3章)。
LOCALITIES: tuple[str, ...] = ("external", "local")
LOCALITY_SOURCES: tuple[str, ...] = ("workflow", "connection", "default")
# 送る画像の使い方。
IMAGE_ROLES: tuple[str, ...] = ("primary", "reference", "mask", "attachment")
VERDICTS: tuple[str, ...] = ("ok", "flag", "deny")

# 応答の文字列の長さの上限(超えたら切る)。
_TEXT_MAX = 2000
_LABEL_MAX = 200
_VERDICTS_MAX = 100


class ContractError(ValueError):
    """応答が契約に合わない(GAKEI はその呼び出しを失敗として扱う)。"""


class ImageWithheld(LookupError):
    """画像を残さなかった Asset(ADR-0051 4章。`deny` の判定を受けた)の原本を開こうとした。

    `ModContext.open_image` と `open_image_with_lineage` が出す。原本はもう無いので、Mod はこの
    Asset を飛ばしてよい(失敗にしなくてよい)。"""


# -- イベント -------------------------------------------------------------------------


@dataclass(frozen=True)
class Instance:
    """イベントを送った GAKEI。`name` は管理者が決めた名前(未設定なら null)。"""

    version: str
    name: str | None = None


@dataclass(frozen=True)
class Actor:
    """実行したユーザー。個人モードでは、イベントの `actor` そのものが null。"""

    id: str
    email: str | None = None
    name: str | None = None


@dataclass(frozen=True)
class ImageRef:
    """送る画像・取り込んだ画像。`asset_id` は Asset でない画像(画像で探すときの画像)では null。"""

    asset_id: str | None
    sha256: str
    width: int
    height: int
    bytes: int
    mime: str
    # primary(主たる入力)/ reference(参照)/ mask / attachment(添付)。
    # `asset.ingested` の Asset と `run.finished` の出力では null。
    role: str | None = None


@dataclass(frozen=True)
class WorkflowRef:
    id: str
    name: str | None = None


@dataclass(frozen=True)
class ConnectionRef:
    """LLM・VLM・埋め込みの接続先(ADR-0032)。`builtin` は組み込みの「OpenAI の設定」。"""

    id: str
    name: str | None = None
    builtin: bool = False


@dataclass(frozen=True)
class Target:
    """送り先。API キーは含めない。"""

    provider: str
    model: str | None
    host: str | None
    locality: str
    locality_source: str
    workflow: WorkflowRef | None = None
    connection: ConnectionRef | None = None


@dataclass(frozen=True)
class TextPart:
    """送る文。`role` は prompt / negative_prompt / system / user / assistant / tool / tags /
    query など。"""

    role: str
    text: str


@dataclass(frozen=True)
class BeforeSendSubject:
    purpose: str
    target: Target
    text: list[TextPart] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    images: list[ImageRef] = field(default_factory=list)
    run_id: str | None = None
    # 自動タイトル・タグのとき、対象の Asset。
    asset_id: str | None = None
    # 画像の生成で、領域の指示の四角を付けたときの四角の数(ADR-0053 8章)。四角ごとの文は
    # `text` の `region`(指示)と `region_text`(描く文字)。四角が無ければ null。
    region_count: int | None = None


@dataclass(frozen=True)
class AssetIngestedSubject:
    asset: ImageRef
    kind: str
    source: str
    run_id: str | None = None
    # レーティング(判定済みのとき。ADR-0049)。
    rating: str | None = None


@dataclass(frozen=True)
class RunError:
    code: str | None
    message: str | None


@dataclass(frozen=True)
class RunFinishedSubject:
    run_id: str
    status: str
    provider: str
    model: str
    operation: str
    prompt: str
    params: dict[str, Any] = field(default_factory=dict)
    inputs: list[ImageRef] = field(default_factory=list)
    outputs: list[ImageRef] = field(default_factory=list)
    usage: dict[str, Any] | None = None
    error: RunError | None = None


Subject = BeforeSendSubject | AssetIngestedSubject | RunFinishedSubject


@dataclass(frozen=True)
class Event:
    """すべてのイベントに共通の封筒。`occurred_at` は ISO 8601(UTC)。"""

    event: str
    event_id: str
    occurred_at: str
    instance: Instance
    actor: Actor | None
    subject: Subject
    schema_version: int = SCHEMA_VERSION

    def to_json(self) -> dict[str, Any]:
        return event_to_json(self)


def event_to_json(event: Event) -> dict[str, Any]:
    """イベントの JSON(Webhook の本文と、配達の待ち行列に積む中身)。キーの並びは決まった順。"""
    return {
        "schema_version": event.schema_version,
        "event": event.event,
        "event_id": event.event_id,
        "occurred_at": event.occurred_at,
        "instance": asdict(event.instance),
        "actor": asdict(event.actor) if event.actor is not None else None,
        "subject": asdict(event.subject),
    }


def _image_from_json(raw: Mapping[str, Any]) -> ImageRef:
    return ImageRef(**{k: raw.get(k) for k in ImageRef.__dataclass_fields__})  # type: ignore[arg-type]


def event_from_json(raw: Mapping[str, Any]) -> Event:
    """`event_to_json` の逆(配達の待ち行列から読み戻すときに使う)。"""
    name = raw["event"]
    subject_raw = dict(raw["subject"])
    subject: Subject
    if name == AI_BEFORE_SEND:
        target_raw = dict(subject_raw["target"])
        workflow = target_raw.get("workflow")
        connection = target_raw.get("connection")
        target_raw["workflow"] = WorkflowRef(**workflow) if workflow else None
        target_raw["connection"] = ConnectionRef(**connection) if connection else None
        subject_raw["target"] = Target(**target_raw)
        subject_raw["text"] = [TextPart(**p) for p in subject_raw.get("text") or []]
        subject_raw["images"] = [_image_from_json(i) for i in subject_raw.get("images") or []]
        subject = BeforeSendSubject(**subject_raw)
    elif name == ASSET_INGESTED:
        subject_raw["asset"] = _image_from_json(subject_raw["asset"])
        subject = AssetIngestedSubject(**subject_raw)
    elif name == RUN_FINISHED:
        subject_raw["inputs"] = [_image_from_json(i) for i in subject_raw.get("inputs") or []]
        subject_raw["outputs"] = [_image_from_json(i) for i in subject_raw.get("outputs") or []]
        error = subject_raw.get("error")
        subject_raw["error"] = RunError(**error) if error else None
        subject = RunFinishedSubject(**subject_raw)
    else:
        raise ContractError(f"unknown event: {name}")
    actor = raw.get("actor")
    return Event(
        event=name,
        event_id=raw["event_id"],
        occurred_at=raw["occurred_at"],
        instance=Instance(**raw["instance"]),
        actor=Actor(**actor) if actor else None,
        subject=subject,
        schema_version=raw.get("schema_version", SCHEMA_VERSION),
    )


# -- 応答 -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Decision:
    """`ai.before_send` の応答。`reason` は利用者に見せる文、`reference` は外部の受付番号など。"""

    decision: Literal["allow", "deny"]
    reason: str | None = None
    reference: str | None = None

    @classmethod
    def allow(cls, reference: str | None = None) -> Decision:
        return cls("allow", None, reference)

    @classmethod
    def deny(cls, reason: str, reference: str | None = None) -> Decision:
        return cls("deny", reason, reference)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class VerdictTarget:
    """判定の対象。`type` は asset か run。"""

    type: Literal["asset", "run"]
    id: str


@dataclass(frozen=True)
class Verdict:
    """`asset.ingested` と `run.finished` の判定。`score` は 0〜1(任意)。"""

    target: VerdictTarget
    verdict: Literal["ok", "flag", "deny"]
    label: str | None = None
    score: float | None = None
    message: str | None = None
    reference: str | None = None
    url: str | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def _optional_text(raw: Mapping[str, Any], key: str, limit: int = _TEXT_MAX) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ContractError(f"{key} must be a string")
    value = value.strip()
    return value[:limit] if value else None


def decision_from_json(raw: Any) -> Decision:
    """`{"decision": "allow" | "deny", "reason"?, "reference"?}` を読む。合わなければ
    ContractError。"""
    if isinstance(raw, Decision):
        raw = raw.to_json()
    if not isinstance(raw, Mapping):
        raise ContractError("response must be an object")
    decision = raw.get("decision")
    if decision not in ("allow", "deny"):
        raise ContractError("decision must be 'allow' or 'deny'")
    return Decision(
        decision=decision,
        reason=_optional_text(raw, "reason"),
        reference=_optional_text(raw, "reference", _LABEL_MAX),
    )


def _verdict_from_json(raw: Any) -> Verdict:
    if isinstance(raw, Verdict):
        raw = raw.to_json()
    if not isinstance(raw, Mapping):
        raise ContractError("verdict must be an object")
    target = raw.get("target")
    if not isinstance(target, Mapping):
        raise ContractError("target must be an object")
    target_type = target.get("type")
    target_id = target.get("id")
    if target_type not in ("asset", "run") or not isinstance(target_id, str) or not target_id:
        raise ContractError("target must be {type: asset|run, id}")
    verdict = raw.get("verdict")
    if verdict not in VERDICTS:
        raise ContractError("verdict must be 'ok', 'flag' or 'deny'")
    score = raw.get("score")
    if score is not None:
        if isinstance(score, bool) or not isinstance(score, int | float):
            raise ContractError("score must be a number")
        score = float(score)
        if math.isnan(score) or not (0.0 <= score <= 1.0):
            raise ContractError("score must be between 0 and 1")
    url = _optional_text(raw, "url")
    if url is not None and not url.startswith(("http://", "https://")):
        raise ContractError("url must be http(s)")
    return Verdict(
        target=VerdictTarget(type=target_type, id=target_id),
        verdict=verdict,
        label=_optional_text(raw, "label", _LABEL_MAX),
        score=score,
        message=_optional_text(raw, "message"),
        reference=_optional_text(raw, "reference", _LABEL_MAX),
        url=url,
    )


def verdicts_from_json(raw: Any) -> list[Verdict]:
    """判定の配列を読む。null・空は判定なし。`{"verdicts": [...]}` の形も受ける。"""
    if raw is None:
        return []
    if isinstance(raw, Mapping):
        if "verdicts" not in raw:
            raise ContractError("response must be an array of verdicts")
        raw = raw["verdicts"]
        if raw is None:
            return []
    if not isinstance(raw, list | tuple):
        raise ContractError("response must be an array of verdicts")
    if len(raw) > _VERDICTS_MAX:
        raise ContractError(f"too many verdicts (max {_VERDICTS_MAX})")
    return [_verdict_from_json(item) for item in raw]


# -- Python の Mod に渡すもの -------------------------------------------------------------


@dataclass(frozen=True)
class ModContext:
    """Python の Mod に渡す道具。

    - `settings`: マニフェストに書いた設定の項目の値(秘密の項目も値が入る。ログに出さないこと)。
    - `open_image`: 画像の原本のバイト列を返す。`ImageRef` か Asset の id を渡す。
    - `open_image_with_lineage`: ダウンロードと同じ、系列の情報を埋め込んだ PNG を返す
      (ADR-0014。系列はその Asset を作った人に見える範囲)。PNG でない原本と、まだ保存して
      いない画像(「判定を待つ」購読、テスト送信)は、原本のバイト列をそのまま返す。
    - `get_run`: Run の id から、`run.finished` の `subject` と同じ形の Run の情報を返す(無ければ
      None)。`asset.ingested` で届いた生成の出力の Run を調べるときに使う。
    - 画像を残さなかった Asset(ADR-0051 4章)を開こうとすると、`ImageWithheld` を出す。
    - `logger`: この Mod のロガー。
    - `submit_verdicts`: 判定を後から送る(ADR-0051 8章。外部のシステムの結果を待ってから
      判定を返したいとき)。対象は、この Mod に届いた Asset・Run だけ(`asset.ingested` /
      `run.finished` で届いたもの。対象外が混じると何も記録せずに例外)。記録した数を返す。
      `deny` の判定は、応答で返したときと同じく画像を残さない処理をする。関数の中で受け取った
      `ctx` を覚えておき、後で呼んでよい。
    """

    mod_id: str
    settings: Mapping[str, Any]
    open_image: Callable[[ImageRef | str], Awaitable[bytes]]
    logger: logging.Logger
    submit_verdicts: Callable[[list[Verdict]], Awaitable[int]] | None = None
    open_image_with_lineage: Callable[[ImageRef | str], Awaitable[bytes]] | None = None
    get_run: Callable[[str], Awaitable[RunFinishedSubject | None]] | None = None


__all__ = [
    "AI_BEFORE_SEND",
    "API_VERSION",
    "ASSET_INGESTED",
    "Actor",
    "AssetIngestedSubject",
    "BeforeSendSubject",
    "ConnectionRef",
    "ContractError",
    "Decision",
    "EVENTS",
    "Event",
    "ImageRef",
    "ImageWithheld",
    "Instance",
    "IMAGE_ROLES",
    "LOCALITIES",
    "ModContext",
    "PURPOSES",
    "RUN_FINISHED",
    "RunError",
    "RunFinishedSubject",
    "SCHEMA_VERSION",
    "SOURCES",
    "Target",
    "TextPart",
    "VERDICTS",
    "Verdict",
    "VerdictTarget",
    "WorkflowRef",
    "decision_from_json",
    "event_from_json",
    "event_to_json",
    "verdicts_from_json",
]
