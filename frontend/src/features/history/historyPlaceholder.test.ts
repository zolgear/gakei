import { describe, expect, it } from 'vitest'
import { historyPlaceholder } from './historyPlaceholder'

const base = {
  isLoading: false,
  isError: false,
  hasData: true,
  total: 0,
  filteredCount: 0,
  filterActive: false,
}

describe('historyPlaceholder', () => {
  it('読み込み中', () => {
    expect(historyPlaceholder({ ...base, isLoading: true, hasData: false })).toBe('loading')
  })

  it('履歴が0件(初回起動)なら何も出さない', () => {
    expect(historyPlaceholder(base)).toBeNull()
  })

  it('0件で絞り込み中でも何も出さない', () => {
    expect(historyPlaceholder({ ...base, filterActive: true })).toBeNull()
  })

  it('一度も取得できずに失敗したときだけエラー', () => {
    expect(historyPlaceholder({ ...base, isError: true, hasData: false })).toBe('error')
  })

  it('再取得に失敗しても、取得済みの一覧があればエラーを出さない', () => {
    expect(historyPlaceholder({ ...base, isError: true, total: 3, filteredCount: 3 })).toBeNull()
  })

  it('絞り込みで0件になったら該当なし', () => {
    expect(
      historyPlaceholder({ ...base, total: 3, filteredCount: 0, filterActive: true }),
    ).toBe('noMatch')
  })

  it('お気に入りで絞り込んで1件も無ければ、お気に入りが無いことを出す', () => {
    expect(historyPlaceholder({ ...base, favoriteOnly: true })).toBe('noFavorites')
    expect(historyPlaceholder({ ...base, favoriteOnly: true, filterActive: true })).toBe('noFavorites')
  })

  it('お気に入りはあるが状態の絞り込みで0件なら該当なし', () => {
    expect(
      historyPlaceholder({ ...base, favoriteOnly: true, filterActive: true, total: 2, filteredCount: 0 }),
    ).toBe('noMatch')
  })

  it('形・透過・領域で絞って1件も無ければ該当なし(お気に入りと一緒でも)', () => {
    expect(historyPlaceholder({ ...base, labelFiltered: true, filterActive: true })).toBe('noMatch')
    expect(
      historyPlaceholder({ ...base, labelFiltered: true, favoriteOnly: true, filterActive: true }),
    ).toBe('noMatch')
  })
})
