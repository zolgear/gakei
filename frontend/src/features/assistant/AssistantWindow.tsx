/**
 * アシスタントの浮いた窓(ADR-0048 2章、2026-10-24 に改めた)。どの画面にも重ねて出し、背後の画面は
 * そのまま操作できる(暗くしない、フォーカスを閉じ込めない。ADR-0009 9章のダイアログの規則の対象外)。
 *
 * - 広い幅: 見出しをドラッグして動かし、左・右・下の辺と下の角で大きさを変える。
 * - 768px 未満: 画面の下から出るシート(全幅)。見出しを上下にドラッグして高さを変える。
 * - たたむ(見出しだけ)と閉じる。どちらでも中身はアンマウントせずに隠す(送信欄の書きかけと添付を
 *   残す)。会話と処理中の応答は `AssistantSessionProvider` が持つので、閉じても続く。
 * - Esc は窓の中にフォーカスがあるときだけ閉じる(背後の画面の Esc とぶつけない)。
 *
 * 重なり順は画面の中身より上、エディター(マスク・配置・領域など)・ポップオーバー・モーダル・
 * トースト・狭い幅のドロワーより下(AssistantWindow.module.css)。
 */
import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from 'react'
import { useI18n } from '../../i18n'
import { useIsMobileViewport } from '../../lib/viewport'
import { AssistantPanel } from './AssistantPanel'
import {
  clampSheetHeight,
  clampWindowRect,
  defaultSheetHeight,
  defaultWindowRect,
  loadAssistantWindow,
  moveWindowRect,
  resizeWindowRect,
  saveAssistantWindow,
  type ResizeEdge,
  type Viewport,
  type WindowRect,
} from './assistantWindowGeometry'
import { useAssistantSession } from './useAssistantSession'
import { ASSISTANT_COMPOSER_ATTRIBUTE, ASSISTANT_TOGGLE_ATTRIBUTE } from './assistantWindowAttributes'
import styles from './AssistantWindow.module.css'

interface AssistantWindowProps {
  open: boolean
  collapsed: boolean
  onCollapsedChange: (collapsed: boolean) => void
  onClose: () => void
}

function currentViewport(): Viewport {
  return { width: window.innerWidth, height: window.innerHeight }
}

const RESIZE_EDGES: ResizeEdge[] = ['left', 'right', 'bottom', 'bottomLeft', 'bottomRight']

type Drag =
  | { kind: 'move'; startX: number; startY: number; start: WindowRect }
  | { kind: 'resize'; edge: ResizeEdge; startX: number; startY: number; start: WindowRect }
  | { kind: 'sheet'; startY: number; startHeight: number }

