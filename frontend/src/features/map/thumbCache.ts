/**
 * マップの canvas に描くサムネイルの読み込みと保持(ADR-0004 の thumb を使う)。
 *
 * - 見えている範囲の、ある程度大きく描くものだけを読む(`want` で毎フレーム差し替える)。
 *   同時に読むのは数枚まで。
 * - 読んだら正方形に切り抜き(焦点があればその位置が入るように。ADR-0043。無ければ中央)、小さな ImageBitmap にして元の画像は捨てる(数千枚を
 *   512px のまま持つとメモリが足りない)。古いものから捨てる(LRU)。
 * - タブを切り替えても読み直さないよう、ページの中で1つを共有する。
 * - マスクする画像(ADR-0049 5章「マップ」)には、読んだタイルからぼかしたタイルを作って描く
 *   (`getMasked`)。小さく縮めてから引き伸ばしてぼかし(`ctx.filter` は Safari が対応しないので
 *   使わない)、ブラウザのメモリにだけ置く。元のタイルを捨てるときに一緒に捨てる。
 */
import { assetUrl } from '../../api/assetUrl'
import type { FocalPointValue } from '../../lib/focalPoint'
import { BLUR_SAMPLE_PX } from './mapRatingMask'

/** 保持するサムネイルの一辺(デバイスピクセル)。 */
export const THUMB_TILE_PX = 96
/**
 * ぼかしたタイルの一辺。ぼけた絵は引き伸ばしても崩れないので小さく持つ(描くときに
 * `drawImage` が滑らかに広げる)。
 */
const BLURRED_TILE_PX = 32
/** ぼかしたタイルを少し暗くする(サムネイルのマスク `.mask` の背景と同じ)。 */
const BLURRED_SHADE = 'rgba(16, 17, 20, 0.28)'
const MAX_ENTRIES = 1500
const CONCURRENCY = 6

type Listener = () => void

/** 読みたいサムネイル。焦点(ADR-0043)があれば切り抜く位置に使う。 */
export type ThumbRequest = { id: string; focal?: FocalPointValue | null }

class ThumbCache {
  private bitmaps = new Map<string, ImageBitmap | HTMLCanvasElement>()
  private blurred = new Map<string, HTMLCanvasElement>()
  private inFlight = new Set<string>()
  private failed = new Set<string>()
  private queue: ThumbRequest[] = []
  private listeners = new Set<Listener>()

  get(id: string): ImageBitmap | HTMLCanvasElement | undefined {
    const value = this.bitmaps.get(id)
    if (value) {
      // 使ったものを後ろへ(LRU)。
      this.bitmaps.delete(id)
      this.bitmaps.set(id, value)
    }
    return value
  }

  /**
   * マスクする画像のための、ぼかしたタイル。元のタイルをまだ読んでいなければ undefined
   * (呼び出し側は `want` で元のタイルを読む)。
   */
  getMasked(id: string): HTMLCanvasElement | undefined {
    const tile = this.get(id)
    if (!tile) return undefined
    const cached = this.blurred.get(id)
    if (cached) return cached
    const made = toBlurredTile(tile)
    if (made) this.blurred.set(id, made)
    return made ?? undefined
  }

  /** 読みたいものを、優先する順に渡す(前の要求は捨てる)。 */
  want(requests: ThumbRequest[]): void {
    this.queue = requests.filter(
      ({ id }) => !this.bitmaps.has(id) && !this.inFlight.has(id) && !this.failed.has(id),
    )
    this.pump()
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener)
    return () => {
      this.listeners.delete(listener)
    }
  }

  private pump(): void {
    while (this.inFlight.size < CONCURRENCY && this.queue.length > 0) {
      const { id, focal } = this.queue.shift()!
      this.inFlight.add(id)
      void this.load(id, focal).finally(() => {
        this.inFlight.delete(id)
        this.pump()
      })
    }
  }

  private async load(id: string, focal: FocalPointValue | null | undefined): Promise<void> {
    try {
      const res = await fetch(assetUrl(id, 'thumb'))
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const blob = await res.blob()
      const tile = await toSquareTile(blob, focal)
      this.bitmaps.set(id, tile)
      while (this.bitmaps.size > MAX_ENTRIES) {
        const oldest = this.bitmaps.keys().next().value as string
        const value = this.bitmaps.get(oldest)
        if (value && 'close' in value) value.close()
        this.bitmaps.delete(oldest)
        this.blurred.delete(oldest)
      }
      for (const listener of this.listeners) listener()
    } catch {
      this.failed.add(id)
    }
  }
}

