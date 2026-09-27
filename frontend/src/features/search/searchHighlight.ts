/**
 * 検索結果の snippet 内の一致語を強調するための分割。`<mark>` を JSX 側で当てるために、
 * テキストを「強調する/しない」のセグメント列にする。純粋関数のみ(副作用なし)。
 */
export interface HighlightSegment {
  text: string
  highlighted: boolean
}

interface MatchRange {
  start: number
  end: number
}

function findMatchRanges(text: string, terms: string[]): MatchRange[] {
  const lowerText = text.toLowerCase()
  const ranges: MatchRange[] = []
  for (const term of terms) {
    const lowerTerm = term.toLowerCase()
    if (lowerTerm.length === 0) continue
    let fromIndex = 0
    while (fromIndex <= lowerText.length) {
      const idx = lowerText.indexOf(lowerTerm, fromIndex)
      if (idx === -1) break
      ranges.push({ start: idx, end: idx + lowerTerm.length })
      fromIndex = idx + lowerTerm.length
    }
  }
  return ranges
}

/** 重なる・隣接する範囲をまとめる(元の配列は変更しない)。 */
function mergeRanges(ranges: MatchRange[]): MatchRange[] {
  if (ranges.length === 0) return []
  const sorted = [...ranges].sort((a, b) => a.start - b.start)
  const merged: MatchRange[] = [{ ...sorted[0] }]
  for (const r of sorted.slice(1)) {
    const last = merged[merged.length - 1]
    if (r.start <= last.end) {
      last.end = Math.max(last.end, r.end)
    } else {
      merged.push({ ...r })
    }
  }
  return merged
}

/**
 * text のうち、query の各語(空白区切り、AND ではなく「どれかに一致すれば強調」)に
 * 一致する範囲を強調セグメントとして分割する。大文字小文字は区別しない。
 * query が空、または一致箇所が無ければ、text 全体を1つの非強調セグメントとして返す。
 */
export function splitHighlightSegments(text: string, query: string): HighlightSegment[] {
  const terms = query.trim().split(/\s+/).filter(Boolean)
  if (terms.length === 0 || text.length === 0) {
    return [{ text, highlighted: false }]
  }

  const ranges = mergeRanges(findMatchRanges(text, terms))
  if (ranges.length === 0) {
    return [{ text, highlighted: false }]
  }

  const segments: HighlightSegment[] = []
  let cursor = 0
  for (const range of ranges) {
    if (range.start > cursor) {
      segments.push({ text: text.slice(cursor, range.start), highlighted: false })
    }
    segments.push({ text: text.slice(range.start, range.end), highlighted: true })
    cursor = range.end
  }
  if (cursor < text.length) {
    segments.push({ text: text.slice(cursor), highlighted: false })
  }
  return segments
}
