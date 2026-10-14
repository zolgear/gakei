import { describe, expect, it } from 'vitest'
import {
  applyNumericTransform,
  centerQuadOnCanvas,
  edgeLengths,
  fitQuadToCanvas,
  hitCorner,
  initialLayout,
  isValidQuad,
  moveCornerClamped,
  normalizeDegrees,
  pinchQuad,
  pointInQuad,
  quadAngleDegrees,
  quadBounds,
  quadCenter,
  quadScalePercent,
  rectQuad,
  rotateHandlePos,
  rotateQuad,
  scaleFromCornerDrag,
  snapAngle,
  translateQuad,
  type Quad,
} from './quad'

function close(q: Quad, expected: [number, number][], digits = 6) {
  q.forEach((p, i) => {
    expect(p.x).toBeCloseTo(expected[i][0], digits)
    expect(p.y).toBeCloseTo(expected[i][1], digits)
  })
}

describe('基本の変換', () => {
  it('rectQuad は左上・右上・右下・左下の順', () => {
    close(rectQuad(50, 40, 20, 10), [
      [40, 35],
      [60, 35],
      [60, 45],
      [40, 45],
    ])
  })

  it('移動と中心', () => {
    const q = translateQuad(rectQuad(0, 0, 10, 10), 5, -3)
    expect(quadCenter(q)).toEqual({ x: 5, y: -3 })
  })

  it('90° 回転すると上の辺が下向きになる(時計回り)', () => {
    const q = rotateQuad(rectQuad(0, 0, 10, 10), { x: 0, y: 0 }, Math.PI / 2)
    expect(quadAngleDegrees(q)).toBeCloseTo(90)
    close(q, [
      [5, -5],
      [5, 5],
      [-5, 5],
      [-5, -5],
    ])
  })

  it('15° 刻みに丸める', () => {
    expect((snapAngle((22 * Math.PI) / 180) * 180) / Math.PI).toBeCloseTo(15)
    expect((snapAngle((23 * Math.PI) / 180) * 180) / Math.PI).toBeCloseTo(30)
  })

  it('角度を -180〜180 に収める', () => {
    expect(normalizeDegrees(190)).toBe(-170)
    expect(normalizeDegrees(-190)).toBe(170)
    expect(normalizeDegrees(360)).toBe(0)
    expect(normalizeDegrees(180)).toBe(180)
  })
})

describe('isValidQuad', () => {
  it('長方形と台形は凸', () => {
    expect(isValidQuad(rectQuad(0, 0, 100, 50))).toBe(true)
    expect(
      isValidQuad([
        { x: 30, y: 0 },
        { x: 70, y: 0 },
        { x: 100, y: 100 },
        { x: 0, y: 100 },
      ]),
    ).toBe(true)
  })

  it('凹んだ形・ねじれた形・潰れた形は不正', () => {
    // 凹み(右下の角を内側へ)。
    expect(
      isValidQuad([
        { x: 0, y: 0 },
        { x: 100, y: 0 },
        { x: 30, y: 30 },
        { x: 0, y: 100 },
      ]),
    ).toBe(false)
    // ねじれ(右上と右下を入れ替え)。
    expect(
      isValidQuad([
        { x: 0, y: 0 },
        { x: 100, y: 100 },
        { x: 100, y: 0 },
        { x: 0, y: 100 },
      ]),
    ).toBe(false)
    // 面積 0。
    expect(
      isValidQuad([
        { x: 0, y: 0 },
        { x: 10, y: 0 },
        { x: 20, y: 0 },
        { x: 30, y: 0 },
      ]),
    ).toBe(false)
    expect(isValidQuad(rectQuad(0, 0, 2, 2))).toBe(false)
  })
})

describe('moveCornerClamped(変形)', () => {
  const square = rectQuad(50, 50, 100, 100)

  it('凸のままなら、そのまま動かす', () => {
    const q = moveCornerClamped(square, 0, { x: 20, y: 10 })
    expect(q[0]).toEqual({ x: 20, y: 10 })
    expect(isValidQuad(q)).toBe(true)
  })

  it('凹む位置へは動かさず、凸を保てる手前で止める', () => {
    // 左上の角を右下の角の向こうへ動かそうとする。
    const q = moveCornerClamped(square, 0, { x: 150, y: 150 })
    expect(isValidQuad(q)).toBe(true)
    // 対角線(右上 - 左下)の手前で止まる: x + y < 100。
    expect(q[0].x + q[0].y).toBeLessThan(100)
    expect(q[0].x + q[0].y).toBeGreaterThan(95)
  })

  it('ねじれる位置へも動かさない', () => {
    // 右上を左上より左へ。
    const q = moveCornerClamped(square, 1, { x: -50, y: 0 })
    expect(isValidQuad(q)).toBe(true)
    expect(q[1].x).toBeGreaterThan(0)
  })
})

describe('拡大縮小と回転のハンドル', () => {
  it('角を外へ引くと中心のまま縦横比を保って大きくなる', () => {
    const start = rectQuad(0, 0, 100, 50)
    const q = scaleFromCornerDrag(start, start[2], { x: 100, y: 50 })
    expect(quadCenter(q).x).toBeCloseTo(0)
    expect(quadCenter(q).y).toBeCloseTo(0)
    const [top, right] = edgeLengths(q)
    expect(top).toBeCloseTo(200)
    expect(right).toBeCloseTo(100)
  })

  it('小さくしすぎない', () => {
    const start = rectQuad(0, 0, 100, 50)
    const q = scaleFromCornerDrag(start, start[2], { x: 0, y: 0 })
    expect(isValidQuad(q)).toBe(true)
  })

  it('回転のハンドルは上の辺の外側', () => {
    const p = rotateHandlePos(rectQuad(0, 0, 100, 100), 20)
    expect(p.x).toBeCloseTo(0)
    expect(p.y).toBeCloseTo(-70)
  })
})

