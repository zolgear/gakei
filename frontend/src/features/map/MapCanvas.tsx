/**
 * マップ(ADR-0033 8章)の canvas。地図とネットワークの両方で使う。
 *
 * - 点の座標(`positions`、x0, y0, x1, y1, ...)を、パンとズームを付けて描く。拡大してサムネイルが
 *   ある程度の大きさになったら、見えている分だけサムネイルを読んで描く(それまでは点)。
 * - 操作: ドラッグで移動、ホイールとピンチで拡大縮小、クリック(タップ)で選ぶ。何も無い所を
 *   押すと選択を外す。Esc でも外す。
 * - 選んだ画像は、その近傍(`neighborIndices` の行)を強調し、ほかを薄くする(「似た画像」)。
 * - 選んだ画像は左下のカードに出し、「↗」で大きく見るパネル(`MapPreviewPanel`)に広げられる。
 *   Esc は、パネルを開いていればまずパネルを畳み、次に選択を外す。
 * - 利用者が動かすまでは、座標が変わるたびに全体が収まるように合わせる(地図の計算の途中経過や、
 *   ネットワークの力学モデルの動きを追う)。
 * - 色は CSS 変数(配色)から読む。
 */
import { useEffect, useRef, useState, type ReactNode, type RefObject } from 'react'
import { Link } from 'react-router'
import { assetUrl } from '../../api/assetUrl'
import type { EmbeddingGraphNode } from '../../api/client'
import { fmt, useI18n } from '../../i18n'
import { buildSimilarSearchPath } from '../search/searchQuerySync'
import { neighborsOf, type SimilarityEdge } from './edges'
import { hitTest } from './hitTest'
import { MapPreviewPanel } from './MapPreviewPanel'
import { thumbCache } from './thumbCache'
import {
  DOT_RADIUS,
  THUMB_MIN_PX,
  arrowSizePx,
  drawTilePx,
  fitNodes,
  lineageSegment,
  type LineageSegment,
} from './nodeSize'
import {
  computeBounds,
  panBy,
  tileWorldSize,
  worldToScreen,
  zoomAt,
  type Viewport,
} from './viewport'
import styles from './MapCanvas.module.css'

export interface MapCanvasHandle {
  /** 座標を書き換えたあとに呼ぶ(次のフレームで描き直す)。 */
  redraw: () => void
}

interface MapCanvasProps {
  nodes: EmbeddingGraphNode[]
  /** x0, y0, x1, y1, ...。呼び出し側が書き換えてよい(そのあと `redraw`)。 */
  positions: Float32Array | null
  /** 別のデータに替わったら変える。表示を全体に合わせ直す。 */
  dataKey: string
  neighborIndices: number[][]
  similarityEdges?: SimilarityEdge[] | null
  lineageEdges?: [number, number][] | null
  selected: number | null
  onSelect: (index: number | null) => void
  controllerRef?: RefObject<MapCanvasHandle | null>
  ariaLabel: string
  /** canvas の上に重ねる(進み具合など)。 */
  overlay?: ReactNode
  /**
   * サムネイルの大きさの倍率。ネットワークでは辺が見えるよう小さめにする。
   */
  tileScale?: number
}

/** クリックとみなす移動量の上限。 */
const CLICK_SLOP = 6
/** 当たり判定の最小の半径(指でも押せるように)。 */
const MIN_HIT_RADIUS = 10
/** 1フレームで読みに行くサムネイルの数の上限。 */
const MAX_WANTED = 240

interface Colors {
  dot: string
  dotDim: string
  tileBg: string
  accent: string
  text: string
  lineage: string
  bg: string
}

function readColors(el: Element): Colors {
  const css = getComputedStyle(el)
  const v = (name: string, fallback: string) => css.getPropertyValue(name).trim() || fallback
  return {
    dot: v('--color-text-muted', '#9aa0aa'),
    dotDim: v('--color-text-faint', '#6b7178'),
    tileBg: v('--color-header-band', '#2b2f36'),
    accent: v('--color-accent', '#e3b341'),
    text: v('--color-text', '#e6e7ea'),
    lineage: v('--color-edit', '#56c2d6'),
    bg: v('--color-bg', '#17181b'),
  }
}

interface PointerInfo {
  x: number
  y: number
}

