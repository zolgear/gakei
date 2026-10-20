/** 領域の名前のチップ(`RegionLabelChips`)に並べる物を作る純粋関数(ADR-0053)。 */
import type { RegionBox } from '../../api/client'
import { fmt, type Messages } from '../../i18n'
import type { RegionHighlight } from './RegionOverlay'
import { orderedRegions, regionForCandidate, regionLabel } from './regions'
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
  /** 検出の候補のチップ(まだ四角になっておらず、送られない)。押すと四角にする。 */
  candidate?: Pick<RegionCandidate, 'key' | 'box' | 'label'>
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

/**
 * 入力の補助の、検出の候補のチップ。すでに四角にした候補(同じ四角がある)は出さない(「四角 n 名前」の
 * チップになる)。同じ名前で同じ位置の候補は1つにする。
 */
export function candidateChipItems(
  candidates: readonly Pick<RegionCandidate, 'key' | 'box' | 'label'>[],
  regions: readonly RegionBox[],
): RegionChipItem[] {
  const seen = new Set<string>()
  const items: RegionChipItem[] = []
  for (const c of candidates) {
    const label = c.label.trim()
    if (!label || regionForCandidate(regions, c)) continue
    const id = `${label}|${c.box.join(',')}`
    if (seen.has(id)) continue
    seen.add(id)
    items.push({ key: `candidate:${c.key}`, text: label, highlight: { type: 'label', label: c.label }, candidate: c })
  }
  return items
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
