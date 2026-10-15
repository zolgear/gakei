import { describe, expect, it } from 'vitest'
import { bilinear, invBilinear } from './bilinear'
import { rectQuad, rotateQuad, type Quad } from './quad'

const square: Quad = rectQuad(500, 500, 400, 400)
const rect: Quad = rectQuad(1920, 1080, 3000, 800)
const parallelogram: Quad = [
  { x: 100, y: 100 },
  { x: 600, y: 100 },
  { x: 800, y: 500 },
  { x: 300, y: 500 },
]
/** 上の辺が短い台形(上下の辺は平行。2次の分岐を通る)。 */
const trapezoid: Quad = [
  { x: 300, y: 100 },
  { x: 700, y: 100 },
  { x: 950, y: 900 },
  { x: 50, y: 900 },
]
/** 左の辺が短い台形(左右の辺は平行。1次の分岐を通る)。 */
const sideTrapezoid: Quad = [
  { x: 100, y: 300 },
  { x: 900, y: 50 },
  { x: 900, y: 950 },
  { x: 100, y: 700 },
]
/** どの辺も平行でない凸の四角形。 */
const irregular: Quad = [
  { x: 120, y: 80 },
  { x: 880, y: 160 },
  { x: 760, y: 940 },
  { x: 60, y: 700 },
]

const shapes: [string, Quad][] = [
  ['正方形', square],
  ['長方形', rect],
  ['平行四辺形', parallelogram],
  ['台形(上下が平行)', trapezoid],
  ['台形(左右が平行)', sideTrapezoid],
  ['不規則な四角形', irregular],
  ['回転した正方形', rotateQuad(square, { x: 500, y: 500 }, Math.PI / 6)],
  ['90° 回転した長方形', rotateQuad(rect, { x: 1920, y: 1080 }, Math.PI / 2)],
  ['回転した台形', rotateQuad(sideTrapezoid, { x: 500, y: 500 }, -2.2)],
  ['回転した不規則な四角形', rotateQuad(irregular, { x: 0, y: 0 }, 1.1)],
]

const samples: [number, number][] = []
for (let i = 0; i <= 8; i++) {
  for (let j = 0; j <= 8; j++) samples.push([i / 8, j / 8])
}

describe('bilinear(順の写像)', () => {
  it('単位正方形の角を四隅へ写す', () => {
    const units: [number, number][] = [
      [0, 0],
      [1, 0],
      [1, 1],
      [0, 1],
    ]
    units.forEach(([u, v], i) => {
      const p = bilinear(irregular, u, v)
      expect(p.x).toBeCloseTo(irregular[i].x, 9)
      expect(p.y).toBeCloseTo(irregular[i].y, 9)
    })
  })
})

describe('invBilinear(逆の写像)', () => {
  for (const [name, q] of shapes) {
    it(`順の写像 → 逆の写像で元の (u, v) に戻る(${name})`, () => {
      for (const [u, v] of samples) {
        const uv = invBilinear(q, bilinear(q, u, v))
        expect(uv).not.toBeNull()
        expect(uv!.u).toBeCloseTo(u, 6)
        expect(uv!.v).toBeCloseTo(v, 6)
      }
    })
  }

  it('潰れた四角形は null', () => {
    const p = { x: 10, y: 10 }
    expect(invBilinear([p, p, p, p], p)).toBeNull()
  })
})

describe('台形でも面のまま(射影のように奥行きが付かない)', () => {
  it('辺の上の点が均等に並ぶ', () => {
    // 下の辺(長い辺)と右の辺の上を等分した点は、逆の写像でも等分になる。
    for (let i = 0; i <= 4; i++) {
      const t = i / 4
      const bottom = {
        x: trapezoid[3].x + (trapezoid[2].x - trapezoid[3].x) * t,
        y: trapezoid[3].y + (trapezoid[2].y - trapezoid[3].y) * t,
      }
      const right = {
        x: trapezoid[1].x + (trapezoid[2].x - trapezoid[1].x) * t,
        y: trapezoid[1].y + (trapezoid[2].y - trapezoid[1].y) * t,
      }
      const a = invBilinear(trapezoid, bottom)!
      expect(a.u).toBeCloseTo(t, 9)
      expect(a.v).toBeCloseTo(1, 9)
      const b = invBilinear(trapezoid, right)!
      expect(b.u).toBeCloseTo(1, 9)
      expect(b.v).toBeCloseTo(t, 9)
    }
  })

  it('縦の中央の線は、左右の辺の中点を結ぶ線(短い辺の側へ寄らない)', () => {
    const mid = bilinear(trapezoid, 0.5, 0.5)
    // 上下の辺の高さのちょうど真ん中(射影なら対角線の交点で、短い上の辺の側へ寄る)。
    expect(mid.y).toBeCloseTo((100 + 900) / 2, 9)
    // 等間隔の v は、縦にも等間隔に並ぶ。
    const ys = [0, 0.25, 0.5, 0.75, 1].map((v) => bilinear(trapezoid, 0.5, v).y)
    for (let i = 1; i < ys.length; i++) expect(ys[i] - ys[i - 1]).toBeCloseTo(200, 9)
  })

  it('左右の辺の高さが違う台形でも、横に等間隔の u は横に等間隔に並ぶ', () => {
    const xs = [0, 0.25, 0.5, 0.75, 1].map((u) => bilinear(sideTrapezoid, u, 0.5).x)
    for (let i = 1; i < xs.length; i++) expect(xs[i] - xs[i - 1]).toBeCloseTo(200, 9)
    const uv = invBilinear(sideTrapezoid, { x: 500, y: 500 })!
    expect(uv.u).toBeCloseTo(0.5, 9)
    expect(uv.v).toBeCloseTo(0.5, 9)
  })
})
