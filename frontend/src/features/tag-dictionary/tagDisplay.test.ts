import { describe, expect, it } from 'vitest'
import { formatTagCount, tagCategoryTone } from './tagDisplay'

describe('formatTagCount', () => {
  it('1000 未満はそのまま', () => {
    expect(formatTagCount(0)).toBe('0')
    expect(formatTagCount(999)).toBe('999')
  })

  it('K と M で短くする', () => {
    expect(formatTagCount(1000)).toBe('1K')
    expect(formatTagCount(1234)).toBe('1.2K')
    expect(formatTagCount(45_000)).toBe('45K')
    expect(formatTagCount(45_678)).toBe('46K')
    expect(formatTagCount(1_234_567)).toBe('1.2M')
    expect(formatTagCount(12_345_678)).toBe('12M')
  })

  it('丸めて 1000 になるときは上の単位にする', () => {
    expect(formatTagCount(999_999)).toBe('1M')
  })

  it('負や数でない値は 0', () => {
    expect(formatTagCount(-5)).toBe('0')
    expect(formatTagCount(Number.NaN)).toBe('0')
  })
})

describe('tagCategoryTone', () => {
  it('Danbooru の番号を色の種類にする', () => {
    expect(tagCategoryTone('danbooru', 0)).toBe('general')
    expect(tagCategoryTone('danbooru', 1)).toBe('artist')
    expect(tagCategoryTone('danbooru', 3)).toBe('copyright')
    expect(tagCategoryTone('danbooru', 4)).toBe('character')
    expect(tagCategoryTone('danbooru', 5)).toBe('meta')
  })

  it('知らない番号やほかの体系は other', () => {
    expect(tagCategoryTone('danbooru', 2)).toBe('other')
    expect(tagCategoryTone('other', 0)).toBe('other')
    expect(tagCategoryTone(null, 1)).toBe('other')
  })

  it('番号が無ければ null', () => {
    expect(tagCategoryTone('danbooru', null)).toBeNull()
    expect(tagCategoryTone(undefined, undefined)).toBeNull()
  })
})
