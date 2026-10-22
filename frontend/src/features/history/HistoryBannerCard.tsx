/**
 * 履歴の並びの区切り(年、グループ。ADR-0054 5章)。並びの中でカード1枚分(ギャラリーでは1マス)を
 * 占める。大きな年かグループ名と、分かれば件数だけを出す(説明の文は付けない)。
 */
import { fmt, useI18n } from '../../i18n'
import styles from './HistoryBannerCard.module.css'

interface HistoryBannerCardProps {
  title: string
  count: number | null
  /** `card`: 詳細のグリッドのカード1枚分。`tile`: ギャラリーの1マス。 */
  layout: 'card' | 'tile'
  /** 行揃えで決まった大きさ(px)。無ければ幅いっぱいの正方形(石積み)。 */
  size?: { width: number; height: number }
}

export function HistoryBannerCard({ title, count, layout, size }: HistoryBannerCardProps) {
  const { t } = useI18n()
  const style =
    layout === 'tile' ? (size ? { width: size.width, height: size.height } : { width: '100%', aspectRatio: '1' }) : undefined
  return (
    <section className={styles.banner} data-layout={layout} style={style}>
      <h2 className={styles.title} title={title}>
        {title}
      </h2>
      {count !== null && <span className={styles.count}>{fmt(t.history.page.bannerCount, { count })}</span>}
    </section>
  )
}
