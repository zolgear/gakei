/** 領域の名前のチップ(`RegionLabelChips`)に並べる物を作る純粋関数(ADR-0053)。 */
import type { RegionBox } from '../../api/client'
import { fmt, type Messages } from '../../i18n'
import type { RegionHighlight } from './RegionOverlay'
import { orderedRegions, regionLabel } from './regions'
import type { RegionCandidate } from './useRegionDetection'

export interface RegionChipItem {
  key: string
  /** 入れる文字。 */
  text: string
  /** チップに出す文字(省くと `text`)。 */
  display?: string
  /** 四角のチップの色(並び順の色)。 */
  colorIndex?: number
  highlight: RegionHighlight
}

/** 「四角 1」…のチップ(番号の順)。名前があれば添えて見せる(入れるのは「四角 1」だけ)。 */
export function regionChipItems(t: Messages, regions: readonly RegionBox[]): RegionChipItem[] {
  return orderedRegions(regions).map((r, index) => {
    const text = fmt(t.regions.assist.boxChip, { n: r.number })
    const label = regionLabel(r)
    return {
      key: `region:${r.number}`,
      text,
      display: label ? `${text} ${label}` : text,
      colorIndex: index,
      highlight: { type: 'region', number: r.number },
    }
  })
}

/** 検出の候補の名前のチップ(同じ名前は1つ)。 */
export function labelChipItems(candidates: readonly Pick<RegionCandidate, 'label'>[]): RegionChipItem[] {
  const seen = new Set<string>()
  const items: RegionChipItem[] = []
  for (const c of candidates) {
    const label = c.label.trim()
    if (!label || seen.has(label)) continue
    seen.add(label)
    items.push({ key: `label:${label}`, text: label, highlight: { type: 'label', label } })
  }
  return items
}
