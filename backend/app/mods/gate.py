"""「判定を待つ」購読(ADR-0051 2章)。原本をストレージに書く前に Mod を呼び、応答を待つ。

- `asset.ingested`: 取り込み(`domain/assets.ingest`)が、形式と大きさを確かめた後、原本を書く
  前に `IngestGate.judge` を呼ぶ。画像は保存前のバイト列をそのまま渡す(Webhook の multipart、
  Python の Mod の `open_image`)。
- `run.finished`: 生成の出力の取り込み(`worker/runner._finish_run_succeeded`)が、出力を
  まとめて送って判定を待つ(`judge_run_outputs`)。

呼び出しと判定は、取り込みと同じトランザクションで `mod_invocation` と `mod_verdict` に記録する。
`deny`(と、失敗時の扱いが `block` の Mod の失敗・時間切れ)なら、その Asset の原本は一度も
書かない(行だけを作り、`content_withheld_at` を入れる)。同期で呼んだ購読には、同じイベントを
非同期の配達に重ねて積まない(`ModRuntime.enqueue` の `exclude_waiting`)。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.mods import api as contract
from app.mods import events as mods_events

if TYPE_CHECKING:
    from app.domain.assets import PreparedImage
    from app.domain.models import ModVerdict, Run
    from app.mods.runtime import ModRuntime


def prepared_ref(prepared: PreparedImage) -> contract.ImageRef:
    return contract.ImageRef(
        asset_id=str(prepared.asset_id),
        sha256=prepared.sha256,
        width=prepared.width,
        height=prepared.height,
        bytes=len(prepared.data),
        mime=prepared.mime,
    )


@dataclass(frozen=True)
class IngestGate:
    """取り込みの経路ごとの「判定を待つ」購読。`ingest_gate` で作る(購読が無ければ None)。"""

    runtime: ModRuntime
    source: str
    provider: str | None = None

    def subscribers(self) -> list:
        return self.runtime.waiting_subscribers(
            contract.ASSET_INGESTED, source=self.source, provider=self.provider
        )

    def judge(
        self,
        session: Session,
        prepared: PreparedImage,
        *,
        produced_by_run_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
    ) -> ModVerdict | None:
        """`asset.ingested` を同期で呼び、記録する。`deny` ならその判定を返す。"""
        subs = self.subscribers()
        if not subs:
            return None
        subject = contract.AssetIngestedSubject(
            asset=prepared_ref(prepared),
            kind=str(prepared.kind),
            source=self.source,
            run_id=str(produced_by_run_id) if produced_by_run_id else None,
        )
        event = self.runtime.make_event(
            contract.ASSET_INGESTED,
            subject,
            mods_events.actor_for_user_id(session, created_by_user_id),
        )
        results = self.runtime.judge_blocking(subs, event, {prepared.sha256: prepared.data})
        denials = self.runtime.record_judgement(
            session, event, results, asset_id=prepared.asset_id, run_id=produced_by_run_id
        )
        return denials.get(prepared.asset_id)


def ingest_gate(
    mods: ModRuntime | None, source: str, provider: str | None = None
) -> IngestGate | None:
    """その経路に「判定を待つ」`asset.ingested` の購読があれば IngestGate、無ければ None。"""
    if mods is None:
        return None
    gate = IngestGate(runtime=mods, source=source, provider=provider)
    return gate if gate.subscribers() else None


def judge_run_outputs(
    mods: ModRuntime | None,
    session: Session,
    run: Run,
    prepared: Sequence[PreparedImage],
    *,
    usage: dict | None,
) -> dict[uuid.UUID, ModVerdict]:
    """生成の出力を取り込む前に、「判定を待つ」購読を呼ぶ(出力ごとの `asset.ingested` と、出力を
    まとめた `run.finished`)。記録して、`deny` を受けた出力ごとの判定を返す。

    `asset.ingested` で拒否された出力の画像は、`run.finished` には送らない(メタ情報だけ)。
    """
    if mods is None:
        return {}
    denials: dict[uuid.UUID, ModVerdict] = {}
    gate = ingest_gate(mods, "generated", run.provider)
    if gate is not None:
        for item in prepared:
            verdict = gate.judge(
                session,
                item,
                produced_by_run_id=run.id,
                created_by_user_id=run.created_by_user_id,
            )
            if verdict is not None:
                denials.setdefault(item.asset_id, verdict)
    subs = mods.waiting_subscribers(contract.RUN_FINISHED, provider=run.provider)
    if subs:
        subject = mods_events.run_finished_subject(
            session,
            run,
            status="succeeded",
            outputs=[prepared_ref(item) for item in prepared],
            usage=usage,
        )
        event = mods.make_event(
            contract.RUN_FINISHED,
            subject,
            mods_events.actor_for_user_id(session, run.created_by_user_id),
        )
        extras = {item.sha256: item.data for item in prepared if item.asset_id not in denials}
        results = mods.judge_blocking(subs, event, extras)
        for asset_id, verdict in mods.record_judgement(
            session, event, results, run_id=run.id
        ).items():
            denials.setdefault(asset_id, verdict)
    return denials
