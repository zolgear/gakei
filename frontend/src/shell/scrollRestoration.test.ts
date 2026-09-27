import { describe, expect, it } from 'vitest'
import {
  MAX_SCROLL_ENTRIES,
  getScrollPosition,
  saveScrollPosition,
  scrollStoreKey,
  shouldRestoreScroll,
} from './scrollRestoration'

describe('shouldRestoreScroll', () => {
  it('POP のときだけ true', () => {
    expect(shouldRestoreScroll('POP')).toBe(true)
    expect(shouldRestoreScroll('PUSH')).toBe(false)
    expect(shouldRestoreScroll('REPLACE')).toBe(false)
  })
})

describe('scrollStoreKey', () => {
  it('namespace と location.key を : で連結する', () => {
    expect(scrollStoreKey('history', 'abc123')).toBe('history:abc123')
  })

  it('namespace が違えば別キーになる(コンテナ同士が衝突しない)', () => {
    expect(scrollStoreKey('main', 'abc123')).not.toBe(scrollStoreKey('history', 'abc123'))
  })
})

describe('saveScrollPosition / getScrollPosition', () => {
  it('保存した値を読み戻せる', () => {
    const store = new Map<string, number>()
    saveScrollPosition(store, 'k1', 120)
    expect(getScrollPosition(store, 'k1')).toBe(120)
  })

  it('存在しないキーは undefined', () => {
    const store = new Map<string, number>()
    expect(getScrollPosition(store, 'nope')).toBeUndefined()
  })

  it('同じキーへの再保存は値を上書きする', () => {
    const store = new Map<string, number>()
    saveScrollPosition(store, 'k1', 100)
    saveScrollPosition(store, 'k1', 200)
    expect(getScrollPosition(store, 'k1')).toBe(200)
    expect(store.size).toBe(1)
  })

  it('上限を超えたら最も古いものから捨てる(LRU)', () => {
    const store = new Map<string, number>()
    saveScrollPosition(store, 'a', 1, 2)
    saveScrollPosition(store, 'b', 2, 2)
    saveScrollPosition(store, 'c', 3, 2)
    expect(getScrollPosition(store, 'a')).toBeUndefined()
    expect(getScrollPosition(store, 'b')).toBe(2)
    expect(getScrollPosition(store, 'c')).toBe(3)
    expect(store.size).toBe(2)
  })

  it('既存キーへの再保存は「最近使った」扱いになり、削除対象から外れる', () => {
    const store = new Map<string, number>()
    saveScrollPosition(store, 'a', 1, 2)
    saveScrollPosition(store, 'b', 2, 2)
    // a を再保存(最近使った扱いに)
    saveScrollPosition(store, 'a', 10, 2)
    // c を追加すると、今度は b が最も古いので捨てられる
    saveScrollPosition(store, 'c', 3, 2)
    expect(getScrollPosition(store, 'a')).toBe(10)
    expect(getScrollPosition(store, 'b')).toBeUndefined()
    expect(getScrollPosition(store, 'c')).toBe(3)
  })

  it('既定の上限は MAX_SCROLL_ENTRIES', () => {
    const store = new Map<string, number>()
    for (let i = 0; i < MAX_SCROLL_ENTRIES + 5; i += 1) {
      saveScrollPosition(store, `k${i}`, i)
    }
    expect(store.size).toBe(MAX_SCROLL_ENTRIES)
  })
})
