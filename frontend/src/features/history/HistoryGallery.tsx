/**
 * 履歴の「画像」の形。タイルを石積み(masonry)に並べる。列の数はコンテナの幅から決め
 * (ResizeObserver。768px 未満は 2 列)、短い列から順に積む(`assignMasonryColumns`)。
 * 自動読み込みの番兵はこの一覧の後ろ(履歴ページ側)にあるので、並べ方に関わらず効く。
 */
import { useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { RunSummary } from '../../api/client'
import type { HistoryCardEntry } from './historyCardStyle'
import { assignMasonryColumns, galleryTileAspect, masonryColumnCount } from './galleryLayout'
import { HistoryGalleryTile } from './HistoryGalleryTile'
import styles from './HistoryGallery.module.css'

const GAP_PX = 8
const MIN_COLUMN_WIDTH_PX = 220
const NARROW_COLUMNS = 2
const NARROW_QUERY = '(max-width: 767px)'

function isNarrow(): boolean {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    ? window.matchMedia(NARROW_QUERY).matches
    : false
}

/** コンテナの幅(ResizeObserver)と、狭い画面かどうか。 */
function useContainerWidth(ref: React.RefObject<HTMLElement | null>): { width: number; narrow: boolean } {
  const [state, setState] = useState({ width: 0, narrow: isNarrow() })
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const update = () => {
      const width = el.getBoundingClientRect().width
      const narrow = isNarrow()
      setState((prev) => (prev.width === width && prev.narrow === narrow ? prev : { width, narrow }))
    }
    update()
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', update)
      return () => window.removeEventListener('resize', update)
    }
    const observer = new ResizeObserver(update)
    observer.observe(el)
    return () => observer.disconnect()
  }, [ref])
  return state
}

interface HistoryGalleryProps {
  cards: HistoryCardEntry<RunSummary>[]
}

export function HistoryGallery({ cards }: HistoryGalleryProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const { width, narrow } = useContainerWidth(containerRef)
  const columnCount = masonryColumnCount(width, {
    minColumnWidth: MIN_COLUMN_WIDTH_PX,
    gap: GAP_PX,
    narrow,
    narrowColumns: NARROW_COLUMNS,
  })
  const columns = useMemo(() => {
    const columnWidth = width > 0 ? (width - GAP_PX * (columnCount - 1)) / columnCount : 0
    const gapRatio = columnWidth > 0 ? GAP_PX / columnWidth : 0
    const aspects = cards.map((card) => galleryTileAspect(card.run, card.run.outputs?.[card.outputIndex]))
    return assignMasonryColumns(aspects, columnCount, gapRatio)
  }, [cards, columnCount, width])

  return (
    <div ref={containerRef} className={styles.gallery} style={{ gap: GAP_PX }}>
      {columns.map((indexes, columnIndex) => (
        <div key={columnIndex} className={styles.column} style={{ gap: GAP_PX }}>
          {indexes.map((index) => {
            const card = cards[index]
            return <HistoryGalleryTile key={card.key} run={card.run} outputIndex={card.outputIndex} />
          })}
        </div>
      ))}
    </div>
  )
}
