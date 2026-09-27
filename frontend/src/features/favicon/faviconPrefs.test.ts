/**
 * `parseFaviconProgressPref` の変換規則(ADR-0009 8章)を確認する。'off' のときだけ false、
 * それ以外(未設定・不正な値)は既定のオン(true)になることを見る。
 */
import { describe, expect, it } from 'vitest'
import { parseFaviconProgressPref } from './faviconPrefs'

describe('parseFaviconProgressPref', () => {
  it('未設定(null)は既定のオンになる', () => {
    expect(parseFaviconProgressPref(null)).toBe(true)
  })

  it("'on' はオンになる", () => {
    expect(parseFaviconProgressPref('on')).toBe(true)
  })

  it("'off' だけがオフになる", () => {
    expect(parseFaviconProgressPref('off')).toBe(false)
  })

  it('不正な文字列は既定のオンになる', () => {
    expect(parseFaviconProgressPref('yes')).toBe(true)
  })

  it('文字列以外の値(数値)も既定のオンになる', () => {
    expect(parseFaviconProgressPref(0)).toBe(true)
  })
})
