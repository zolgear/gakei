import { describe, expect, it, vi } from 'vitest'
import {
  backToTopBehavior,
  onHistoryScrollTopRequest,
  requestHistoryScrollTop,
  shouldShowBackToTop,
} from './backToTop'

describe('shouldShowBackToTop', () => {
  it('1画面分以上下へ進んだら出す', () => {
    expect(shouldShowBackToTop(0, 800)).toBe(false)
    expect(shouldShowBackToTop(799, 800)).toBe(false)
    expect(shouldShowBackToTop(800, 800)).toBe(true)
    expect(shouldShowBackToTop(5000, 800)).toBe(true)
  })

  it('高さが分からないうちは出さない', () => {
    expect(shouldShowBackToTop(100, 0)).toBe(false)
  })
})

describe('backToTopBehavior', () => {
  it('動きを減らす設定では即時に戻す', () => {
    expect(backToTopBehavior(false)).toBe('smooth')
    expect(backToTopBehavior(true)).toBe('auto')
  })
})

describe('requestHistoryScrollTop', () => {
  it('購読している間だけ知らせる', () => {
    const listener = vi.fn()
    const unsubscribe = onHistoryScrollTopRequest(listener)
    requestHistoryScrollTop()
    expect(listener).toHaveBeenCalledTimes(1)
    unsubscribe()
    requestHistoryScrollTop()
    expect(listener).toHaveBeenCalledTimes(1)
  })
})
