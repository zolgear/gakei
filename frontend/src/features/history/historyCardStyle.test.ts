/**
 * 履歴のカードの形(詳細 / 画像)。既定は詳細で、'image' のときだけ画像の形になる。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  getHistoryCardStyle,
  parseHistoryCardStyle,
  setHistoryCardStyle,
  subscribeHistoryCardStyle,
} from './historyCardStyle'

afterEach(() => setHistoryCardStyle('detail'))

describe('parseHistoryCardStyle', () => {
  it('未設定(null)は既定の詳細', () => {
    expect(parseHistoryCardStyle(null)).toBe('detail')
  })

  it("'image' と 'detail' はそのまま", () => {
    expect(parseHistoryCardStyle('image')).toBe('image')
    expect(parseHistoryCardStyle('detail')).toBe('detail')
  })

  it('不正な値は既定の詳細', () => {
    expect(parseHistoryCardStyle('grid')).toBe('detail')
    expect(parseHistoryCardStyle('IMAGE')).toBe('detail')
    expect(parseHistoryCardStyle(1)).toBe('detail')
  })
})

describe('historyCardStyle のストア', () => {
  it('既定は詳細', () => {
    expect(getHistoryCardStyle()).toBe('detail')
  })

  it('変更を購読者に知らせ、解除後は知らせない', () => {
    const listener = vi.fn()
    const unsubscribe = subscribeHistoryCardStyle(listener)
    setHistoryCardStyle('image')
    expect(getHistoryCardStyle()).toBe('image')
    expect(listener).toHaveBeenCalledTimes(1)
    unsubscribe()
    setHistoryCardStyle('detail')
    expect(listener).toHaveBeenCalledTimes(1)
  })
})
