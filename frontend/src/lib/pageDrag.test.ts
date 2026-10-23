import { describe, expect, it, vi } from 'vitest'
import { installPageDragTracking, isDragFromThisPage } from './pageDrag'

describe('pageDrag', () => {
  it('ページの中で始まったドラッグを、終わるまで覚える', () => {
    vi.useFakeTimers()
    const target = new EventTarget() as unknown as Document
    installPageDragTracking(target)
    expect(isDragFromThisPage()).toBe(false)
    target.dispatchEvent(new Event('dragstart'))
    expect(isDragFromThisPage()).toBe(true)
    target.dispatchEvent(new Event('dragend'))
    expect(isDragFromThisPage()).toBe(false)

    target.dispatchEvent(new Event('dragstart'))
    target.dispatchEvent(new Event('drop'))
    // drop の処理の間はまだ覚えている
    expect(isDragFromThisPage()).toBe(true)
    vi.runAllTimers()
    expect(isDragFromThisPage()).toBe(false)
    vi.useRealTimers()
  })
})
