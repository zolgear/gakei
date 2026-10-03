import { describe, expect, it } from 'vitest'
import { mulberry32 } from './random'
import {
  FIT_PADDING,
  SMALL_SET_MIN_PX,
  THUMB_MAX_PX,
  arrowSizePx,
  drawTilePx,
  fitNodes,
  lineageSegment,
  nodeExtentPx,
  squareBorderDistance,
} from './nodeSize'
import { computeBounds, tileWorldSize, worldToScreen } from './viewport'

function randomPositions(n: number, seed: number, sx = 10, sy = 6): Float32Array {
  const rand = mulberry32(seed)
  const out = new Float32Array(n * 2)
  for (let i = 0; i < n; i++) {
    out[i * 2] = (rand() - 0.5) * sx
    out[i * 2 + 1] = (rand() - 0.5) * sy
  }
  return out
}

/** 全体を表示したあと、全ノードの描いた矩形(選択の枠を含む)が画面の内側にあるか。 */
function expectAllInside(positions: Float32Array, width: number, height: number, tileScale = 1) {
  const n = positions.length / 2
  const bounds = computeBounds(positions, n)!
  const tileWorld = tileWorldSize(bounds, n) * tileScale
  const v = fitNodes(bounds, width, height, tileWorld, n)
  const ext = nodeExtentPx(tileWorld, v.scale, n)
  let touchesEdge = false
  for (let i = 0; i < n; i++) {
    const [x, y] = worldToScreen(v, positions[i * 2], positions[i * 2 + 1])
    expect(x - ext).toBeGreaterThanOrEqual(FIT_PADDING - 1e-3)
    expect(y - ext).toBeGreaterThanOrEqual(FIT_PADDING - 1e-3)
    expect(x + ext).toBeLessThanOrEqual(width - FIT_PADDING + 1e-3)
    expect(y + ext).toBeLessThanOrEqual(height - FIT_PADDING + 1e-3)
    if (Math.min(x - ext, width - x - ext, y - ext, height - y - ext) < FIT_PADDING + 1) touchesEdge = true
  }
  // 余計に縮めていない(どこかの辺はぴったり余白まで使う)。
  expect(touchesEdge).toBe(true)
  return { v, ext }
}

describe('drawTilePx と nodeExtentPx', () => {
  it('上限と、少ない枚数のときの下限', () => {
    expect(drawTilePx(500, 1000)).toBe(THUMB_MAX_PX)
    expect(drawTilePx(5, 1000)).toBe(5)
    expect(drawTilePx(5, 20)).toBe(SMALL_SET_MIN_PX)
  })

  it('サムネイルは半分と枠、点は輪の分', () => {
    expect(nodeExtentPx(1, 40, 1000)).toBe(24)
    expect(nodeExtentPx(1, 4, 1000)).toBeLessThan(10)
  })
})

describe('fitNodes', () => {
  const sizes: [number, number][] = [
    [1440, 900],
    [1200, 640],
    [390, 540],
    [390, 844],
    [200, 160],
  ]
  for (const [w, h] of sizes) {
    for (const n of [3, 20, 60, 620, 3000]) {
      it(`${w}×${h}、${n}枚で端の画像が欠けない`, () => {
        expectAllInside(randomPositions(n, n + w), w, h)
      })
    }
    it(`${w}×${h}、ネットワークの小さめのタイルでも欠けない`, () => {
      expectAllInside(randomPositions(150, 7, 400, 300), w, h, 0.55)
    })
  }

  it('横に細長い配置(縦の幅がほぼ 0)でも、縦にはみ出さない', () => {
    const positions = new Float32Array([0, 0, 10, 0.001, 5, 0])
    expectAllInside(positions, 390, 540)
  })

  it('1点だけでも発散しない', () => {
    const v = fitNodes({ minX: 1, minY: 1, maxX: 1, maxY: 1 }, 390, 540, 1, 1)
    expect(Number.isFinite(v.scale)).toBe(true)
    expect(worldToScreen(v, 1, 1)).toEqual([195, 270])
  })
})

describe('lineageSegment', () => {
  it('右向きの辺は、親の縁から子の縁の手前まで引き、矢じりの先は子の縁の外', () => {
    const seg = lineageSegment(0, 0, 100, 0, 10, 8, 3)!
    expect(seg.x0).toBeCloseTo(13)
    expect(seg.tipX).toBeCloseTo(87)
    expect(seg.x1).toBeCloseTo(79)
    expect(seg.y0).toBeCloseTo(0)
  })

  it('斜めの辺は正方形の縁(角の方向は遠い)で止める', () => {
    const seg = lineageSegment(0, 0, 100, 100, 10, 8, 0)!
    // 45度では縁までの距離は half × √2。
    expect(Math.hypot(100 - seg.tipX, 100 - seg.tipY)).toBeCloseTo(10 * Math.SQRT2)
    expect(squareBorderDistance(10, 1, 0)).toBe(10)
  })

  it('サムネイルが重なるほど近いときは引かない', () => {
    expect(lineageSegment(0, 0, 20, 0, 10, 8)).toBeNull()
    expect(lineageSegment(5, 5, 5, 5, 10, 8)).toBeNull()
  })

  it('矢じりの長さはタイルに比例し、範囲に収める', () => {
    expect(arrowSizePx(12)).toBe(7)
    expect(arrowSizePx(50)).toBe(10)
    expect(arrowSizePx(112)).toBe(14)
  })
})
