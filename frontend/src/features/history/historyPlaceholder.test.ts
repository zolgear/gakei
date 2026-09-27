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
})
