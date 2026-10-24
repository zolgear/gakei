/**
 * Asset の詳細の上に置く種別のバッジ(リボンと同じ色とアイコンに呼び名を添える)。
 * 生成した画像には何も出さない(`kindRibbon`)。
 */
import { ArrowUpIcon, DownloadIcon, PencilIcon } from '../../components/icons'
import { useI18n } from '../../i18n'
import { kindRibbon } from './kindRibbon'
import { kindBadgeText, kindRibbonLabel } from './kindRibbonLabel'
import styles from './AssetKindBadge.module.css'

interface AssetKindBadgeProps {
  kind: string
  imported?: boolean
}

export function AssetKindBadge({ kind, imported = false }: AssetKindBadgeProps) {
  const { t } = useI18n()
  const ribbon = kindRibbon(kind, imported)
  if (!ribbon) return null
  return (
    <span className={styles.badge} data-tone={ribbon.tone} title={kindRibbonLabel(t, ribbon.label)}>
      {ribbon.tone === 'upload' ? (
        <ArrowUpIcon size={12} />
      ) : ribbon.tone === 'imported' ? (
        <DownloadIcon size={12} />
      ) : (
        <PencilIcon size={12} />
      )}
      {kindBadgeText(t, ribbon.label)}
    </span>
  )
}
