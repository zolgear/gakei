/**
 * 履歴の並べ替えの保存値(ADR-0054 5章)。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  effectiveHistorySort,
  getHistorySort,
  parseHistorySort,
  setHistorySort,
  subscribeHistorySort,
} from './historySort'

afterEach(() => setHistorySort('date'))

describe('parseHistorySort', () => {
  it('group と embedding はそのまま、それ以外は日付', () => {
    expect(parseHistorySort('group')).toBe('group')
    expect(parseHistorySort('embedding')).toBe('embedding')
    expect(parseHistorySort('date')).toBe('date')
    expect(parseHistorySort(null)).toBe('date')
    expect(parseHistorySort('random')).toBe('date')
  })
})

describe('effectiveHistorySort', () => {
  it('似た順は埋め込みが使えないと日付になる(分かるまでは待つ)', () => {
    expect(effectiveHistorySort('embedding', { available: true, loading: false })).toBe('embedding')
    expect(effectiveHistorySort('embedding', { available: false, loading: false })).toBe('date')
    expect(effectiveHistorySort('embedding', { available: false, loading: true })).toBe('embedding')
    expect(effectiveHistorySort('group', { available: false, loading: false })).toBe('group')
  })

  it('一覧が 409 を返したら、使えると言われていても日付で見せる', () => {
    expect(effectiveHistorySort('embedding', { available: true, loading: false, rejected: true })).toBe('date')
    expect(effectiveHistorySort('group', { available: true, loading: false, rejected: true })).toBe('group')
    expect(effectiveHistorySort('embedding', { available: true, loading: false, rejected: false })).toBe(
      'embedding',
    )
  })
})

describe('historySort のストア', () => {
  it('既定は日付で、変更を購読者に知らせる', () => {
    expect(getHistorySort()).toBe('date')
    const listener = vi.fn()
    const unsubscribe = subscribeHistorySort(listener)
    setHistorySort('group')
    expect(getHistorySort()).toBe('group')
    expect(listener).toHaveBeenCalledTimes(1)
    unsubscribe()
  })
})
