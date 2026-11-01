"""IP 素材の状況のレポート(ADR-0059 8章)と、素材の原本の破棄(9章)。

- レポートは、期間(日付。両端を含む。UTC)を区切って、記録(`run_ip_use`、`audit_event`、来歴)
  から作る。画面(JSON)、CSV(利用の1件ずつ)、権利者に渡す報告書(1つの HTML)の3つで出す。
- 報告書に実行者の名前を入れるかは、書き出すときに選ぶ(既定は入れない。ADR-0037 と同じ)。
  入れないときは「利用者 1」などの仮の名前にする(同じ報告書の中では同じ人に同じ名前)。
- 破棄は、IP が終了した後に、担当者か管理者が行う。素材の原本と派生画像を消し、Asset の行・来歴・
  記録は残す(ADR-0051 4章の「画像を残さない」と同じ状態の列を使う)。同じ内容の原本を IP 素材で
  ない Asset が共有していれば、そのファイルは消さず、そのことを証跡に残す。
"""

from __future__ import annotations

import csv
import html
import io
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.domain import ip_materials
from app.domain.content_withholding import PurgeTarget, _drop_image_products
from app.domain.models import (
    AppUser,
    Asset,
    AssetIpLineage,
    AuditEvent,
    IpGrant,
    IpMaterial,
    IpProperty,
    IpTerms,
    Run,
    RunInput,
    RunIpUse,
    Team,
)
from app.i18n import t

# 証跡のうち、レポートに数える操作。
_COUNTED_ACTIONS = (
    "asset.view_detail",
    "asset.download_original",
    "export.lineage",
    "ip.denied",
    "ip.grant_add",
    "ip.grant_revoke",
    "ip.material_add",
    "ip.material_withdraw",
    "ip.material_destroy",
    "ip.terms_add",
    "ip.state_change",
)


class IpReportPeriodError(ValueError):
    """期間が正しくない(開始が終了より後)。"""


@dataclass
class UseRow:
    occurred_at: datetime
    run_id: uuid.UUID
    status: str
    provider: str
    model: str
    purpose: str
    work_note: str | None
    user_id: uuid.UUID | None
    grantee: str
    terms_version: int
    material_ids: list[uuid.UUID]
    output_count: int


@dataclass
class MaterialRow:
    asset_id: uuid.UUID
    sha256: str
    added_at: datetime
    withdrawn_at: datetime | None
    destroyed: bool
    use_count: int


@dataclass
class IpReport:
    ip: IpProperty
    start: date
    end: date
    generated_at: datetime
    terms: list[IpTerms]
    uses: list[UseRow]
    materials: list[MaterialRow]
    derived_count: int
    actions: Counter[str] = field(default_factory=Counter)
    denied_reasons: Counter[str] = field(default_factory=Counter)
    grant_events: list[AuditEvent] = field(default_factory=list)
    user_names: dict[uuid.UUID, str] = field(default_factory=dict)

    def counter(self, key: str) -> Counter[str]:
        values: Counter[str] = Counter()
        for row in self.uses:
            if key == "purpose":
                values[row.purpose] += 1
            elif key == "work_note":
                values[row.work_note or ""] += 1
            elif key == "grantee":
                values[row.grantee] += 1
            elif key == "provider":
                values[f"{row.provider} / {row.model}"] += 1
            elif key == "user":
                values[self.user_label(row.user_id)] += 1
        return values

    def user_label(self, user_id: uuid.UUID | None) -> str:
        if user_id is None:
            return "—"
        return self.user_names.get(user_id, "—")


def _bounds(start: date, end: date) -> tuple[datetime, datetime]:
    if start > end:
        raise IpReportPeriodError
    return (
        datetime.combine(start, time.min, tzinfo=UTC),
        datetime.combine(end + timedelta(days=1), time.min, tzinfo=UTC),
    )


def default_period(ip: IpProperty) -> tuple[date, date]:
    """既定の期間(IP を作った日から今日まで)。"""
    return ip.created_at.astimezone(UTC).date(), datetime.now(UTC).date()


