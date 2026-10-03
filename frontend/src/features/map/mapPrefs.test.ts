import { describe, expect, it } from 'vitest'
import type { StorageLike } from '../../lib/browserStorage'
import { memoryStorage } from '../../lib/memoryStorage'
import {
  MAP_PREFS_STORAGE_KEY,
  loadMapPrefs,
  parseMapPrefs,
  pickStoredFields,
  saveMapPrefs,
  serializeMapPrefs,
} from './mapPrefs'
import { DEFAULT_MAP_URL_STATE, buildMapSearchParams, parseMapUrlState } from './mapUrlState'

describe('parseMapPrefs', () => {
  it('無い・壊れている・版が違うときは空', () => {
    expect(parseMapPrefs(null)).toEqual({})
    expect(parseMapPrefs('{')).toEqual({})
    expect(parseMapPrefs('[]')).toEqual({})
    expect(parseMapPrefs('{"v":2,"k":20}')).toEqual({})
    expect(parseMapPrefs('{"k":20}')).toEqual({})
  })

  it('項目ごとに検証し、読めない項目だけを捨てる', () => {
    expect(parseMapPrefs('{"v":1,"view":"map","k":20,"limit":2000,"threshold":0.9,"lineage":true}')).toEqual({
      view: 'umap',
      k: 20,
      limit: 2000,
      threshold: 0.9,
      showLineage: true,
    })
    expect(parseMapPrefs('{"v":1,"view":"x","k":7,"limit":"500","threshold":"0.9","lineage":1}')).toEqual({})
  })

  it('しきい値は範囲に収めて丸める', () => {
    expect(parseMapPrefs('{"v":1,"threshold":2}').threshold).toBe(0.99)
    expect(parseMapPrefs('{"v":1,"threshold":0.8349}').threshold).toBe(0.83)
  })

  it('パネルの開閉は真偽値だけを読む(無い・読めないときは閉じたまま)', () => {
    expect(parseMapPrefs('{"v":1,"previewOpen":true}')).toEqual({ previewOpen: true })
    expect(parseMapPrefs('{"v":1,"previewOpen":false}')).toEqual({ previewOpen: false })
    expect(parseMapPrefs('{"v":1,"previewOpen":"true"}')).toEqual({})
    expect(parseMapPrefs('{"v":1,"previewOpen":1}')).toEqual({})
    // 項目を足す前に覚えた値も、そのまま読める。
    expect(parseMapPrefs('{"v":1,"k":20}')).toEqual({ k: 20 })
  })

  it('書いたものを読み戻せる', () => {
    const prefs = { view: 'network' as const, k: 5, limit: 500, threshold: 0.7, showLineage: false, previewOpen: true }
    expect(parseMapPrefs(serializeMapPrefs(prefs))).toEqual(prefs)
  })
})

describe('pickStoredFields', () => {
  it('グループ・タグ・選んだ画像は覚えない', () => {
    expect(pickStoredFields({ groupId: 'g', tags: ['a'], selectedId: 's', k: 15 })).toEqual({ k: 15 })
    expect(pickStoredFields({ selectedId: null })).toEqual({})
    expect(pickStoredFields({ previewOpen: false })).toEqual({ previewOpen: false })
  })
})

describe('loadMapPrefs / saveMapPrefs', () => {
  it('変えた項目だけを覚えた値に重ねて書く', () => {
    const storage = memoryStorage()
    const first = saveMapPrefs({}, { k: 20, groupId: 'g' }, storage)
    expect(first).toEqual({ k: 20 })
    const second = saveMapPrefs(first, { threshold: 0.9 }, storage)
    expect(second).toEqual({ k: 20, threshold: 0.9 })
    expect(JSON.parse(storage.getItem(MAP_PREFS_STORAGE_KEY) ?? '')).toEqual({ v: 1, k: 20, threshold: 0.9 })
    expect(loadMapPrefs(storage)).toEqual({ k: 20, threshold: 0.9 })
  })

  it('パネルの開閉も、ほかの覚えた値を残したまま重ねて書く', () => {
    const storage = memoryStorage()
    const prefs = saveMapPrefs({}, { k: 20 }, storage)
    const opened = saveMapPrefs(prefs, { previewOpen: true }, storage)
    expect(opened).toEqual({ k: 20, previewOpen: true })
    expect(loadMapPrefs(storage)).toEqual({ k: 20, previewOpen: true })
    // URL の状態を変えても、パネルの開閉は消えない。
    const changed = saveMapPrefs(opened, { threshold: 0.9, selectedId: 'a' }, storage)
    expect(loadMapPrefs(storage)).toEqual({ k: 20, threshold: 0.9, previewOpen: true })
    saveMapPrefs(changed, { previewOpen: false }, storage)
    expect(loadMapPrefs(storage)).toEqual({ k: 20, threshold: 0.9, previewOpen: false })
  })

  it('ストレージが使えなくても例外にせず、結果は返す', () => {
    const broken: StorageLike = {
      length: 0,
      key: () => null,
      getItem: () => {
        throw new Error('SecurityError')
      },
      setItem: () => {
        throw new Error('QuotaExceededError')
      },
      removeItem: () => {},
    }
    expect(loadMapPrefs(broken)).toEqual({})
    expect(saveMapPrefs({}, { k: 5 }, broken)).toEqual({ k: 5 })
    expect(loadMapPrefs(null)).toEqual({})
    expect(saveMapPrefs({}, { k: 5 }, null)).toEqual({ k: 5 })
  })
})

describe('URL > 覚えた値 > 既定', () => {
  const stored = { view: 'umap' as const, k: 20, limit: 2000, threshold: 0.9, showLineage: true }

  it('URL に無い項目は覚えた値を使う', () => {
    const s = parseMapUrlState(new URLSearchParams(''), stored)
    expect(s).toEqual({ ...DEFAULT_MAP_URL_STATE, ...stored })
  })

  it('URL にある項目は URL を使う', () => {
    const s = parseMapUrlState(new URLSearchParams('view=network&k=5&limit=500&th=0.6&lineage=0'), stored)
    expect(s).toMatchObject({ view: 'network', k: 5, limit: 500, threshold: 0.6, showLineage: false })
  })

  it('URL の値が読めないときは覚えた値', () => {
    const s = parseMapUrlState(new URLSearchParams('view=x&k=7&th=abc'), stored)
    expect(s).toMatchObject({ view: 'umap', k: 20, threshold: 0.9 })
  })

  it('覚えた値が無ければ既定', () => {
    expect(parseMapUrlState(new URLSearchParams(''), {})).toEqual(DEFAULT_MAP_URL_STATE)
  })

  it('既定に戻して覚えた値も既定にすると、URL に無くても既定になる', () => {
    const next = saveMapPrefs(stored, { k: DEFAULT_MAP_URL_STATE.k, showLineage: false }, null)
    const qs = buildMapSearchParams({ ...parseMapUrlState(new URLSearchParams(''), stored), k: 10, showLineage: false })
    expect(qs.has('k')).toBe(false)
    expect(parseMapUrlState(qs, next)).toMatchObject({ k: 10, showLineage: false })
  })
})
