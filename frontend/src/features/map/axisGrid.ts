/**
 * 立体の見せ方に重ねる「座標のライン」(純関数のみ。試作)。
 *
 * 向きと距離感が分かるように、原点を通る X 軸・Y 軸の線と、その平面(z = 0)上の薄い格子の
 * 座標を作る。格子の間隔はデータの広がりから決め、線の数が増えすぎないように丸めた値
 * (1・2・5 × 10 の冪)にする。
 *
 * 返すのは配置の座標のままの線分(x0, y0, z0, x1, y1, z1 の 6 要素で 1 本)。画面の座標への
 * 変換は投影(`projection.ts`)に任せるので、ここは時刻も乱数も使わない純粋な計算になる。
 */
import type { Bounds3 } from './camera3d'

/** 格子の目標の分割数(広がりをだいたいこの数に分ける)。 */
export const AXIS_GRID_DIVISIONS = 8
/** 1 方向の線の数の上限(これを超えるなら間隔を倍にする)。 */
export const AXIS_GRID_MAX_TICKS = 21

export interface AxisGrid {
  /** 格子の間隔(配置の座標)。 */
  step: number
  /** X 軸・Y 軸(2 本)。 */
  axes: Float32Array
  /** 格子の線(軸と重なる線は含まない)。 */
  grid: Float32Array
}

/**
 * 広がり `span` を `divisions` 個くらいに分ける、丸めた間隔(1・2・5 × 10 の冪)。
 * 広がりが 0 や有限でないときは 1。
 */
export function niceStep(span: number, divisions: number = AXIS_GRID_DIVISIONS): number {
  if (!Number.isFinite(span) || !(span > 0)) return 1
  const raw = span / Math.max(1, divisions)
  const magnitude = Math.pow(10, Math.floor(Math.log10(raw)))
  const normalized = raw / magnitude
  const multiple = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10
  return multiple * magnitude
}

/**
 * 座標のラインの線分。`bounds` が無ければ null。
 *
 * 範囲は、データの外接直方体に原点を加えたものを間隔の倍数に丸めて使う(原点が必ず入るので、
 * 軸は格子の中を通る)。線の数が上限を超えるときは、収まるまで間隔を倍にする。
 */
export function axisGridLines(
  bounds: Bounds3 | null,
  divisions: number = AXIS_GRID_DIVISIONS,
  maxTicks: number = AXIS_GRID_MAX_TICKS,
): AxisGrid | null {
  if (!bounds) return null
  // 原点を含めた範囲。
  const minX = Math.min(bounds.min[0], 0)
  const maxX = Math.max(bounds.max[0], 0)
  const minY = Math.min(bounds.min[1], 0)
  const maxY = Math.max(bounds.max[1], 0)
  if (![minX, maxX, minY, maxY].every(Number.isFinite)) return null

  let step = niceStep(Math.max(maxX - minX, maxY - minY), divisions)
  const ticksFor = (lo: number, hi: number, s: number) =>
    Math.floor(hi / s) - Math.ceil(lo / s) + 1
  for (let guard = 0; guard < 40; guard++) {
    const count = Math.max(ticksFor(minX, maxX, step), ticksFor(minY, maxY, step))
    if (count <= maxTicks) break
    step *= 2
  }

  // 線を引く範囲は、間隔の倍数まで広げる(格子の端が揃う)。
  const x0 = Math.floor(minX / step) * step
  const x1 = Math.ceil(maxX / step) * step
  const y0 = Math.floor(minY / step) * step
  const y1 = Math.ceil(maxY / step) * step

  const axes = new Float32Array([x0, 0, 0, x1, 0, 0, 0, y0, 0, 0, y1, 0])
  const grid: number[] = []
  // Y 軸に平行な線(x = 一定)。x = 0 は軸そのものなので飛ばす。
  for (let i = Math.ceil(x0 / step); i <= Math.round(x1 / step); i++) {
    if (i === 0) continue
    const x = i * step
    grid.push(x, y0, 0, x, y1, 0)
  }
  // X 軸に平行な線(y = 一定)。
  for (let i = Math.ceil(y0 / step); i <= Math.round(y1 / step); i++) {
    if (i === 0) continue
    const y = i * step
    grid.push(x0, y, 0, x1, y, 0)
  }
  return { step, axes, grid: new Float32Array(grid) }
}