def build_report(
    db: Session, ip: IpProperty, start: date, end: date, *, include_names: bool
) -> IpReport:
    lower, upper = _bounds(start, end)
    in_period = and_(RunIpUse.created_at >= lower, RunIpUse.created_at < upper)
    material_ids = {
        m.asset_id: m
        for m in db.execute(select(IpMaterial).where(IpMaterial.ip_id == ip.id)).scalars().all()
    }

    rows = db.execute(
        select(RunIpUse, Run, IpTerms.version, IpGrant, Team, AppUser)
        .join(Run, Run.id == RunIpUse.run_id)
        .join(IpTerms, IpTerms.id == RunIpUse.terms_id)
        .join(IpGrant, IpGrant.id == RunIpUse.grant_id)
        .outerjoin(Team, Team.id == IpGrant.team_id)
        .outerjoin(AppUser, AppUser.id == IpGrant.user_id)
        .where(RunIpUse.ip_id == ip.id, in_period)
        .order_by(RunIpUse.created_at, RunIpUse.id)
    ).all()
    run_ids = [use.run_id for use, *_ in rows]
    inputs: dict[uuid.UUID, list[uuid.UUID]] = {}
    for run_id, asset_id in db.execute(
        select(RunInput.run_id, RunInput.asset_id).where(RunInput.run_id.in_(run_ids))
    ).all():
        if asset_id in material_ids:
            inputs.setdefault(run_id, []).append(asset_id)
    outputs = dict(
        db.execute(
            select(Asset.produced_by_run_id, func.count())
            .where(Asset.produced_by_run_id.in_(run_ids))
            .group_by(Asset.produced_by_run_id)
        ).all()
    )

    # 実行者の表示名。名前を入れないときは「利用者 N」(初めて出た順)。
    user_ids = [run.created_by_user_id for _use, run, *_ in rows if run.created_by_user_id]
    names: dict[uuid.UUID, str] = {}
    if include_names and user_ids:
        for user in db.execute(select(AppUser).where(AppUser.id.in_(set(user_ids)))).scalars():
            names[user.id] = user.name or user.email or str(user.id)
    else:
        for user_id in user_ids:
            if user_id not in names:
                names[user_id] = t("ipReport.anonymousUser", number=len(names) + 1)

    uses: list[UseRow] = []
    for use, run, version, _grant, team, grant_user in rows:
        if team is not None:
            grantee = t("ipReport.grantTeam", name=team.name)
        elif include_names and grant_user is not None:
            grantee = grant_user.name or grant_user.email or "—"
        else:
            grantee = t("ipReport.grantPerson")
        uses.append(
            UseRow(
                occurred_at=use.created_at,
                run_id=run.id,
                status=str(run.status),
                provider=run.provider,
                model=run.model,
                purpose=use.purpose,
                work_note=use.work_note,
                user_id=run.created_by_user_id,
                grantee=grantee,
                terms_version=version,
                material_ids=inputs.get(run.id, []),
                output_count=int(outputs.get(run.id, 0)),
            )
        )

    use_counts: Counter[uuid.UUID] = Counter()
    for row in uses:
        for asset_id in row.material_ids:
            use_counts[asset_id] += 1
    assets = {
        a.id: a for a in db.execute(select(Asset).where(Asset.id.in_(list(material_ids)))).scalars()
    }
    materials = [
        MaterialRow(
            asset_id=asset_id,
            sha256=assets[asset_id].sha256 if asset_id in assets else "",
            added_at=material.added_at,
            withdrawn_at=material.withdrawn_at,
            destroyed=asset_id in assets and assets[asset_id].content_withheld_at is not None,
            use_count=use_counts.get(asset_id, 0),
        )
        for asset_id, material in sorted(material_ids.items(), key=lambda kv: kv[1].added_at)
    ]

    derived_count = db.execute(
        select(func.count())
        .select_from(AssetIpLineage)
        .join(Asset, Asset.id == AssetIpLineage.asset_id)
        .where(
            AssetIpLineage.ip_id == ip.id,
            Asset.id.not_in(list(material_ids) or [uuid.UUID(int=0)]),
            Asset.created_at >= lower,
            Asset.created_at < upper,
        )
    ).scalar_one()

    events = list(
        db.execute(
            select(AuditEvent)
            .where(
                AuditEvent.ip_id == ip.id,
                AuditEvent.action.in_(_COUNTED_ACTIONS),
                AuditEvent.occurred_at >= lower,
                AuditEvent.occurred_at < upper,
            )
            .order_by(AuditEvent.occurred_at, AuditEvent.id)
        )
        .scalars()
        .all()
    )
    actions: Counter[str] = Counter(event.action for event in events)
    denied: Counter[str] = Counter(
        str((event.detail or {}).get("reason") or "")
        for event in events
        if event.action == "ip.denied"
    )
    terms = [row for row in ip_materials.all_terms(db, ip.id) if row.created_at < upper]
    return IpReport(
        ip=ip,
        start=start,
        end=end,
        generated_at=datetime.now(UTC),
        terms=terms,
        uses=uses,
        materials=materials,
        derived_count=int(derived_count),
        actions=actions,
        denied_reasons=denied,
        grant_events=[e for e in events if e.action in ("ip.grant_add", "ip.grant_revoke")],
        user_names=names,
    )


# -- CSV -----------------------------------------------------------------------------------

CSV_COLUMNS = (
    "occurred_at",
    "run_id",
    "status",
    "purpose",
    "work_note",
    "user",
    "grantee",
    "terms_version",
    "provider",
    "model",
    "material_asset_ids",
    "output_count",
)


