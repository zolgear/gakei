import { describe, expect, it } from 'vitest'
import {
  DEFAULT_MAP_URL_STATE,
  activeFilterCount,
  buildMapSearchParams,
  parseMapUrlState,
} from './mapUrlState'

describe('mapUrlState', () => {
  it('空なら既定', () => {
    expect(parseMapUrlState(new URLSearchParams())).toEqual(DEFAULT_MAP_URL_STATE)
    expect(buildMapSearchParams(DEFAULT_MAP_URL_STATE).toString()).toBe('')
  })

  it('往復で同じ状態に戻る', () => {
    const state = { view: 'network' as const, groupId: 'g1', tag: '猫', limit: 2000, k: 20 }
    expect(parseMapUrlState(buildMapSearchParams(state))).toEqual(state)
  })

  it('選べない値は既定に戻す', () => {
    const s = parseMapUrlState(new URLSearchParams('view=x&limit=123&k=abc'))
    expect(s.view).toBe('umap')
    expect(s.limit).toBe(DEFAULT_MAP_URL_STATE.limit)
    expect(s.k).toBe(DEFAULT_MAP_URL_STATE.k)
  })

  it('既定から変えた絞り込みを数える', () => {
    expect(activeFilterCount(DEFAULT_MAP_URL_STATE)).toBe(0)
    expect(activeFilterCount({ ...DEFAULT_MAP_URL_STATE, tag: 'a', k: 5, view: 'network' })).toBe(2)
  })
})
