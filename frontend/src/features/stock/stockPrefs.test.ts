/**
 * 「スケッチとマスクをストックに出す」設定(ADR-0035)。既定はオフで、'true' のときだけオンになる。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  getStockShowSketchMask,
  parseStockShowSketchMaskPref,
  setStockShowSketchMask,
  subscribeStockShowSketchMask,
} from './stockPrefs'

afterEach(() => setStockShowSketchMask(false))

describe('parseStockShowSketchMaskPref', () => {
  it('未設定(null)は既定のオフ', () => {
    expect(parseStockShowSketchMaskPref(null)).toBe(false)
  })

  it("'true' だけがオン", () => {
    expect(parseStockShowSketchMaskPref('true')).toBe(true)
    expect(parseStockShowSketchMaskPref('false')).toBe(false)
  })

  it('不正な値は既定のオフ', () => {
    expect(parseStockShowSketchMaskPref('yes')).toBe(false)
    expect(parseStockShowSketchMaskPref(1)).toBe(false)
  })
})

describe('stockPrefs のストア', () => {
  it('既定はオフ', () => {
    expect(getStockShowSketchMask()).toBe(false)
  })

  it('変更を購読者に知らせ、解除後は知らせない', () => {
    const listener = vi.fn()
    const unsubscribe = subscribeStockShowSketchMask(listener)
    setStockShowSketchMask(true)
    expect(getStockShowSketchMask()).toBe(true)
    expect(listener).toHaveBeenCalledTimes(1)
    unsubscribe()
    setStockShowSketchMask(false)
    expect(listener).toHaveBeenCalledTimes(1)
  })
})
