import { describe, expect, it } from 'vitest'
import { shouldAutoFetchNextPage } from './sentinel'

const base = {
  isIntersecting: true,
  hasNextPage: true,
  isFetchingNextPage: false,
  isFetchNextPageError: false,
}

describe('shouldAutoFetchNextPage', () => {
  it('番兵が見えていて次ページがあり、取得中でも失敗中でもなければ true', () => {
    expect(shouldAutoFetchNextPage(base)).toBe(true)
  })

  it('番兵が見えていなければ false', () => {
    expect(shouldAutoFetchNextPage({ ...base, isIntersecting: false })).toBe(false)
  })

  it('次ページが無ければ false', () => {
    expect(shouldAutoFetchNextPage({ ...base, hasNextPage: false })).toBe(false)
  })

  it('取得中は二重に呼ばない', () => {
    expect(shouldAutoFetchNextPage({ ...base, isFetchingNextPage: true })).toBe(false)
  })

  it('直前の取得が失敗していれば自動では再試行しない', () => {
    expect(shouldAutoFetchNextPage({ ...base, isFetchNextPageError: true })).toBe(false)
  })
})
