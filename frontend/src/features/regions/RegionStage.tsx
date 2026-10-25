/**
 * 「領域」の画面の画像と四角(ADR-0053 1章)。主たる入力のプレビューを枠に収めて表示し、その上の
 * SVG に四角・番号のバッジ・検出の候補を描く。操作は Pointer Events で受け、マウスとタッチを同じに扱う。
 *
 * - 何も無い所(選んでいない四角の内側も含む)をドラッグ: 新しい四角を描く。
 * - 四角の枠・バッジを押す: 選ぶ。選んだ四角は内側をドラッグして動かし、角と辺のハンドルで大きさを変える。
 * - 「動かす」の四角は、元の位置(点線)と移す先(実線)をそれぞれ動かせる。
 * - 検出の候補(点線と名前)を押す: 四角にする(ドラッグしたときは、そこから新しく描く)。
 * - 「画面を動かす」のときは、ドラッグで表示を動かす(パン。拡大したときに使う)。
 *
 * 四角は割合(0〜1)で持つので、拡大しても位置はずれない。ドラッグ中の途中の状態は `onPreview` で
 * 返し、指を離したときに1回だけ `onCommit` する(元に戻すの履歴を1つにする)。
 */
import { useCallback, useLayoutEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import { useElementSize } from '../../lib/useElementSize'
import {
  addRegion,
  boxCenter,
  boxFromPoints,
  defaultRegionKind,
  handlePoint,
  isBoxBigEnough,
  moveBox,
  newRegionKey,
  RESIZE_HANDLES,
  regionBadgeTextColor,
  regionColor,
  resizeBox,
  updateRegion,
  type Box,
  type EditorRegion,
  type Point,
  type ResizeHandle,
} from './regions'
import { estimateTextWidth, placeCaptions, type CaptionRect, type CaptionRequest } from './captionLayout'
import type { RegionCandidate } from './useRegionDetection'
import styles from './RegionEditor.module.css'

type Part = 'box' | 'source'

type Gesture =
  | {
      type: 'draw'
      start: Point
      startClient: Point
      key: string
      candidate: RegionCandidate | null
      base: EditorRegion[]
      moved: boolean
      last: EditorRegion[] | null
    }
  | {
      type: 'move'
      key: string
      part: Part
      start: Point
      startClient: Point
      orig: Box
      base: EditorRegion[]
      moved: boolean
      last: EditorRegion[] | null
    }
  | {
      type: 'resize'
      key: string
      part: Part
      handle: ResizeHandle
      base: EditorRegion[]
      moved: boolean
      last: EditorRegion[] | null
    }
  | { type: 'pan'; startClient: Point; startScroll: Point }

/** これ以上動いたら「押した」ではなく「ドラッグ」とみなす(px)。 */
const DRAG_THRESHOLD = 4
/** 名前の文字の大きさ(px。`RegionEditor.module.css` の `.candidateLabel` と同じ)。 */
const CAPTION_FONT = 11
/** 画像の周りの余白(px)。 */
const PAD = 12

export interface RegionStageProps {
  /**
   * 下に敷く画像。null なら白紙(入力画像の無い Run。出力の大きさの縦横比の白い面。ADR-0053 6章)。
   * 白紙でも四角は `natural` に対する割合で持つ。
   */
  imageUrl: string | null
  natural: { width: number; height: number }
  regions: EditorRegion[]
  selectedKey: string | null
  candidates: RegionCandidate[]
  zoom: number
  panMode: boolean
  /** 新しい四角を描けるか(上限に達していないか)。 */
  canAdd: boolean
  labels: { badge: (n: number) => string }
  onSelect: (key: string | null) => void
  onPreview: (regions: EditorRegion[] | null) => void
  onCommit: (regions: EditorRegion[], selectKey?: string) => void
  onAdoptCandidate: (candidate: RegionCandidate) => void
  onImageError: () => void
}

function isCoarsePointer(): boolean {
  return typeof window !== 'undefined' && window.matchMedia('(pointer: coarse)').matches
}

export function RegionStage({
  imageUrl,
  natural,
  regions,
  selectedKey,
  candidates,
  zoom,
  panMode,
  canAdd,
  labels,
  onSelect,
  onPreview,
  onCommit,
  onAdoptCandidate,
  onImageError,
}: RegionStageProps) {
  const [measureRef, , areaSize] = useElementSize<HTMLDivElement>()
  // スクロール(パンと拡大の中心合わせ)に使う要素。
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const areaRef = useCallback(
    (el: HTMLDivElement | null) => {
      scrollRef.current = el
      measureRef(el)
    },
    [measureRef],
  )
  const svgRef = useRef<SVGSVGElement | null>(null)
  const gestureRef = useRef<Gesture | null>(null)
  const [panning, setPanning] = useState(false)
  const [coarse] = useState(isCoarsePointer)
  const handleSize = coarse ? 16 : 9
  const hitRadius = coarse ? 22 : 10
  const badgeSize = coarse ? 22 : 18
  const borderHit = coarse ? 24 : 12

  const areaW = areaSize?.width ?? 0
  const areaH = areaSize?.height ?? 0
  const fit =
    areaW > 0 && areaH > 0 ? Math.max(0.01, Math.min((areaW - PAD * 2) / natural.width, (areaH - PAD * 2) / natural.height)) : 0
  const dispW = natural.width * fit * zoom
  const dispH = natural.height * fit * zoom
  const innerW = Math.max(areaW, dispW + PAD * 2)
  const innerH = Math.max(areaH, dispH + PAD * 2)

  // 拡大率を変えたとき、表示の中心を保つ。
  const prevZoomRef = useRef(zoom)
  useLayoutEffect(() => {
    const el = scrollRef.current
    const prev = prevZoomRef.current
    prevZoomRef.current = zoom
    if (!el || prev === zoom || areaW === 0) return
    const ratio = zoom / prev
    const cx = el.scrollLeft + areaW / 2
    const cy = el.scrollTop + areaH / 2
    el.scrollLeft = cx * ratio - areaW / 2
    el.scrollTop = cy * ratio - areaH / 2
  }, [zoom, areaW, areaH])

  function pointFromClient(clientX: number, clientY: number): Point {
    const rect = svgRef.current?.getBoundingClientRect()
    if (!rect || rect.width === 0 || rect.height === 0) return { x: 0, y: 0 }
    return {
      x: Math.min(1, Math.max(0, (clientX - rect.left) / rect.width)),
      y: Math.min(1, Math.max(0, (clientY - rect.top) / rect.height)),
    }
  }

  function handlePointerDown(e: ReactPointerEvent<SVGSVGElement>) {
    if (e.button !== 0 && e.pointerType === 'mouse') return
    if (gestureRef.current) return
    e.preventDefault()
    const client = { x: e.clientX, y: e.clientY }
    if (panMode) {
      const el = scrollRef.current
      if (!el) return
      gestureRef.current = { type: 'pan', startClient: client, startScroll: { x: el.scrollLeft, y: el.scrollTop } }
      setPanning(true)
      e.currentTarget.setPointerCapture(e.pointerId)
      return
    }
    const point = pointFromClient(e.clientX, e.clientY)
    const hit = (e.target as Element).closest('[data-hit]')
    const kind = hit?.getAttribute('data-hit')
    const key = hit?.getAttribute('data-key') ?? null
    const part = (hit?.getAttribute('data-part') as Part | null) ?? 'box'
    const region = key ? regions.find((r) => r.key === key) : undefined
    e.currentTarget.setPointerCapture(e.pointerId)

    if (kind === 'handle' && region) {
      onSelect(region.key)
      gestureRef.current = {
        type: 'resize',
        key: region.key,
        part,
        handle: hit!.getAttribute('data-handle') as ResizeHandle,
        base: regions,
        moved: false,
        last: null,
      }
      return
    }
    if (kind === 'box' && region) {
      onSelect(region.key)
      const orig = part === 'source' && region.sourceBox ? region.sourceBox : region.box
      gestureRef.current = { type: 'move', key: region.key, part, start: point, startClient: client, orig, base: regions, moved: false, last: null }
      return
    }
    const candidateKey = kind === 'candidate' ? hit?.getAttribute('data-ckey') : null
    gestureRef.current = {
      type: 'draw',
      start: point,
      startClient: client,
      key: newRegionKey(),
      candidate: candidates.find((c) => c.key === candidateKey) ?? null,
      base: regions,
      moved: false,
      last: null,
    }
  }

  function handlePointerMove(e: ReactPointerEvent<SVGSVGElement>) {
    const g = gestureRef.current
    if (!g) return
    if (g.type === 'pan') {
      const el = scrollRef.current
      if (!el) return
      el.scrollLeft = g.startScroll.x - (e.clientX - g.startClient.x)
      el.scrollTop = g.startScroll.y - (e.clientY - g.startClient.y)
      return
    }
    const point = pointFromClient(e.clientX, e.clientY)
    if (g.type === 'draw' || g.type === 'move') {
      if (!g.moved && Math.hypot(e.clientX - g.startClient.x, e.clientY - g.startClient.y) < DRAG_THRESHOLD) return
    }
    g.moved = true
    let next: EditorRegion[] | null = null
    if (g.type === 'draw') {
      if (!canAdd) return
      next = addRegion(g.base, boxFromPoints(g.start, point), { key: g.key, kind: defaultRegionKind(imageUrl === null) })
    } else if (g.type === 'move') {
      const moved = moveBox(g.orig, point.x - g.start.x, point.y - g.start.y)
      next = updateRegion(g.base, g.key, g.part === 'source' ? { sourceBox: moved } : { box: moved })
    } else {
      const region = g.base.find((r) => r.key === g.key)
      const orig = region ? (g.part === 'source' && region.sourceBox ? region.sourceBox : region.box) : null
      if (!orig) return
      const resized = resizeBox(orig, g.handle, point)
      next = updateRegion(g.base, g.key, g.part === 'source' ? { sourceBox: resized } : { box: resized })
    }
    g.last = next
    onPreview(next)
  }

  function finish(e: ReactPointerEvent<SVGSVGElement>, cancelled: boolean) {
    const g = gestureRef.current
    if (!g) return
    gestureRef.current = null
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId)
    if (g.type === 'pan') {
      setPanning(false)
      return
    }
    if (cancelled) {
      onPreview(null)
      return
    }
    if (g.type === 'draw') {
      if (!g.moved) {
        onPreview(null)
        if (g.candidate) onAdoptCandidate(g.candidate)
        else onSelect(null)
        return
      }
      const drawn = g.last?.find((r) => r.key === g.key)
      if (g.last && drawn && isBoxBigEnough(drawn.box)) onCommit(g.last, g.key)
      else onPreview(null)
      return
    }
    if (g.moved && g.last) onCommit(g.last)
    else onPreview(null)
  }

  const ordered = selectedKey
    ? [...regions.filter((r) => r.key !== selectedKey), ...regions.filter((r) => r.key === selectedKey)]
    : regions

  function px(box: Box) {
    return { x: box[0] * dispW, y: box[1] * dispH, w: (box[2] - box[0]) * dispW, h: (box[3] - box[1]) * dispH }
  }

  function badgePos(box: Box) {
    const r = px(box)
    return {
      x: Math.min(Math.max(0, r.x + 3), Math.max(0, dispW - badgeSize)),
      y: Math.min(Math.max(0, r.y + 3), Math.max(0, dispH - badgeSize)),
    }
  }

  // 四角と候補の名前の位置。近い四角の名前が重ならないよう、重なるときは上下にずらす。
  const captionHeight = CAPTION_FONT * 1.3
  const captionRequests: CaptionRequest[] = []
  const badgeRects: CaptionRect[] = []
  for (const region of regions) {
    const b = badgePos(region.box)
    badgeRects.push({ x: b.x, y: b.y, width: badgeSize, height: badgeSize })
    if (region.kind === 'move' && region.sourceBox) {
      const sb = badgePos(region.sourceBox)
      badgeRects.push({ x: sb.x, y: sb.y, width: badgeSize, height: badgeSize })
    }
    const label = region.label.trim()
    if (label) {
      captionRequests.push({
        key: `r:${region.key}`,
        x: b.x + badgeSize + 4,
        y: b.y + (badgeSize - captionHeight) / 2,
        width: estimateTextWidth(label, CAPTION_FONT) + 4,
        height: captionHeight,
      })
    }
  }
  for (const c of candidates) {
    const r = px(c.box)
    captionRequests.push({
      key: `c:${c.key}`,
      x: r.x + 4,
      y: r.y + 4,
      width: estimateTextWidth(c.label, CAPTION_FONT) + 4,
      height: captionHeight,
    })
  }
  const captions = placeCaptions(captionRequests, { width: dispW, height: dispH }, badgeRects)

  function badge(box: Box, index: number, key: string, part: Part, faded: boolean, caption?: string) {
    const { x, y } = badgePos(box)
    const captionPos = caption ? captions.get(`r:${key}`) : undefined
    return (
      <g data-hit="box" data-key={key} data-part={part} opacity={faded ? 0.75 : 1} className={styles.grab}>
        <rect x={x} y={y} width={badgeSize} height={badgeSize} rx={3} fill={regionColor(index)} stroke="#ffffff" strokeWidth={1.5} />
        <text
          x={x + badgeSize / 2}
          y={y + badgeSize / 2}
          textAnchor="middle"
          dominantBaseline="central"
          fontSize={badgeSize * 0.62}
          fontWeight={700}
          fill={regionBadgeTextColor(index)}
          className={styles.badgeText}
        >
          {index + 1}
        </text>
        {/* 四角の名前(ADR-0053)。 */}
        {caption && captionPos && (
          <text x={captionPos.x} y={captionPos.y + 1} dominantBaseline="hanging" className={styles.candidateLabel}>
            {caption}
          </text>
        )}
      </g>
    )
  }

  function frame(box: Box, index: number, key: string, part: Part, selected: boolean, dashed: boolean) {
    const r = px(box)
    const color = regionColor(index)
    const width = selected ? 3 : 2
    return (
      <>
        <rect x={r.x} y={r.y} width={r.w} height={r.h} fill="none" stroke="rgba(255,255,255,0.9)" strokeWidth={width + 2} strokeDasharray={dashed ? '7 5' : undefined} pointerEvents="none" />
        <rect x={r.x} y={r.y} width={r.w} height={r.h} fill={selected ? 'rgba(255,255,255,0.06)' : 'none'} stroke={color} strokeWidth={width} strokeDasharray={dashed ? '7 5' : undefined} pointerEvents="none" />
        {/* 当たり判定。選んでいない四角は枠だけ(内側に新しい四角を描けるように)、選んだ四角は内側全体。 */}
        <rect
          data-hit="box"
          data-key={key}
          data-part={part}
          x={r.x}
          y={r.y}
          width={r.w}
          height={r.h}
          fill="none"
          stroke="transparent"
          strokeWidth={borderHit}
          pointerEvents={selected ? 'all' : 'stroke'}
          className={styles.grab}
        />
      </>
    )
  }

  function handles(box: Box, index: number, key: string, part: Part) {
    return RESIZE_HANDLES.map((h) => {
      const p = handlePoint(box, h)
      const cx = p.x * dispW
      const cy = p.y * dispH
      return (
        <g key={`${part}-${h}`} data-hit="handle" data-key={key} data-part={part} data-handle={h} className={styles[`handle_${h}`]}>
          <circle cx={cx} cy={cy} r={hitRadius} fill="transparent" />
          <rect
            x={cx - handleSize / 2}
            y={cy - handleSize / 2}
            width={handleSize}
            height={handleSize}
            fill="#ffffff"
            stroke={regionColor(index)}
            strokeWidth={2}
          />
        </g>
      )
    })
  }

  function arrow(source: Box, target: Box, index: number) {
    const a = boxCenter(source)
    const b = boxCenter(target)
    const sx = a.x * dispW
    const sy = a.y * dispH
    const ex = b.x * dispW
    const ey = b.y * dispH
    const len = Math.hypot(ex - sx, ey - sy)
    if (len < 16) return null
    const ux = (ex - sx) / len
    const uy = (ey - sy) / len
    const head = 12
    const bx = ex - ux * head
    const by = ey - uy * head
    const color = regionColor(index)
    const points = `${ex},${ey} ${bx - uy * head * 0.55},${by + ux * head * 0.55} ${bx + uy * head * 0.55},${by - ux * head * 0.55}`
    return (
      <g pointerEvents="none">
        <line x1={sx} y1={sy} x2={bx} y2={by} stroke="rgba(255,255,255,0.9)" strokeWidth={4} />
        <line x1={sx} y1={sy} x2={bx} y2={by} stroke={color} strokeWidth={2} />
        <polygon points={points} fill={color} stroke="#ffffff" strokeWidth={1} />
      </g>
    )
  }

  return (
    <div ref={areaRef} className={styles.stageScroll} data-zoomed={zoom > 1 || undefined}>
      <div className={styles.stageInner} style={{ width: innerW, height: innerH }}>
        {fit > 0 && (
          <div
            className={styles.imageFrame}
            style={{ width: dispW, height: dispH, left: (innerW - dispW) / 2, top: (innerH - dispH) / 2 }}
          >
            {imageUrl === null ? (
              <div className={styles.blankCanvas} aria-hidden="true" />
            ) : (
              <img className={styles.stageImage} src={imageUrl} alt="" draggable={false} onError={onImageError} />
            )}
            <svg
              ref={svgRef}
              className={styles.stageSvg}
              width={dispW}
              height={dispH}
              viewBox={`0 0 ${dispW} ${dispH}`}
              data-pan={panMode || undefined}
              data-panning={panning || undefined}
              onPointerDown={handlePointerDown}
              onPointerMove={handlePointerMove}
              onPointerUp={(e) => finish(e, false)}
              onPointerCancel={(e) => finish(e, true)}
            >
              <rect x={0} y={0} width={dispW} height={dispH} fill="transparent" />
              {candidates.map((c) => {
                const r = px(c.box)
                const pos = captions.get(`c:${c.key}`) ?? { x: r.x + 4, y: r.y + 4 }
                return (
                  <g key={c.key} data-hit="candidate" data-ckey={c.key} className={styles.candidate}>
                    <rect x={r.x} y={r.y} width={r.w} height={r.h} fill="rgba(255,255,255,0.04)" stroke="rgba(0,0,0,0.75)" strokeWidth={3} strokeDasharray="5 4" pointerEvents="all" />
                    <rect x={r.x} y={r.y} width={r.w} height={r.h} fill="none" stroke="#ffffff" strokeWidth={1.5} strokeDasharray="5 4" pointerEvents="none" />
                    <text x={pos.x} y={pos.y + 1} dominantBaseline="hanging" className={styles.candidateLabel}>
                      {c.label}
                    </text>
                  </g>
                )
              })}
              {ordered.map((region) => {
                const index = regions.indexOf(region)
                const selected = region.key === selectedKey
                const isMove = region.kind === 'move' && region.sourceBox !== null
                return (
                  <g key={region.key} aria-label={labels.badge(index + 1)}>
                    {isMove && region.sourceBox && (
                      <>
                        {frame(region.sourceBox, index, region.key, 'source', selected, true)}
                        {arrow(region.sourceBox, region.box, index)}
                        {badge(region.sourceBox, index, region.key, 'source', true)}
                      </>
                    )}
                    {frame(region.box, index, region.key, 'box', selected, false)}
                    {badge(region.box, index, region.key, 'box', false, region.label.trim() || undefined)}
                    {selected && isMove && region.sourceBox && handles(region.sourceBox, index, region.key, 'source')}
                    {selected && handles(region.box, index, region.key, 'box')}
                  </g>
                )
              })}
            </svg>
          </div>
        )}
      </div>
    </div>
  )
}
