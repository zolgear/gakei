/** マスクエディタの座標変換・ストローク補間。DOM に依存しない純粋関数。 */
export interface Point {
  x: number
  y: number
}

export interface Rect {
  left: number
  top: number
  width: number
  height: number
}

export interface CanvasSize {
  width: number
  height: number
}

/**
 * ポインター座標(clientX/Y、CSS ピクセル)を、Canvas の実解像度(実寸)の座標に変換する。
 * Canvas は CSS で縮小表示されている(表示サイズ ≠ 実寸)前提。
 */
export function pointerToCanvasCoords(
  clientX: number,
  clientY: number,
  rect: Rect,
  canvas: CanvasSize,
): Point {
  if (rect.width <= 0 || rect.height <= 0) {
    return { x: 0, y: 0 }
  }
  const scaleX = canvas.width / rect.width
  const scaleY = canvas.height / rect.height
  return {
    x: (clientX - rect.left) * scaleX,
    y: (clientY - rect.top) * scaleY,
  }
}

/**
 * from → to の間を、最大でも step 間隔になるように等分した点列を返す(to を含む、from は含まない)。
 * ポインター移動が速いときに、円を連続で置くだけだとストロークが途切れるのを防ぐ。
 */
export function interpolatePoints(from: Point, to: Point, step: number): Point[] {
  const dx = to.x - from.x
  const dy = to.y - from.y
  const distance = Math.hypot(dx, dy)
  if (distance === 0) return [to]

  const safeStep = step > 0 ? step : 1
  const stepsCount = Math.max(1, Math.ceil(distance / safeStep))
  const points: Point[] = []
  for (let i = 1; i <= stepsCount; i += 1) {
    const t = i / stepsCount
    points.push({ x: from.x + dx * t, y: from.y + dy * t })
  }
  return points
}
