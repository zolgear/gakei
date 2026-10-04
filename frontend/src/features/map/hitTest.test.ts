import { describe, expect, it } from 'vitest'
import { hitTest } from './hitTest'

describe('hitTest', () => {
  const positions = new Float32Array([0, 0, 10, 0, 10.5, 0.2])
  const v = { x: 0, y: 0, scale: 10 }

  it('半径の内側で最も近いものを返す', () => {
    expect(hitTest(positions, 3, v, 2, 1, 8)).toBe(0)
    expect(hitTest(positions, 3, v, 104, 1, 8)).toBe(2)
    expect(hitTest(positions, 3, v, 99, 0, 8)).toBe(1)
  })

  it('外れたら -1', () => {
    expect(hitTest(positions, 3, v, 50, 50, 8)).toBe(-1)
  })

  it('表示の倍率と平行移動を考慮する', () => {
    expect(hitTest(positions, 3, { x: 100, y: 100, scale: 1 }, 110, 100, 2)).toBe(1)
  })

  it('有限でない座標は飛ばす', () => {
    expect(hitTest(new Float32Array([Number.NaN, 0]), 1, v, 0, 0, 100)).toBe(-1)
  })
})
