/**
 * 読み取りの要約(ADR-0052 4章)。何項目が決まり、何項目を確かめるべきかを一文で出し、
 * 確信度ごとの件数と、畳んだ凡例を添える。編集画面では、保存済みの値と今の読み取りの違いの件数を出す。
 */
import { fmt, useI18n } from '../../i18n'
import type { ReadingSummary as Summary, RowBadge } from './bindingCards'
import { ConfidenceBadge } from './BindingRows'
import styles from './BindingCards.module.css'

const BADGE_ORDER: RowBadge[] = ['marked', 'unique', 'ranked', 'ask', 'absent', 'manual', 'saved']
const LEGEND: RowBadge[] = ['marked', 'unique', 'ranked', 'ask', 'absent', 'manual', 'saved']

interface ReadingSummaryProps {
  summary: Summary
  /** 編集画面で、保存済みの値と今の読み取りが違う項目の数(新規登録では null)。 */
  differCount: number | null
  /** 読み取りに失敗した(保存済みの値だけで開いた)。 */
  noReading: boolean
}

export function ReadingSummary({ summary, differCount, noReading }: ReadingSummaryProps) {
  const { t } = useI18n()
  const c = t.comfyui.cards
  let headline: string
  if (noReading) headline = c.summaryNoReading
  else if (differCount !== null)
    headline = differCount === 0 ? c.summarySavedMatch : fmt(c.summarySavedDiffer, { count: differCount })
  else if (summary.toConfirm === 0) headline = fmt(c.summaryAllDecided, { total: summary.total })
  else
    headline = fmt(c.summaryToConfirm, {
      total: summary.total,
      decided: summary.decided,
      count: summary.toConfirm,
    })

  return (
    <div className={styles.summary}>
      <p className={styles.summaryHeadline}>{headline}</p>
      <div className={styles.tally}>
        {BADGE_ORDER.filter((badge) => summary.counts[badge]).map((badge) => (
          <span key={badge} className={styles.tallyItem}>
            <ConfidenceBadge badge={badge} />
            <span className={styles.tallyCount}>{summary.counts[badge]}</span>
          </span>
        ))}
      </div>
      <details className={styles.mini}>
        <summary>{c.legendSummary}</summary>
        <dl className={styles.legend}>
          {LEGEND.map((badge) => (
            <div key={badge} className={styles.legendItem}>
              <dt>
                <ConfidenceBadge badge={badge} />
              </dt>
              <dd>{c.badgeTips[badge]}</dd>
            </div>
          ))}
        </dl>
      </details>
    </div>
  )
}
