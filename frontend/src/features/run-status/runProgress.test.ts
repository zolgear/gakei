import { describe, expect, it } from 'vitest'
import { latestPartialsPerOutput, progressPercent } from './runProgress'

describe('progressPercent', () => {
  it('value/max の割合(%)を返す', () => {
    expect(progressPercent({ value: 3, max: 10 })).toBe(30)
  })

  it('value が max と同じなら100', () => {
    expect(progressPercent({ value: 10, max: 10 })).toBe(100)
  })

  it('value が max を超えても100で頭打ち', () => {
    expect(progressPercent({ value: 15, max: 10 })).toBe(100)
  })

  it('max が0以下なら0(0除算を避ける)', () => {
    expect(progressPercent({ value: 0, max: 0 })).toBe(0)
    expect(progressPercent({ value: 5, max: 0 })).toBe(0)
  })

  it('value/max が null/undefined なら0扱い', () => {
    expect(progressPercent({ value: null, max: null })).toBe(0)
    expect(progressPercent({ value: undefined, max: undefined })).toBe(0)
  })

  it('value が負なら0で頭打ち', () => {
    expect(progressPercent({ value: -5, max: 10 })).toBe(0)
  })
})

describe('latestPartialsPerOutput', () => {
  it('出力ごとに index が最大の1枚だけを残し、output_index の昇順に並べる', () => {
    const partials = [
      { index: 0, output_index: 0 },
      { index: 1, output_index: 1 },
      { index: 2, output_index: 0 },
      { index: 3, output_index: 0 },
      { index: 4, output_index: 1 },
    ]
    expect(latestPartialsPerOutput(partials)).toEqual([
      { index: 3, output_index: 0 },
      { index: 4, output_index: 1 },
    ])
  })

  it('output_index が無ければ 0 とみなす。空なら空', () => {
    expect(latestPartialsPerOutput([{ index: 0 }, { index: 1 }])).toEqual([{ index: 1 }])
    expect(latestPartialsPerOutput([])).toEqual([])
  })
})
