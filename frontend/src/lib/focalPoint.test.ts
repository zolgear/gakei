import { describe, expect, it } from 'vitest'
import { focalObjectPosition, focalStyle } from './focalPoint'

describe('focalObjectPosition', () => {
  it('焦点を百分率の object-position にする', () => {
    expect(focalObjectPosition({ x: 0.35, y: 0.2 })).toBe('35% 20%')
    expect(focalObjectPosition({ x: 0.3456, y: 0 })).toBe('34.6% 0%')
    expect(focalObjectPosition({ x: 1, y: 1 })).toBe('100% 100%')
  })

  it('焦点が無ければ undefined(中央のまま)', () => {
    expect(focalObjectPosition(null)).toBeUndefined()
    expect(focalObjectPosition(undefined)).toBeUndefined()
  })

  it('範囲の外は 0〜100% に収め、数でない値は使わない', () => {
    expect(focalObjectPosition({ x: -0.5, y: 1.5 })).toBe('0% 100%')
    expect(focalObjectPosition({ x: Number.NaN, y: 0.5 })).toBeUndefined()
  })
})

describe('focalStyle', () => {
  it('焦点があれば objectPosition を返す', () => {
    expect(focalStyle({ x: 0.5, y: 0.25 })).toEqual({ objectPosition: '50% 25%' })
    expect(focalStyle(null)).toBeUndefined()
  })
})
