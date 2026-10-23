import type { Messages } from '../../i18n'
import type { KindRibbon } from './kindRibbon'

/**
 * 種別の呼び名。リボンのツールチップと、詳細の上のバッジで同じ呼び名にする。
 * アップロードは利用者の言い方に合わせて「ストック」、取り込みは「インポート」。
 */
export function kindRibbonLabel(t: Messages, label: KindRibbon['label']): string {
  if (label === 'imported') return t.common.assetKindRibbon.imported
  if (label === 'upload') return t.common.assetKindRibbon.upload
  return t.common.assetKindLabels[label]
}

/** バッジに出す短い呼び名(取り込みは「(未検証)」を省き、ツールチップに回す)。 */
export function kindBadgeText(t: Messages, label: KindRibbon['label']): string {
  return label === 'imported' ? t.common.assetKindRibbon.importedShort : kindRibbonLabel(t, label)
}
