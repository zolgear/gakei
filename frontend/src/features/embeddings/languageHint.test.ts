import { describe, expect, it } from 'vitest'
import { containsJapanese, isEnglishOnlyModel, shouldShowEnglishOnlyHint } from './languageHint'

describe('containsJapanese', () => {
  it('ひらがな・カタカナ・漢字・半角カナを含めば true', () => {
    expect(containsJapanese('ねこ')).toBe(true)
    expect(containsJapanese('ネコ')).toBe(true)
    expect(containsJapanese('猫')).toBe(true)
    expect(containsJapanese('ﾈｺ')).toBe(true)
    expect(containsJapanese('a cat on 海辺')).toBe(true)
  })

  it('英数字・記号・全角英数だけなら false', () => {
    expect(containsJapanese('a cat on the beach')).toBe(false)
    expect(containsJapanese('123 !?')).toBe(false)
    expect(containsJapanese('ＡＢＣ')).toBe(false)
    expect(containsJapanese('')).toBe(false)
  })
})

describe('isEnglishOnlyModel', () => {
  it('multilingual が分かればそれに従う', () => {
    expect(isEnglishOnlyModel({ multilingual: false })).toBe(true)
    expect(isEnglishOnlyModel({ multilingual: true, languages: ['en'] })).toBe(false)
  })

  it('multilingual が無ければ languages で判定する', () => {
    expect(isEnglishOnlyModel({ languages: ['en'] })).toBe(true)
    expect(isEnglishOnlyModel({ languages: ['ja', 'en'] })).toBe(false)
  })

  it('リモート(どちらも null)は分からないので false', () => {
    expect(isEnglishOnlyModel({ multilingual: null, languages: null })).toBe(false)
    expect(isEnglishOnlyModel({ languages: [] })).toBe(false)
  })
})

describe('shouldShowEnglishOnlyHint', () => {
  it('英語向けのモデルで日本語を含む検索のときだけ出す', () => {
    expect(shouldShowEnglishOnlyHint('海辺の猫', { multilingual: false, languages: ['en'] })).toBe(true)
    expect(shouldShowEnglishOnlyHint('cat', { multilingual: false, languages: ['en'] })).toBe(false)
    expect(shouldShowEnglishOnlyHint('海辺の猫', { multilingual: true, languages: ['ja', 'en'] })).toBe(false)
    expect(shouldShowEnglishOnlyHint('海辺の猫', { multilingual: null, languages: null })).toBe(false)
    expect(shouldShowEnglishOnlyHint('海辺の猫', null)).toBe(false)
  })
})
