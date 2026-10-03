/**
 * マップの canvas の当たり判定。画面の点 (sx, sy) から、半径 radius(CSS ピクセル、正方形で判定)
 * の内側にある最も近いノードを返す。無ければ -1。
 */
import { worldToScreen, type Viewport } from './viewport'

export function hitTest(
  positions: ArrayLike<number>,
  count: number,
  v: Viewport,
  sx: number,
  sy: number,
  radius: number,
): number {
  let best = -1
  let bestDist = Infinity
  for (let i = 0; i < count; i++) {
    const wx = positions[i * 2]
    const wy = positions[i * 2 + 1]
    if (!Number.isFinite(wx) || !Number.isFinite(wy)) continue
    const [px, py] = worldToScreen(v, wx, wy)
    const dx = px - sx
    const dy = py - sy
    if (Math.abs(dx) > radius || Math.abs(dy) > radius) continue
    const d = dx * dx + dy * dy
    if (d < bestDist) {
      bestDist = d
      best = i
    }
  }
  return best
}
