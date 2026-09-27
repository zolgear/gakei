/**
 * マスクエディタ(全画面モーダル)。position 0 の入力画像に対して開く。
 * Canvas は画像の実寸(asset.width × asset.height)で保持し、表示だけ CSS で縮小する。
 * 履歴(undo)はメモリを食いすぎないよう、アルファチャンネルだけをランレングス圧縮して保持する
 * (RGB は常に固定のブラシ色なので、アルファさえあれば見た目を完全に復元できる)。
 */
import { useEffect, useLayoutEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createAsset, getAsset, type AssetDetail } from '../../api/client'
import { assetUrl } from '../../api/assetUrl'
import { fmt, useI18n } from '../../i18n'
import { canvasToBlob, computeFitScale, loadImage, type Size } from './canvasUtils'
import {
  alphaToOverlayRgba,
  composeMaskRgba,
  decodeRunLength,
  encodeRunLength,
  extractAlphaChannel,
  hasAnyPaint,
  invertPaintAlpha,
  type RunLengthPair,
} from './maskAlpha'
import { interpolatePoints, pointerToCanvasCoords, type Point } from './maskGeometry'
import { useUndoShortcut } from './undoShortcut'
import styles from './MaskEditor.module.css'

const BRUSH_COLOR: readonly [number, number, number] = [255, 59, 59]
const BRUSH_ALPHA = 0.55
const MAX_HISTORY = 12

interface HistorySnapshot {
  runs: RunLengthPair[]
  length: number
}

interface MaskEditorProps {
  baseAsset: AssetDetail
  existingMaskAssetId?: string
  maxMaskBytes: number
  onSave: (asset: AssetDetail) => void
  onCancel: () => void
}

/**
 * `new ImageData(...)` のための小さなラッパー。TS の DOM 型定義は
 * `Uint8ClampedArray<ArrayBuffer>` を要求するが、実行時には SharedArrayBuffer になることは
 * ないため、ここでキャストを1箇所に閉じる。
 */
function toImageData(data: Uint8ClampedArray, width: number, height: number): ImageData {
  return new ImageData(data as Uint8ClampedArray<ArrayBuffer>, width, height)
}

