import { describe, expect, it } from 'vitest'
import { computeFitScale } from '../../lib/geometry'
import { shouldUseOriginal } from './viewerScale'

describe('computeFitScale', () => {
  it('コンテンツがコンテナより大きい場合は縮小する', () => {
    // 3840x2160 を 1200x800 に収める → min(1200/3840, 800/2160) = min(0.3125, 0.370..) = 0.3125
    expect(computeFitScale({ width: 1200, height: 800 }, { width: 3840, height: 2160 })).toBeCloseTo(
      0.3125,
    )
  })

  it('コンテンツがコンテナより小さい場合は拡大せず1になる', () => {
    expect(computeFitScale({ width: 1200, height: 800 }, { width: 400, height: 300 })).toBe(1)
  })

  it('ちょうど収まる場合は1になる', () => {
    expect(computeFitScale({ width: 800, height: 600 }, { width: 800, height: 600 })).toBe(1)
  })

  it('コンテナのサイズが0以下なら1を返す', () => {
    expect(computeFitScale({ width: 0, height: 0 }, { width: 800, height: 600 })).toBe(1)
  })

  it('コンテンツのサイズが0以下なら1を返す', () => {
    expect(computeFitScale({ width: 800, height: 600 }, { width: 0, height: 0 })).toBe(1)
  })
})

describe('shouldUseOriginal', () => {
  const content = { width: 3840, height: 2160 }

  it('等倍(dpr=1)で preview の実解像度(2048)以下なら false', () => {
    // displayedEdge = 0.5 * 3840 * 1 = 1920 <= 2048
    expect(shouldUseOriginal(0.5, content, 2048, 1)).toBe(false)
  })

  it('等倍(dpr=1)で preview の実解像度を超えたら true', () => {
    // displayedEdge = 0.6 * 3840 * 1 = 2304 > 2048
    expect(shouldUseOriginal(0.6, content, 2048, 1)).toBe(true)
  })

  it('devicePixelRatio を考慮する(HiDPIでは低いscaleでも切り替わる)', () => {
    // displayedEdge = 0.3 * 3840 * 2 = 2304 > 2048 (dpr=1なら 1152 で切り替わらない)
    expect(shouldUseOriginal(0.3, content, 2048, 1)).toBe(false)
    expect(shouldUseOriginal(0.3, content, 2048, 2)).toBe(true)
  })

  it('画像の長辺が preview の長辺より小さい場合は、その長辺を基準にする', () => {
    const small = { width: 1024, height: 1024 }
    // previewNativeEdge = min(2048, 1024) = 1024。displayedEdge = 1.5*1024 = 1536 > 1024
    expect(shouldUseOriginal(1.5, small, 2048, 1)).toBe(true)
    expect(shouldUseOriginal(0.9, small, 2048, 1)).toBe(false)
  })
})
