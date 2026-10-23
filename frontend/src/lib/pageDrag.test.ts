import { describe, expect, it, vi } from 'vitest'
import { insertsUrlIntoTextField, installPageDragTracking, isDragFromThisPage } from './pageDrag'

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

describe('insertsUrlIntoTextField', () => {
  it('リンク・画像・アプリの Asset は、入力欄に URL が入ってしまうものとみなす', () => {
    expect(insertsUrlIntoTextField(['text/uri-list', 'text/plain'])).toBe(true)
    expect(insertsUrlIntoTextField(['Files'])).toBe(true)
    expect(insertsUrlIntoTextField(['application/x-gakei-asset-id', 'text/plain'])).toBe(true)
  })

  it('入力欄の中の文字を選んで動かすドラッグは止めない', () => {
    expect(insertsUrlIntoTextField(['text/plain'])).toBe(false)
    expect(insertsUrlIntoTextField(['text/plain', 'text/html'])).toBe(false)
  })
})