export function MapCanvas({
  nodes,
  positions,
  dataKey,
  neighborIndices,
  similarityEdges,
  lineageEdges,
  selected,
  onSelect,
  controllerRef,
  ariaLabel,
  overlay,
  tileScale = 1,
}: MapCanvasProps) {
  const { t } = useI18n()
  const m = t.map
  const containerRef = useRef<HTMLDivElement | null>(null)
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const viewportRef = useRef<Viewport>({ x: 0, y: 0, scale: 1 })
  const sizeRef = useRef({ width: 0, height: 0 })
  const userMovedRef = useRef(false)
  const frameRef = useRef<number | null>(null)
  const [hover, setHover] = useState<{ index: number; x: number; y: number } | null>(null)
  // 選んだ画像を大きく見るパネルを開いているか。覚えない(選択を外したら閉じる)。
  const [previewOpen, setPreviewOpen] = useState(false)
  const expandButtonRef = useRef<HTMLButtonElement | null>(null)
  if (previewOpen && selected === null) setPreviewOpen(false)

  // 描画は requestAnimationFrame から呼ぶので、最新の props を ref で読む。
  const propsRef = useRef({
    nodes,
    positions,
    neighborIndices,
    similarityEdges,
    lineageEdges,
    selected,
    tileScale,
    hover: -1,
  })
  propsRef.current = {
    nodes,
    positions,
    neighborIndices,
    similarityEdges,
    lineageEdges,
    selected,
    tileScale,
    hover: hover?.index ?? -1,
  }

  const drawRef = useRef<() => void>(() => {})
  drawRef.current = () => {
    frameRef.current = null
    const canvas = canvasRef.current
    const container = containerRef.current
    if (!canvas || !container) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const { width, height } = sizeRef.current
    const dpr = window.devicePixelRatio || 1
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, width, height)
    const p = propsRef.current
    const pos = p.positions
    const n = Math.min(p.nodes.length, pos ? pos.length / 2 : 0)
    if (!pos || n === 0 || width === 0 || height === 0) return

    const bounds = computeBounds(pos, n)
    const tileWorld = tileWorldSize(bounds, n, pos, p.neighborIndices) * p.tileScale
    if (!userMovedRef.current && bounds) {
      // 端の画像(半分の大きさと選択の枠)も画面に収める。
      viewportRef.current = fitNodes(bounds, width, height, tileWorld, n)
    }
    const v = viewportRef.current
    const colors = readColors(container)
    const tilePx = drawTilePx(tileWorld * v.scale, n)
    const thumbMode = tilePx >= THUMB_MIN_PX
    const half = thumbMode ? tilePx / 2 : DOT_RADIUS
    const margin = half + 2

    const sel = p.selected !== null && p.selected < n ? p.selected : null
    const emphasized = sel !== null ? new Set(neighborsOf(p.neighborIndices, sel)) : null

    const sx = new Float32Array(n)
    const sy = new Float32Array(n)
    for (let i = 0; i < n; i++) {
      const [x, y] = worldToScreen(v, pos[i * 2], pos[i * 2 + 1])
      sx[i] = x
      sy[i] = y
    }
    const visible = (i: number) =>
      sx[i] >= -margin && sx[i] <= width + margin && sy[i] >= -margin && sy[i] <= height + margin

    // 類似度の辺(ネットワーク)。
    if (p.similarityEdges && p.similarityEdges.length > 0) {
      ctx.save()
      ctx.strokeStyle = colors.dot
      ctx.globalAlpha = sel !== null ? 0.12 : 0.35
      ctx.lineWidth = 1
      ctx.beginPath()
      for (const e of p.similarityEdges) {
        if (e.source >= n || e.target >= n) continue
        ctx.moveTo(sx[e.source], sy[e.source])
        ctx.lineTo(sx[e.target], sy[e.target])
      }
      ctx.stroke()
      ctx.restore()
    }

    // 選んだ画像から近傍への線。
    if (sel !== null && emphasized) {
      ctx.save()
      ctx.strokeStyle = colors.accent
      ctx.globalAlpha = 0.7
      ctx.lineWidth = 1.25
      ctx.beginPath()
      for (const j of emphasized) {
        if (j >= n) continue
        ctx.moveTo(sx[sel], sy[sel])
        ctx.lineTo(sx[j], sy[j])
      }
      ctx.stroke()
      ctx.restore()
    }

    const wanted: string[] = []
    const drawNode = (i: number) => {
      if (thumbMode) {
        const id = p.nodes[i].id
        const tile = thumbCache.get(id)
        const x = sx[i] - half
        const y = sy[i] - half
        if (tile) {
          ctx.drawImage(tile, x, y, tilePx, tilePx)
        } else {
          ctx.fillStyle = colors.tileBg
          ctx.fillRect(x, y, tilePx, tilePx)
          if (wanted.length < MAX_WANTED) wanted.push(id)
        }
      } else {
        ctx.moveTo(sx[i] + DOT_RADIUS, sy[i])
        ctx.arc(sx[i], sy[i], DOT_RADIUS, 0, Math.PI * 2)
      }
    }

    // ノード。選んでいるときは、近傍以外を薄くする。
    if (!thumbMode) ctx.beginPath()
    ctx.save()
    ctx.globalAlpha = sel !== null ? 0.3 : 1
    ctx.fillStyle = colors.dot
    for (let i = 0; i < n; i++) {
      if (i === sel || emphasized?.has(i) || !visible(i)) continue
      drawNode(i)
    }
    if (!thumbMode) ctx.fill()
    ctx.restore()

    if (sel !== null && emphasized) {
      ctx.save()
      if (!thumbMode) ctx.beginPath()
      ctx.fillStyle = colors.accent
      for (const j of emphasized) if (j < n && visible(j)) drawNode(j)
      if (!thumbMode) ctx.fill()
      if (thumbMode) {
        ctx.strokeStyle = colors.accent
        ctx.lineWidth = 1.5
        for (const j of emphasized) if (j < n && visible(j)) ctx.strokeRect(sx[j] - half, sy[j] - half, tilePx, tilePx)
      }
      ctx.restore()
    }

    // 系列の辺(破線)。サムネイルに隠れないよう、ノードの上に描く。
    if (p.lineageEdges && p.lineageEdges.length > 0) {
      ctx.save()
      ctx.strokeStyle = colors.lineage
      ctx.fillStyle = colors.lineage
      ctx.globalAlpha = 0.95
      ctx.lineWidth = 2
      if (thumbMode) {
        // 親の縁から子の縁まで引き、子の側に矢じりを付ける(サムネイルに隠れないように縁で止める)。
        const arrow = arrowSizePx(tilePx)
        const wing = arrow * 0.5
        const segments: LineageSegment[] = []
        const overlapped: [number, number][] = []
        for (const [a, b] of p.lineageEdges) {
          if (a >= n || b >= n) continue
          const seg = lineageSegment(sx[a], sy[a], sx[b], sy[b], half, arrow)
          if (seg) segments.push(seg)
          else overlapped.push([a, b])
        }
        ctx.setLineDash([5, 4])
        ctx.beginPath()
        for (const s of segments) {
          ctx.moveTo(s.x0, s.y0)
          ctx.lineTo(s.x1, s.y1)
        }
        ctx.stroke()
        ctx.setLineDash([])
        ctx.beginPath()
        for (const s of segments) {
          ctx.moveTo(s.tipX, s.tipY)
          ctx.lineTo(s.x1 - s.uy * wing, s.y1 + s.ux * wing)
          ctx.lineTo(s.x1 + s.uy * wing, s.y1 - s.ux * wing)
          ctx.closePath()
        }
        ctx.fill()
        // サムネイルが重なって線を引けない組は、中心どうしを実線で結ぶ(向きは示せない)。
        if (overlapped.length > 0) {
          ctx.beginPath()
          for (const [a, b] of overlapped) {
            ctx.moveTo(sx[a], sy[a])
            ctx.lineTo(sx[b], sy[b])
          }
          ctx.stroke()
        }
      } else {
        ctx.setLineDash([5, 4])
        ctx.beginPath()
        for (const [a, b] of p.lineageEdges) {
          if (a >= n || b >= n) continue
          ctx.moveTo(sx[a], sy[a])
          ctx.lineTo(sx[b], sy[b])
        }
        ctx.stroke()
        ctx.setLineDash([])
        // 点のときは、子の側に小さな丸を付けて向きを示す。
        ctx.beginPath()
        for (const [a, b] of p.lineageEdges) {
          if (a >= n || b >= n) continue
          const dx = sx[a] - sx[b]
          const dy = sy[a] - sy[b]
          const len = Math.hypot(dx, dy)
          if (len < 1) continue
          const off = Math.min(len / 2, DOT_RADIUS + 4)
          const cx = sx[b] + (dx / len) * off
          const cy = sy[b] + (dy / len) * off
          ctx.moveTo(cx + 2.5, cy)
          ctx.arc(cx, cy, 2.5, 0, Math.PI * 2)
        }
        ctx.fill()
      }
      ctx.restore()
    }

    // なぞっている画像と選んだ画像を最後に、枠付きで描く。
    const ring = (i: number, color: string, width: number) => {
      const r = thumbMode ? half + 2 : DOT_RADIUS + 4
      ctx.save()
      if (thumbMode) {
        drawNode(i)
        ctx.strokeStyle = colors.bg
        ctx.lineWidth = width + 2
        ctx.strokeRect(sx[i] - r, sy[i] - r, r * 2, r * 2)
        ctx.strokeStyle = color
        ctx.lineWidth = width
        ctx.strokeRect(sx[i] - r, sy[i] - r, r * 2, r * 2)
      } else {
        ctx.fillStyle = color
        ctx.beginPath()
        ctx.arc(sx[i], sy[i], DOT_RADIUS + 1.5, 0, Math.PI * 2)
        ctx.fill()
        ctx.strokeStyle = color
        ctx.lineWidth = width
        ctx.beginPath()
        ctx.arc(sx[i], sy[i], r, 0, Math.PI * 2)
        ctx.stroke()
      }
      ctx.restore()
    }
    if (p.hover >= 0 && p.hover < n && p.hover !== sel && visible(p.hover)) ring(p.hover, colors.text, 1.5)
    if (sel !== null && visible(sel)) ring(sel, colors.accent, 2)

    if (thumbMode) thumbCache.want(wanted)
  }

  const requestDraw = () => {
    if (frameRef.current !== null) return
    frameRef.current = requestAnimationFrame(() => drawRef.current())
  }

  // 呼び出し側へ描き直しの口を渡す。
  useEffect(() => {
    if (!controllerRef) return
    controllerRef.current = { redraw: requestDraw }
    return () => {
      controllerRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [controllerRef])

  // データが替わったら全体に合わせ直す。
  useEffect(() => {
    userMovedRef.current = false
    setHover(null)
    requestDraw()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dataKey])

  // props が変わったら描き直す。
  useEffect(() => {
    requestDraw()
  })

  // サムネイルが届いたら描き直す。
  useEffect(() => thumbCache.subscribe(requestDraw), [])

  // 大きさの変化(と、デバイスピクセル比)。
  useEffect(() => {
    const container = containerRef.current
    const canvas = canvasRef.current
    if (!container || !canvas) return
    const resize = () => {
      const rect = container.getBoundingClientRect()
      const dpr = window.devicePixelRatio || 1
      sizeRef.current = { width: rect.width, height: rect.height }
      canvas.width = Math.max(1, Math.round(rect.width * dpr))
      canvas.height = Math.max(1, Math.round(rect.height * dpr))
      canvas.style.width = `${rect.width}px`
      canvas.style.height = `${rect.height}px`
      requestDraw()
    }
    resize()
    const observer = new ResizeObserver(resize)
    observer.observe(container)
    return () => {
      observer.disconnect()
      if (frameRef.current !== null) cancelAnimationFrame(frameRef.current)
      frameRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ---- 操作 ----
  const pointersRef = useRef(new Map<number, PointerInfo>())
  const gestureRef = useRef({ startX: 0, startY: 0, moved: 0, multi: false })

  function localPoint(e: { clientX: number; clientY: number }): [number, number] {
    const rect = canvasRef.current!.getBoundingClientRect()
    return [e.clientX - rect.left, e.clientY - rect.top]
  }

  function currentHitRadius(): number {
    const p = propsRef.current
    const pos = p.positions
    if (!pos) return MIN_HIT_RADIUS
    const n = Math.min(p.nodes.length, pos.length / 2)
    const tilePx = drawTilePx(
      tileWorldSize(computeBounds(pos, n), n, pos, p.neighborIndices) * p.tileScale * viewportRef.current.scale,
      n,
    )
    return Math.max(MIN_HIT_RADIUS, tilePx >= THUMB_MIN_PX ? tilePx / 2 : 0)
  }

  function pick(x: number, y: number): number {
    const p = propsRef.current
    const pos = p.positions
    if (!pos) return -1
    return hitTest(pos, Math.min(p.nodes.length, pos.length / 2), viewportRef.current, x, y, currentHitRadius())
  }

  function onPointerDown(e: React.PointerEvent<HTMLCanvasElement>) {
    const [x, y] = localPoint(e)
    e.currentTarget.setPointerCapture(e.pointerId)
    pointersRef.current.set(e.pointerId, { x, y })
    if (pointersRef.current.size === 1) {
      gestureRef.current = { startX: x, startY: y, moved: 0, multi: false }
    } else {
      gestureRef.current.multi = true
    }
  }

  function onPointerMove(e: React.PointerEvent<HTMLCanvasElement>) {
    const [x, y] = localPoint(e)
    const pointers = pointersRef.current
    const prev = pointers.get(e.pointerId)
    if (!prev) {
      // ボタンを押していないマウスの移動: なぞった画像を示す。
      if (e.pointerType === 'mouse') {
        const hit = pick(x, y)
        if (hit < 0) {
          if (hover !== null) setHover(null)
        } else if (hover?.index !== hit) {
          setHover({ index: hit, x, y })
        }
      }
      return
    }
    if (pointers.size === 1) {
      const dx = x - prev.x
      const dy = y - prev.y
      gestureRef.current.moved += Math.abs(dx) + Math.abs(dy)
      if (gestureRef.current.moved > CLICK_SLOP) {
        userMovedRef.current = true
        viewportRef.current = panBy(viewportRef.current, dx, dy)
        if (hover !== null) setHover(null)
        requestDraw()
      }
      pointers.set(e.pointerId, { x, y })
      return
    }
    if (pointers.size >= 2) {
      const [a, b] = [...pointers.entries()].slice(0, 2)
      const before = { a: a[1], b: b[1] }
      pointers.set(e.pointerId, { x, y })
      const after = { a: pointers.get(a[0])!, b: pointers.get(b[0])! }
      const d0 = Math.hypot(before.a.x - before.b.x, before.a.y - before.b.y)
      const d1 = Math.hypot(after.a.x - after.b.x, after.a.y - after.b.y)
      const mx0 = (before.a.x + before.b.x) / 2
      const my0 = (before.a.y + before.b.y) / 2
      const mx1 = (after.a.x + after.b.x) / 2
      const my1 = (after.a.y + after.b.y) / 2
      let v = panBy(viewportRef.current, mx1 - mx0, my1 - my0)
      if (d0 > 0 && d1 > 0) v = zoomAt(v, mx1, my1, d1 / d0)
      viewportRef.current = v
      userMovedRef.current = true
      gestureRef.current.moved += CLICK_SLOP + 1
      requestDraw()
    }
  }

  function onPointerUp(e: React.PointerEvent<HTMLCanvasElement>) {
    const pointers = pointersRef.current
    if (!pointers.has(e.pointerId)) return
    pointers.delete(e.pointerId)
    const g = gestureRef.current
    if (pointers.size === 0 && !g.multi && g.moved <= CLICK_SLOP && e.type === 'pointerup') {
      const [x, y] = localPoint(e)
      const hit = pick(x, y)
      onSelect(hit >= 0 ? hit : null)
      setHover(null)
    }
  }

  // ホイールは passive: false で受けないとページがスクロールしてしまう。
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const rect = canvas.getBoundingClientRect()
      const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? rect.height : 1
      const factor = Math.exp(-e.deltaY * unit * 0.0015)
      viewportRef.current = zoomAt(viewportRef.current, e.clientX - rect.left, e.clientY - rect.top, factor)
      userMovedRef.current = true
      setHover(null)
      requestDraw()
    }
    canvas.addEventListener('wheel', onWheel, { passive: false })
    return () => canvas.removeEventListener('wheel', onWheel)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function zoomButton(factor: number) {
    const { width, height } = sizeRef.current
    viewportRef.current = zoomAt(viewportRef.current, width / 2, height / 2, factor)
    userMovedRef.current = true
    requestDraw()
  }

  function fitAll() {
    userMovedRef.current = false
    requestDraw()
  }

  function collapsePreview() {
    setPreviewOpen(false)
    // 畳んだら、広げたボタンに焦点を戻す(カードが描かれてから)。
    requestAnimationFrame(() => expandButtonRef.current?.focus({ preventScroll: true }))
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Escape' && previewOpen) {
      e.preventDefault()
      setPreviewOpen(false)
    } else if (e.key === 'Escape' && selected !== null) {
      e.preventDefault()
      onSelect(null)
    } else if (e.key === '+' || e.key === '=') {
      e.preventDefault()
      zoomButton(1.4)
    } else if (e.key === '-') {
      e.preventDefault()
      zoomButton(1 / 1.4)
    } else if (e.key === '0') {
      e.preventDefault()
      fitAll()
    }
  }

  const hoverNode = hover && hover.index < nodes.length && hover.index !== selected ? nodes[hover.index] : null
  const selectedNode = selected !== null && selected < nodes.length ? nodes[selected] : null
  const { width: cw } = sizeRef.current

  return (
    <div ref={containerRef} className={styles.root} data-preview-open={(previewOpen && selectedNode !== null) || undefined}>
      <canvas
        ref={canvasRef}
        className={styles.canvas}
        role="img"
        aria-label={ariaLabel}
        tabIndex={0}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onPointerLeave={(e) => {
          if (e.pointerType === 'mouse' && !pointersRef.current.has(e.pointerId)) setHover(null)
        }}
        onKeyDown={onKeyDown}
      />

      {overlay && <div className={styles.overlay}>{overlay}</div>}

      <div className={styles.zoomControls} role="group" aria-label={m.zoomGroup}>
        <button type="button" className={styles.zoomButton} aria-label={m.zoomIn} title={m.zoomIn} onClick={() => zoomButton(1.4)}>
          +
        </button>
        <button type="button" className={styles.zoomButton} aria-label={m.zoomOut} title={m.zoomOut} onClick={() => zoomButton(1 / 1.4)}>
          −
        </button>
        <button type="button" className={styles.zoomButton} aria-label={m.fit} title={m.fit} onClick={fitAll}>
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <path d="M2 6V2h4M10 2h4v4M14 10v4h-4M6 14H2v-4" stroke="currentColor" strokeWidth="1.5" />
          </svg>
        </button>
      </div>

      {hoverNode && hover && (
        <div
          className={styles.hoverCard}
          style={{
            left: Math.min(hover.x + 14, Math.max(0, cw - 200)),
            top: Math.max(8, hover.y - 70),
          }}
          aria-hidden="true"
        >
          <img src={assetUrl(hoverNode.id, 'thumb')} alt="" className={styles.hoverThumb} />
          <span className={styles.hoverTitle}>{hoverNode.title || m.untitled}</span>
        </div>
      )}

      {selectedNode && previewOpen && (
        <MapPreviewPanel assetId={selectedNode.id} fallbackTitle={selectedNode.title} onCollapse={collapsePreview} />
      )}

      {selectedNode && !previewOpen && (
        <div
          className={styles.selectionCard}
          role="region"
          aria-label={m.selectionLabel}
          onKeyDown={(e) => {
            if (e.key === 'Escape') {
              e.preventDefault()
              onSelect(null)
              canvasRef.current?.focus({ preventScroll: true })
            }
          }}
        >
          <Link to={`/assets/${selectedNode.id}`} className={styles.selectionThumbLink} aria-label={m.openInViewer}>
            <img src={assetUrl(selectedNode.id, 'thumb')} alt="" className={styles.selectionThumb} />
          </Link>
          <div className={styles.selectionBody}>
            <span className={styles.selectionTitle}>{selectedNode.title || m.untitled}</span>
            <span className={styles.selectionMeta}>
              {fmt(m.neighborsHighlighted, { count: neighborsOf(neighborIndices, selected!).length })}
            </span>
            <div className={styles.selectionLinks}>
              <Link to={`/assets/${selectedNode.id}`}>{m.openInViewer}</Link>
              <Link to={`/lineage/${selectedNode.id}`}>{m.lineageGraph}</Link>
              <Link to={buildSimilarSearchPath(selectedNode.id)}>{m.similarInSearch}</Link>
            </div>
          </div>
          <div className={styles.selectionButtons}>
            <button
              ref={expandButtonRef}
              type="button"
              className={styles.selectionButton}
              aria-label={m.expandPreview}
              title={m.expandPreview}
              onClick={() => setPreviewOpen(true)}
            >
              <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                <path d="M3 13L12 4M6 4h6v6" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
            <button type="button" className={styles.selectionButton} aria-label={t.common.close} title={t.common.close} onClick={() => onSelect(null)}>
              ×
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