export function MaskEditor({
  baseAsset,
  existingMaskAssetId,
  maxMaskBytes,
  onSave,
  onCancel,
}: MaskEditorProps) {
  const { t } = useI18n()
  const m = t.mask
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const containerRef = useRef<HTMLDivElement | null>(null)
  const cursorRef = useRef<HTMLDivElement | null>(null)
  const historyRef = useRef<HistorySnapshot[]>([])
  const drawingRef = useRef(false)
  const lastPointRef = useRef<Point | null>(null)

  const [containerSize, setContainerSize] = useState<Size | null>(null)
  const [mode, setMode] = useState<'paint' | 'erase'>('paint')
  const [brushRadius, setBrushRadius] = useState(() =>
    Math.max(6, Math.round(Math.min(baseAsset.width, baseAsset.height) / 30)),
  )
  const [canUndo, setCanUndo] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [isRestoring, setIsRestoring] = useState(existingMaskAssetId !== undefined)

  const queryClient = useQueryClient()
  const previewUrl = assetUrl(baseAsset.id, 'preview')

  const existingMaskQuery = useQuery({
    queryKey: ['asset', existingMaskAssetId],
    queryFn: () => getAsset(existingMaskAssetId as string),
    enabled: existingMaskAssetId !== undefined,
  })

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

  // 既存マスクがあれば読み込んで Canvas に復元する。
  useEffect(() => {
    let cancelled = false
    async function restore() {
      const canvas = canvasRef.current
      if (!canvas) return
      const ctx = canvas.getContext('2d')
      if (!ctx) return

      if (!existingMaskAssetId) {
        setIsRestoring(false)
        return
      }
      try {
        const img = await loadImage(assetUrl(existingMaskAssetId, 'original'))
        if (cancelled) return
        const offscreen = document.createElement('canvas')
        offscreen.width = baseAsset.width
        offscreen.height = baseAsset.height
        const offCtx = offscreen.getContext('2d')
        if (!offCtx) return
        offCtx.drawImage(img, 0, 0, baseAsset.width, baseAsset.height)
        const maskRgba = offCtx.getImageData(0, 0, baseAsset.width, baseAsset.height).data
        // 既存マスクは「透明=編集対象」。paintAlpha は逆(塗った=編集対象)にする代わりに、
        // ここでは表示用にそのまま「透明だった所」をブラシ色で塗って見せたいので、
        // alpha を反転して(255-alpha)paintAlpha として扱う。
        const paintAlpha = new Uint8ClampedArray(maskRgba.length / 4)
        for (let i = 0; i < paintAlpha.length; i += 1) {
          paintAlpha[i] = 255 - maskRgba[i * 4 + 3]
        }
        const overlay = alphaToOverlayRgba(paintAlpha, BRUSH_COLOR)
        ctx.putImageData(toImageData(overlay, baseAsset.width, baseAsset.height), 0, 0)
      } catch {
        // 復元に失敗しても白紙から描けるようにするだけで、エラー表示はしない。
      } finally {
        if (!cancelled) setIsRestoring(false)
      }
    }
    void restore()
    return () => {
      cancelled = true
    }
    // baseAsset/existingMaskAssetId はこのモーダルの生存期間中は変わらない前提。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function pushHistory() {
    const canvas = canvasRef.current
    const ctx = canvas?.getContext('2d')
    if (!canvas || !ctx) return
    const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data
    const alpha = extractAlphaChannel(data)
    const runs = encodeRunLength(alpha)
    historyRef.current.push({ runs, length: alpha.length })
    if (historyRef.current.length > MAX_HISTORY) {
      historyRef.current.shift()
    }
    setCanUndo(historyRef.current.length > 0)
  }

  function drawDab(point: Point) {
    const canvas = canvasRef.current
    const ctx = canvas?.getContext('2d')
    if (!ctx) return
    ctx.save()
    if (mode === 'erase') {
      ctx.globalCompositeOperation = 'destination-out'
      ctx.globalAlpha = 1
    } else {
      ctx.globalCompositeOperation = 'source-over'
      ctx.globalAlpha = BRUSH_ALPHA
    }
    ctx.fillStyle = `rgb(${BRUSH_COLOR[0]}, ${BRUSH_COLOR[1]}, ${BRUSH_COLOR[2]})`
    ctx.beginPath()
    ctx.arc(point.x, point.y, brushRadius, 0, Math.PI * 2)
    ctx.fill()
    ctx.restore()
  }

  function toCanvasPoint(clientX: number, clientY: number): Point | null {
    const canvas = canvasRef.current
    if (!canvas) return null
    const rect = canvas.getBoundingClientRect()
    return pointerToCanvasCoords(clientX, clientY, rect, {
      width: canvas.width,
      height: canvas.height,
    })
  }

  function updateCursor(clientX: number, clientY: number) {
    const canvas = canvasRef.current
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
    if (isRestoring) return
    const canvas = canvasRef.current
    if (!canvas) return
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
    const canvas = canvasRef.current
    if (canvas?.hasPointerCapture(e.pointerId)) {
      canvas.releasePointerCapture(e.pointerId)
    }
  }

  function handleClear() {
    const canvas = canvasRef.current
    const ctx = canvas?.getContext('2d')
    if (!canvas || !ctx) return
    pushHistory()
    ctx.clearRect(0, 0, canvas.width, canvas.height)
    setSaveError(null)
  }

  function handleInvert() {
    const canvas = canvasRef.current
    const ctx = canvas?.getContext('2d')
    if (!canvas || !ctx) return
    pushHistory()
    const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data
    const alpha = extractAlphaChannel(data)
    const inverted = invertPaintAlpha(alpha)
    const overlay = alphaToOverlayRgba(inverted, BRUSH_COLOR)
    ctx.putImageData(toImageData(overlay, canvas.width, canvas.height), 0, 0)
    setSaveError(null)
  }

  function handleUndo() {
    const canvas = canvasRef.current
    const ctx = canvas?.getContext('2d')
    if (!canvas || !ctx) return
    const snapshot = historyRef.current.pop()
    setCanUndo(historyRef.current.length > 0)
    if (!snapshot) return
    const alpha = decodeRunLength(snapshot.runs, snapshot.length)
    const overlay = alphaToOverlayRgba(alpha, BRUSH_COLOR)
    ctx.putImageData(toImageData(overlay, canvas.width, canvas.height), 0, 0)
  }

  // 描いている途中の undo は、ストロークの開始時に積んだ履歴と食い違うので受け付けない。
  useUndoShortcut(() => {
    if (!drawingRef.current) handleUndo()
  })

  const saveMutation = useMutation({
    mutationFn: async () => {
      const canvas = canvasRef.current
      const ctx = canvas?.getContext('2d')
      if (!canvas || !ctx) throw new Error(m.canvasInitError)

      const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data
      const alpha = extractAlphaChannel(data)
      if (!hasAnyPaint(alpha)) {
        throw new Error(m.noPaintError)
      }

      const exportCanvas = document.createElement('canvas')
      exportCanvas.width = canvas.width
      exportCanvas.height = canvas.height
      const exportCtx = exportCanvas.getContext('2d')
      if (!exportCtx) throw new Error(m.exportCanvasInitError)
      const exportRgba = composeMaskRgba(alpha)
      exportCtx.putImageData(toImageData(exportRgba, canvas.width, canvas.height), 0, 0)

      const blob = await canvasToBlob(exportCanvas)
      if (!blob) throw new Error(m.pngExportError)
      if (blob.size >= maxMaskBytes) {
        throw new Error(fmt(m.maxSizeExceeded, { maxMb: Math.round(maxMaskBytes / 1024 / 1024) }))
      }

      // 既存マスクの続きを描いている場合は置き換える(ADR-0010、2026-09-25 追記をマスクにも
      // 一般化。未使用なら置き換え元をサーバー側で論理削除、使用済みなら残す)。
      return createAsset(blob, 'mask', undefined, existingMaskAssetId)
    },
    onSuccess: (asset) => {
      // ストック一覧(マスクも表示対象)から見えるようにする。
      queryClient.invalidateQueries({ queryKey: ['assets'] })
      onSave(asset)
    },
    onError: (err: unknown) => {
      setSaveError(err instanceof Error ? err.message : m.saveFailedDefault)
    },
  })

  const displayScale = containerSize
    ? computeFitScale(containerSize, { width: baseAsset.width, height: baseAsset.height })
    : 1
  const displayWidth = Math.round(baseAsset.width * displayScale)
  const displayHeight = Math.round(baseAsset.height * displayScale)

  return (
    <div className={styles.overlay} role="dialog" aria-modal="true">
      <div className={styles.modal}>
        <div className={styles.header}>
          <h2 className={styles.title}>{m.title}</h2>
          <button type="button" className={styles.closeButton} onClick={onCancel}>
            ✕
          </button>
        </div>

        <div className={styles.toolbar}>
          <label className={styles.toolGroup}>
            <input
              type="radio"
              name="mask-mode"
              checked={mode === 'paint'}
              onChange={() => setMode('paint')}
            />
            {m.brush}
          </label>
          <label className={styles.toolGroup}>
            <input
              type="radio"
              name="mask-mode"
              checked={mode === 'erase'}
              onChange={() => setMode('erase')}
            />
            {m.eraser}
          </label>
          <label className={styles.toolGroup}>
            {m.brushRadius}
            <input
              type="range"
              min={2}
              max={Math.max(20, Math.round(Math.min(baseAsset.width, baseAsset.height) / 4))}
              value={brushRadius}
              onChange={(e) => setBrushRadius(Number(e.target.value))}
            />
            <span className={styles.brushValue}>{brushRadius}px</span>
          </label>
          <button type="button" onClick={handleUndo} disabled={!canUndo}>
            {m.undo}
          </button>
          <button type="button" onClick={handleInvert}>
            {m.invert}
          </button>
          <button type="button" onClick={handleClear}>
            {m.clearAll}
          </button>
        </div>

        <div ref={containerRef} className={styles.canvasArea}>
          {isRestoring && <p className={styles.loading}>{m.loading}</p>}
          <div
            className={styles.stage}
            style={{ width: displayWidth, height: displayHeight }}
          >
            <img
              className={`${styles.backdrop} checkerboard`}
              src={previewUrl}
              alt=""
              style={{ width: displayWidth, height: displayHeight }}
              draggable={false}
            />
            <canvas
              ref={canvasRef}
              width={baseAsset.width}
              height={baseAsset.height}
              className={styles.canvas}
              style={{ width: displayWidth, height: displayHeight }}
              onPointerDown={handlePointerDown}
              onPointerMove={handlePointerMove}
              onPointerUp={endStroke}
              onPointerLeave={endStroke}
              onPointerCancel={endStroke}
            />
            <div ref={cursorRef} className={styles.brushCursor} />
          </div>
        </div>

        {existingMaskQuery.isError && (
          <p className={styles.note}>{m.existingMaskLoadError}</p>
        )}
        {saveError && <p className={styles.errorText}>{saveError}</p>}

        <div className={styles.footer}>
          <button type="button" className={styles.secondaryButton} onClick={onCancel}>
            {m.cancel}
          </button>
          <button
            type="button"
            className={styles.primaryButton}
            onClick={() => saveMutation.mutate()}
            disabled={saveMutation.isPending || isRestoring}
          >
            {saveMutation.isPending ? m.saving : m.save}
          </button>
        </div>
      </div>
    </div>
  )
}
