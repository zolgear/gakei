/**
 * マップの canvas に描くサムネイルの読み込みと保持(ADR-0004 の thumb を使う)。
 *
 * - 見えている範囲の、ある程度大きく描くものだけを読む(`want` で毎フレーム差し替える)。
 *   同時に読むのは数枚まで。
 * - 読んだら正方形に切り抜き(焦点があればその位置が入るように。ADR-0043。無ければ中央)、小さな ImageBitmap にして元の画像は捨てる(数千枚を
 *   512px のまま持つとメモリが足りない)。古いものから捨てる(LRU)。
 * - タブを切り替えても読み直さないよう、ページの中で1つを共有する。
 */
import { assetUrl } from '../../api/assetUrl'
import type { FocalPointValue } from '../../lib/focalPoint'

/** 保持するサムネイルの一辺(デバイスピクセル)。 */
export const THUMB_TILE_PX = 96
const MAX_ENTRIES = 1500
const CONCURRENCY = 6

type Listener = () => void

/** 読みたいサムネイル。焦点(ADR-0043)があれば切り抜く位置に使う。 */
export type ThumbRequest = { id: string; focal?: FocalPointValue | null }

class ThumbCache {
  private bitmaps = new Map<string, ImageBitmap | HTMLCanvasElement>()
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
