import { describe, expect, it } from 'vitest'
import { isAnimatedAsset } from './animatedInputs'

describe('isAnimatedAsset', () => {
  it('GIF だけを入力に使えない画像とみなす(ADR-0061 6章)', () => {
    expect(isAnimatedAsset({ mime: 'image/gif' })).toBe(true)
    expect(isAnimatedAsset({ mime: 'image/png' })).toBe(false)
    expect(isAnimatedAsset(undefined)).toBe(false)
    expect(isAnimatedAsset({})).toBe(false)
  })
})