describe('2本指', () => {
  it('2点の間が2倍・90° 回ると、形も中点のまわりに2倍・90° 回る', () => {
    const start = rectQuad(0, 0, 10, 10)
    const q = pinchQuad(start, { x: -10, y: 0 }, { x: 10, y: 0 }, { x: 0, y: -20 }, { x: 0, y: 20 })
    expect(quadAngleDegrees(q)).toBeCloseTo(90)
    expect(edgeLengths(q)[0]).toBeCloseTo(20)
    expect(quadCenter(q).x).toBeCloseTo(0)
  })

  it('中点が動いた分だけ動く', () => {
    const start = rectQuad(0, 0, 10, 10)
    const q = pinchQuad(start, { x: -10, y: 0 }, { x: 10, y: 0 }, { x: 0, y: 5 }, { x: 20, y: 5 })
    expect(quadCenter(q)).toEqual({ x: 10, y: 5 })
  })
})

describe('数値の入力', () => {
  const natural = { width: 200, height: 100 }

  it('倍率・角度・中心を当てる', () => {
    const q0 = rectQuad(100, 100, 200, 100)
    expect(quadScalePercent(q0, natural)).toBeCloseTo(100)
    const q = applyNumericTransform(q0, natural, { scalePercent: 50, angleDeg: 30, centerX: 300, centerY: 200 })
    expect(quadScalePercent(q, natural)).toBeCloseTo(50)
    expect(quadAngleDegrees(q)).toBeCloseTo(30)
    expect(quadCenter(q).x).toBeCloseTo(300)
    expect(quadCenter(q).y).toBeCloseTo(200)
  })
})

describe('キャンバスに合わせる・中央に寄せる', () => {
  const canvas = { width: 1000, height: 500 }

  it('外接矩形がキャンバスに収まる', () => {
    const q = fitQuadToCanvas(rectQuad(0, 0, 100, 100), canvas)
    const b = quadBounds(q)
    expect(b.minY).toBeCloseTo(0)
    expect(b.maxY).toBeCloseTo(500)
    expect(b.minX).toBeCloseTo(250)
    expect(b.maxX).toBeCloseTo(750)
  })

  it('中央に寄せる(大きさは変えない)', () => {
    const q = centerQuadOnCanvas(rectQuad(0, 0, 100, 100), canvas)
    expect(quadCenter(q)).toEqual({ x: 500, y: 250 })
    expect(edgeLengths(q)[0]).toBeCloseTo(100)
  })
})

describe('initialLayout', () => {
  const canvas = { width: 1024, height: 1024 }

  function overlaps(a: Quad, b: Quad): boolean {
    const ba = quadBounds(a)
    const bb = quadBounds(b)
    return ba.minX < bb.maxX - 1e-6 && bb.minX < ba.maxX - 1e-6 && ba.minY < bb.maxY - 1e-6 && bb.minY < ba.maxY - 1e-6
  }

  it('1枚はキャンバスいっぱいに収める', () => {
    const [q] = initialLayout([{ width: 1024, height: 1024 }], canvas)
    close(q, [
      [0, 0],
      [1024, 0],
      [1024, 1024],
      [0, 1024],
    ])
  })

  it('複数は重ならず、キャンバスに収まり、縦横比を保つ', () => {
    const sizes = [
      { width: 1024, height: 1536 },
      { width: 1536, height: 1024 },
      { width: 512, height: 512 },
      { width: 800, height: 1200 },
      { width: 1000, height: 1000 },
    ]
    const quads = initialLayout(sizes, canvas)
    expect(quads).toHaveLength(5)
    quads.forEach((q, i) => {
      const b = quadBounds(q)
      expect(b.minX).toBeGreaterThanOrEqual(-1e-6)
      expect(b.minY).toBeGreaterThanOrEqual(-1e-6)
      expect(b.maxX).toBeLessThanOrEqual(1024 + 1e-6)
      expect(b.maxY).toBeLessThanOrEqual(1024 + 1e-6)
      const [top, right] = edgeLengths(q)
      expect(top / right).toBeCloseTo(sizes[i].width / sizes[i].height)
      for (let j = i + 1; j < quads.length; j += 1) expect(overlaps(q, quads[j])).toBe(false)
    })
  })

  it('16 枚でも収まる', () => {
    const sizes = Array.from({ length: 16 }, () => ({ width: 1024, height: 1024 }))
    const quads = initialLayout(sizes, canvas)
    expect(quads.every((q) => isValidQuad(q))).toBe(true)
    // 4×4 に並ぶ。
    expect(edgeLengths(quads[0])[0]).toBeGreaterThan(200)
  })
})

describe('当たり判定', () => {
  const q = rotateQuad(rectQuad(0, 0, 100, 100), { x: 0, y: 0 }, Math.PI / 4)

  it('点が中にあるか', () => {
    expect(pointInQuad({ x: 0, y: 0 }, q)).toBe(true)
    expect(pointInQuad({ x: 60, y: 0 }, q)).toBe(true)
    expect(pointInQuad({ x: 49, y: 49 }, q)).toBe(false)
  })

  it('一番近い角を返す', () => {
    expect(hitCorner({ x: 0, y: -69 }, q, 5)).toBe(0)
    expect(hitCorner({ x: 0, y: 0 }, q, 5)).toBe(-1)
  })
})
