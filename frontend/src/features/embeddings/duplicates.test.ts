import { describe, expect, it } from 'vitest'
import {
  DUPLICATE_THRESHOLD_MAX,
  DUPLICATE_THRESHOLD_MIN,
  clampThreshold,
  compareTargets,
  formatScore,
  formatThreshold,
  toggleCompareSelection,
  formatCompactDateTime,
  orderOldestFirst,
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

describe('orderOldestFirst', () => {
  const older = { id: 'a', created_at: '2026-10-03T10:00:00Z' }
  const newer = { id: 'b', created_at: '2026-10-03T12:00:00Z' }
  it('古い方を左にする', () => {
    expect(orderOldestFirst(newer, older)).toEqual([older, newer])
    expect(orderOldestFirst(older, newer)).toEqual([older, newer])
  })
  it('同じ日時や読めない日時は渡した順のまま', () => {
    const same = { id: 'c', created_at: older.created_at }
    expect(orderOldestFirst(same, older)).toEqual([same, older])
    expect(orderOldestFirst({ id: 'x', created_at: 'bad' }, older)[0].id).toBe('x')
  })
})

describe('formatCompactDateTime', () => {
  // ローカル時刻で作る(実行環境のタイムゾーンに依らない)。
  const local = (y: number, mo: number, d: number, h: number, mi: number) => new Date(y, mo - 1, d, h, mi, 30).toISOString()
  const now = new Date(2026, 9, 3, 23, 0)

  it('今年は年と秒を省く', () => {
    expect(formatCompactDateTime(local(2026, 10, 3, 21, 46), 'ja-JP', now)).toBe('10/3 21:46')
    expect(formatCompactDateTime(local(2026, 1, 9, 7, 5), 'ja-JP', now)).toBe('1/9 07:05')
    expect(formatCompactDateTime(local(2026, 10, 3, 21, 46), 'en-US', now)).toBe('10/3 21:46')
  })

  it('別の年は年を付ける', () => {
    expect(formatCompactDateTime(local(2025, 12, 31, 0, 1), 'ja-JP', now)).toBe('2025/12/31 00:01')
  })

  it('読めない値はそのまま', () => {
    expect(formatCompactDateTime('not-a-date', 'ja-JP', now)).toBe('not-a-date')
  })
})
