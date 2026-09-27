/**
 * スケッチエディタ(全画面モーダル)。ADR-0010。MaskEditor と同じモーダル構成・
 * Pointer Events(`setPointerCapture`、`touch-action: none`)を使う。
 *
 * Canvas は2層: 下地(白紙は白、既存画像は preview を等倍で描く)と、線を描く透明レイヤー。
 * 描画は線レイヤーだけに行い、消しゴムは線レイヤー上で `destination-out`(下地は消えない)。
 * 保存時は下地 + 線を1枚の PNG に合成する。
 *
 * 「線レイヤーの表示オン/オフ」は下地との見比べ用の表示切り替えでしかない(線 canvas の
 * `visibility` を切り替えるだけ)。ペン・消しゴム・元に戻す・全消去は表示状態に関わらず常に
 * 使える。オフの状態で描き始めた場合は、見えないまま描かせないよう自動で表示を戻す。
 * 保存は表示状態に関わらず線を合成する。
 *
 * **未使用スケッチの再編集(ADR-0010、2026-09-25 追記)。** `base.kind === 'asset'` で
 * 渡された Asset が `kind=sketch` かつ `used_as_input=false`(まだどの Run の入力にもなって
 * いない)なら、`planSketchOpen`(sketchGeometry.ts)の判定に従って「元の下地 + 消せる線」の
 * 状態に組み立て直して開く。線レイヤーはサーバーに保存せず、ブラウザの IndexedDB
 * (`sketchLayerStore.ts`)にスケッチの Asset id をキーとして保持する。保存は
 * `replaces_asset_id` を送る置き換えになり、系列のノードを増やさない。それ以外
 * (使用済みのスケッチ・通常の画像)は従来どおりの上描き(`source_asset_id`)。
 */
import { useEffect, useLayoutEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { createAsset, type AssetDetail } from '../../api/client'
import { assetUrl } from '../../api/assetUrl'
import { fmt, useI18n } from '../../i18n'
import { canvasToBlob, computeFitScale, loadImage, type Size } from '../mask/canvasUtils'
import { interpolatePoints, pointerToCanvasCoords, type Point } from '../mask/maskGeometry'
import {
  brushRadiusFor,
  planSketchOpen,
  shouldCacheSavedLineLayer,
  type BrushSize,
  type PixelSize,
  type SketchOpenPlan,
  type SketchReopenAsset,
} from './sketchGeometry'
import { loadSketchPrefs, saveSketchPrefs } from './sketchPrefs'
import { useUndoShortcut } from '../mask/undoShortcut'
import * as sketchLayerStore from './sketchLayerStore'
import styles from './SketchEditor.module.css'

const MAX_LONG_EDGE = 2048
const MAX_HISTORY = 12

function sketchColors(t: ReturnType<typeof useI18n>['t']): readonly { hex: string; label: string }[] {
  return [
    { hex: '#111111', label: t.sketch.colors.black },
    { hex: '#ffffff', label: t.sketch.colors.white },
    { hex: '#e5484d', label: t.sketch.colors.red },
    { hex: '#3b82f6', label: t.sketch.colors.blue },
    { hex: '#22c55e', label: t.sketch.colors.green },
    { hex: '#eab308', label: t.sketch.colors.yellow },
  ]
}

function brushSizeOptions(
  t: ReturnType<typeof useI18n>['t'],
): readonly { size: BrushSize; label: string; dot: number }[] {
  return [
    { size: 'thin', label: t.sketch.sizes.thin, dot: 5 },
    { size: 'medium', label: t.sketch.sizes.medium, dot: 9 },
    { size: 'thick', label: t.sketch.sizes.thick, dot: 14 },
  ]
}

export type SketchBase =
  | { kind: 'blank'; width: number; height: number }
  | { kind: 'asset'; asset: AssetDetail }

export interface SketchSaveOptions {
  insertRecommendedPrompt: boolean
}

interface SketchEditorProps {
  base: SketchBase
  onSave: (asset: AssetDetail, options: SketchSaveOptions) => void
  onCancel: () => void
}

type Tool = 'pen' | 'erase'

function UndoIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M4 4.5H9a4 4 0 1 1 0 8H6"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path d="M6.5 2 4 4.5l2.5 2.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function ClearIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M3 4.5h10M6.5 4.5V3a1 1 0 0 1 1-1h1a1 1 0 0 1 1 1v1.5M4.5 4.5v8a1 1 0 0 0 1 1h5a1 1 0 0 0 1-1v-8"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

function EraserIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M10.5 2.5 13.5 5.5 6 13H3v-3z"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path d="M3 13h10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  )
}

function EyeIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M1 8s2.5-4.5 7-4.5S15 8 15 8s-2.5 4.5-7 4.5S1 8 1 8z"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinejoin="round"
      />
      <circle cx="8" cy="8" r="2" stroke="currentColor" strokeWidth="1.4" />
    </svg>
  )
}

function EyeOffIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M1 8s2.5-4.5 7-4.5S15 8 15 8s-2.5 4.5-7 4.5S1 8 1 8z"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinejoin="round"
      />
      <circle cx="8" cy="8" r="2" stroke="currentColor" strokeWidth="1.4" />
      <path d="M2 14 14 2" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  )
}

/** `new ImageData(...)` のための小さなラッパー(TS の DOM 型定義とのギャップを1箇所に閉じる)。 */
function cloneImageData(data: ImageData): ImageData {
  return new ImageData(
    new Uint8ClampedArray(data.data) as Uint8ClampedArray<ArrayBuffer>,
    data.width,
    data.height,
  )
}

/** `AssetDetail` から `planSketchOpen` が受け取る最小限の型に詰め替える。 */
function toReopenAsset(asset: AssetDetail): SketchReopenAsset {
  return {
    id: asset.id,
    kind: asset.kind,
    usedAsInput: asset.used_as_input,
    sourceAssetId: asset.source_asset_id ?? null,
    width: asset.width,
    height: asset.height,
  }
}

/**
 * 線レイヤーのキャッシュ(PNG の Blob)を、デコード済みの `HTMLImageElement` にする。
 * デコードできなければ null(呼び出し側は「キャッシュ無し」として扱う)。
 * `planSketchOpen` の判定はデコードが終わってから行う必要があるため、Canvas に描く前に
 * ここで先にデコードを済ませる。
 */
async function decodeCachedLayer(blob: Blob): Promise<HTMLImageElement | null> {
  const objectUrl = URL.createObjectURL(blob)
  try {
    return await loadImage(objectUrl)
  } catch {
    return null
  } finally {
    URL.revokeObjectURL(objectUrl)
  }
}

/** 線レイヤーの初期状態。init() で決めて、Canvas 生成後の layout effect が描く。 */
type LineLayerContent = { kind: 'empty' } | { kind: 'image'; img: HTMLImageElement }

