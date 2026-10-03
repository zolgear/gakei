import { describe, expect, it } from 'vitest'
import { mulberry32 } from './random'

describe('mulberry32', () => {
  it('同じ種なら同じ列になる', () => {
    const a = mulberry32(123)
    const b = mulberry32(123)
    const seqA = Array.from({ length: 20 }, () => a())
    const seqB = Array.from({ length: 20 }, () => b())
    expect(seqA).toEqual(seqB)
  })

  it('種が違えば列も違う', () => {
    const a = mulberry32(1)
    const b = mulberry32(2)
    expect(Array.from({ length: 5 }, () => a())).not.toEqual(Array.from({ length: 5 }, () => b()))
  })

  it('[0, 1) に収まり、偏りが小さい', () => {
    const r = mulberry32(42)
    let sum = 0
    for (let i = 0; i < 10000; i++) {
      const v = r()
      expect(v).toBeGreaterThanOrEqual(0)
      expect(v).toBeLessThan(1)
      sum += v
    }
    expect(sum / 10000).toBeGreaterThan(0.48)
    expect(sum / 10000).toBeLessThan(0.52)
  })
})
