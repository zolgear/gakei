/**
 * 履歴の節の見出し(日、グループ。ADR-0054 5章)。枠も背景も無い太字の文字だけを、節の頭に全幅で
 * 置く。グループの並びでは名前の後ろに件数を添える。グループの先頭だけを出している節には、右端に
 * 「すべて見る」を置き、押すとそのグループで絞り込む(6章。`ShowAllGroupContext` で渡す)。
 */
import { useContext } from 'react'
import { fmt, useI18n } from '../../i18n'
import type { HistorySectionHeading as Heading } from './historySections'
import { ShowAllGroupContext } from './showAllGroupContext'
import styles from './HistorySectionHeading.module.css'

export function HistorySectionHeading({ heading }: { heading: Heading }) {
  const { t } = useI18n()
  const showAll = useContext(ShowAllGroupContext)
  const target = heading.showAll
  return (
    <h2 className={styles.heading}>
      <span className={styles.title}>{heading.title}</span>
      {heading.count != null && (
        <span className={styles.count}>{fmt(t.history.page.sectionCount, { count: heading.count })}</span>
      )}
      {showAll && target !== undefined && (
        <button type="button" className={styles.showAll} onClick={() => showAll(target)}>
          {t.history.page.sectionShowAll}
        </button>
      )}
    </h2>
  )
}
