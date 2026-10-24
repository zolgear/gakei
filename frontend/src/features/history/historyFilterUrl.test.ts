/**
 * 履歴の絞り込みと URL のクエリの変換。
 */
import { describe, expect, it } from 'vitest'
import {
  DEFAULT_HISTORY_FILTER,
  hasLabelFilter,
  historyFilterSearchParams,
  historyServerFilter,
  parseHistoryFilter,
} from './historyFilterUrl'

describe('parseHistoryFilter', () => {
  it('クエリが無ければ既定(すべて・絞らない)', () => {
    expect(parseHistoryFilter(new URLSearchParams())).toEqual(DEFAULT_HISTORY_FILTER)
  })

  it('状態・お気に入り・形・透過・領域を読む', () => {
    expect(
      parseHistoryFilter(new URLSearchParams('status=failed&favorite=1&shape=portrait&transparent=1&regions=1')),
    ).toEqual({ status: 'failed', favoriteOnly: true, shape: 'portrait', transparentOnly: true, regionsOnly: true })
    expect(parseHistoryFilter(new URLSearchParams('status=succeeded&shape=wide'))).toEqual({
      ...DEFAULT_HISTORY_FILTER,
      status: 'succeeded',
      shape: 'wide',
    })
  })

  it('不正な値は無視して既定にする', () => {
    expect(
      parseHistoryFilter(new URLSearchParams('status=all&favorite=true&shape=tall&transparent=yes&regions=0')),
    ).toEqual(DEFAULT_HISTORY_FILTER)
    expect(parseHistoryFilter(new URLSearchParams('status=FAILED&favorite=0&shape=SQUARE'))).toEqual(
      DEFAULT_HISTORY_FILTER,
    )
  })
})

describe('historyFilterSearchParams', () => {
  it('既定値はクエリに書かない', () => {
    const params = historyFilterSearchParams(
      new URLSearchParams('status=failed&favorite=1&shape=square&transparent=1&regions=1'),
      DEFAULT_HISTORY_FILTER,
    )
    expect(params.toString()).toBe('')
  })

  it('絞っている値だけを書き、ほかのキーは残す', () => {
    const before = new URLSearchParams('other=x')
    const params = historyFilterSearchParams(before, {
      status: 'failed',
      favoriteOnly: true,
      shape: 'square',
      transparentOnly: true,
      regionsOnly: false,
    })
    expect(params.get('status')).toBe('failed')
    expect(params.get('favorite')).toBe('1')
    expect(params.get('shape')).toBe('square')
    expect(params.get('transparent')).toBe('1')
    expect(params.has('regions')).toBe(false)
    expect(params.get('other')).toBe('x')
    // 元の値は変えない。
    expect(before.toString()).toBe('other=x')
  })

  it('読んだ値を書くと元に戻る', () => {
    for (const query of [
      '',
      'status=succeeded',
      'favorite=1',
      'status=failed&favorite=1',
      'shape=wide&regions=1',
      'transparent=1&shape=square',
    ]) {
      const params = historyFilterSearchParams(new URLSearchParams(), parseHistoryFilter(new URLSearchParams(query)))
      expect(parseHistoryFilter(params)).toEqual(parseHistoryFilter(new URLSearchParams(query)))
    }
  })
})

describe('historyServerFilter', () => {
  it('絞らないものは省く(状態はクライアントで絞るので入れない)', () => {
    expect(historyServerFilter(DEFAULT_HISTORY_FILTER)).toEqual({})
    expect(historyServerFilter({ ...DEFAULT_HISTORY_FILTER, status: 'failed' })).toEqual({})
    expect(
      historyServerFilter({
        status: 'all',
        favoriteOnly: true,
        shape: 'portrait',
        transparentOnly: true,
        regionsOnly: true,
      }),
    ).toEqual({ favorite: true, shape: 'portrait', transparent: true, regions: true })
  })

  it('hasLabelFilter は形・透過・領域のどれかで真', () => {
    expect(hasLabelFilter(DEFAULT_HISTORY_FILTER)).toBe(false)
    expect(hasLabelFilter({ ...DEFAULT_HISTORY_FILTER, favoriteOnly: true })).toBe(false)
    expect(hasLabelFilter({ ...DEFAULT_HISTORY_FILTER, shape: 'square' })).toBe(true)
    expect(hasLabelFilter({ ...DEFAULT_HISTORY_FILTER, transparentOnly: true })).toBe(true)
    expect(hasLabelFilter({ ...DEFAULT_HISTORY_FILTER, regionsOnly: true })).toBe(true)
  })
})
