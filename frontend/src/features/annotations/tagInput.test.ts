import { describe, expect, it } from 'vitest'
import {
  TAG_NAME_MAX,
  TITLE_MAX,
  checkTagInput,
  filterTagSuggestions,
  isTitleTooLong,
  moveSuggestionIndex,
  normalizeTagName,
  normalizeTitleInput,
} from './tagInput'

describe('normalizeTagName', () => {
  it('NFKC・空白の圧縮・前後の空白除去・小文字化をする', () => {
    expect(normalizeTagName('  Blue　 Sky  ')).toBe('blue sky')
    expect(normalizeTagName('ＡＢＣ')).toBe('abc')
    expect(normalizeTagName('ｶﾞｷﾞ')).toBe('ガギ')
  })
})

describe('checkTagInput', () => {
  const existing = [{ name: 'cat' }, { name: 'blue sky' }]

  it('空白だけは empty', () => {
    expect(checkTagInput('   ', existing)).toEqual({ ok: false, reason: 'empty' })
  })

  it('上限を超えると tooLong', () => {
    expect(checkTagInput('a'.repeat(TAG_NAME_MAX + 1), [])).toEqual({ ok: false, reason: 'tooLong' })
    expect(checkTagInput('a'.repeat(TAG_NAME_MAX), [])).toEqual({ ok: true, name: 'a'.repeat(TAG_NAME_MAX) })
  })

  it('正規化した名前で重複を判定する', () => {
    expect(checkTagInput('  CAT ', existing)).toEqual({ ok: false, reason: 'duplicate' })
    expect(checkTagInput('Blue  Sky', existing)).toEqual({ ok: false, reason: 'duplicate' })
  })

  it('新しいタグは正規化した名前を返す', () => {
    expect(checkTagInput(' Dog ', existing)).toEqual({ ok: true, name: 'dog' })
  })
})

describe('normalizeTitleInput / isTitleTooLong', () => {
  it('空なら null(タイトルを消す)', () => {
    expect(normalizeTitleInput('   ')).toBeNull()
    expect(normalizeTitleInput('')).toBeNull()
  })

  it('改行や空白の連続を 1 つにする(大文字小文字は保つ)', () => {
    expect(normalizeTitleInput(' 夕暮れの\n  Street ')).toBe('夕暮れの Street')
  })

  it('正規化した長さで上限を判定する', () => {
    expect(isTitleTooLong(`  ${'あ'.repeat(TITLE_MAX)}  `)).toBe(false)
    expect(isTitleTooLong('あ'.repeat(TITLE_MAX + 1))).toBe(true)
  })
})

describe('filterTagSuggestions', () => {
  it('付いているタグを除き、並びを保って先頭 limit 件', () => {
    const items = [
      { name: 'cat', count: 9 },
      { name: 'dog', count: 5 },
      { name: 'bird', count: 3 },
      { name: 'fish', count: 1 },
    ]
    expect(filterTagSuggestions(items, [{ name: 'Cat' }], 2).map((i) => i.name)).toEqual(['dog', 'bird'])
  })
})

describe('moveSuggestionIndex', () => {
  it('未選択から ↓ は先頭、↑ は末尾', () => {
    expect(moveSuggestionIndex(-1, 3, 1)).toBe(0)
    expect(moveSuggestionIndex(-1, 3, -1)).toBe(2)
  })

  it('端で循環する', () => {
    expect(moveSuggestionIndex(2, 3, 1)).toBe(0)
    expect(moveSuggestionIndex(0, 3, -1)).toBe(2)
  })

  it('候補が無ければ -1', () => {
    expect(moveSuggestionIndex(0, 0, 1)).toBe(-1)
  })
})
