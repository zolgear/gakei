import { describe, expect, it } from 'vitest'
import {
  clampRect,
  fitScale,
  initialRect,
  moveRect,
  MIN_CROP_SIZE,
  resizeWithAspect,
  roundRect,
  toDisplay,
  toNatural,
  type CropRect,
} from './cropMath'

describe('fitScale', () => {
  it('container の方が小さければ縮小する', () => {
    expect(fitScale({ width: 1000, height: 500 }, { width: 500, height: 500 })).toBe(0.5)
  })

  it('container の方が大きければ拡大する(ビューアと違い1を上限にしない)', () => {
    expect(fitScale({ width: 100, height: 100 }, { width: 400, height: 300 })).toBe(3)
  })

  it('natural/container のいずれかが0以下なら1を返す', () => {
    expect(fitScale({ width: 0, height: 100 }, { width: 100, height: 100 })).toBe(1)
    expect(fitScale({ width: 100, height: 100 }, { width: 100, height: 0 })).toBe(1)
  })
})

describe('toDisplay / toNatural', () => {
  it('往復すると元の矩形に戻る(整数入力)', () => {
    const rect: CropRect = { x: 10, y: 20, width: 300, height: 150 }
    for (const scale of [0.5, 1, 1.3, 2, 3.7]) {
      expect(toNatural(toDisplay(rect, scale), scale)).toEqual(rect)
    }
  })

  it('toDisplay はスケールを乗じるだけ', () => {
    expect(toDisplay({ x: 10, y: 20, width: 30, height: 40 }, 2)).toEqual({
      x: 20,
      y: 40,
      width: 60,
      height: 80,
    })
  })

  it('toNatural はスケールが0でもゼロ除算せず丸めた矩形を返す', () => {
    expect(toNatural({ x: 10, y: 20, width: 30, height: 40 }, 0)).toEqual({
      x: 10,
      y: 20,
      width: 30,
      height: 40,
    })
  })
})

describe('clampRect', () => {
  it('bounds の内側ならそのまま', () => {
    const rect: CropRect = { x: 10, y: 10, width: 50, height: 50 }
    expect(clampRect(rect, { width: 100, height: 100 })).toEqual(rect)
  })

  it('右下にはみ出していれば位置をずらす(サイズは変えない)', () => {
    const rect: CropRect = { x: 80, y: 80, width: 50, height: 50 }
    expect(clampRect(rect, { width: 100, height: 100 })).toEqual({ x: 50, y: 50, width: 50, height: 50 })
  })

  it('bounds よりサイズが大きければサイズごと縮める', () => {
    const rect: CropRect = { x: 0, y: 0, width: 200, height: 150 }
    expect(clampRect(rect, { width: 100, height: 100 })).toEqual({ x: 0, y: 0, width: 100, height: 100 })
  })
})

describe('moveRect', () => {
  const bounds = { width: 200, height: 100 }

  it('境界内なら (dx, dy) だけ動く', () => {
    const rect: CropRect = { x: 10, y: 10, width: 50, height: 50 }
    expect(moveRect(rect, 5, -5, bounds)).toEqual({ x: 15, y: 5, width: 50, height: 50 })
  })

  it('外周を超えないようクランプする(サイズは変えない)', () => {
    const rect: CropRect = { x: 10, y: 10, width: 50, height: 50 }
    expect(moveRect(rect, 1000, 1000, bounds)).toEqual({ x: 150, y: 50, width: 50, height: 50 })
    expect(moveRect(rect, -1000, -1000, bounds)).toEqual({ x: 0, y: 0, width: 50, height: 50 })
  })
})

