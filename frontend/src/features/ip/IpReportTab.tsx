/**
 * IP のページの「レポート」(ADR-0059 8章。担当者と管理者)。期間を選んで、利用の件数・用途別・
 * 作業のメモ別・許可の先別・実行者別・送り先別・素材ごとの利用・拒否の理由を出す。権利者に渡す
 * 報告書(HTML)と、利用の1件ずつ(CSV)を書き出せる。実行者の名前は、書き出すときに入れるかを選ぶ
 * (既定は入れない)。
 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getIpReport, ipReportDownloadUrl, type IpDetail, type IpReportView } from '../../api/client'
import { formatDateTime } from '../../lib/format'
import { useI18n } from '../../i18n'
import { IP_QUERY_KEY } from './ipQueries'
import styles from './Ip.module.css'

type Counts = IpReportView['by_purpose']

interface CountTableProps {
  title: string
  rows: Counts | undefined
  noneLabel: string
  empty?: string
}

function CountTable({ title, rows, noneLabel, empty }: CountTableProps) {
  return (
    <article className={styles.card}>
      <h3 className={styles.cardTitle}>{title}</h3>
      {!rows || rows.length === 0 ? (
        <p className={styles.muted}>{noneLabel}</p>
      ) : (
        <dl className={styles.countList}>
          {rows.map((row) => (
            <div key={row.key} className={styles.countRow}>
              <dt>{row.key || empty || '—'}</dt>
              <dd>{row.count}</dd>
            </div>
          ))}
        </dl>
      )}
    </article>
  )
}

export function IpReportTab({ ip }: { ip: IpDetail }) {
  const { t } = useI18n()
  const m = t.ip.report
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [includeNames, setIncludeNames] = useState(false)
  const query = useQuery({
    queryKey: [...IP_QUERY_KEY, ip.id, 'report', start, end],
    queryFn: () => getIpReport(ip.id, start || null, end || null),
  })
  const report = query.data

  return (
    <section className={styles.section}>
      <div className={styles.fieldRow}>
        <label className={styles.field}>
          <span>{t.ip.terms.validFrom}</span>
          <input className={styles.input} type="date" value={start} onChange={(e) => setStart(e.target.value)} />
        </label>
        <label className={styles.field}>
          <span>{t.ip.terms.validUntil}</span>
          <input className={styles.input} type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
        </label>
      </div>
      <div className={styles.inlineRow}>
        <a className={styles.secondaryButton} href={ipReportDownloadUrl(ip.id, 'html', start || null, end || null, includeNames)}>
          {m.downloadHtml}
        </a>
        <a className={styles.secondaryButton} href={ipReportDownloadUrl(ip.id, 'csv', start || null, end || null, includeNames)}>
          {m.downloadCsv}
        </a>
        <label className={styles.checkRow}>
          <input type="checkbox" checked={includeNames} onChange={(e) => setIncludeNames(e.target.checked)} />
          {m.includeNames}
        </label>
      </div>
      {query.isError && <p className={styles.errorText}>{m.failed}</p>}
      {report && (
        <>
          <p className={styles.muted}>
            {report.start} 〜 {report.end}
          </p>
          <div className={styles.reportSummary}>
            {(
              [
                [m.uses, report.uses],
                [m.usesSucceeded, report.uses_succeeded],
                [m.outputs, report.outputs],
                [m.derived, report.derived],
                [m.originalDownloads, report.original_downloads],
                [m.detailViews, report.detail_views],
                [m.lineageExports, report.lineage_exports],
                [m.denied, report.denied],
              ] as const
            ).map(([label, value]) => (
              <div key={label} className={styles.stat}>
                <span className={styles.statValue}>{value}</span>
                <span className={styles.muted}>{label}</span>
              </div>
            ))}
          </div>
          <div className={styles.reportGrid}>
            <CountTable title={m.byPurpose} rows={report.by_purpose} noneLabel={m.none} />
            <CountTable title={m.byWorkNote} rows={report.by_work_note} noneLabel={m.none} empty={m.noWorkNote} />
            <CountTable title={m.byGrantee} rows={report.by_grantee} noneLabel={m.none} />
            <CountTable title={m.byUser} rows={report.by_user} noneLabel={m.none} />
            <CountTable title={m.byProvider} rows={report.by_provider} noneLabel={m.none} />
            <CountTable title={m.deniedReasons} rows={report.denied_reasons} noneLabel={m.none} />
          </div>
          <article className={styles.card}>
            <h3 className={styles.cardTitle}>{m.materials}</h3>
            <dl className={styles.countList}>
              {report.materials.map((material) => (
                <div key={material.asset_id} className={styles.countRow}>
                  <dt>
                    {formatDateTime(material.added_at)}
                    {material.destroyed ? ` · ${m.destroyed}` : material.withdrawn_at ? ` · ${t.ip.withdrawn}` : ''}
                  </dt>
                  <dd>{material.use_count}</dd>
                </div>
              ))}
            </dl>
          </article>
        </>
      )}
    </section>
  )
}