/**
 * 正方形に切り抜く左上の位置。CSS の `object-position: x% y%` と同じく、余る幅・高さに
 * 焦点の割合を掛ける(焦点が無ければ 0.5 = 中央)。
 */
function cropOrigin(width: number, height: number, side: number, focal: FocalPointValue | null | undefined) {
  const ratio = (v: number | undefined) => (v !== undefined && Number.isFinite(v) ? Math.min(1, Math.max(0, v)) : 0.5)
  return {
    sx: Math.floor((width - side) * ratio(focal?.x)),
    sy: Math.floor((height - side) * ratio(focal?.y)),
  }
}

async function toSquareTile(
  blob: Blob,
  focal: FocalPointValue | null | undefined,
): Promise<ImageBitmap | HTMLCanvasElement> {
  if (typeof createImageBitmap === 'function') {
    const full = await createImageBitmap(blob)
    try {
      const side = Math.min(full.width, full.height)
      const { sx, sy } = cropOrigin(full.width, full.height, side, focal)
      try {
        return await createImageBitmap(full, sx, sy, side, side, {
          resizeWidth: THUMB_TILE_PX,
          resizeHeight: THUMB_TILE_PX,
          resizeQuality: 'medium',
        })
      } catch {
        // resize の指定に対応しないブラウザは canvas で縮める。
        return drawToCanvas(full, sx, sy, side)
      }
    } finally {
      full.close()
    }
  }
  const url = URL.createObjectURL(blob)
  try {
    const img = new Image()
    img.src = url
    await img.decode()
    const side = Math.min(img.naturalWidth, img.naturalHeight)
    const { sx, sy } = cropOrigin(img.naturalWidth, img.naturalHeight, side, focal)
    return drawToCanvas(img, sx, sy, side)
  } finally {
    URL.revokeObjectURL(url)
  }
}

function drawToCanvas(source: CanvasImageSource, sx: number, sy: number, side: number): HTMLCanvasElement {
  const canvas = document.createElement('canvas')
  canvas.width = THUMB_TILE_PX
  canvas.height = THUMB_TILE_PX
  const ctx = canvas.getContext('2d')
  ctx?.drawImage(source, sx, sy, side, side, 0, 0, THUMB_TILE_PX, THUMB_TILE_PX)
  return canvas
}

export const thumbCache = new ThumbCache()

/**
 * ぼかしたタイルを作る。`BLUR_SAMPLE_PX` まで縮めてから、2段で引き伸ばす(一度に広げると格子が
 * 見えやすい)。canvas が使えなければ null。
 */
function toBlurredTile(source: CanvasImageSource): HTMLCanvasElement | null {
  const step = (from: CanvasImageSource, side: number): HTMLCanvasElement | null => {
    const canvas = document.createElement('canvas')
    canvas.width = side
    canvas.height = side
    const ctx = canvas.getContext('2d')
    if (!ctx) return null
    ctx.imageSmoothingEnabled = true
    ctx.imageSmoothingQuality = 'high'
    ctx.drawImage(from, 0, 0, side, side)
    return canvas
  }
  const small = step(source, BLUR_SAMPLE_PX)
  const mid = small && step(small, Math.round((BLUR_SAMPLE_PX + BLURRED_TILE_PX) / 2))
  const out = mid && step(mid, BLURRED_TILE_PX)
  const ctx = out?.getContext('2d')
  if (!out || !ctx) return null
  ctx.fillStyle = BLURRED_SHADE
  ctx.fillRect(0, 0, BLURRED_TILE_PX, BLURRED_TILE_PX)
  return out
}
