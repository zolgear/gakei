import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { setLocale } from '../../i18n'
import {
  REPEAT_MAX,
  loadRepeatRaw,
  parseRepeat,
  repeatTotalText,
  saveRepeatRaw,
  submitButtonLabel,
} from './repeat'

describe('parseRepeat', () => {
  it('空欄は既定の 1', () => {
    expect(parseRepeat('')).toBe(1)
    expect(parseRepeat('  ')).toBe(1)
  })

  it('1〜20 の整数だけを受ける', () => {
    expect(parseRepeat('1')).toBe(1)
    expect(parseRepeat('5')).toBe(5)
    expect(parseRepeat(String(REPEAT_MAX))).toBe(20)
    expect(parseRepeat('0')).toBeNull()
    expect(parseRepeat('21')).toBeNull()
    expect(parseRepeat('-1')).toBeNull()
    expect(parseRepeat('2.5')).toBeNull()
    expect(parseRepeat('abc')).toBeNull()
  })
})

describe('submitButtonLabel', () => {
  afterEach(() => setLocale('ja'))

  it('1 回(と送れない値)はいつもの「生成」', () => {
    expect(submitButtonLabel(1)).toBe('生成')
    expect(submitButtonLabel(null)).toBe('生成')
  })

  it('2 回以上は回数を添える', () => {
    expect(submitButtonLabel(5)).toBe('生成 ×5')
    setLocale('en')
    expect(submitButtonLabel(3)).toBe('Generate ×3')
  })
})

describe('repeatTotalText', () => {
  it('1 回なら出さない(既存の表示のまま)', () => {
    expect(repeatTotalText(4, 1)).toBeNull()
    expect(repeatTotalText(4, null)).toBeNull()
  })

  it('1 回の枚数 × 繰り返し回数', () => {
    expect(repeatTotalText(6, 3)).toBe('合計 18 枚(1回 6 枚 × 3 回)')
    expect(repeatTotalText(1, 20)).toBe('合計 20 枚(1回 1 枚 × 20 回)')
  })
})

function makeMemoryStorage(): Storage {
  const store = new Map<string, string>()
  return {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => {
      store.set(key, value)
    },
    removeItem: (key: string) => {
      store.delete(key)
    },
    clear: () => store.clear(),
    key: (index: number) => Array.from(store.keys())[index] ?? null,
    get length() {
      return store.size
    },
  }
}

describe('loadRepeatRaw / saveRepeatRaw', () => {
  beforeEach(() => vi.stubGlobal('localStorage', makeMemoryStorage()))
  afterEach(() => vi.unstubAllGlobals())

  it('localStorage が使えなければ空欄(= 1)', () => {
    vi.stubGlobal('localStorage', undefined)
    expect(loadRepeatRaw()).toBe('')
    expect(() => saveRepeatRaw('3')).not.toThrow()
  })

  it('覚えた値を読み戻す。1 と空欄は覚えない', () => {
    expect(loadRepeatRaw()).toBe('')
    saveRepeatRaw('7')
    expect(loadRepeatRaw()).toBe('7')
    saveRepeatRaw('1')
    expect(loadRepeatRaw()).toBe('')
  })

  it('壊れた値・範囲外は空欄にする', () => {
    localStorage.setItem('gakei.runForm.repeat', '99')
    expect(loadRepeatRaw()).toBe('')
  })
})