export function SketchEditor({ base, onSave, onCancel }: SketchEditorProps) {
  const { t } = useI18n()
  const SKETCH_COLORS = sketchColors(t)
  const BRUSH_SIZES = brushSizeOptions(t)
  const baseCanvasRef = useRef<HTMLCanvasElement | null>(null)
  const lineCanvasRef = useRef<HTMLCanvasElement | null>(null)
  const containerRef = useRef<HTMLDivElement | null>(null)
  const cursorRef = useRef<HTMLDivElement | null>(null)
  const baseImageRef = useRef<HTMLImageElement | null>(null)
  const historyRef = useRef<ImageData[]>([])
  const drawingRef = useRef(false)
  const lastPointRef = useRef<Point | null>(null)
  // 開き方の判定結果(ADR-0010、2026-09-25 追記)。保存時にどの id を source/replaces として
  // 送るかは、これを見て決める(base.kind === 'blank' のときは常に null のまま)。
  const openPlanRef = useRef<SketchOpenPlan | null>(null)
  // 線レイヤーの初期状態。Canvas 生成後の layout effect が一度だけ描く。
  const lineInitRef = useRef<LineLayerContent>({ kind: 'empty' })

  const [canvasSize, setCanvasSize] = useState<PixelSize | null>(null)
  const [containerSize, setContainerSize] = useState<Size | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [tool, setTool] = useState<Tool>('pen')
  // ペン色と太さは前回の選択を引き継ぐ(localStorage)。初回は先頭の色と細いペン。
  const [initialPrefs] = useState(() => loadSketchPrefs(SKETCH_COLORS.map((c) => c.hex)))
  const [color, setColor] = useState(initialPrefs.color)
  const [brushSize, setBrushSize] = useState<BrushSize>(initialPrefs.brushSize)
  useEffect(() => {
    saveSketchPrefs({ color, brushSize })
  }, [color, brushSize])
  const [linesVisible, setLinesVisible] = useState(true)
  const [canUndo, setCanUndo] = useState(false)
  const [insertRecommendedPrompt, setInsertRecommendedPrompt] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  const queryClient = useQueryClient()

  // コンテナのサイズを測って表示スケールを決める(実寸はそのまま、表示だけ縮小)。
  useLayoutEffect(() => {
    const el = containerRef.current
    if (!el) return
    const update = () => setContainerSize({ width: el.clientWidth, height: el.clientHeight })
    update()
    const observer = new ResizeObserver(update)
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  // 下地・線レイヤーの準備(ADR-0010、2026-09-25 追記の「未使用スケッチの再編集」を含む)。
  // 白紙・通常の上描きはサイズを決めるだけ。未使用スケッチの再開は IndexedDB のキャッシュの
  // 有無で `planSketchOpen` の判定が変わるため、先に非同期でキャッシュを引く。
  useEffect(() => {
    let cancelled = false
    async function init() {
      if (base.kind === 'blank') {
        const plan = planSketchOpen({ kind: 'blank', width: base.width, height: base.height }, false, MAX_LONG_EDGE)
        openPlanRef.current = plan
        if (!cancelled && plan.base.kind === 'white') {
          setCanvasSize({ width: plan.base.width, height: plan.base.height })
        }
        return
      }

      const reopenAsset = toReopenAsset(base.asset)
      const isResumableSketch = reopenAsset.kind === 'sketch' && !reopenAsset.usedAsInput
      const cachedBlob = isResumableSketch ? await sketchLayerStore.getLayer(reopenAsset.id) : null
      if (cancelled) return
      // planSketchOpen を呼ぶ前にデコードを済ませる。デコードできなければキャッシュ無し扱い
      // にする(「読み込めなければ黙って空」ではなく、下地から作り直す判定に反映させる)。
      const cachedImage = cachedBlob ? await decodeCachedLayer(cachedBlob) : null
      if (cancelled) return

      const plan = planSketchOpen({ kind: 'asset', asset: reopenAsset }, cachedImage !== null, MAX_LONG_EDGE)
      openPlanRef.current = plan

      try {
        // 線レイヤーの初期状態を先に揃えてから canvasSize を決める(canvasSize の変化で
        // 下の layout effect が一度だけ描くため、後から読み込んだ画像は描かれない)。
        if (plan.lineLayer.kind === 'cache' && cachedImage) {
          lineInitRef.current = { kind: 'image', img: cachedImage }
        } else if (plan.lineLayer.kind === 'composite') {
          const img = await loadImage(assetUrl(plan.lineLayer.assetId, 'preview'))
          if (cancelled) return
          lineInitRef.current = { kind: 'image', img }
        } else {
          lineInitRef.current = { kind: 'empty' }
        }

        if (plan.base.kind === 'image') {
          const img = await loadImage(assetUrl(plan.base.assetId, 'preview'))
          if (cancelled) return
          baseImageRef.current = img
          setCanvasSize({ width: img.naturalWidth, height: img.naturalHeight })
        } else {
          baseImageRef.current = null
          setCanvasSize({ width: plan.base.width, height: plan.base.height })
        }
      } catch {
        if (!cancelled) setLoadError(t.sketch.loadError)
      }
    }
    void init()
    return () => {
      cancelled = true
    }
    // base はこのモーダルの生存期間中は変わらない前提。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // canvasSize が決まって Canvas 要素がその実寸で作られた後に、下地と線レイヤーの初期状態を描く。
  useLayoutEffect(() => {
    if (!canvasSize) return
    const canvas = baseCanvasRef.current
    const ctx = canvas?.getContext('2d')
    if (canvas && ctx) {
      if (baseImageRef.current) {
        ctx.drawImage(baseImageRef.current, 0, 0, canvas.width, canvas.height)
      } else {
        ctx.fillStyle = '#ffffff'
        ctx.fillRect(0, 0, canvas.width, canvas.height)
      }
    }

    const lineCanvas = lineCanvasRef.current
    const lineInit = lineInitRef.current
    if (lineCanvas && lineInit.kind === 'image') {
      lineCanvas.getContext('2d')?.drawImage(lineInit.img, 0, 0, lineCanvas.width, lineCanvas.height)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canvasSize])

  const shortEdge = canvasSize ? Math.min(canvasSize.width, canvasSize.height) : 0
  const brushRadius = brushRadiusFor(shortEdge, brushSize)

  function pushHistory() {
    const canvas = lineCanvasRef.current
    const ctx = canvas?.getContext('2d')
    if (!canvas || !ctx) return
    historyRef.current.push(ctx.getImageData(0, 0, canvas.width, canvas.height))
    if (historyRef.current.length > MAX_HISTORY) {
      historyRef.current.shift()
    }
    setCanUndo(historyRef.current.length > 0)
  }

  function drawDab(point: Point) {
    const ctx = lineCanvasRef.current?.getContext('2d')
    if (!ctx) return
    ctx.save()
    if (tool === 'erase') {
      ctx.globalCompositeOperation = 'destination-out'
      ctx.globalAlpha = 1
    } else {
      ctx.globalCompositeOperation = 'source-over'
      ctx.globalAlpha = 1
      ctx.fillStyle = color
    }
    ctx.beginPath()
    ctx.arc(point.x, point.y, brushRadius, 0, Math.PI * 2)
    ctx.fill()
    ctx.restore()
  }

  function toCanvasPoint(clientX: number, clientY: number): Point | null {
    const canvas = lineCanvasRef.current
    if (!canvas) return null
    const rect = canvas.getBoundingClientRect()
    return pointerToCanvasCoords(clientX, clientY, rect, { width: canvas.width, height: canvas.height })
  }

  function updateCursor(clientX: number, clientY: number) {
    const canvas = lineCanvasRef.current
    const cursor = cursorRef.current
    if (!canvas || !cursor) return
    const rect = canvas.getBoundingClientRect()
    const displayScale = rect.width / canvas.width
    const diameter = brushRadius * 2 * displayScale
    cursor.style.width = `${diameter}px`
    cursor.style.height = `${diameter}px`
    cursor.style.left = `${clientX - rect.left}px`
    cursor.style.top = `${clientY - rect.top}px`
  }

  function handlePointerDown(e: ReactPointerEvent<HTMLCanvasElement>) {
    if (!canvasSize) return
    const canvas = lineCanvasRef.current
    if (!canvas) return
    // 見えない状態のまま描かせない。表示だけを戻す(合成には関係しない)。
    if (!linesVisible) setLinesVisible(true)
    canvas.setPointerCapture(e.pointerId)
    const point = toCanvasPoint(e.clientX, e.clientY)
    if (!point) return
    pushHistory()
    drawingRef.current = true
    lastPointRef.current = point
    drawDab(point)
    setSaveError(null)
  }

  function handlePointerMove(e: ReactPointerEvent<HTMLCanvasElement>) {
    updateCursor(e.clientX, e.clientY)
    if (!drawingRef.current) return
    const point = toCanvasPoint(e.clientX, e.clientY)
    if (!point) return
    const from = lastPointRef.current ?? point
    const step = Math.max(1, brushRadius / 3)
    for (const p of interpolatePoints(from, point, step)) {
      drawDab(p)
    }
    lastPointRef.current = point
  }

  function endStroke(e: ReactPointerEvent<HTMLCanvasElement>) {
    if (drawingRef.current) {
      drawingRef.current = false
      lastPointRef.current = null
    }
    const canvas = lineCanvasRef.current
    if (canvas?.hasPointerCapture(e.pointerId)) {
      canvas.releasePointerCapture(e.pointerId)
    }
  }

  function handleUndo() {
    const canvas = lineCanvasRef.current
    const ctx = canvas?.getContext('2d')
    if (!canvas || !ctx) return
    const snapshot = historyRef.current.pop()
    setCanUndo(historyRef.current.length > 0)
    if (!snapshot) return
    ctx.putImageData(cloneImageData(snapshot), 0, 0)
  }

  // 描いている途中の undo は、ストロークの開始時に積んだ履歴と食い違うので受け付けない。
  useUndoShortcut(() => {
    if (!drawingRef.current) handleUndo()
  })

  function handleClearAll() {
    const canvas = lineCanvasRef.current
    const ctx = canvas?.getContext('2d')
    if (!canvas || !ctx) return
    pushHistory()
    ctx.clearRect(0, 0, canvas.width, canvas.height)
    setSaveError(null)
  }

  const saveMutation = useMutation({
    mutationFn: async () => {
      const baseCanvas = baseCanvasRef.current
      const lineCanvas = lineCanvasRef.current
      if (!baseCanvas || !lineCanvas) throw new Error(t.sketch.canvasInitError)

      const exportCanvas = document.createElement('canvas')
      exportCanvas.width = baseCanvas.width
      exportCanvas.height = baseCanvas.height
      const exportCtx = exportCanvas.getContext('2d')
      if (!exportCtx) throw new Error(t.sketch.exportCanvasInitError)
      // 表示状態(visibility)に関わらず、線は常に合成する。
      exportCtx.drawImage(baseCanvas, 0, 0)
      exportCtx.drawImage(lineCanvas, 0, 0)

      const blob = await canvasToBlob(exportCanvas)
      if (!blob) throw new Error(t.sketch.pngExportError)
      // 線レイヤーだけの PNG も作る(IndexedDB へ保存し、次回の再開に使う)。
      const lineBlob = await canvasToBlob(lineCanvas)

      if (base.kind === 'blank') {
        const asset = await createAsset(blob, 'sketch')
        return { asset, lineBlob, replacedAssetId: undefined as string | undefined }
      }
      // ADR-0010(2026-09-25 追記): 未使用スケッチの再開なら replaces_asset_id で置き換える
      // (source_asset_id はサーバーが決めるので送らない)。それ以外は従来どおりの上描き。
      if (openPlanRef.current?.save === 'replace') {
        const asset = await createAsset(blob, 'sketch', undefined, base.asset.id)
        return { asset, lineBlob, replacedAssetId: base.asset.id }
      }
      const asset = await createAsset(blob, 'sketch', base.asset.id)
      return { asset, lineBlob, replacedAssetId: undefined as string | undefined }
    },
    onSuccess: ({ asset, lineBlob, replacedAssetId }) => {
      // ストック一覧から見えるようにする。
      queryClient.invalidateQueries({ queryKey: ['assets'] })
      // 次回の再開用に線レイヤーを保存する。ただし「キャンバスの下地」と「応答の Asset の
      // source_asset_id」が一致するときだけ(「以前の線が消えるバグ」対応)。一致しなければ
      // 次回は合成画像を下地にする(線は焼き込まれるが消えない)。置き換え元のキャッシュ削除は
      // この判定に関わらず行う(失敗しても保存フロー自体は成功扱い。sketchLayerStore は
      // 例外を投げない)。
      const plan = openPlanRef.current
      if (lineBlob && plan && shouldCacheSavedLineLayer(plan, asset.source_asset_id ?? null)) {
        void sketchLayerStore.saveLayer(asset.id, lineBlob)
      }
      if (replacedAssetId) void sketchLayerStore.deleteLayer(replacedAssetId)
      onSave(asset, { insertRecommendedPrompt })
    },
    onError: (err: unknown) => {
      setSaveError(err instanceof Error ? err.message : t.sketch.saveFailedDefault)
    },
  })

  const displayScale = containerSize && canvasSize ? computeFitScale(containerSize, canvasSize) : 1
  const displayWidth = canvasSize ? Math.round(canvasSize.width * displayScale) : 0
  const displayHeight = canvasSize ? Math.round(canvasSize.height * displayScale) : 0

  return (
    <div className={styles.overlay} role="dialog" aria-modal="true">
      <div className={styles.modal}>
        <div className={styles.header}>
          <h2 className={styles.title}>{t.sketch.title}</h2>
          <button type="button" className={styles.closeButton} onClick={onCancel} aria-label={t.sketch.close} title={t.sketch.close}>
            ✕
          </button>
        </div>

        <div className={styles.toolbar}>
          <div className={styles.toolGroup}>
            {SKETCH_COLORS.map((c) => (
              <button
                key={c.hex}
                type="button"
                className={styles.swatch}
                style={{ background: c.hex }}
                data-selected={tool === 'pen' && color === c.hex}
                aria-label={fmt(t.sketch.colorAria, { label: c.label })}
                title={fmt(t.sketch.colorAria, { label: c.label })}
                onClick={() => {
                  setColor(c.hex)
                  setTool('pen')
                }}
              />
            ))}
          </div>

          <div className={styles.toolGroup}>
            {BRUSH_SIZES.map((b) => (
              <button
                key={b.size}
                type="button"
                className={styles.sizeButton}
                data-selected={brushSize === b.size}
                aria-label={fmt(t.sketch.sizeAria, { label: b.label })}
                title={fmt(t.sketch.sizeAria, { label: b.label })}
                onClick={() => setBrushSize(b.size)}
              >
                <span className={styles.sizeDot} style={{ width: b.dot, height: b.dot }} />
              </button>
            ))}
          </div>

          <div className={styles.toolGroup}>
            <button
              type="button"
              className={styles.toolButton}
              data-selected={tool === 'erase'}
              aria-label={t.sketch.erase}
              title={t.sketch.erase}
              onClick={() => setTool('erase')}
            >
              <EraserIcon />
            </button>
            <button
              type="button"
              className={styles.toolButton}
              aria-label={t.sketch.undo}
              title={t.sketch.undo}
              onClick={handleUndo}
              disabled={!canUndo}
            >
              <UndoIcon />
            </button>
            <button
              type="button"
              className={styles.toolButton}
              aria-label={t.sketch.clearAll}
              title={t.sketch.clearAll}
              onClick={handleClearAll}
            >
              <ClearIcon />
            </button>
            <button
              type="button"
              className={styles.toolButton}
              data-selected={!linesVisible}
              aria-label={linesVisible ? t.sketch.hideLines : t.sketch.showLines}
              title={linesVisible ? t.sketch.hideLines : t.sketch.showLines}
              onClick={() => setLinesVisible((v) => !v)}
            >
              {linesVisible ? <EyeIcon /> : <EyeOffIcon />}
            </button>
          </div>
        </div>

        <div ref={containerRef} className={styles.canvasArea}>
          {!canvasSize && !loadError && <p className={styles.loading}>{t.sketch.loading}</p>}
          {loadError && <p className={styles.note}>{loadError}</p>}
          {canvasSize && (
            <div className={styles.stage} style={{ width: displayWidth, height: displayHeight }}>
              <canvas
                ref={baseCanvasRef}
                width={canvasSize.width}
                height={canvasSize.height}
                className={styles.baseCanvas}
                style={{ width: displayWidth, height: displayHeight }}
              />
              <canvas
                ref={lineCanvasRef}
                width={canvasSize.width}
                height={canvasSize.height}
                className={styles.lineCanvas}
                style={{
                  width: displayWidth,
                  height: displayHeight,
                  visibility: linesVisible ? 'visible' : 'hidden',
                }}
                onPointerDown={handlePointerDown}
                onPointerMove={handlePointerMove}
                onPointerUp={endStroke}
                onPointerLeave={endStroke}
                onPointerCancel={endStroke}
              />
              <div ref={cursorRef} className={styles.brushCursor} />
            </div>
          )}
        </div>

        <div className={styles.footer}>
          <label className={styles.recommendedLabel}>
            <input
              type="checkbox"
              checked={insertRecommendedPrompt}
              onChange={(e) => setInsertRecommendedPrompt(e.target.checked)}
            />
            {t.sketch.insertRecommendedPrompt}
          </label>
          {saveError && <p className={styles.errorText}>{saveError}</p>}
          <button type="button" className={styles.secondaryButton} onClick={onCancel}>
            {t.sketch.cancel}
          </button>
          <button
            type="button"
            className={styles.primaryButton}
            onClick={() => saveMutation.mutate()}
            disabled={saveMutation.isPending || !canvasSize}
          >
            {saveMutation.isPending ? t.sketch.saving : t.sketch.save}
          </button>
        </div>
      </div>
    </div>
  )
}
