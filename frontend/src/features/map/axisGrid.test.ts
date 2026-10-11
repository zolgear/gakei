/** 座標のライン(`axisGrid.ts`)のテスト。 */
import { describe, expect, it } from 'vitest'
import { AXIS_GRID_MAX_TICKS, axisGridLines, niceStep } from './axisGrid'
import type { Bounds3 } from './camera3d'

function bounds(minX: number, minY: number, maxX: number, maxY: number): Bounds3 {
  return { min: [minX, minY, -1], max: [maxX, maxY, 1] }
}

describe('niceStep', () => {
  it('1・2・5 × 10 の冪に丸める', () => {
    expect(niceStep(80, 8)).toBe(10)
    expect(niceStep(8, 8)).toBe(1)
    expect(niceStep(0.8, 8)).toBeCloseTo(0.1, 10)
    expect(niceStep(16, 8)).toBe(2)
    expect(niceStep(40, 8)).toBe(5)
  })

  it('広がりが大きいほど間隔も大きい', () => {
    expect(niceStep(1000)).toBeGreaterThan(niceStep(10))
  })

  it('広がりが 0・有限でないときは 1', () => {
    expect(niceStep(0)).toBe(1)
    expect(niceStep(-5)).toBe(1)
    expect(niceStep(Number.NaN)).toBe(1)
  })
})

describe('axisGridLines', () => {
  it('点が無ければ null', () => {
    expect(axisGridLines(null)).toBeNull()
  })

  it('原点を通る 3 本の軸と、その平面の格子を返す', () => {
    const grid = axisGridLines(bounds(-10, -10, 10, 10))!
    expect(grid.axes).toHaveLength(18)
    // X 軸は y = z = 0、Y 軸は x = z = 0。
    expect([grid.axes[1], grid.axes[2], grid.axes[4], grid.axes[5]]).toEqual([0, 0, 0, 0])
    expect([grid.axes[6], grid.axes[8], grid.axes[9], grid.axes[11]]).toEqual([0, 0, 0, 0])
    // Z 軸は x = y = 0 で、原点を挟んで伸びる。
    expect([grid.axes[12], grid.axes[13], grid.axes[15], grid.axes[16]]).toEqual([0, 0, 0, 0])
    expect(grid.axes[14]).toBeLessThan(0)
    expect(grid.axes[17]).toBeGreaterThan(0)
    expect(grid.grid.length % 6).toBe(0)
    expect(grid.grid.length).toBeGreaterThan(0)
    // 格子の線は z = 0 の平面にある。
    for (let i = 0; i < grid.grid.length; i += 3) expect(grid.grid[i + 2]).toBe(0)
  })

  it('格子は軸と重ならない(x = 0・y = 0 の線は軸が受け持つ)', () => {
    const grid = axisGridLines(bounds(-10, -10, 10, 10))!
    for (let i = 0; i + 5 < grid.grid.length; i += 6) {
      const sameX = grid.grid[i] === grid.grid[i + 3]
      const value = sameX ? grid.grid[i] : grid.grid[i + 1]
      expect(value).not.toBe(0)
    }
  })

  it('広がりが大きくても線の数は増えすぎない', () => {
    for (const span of [1, 10, 1000, 1e6]) {
      const grid = axisGridLines(bounds(-span, -span, span, span))!
      const lines = grid.grid.length / 6
      // 2 方向ぶんなので上限の 2 倍まで。
      expect(lines).toBeLessThanOrEqual(AXIS_GRID_MAX_TICKS * 2)
      expect(grid.step).toBeGreaterThan(0)
    }
  })

  it('原点がデータの外にあっても、範囲に原点を含める', () => {
    const grid = axisGridLines(bounds(100, 100, 120, 120))!
    // X 軸は原点から data の端まで伸びる。
    expect(Math.min(grid.axes[0], grid.axes[3])).toBeLessThanOrEqual(0)
    expect(Math.max(grid.axes[0], grid.axes[3])).toBeGreaterThanOrEqual(120)
  })
})
