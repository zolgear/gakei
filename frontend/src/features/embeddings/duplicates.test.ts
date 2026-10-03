import { describe, expect, it } from 'vitest'
import {
  DUPLICATE_THRESHOLD_MAX,
  DUPLICATE_THRESHOLD_MIN,
  clampThreshold,
  compareTargets,
  formatScore,
  formatThreshold,
  toggleCompareSelection,
} from './duplicates'

describe('clampThreshold', () => {
  it('範囲に収め、0.01 刻みに丸める(浮動小数の端数を残さない)', () => {
    expect(clampThreshold(0.95)).toBe(0.95)
    expect(clampThreshold(0.904)).toBe(0.9)
    expect(clampThreshold(0.905)).toBe(0.91)
    expect(clampThreshold(0.1)).toBe(DUPLICATE_THRESHOLD_MIN)
    expect(clampThreshold(1.5)).toBe(DUPLICATE_THRESHOLD_MAX)
  })

  it('数でなければ null', () => {
    expect(clampThreshold(Number.NaN)).toBeNull()
    expect(clampThreshold(Number.POSITIVE_INFINITY)).toBeNull()
  })
})

describe('formatThreshold / formatScore', () => {
  it('しきい値は小数2桁、類似度は小数3桁', () => {
    expect(formatThreshold(0.9)).toBe('0.90')
    expect(formatScore(0.98765)).toBe('0.988')
    expect(formatScore(1)).toBe('1.000')
  })
})

describe('toggleCompareSelection', () => {
  it('選んでいなければ足し、選んでいれば外す', () => {
    expect(toggleCompareSelection([], 'a')).toEqual(['a'])
    expect(toggleCompareSelection(['a'], 'b')).toEqual(['a', 'b'])
    expect(toggleCompareSelection(['a', 'b'], 'a')).toEqual(['b'])
  })

  it('3枚目を選ぶと、いちばん前に選んだものを外す', () => {
    expect(toggleCompareSelection(['a', 'b'], 'c')).toEqual(['b', 'c'])
  })
})

describe('compareTargets', () => {
  it('2枚のグループは選ばなくてもその2枚', () => {
    expect(compareTargets(['a', 'b'], [])).toEqual(['a', 'b'])
  })

  it('3枚以上のグループは、そのグループの中で選んだ2枚(選んだ順)', () => {
    expect(compareTargets(['a', 'b', 'c'], ['c', 'a'])).toEqual(['c', 'a'])
  })

  it('ほかのグループで選んだ画像は数えない。2枚に満たなければ null', () => {
    expect(compareTargets(['a', 'b', 'c'], ['a', 'x'])).toBeNull()
    expect(compareTargets(['a', 'b', 'c'], [])).toBeNull()
  })
})
