import { describe, expect, it } from 'vitest'
import {
  DEFAULT_MAP_URL_STATE,
  activeFilterCount,
  buildMapSearchParams,
  clampThreshold,
  parseMapUrlState,
} from './mapUrlState'

describe('mapUrlState', () => {
  it('空なら既定', () => {
    expect(parseMapUrlState(new URLSearchParams())).toEqual(DEFAULT_MAP_URL_STATE)
    expect(buildMapSearchParams(DEFAULT_MAP_URL_STATE).toString()).toBe('')
  })

  it('往復で同じ状態に戻る', () => {
    const state = {
      view: 'umap' as const,
      groupId: 'g1',
      tags: ['猫', 'outdoor'],
      limit: 2000,
      k: 20,
      threshold: 0.85,
      showLineage: true,
      selectedId: 'a1',
    }
    expect(parseMapUrlState(buildMapSearchParams(state))).toEqual(state)
  })

  it('選べない値は既定に戻す', () => {
    const s = parseMapUrlState(new URLSearchParams('view=x&limit=123&k=abc'))
    expect(s.view).toBe('network')
    expect(s.limit).toBe(DEFAULT_MAP_URL_STATE.limit)
    expect(s.k).toBe(DEFAULT_MAP_URL_STATE.k)
  })

  it('既定から変えた絞り込みを数える', () => {
    expect(activeFilterCount(DEFAULT_MAP_URL_STATE)).toBe(0)
    expect(activeFilterCount({ ...DEFAULT_MAP_URL_STATE, tags: ['a'], k: 5, view: 'umap' })).toBe(2)
    expect(activeFilterCount({ ...DEFAULT_MAP_URL_STATE, tags: ['a', 'b', 'c'] })).toBe(3)
  })

  it('ネットワークが既定。地図は view=map', () => {
    expect(DEFAULT_MAP_URL_STATE.view).toBe('network')
    expect(buildMapSearchParams({ ...DEFAULT_MAP_URL_STATE, view: 'umap' }).toString()).toBe('view=map')
    expect(buildMapSearchParams({ ...DEFAULT_MAP_URL_STATE, view: 'network' }).toString()).toBe('')
    expect(parseMapUrlState(new URLSearchParams('view=map')).view).toBe('umap')
    // 以前の URL も読める。
    expect(parseMapUrlState(new URLSearchParams('view=network')).view).toBe('network')
    expect(parseMapUrlState(new URLSearchParams('view=umap')).view).toBe('umap')
  })

  it('タグは繰り返しの tag=。正規化して重複と空を除き、順を保つ', () => {
    const s = parseMapUrlState(new URLSearchParams('tag=%20Blue%E3%80%80%20Sky%20&tag=cat&tag=&tag=CAT&tag=blue%20sky'))
    expect(s.tags).toEqual(['blue sky', 'cat'])
    expect(buildMapSearchParams({ ...DEFAULT_MAP_URL_STATE, tags: ['b', 'A', 'a'] }).toString()).toBe('tag=b&tag=a')
    expect(parseMapUrlState(new URLSearchParams(`tag=${'x'.repeat(101)}`)).tags).toEqual([])
    // 1つだけの tag= も従来どおり読める。
    expect(parseMapUrlState(new URLSearchParams('tag=猫')).tags).toEqual(['猫'])
  })

  it('しきい値・系列・選択を URL に置き、既定は省く', () => {
    const qs = buildMapSearchParams({ ...DEFAULT_MAP_URL_STATE, threshold: 0.9, showLineage: true, selectedId: 'abc' })
    expect(qs.toString()).toBe('th=0.90&lineage=1&sel=abc')
    expect(buildMapSearchParams({ ...DEFAULT_MAP_URL_STATE, threshold: 0.8 }).toString()).toBe('')
  })

  it('しきい値は範囲に収めて丸め、数でなければ既定', () => {
    expect(parseMapUrlState(new URLSearchParams('th=2')).threshold).toBe(0.99)
    expect(parseMapUrlState(new URLSearchParams('th=0.1')).threshold).toBe(0.5)
    expect(parseMapUrlState(new URLSearchParams('th=0.8349')).threshold).toBe(0.83)
    expect(parseMapUrlState(new URLSearchParams('th=abc')).threshold).toBe(0.8)
    expect(parseMapUrlState(new URLSearchParams('th=')).threshold).toBe(0.8)
    expect(clampThreshold(0.7 + 0.1)).toBe(0.8)
  })

  it('lineage は 1 のときだけ、sel は空なら選ばない', () => {
    expect(parseMapUrlState(new URLSearchParams('lineage=true')).showLineage).toBe(false)
    expect(parseMapUrlState(new URLSearchParams('lineage=1')).showLineage).toBe(true)
    expect(parseMapUrlState(new URLSearchParams('sel=%20')).selectedId).toBeNull()
  })

  it('しきい値・系列・選択は絞り込みの数に入れない', () => {
    expect(
      activeFilterCount({ ...DEFAULT_MAP_URL_STATE, threshold: 0.9, showLineage: true, selectedId: 'x' }),
    ).toBe(0)
  })
})
