import { describe, expect, it } from 'vitest'
import {
  clampSplitPercent,
  computeContainRect,
  computeFrameSize,
  computeSplitClipPath,
  splitPercentFromPointer,
  transformsEqual,
} from './compareSync'

describe('transformsEqual', () => {
  it('完全に同じなら true', () => {
    expect(transformsEqual({ positionX: 1, positionY: 2, scale: 1 }, { positionX: 1, positionY: 2, scale: 1 })).toBe(
      true,
    )
  })

  it('誤差の範囲内なら true', () => {
    expect(
      transformsEqual({ positionX: 1, positionY: 2, scale: 1 }, { positionX: 1.005, positionY: 2, scale: 1 }),
    ).toBe(true)
  })

  it('誤差を超えたら false', () => {
    expect(transformsEqual({ positionX: 1, positionY: 2, scale: 1 }, { positionX: 1.5, positionY: 2, scale: 1 })).toBe(
      false,
    )
    expect(transformsEqual({ positionX: 0, positionY: 0, scale: 1 }, { positionX: 0, positionY: 0, scale: 1.2 })).toBe(
      false,
    )
  })
})

describe('computeFrameSize', () => {
  it('出力の縦横比(サイズ)を基準にする', () => {
    expect(computeFrameSize({ width: 2048, height: 1024 }, { width: 1024, height: 1024 })).toEqual({
      width: 2048,
      height: 1024,
    })
  })

  it('出力が無効なら入力を基準にする', () => {
    expect(computeFrameSize(null, { width: 800, height: 600 })).toEqual({ width: 800, height: 600 })
    expect(computeFrameSize({ width: 0, height: 0 }, { width: 800, height: 600 })).toEqual({
      width: 800,
      height: 600,
    })
  })

  it('どちらも無効なら 1x1', () => {
    expect(computeFrameSize(null, null)).toEqual({ width: 1, height: 1 })
  })
})

describe('computeContainRect', () => {
  it('横長の画像を正方形の枠に収める', () => {
    // 枠 1000x1000、画像 2000x1000(2:1)→ fitScale=0.5 → 1000x500、上下に余白
    const rect = computeContainRect({ width: 1000, height: 1000 }, { width: 2000, height: 1000 })
    expect(rect.fitScale).toBeCloseTo(0.5)
    expect(rect.width).toBeCloseTo(1000)
    expect(rect.height).toBeCloseTo(500)
    expect(rect.left).toBeCloseTo(0)
    expect(rect.top).toBeCloseTo(250)
  })

  it('縦長の画像を横長の枠に収める', () => {
    const rect = computeContainRect({ width: 2000, height: 1000 }, { width: 1000, height: 2000 })
    expect(rect.fitScale).toBeCloseTo(0.5)
    expect(rect.width).toBeCloseTo(500)
    expect(rect.height).toBeCloseTo(1000)
    expect(rect.left).toBeCloseTo(750)
    expect(rect.top).toBeCloseTo(0)
  })

  it('同じ縦横比ならぴったり収まる(余白なし)', () => {
    const rect = computeContainRect({ width: 1000, height: 1000 }, { width: 500, height: 500 })
    expect(rect.fitScale).toBeCloseTo(2)
    expect(rect.width).toBeCloseTo(1000)
    expect(rect.height).toBeCloseTo(1000)
    expect(rect.left).toBe(0)
    expect(rect.top).toBe(0)
  })

  it('サイズが0以下なら全て0', () => {
    expect(computeContainRect({ width: 0, height: 0 }, { width: 100, height: 100 })).toEqual({
      width: 0,
      height: 0,
      left: 0,
      top: 0,
      fitScale: 0,
    })
  })
})

describe('clampSplitPercent', () => {
  it('0〜100 の範囲内はそのまま', () => {
    expect(clampSplitPercent(50)).toBe(50)
  })

  it('範囲外はクランプする', () => {
    expect(clampSplitPercent(-10)).toBe(0)
    expect(clampSplitPercent(150)).toBe(100)
  })

  it('NaN は 50 にフォールバックする', () => {
    expect(clampSplitPercent(NaN)).toBe(50)
  })
})

describe('splitPercentFromPointer', () => {
  it('コンテナ中央なら50%', () => {
    expect(splitPercentFromPointer(500, 0, 1000)).toBe(50)
  })

  it('左端/右端でクランプする', () => {
    expect(splitPercentFromPointer(-100, 0, 1000)).toBe(0)
    expect(splitPercentFromPointer(1500, 0, 1000)).toBe(100)
  })

  it('コンテナの left オフセットを考慮する', () => {
    expect(splitPercentFromPointer(600, 100, 1000)).toBe(50)
  })

  it('コンテナ幅が0以下なら50を返す', () => {
    expect(splitPercentFromPointer(10, 0, 0)).toBe(50)
  })
})

describe('computeSplitClipPath', () => {
  it('分割位置に応じた inset() を返す', () => {
    expect(computeSplitClipPath(30)).toBe('inset(0 0 0 30%)')
  })

  it('クランプしてから使う', () => {
    expect(computeSplitClipPath(-5)).toBe('inset(0 0 0 0%)')
    expect(computeSplitClipPath(120)).toBe('inset(0 0 0 100%)')
  })
})
