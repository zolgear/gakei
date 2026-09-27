import { describe, expect, it } from 'vitest'
import { removeItemFromPagedData, type InfiniteQueryData } from './queryCache'

interface Item {
  id: string
  name: string
}

function data(pages: { items: Item[]; next_cursor?: string | null }[]): InfiniteQueryData<Item> {
  return { pages, pageParams: pages.map(() => undefined) }
}

describe('removeItemFromPagedData', () => {
  it('undefined はそのまま undefined を返す(キャッシュ未取得時に備える)', () => {
    expect(removeItemFromPagedData<Item>(undefined, 'a')).toBeUndefined()
  })

  it('該当 id を1件のページから除く', () => {
    const input = data([{ items: [{ id: 'a', name: 'A' }, { id: 'b', name: 'B' }] }])
    const result = removeItemFromPagedData(input, 'a')
    expect(result?.pages[0].items).toEqual([{ id: 'b', name: 'B' }])
  })

  it('複数ページにまたがっていても該当ページだけ除く', () => {
    const input = data([
      { items: [{ id: 'a', name: 'A' }] },
      { items: [{ id: 'b', name: 'B' }, { id: 'c', name: 'C' }] },
    ])
    const result = removeItemFromPagedData(input, 'b')
    expect(result?.pages[0].items).toEqual([{ id: 'a', name: 'A' }])
    expect(result?.pages[1].items).toEqual([{ id: 'c', name: 'C' }])
  })

  it('存在しない id を指定しても変化しない(中身は空にならない)', () => {
    const input = data([{ items: [{ id: 'a', name: 'A' }] }])
    const result = removeItemFromPagedData(input, 'zzz')
    expect(result?.pages[0].items).toEqual([{ id: 'a', name: 'A' }])
  })

  it('元のオブジェクトを変更しない(純粋関数)', () => {
    const input = data([{ items: [{ id: 'a', name: 'A' }] }])
    removeItemFromPagedData(input, 'a')
    expect(input.pages[0].items).toHaveLength(1)
  })
})
