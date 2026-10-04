/**
 * マップの canvas の座標変換(画面 ↔ 配置の座標)。画面の座標 = 配置の座標 × scale + (x, y)。
 * 画面の座標は CSS ピクセル(デバイスピクセル比は描くときに掛ける)。
 */

export interface Viewport {
  x: number
  y: number
  scale: number
}

export interface Bounds {
  minX: number
  minY: number
  maxX: number
  maxY: number
}

export const MIN_SCALE = 1e-4
export const MAX_SCALE = 1e5

export function worldToScreen(v: Viewport, wx: number, wy: number): [number, number] {
  return [wx * v.scale + v.x, wy * v.scale + v.y]
}

export function screenToWorld(v: Viewport, sx: number, sy: number): [number, number] {
  return [(sx - v.x) / v.scale, (sy - v.y) / v.scale]
}

/** 画面の点 (sx, sy) を動かさずに factor 倍する。倍率は [minScale, maxScale] に収める。 */
export function zoomAt(
  v: Viewport,
  sx: number,
  sy: number,
  factor: number,
  minScale: number = MIN_SCALE,
  maxScale: number = MAX_SCALE,
): Viewport {
  const scale = Math.min(maxScale, Math.max(minScale, v.scale * factor))
  const [wx, wy] = screenToWorld(v, sx, sy)
  return { scale, x: sx - wx * scale, y: sy - wy * scale }
}

export function panBy(v: Viewport, dx: number, dy: number): Viewport {
  return { ...v, x: v.x + dx, y: v.y + dy }
}

/** 点列(x0, y0, x1, y1, ...)の外接矩形。点が無いか、有限でない値しか無ければ null。 */
export function computeBounds(positions: ArrayLike<number>, count: number = positions.length / 2): Bounds | null {
  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity
  for (let i = 0; i < count; i++) {
    const x = positions[i * 2]
    const y = positions[i * 2 + 1]
    if (!Number.isFinite(x) || !Number.isFinite(y)) continue
    if (x < minX) minX = x
    if (x > maxX) maxX = x
    if (y < minY) minY = y
    if (y > maxY) maxY = y
  }
  if (minX === Infinity) return null
  return { minX, minY, maxX, maxY }
}

/** 外接矩形が width × height の画面に余白 padding を残して収まる表示。 */
export function fitBounds(bounds: Bounds, width: number, height: number, padding: number): Viewport {
  const w = Math.max(bounds.maxX - bounds.minX, 1e-9)
  const h = Math.max(bounds.maxY - bounds.minY, 1e-9)
  const availW = Math.max(width - padding * 2, 1)
  const availH = Math.max(height - padding * 2, 1)
  let scale = Math.min(availW / w, availH / h)
  // 1点だけ・全点が同じ位置のときは倍率が発散するので、適当な値に抑える。
  if (!Number.isFinite(scale) || scale > 1e6) scale = 1
  scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale))
  const cx = (bounds.minX + bounds.maxX) / 2
  const cy = (bounds.minY + bounds.maxY) / 2
  return { scale, x: width / 2 - cx * scale, y: height / 2 - cy * scale }
}

/**
 * サムネイル1枚の、配置の座標での一辺。
 *
 * 点の平均の間隔(外接矩形の面積 / 点の数 の平方根)の 0.9 倍を上限にする。近傍の表(`neighborIndices`)
 * を渡すと、配置の上でいちばん近い近傍までの距離の中央値も見て、塊が詰まっているときは小さくする
 * (ただし平均の間隔の 0.35 倍より小さくはしない。全体を表示したときに点ばかりにならないように)。
 */
export function tileWorldSize(
  bounds: Bounds | null,
  count: number,
  positions?: ArrayLike<number>,
  neighborIndices?: number[][],
): number {
  if (!bounds || count <= 0) return 1
  const w = bounds.maxX - bounds.minX
  const h = bounds.maxY - bounds.minY
  const area = Math.max(w * h, 1e-12)
  const spacing = Math.sqrt(area / count)
  let size = spacing * 0.9
  if (positions && neighborIndices && neighborIndices.length >= count) {
    const nearest: number[] = []
    for (let i = 0; i < count; i++) {
      const row = neighborIndices[i]
      const j = row?.find((k) => k !== i && k >= 0 && k < count)
      if (j === undefined) continue
      const d = Math.hypot(positions[i * 2] - positions[j * 2], positions[i * 2 + 1] - positions[j * 2 + 1])
      if (Number.isFinite(d)) nearest.push(d)
    }
    if (nearest.length > 0) {
      nearest.sort((a, b) => a - b)
      const median = nearest[Math.floor(nearest.length / 2)]
      size = Math.min(size, Math.max(median * 1.2, spacing * 0.35))
    }
  }
  return Number.isFinite(size) && size > 0 ? size : 1
}