describe('resizeWithAspect', () => {
  const bounds = { width: 400, height: 300 }

  it('se ハンドルは右下に広がる(自由な形)', () => {
    const rect: CropRect = { x: 50, y: 50, width: 100, height: 100 }
    expect(resizeWithAspect(rect, 'se', 20, 10, null, bounds)).toEqual({
      x: 50,
      y: 50,
      width: 120,
      height: 110,
    })
  })

  it('nw ハンドルは対角(右下)を固定して左上に広がる', () => {
    const rect: CropRect = { x: 100, y: 100, width: 100, height: 100 }
    // 左に20、上に10 動かす(角を (80, 90) に)
    expect(resizeWithAspect(rect, 'nw', -20, -10, null, bounds)).toEqual({
      x: 80,
      y: 90,
      width: 120,
      height: 110,
    })
  })

  it('ne ハンドルは右上、sw ハンドルは左下に広がる', () => {
    const rect: CropRect = { x: 100, y: 100, width: 100, height: 100 }
    expect(resizeWithAspect(rect, 'ne', 20, -10, null, bounds)).toEqual({
      x: 100,
      y: 90,
      width: 120,
      height: 110,
    })
    expect(resizeWithAspect(rect, 'sw', -20, 10, null, bounds)).toEqual({
      x: 80,
      y: 100,
      width: 120,
      height: 110,
    })
  })

  it('aspect を指定すると縦横比を保つ', () => {
    const rect: CropRect = { x: 0, y: 0, width: 100, height: 100 }
    const result = resizeWithAspect(rect, 'se', 100, 0, 1, bounds)
    expect(result.width).toBe(result.height)
    expect(result.width).toBeGreaterThan(100)
  })

  it('aspect 固定で bounds の外に出そうになると、比を保ったまま縮める', () => {
    const rect: CropRect = { x: 350, y: 0, width: 50, height: 50 }
    // 右に大きく広げようとしても bounds.width=400 まで(残り50px)しか広がれない
    const result = resizeWithAspect(rect, 'se', 500, 500, 1, bounds)
    expect(result.x + result.width).toBeLessThanOrEqual(bounds.width)
    expect(result.y + result.height).toBeLessThanOrEqual(bounds.height)
    expect(result.width).toBe(result.height)
  })

  it('最小サイズ(MIN_CROP_SIZE)未満には縮まらない(anchor を越えて反転もしない)', () => {
    const rect: CropRect = { x: 100, y: 100, width: 100, height: 100 }
    const result = resizeWithAspect(rect, 'se', -1000, -1000, null, bounds)
    expect(result.width).toBe(MIN_CROP_SIZE)
    expect(result.height).toBe(MIN_CROP_SIZE)
    // anchor(左上、100,100)は固定されたまま。
    expect(result.x).toBe(100)
    expect(result.y).toBe(100)
  })

  it('aspect 固定でも最小サイズを下回らない', () => {
    const rect: CropRect = { x: 100, y: 100, width: 100, height: 200 }
    const result = resizeWithAspect(rect, 'se', -1000, -1000, 0.5, bounds)
    expect(result.width).toBeGreaterThanOrEqual(MIN_CROP_SIZE)
    expect(result.width / result.height).toBeCloseTo(0.5, 5)
  })
})

describe('initialRect', () => {
  it('aspect が null なら画像全体', () => {
    expect(initialRect({ width: 300, height: 200 }, null)).toEqual({ x: 0, y: 0, width: 300, height: 200 })
  })

  it('正方形(aspect=1)、横長画像なら高さいっぱいの正方形を中央に置く', () => {
    expect(initialRect({ width: 300, height: 200 }, 1)).toEqual({ x: 50, y: 0, width: 200, height: 200 })
  })

  it('正方形(aspect=1)、縦長画像なら幅いっぱいの正方形を中央に置く', () => {
    expect(initialRect({ width: 200, height: 300 }, 1)).toEqual({ x: 0, y: 50, width: 200, height: 200 })
  })

  it('任意の縦横比でも中央かつ画像内に収まる', () => {
    const result = initialRect({ width: 300, height: 300 }, 16 / 9)
    expect(result.width / result.height).toBeCloseTo(16 / 9, 1)
    expect(result.x).toBeGreaterThanOrEqual(0)
    expect(result.y).toBeGreaterThanOrEqual(0)
    expect(result.x + result.width).toBeLessThanOrEqual(300)
    expect(result.y + result.height).toBeLessThanOrEqual(300)
  })
})

describe('roundRect', () => {
  it('全フィールドを整数に丸める', () => {
    expect(roundRect({ x: 1.4, y: 1.6, width: 10.5, height: 10.49 })).toEqual({
      x: 1,
      y: 2,
      width: 11,
      height: 10,
    })
  })

  it('幅・高さが負になることはない', () => {
    expect(roundRect({ x: 0, y: 0, width: -5, height: -1 })).toEqual({ x: 0, y: 0, width: 0, height: 0 })
  })
})