export function AssistantWindow({ open, collapsed, onCollapsedChange, onClose }: AssistantWindowProps) {
  const { t } = useI18n()
  const w = t.assistant.window
  const titleId = useId()
  const mobile = useIsMobileViewport()
  const { state } = useAssistantSession()
  const rootRef = useRef<HTMLElement | null>(null)
  const [stored] = useState(loadAssistantWindow)
  const [rect, setRect] = useState<WindowRect | null>(stored.rect)
  const [sheetHeight, setSheetHeight] = useState<number | null>(stored.sheetHeight)
  const [viewport, setViewport] = useState<Viewport>(currentViewport)
  const dragRef = useRef<Drag | null>(null)
  const [dragging, setDragging] = useState(false)
  // 保存はドラッグの終わりに1回だけ。最新の値を ref でも持つ。
  const latestRef = useRef({ rect, sheetHeight })
  useEffect(() => {
    latestRef.current = { rect, sheetHeight }
  }, [rect, sheetHeight])

  useEffect(() => {
    const onResize = () => setViewport(currentViewport())
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  const shownRect = clampWindowRect(rect ?? defaultWindowRect(viewport), viewport)
  const shownSheetHeight = clampSheetHeight(sheetHeight ?? defaultSheetHeight(viewport.height), viewport.height)

  // 開いた・広げたら送信欄へフォーカスを移す。
  const expanded = open && !collapsed
  useEffect(() => {
    if (!expanded) return
    rootRef.current?.querySelector<HTMLElement>(`[${ASSISTANT_COMPOSER_ATTRIBUTE}]`)?.focus()
  }, [expanded])

  const close = useCallback(() => {
    const root = rootRef.current
    // 窓の中にフォーカスがあったら、見えている開閉ボタンへ戻す(隠れた要素にフォーカスを残さない)。
    if (root && root.contains(document.activeElement)) {
      const toggles = Array.from(document.querySelectorAll<HTMLElement>(`[${ASSISTANT_TOGGLE_ATTRIBUTE}]`))
      const visible = toggles.find((el) => el.getClientRects().length > 0)
      if (visible) visible.focus()
      else (document.activeElement as HTMLElement | null)?.blur()
    }
    onClose()
  }, [onClose])

  const handleKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLElement>) => {
      if (event.key !== 'Escape' || event.defaultPrevented || event.nativeEvent.isComposing) return
      // 窓の中のモーダル(画像の添付など)の Esc はそちらに任せる。
      if (event.target instanceof Element && event.target.closest('[aria-modal="true"]')) return
      event.preventDefault()
      close()
    },
    [close],
  )

  const beginDrag = useCallback((event: ReactPointerEvent<HTMLElement>, drag: Drag) => {
    if (event.button !== 0) return
    event.currentTarget.setPointerCapture(event.pointerId)
    dragRef.current = drag
    setDragging(true)
    document.body.style.userSelect = 'none'
  }, [])

  const handleTitlePointerDown = useCallback(
    (event: ReactPointerEvent<HTMLElement>) => {
      // 見出しのボタンは押せるままにする。
      if (event.target instanceof Element && event.target.closest('button')) return
      if (mobile) {
        if (collapsed) return
        beginDrag(event, { kind: 'sheet', startY: event.clientY, startHeight: shownSheetHeight })
      } else {
        beginDrag(event, { kind: 'move', startX: event.clientX, startY: event.clientY, start: shownRect })
      }
    },
    [beginDrag, collapsed, mobile, shownRect, shownSheetHeight],
  )

  const handlePointerMove = useCallback((event: ReactPointerEvent<HTMLElement>) => {
    const drag = dragRef.current
    if (!drag) return
    const vp = currentViewport()
    if (drag.kind === 'sheet') {
      setSheetHeight(clampSheetHeight(drag.startHeight - (event.clientY - drag.startY), vp.height))
      return
    }
    const dx = event.clientX - drag.startX
    const dy = event.clientY - drag.startY
    setRect(
      drag.kind === 'move'
        ? moveWindowRect(drag.start, dx, dy, vp)
        : resizeWindowRect(drag.start, drag.edge, dx, dy, vp),
    )
  }, [])

  const endDrag = useCallback((event: ReactPointerEvent<HTMLElement>) => {
    if (!dragRef.current) return
    dragRef.current = null
    setDragging(false)
    document.body.style.userSelect = ''
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
    // pointermove の setState がまだ effect に届いていないことがあるので、次の描画の後に保存する。
    requestAnimationFrame(() => saveAssistantWindow(latestRef.current))
  }, [])

  const style: CSSProperties = mobile
    ? { height: collapsed ? undefined : shownSheetHeight }
    : {
        left: shownRect.x,
        top: shownRect.y,
        width: shownRect.width,
        height: collapsed ? undefined : shownRect.height,
      }

  return (
    <section
      ref={rootRef}
      className={styles.window}
      data-mode={mobile ? 'sheet' : 'window'}
      data-collapsed={collapsed || undefined}
      data-dragging={dragging || undefined}
      hidden={!open}
      role="dialog"
      aria-labelledby={titleId}
      style={style}
      onKeyDown={handleKeyDown}
    >
      <header
        className={styles.titleBar}
        onPointerDown={handleTitlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
      >
        {mobile && !collapsed && <span className={styles.grip} aria-hidden="true" />}
        <h2 id={titleId} className={styles.title}>
          {t.assistant.heading}
        </h2>
        {collapsed && state.sending && (
          <span className={styles.busyDot} role="status" aria-label={w.responding} title={w.responding} />
        )}
        <div className={styles.titleActions}>
          <button
            type="button"
            className={styles.titleButton}
            aria-label={collapsed ? w.expand : w.collapse}
            title={collapsed ? w.expand : w.collapse}
            aria-expanded={!collapsed}
            onClick={() => onCollapsedChange(!collapsed)}
          >
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
              {collapsed ? (
                <rect x="2.5" y="2.5" width="9" height="9" rx="1" stroke="currentColor" strokeWidth="1.5" />
              ) : (
                <path d="M3 10.5h8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
              )}
            </svg>
          </button>
          <button type="button" className={styles.titleButton} aria-label={w.close} title={w.close} onClick={close}>
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
              <path d="M3.5 3.5l7 7M10.5 3.5l-7 7" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
            </svg>
          </button>
        </div>
      </header>
      <div className={styles.body} hidden={collapsed}>
        <AssistantPanel />
      </div>
      {!mobile &&
        !collapsed &&
        RESIZE_EDGES.map((edge) => (
          <div
            key={edge}
            className={styles.resizeHandle}
            data-edge={edge}
            aria-hidden="true"
            onPointerDown={(event) =>
              beginDrag(event, { kind: 'resize', edge, startX: event.clientX, startY: event.clientY, start: shownRect })
            }
            onPointerMove={handlePointerMove}
            onPointerUp={endDrag}
            onPointerCancel={endDrag}
          />
        ))}
    </section>
  )
}
