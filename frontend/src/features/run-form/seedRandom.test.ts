import { describe, expect, it } from 'vitest'
import { SEED_MAX, isSeedRandom, randomSeedValue, seedMaximum } from './seedRandom'

describe('isSeedRandom', () => {
  it('空文字列はランダム扱い', () => {
    expect(isSeedRandom('')).toBe(true)
  })

  it('数字の文字列は固定扱い', () => {
    expect(isSeedRandom('0')).toBe(false)
    expect(isSeedRandom('12345')).toBe(false)
  })
})

describe('randomSeedValue', () => {
  it('0〜SEED_MAX の範囲に収まる(注入した乱数から決定的に計算する)', () => {
    // 上位 21 bit を全部 1、下位 32 bit を全部 1 にすると SEED_MAX になる。
    const maxBuf = new Uint32Array([0xffffffff, 0xffffffff])
    expect(randomSeedValue(() => maxBuf)).toBe(SEED_MAX)

    const zeroBuf = new Uint32Array([0, 0])
    expect(randomSeedValue(() => zeroBuf)).toBe(0)
  })

  it('上位ワードの下位 21 bit だけを使う', () => {
    // 上位ワードの 22 bit 目以降(0x00200000)は捨てられる。
    const buf = new Uint32Array([0x00200001, 5])
    expect(randomSeedValue(() => buf)).toBe(1 * 2 ** 32 + 5)
  })

  it('引数省略時は実際の crypto.getRandomValues を使い、範囲内に収まる', () => {
    for (let i = 0; i < 20; i++) {
      const value = randomSeedValue()
      expect(Number.isInteger(value)).toBe(true)
      expect(value).toBeGreaterThanOrEqual(0)
      expect(value).toBeLessThanOrEqual(SEED_MAX)
    }
  })
})

describe('上限のある seed(SD WebUI は 2^32 未満。ADR-0038)', () => {
  it('maximum を渡すと 0〜maximum に収める', () => {
    const max = 2 ** 32 - 1
    const value = randomSeedValue(() => new Uint32Array([0x1fffff, 0xffffffff]), max)
    expect(value).toBeGreaterThanOrEqual(0)
    expect(value).toBeLessThanOrEqual(max)
    for (let i = 0; i < 50; i++) {
      expect(randomSeedValue(undefined, max)).toBeLessThanOrEqual(max)
    }
  })

  it('seedMaximum は capabilities の maximum を使い、無ければ SEED_MAX', () => {
    expect(seedMaximum({ maximum: 2 ** 32 - 1 })).toBe(2 ** 32 - 1)
    expect(seedMaximum({ maximum: null })).toBe(SEED_MAX)
    expect(seedMaximum({})).toBe(SEED_MAX)
  })
})
