"""Mod の `deny` の判定の効力と、画像を残さない処理(ADR-0051 4章)。

- **判定の効力:** Asset に対する最新の判定(Mod ごと)が `deny` のものが1つでもあるか、画像を
  残さなかった(`asset.content_withheld_at`)なら、その Asset は外部の AI へ送らない
  (`blocked_asset_ids`)。Run の作成、queued の Run、アシスタントへの添付、VLM、リモートの埋め込み、
  自動タイトル・タグと埋め込みの待ち行列が、これを見る。
- **画像を残さない:** `deny` の判定を受けた Asset は、原本と派生(全版)を消し、状態の列
  (`content_withheld_at`・`content_withheld_verdict_id`)を入れる(`withhold`)。同じ内容
  (sha256)の Asset は原本のファイルを共有するので、ほかの Asset にも及ぼし、及ぼしたことを
  `mod_verdict` の行(`propagated_from_id` で元の判定を指す)で残す。画像から作ったもの(埋め込み、
  知覚ハッシュ、焦点)は消し、タイトル・タグ・レーティングの文字の記録は残す。取り消せない(後で
  `ok` の判定が来ても戻さない)。
- 保存先のファイルは、DB を commit した後に `purge` で消す(DB の状態を先に確定させ、ファイルを
  消せなかったら、次の起動の `purge_leftovers` が消し直す)。

非同期の判定(配達の応答、受付の API、Python の Mod が後で送る判定)と、「判定を待つ」購読の
同期の判定の両方が、ここを通す。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.domain.models import (
    Asset,
    AssetAnnotation,
    AssetContentRating,
    AssetEmbedding,
    AssetFocalPoint,
    AssetPerceptualHash,
    ModInvocation,
    ModVerdict,
)
from app.i18n import t

if TYPE_CHECKING:
    from app.domain.schemas import ModVerdictView, RunModInvocationView
    from app.domain.storage import AssetStore

logger = logging.getLogger(__name__)

VERDICT_DENY = "deny"
_QUEUED_STATUSES = ("queued",)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ContentWithheldError(Exception):
    """画像を残さなかった・`deny` の判定を受けた Asset を、外部の AI に送ろうとした・画像を
    返そうとした。`str(e)` は利用者向けの文言。"""

    def __init__(self, asset_id: uuid.UUID, message: str) -> None:
        super().__init__(message)
        self.asset_id = asset_id


# -- 判定の読み出し --------------------------------------------------------------------


def _latest_per_mod(rows: Iterable[ModVerdict]) -> list[ModVerdict]:
    """(対象、Mod)ごとの最新の判定。`rows` は古い順に並べて渡す。"""
    latest: dict[tuple[uuid.UUID | None, uuid.UUID | None, str, str], ModVerdict] = {}
    for row in rows:
        latest[(row.asset_id, row.run_id, row.mod_kind, row.mod_id)] = row
    return list(latest.values())


def latest_verdicts(
    db: Session, *, asset_id: uuid.UUID | None = None, run_id: uuid.UUID | None = None
) -> list[ModVerdict]:
    """Asset か Run に対する、Mod ごとの最新の判定(新しい順)。"""
    if (asset_id is None) == (run_id is None):
        raise ValueError("asset_id か run_id のどちらか一方を渡す")
    condition = ModVerdict.asset_id == asset_id if asset_id else ModVerdict.run_id == run_id
    rows = (
        db.execute(
            select(ModVerdict)
            .where(condition)
            .order_by(ModVerdict.created_at.asc(), ModVerdict.id.asc())
        )
        .scalars()
        .all()
    )
    result = _latest_per_mod(rows)
    result.sort(key=lambda r: (r.created_at, str(r.id)), reverse=True)
    return result


def denied_asset_ids(db: Session, asset_ids: Iterable[uuid.UUID]) -> set[uuid.UUID]:
    """最新の判定が `deny` の Mod が1つでもある Asset。"""
    ids = list(dict.fromkeys(asset_ids))
    if not ids:
        return set()
    rows = (
        db.execute(
            select(ModVerdict)
            .where(ModVerdict.asset_id.in_(ids))
            .order_by(ModVerdict.created_at.asc(), ModVerdict.id.asc())
        )
        .scalars()
        .all()
    )
    return {
        row.asset_id
        for row in _latest_per_mod(rows)
        if row.verdict == VERDICT_DENY and row.asset_id is not None
    }


def blocked_asset_ids(db: Session, asset_ids: Iterable[uuid.UUID]) -> set[uuid.UUID]:
    """外部の AI へ送らない Asset(画像を残さなかったもの、または最新の判定が `deny` の Mod が
    あるもの)。"""
    ids = list(dict.fromkeys(asset_ids))
    if not ids:
        return set()
    withheld = set(
        db.execute(
            select(Asset.id).where(Asset.id.in_(ids), Asset.content_withheld_at.is_not(None))
        )
        .scalars()
        .all()
    )
    return withheld | denied_asset_ids(db, ids)


def is_withheld(asset: Asset) -> bool:
    return asset.content_withheld_at is not None


def withheld_verdict(db: Session, asset: Asset) -> ModVerdict | None:
    """画像を残さなかった理由の判定(無い・読めないときは None)。"""
    if asset.content_withheld_verdict_id is None:
        return None
    return db.get(ModVerdict, asset.content_withheld_verdict_id)


def withheld_message(db: Session, asset: Asset, *, include_verdict: bool = True) -> str:
    """画像を返さないときの理由の文(410 の本文など)。`include_verdict` が偽なら(共有リンク
    など、判定を見せない相手には)判定の `label` と `message` を付けない。"""
    if include_verdict:
        verdict = withheld_verdict(db, asset)
        if verdict is not None and (verdict.label or verdict.message):
            return t(
                "mods.contentWithheldWithVerdict",
                mod=verdict.mod_name,
                label=verdict.label or "-",
                message=verdict.message or "-",
            )
    return t("mods.contentWithheld")


def ensure_sendable(db: Session, assets: Sequence[Asset]) -> None:
    """外部の AI へ送ってよいかを確かめる。送れない Asset があれば ContentWithheldError。"""
    blocked = blocked_asset_ids(db, [a.id for a in assets])
    for asset in assets:
        if asset.id in blocked:
            raise ContentWithheldError(asset.id, t("mods.assetDenied", id=asset.id))


# -- 画面・API 向けの形 ----------------------------------------------------------------


def verdict_view(row: ModVerdict) -> ModVerdictView:
    from app.domain.schemas import ModVerdictView

    return ModVerdictView(
        id=row.id,
        mod_kind=row.mod_kind,  # type: ignore[arg-type]
        mod_id=row.mod_id,
        mod_name=row.mod_name,
        verdict=row.verdict,  # type: ignore[arg-type]
        label=row.label,
        score=row.score,
        message=row.message,
        reference=row.reference,
        url=row.url,
        propagated=row.propagated_from_id is not None,
        created_at=row.created_at,
    )


def verdict_views(
    db: Session, *, asset_id: uuid.UUID | None = None, run_id: uuid.UUID | None = None
) -> list[ModVerdictView]:
    return [verdict_view(r) for r in latest_verdicts(db, asset_id=asset_id, run_id=run_id)]


def withheld_fields(db: Session, asset: Asset) -> dict:
    """`AssetDetail` の判定と画像を残さなかったことの項目。"""
    verdict = withheld_verdict(db, asset) if is_withheld(asset) else None
    return {
        "verdicts": verdict_views(db, asset_id=asset.id),
        "content_withheld_verdict": verdict_view(verdict) if verdict is not None else None,
    }


def summary_fields(asset: Asset) -> dict:
    """一覧・系列のノードの、画像を残さなかったことの項目。"""
    return {
        "content_withheld": asset.content_withheld_at is not None,
        "content_withheld_at": asset.content_withheld_at,
    }


def run_invocation_views(db: Session, run_id: uuid.UUID) -> list[RunModInvocationView]:
    """Run の `ai.before_send` の呼び出しの記録(古い順)。運用の情報(`error`)は出さない。"""
    from app.domain.schemas import RunModInvocationView

    rows = (
        db.execute(
            select(ModInvocation)
            .where(ModInvocation.run_id == run_id, ModInvocation.event == "ai.before_send")
            .order_by(ModInvocation.created_at.asc(), ModInvocation.id.asc())
        )
        .scalars()
        .all()
    )
    return [
        RunModInvocationView(
            id=row.id,
            mod_kind=row.mod_kind,  # type: ignore[arg-type]
            mod_id=row.mod_id,
            mod_name=row.mod_name,
            event=row.event,
            purpose=row.purpose,
            outcome=row.outcome,
            effective=row.effective,
            reason=row.reason,
            reference=row.reference,
            duration_ms=row.duration_ms,
            created_at=row.created_at,
        )
        for row in rows
    ]


# -- 画像を残さない --------------------------------------------------------------------


@dataclass
class PurgeTarget:
    """保存先から消すもの(同じ内容の原本のキーと、派生の sha256)。"""

    sha256: str
    blob_keys: set[str] = field(default_factory=set)


def _copy_verdict(source: ModVerdict, asset_id: uuid.UUID, now: datetime) -> ModVerdict:
    """同じ内容のほかの Asset に及ぼした判定の行。"""
    return ModVerdict(
        id=uuid.uuid4(),
        mod_kind=source.mod_kind,
        mod_id=source.mod_id,
        mod_name=source.mod_name,
        invocation_id=source.invocation_id,
        event_id=source.event_id,
        asset_id=asset_id,
        run_id=None,
        verdict=source.verdict,
        label=source.label,
        score=source.score,
        message=source.message,
        reference=source.reference,
        url=source.url,
        propagated_from_id=source.propagated_from_id or source.id,
        created_at=now,
    )


def _drop_image_products(db: Session, asset_ids: list[uuid.UUID], now: datetime) -> None:
    """画像から作ったもの(埋め込み、知覚ハッシュ、焦点)を消し、待ち行列にある自動タイトル・
    タグとレーティングを失敗にする(文字の記録は残す)。"""
    if not asset_ids:
        return
    db.execute(delete(AssetEmbedding).where(AssetEmbedding.asset_id.in_(asset_ids)))
    db.execute(delete(AssetPerceptualHash).where(AssetPerceptualHash.asset_id.in_(asset_ids)))
    db.execute(delete(AssetFocalPoint).where(AssetFocalPoint.asset_id.in_(asset_ids)))
    message = t("mods.contentWithheld")
    for row in (
        db.execute(
            select(AssetAnnotation).where(
                AssetAnnotation.asset_id.in_(asset_ids),
                AssetAnnotation.auto_status.in_(_QUEUED_STATUSES),
            )
        )
        .scalars()
        .all()
    ):
        row.auto_status = "failed"
        row.auto_error = message
        row.auto_finished_at = now
        row.updated_at = now
    for row in (
        db.execute(
            select(AssetContentRating).where(
                AssetContentRating.asset_id.in_(asset_ids),
                AssetContentRating.status.in_(_QUEUED_STATUSES),
            )
        )
        .scalars()
        .all()
    ):
        row.status = "failed"
        row.error = message
        row.updated_at = now


def withhold(
    db: Session, verdict: ModVerdict, *, now: datetime | None = None
) -> PurgeTarget | None:
    """`deny` の判定の効力を当てる(commit はしない)。対象の Asset と、同じ内容のほかの Asset を
    「画像なし(拒否)」にし、画像から作ったものを消す。保存先から消すものを返す(commit の後に
    `purge` に渡す)。対象の Asset が無ければ None。"""
    if verdict.verdict != VERDICT_DENY or verdict.asset_id is None:
        return None
    asset = db.get(Asset, verdict.asset_id)
    if asset is None:
        return None
    now = now or _utcnow()
    twins = list(db.execute(select(Asset).where(Asset.sha256 == asset.sha256)).scalars().all())
    for twin in twins:
        if twin.content_withheld_at is not None:
            continue
        if twin.id == verdict.asset_id:
            twin.content_withheld_verdict_id = verdict.id
        else:
            copied = _copy_verdict(verdict, twin.id, now)
            db.add(copied)
            twin.content_withheld_verdict_id = copied.id
        twin.content_withheld_at = now
    _drop_image_products(db, [twin.id for twin in twins], now)
    db.flush()
    return PurgeTarget(sha256=asset.sha256, blob_keys={twin.blob_key for twin in twins})


def apply_denials(db: Session, verdicts: Iterable[ModVerdict]) -> list[PurgeTarget]:
    """足した判定のうち `deny` の効力を当てる(commit はしない)。"""
    targets: dict[str, PurgeTarget] = {}
    for verdict in verdicts:
        target = withhold(db, verdict)
        if target is None:
            continue
        current = targets.setdefault(target.sha256, PurgeTarget(sha256=target.sha256))
        current.blob_keys |= target.blob_keys
    return list(targets.values())


def purge(store: AssetStore, targets: Iterable[PurgeTarget]) -> None:
    """保存先から原本と派生(全版)を消す。失敗は記録して続ける(次の起動で消し直す)。"""
    for target in targets:
        try:
            for key in sorted(target.blob_keys):
                store.delete(key)
            for key in list(store.list_keys(f"derived/{target.sha256}/")):
                store.delete(key)
        except Exception:  # noqa: BLE001 - 判定の記録は済んでいる。ファイルは次の起動で消し直す
            logger.exception("画像を残さない処理で、保存先のファイルを消せませんでした")
    # ほかの経路(MCP・アシスタントの画像)が覚えている縮小画像も捨てる。
    from app.domain import agent_images

    agent_images.forget(target.sha256 for target in targets)


def purge_leftovers(db: Session, store: AssetStore) -> int:
    """起動のときに呼ぶ。画像を残さなかった Asset の原本・派生が保存先に残っていれば消す
    (消す前にサーバーが止まった、保存先に届かなかった、など)。消した内容の数を返す。"""
    rows = db.execute(
        select(Asset.sha256, Asset.blob_key).where(Asset.content_withheld_at.is_not(None))
    ).all()
    by_sha: dict[str, set[str]] = {}
    for sha256, blob_key in rows:
        by_sha.setdefault(sha256, set()).add(blob_key)
    count = 0
    targets: list[PurgeTarget] = []
    for sha256, keys in by_sha.items():
        try:
            leftover = any(store.exists(key) for key in keys) or any(
                True for _ in store.list_keys(f"derived/{sha256}/")
            )
        except Exception:  # noqa: BLE001
            logger.exception("画像を残さなかった Asset の保存先を確かめられませんでした")
            continue
        if leftover:
            targets.append(PurgeTarget(sha256=sha256, blob_keys=keys))
            count += 1
    purge(store, targets)
    return count


def find_withheld_twin(db: Session, sha256: str) -> Asset | None:
    """同じ内容で、画像を残さなかった Asset(後から同じ内容を取り込んだときに使う)。"""
    return (
        db.execute(
            select(Asset)
            .where(Asset.sha256 == sha256, Asset.content_withheld_at.is_not(None))
            .order_by(Asset.content_withheld_at.asc(), Asset.id.asc())
            .limit(1)
        )
        .scalars()
        .first()
    )


def inherit_from_twin(db: Session, twin: Asset, asset_id: uuid.UUID) -> uuid.UUID | None:
    """同じ内容を後から取り込んだ Asset に、元の判定を及ぼした行を足す(commit はしない)。
    足した判定の id を返す(元の判定が無ければ None)。"""
    source = withheld_verdict(db, twin)
    if source is None:
        return None
    copied = _copy_verdict(source, asset_id, _utcnow())
    db.add(copied)
    return copied.id
