/**
 * 履歴の並びの区切り(日、グループ。ADR-0054 5章)。並びの中でカード1枚分(ギャラリーでは1マス)を
 * 占める。大きな題(月日かグループ名)と、分かれば件数だけを出す(説明の文は付けない)。日のバナーは
 * 年の最初の日だけ題の上に年を、題の下に曜日を小さく添える。
 */
import { fmt, useI18n } from '../../i18n'
import type { HistoryBannerItem } from './historySections'
import styles from './HistoryBannerCard.module.css'

interface HistoryBannerCardProps {
  banner: Pick<HistoryBannerItem, 'title' | 'count' | 'eyebrow' | 'caption'>
  /** `card`: 詳細のグリッドのカード1枚分。`tile`: ギャラリーの1マス。 */
  layout: 'card' | 'tile'
  /** 行揃えで決まった大きさ(px)。無ければ幅いっぱいの正方形(石積み)。 */
  size?: { width: number; height: number }
}

export function HistoryBannerCard({ banner, layout, size }: HistoryBannerCardProps) {
  const { t } = useI18n()
  const { title, count, eyebrow, caption } = banner
  const style =
    layout === 'tile' ? (size ? { width: size.width, height: size.height } : { width: '100%', aspectRatio: '1' }) : undefined
  return (
    <section className={styles.banner} data-layout={layout} style={style}>
      {eyebrow && <span className={styles.eyebrow}>{eyebrow}</span>}
      <h2 className={styles.title} title={title}>
        {title}
      </h2>
      {caption && <span className={styles.caption}>{caption}</span>}
      {count != null && <span className={styles.count}>{fmt(t.history.page.bannerCount, { count })}</span>}
    </section>
  )
}
