import { describe, expect, it } from 'vitest'
import { computeExpectedPartials } from './expectedPartials'

describe('computeExpectedPartials', () => {
  it('n=1(既定)・partial_images=2 なら 2', () => {
    expect(computeExpectedPartials({ partial_images: 2 })).toBe(2)
  })

  it('n=3・partial_images=2 なら 6', () => {
    expect(computeExpectedPartials({ n: 3, partial_images: 2 })).toBe(6)
  })

  it('partial_images が無い・0 なら null', () => {
    expect(computeExpectedPartials({ n: 3 })).toBeNull()
    expect(computeExpectedPartials({ n: 3, partial_images: 0 })).toBeNull()
  })

  it('params が null/undefined でも null', () => {
    expect(computeExpectedPartials(null)).toBeNull()
    expect(computeExpectedPartials(undefined)).toBeNull()
  })

  it('数値でない値(文字列・負の数)は既定値扱い', () => {
    expect(computeExpectedPartials({ n: '5', partial_images: 2 })).toBe(2) // n は既定1扱い
    expect(computeExpectedPartials({ n: 2, partial_images: -1 })).toBeNull() // partial_images は既定0扱い
  })

  it('小数は切り捨てる', () => {
    expect(computeExpectedPartials({ n: 2.9, partial_images: 1.9 })).toBe(2) // 2 * 1 = 2
  })
})
