/**
 * Mod の判定の一覧(ADR-0051 4章・5章)。ビューアの詳細と Run の詳細で使う。
 *
 * Mod ごとに1件(API が最新だけを新しい順に返す)。印(ok / flag / deny)、Mod の名前、label、
 * score、message、reference、外部へのリンク、日時を出す。同じ内容の画像の判定からの波及
 * (`propagated`)なら、そのことを添える。
 */
import type { ModVerdictView } from '../../api/client'
import { useI18n } from '../../i18n'
import { formatDateTime } from '../../lib/format'
import { formatVerdictScore, safeExternalUrl, type Verdict } from './modVerdicts'
import styles from './Verdicts.module.css'
import own from './Withheld.module.css'

/** 判定の印。 */
export function VerdictBadge({ verdict }: { verdict: Verdict }) {
  const { t } = useI18n()
  return (
    <span className={own.verdictBadge} data-verdict={verdict}>
      {t.mods.verdicts[verdict]}
    </span>
  )
}

export function VerdictList({ verdicts }: { verdicts: ModVerdictView[] }) {
  return (
    <ul className={styles.list}>
      {verdicts.map((v) => (
        <li key={v.id}>
          <VerdictItem verdict={v} />
        </li>
      ))}
    </ul>
  )
}

export function VerdictItem({ verdict: v }: { verdict: ModVerdictView }) {
  const { t } = useI18n()
  const m = t.mods
  const score = formatVerdictScore(v.score)
  const url = safeExternalUrl(v.url)
  return (
    <div className={styles.item} data-verdict={v.verdict}>
      <div className={styles.itemHeader}>
        <VerdictBadge verdict={v.verdict} />
        <span className={styles.modName}>{v.mod_name}</span>
        <time className={styles.date} dateTime={v.created_at}>
          {formatDateTime(v.created_at)}
        </time>
      </div>
      {v.label && <p className={styles.label}>{v.label}</p>}
      {v.message && <p className={styles.message}>{v.message}</p>}
      {(score || v.reference) && (
        <dl className={styles.meta}>
          {score && (
            <>
              <dt>{m.score}</dt>
              <dd>{score}</dd>
            </>
          )}
          {v.reference && (
            <>
              <dt>{m.reference}</dt>
              <dd className={styles.mono}>{v.reference}</dd>
            </>
          )}
        </dl>
      )}
      {url && (
        <a className={styles.link} href={url} target="_blank" rel="noopener noreferrer">
          {m.openExternal}
        </a>
      )}
      {v.propagated && <p className={styles.note}>{m.propagated}</p>}
    </div>
  )
}
