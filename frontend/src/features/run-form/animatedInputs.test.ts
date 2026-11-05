import { describe, expect, it } from 'vitest'
import { isAnimatedAsset } from './animatedInputs'

describe('isAnimatedAsset', () => {
  it('GIF を入力に使えない画像とみなす(ADR-0061 6章)', () => {
    expect(isAnimatedAsset({ mime: 'image/gif' })).toBe(true)
    expect(isAnimatedAsset({ mime: 'image/png' })).toBe(false)
    expect(isAnimatedAsset(undefined)).toBe(false)
    expect(isAnimatedAsset({})).toBe(false)
  })
})

describe('SVG(ADR-0062)', () => {
  it('SVG も入力に使えない', () => {
    expect(isAnimatedAsset({ mime: 'image/svg+xml' })).toBe(true)
  })
})
