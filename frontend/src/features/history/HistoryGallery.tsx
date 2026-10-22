/**
 * 履歴のギャラリー。並べ方は2つで、ツールバーで切り替える。
 * - 石積み(`masonry`): 列の数をコンテナの幅から決め(768px 未満は 2 列)、短い列から順に積む
 *   (`assignMasonryColumns`)。タイルは列の幅いっぱいに、元の縦横比で出す。
 * - 行揃え(`rows`): 行の高さを揃えて左から詰める(`layoutJustifiedRows`)。目標の行の高さは
 *   768px 未満で低くする。各タイルの幅と高さは px で指定する。
 * コンテナの幅は ResizeObserver で追う。タイルの中身(ホバーの帯、マスクなど)は共通。
 * 区切りのバナー(日、グループ。ADR-0054)は1マス(縦横比 1:1)を占める。
 * 自動読み込みの番兵はこの一覧の後ろ(履歴ページ側)にあるので、並べ方に関わらず効く。
 */
import { useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { RunSummary } from '../../api/client'
import type { HistoryListItem } from './historySections'
import { HistoryBannerCard } from './HistoryBannerCard'
import { assignMasonryColumns, galleryTileAspect, layoutJustifiedRows, masonryColumnCount } from './galleryLayout'
import { HistoryGalleryTile } from './HistoryGalleryTile'
import styles from './HistoryGallery.module.css'

const GAP_PX = 8
const MIN_COLUMN_WIDTH_PX = 220
const NARROW_COLUMNS = 2
const TARGET_ROW_HEIGHT_PX = 260
const NARROW_TARGET_ROW_HEIGHT_PX = 150
const NARROW_QUERY = '(max-width: 767px)'

export type HistoryGalleryLayout = 'masonry' | 'rows'

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
  items: HistoryListItem<RunSummary>[]
  layout: HistoryGalleryLayout
}

export function HistoryGallery({ items: cards, layout }: HistoryGalleryProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const { width, narrow } = useContainerWidth(containerRef)
  const aspects = useMemo(
    () =>
      cards.map((card) =>
        card.kind === 'banner' ? 1 : galleryTileAspect(card.run, card.run.outputs?.[card.outputIndex]),
      ),
    [cards],
  )

  return (
    <div ref={containerRef}>
      {layout === 'rows' ? (
        <JustifiedRows cards={cards} aspects={aspects} width={width} narrow={narrow} />
      ) : (
        <MasonryColumns cards={cards} aspects={aspects} width={width} narrow={narrow} />
      )}
    </div>
  )
}

interface LayoutProps {
  cards: HistoryListItem<RunSummary>[]
  aspects: number[]
  width: number
  narrow: boolean
}

function MasonryColumns({ cards, aspects, width, narrow }: LayoutProps) {
  const columnCount = masonryColumnCount(width, {
    minColumnWidth: MIN_COLUMN_WIDTH_PX,
    gap: GAP_PX,
    narrow,
    narrowColumns: NARROW_COLUMNS,
  })
  const columns = useMemo(() => {
    const columnWidth = width > 0 ? (width - GAP_PX * (columnCount - 1)) / columnCount : 0
    const gapRatio = columnWidth > 0 ? GAP_PX / columnWidth : 0
    return assignMasonryColumns(aspects, columnCount, gapRatio)
  }, [aspects, columnCount, width])

  return (
    <div className={styles.masonry} style={{ gap: GAP_PX }}>
      {columns.map((indexes, columnIndex) => (
        <div key={columnIndex} className={styles.column} style={{ gap: GAP_PX }}>
          {indexes.map((index) => {
            const card = cards[index]
            if (card.kind === 'banner') {
              return <HistoryBannerCard key={card.key} banner={card} layout="tile" />
            }
            return (
              <HistoryGalleryTile
                key={card.key}
                run={card.run}
                outputIndex={card.outputIndex}
                aspect={aspects[index]}
              />
            )
          })}
        </div>
      ))}
    </div>
  )
}

function JustifiedRows({ cards, aspects, width, narrow }: LayoutProps) {
  const rows = useMemo(
    () =>
      layoutJustifiedRows(aspects, width, {
        targetHeight: narrow ? NARROW_TARGET_ROW_HEIGHT_PX : TARGET_ROW_HEIGHT_PX,
        gap: GAP_PX,
      }),
    [aspects, width, narrow],
  )

  return (
    <div className={styles.rows} style={{ gap: GAP_PX }}>
      {rows.map((row) => (
        <div key={cards[row.tiles[0].index].key} className={styles.row} style={{ gap: GAP_PX }}>
          {row.tiles.map((tile) => {
            const card = cards[tile.index]
            const size = { width: tile.width, height: tile.height }
            if (card.kind === 'banner') {
              return (
                <HistoryBannerCard key={card.key} banner={card} layout="tile" size={size} />
              )
            }
            return (
              <HistoryGalleryTile
                key={card.key}
                run={card.run}
                outputIndex={card.outputIndex}
                aspect={aspects[tile.index]}
                size={size}
              />
            )
          })}
        </div>
      ))}
    </div>
  )
}