def report_csv(report: IpReport) -> str:
    """利用の1件ずつ(1行が1つの Run)。Excel で開けるよう、呼ぶ側で BOM を付ける。"""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_COLUMNS)
    for row in report.uses:
        writer.writerow(
            [
                row.occurred_at.astimezone(UTC).isoformat(),
                str(row.run_id),
                row.status,
                row.purpose,
                row.work_note or "",
                report.user_label(row.user_id),
                row.grantee,
                row.terms_version,
                row.provider,
                row.model,
                " ".join(str(a) for a in row.material_ids),
                row.output_count,
            ]
        )
    return buf.getvalue()


# -- 報告書(HTML) --------------------------------------------------------------------------


def _e(value: object) -> str:
    return html.escape("" if value is None else str(value))


def _table(headers: list[str], rows: list[list[object]]) -> str:
    head = "".join(f"<th>{_e(h)}</th>" for h in headers)
    if not rows:
        body = f'<tr><td colspan="{len(headers)}" class="muted">{_e(t("ipReport.none"))}</td></tr>'
    else:
        body = "".join("<tr>" + "".join(f"<td>{_e(c)}</td>" for c in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _counter_rows(counter: Counter[str], empty_label: str) -> list[list[object]]:
    return [[key or empty_label, count] for key, count in counter.most_common()]


def report_html(report: IpReport) -> str:
    """権利者に渡す報告書(1つの HTML。画像は入れない)。"""
    ip = report.ip
    external = {
        "allow": t("ipReport.externalAllow"),
        "local_only": t("ipReport.externalLocalOnly"),
        "deny": t("ipReport.externalDeny"),
    }
    succeeded = sum(1 for u in report.uses if u.status == "succeeded")
    outputs = sum(u.output_count for u in report.uses)
    sections = [
        f"<h1>{_e(t('ipReport.title', name=ip.name))}</h1>",
        '<dl class="meta">'
        f"<dt>{_e(t('ipReport.rightsHolder'))}</dt><dd>{_e(ip.rights_holder or '—')}</dd>"
        f"<dt>{_e(t('ipReport.period'))}</dt>"
        f"<dd>{_e(report.start.isoformat())} 〜 {_e(report.end.isoformat())}</dd>"
        f"<dt>{_e(t('ipReport.generatedAt'))}</dt>"
        f"<dd>{_e(report.generated_at.isoformat(timespec='seconds'))}</dd>"
        f"<dt>{_e(t('ipReport.state'))}</dt><dd>{_e(t('ipReport.states.' + ip.state))}</dd>"
        "</dl>",
        f"<h2>{_e(t('ipReport.summary'))}</h2>",
        _table(
            [t("ipReport.item"), t("ipReport.count")],
            [
                [t("ipReport.uses"), len(report.uses)],
                [t("ipReport.usesSucceeded"), succeeded],
                [t("ipReport.outputs"), outputs],
                [t("ipReport.derived"), report.derived_count],
                [t("ipReport.originalDownloads"), report.actions.get("asset.download_original", 0)],
                [t("ipReport.detailViews"), report.actions.get("asset.view_detail", 0)],
                [t("ipReport.lineageExports"), report.actions.get("export.lineage", 0)],
                [t("ipReport.denied"), report.actions.get("ip.denied", 0)],
            ],
        ),
        f"<p class='note'>{_e(t('ipReport.outsideNote'))}</p>",
        f"<h2>{_e(t('ipReport.terms'))}</h2>",
        _table(
            [
                t("ipReport.version"),
                t("ipReport.contractNumber"),
                t("ipReport.contractPeriod"),
                t("ipReport.purposes"),
                t("ipReport.externalAi"),
                t("ipReport.recordedAt"),
            ],
            [
                [
                    row.version,
                    row.contract_number or "—",
                    f"{row.valid_from or '—'} 〜 {row.valid_until or '—'}",
                    "、".join(row.purposes or []),
                    external.get(row.external_ai, row.external_ai),
                    row.created_at.isoformat(timespec="seconds"),
                ]
                for row in report.terms
            ],
        ),
        f"<h2>{_e(t('ipReport.byPurpose'))}</h2>",
        _table(
            [t("ipReport.purpose"), t("ipReport.count")],
            _counter_rows(report.counter("purpose"), "—"),
        ),
        f"<h2>{_e(t('ipReport.byWorkNote'))}</h2>",
        _table(
            [t("ipReport.workNote"), t("ipReport.count")],
            _counter_rows(report.counter("work_note"), t("ipReport.noWorkNote")),
        ),
        f"<h2>{_e(t('ipReport.byGrantee'))}</h2>",
        _table(
            [t("ipReport.grantee"), t("ipReport.count")],
            _counter_rows(report.counter("grantee"), "—"),
        ),
        f"<h2>{_e(t('ipReport.byUser'))}</h2>",
        _table(
            [t("ipReport.user"), t("ipReport.count")], _counter_rows(report.counter("user"), "—")
        ),
        f"<h2>{_e(t('ipReport.byProvider'))}</h2>",
        _table(
            [t("ipReport.provider"), t("ipReport.count")],
            _counter_rows(report.counter("provider"), "—"),
        ),
        f"<h2>{_e(t('ipReport.materials'))}</h2>",
        _table(
            [
                t("ipReport.materialId"),
                t("ipReport.sha256"),
                t("ipReport.addedAt"),
                t("ipReport.materialState"),
                t("ipReport.useCount"),
            ],
            [
                [
                    str(m.asset_id),
                    m.sha256[:16],
                    m.added_at.isoformat(timespec="seconds"),
                    t("ipReport.materialDestroyed")
                    if m.destroyed
                    else t("ipReport.materialWithdrawn")
                    if m.withdrawn_at
                    else t("ipReport.materialActive"),
                    m.use_count,
                ]
                for m in report.materials
            ],
        ),
        f"<h2>{_e(t('ipReport.deniedReasons'))}</h2>",
        _table(
            [t("ipReport.reason"), t("ipReport.count")],
            [
                [t(key, name=ip.name) if key.startswith("ip.") else key or "—", count]
                for key, count in report.denied_reasons.most_common()
            ],
        ),
        f"<h2>{_e(t('ipReport.useList'))}</h2>",
        _table(
            [
                t("ipReport.date"),
                t("ipReport.purpose"),
                t("ipReport.workNote"),
                t("ipReport.user"),
                t("ipReport.grantee"),
                t("ipReport.version"),
                t("ipReport.outputs"),
            ],
            [
                [
                    u.occurred_at.isoformat(timespec="seconds"),
                    u.purpose,
                    u.work_note or "",
                    report.user_label(u.user_id),
                    u.grantee,
                    u.terms_version,
                    u.output_count,
                ]
                for u in report.uses
            ],
        ),
    ]
    style = (
        "body{font-family:system-ui,sans-serif;margin:32px auto;max-width:960px;padding:0 16px;"
        "color:#1b1c1f;line-height:1.5}h1{font-size:22px}h2{font-size:16px;margin-top:28px}"
        "table{border-collapse:collapse;width:100%;font-size:13px}th,td{border:1px solid #ccd;"
        "padding:4px 8px;text-align:left;vertical-align:top}th{background:#f2f3f6}"
        "dl.meta{display:grid;grid-template-columns:auto 1fr;gap:4px 16px;font-size:14px}"
        "dt{color:#555}dd{margin:0}.muted,.note{color:#666}.note{font-size:12px}"
    )
    return (
        f'<!doctype html><html lang="{_e(t("ipReport.lang"))}"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{_e(t('ipReport.title', name=ip.name))}</title><style>{style}</style></head>"
        f"<body>{''.join(sections)}</body></html>"
    )


# -- 破棄 -----------------------------------------------------------------------------------


@dataclass
class DestroyResult:
    asset_ids: list[uuid.UUID]
    # 同じ内容の原本を IP 素材でない Asset が共有しているので、ファイルを消さなかったもの。
    kept_shared: list[uuid.UUID]
    targets: list[PurgeTarget]


def destroy_materials(db: Session, ip: IpProperty) -> DestroyResult:
    """IP の素材の原本と派生画像を消す(commit はしない。保存先は commit の後に `purge`)。
    Asset の行・来歴・記録は残し、`content_withheld_at` を入れる(画像なしの表示になる)。"""
    now = datetime.now(UTC)
    materials = list(
        db.execute(
            select(Asset)
            .join(IpMaterial, IpMaterial.asset_id == Asset.id)
            .where(IpMaterial.ip_id == ip.id, Asset.content_withheld_at.is_(None))
        )
        .scalars()
        .all()
    )
    destroyed: list[uuid.UUID] = []
    kept_shared: list[uuid.UUID] = []
    targets: dict[str, PurgeTarget] = {}
    ids = {a.id for a in materials}
    for asset in materials:
        asset.content_withheld_at = now
        destroyed.append(asset.id)
        twins = list(
            db.execute(
                select(Asset).where(
                    Asset.sha256 == asset.sha256,
                    Asset.id.not_in(ids),
                    Asset.content_withheld_at.is_(None),
                )
            )
            .scalars()
            .all()
        )
        if twins:
            kept_shared.append(asset.id)
            continue
        target = targets.setdefault(asset.sha256, PurgeTarget(sha256=asset.sha256))
        target.blob_keys.add(asset.blob_key)
    _drop_image_products(db, destroyed, now)
    db.flush()
    return DestroyResult(
        asset_ids=destroyed, kept_shared=kept_shared, targets=list(targets.values())
    )
