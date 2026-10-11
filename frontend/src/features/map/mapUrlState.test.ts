import { describe, expect, it } from 'vitest'
import {
  DEFAULT_MAP_URL_STATE,
  activeFilterCount,
  buildMapSearchParams,
  clampThreshold,
  isMapSettingsChange,
  parseMapUrlState,
} from './mapUrlState'

describe('mapUrlState', () => {
  it('空なら既定', () => {
    expect(parseMapUrlState(new URLSearchParams())).toEqual(DEFAULT_MAP_URL_STATE)
    expect(buildMapSearchParams(DEFAULT_MAP_URL_STATE).toString()).toBe('')
  })

  it('往復で同じ状態に戻る', () => {
    const state = {
      view: 'umap3d' as const,
      shape: 'flat' as const,
      groupId: 'g1',
      tags: ['猫', 'outdoor'],
      limit: 2000,
      k: 20,
      threshold: 0.85,
      showLineage: true,
      selectedId: 'a1',
      query: '夕暮れの海辺',
      matchCount: 50,
      metric: 'rank' as const,
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
    expect(activeFilterCount({ ...DEFAULT_MAP_URL_STATE, groupId: 'g1' })).toBe(1)
    expect(activeFilterCount({ ...DEFAULT_MAP_URL_STATE, groupId: 'g1', tags: ['a'] })).toBe(2)
    expect(activeFilterCount({ ...DEFAULT_MAP_URL_STATE, tags: ['a', 'b', 'c'] })).toBe(3)
  })

  it('3D は view=3d。往復で戻る', () => {
    expect(buildMapSearchParams({ ...DEFAULT_MAP_URL_STATE, view: 'umap3d' }).toString()).toBe('view=3d')
    expect(parseMapUrlState(new URLSearchParams('view=3d')).view).toBe('umap3d')
    // 手で書かれた内部の名前も読む。
    expect(parseMapUrlState(new URLSearchParams('view=umap3d')).view).toBe('umap3d')
  })

  it('ネットワークが既定。以前の 2D の地図の URL は 3D として読む', () => {
    expect(DEFAULT_MAP_URL_STATE.view).toBe('network')
    expect(buildMapSearchParams({ ...DEFAULT_MAP_URL_STATE, view: 'network' }).toString()).toBe('')
    // タブは「ネットワーク」と「地図」の 2 つ。2D の地図の URL で開いても空白にならない。
    expect(parseMapUrlState(new URLSearchParams('view=map')).view).toBe('umap3d')
    expect(parseMapUrlState(new URLSearchParams('view=umap')).view).toBe('umap3d')
    // 以前の URL も読める。
    expect(parseMapUrlState(new URLSearchParams('view=network')).view).toBe('network')
  })

  it('見せ方は shape=flat。既定の立体は URL に出さない', () => {
    expect(DEFAULT_MAP_URL_STATE.shape).toBe('orbit')
    expect(buildMapSearchParams({ ...DEFAULT_MAP_URL_STATE, shape: 'orbit' }).toString()).toBe('')
    expect(buildMapSearchParams({ ...DEFAULT_MAP_URL_STATE, shape: 'flat' }).toString()).toBe('shape=flat')
    expect(parseMapUrlState(new URLSearchParams('shape=flat')).shape).toBe('flat')
    expect(parseMapUrlState(new URLSearchParams('shape=orbit')).shape).toBe('orbit')
    // 知らない値は既定(または覚えた値)。
    expect(parseMapUrlState(new URLSearchParams('shape=cube')).shape).toBe('orbit')
    expect(parseMapUrlState(new URLSearchParams('shape=cube'), { shape: 'flat' }).shape).toBe('flat')
    // 見せ方はタブと同じく「画面の設定」には数えない(開き直したときの回転を止めない)。
    expect(isMapSettingsChange({ shape: 'flat' })).toBe(false)
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

  it('検索の文章・一致の件数・距離の取り方を URL に置き、既定は省く', () => {
    const qs = buildMapSearchParams({
      ...DEFAULT_MAP_URL_STATE,
      query: '  cat  ',
      matchCount: 100,
      metric: 'rowNormalized',
    })
    // 打った空白はそのまま保つ(打っている途中に消えないように)。
    expect(qs.get('q')).toBe('  cat  ')
    expect(qs.get('mc')).toBe('100')
    expect(qs.get('dist')).toBe('rowNormalized')
    expect(
      buildMapSearchParams({ ...DEFAULT_MAP_URL_STATE, query: '   ', matchCount: 25, metric: 'similarity' }).toString(),
    ).toBe('')
  })

  it('一致の件数と距離の取り方は、選べない値なら既定', () => {
    const s = parseMapUrlState(new URLSearchParams('mc=7&dist=cosine'))
    expect(s.matchCount).toBe(DEFAULT_MAP_URL_STATE.matchCount)
    expect(s.metric).toBe('similarity')
    expect(parseMapUrlState(new URLSearchParams('mc=10&dist=rank'))).toMatchObject({
      matchCount: 10,
      metric: 'rank',
    })
    expect(parseMapUrlState(new URLSearchParams()).query).toBe('')
  })

  it('検索の文章・一致の件数・距離の取り方は絞り込みの数に入れない', () => {
    expect(
      activeFilterCount({ ...DEFAULT_MAP_URL_STATE, query: 'cat', matchCount: 100, metric: 'rank' }),
    ).toBe(0)
  })

  it('しきい値・系列・選択は絞り込みの数に入れない', () => {
    expect(
      activeFilterCount({ ...DEFAULT_MAP_URL_STATE, threshold: 0.9, showLineage: true, selectedId: 'x' }),
    ).toBe(0)
  })

  it('上限と近傍の数は絞り込みの数に入れない(規模の調整で、画像は絞られない)', () => {
    expect(activeFilterCount({ ...DEFAULT_MAP_URL_STATE, limit: 5000, k: 30 })).toBe(0)
    // 覚えた近傍の数だけが既定と違うときも、バッジは付かない。
    expect(activeFilterCount(parseMapUrlState(new URLSearchParams(), { k: 15 }))).toBe(0)
    // 絞り込みと混ざっても、数えるのはグループとタグだけ。
    expect(activeFilterCount({ ...DEFAULT_MAP_URL_STATE, limit: 5000, k: 30, groupId: 'g', tags: ['a'] })).toBe(2)
  })
})

describe('isMapSettingsChange', () => {
  it('絞り込み・しきい値・検索・距離の取り方は設定の変更', () => {
    expect(isMapSettingsChange({ groupId: 'g1' })).toBe(true)
    expect(isMapSettingsChange({ tags: ['a'] })).toBe(true)
    expect(isMapSettingsChange({ limit: 500 })).toBe(true)
    expect(isMapSettingsChange({ k: 20 })).toBe(true)
    expect(isMapSettingsChange({ threshold: 0.5 })).toBe(true)
    expect(isMapSettingsChange({ showLineage: true })).toBe(true)
    expect(isMapSettingsChange({ query: 'cat' })).toBe(true)
    expect(isMapSettingsChange({ matchCount: 50 })).toBe(true)
    expect(isMapSettingsChange({ metric: 'rank' })).toBe(true)
  })

  it('選んだ画像とタブの切り替えは設定の変更ではない', () => {
    expect(isMapSettingsChange({})).toBe(false)
    expect(isMapSettingsChange({ selectedId: 'a1' })).toBe(false)
    expect(isMapSettingsChange({ view: 'umap3d' })).toBe(false)
    expect(isMapSettingsChange({ view: 'network', selectedId: null })).toBe(false)
    // 設定が混ざっていれば設定の変更。
    expect(isMapSettingsChange({ view: 'umap3d', k: 20 })).toBe(true)
  })
})
