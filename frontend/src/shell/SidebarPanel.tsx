/**
 * サイドバー(既定288px)。IconRail で選ばれたパネルを表示する。選択なし(畳んだ)なら何も描画しない。
 * `resizable` が true のインスタンス(デスクトップ用)だけ、右端のハンドルをドラッグして幅を
 * 変えられる。768px 未満のドロワー表示は `resizable` を渡さず、従来どおり固定幅のまま。
 */
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from 'react'
import type { PanelId } from './panelStorage'
import { StockPanel } from '../features/stock/StockPanel'
import { PromptSetsPanel } from '../features/prompt-sets/PromptSetsPanel'
import { ParameterSetsPanel } from '../features/parameter-sets/ParameterSetsPanel'
import { LineageGraphPanel } from '../features/lineage/LineageGraphPanel'
import { HistoryPanel } from '../features/history/HistoryPanel'
import { SearchPanel } from '../features/search/SearchPanel'
import {
  DEFAULT_SIDEBAR_WIDTH,
  MIN_SIDEBAR_WIDTH,
  clampSidebarWidth,
  loadSidebarWidth,
  maxSidebarWidth,
  saveSidebarWidth,
} from './sidebarWidth'
import { useI18n } from '../i18n'
import styles from './SidebarPanel.module.css'

interface SidebarPanelProps {
  selected: PanelId | null
  /** ドラッグでのリサイズを有効にするか(デスクトップ用インスタンスのみ true)。既定 false。 */
  resizable?: boolean
  /**
   * メイン領域のどちら側に置かれているか(既定 left)。right のときはハンドルを左端に出し、
   * ドラッグと ←/→ の向きを反転する(左へ動かすほど広がる)。
   */
  side?: 'left' | 'right'
}

function currentWindowWidth(): number {
  return typeof window !== 'undefined' ? window.innerWidth : 1024
}

export function SidebarPanel({ selected, resizable = false, side = 'left' }: SidebarPanelProps) {
  // 右側配置ではポインタが左へ動くほど幅が広がる。
  const direction = side === 'right' ? -1 : 1
  const { t } = useI18n()
  const [width, setWidth] = useState(() => loadSidebarWidth(currentWindowWidth()))
  const [windowWidth, setWindowWidth] = useState(currentWindowWidth)
  const [dragging, setDragging] = useState(false)
  // ドラッグ開始時点のポインタ位置と幅。pointermove のたびに再計算するため ref に持つ。
  const dragOriginRef = useRef<{ startX: number; startWidth: number } | null>(null)
  // ドラッグ中の最新の幅。終了時の保存に使う。
  const dragWidthRef = useRef(width)

  // リサイズ可能な間だけ、ウィンドウ幅を追って表示上の上限(clamp)を再計算する。
  useEffect(() => {
    if (!resizable) return
    const onResize = () => setWindowWidth(window.innerWidth)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [resizable])

  const displayWidth = clampSidebarWidth(width, windowWidth)
  const max = maxSidebarWidth(windowWidth)

  const commitWidth = useCallback((next: number) => {
    const clamped = clampSidebarWidth(next, currentWindowWidth())
    setWidth(clamped)
    saveSidebarWidth(clamped)
  }, [])

  const handlePointerDown = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      event.currentTarget.setPointerCapture(event.pointerId)
      dragOriginRef.current = { startX: event.clientX, startWidth: displayWidth }
      dragWidthRef.current = displayWidth
      setDragging(true)
      document.body.style.userSelect = 'none'
      document.body.style.cursor = 'col-resize'
    },
    [displayWidth],
  )

  const handlePointerMove = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    const origin = dragOriginRef.current
    if (!origin) return
    const delta = (event.clientX - origin.startX) * direction
    const next = clampSidebarWidth(origin.startWidth + delta, currentWindowWidth())
    dragWidthRef.current = next
    setWidth(next)
  }, [direction])

  const endDrag = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    if (!dragOriginRef.current) return
    dragOriginRef.current = null
    setDragging(false)
    document.body.style.userSelect = ''
    document.body.style.cursor = ''
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
    // 保存はドラッグ終了時に1回だけ。
    saveSidebarWidth(dragWidthRef.current)
  }, [])

  const handleDoubleClick = useCallback(() => {
    commitWidth(DEFAULT_SIDEBAR_WIDTH)
  }, [commitWidth])

  const handleKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLDivElement>) => {
      const step = event.shiftKey ? 64 : 16
      switch (event.key) {
        case 'ArrowLeft':
          commitWidth(displayWidth - step * direction)
          break
        case 'ArrowRight':
          commitWidth(displayWidth + step * direction)
          break
        case 'Home':
          commitWidth(MIN_SIDEBAR_WIDTH)
          break
        case 'End':
          commitWidth(max)
          break
        case 'Enter':
          commitWidth(DEFAULT_SIDEBAR_WIDTH)
          break
        default:
          return
      }
      event.preventDefault()
    },
    [commitWidth, displayWidth, max, direction],
  )

  if (selected === null) return null

  const sidebarStyle = resizable
    ? ({ '--shell-sidebar-w': `${displayWidth}px` } as CSSProperties)
    : undefined

  return (
    <aside
      aria-label={t.shell.sidebar.resourcePanel}
      className={styles.sidebar}
      data-side={side}
      style={sidebarStyle}
    >
      {selected === 'stock' && <StockPanel />}
      {selected === 'prompts' && <PromptSetsPanel />}
      {selected === 'parameterSets' && <ParameterSetsPanel />}
      {selected === 'graph' && <LineageGraphPanel />}
      {selected === 'history' && <HistoryPanel />}
      {selected === 'search' && <SearchPanel />}
      {resizable && (
        <div
          className={styles.handle}
          role="separator"
          aria-orientation="vertical"
          aria-label={t.shell.sidebar.widthHandle}
          aria-valuenow={Math.round(displayWidth)}
          aria-valuemin={MIN_SIDEBAR_WIDTH}
          aria-valuemax={Math.round(max)}
          data-dragging={dragging || undefined}
          tabIndex={0}
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={endDrag}
          onPointerCancel={endDrag}
          onDoubleClick={handleDoubleClick}
          onKeyDown={handleKeyDown}
        />
      )}
    </aside>
  )
}
