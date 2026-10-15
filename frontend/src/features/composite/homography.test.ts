import { describe, expect, it } from 'vitest'
import { applyMat3, invertMat3, squareToQuad, toColumnMajor } from './homography'
import { rectQuad, rotateQuad, type Quad } from './quad'

const trapezoid: Quad = [
  { x: 300, y: 100 },
  { x: 700, y: 100 },
  { x: 950, y: 900 },
  { x: 50, y: 900 },
]

describe('squareToQuad', () => {
  it('単位正方形の角を四隅へ写す(台形)', () => {
    const m = squareToQuad(trapezoid)
    const units = [
      { x: 0, y: 0 },
      { x: 1, y: 0 },
      { x: 1, y: 1 },
      { x: 0, y: 1 },
    ]
    units.forEach((u, i) => {
      const p = applyMat3(m, u)
      expect(p.x).toBeCloseTo(trapezoid[i].x, 6)
      expect(p.y).toBeCloseTo(trapezoid[i].y, 6)
    })
  })

  it('射影なので、正方形の中心は対角線の交点へ写る(アフィンの平均とは違う)', () => {
    const q: Quad = [
      { x: 0, y: 0 },
      { x: 100, y: 0 },
      { x: 200, y: 200 },
      { x: 0, y: 100 },
    ]
    const p = applyMat3(squareToQuad(q), { x: 0.5, y: 0.5 })
    // 対角線 (0,0)-(200,200) と (100,0)-(0,100) の交点は (50, 50)。
    expect(p.x).toBeCloseTo(50, 6)
    expect(p.y).toBeCloseTo(50, 6)
  })

  it('直線は直線へ写る(上の辺の中点は上の辺の上)', () => {
    const m = squareToQuad(trapezoid)
    const p = applyMat3(m, { x: 0.25, y: 0 })
    expect(p.y).toBeCloseTo(100, 6)
  })

  it('長方形・回転した長方形(アフィン)でも正しい', () => {
    const q = rotateQuad(rectQuad(10, 20, 40, 30), { x: 10, y: 20 }, 0.3)
    const m = squareToQuad(q)
    expect(m[6]).toBeCloseTo(0)
    expect(m[7]).toBeCloseTo(0)
    const p = applyMat3(m, { x: 1, y: 1 })
    expect(p.x).toBeCloseTo(q[2].x, 6)
    expect(p.y).toBeCloseTo(q[2].y, 6)
  })
})

describe('invertMat3', () => {
  it('逆行列でキャンバスの点 → 単位正方形に戻る', () => {
    const m = squareToQuad(trapezoid)
    const inv = invertMat3(m)
    expect(inv).not.toBeNull()
    const uv = { x: 0.3, y: 0.8 }
    const back = applyMat3(inv!, applyMat3(m, uv))
    expect(back.x).toBeCloseTo(0.3, 6)
    expect(back.y).toBeCloseTo(0.8, 6)
  })

  it('特異な行列は null', () => {
    expect(invertMat3([0, 0, 0, 0, 0, 0, 0, 0, 0])).toBeNull()
  })
})

describe('toColumnMajor', () => {
  it('列優先に並べ替える', () => {
    expect(Array.from(toColumnMajor([1, 2, 3, 4, 5, 6, 7, 8, 9]))).toEqual([1, 4, 7, 2, 5, 8, 3, 6, 9])
  })
})
