import { describe, expect, it } from 'vitest'
import {
  clampSplitPercent,
  computeContainRect,
  computeFrameSize,
  computeSharedFrameSize,
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

describe('computeSharedFrameSize(重複の候補の比較)', () => {
  it('解像度だけが違う2枚は、大きい方の寸法の枠にし、小さい方を同じ大きさまで拡大する', () => {
    const big = { width: 1080, height: 1080 }
    const small = { width: 540, height: 540 }
    // どちらの順でも大きい方が枠になる
    expect(computeSharedFrameSize(big, small)).toEqual(big)
    expect(computeSharedFrameSize(small, big)).toEqual(big)
    const frame = computeSharedFrameSize(small, big)
    const a = computeContainRect(frame, small)
    const b = computeContainRect(frame, big)
    // 表示上の矩形が一致する(重ねてもずれない)
    expect({ ...a, fitScale: 0 }).toEqual({ ...b, fitScale: 0 })
    expect(a).toEqual({ width: 1080, height: 1080, left: 0, top: 0, fitScale: 2 })
    expect(b.fitScale).toBe(1)
  })

  it('縦横比も同じ横長の2枚(1536x1024 と 768x512)も同じ矩形に重なる', () => {
    const frame = computeSharedFrameSize({ width: 768, height: 512 }, { width: 1536, height: 1024 })
    expect(frame).toEqual({ width: 1536, height: 1024 })
    expect(computeContainRect(frame, { width: 768, height: 512 })).toEqual({
      width: 1536,
      height: 1024,
      left: 0,
      top: 0,
      fitScale: 2,
    })
  })

  it('縦横比が違うときは同じ枠に contain で収め、どちらも中心を揃える', () => {
    const frame = computeSharedFrameSize({ width: 1000, height: 1000 }, { width: 600, height: 400 })
    expect(frame).toEqual({ width: 1000, height: 1000 })
    const wide = computeContainRect(frame, { width: 600, height: 400 })
    expect(wide.width).toBeCloseTo(1000)
    expect(wide.height).toBeCloseTo(666.667, 2)
    expect(wide.left).toBeCloseTo(0)
    expect(wide.top).toBeCloseTo(166.667, 2)
    expect(wide.top + wide.height / 2).toBeCloseTo(frame.height / 2)
  })

  it('面積が同じなら1枚目、無効なサイズは無視する', () => {
    expect(computeSharedFrameSize({ width: 200, height: 100 }, { width: 100, height: 200 })).toEqual({ width: 200, height: 100 })
    expect(computeSharedFrameSize(null, { width: 100, height: 50 })).toEqual({ width: 100, height: 50 })
    expect(computeSharedFrameSize({ width: 0, height: 0 }, { width: 100, height: 50 })).toEqual({ width: 100, height: 50 })
    expect(computeSharedFrameSize({ width: 100, height: 50 }, null)).toEqual({ width: 100, height: 50 })
    expect(computeSharedFrameSize(null, null)).toEqual({ width: 1, height: 1 })
  })
})
