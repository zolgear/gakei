/**
 * Asset の詳細(ビューアの情報欄など)に出す、Mod の判定(ADR-0051 5章「画面」)。
 *
 * - `WithheldNotice`: 画像を残さなかった Asset の上部に、その理由(`content_withheld_verdict`)を出す。
 * - `AssetVerdictsSection`: 「判定」の節。Mod ごとの最新の判定を出す。判定が無ければ節を出さない。
 */
import type { AssetDetail } from '../../api/client'
import { fmt, useI18n } from '../../i18n'
import { formatDateTime } from '../../lib/format'
import sectionStyles from '../viewer/AssetAnnotationSection.module.css'
import { isWithheld } from './modVerdicts'
import { VerdictItem, VerdictList } from './VerdictList'
import { ImageWithheldIcon } from './WithheldPlaceholder'
import styles from './Verdicts.module.css'

type AssetVerdictFields = Pick<AssetDetail, 'content_withheld' | 'content_withheld_at' | 'content_withheld_verdict' | 'verdicts'>

export function WithheldNotice({ asset }: { asset: AssetVerdictFields }) {
  const { t } = useI18n()
  const m = t.mods
  if (!isWithheld(asset)) return null
  const verdict = asset.content_withheld_verdict ?? null
  return (
    <div className={styles.withheldBanner} role="note">
      <p className={styles.withheldTitle}>
        <ImageWithheldIcon size={14} />
        {m.withheld}
      </p>
      <p className={styles.withheldText}>
        {asset.content_withheld_at
          ? fmt(m.withheldNoticeAt, { date: formatDateTime(asset.content_withheld_at) })
          : m.withheldNotice}
      </p>
      {verdict ? (
        <VerdictItem verdict={verdict} />
      ) : (
        <p className={styles.withheldText}>{m.withheldNoVerdict}</p>
      )}
    </div>
  )
}

export function AssetVerdictsSection({ asset }: { asset: AssetVerdictFields }) {
  const { t } = useI18n()
  const verdicts = asset.verdicts ?? []
  if (verdicts.length === 0) return null
  return (
    <section className={sectionStyles.section} aria-label={t.mods.verdictsHeading}>
      <h3 className={sectionStyles.heading}>{t.mods.verdictsHeading}</h3>
      <VerdictList verdicts={verdicts} />
    </section>
  )
}
