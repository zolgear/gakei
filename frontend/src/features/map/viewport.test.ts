import { describe, expect, it } from 'vitest'
import {
  computeBounds,
  fitBounds,
  panBy,
  screenToWorld,
  tileWorldSize,
  worldToScreen,
  zoomAt,
  type Viewport,
} from './viewport'

describe('座標変換', () => {
  const v: Viewport = { x: 100, y: 50, scale: 4 }

  it('画面 ↔ 配置の往復で元に戻る', () => {
    const [sx, sy] = worldToScreen(v, 3, -2)
    expect([sx, sy]).toEqual([112, 42])
    expect(screenToWorld(v, sx, sy)).toEqual([3, -2])
  })

  it('zoomAt は指定した画面の点を動かさない', () => {
    const before = screenToWorld(v, 200, 120)
    const next = zoomAt(v, 200, 120, 2.5)
    expect(next.scale).toBe(10)
    const after = screenToWorld(next, 200, 120)
    expect(after[0]).toBeCloseTo(before[0])
    expect(after[1]).toBeCloseTo(before[1])
  })

  it('zoomAt は倍率を範囲に収める', () => {
    expect(zoomAt(v, 0, 0, 1000, 0.1, 10).scale).toBe(10)
    expect(zoomAt(v, 0, 0, 0.0001, 0.1, 10).scale).toBe(0.1)
  })

  it('panBy は平行移動だけ', () => {
    expect(panBy(v, 5, -3)).toEqual({ x: 105, y: 47, scale: 4 })
  })
})

describe('computeBounds と fitBounds', () => {
  it('有限でない値を除いて外接矩形を求める', () => {
    expect(computeBounds(new Float32Array([0, 0, 2, 4, Number.NaN, 9, -1, 1]))).toEqual({
      minX: -1,
      minY: 0,
      maxX: 2,
      maxY: 4,
    })
    expect(computeBounds(new Float32Array([]))).toBeNull()
  })

  it('余白を残して中央に収める', () => {
    const v = fitBounds({ minX: 0, minY: 0, maxX: 10, maxY: 5 }, 220, 220, 10)
    expect(v.scale).toBe(20)
    expect(worldToScreen(v, 5, 2.5)).toEqual([110, 110])
    expect(worldToScreen(v, 0, 0)[0]).toBe(10)
  })

  it('全点が同じ位置でも発散しない', () => {
    const v = fitBounds({ minX: 3, minY: 3, maxX: 3, maxY: 3 }, 100, 100, 10)
    expect(Number.isFinite(v.scale)).toBe(true)
    expect(worldToScreen(v, 3, 3)).toEqual([50, 50])
  })
})

describe('tileWorldSize', () => {
  it('点の平均の間隔に比例する', () => {
    const a = tileWorldSize({ minX: 0, minY: 0, maxX: 10, maxY: 10 }, 100)
    const b = tileWorldSize({ minX: 0, minY: 0, maxX: 10, maxY: 10 }, 400)
    expect(a).toBeCloseTo(0.9)
    expect(b).toBeCloseTo(0.45)
    expect(tileWorldSize(null, 10)).toBe(1)
  })

  it('近傍が詰まっているときは小さくする(平均の間隔の 0.35 倍まで)', () => {
    const bounds = { minX: 0, minY: 0, maxX: 10, maxY: 10 }
    // 4点。0 と 1、2 と 3 がごく近い。
    const positions = [0, 0, 0.01, 0, 10, 10, 10, 9.99]
    const neighbors = [
      [0, 1],
      [1, 0],
      [2, 3],
      [3, 2],
    ]
    const spacing = Math.sqrt(100 / 4)
    expect(tileWorldSize(bounds, 4, positions, neighbors)).toBeCloseTo(spacing * 0.35)
    // 近傍が十分に離れていれば、平均の間隔の 0.9 倍のまま。
    const spread = [0, 0, 10, 0, 0, 10, 10, 10]
    expect(tileWorldSize(bounds, 4, spread, neighbors)).toBeCloseTo(spacing * 0.9)
  })
})
