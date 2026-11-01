/**
 * 履歴のグループの絞り込み(含める・除く。ADR-0054 6章)。
 */
import { describe, expect, it } from 'vitest'
import { groupFilterMode, matchGroups, selectedGroups, setGroupFilterMode, toggleGroup } from './historyGroupFilter'

const none = { groups: [], excludeGroups: [] }

describe('groupFilterMode', () => {
  it('除くだけが選ばれていれば除く、それ以外は含める', () => {
    expect(groupFilterMode(none)).toBe('include')
    expect(groupFilterMode({ groups: ['a'], excludeGroups: [] })).toBe('include')
    expect(groupFilterMode({ groups: [], excludeGroups: ['a'] })).toBe('exclude')
    expect(selectedGroups({ groups: [], excludeGroups: ['a'] })).toEqual(['a'])
  })
})

describe('setGroupFilterMode', () => {
  it('選んだグループを新しい向きへ移す', () => {
    expect(setGroupFilterMode({ groups: ['a', 'b'], excludeGroups: [] }, 'exclude')).toEqual({
      groups: [],
      excludeGroups: ['a', 'b'],
    })
    expect(setGroupFilterMode({ groups: [], excludeGroups: ['a'] }, 'include')).toEqual({
      groups: ['a'],
      excludeGroups: [],
    })
  })
})

describe('toggleGroup', () => {
  it('今の向きで選ぶ・外す', () => {
    const one = toggleGroup(none, 'exclude', 'a')
    expect(one).toEqual({ groups: [], excludeGroups: ['a'] })
    const two = toggleGroup(one, 'exclude', 'none')
    expect(two).toEqual({ groups: [], excludeGroups: ['a', 'none'] })
    expect(toggleGroup(two, 'exclude', 'a')).toEqual({ groups: [], excludeGroups: ['none'] })
    expect(toggleGroup(none, 'include', 'a')).toEqual({ groups: ['a'], excludeGroups: [] })
  })
})

describe('matchGroups', () => {
  it('名前に含むものだけ(大文字・小文字を区別しない)', () => {
    const groups = [{ name: 'Poses' }, { name: 'Landscape' }, { name: 'pose B' }]
    expect(matchGroups(groups, '')).toEqual(groups)
    expect(matchGroups(groups, ' POSE ')).toEqual([{ name: 'Poses' }, { name: 'pose B' }])
  })
})
