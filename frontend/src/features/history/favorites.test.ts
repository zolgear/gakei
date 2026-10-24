import { describe, expect, it } from 'vitest'
import type { RunSummary } from '../../api/client'
import { collectFavoriteIds, setFavoriteInCache, shownWhileFavoriteOnly } from './favorites'
import { isFavoriteFilteredKey } from './useFavoriteToggle'

function run(id: string, outputs: { asset_id: string; favorite?: boolean }[]): RunSummary {
  return {
    id,
    status: 'succeeded',
    outputs: outputs.map((o, i) => ({
      asset_id: o.asset_id,
      output_index: i,
      content_withheld: false,
      favorite: o.favorite ?? false,
    })),
  } as unknown as RunSummary
}

describe('setFavoriteInCache', () => {
  it('Run の一覧のページの、該当の出力だけを書き換える', () => {
    const untouched = run('r2', [{ asset_id: 'c' }])
    const data = {
      pages: [{ items: [run('r1', [{ asset_id: 'a' }, { asset_id: 'b' }])] }, { items: [untouched] }],
      pageParams: [undefined, 'x'],
    }
    const next = setFavoriteInCache(data, 'b', true)
    expect(next.pages[0].items[0].outputs?.map((o) => o.favorite)).toEqual([false, true])
    // 該当の無いページは同じ参照のまま。
    expect(next.pages[1]).toBe(data.pages[1])
    expect(next.pageParams).toBe(data.pageParams)
  })

  it('画像単位の履歴の項目(run の中)も書き換える', () => {
    const data = {
      pages: [{ items: [{ asset_id: 'a', group_id: null, run: run('r1', [{ asset_id: 'a', favorite: true }]) }] }],
      pageParams: [undefined],
    }
    const next = setFavoriteInCache(data, 'a', false)
    expect(next.pages[0].items[0].run.outputs?.[0].favorite).toBe(false)
  })

  it('変わらなければ同じ参照を返し、形の違う値はそのまま', () => {
    const data = { pages: [{ items: [run('r1', [{ asset_id: 'a', favorite: true }])] }], pageParams: [undefined] }
    expect(setFavoriteInCache(data, 'a', true)).toBe(data)
    expect(setFavoriteInCache(data, 'zzz', true)).toBe(data)
    const counts = { running: 1, queued: 0 }
    expect(setFavoriteInCache(counts, 'a', true)).toBe(counts)
    expect(setFavoriteInCache(undefined, 'a', true)).toBeUndefined()
  })
})

describe('setFavoriteInCache(ストックの一覧)', () => {
  it('AssetSummary の項目は id が合うものだけを書き換え、変わらなければ同じ参照', () => {
    const a = { id: 'a', kind: 'generated', mime: 'image/png', favorite: false }
    const b = { id: 'b', kind: 'upload', mime: 'image/png', favorite: false }
    const data = { pages: [{ items: [a, b] }], pageParams: [undefined] }
    const next = setFavoriteInCache(data, 'a', true)
    expect(next.pages[0].items[0]).toEqual({ ...a, favorite: true })
    expect(next.pages[0].items[1]).toBe(b)
    expect(setFavoriteInCache(next, 'a', true)).toBe(next)
    expect(setFavoriteInCache(data, 'z', true)).toBe(data)
  })
})

describe('setFavoriteInCache の1件の形', () => {
  it('Asset 1件(AssetDetail)は id が合うときだけ favorite を書き換える', () => {
    const detail = { id: 'a', sha256: 'x', favorite: false }
    expect(setFavoriteInCache(detail, 'a', true)).toEqual({ id: 'a', sha256: 'x', favorite: true })
    expect(setFavoriteInCache(detail, 'a', false)).toBe(detail)
    expect(setFavoriteInCache(detail, 'b', true)).toBe(detail)
  })

  it('Run 1件(RunDetail)は出力を書き換える', () => {
    const detail = run('r1', [{ asset_id: 'a' }, { asset_id: 'b' }])
    const next = setFavoriteInCache(detail, 'a', true)
    expect(next.outputs?.map((o) => o.favorite)).toEqual([true, false])
    expect(setFavoriteInCache(detail, 'zzz', true)).toBe(detail)
  })
})

describe('絞り込み中の表示', () => {
  it('一度お気に入りとして見えた出力は、外しても残す', () => {
    const first = collectFavoriteIds(new Set(), [run('r1', [{ asset_id: 'a', favorite: true }, { asset_id: 'b' }])])
    expect([...first]).toEqual(['a'])
    expect(shownWhileFavoriteOnly({ asset_id: 'a', favorite: false }, first)).toBe(true)
    expect(shownWhileFavoriteOnly({ asset_id: 'b', favorite: false }, first)).toBe(false)
    expect(shownWhileFavoriteOnly({ asset_id: 'b', favorite: true }, first)).toBe(true)
    expect(shownWhileFavoriteOnly(undefined, first)).toBe(false)
  })

  it('増えなければ同じ集合を返す', () => {
    const prev = new Set(['a'])
    expect(collectFavoriteIds(prev, [run('r1', [{ asset_id: 'a', favorite: true }])])).toBe(prev)
  })
})

describe('isFavoriteFilteredKey', () => {
  it('キーに { favorite: true } を含む一覧だけ', () => {
    expect(isFavoriteFilteredKey(['runs', { favorite: true }])).toBe(true)
    expect(isFavoriteFilteredKey(['history-assets', 'group', { favorite: true }])).toBe(true)
    expect(isFavoriteFilteredKey(['runs'])).toBe(false)
    expect(isFavoriteFilteredKey(['runs', 'queue-status'])).toBe(false)
    expect(isFavoriteFilteredKey(['history-assets', 'group'])).toBe(false)
  })
})
