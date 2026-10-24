/**
 * 配置の編集画面のキャンバス(ADR-0050 1章)。WebGL の canvas に素材を描き、その上の SVG に
 * 選択の枠・四隅のハンドル・回転のハンドルと、キャンバスの外側の暗幕を描く。
 *
 * 操作は Pointer Events で受ける(`touch-action: none`。マウス・ペン・タッチ共通)。
 * - 素材をドラッグ: 移動(押した素材を選ぶ。何も無い所を押すと選択を外す)
 * - 四隅のハンドル: 既定のモードでは中心を固定して縦横比を保った拡大縮小、「変形」のモードでは
 *   その角だけを動かす(凸を保てる所で止める)
 * - 上の丸: 回転(Shift で 15° 刻み)
 * - 2本指: 選んだ素材の拡大縮小 + 回転 + 移動
 * iOS の Safari は `touch-action: none` だけでは、素材を動かしながらページ(狭い幅でスクロールする
 * ダイアログ)もスクロールしてしまう。素材やハンドルに触れた指と2本目の指は、ネイティブの
 * touchstart / touchmove(passive でない)で `preventDefault` してスクロールさせない。何も無い所から
 * 始めた指はスクロールに使える。
 * ドラッグ中は `onPreview` で途中の状態を親へ渡し、指を離したときに `onCommit` で1回だけ
 * 履歴に積む。
 *
 * 画面の表示には preview の派生(長辺 2048px)をテクスチャにする。保存の書き出し
 * (`exportComposite`)は同じ描画の処理で原本を使う。
 */
