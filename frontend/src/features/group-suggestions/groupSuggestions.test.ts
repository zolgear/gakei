import { describe, expect, it } from 'vitest'
import {
  COVER_LIMIT,
  acceptBody,
  candidatesById,
  chosenMemberIds,
  formatRate,
  hiddenMemberCount,
  movingCount,
  namingSuggestionIds,
  shownMemberIds,
  toggleExcluded,
} from './groupSuggestions'

const ids = Array.from({ length: 15 }, (_, i) => `a${i}`)

describe('shownMemberIds / hiddenMemberCount', () => {
  it('閉じているときは先頭の表紙だけ', () => {
    expect(shownMemberIds(ids, false)).toEqual(ids.slice(0, COVER_LIMIT))
    expect(hiddenMemberCount(ids, false)).toBe(3)
  })
  it('開くと全部', () => {
    expect(shownMemberIds(ids, true)).toEqual(ids)
    expect(hiddenMemberCount(ids, true)).toBe(0)
  })
  it('表紙に収まるなら残りは無い', () => {
    expect(hiddenMemberCount(['a', 'b'], false)).toBe(0)
  })
})

describe('toggleExcluded / chosenMemberIds', () => {
  it('外して戻せる', () => {
    const once = toggleExcluded([], 'a1')
    expect(once).toEqual(['a1'])
    expect(chosenMemberIds(['a0', 'a1', 'a2'], once)).toEqual(['a0', 'a2'])
    expect(toggleExcluded(once, 'a1')).toEqual([])
  })
})

describe('acceptBody', () => {
  const fresh = { member_ids: ['a', 'b', 'c'], target_group: null }
  it('新しいグループは名前が要る', () => {
    expect(acceptBody(fresh, '  ', [])).toBeNull()
    expect(acceptBody(fresh, ' 海 ', ['b'])).toEqual({ asset_ids: ['a', 'c'], name: '海' })
    expect(acceptBody(fresh, 'x'.repeat(101), [])).toBeNull()
  })
  it('既存のグループへの追加は名前を使わない', () => {
    const add = { member_ids: ['a', 'b'], target_group: { id: 'g', name: 'G', overlap: 0.6 } }
    expect(acceptBody(add, '', [])).toEqual({ asset_ids: ['a', 'b'], target_group_id: 'g' })
  })
  it('全部外したら送れない', () => {
    expect(acceptBody(fresh, '海', ['a', 'b', 'c'])).toBeNull()
  })
})

describe('movingCount', () => {
  it('外した画像は数えない', () => {
    expect(movingCount({ moving_ids: ['a', 'b'] }, [])).toBe(2)
    expect(movingCount({ moving_ids: ['a', 'b'] }, ['a', 'c'])).toBe(1)
    expect(movingCount({ moving_ids: [] }, [])).toBe(0)
  })
})

it('formatRate', () => {
  expect(formatRate(0.494)).toBe('49%')
  expect(formatRate(1)).toBe('100%')
})

describe('namingSuggestionIds / candidatesById', () => {
  it('既存のグループへの追加の提案は名前の候補を求めない', () => {
    const target = { id: 'g', name: 'G', overlap: 0.6 }
    expect(
      namingSuggestionIds([
        { id: 's1', target_group: null },
        { id: 's2', target_group: target },
        { id: 's3' },
      ]),
    ).toEqual(['s1', 's3'])
  })
  it('応答を提案の id で引ける', () => {
    const map = candidatesById({ model: 'm', items: [{ suggestion_id: 's1', candidates: ['a', 'b'] }] })
    expect(map.get('s1')).toEqual(['a', 'b'])
    expect(map.get('s2')).toBeUndefined()
    expect(candidatesById(undefined).size).toBe(0)
  })
})
