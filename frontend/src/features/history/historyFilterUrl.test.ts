/**
 * 履歴の絞り込みと URL のクエリの変換。
 */
import { describe, expect, it } from 'vitest'
import { historyFilterSearchParams, parseHistoryFilter } from './historyFilterUrl'

describe('parseHistoryFilter', () => {
  it('クエリが無ければ既定(すべて・お気に入りなし)', () => {
    expect(parseHistoryFilter(new URLSearchParams())).toEqual({ status: 'all', favoriteOnly: false })
  })

  it('状態とお気に入りを読む', () => {
    expect(parseHistoryFilter(new URLSearchParams('status=failed&favorite=1'))).toEqual({
      status: 'failed',
      favoriteOnly: true,
    })
    expect(parseHistoryFilter(new URLSearchParams('status=succeeded'))).toEqual({
      status: 'succeeded',
      favoriteOnly: false,
    })
  })

  it('不正な値は無視して既定にする', () => {
    expect(parseHistoryFilter(new URLSearchParams('status=all&favorite=true'))).toEqual({
      status: 'all',
      favoriteOnly: false,
    })
    expect(parseHistoryFilter(new URLSearchParams('status=FAILED&favorite=0'))).toEqual({
      status: 'all',
      favoriteOnly: false,
    })
  })
})

describe('historyFilterSearchParams', () => {
  it('既定値はクエリに書かない', () => {
    const params = historyFilterSearchParams(new URLSearchParams('status=failed&favorite=1'), {
      status: 'all',
      favoriteOnly: false,
    })
    expect(params.toString()).toBe('')
  })

  it('絞っている値だけを書き、ほかのキーは残す', () => {
    const before = new URLSearchParams('other=x')
    const params = historyFilterSearchParams(before, { status: 'failed', favoriteOnly: true })
    expect(params.get('status')).toBe('failed')
    expect(params.get('favorite')).toBe('1')
    expect(params.get('other')).toBe('x')
    // 元の値は変えない。
    expect(before.toString()).toBe('other=x')
  })

  it('読んだ値を書くと元に戻る', () => {
    for (const query of ['', 'status=succeeded', 'favorite=1', 'status=failed&favorite=1']) {
      const params = historyFilterSearchParams(new URLSearchParams(), parseHistoryFilter(new URLSearchParams(query)))
      expect(parseHistoryFilter(params)).toEqual(parseHistoryFilter(new URLSearchParams(query)))
    }
  })
})