import { useEffect, useLayoutEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import type { RegionBox } from '../../api/client'
import { assetUrl } from '../../api/assetUrl'
import { usePreventPageZoom } from '../../lib/usePreventPageZoom'
import { loadImage } from '../mask/canvasUtils'
import { setLayerQuad } from './editorDoc'
import {
  angleBetween,
  hitCorner,
  moveCornerClamped,
  pinchQuad,
  pointInQuad,
  quadAngleDegrees,
  quadCenter,
  rotateHandlePos,
  rotateQuad,
  scaleFromCornerDrag,
  snapAngle,
  translateQuad,
  type Pt,
  type Quad,
} from './quad'
import type { EditorDoc } from './recipe'
import { compositeRegionMarks } from './regionMarks'
import { CompositeRenderer, TRANSPARENT, type RenderLayer } from './renderer'
import styles from './CompositeEditor.module.css'

export type TransformMode = 'transform' | 'distort'

interface CompositeStageProps {
  doc: EditorDoc
  selectedKey: string | null
  mode: TransformMode
  onSelect: (key: string | null) => void
  onPreview: (doc: EditorDoc) => void
  onCommit: (doc: EditorDoc) => void
  /** WebGL を使えなかった・失った。 */
  onRendererError: () => void
  /** 素材のテクスチャの読み込みに失敗した Asset。 */
  onTextureError: (assetId: string) => void
  /** 重ねて見せる生成のフォームの四角(ADR-0053。表示だけで書き出しには描かない)。 */
  regions?: readonly RegionBox[]
}

type Gesture =
  | { kind: 'move'; key: string; base: EditorDoc; startQuad: Quad; startP: Pt }
  | { kind: 'scale'; key: string; base: EditorDoc; startQuad: Quad; corner: number }
  | { kind: 'distort'; key: string; base: EditorDoc; startQuad: Quad; corner: number }
  | { kind: 'rotate'; key: string; base: EditorDoc; startQuad: Quad; center: Pt; startP: Pt }
  | { kind: 'pinch'; key: string; base: EditorDoc; startQuad: Quad; ids: [number, number]; a0: Pt; b0: Pt }

const PREVIEW_TEXTURE_EDGE = 2048
/** ハンドルの見た目の半径(CSS の px)。 */
const HANDLE_RADIUS = 6
/** 回転のハンドルの、上の辺からの距離(CSS の px)。 */
const ROTATE_OFFSET = 28

interface View {
  /** キャンバスの px → CSS の px。 */
  scale: number
  offsetX: number
  offsetY: number
  width: number
  height: number
}

function computeView(container: { width: number; height: number }, canvas: { width: number; height: number }): View {
  const margin = Math.max(20, Math.min(56, Math.min(container.width, container.height) * 0.08))
  const availW = Math.max(1, container.width - margin * 2)
  const availH = Math.max(1, container.height - margin * 2)
  const scale = Math.min(availW / canvas.width, availH / canvas.height)
  return {
    scale,
    offsetX: (container.width - canvas.width * scale) / 2,
    offsetY: (container.height - canvas.height * scale) / 2,
    width: container.width,
    height: container.height,
  }
}

export function CompositeStage({
  doc,
  selectedKey,
  mode,
  onSelect,
  onPreview,
  onCommit,
  onRendererError,
  onTextureError,
  regions = [],
}: CompositeStageProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  // 2本指でページ全体が拡大されないようにする(iOS の Safari。lib/usePreventPageZoom.ts)。
  usePreventPageZoom(containerRef)
  const glCanvasRef = useRef<HTMLCanvasElement | null>(null)
  const svgRef = useRef<SVGSVGElement | null>(null)
  const rendererRef = useRef<CompositeRenderer | null>(null)
  const texturesRef = useRef(new Map<string, WebGLTexture>())
  const loadingRef = useRef(new Set<string>())
  const [textureVersion, setTextureVersion] = useState(0)
  const [containerSize, setContainerSize] = useState<{ width: number; height: number } | null>(null)
  const pointersRef = useRef(new Map<number, { x: number; y: number; type: string }>())
  const gestureRef = useRef<Gesture | null>(null)
  const draftRef = useRef<EditorDoc | null>(null)
  const [cursor, setCursor] = useState('default')

  // 最新の props をイベントのハンドラーから読む。
  const latest = useRef({ doc, onTextureError, onRendererError })
  useEffect(() => {
    latest.current = { doc, onTextureError, onRendererError }
  })

  useLayoutEffect(() => {
    const el = containerRef.current
    if (!el) return
    const update = () => setContainerSize({ width: el.clientWidth, height: el.clientHeight })
    update()
    const observer = new ResizeObserver(update)
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  // WebGL の準備。canvas は消えないので1回だけ。
  useEffect(() => {
    const canvas = glCanvasRef.current
    if (!canvas) return
    const renderer = CompositeRenderer.create(canvas)
    if (!renderer) {
      latest.current.onRendererError()
      return
    }
    rendererRef.current = renderer
    const textures = texturesRef.current
    const loading = loadingRef.current
    const handleLost = (e: Event) => {
      e.preventDefault()
      latest.current.onRendererError()
    }
    canvas.addEventListener('webglcontextlost', handleLost)
    setTextureVersion((v) => v + 1)
    return () => {
      canvas.removeEventListener('webglcontextlost', handleLost)
      for (const tex of textures.values()) renderer.deleteTexture(tex)
      textures.clear()
      loading.clear()
      renderer.dispose()
      rendererRef.current = null
    }
  }, [])

  // 素材のテクスチャを読み込む(まだ無いものだけ)。
  const assetIds = [...new Set(doc.layers.map((l) => l.assetId))]
  const assetIdsKey = assetIds.join(',')
  useEffect(() => {
    const renderer = rendererRef.current
    if (!renderer) return
    for (const id of assetIds) {
      if (texturesRef.current.has(id) || loadingRef.current.has(id)) continue
      loadingRef.current.add(id)
      loadImage(assetUrl(id, 'preview'))
        .then((img) => {
          if (rendererRef.current !== renderer) return
          texturesRef.current.set(id, renderer.createTexture(img, PREVIEW_TEXTURE_EDGE))
          setTextureVersion((v) => v + 1)
        })
        .catch(() => latest.current.onTextureError(id))
        .finally(() => loadingRef.current.delete(id))
    }
    // assetIds は assetIdsKey で表す。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assetIdsKey])

  const view = containerSize ? computeView(containerSize, doc.canvas) : null

  // 描く。
  useEffect(() => {
    const renderer = rendererRef.current
    const canvas = glCanvasRef.current
    if (!renderer || !canvas || !view) return
    const dpr = window.devicePixelRatio || 1
    const w = Math.max(1, Math.round(view.width * dpr))
    const h = Math.max(1, Math.round(view.height * dpr))
    if (canvas.width !== w) canvas.width = w
    if (canvas.height !== h) canvas.height = h
    const frame = requestAnimationFrame(() => {
      const layers: RenderLayer[] = []
      for (const l of doc.layers) {
        const texture = texturesRef.current.get(l.assetId)
        if (texture) layers.push({ texture, corners: l.quad, flipX: l.flipX, flipY: l.flipY })
      }
      renderer.clear(TRANSPARENT)
      renderer.drawLayers(layers, { scale: view.scale * dpr, offsetX: view.offsetX * dpr, offsetY: view.offsetY * dpr })
    })
    return () => cancelAnimationFrame(frame)
  }, [doc, view?.scale, view?.offsetX, view?.offsetY, view?.width, view?.height, textureVersion]) // eslint-disable-line react-hooks/exhaustive-deps

  function toCanvas(clientX: number, clientY: number): Pt | null {
    const svg = svgRef.current
    if (!svg || !view) return null
    const rect = svg.getBoundingClientRect()
    return { x: (clientX - rect.left - view.offsetX) / view.scale, y: (clientY - rect.top - view.offsetY) / view.scale }
  }

  function currentDoc(): EditorDoc {
    return draftRef.current ?? latest.current.doc
  }

  function hitRadiusCss(pointerType: string): number {
    return pointerType === 'touch' ? 22 : 11
  }

  /** 選んだ素材のハンドル(回転 → 四隅)と、素材(上から)を順に調べる。 */
  function hitTest(p: Pt, pointerType: string):
    | { kind: 'rotate' | 'corner'; key: string; corner: number }
    | { kind: 'layer'; key: string }
    | null {
    if (!view) return null
    const d = currentDoc()
    const radius = hitRadiusCss(pointerType) / view.scale
    const selected = d.layers.find((l) => l.key === selectedKey)
    if (selected) {
      const rh = rotateHandlePos(selected.quad, ROTATE_OFFSET / view.scale)
      if (Math.hypot(rh.x - p.x, rh.y - p.y) <= radius) return { kind: 'rotate', key: selected.key, corner: -1 }
      const corner = hitCorner(p, selected.quad, radius)
      if (corner !== -1) return { kind: 'corner', key: selected.key, corner }
    }
    for (let i = d.layers.length - 1; i >= 0; i -= 1) {
      if (pointInQuad(p, d.layers[i].quad)) return { kind: 'layer', key: d.layers[i].key }
    }
    return null
  }

  function startPinch(base: EditorDoc, key: string) {
    const entries = [...pointersRef.current.entries()].slice(0, 2)
    if (entries.length < 2) return
    const layer = base.layers.find((l) => l.key === key)
    if (!layer) return
    const a0 = toCanvas(entries[0][1].x, entries[0][1].y)
    const b0 = toCanvas(entries[1][1].x, entries[1][1].y)
    if (!a0 || !b0) return
    gestureRef.current = {
      kind: 'pinch',
      key,
      base,
      startQuad: layer.quad,
      ids: [entries[0][0], entries[1][0]],
      a0,
      b0,
    }
  }

  // 素材・ハンドルに触れた指でページをスクロールさせない(上のコメント)。ハンドラーは毎回の描画の
  // 関数を ref 経由で呼ぶ(リスナーは付け直さない)。
  const touchHitRef = useRef<(t: Touch) => boolean>(() => false)
  touchHitRef.current = (t: Touch) => {
    const p = toCanvas(t.clientX, t.clientY)
    return p !== null && hitTest(p, 'touch') !== null
  }
  useEffect(() => {
    const svg = svgRef.current
    if (!svg) return
    let blocking = false
    const onTouchStart = (e: TouchEvent) => {
      if (e.touches.length >= 2 || touchHitRef.current(e.changedTouches[0])) blocking = true
      if (blocking && e.cancelable) e.preventDefault()
    }
    const onTouchMove = (e: TouchEvent) => {
      if ((blocking || gestureRef.current) && e.cancelable) e.preventDefault()
    }
    const onTouchEnd = (e: TouchEvent) => {
      if (e.touches.length === 0) blocking = false
    }
    svg.addEventListener('touchstart', onTouchStart, { passive: false })
    svg.addEventListener('touchmove', onTouchMove, { passive: false })
    svg.addEventListener('touchend', onTouchEnd)
    svg.addEventListener('touchcancel', onTouchEnd)
    return () => {
      svg.removeEventListener('touchstart', onTouchStart)
      svg.removeEventListener('touchmove', onTouchMove)
      svg.removeEventListener('touchend', onTouchEnd)
      svg.removeEventListener('touchcancel', onTouchEnd)
    }
  }, [])

  function handlePointerDown(e: ReactPointerEvent<SVGSVGElement>) {
    if (e.button !== 0 && e.pointerType === 'mouse') return
    e.currentTarget.setPointerCapture(e.pointerId)
    pointersRef.current.set(e.pointerId, { x: e.clientX, y: e.clientY, type: e.pointerType })
    const d = currentDoc()

    if (pointersRef.current.size === 2) {
      // 2本目の指: 選んでいる(または動かしている)素材を2本指で操作する。
      const key = gestureRef.current?.key ?? selectedKey
      if (key) startPinch(draftRef.current ?? d, key)
      return
    }
    if (pointersRef.current.size > 2) return

    const p = toCanvas(e.clientX, e.clientY)
    if (!p) return
    const hit = hitTest(p, e.pointerType)
    if (!hit) {
      gestureRef.current = null
      onSelect(null)
      return
    }
    const layer = d.layers.find((l) => l.key === hit.key)
    if (!layer) return
    if (hit.kind === 'rotate') {
      gestureRef.current = { kind: 'rotate', key: hit.key, base: d, startQuad: layer.quad, center: quadCenter(layer.quad), startP: p }
    } else if (hit.kind === 'corner') {
      gestureRef.current =
        mode === 'distort'
          ? { kind: 'distort', key: hit.key, base: d, startQuad: layer.quad, corner: hit.corner }
          : { kind: 'scale', key: hit.key, base: d, startQuad: layer.quad, corner: hit.corner }
    } else {
      if (hit.key !== selectedKey) onSelect(hit.key)
      gestureRef.current = { kind: 'move', key: hit.key, base: d, startQuad: layer.quad, startP: p }
    }
  }

  function updateCursor(e: ReactPointerEvent<SVGSVGElement>) {
    const p = toCanvas(e.clientX, e.clientY)
    const hit = p ? hitTest(p, e.pointerType) : null
    const next = !hit ? 'default' : hit.kind === 'rotate' ? 'grab' : hit.kind === 'corner' ? 'crosshair' : 'move'
    if (next !== cursor) setCursor(next)
  }

  function handlePointerMove(e: ReactPointerEvent<SVGSVGElement>) {
    const tracked = pointersRef.current.get(e.pointerId)
    if (tracked) pointersRef.current.set(e.pointerId, { ...tracked, x: e.clientX, y: e.clientY })
    const g = gestureRef.current
    if (!g) {
      if (e.pointerType === 'mouse') updateCursor(e)
      return
    }
    let quad: Quad | null = null
    if (g.kind === 'pinch') {
      const a = pointersRef.current.get(g.ids[0])
      const b = pointersRef.current.get(g.ids[1])
      if (!a || !b) return
      const a1 = toCanvas(a.x, a.y)
      const b1 = toCanvas(b.x, b.y)
      if (!a1 || !b1) return
      quad = pinchQuad(g.startQuad, g.a0, g.b0, a1, b1)
    } else {
      if (pointersRef.current.size > 1) return
      const p = toCanvas(e.clientX, e.clientY)
      if (!p) return
      if (g.kind === 'move') {
        quad = translateQuad(g.startQuad, p.x - g.startP.x, p.y - g.startP.y)
      } else if (g.kind === 'scale') {
        quad = scaleFromCornerDrag(g.startQuad, g.startQuad[g.corner], p)
      } else if (g.kind === 'distort') {
        // つかんだ角は、前回の位置から動かす(止まった所から続けて動かせるように)。
        const current = draftRef.current?.layers.find((l) => l.key === g.key)?.quad ?? g.startQuad
        quad = moveCornerClamped(current, g.corner, p)
      } else {
        let angle = angleBetween(g.center, g.startP, p)
        if (e.shiftKey) {
          const startAngle = (quadAngleDegrees(g.startQuad) * Math.PI) / 180
          angle = snapAngle(startAngle + angle) - startAngle
        }
        quad = rotateQuad(g.startQuad, g.center, angle)
      }
    }
    if (!quad) return
    const next = setLayerQuad(g.base, g.key, quad)
    draftRef.current = next
    onPreview(next)
  }

  function finishGesture() {
    const draft = draftRef.current
    gestureRef.current = null
    draftRef.current = null
    if (draft) onCommit(draft)
  }

  function handlePointerUp(e: ReactPointerEvent<SVGSVGElement>) {
    pointersRef.current.delete(e.pointerId)
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId)
    const g = gestureRef.current
    if (!g) return
    if (g.kind === 'pinch') {
      // 2本指のうち1本でも離したら終える(残った1本で続けて動かさない)。
      if (g.ids.includes(e.pointerId)) finishGesture()
      return
    }
    if (pointersRef.current.size === 0) finishGesture()
  }

  // 表示(SVG)。
  const selected = doc.layers.find((l) => l.key === selectedKey) ?? null
  const toCss = (p: Pt) => (view ? { x: p.x * view.scale + view.offsetX, y: p.y * view.scale + view.offsetY } : p)
  const points = (q: Quad) =>
    q
      .map(toCss)
      .map((p) => `${p.x},${p.y}`)
      .join(' ')

  const canvasRect = view
    ? { x: view.offsetX, y: view.offsetY, w: doc.canvas.width * view.scale, h: doc.canvas.height * view.scale }
    : null

  return (
    <div ref={containerRef} className={styles.stage}>
      {canvasRect && (
        <div
          className={styles.canvasBackground}
          data-background={doc.background}
          style={{ left: canvasRect.x, top: canvasRect.y, width: canvasRect.w, height: canvasRect.h }}
        />
      )}
      <canvas
        ref={glCanvasRef}
        className={styles.glCanvas}
        style={view ? { width: view.width, height: view.height } : undefined}
      />
      <svg
        ref={svgRef}
        className={styles.overlay}
        style={{ cursor }}
        width={view?.width ?? 0}
        height={view?.height ?? 0}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerCancel={handlePointerUp}
      >
        {view && canvasRect && (
          <>
            {/* キャンバスの外側(書き出されない部分)を暗くする。 */}
            <path
              className={styles.outside}
              fillRule="evenodd"
              d={`M0 0H${view.width}V${view.height}H0Z M${canvasRect.x} ${canvasRect.y}h${canvasRect.w}v${canvasRect.h}h${-canvasRect.w}Z`}
            />
            <rect
              className={styles.canvasOutline}
              x={canvasRect.x}
              y={canvasRect.y}
              width={canvasRect.w}
              height={canvasRect.h}
            />
            {doc.layers.map((l) =>
              texturesRef.current.has(l.assetId) ? null : (
                <polygon key={l.key} className={styles.loadingLayer} points={points(l.quad)} />
              ),
            )}
            {/* 生成のフォームの四角(表示だけ。操作は受けない)。 */}
            {regions.length > 0 && (
              <g pointerEvents="none" data-testid="composite-regions">
                {compositeRegionMarks(regions, canvasRect).map((m) => (
                  <g key={m.number}>
                    <rect x={m.x} y={m.y} width={m.w} height={m.h} fill="none" stroke="rgba(255,255,255,0.9)" strokeWidth={4} />
                    <rect x={m.x} y={m.y} width={m.w} height={m.h} fill="none" stroke={m.color} strokeWidth={2} />
                    <rect x={m.x} y={m.y} width={m.badge} height={m.badge} rx={3} fill={m.color} stroke="#ffffff" strokeWidth={1.5} />
                    <text
                      x={m.x + m.badge / 2}
                      y={m.y + m.badge / 2}
                      textAnchor="middle"
                      dominantBaseline="central"
                      fontSize={m.badge * 0.62}
                      fontWeight={700}
                      fill={m.textColor}
                    >
                      {m.number}
                    </text>
                  </g>
                ))}
              </g>
            )}
            {selected && (
              <g>
                <polygon className={styles.selectionShadow} points={points(selected.quad)} />
                <polygon className={styles.selection} points={points(selected.quad)} />
                {(() => {
                  const top = toCss({ x: (selected.quad[0].x + selected.quad[1].x) / 2, y: (selected.quad[0].y + selected.quad[1].y) / 2 })
                  const rh = toCss(rotateHandlePos(selected.quad, ROTATE_OFFSET / view.scale))
                  return (
                    <>
                      <line className={styles.selection} x1={top.x} y1={top.y} x2={rh.x} y2={rh.y} />
                      <circle className={styles.rotateHandle} cx={rh.x} cy={rh.y} r={HANDLE_RADIUS} />
                    </>
                  )
                })()}
                {selected.quad.map((p, i) => {
                  const c = toCss(p)
                  return mode === 'distort' ? (
                    <rect
                      key={i}
                      className={styles.distortHandle}
                      x={c.x - HANDLE_RADIUS}
                      y={c.y - HANDLE_RADIUS}
                      width={HANDLE_RADIUS * 2}
                      height={HANDLE_RADIUS * 2}
                    />
                  ) : (
                    <circle key={i} className={styles.cornerHandle} cx={c.x} cy={c.y} r={HANDLE_RADIUS} />
                  )
                })}
              </g>
            )}
          </>
        )}
      </svg>
    </div>
  )
}
