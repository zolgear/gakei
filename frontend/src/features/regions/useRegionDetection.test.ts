import { describe, expect, it } from 'vitest'
import { candidatesOf } from './useRegionDetection'

describe('candidatesOf', () => {
  it('検出1回分の候補にし、壊れた四角は捨てる', () => {
    const candidates = candidatesOf({
      id: 'd1',
      query: null,
      model: 'm',
      created_at: '2026-10-20T00:00:00Z',
      regions: [
        { label: 'カップ', box: [0.1, 0.2, 0.3, 0.4] },
        { label: '壊れた', box: [0.5, 0.5, 0.5, 0.6] },
      ],
    })
    expect(candidates).toEqual([{ key: 'd1:0', label: 'カップ', box: [0.1, 0.2, 0.3, 0.4] }])
  })
})
