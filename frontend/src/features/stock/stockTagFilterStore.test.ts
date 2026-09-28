import { afterEach, describe, expect, it, vi } from 'vitest'
import { getStockTagFilter, setStockTagFilter, subscribeStockTagFilter } from './stockTagFilterStore'

afterEach(() => setStockTagFilter(null))

describe('stockTagFilterStore', () => {
  it('設定と解除を購読者に知らせる(同じ値では知らせない)', () => {
    const listener = vi.fn()
    const unsubscribe = subscribeStockTagFilter(listener)
    setStockTagFilter('cat')
    setStockTagFilter('cat')
    expect(getStockTagFilter()).toBe('cat')
    expect(listener).toHaveBeenCalledTimes(1)
    setStockTagFilter('')
    expect(getStockTagFilter()).toBeNull()
    expect(listener).toHaveBeenCalledTimes(2)
    unsubscribe()
    setStockTagFilter('dog')
    expect(listener).toHaveBeenCalledTimes(2)
  })
})
